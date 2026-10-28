#!/usr/bin/env python3
"""Coordinate a separate contention run around an unchanged benchmark worker.

The rendezvous is before the frozen action timer. Counter snapshots and sampled
memory may span the untimed wait; their deltas/peaks are therefore descriptive.
This wrapper does not turn contention results into isolated-campaign evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import uuid


SCHEMA = "h100-contention-barrier-v1"
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
UNSUPPORTED_MEASURED = {"cx_q13", "cx_window"}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return (json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode()


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def publish_once(path, value):
    """Publish complete bytes once, without replacing an existing pathname."""
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
            return True
        except FileExistsError:
            return False
    finally:
        temporary.unlink(missing_ok=True)


class FileBarrier:
    """Two local processes rendezvous at an explicit action, on one host clock."""

    def __init__(self, root, participant, participants, worker_sha256, *,
                 timeout_seconds=1800, release_delay_seconds=0.25):
        self.root = Path(root).resolve()
        self.participants = sorted(participants)
        if (len(self.participants) != 2 or len(set(self.participants)) != 2
                or participant not in self.participants
                or any(not NAME.fullmatch(value) for value in self.participants)):
            raise ValueError("Exactly two distinct safe participant IDs are required")
        if not re.fullmatch(r"[0-9a-f]{64}", worker_sha256):
            raise ValueError("An exact frozen-worker SHA256 is required")
        if any(not math.isfinite(v) or v <= 0 for v in (timeout_seconds, release_delay_seconds)):
            raise ValueError("Barrier timeout and release delay must be positive and finite")
        self.participant = participant
        self.timeout_seconds = timeout_seconds
        self.release_delay_ns = int(release_delay_seconds * 1_000_000_000)
        self.root.mkdir(parents=True, exist_ok=True)
        identity = {"schema_version": SCHEMA, "root": str(self.root),
                    "participants": self.participants, "worker_sha256": worker_sha256}
        self.barrier_id = hashlib.sha256(canonical(identity)).hexdigest()
        identity["barrier_id"] = self.barrier_id
        publish_once(self.root / "IDENTITY.json", identity)
        if json.loads((self.root / "IDENTITY.json").read_text()) != identity:
            raise ValueError("Barrier root belongs to different participants or worker bytes")
        self.claim = {"barrier_id": self.barrier_id, "participant": participant,
                      "pid": os.getpid(), "claimed_at_utc": utcnow()}
        if not publish_once(self.root / f"participant-{participant}.json", self.claim):
            raise ValueError("Participant already claimed this root; no implicit reruns")
        self.events = self.root / f"events-{participant}.jsonl"
        self.record("participant_claimed", None, worker_sha256=worker_sha256)

    def record(self, event, key, **extra):
        row = {"schema_version": SCHEMA, "barrier_id": self.barrier_id,
               "participant": self.participant, "pid": os.getpid(), "event": event,
               "key": key, "at_utc": utcnow(), "monotonic_ns": time.monotonic_ns(), **extra}
        with self.events.open("ab") as handle:
            handle.write(canonical(row))
            handle.flush()
            os.fsync(handle.fileno())

    def skip(self, key, reason):
        self.record("rendezvous_skipped", key, reason=reason)

    def wait(self, key):
        if (not isinstance(key, dict) or set(key) != {"query", "phase", "trial"}
                or not isinstance(key["query"], str) or not isinstance(key["phase"], str)
                or not NAME.fullmatch(key["query"]) or not NAME.fullmatch(key["phase"])
                or isinstance(key["trial"], bool) or not isinstance(key["trial"], int)
                or key["trial"] < 0):
            raise ValueError("Unsafe or invalid action rendezvous key")
        action = self.root / hashlib.sha256(canonical(key)).hexdigest()
        action.mkdir(exist_ok=True)
        ready = {"barrier_id": self.barrier_id, "key": key, "participant": self.participant,
                 "pid": os.getpid(), "ready_at_utc": utcnow(),
                 "ready_monotonic_ns": time.monotonic_ns()}
        if not publish_once(action / f"ready-{self.participant}.json", ready):
            raise ValueError("Duplicate action rendezvous; no implicit retry")
        self.record("ready", key)
        deadline = time.monotonic() + self.timeout_seconds
        release_path = action / "RELEASE.json"
        while True:
            if time.monotonic() >= deadline:
                self.record("timeout", key)
                raise TimeoutError(f"Contention barrier timed out: {key}")
            ready_rows = []
            for participant in self.participants:
                path = action / f"ready-{participant}.json"
                if not path.exists():
                    break
                row = json.loads(path.read_text())
                claim = json.loads((self.root / f"participant-{participant}.json").read_text())
                if (row.get("barrier_id") != self.barrier_id or row.get("key") != key
                        or row.get("participant") != participant or row.get("pid") != claim.get("pid")
                        or claim.get("barrier_id") != self.barrier_id):
                    raise ValueError("Peer readiness does not match this action and participant")
                ready_rows.append(row)
            if len(ready_rows) == len(self.participants):
                release = {"barrier_id": self.barrier_id, "key": key,
                           "participants": self.participants,
                           "ready_monotonic_ns": {row["participant"]: row["ready_monotonic_ns"]
                                                  for row in ready_rows},
                           "release_monotonic_ns": time.monotonic_ns() + self.release_delay_ns}
                publish_once(release_path, release)
                actual = json.loads(release_path.read_text())
                if (actual.get("barrier_id") != self.barrier_id or actual.get("key") != key
                        or actual.get("participants") != self.participants
                        or actual.get("ready_monotonic_ns") != release["ready_monotonic_ns"]
                        or isinstance(actual.get("release_monotonic_ns"), bool)
                        or not isinstance(actual.get("release_monotonic_ns"), int)
                        or actual["release_monotonic_ns"] <= max(release["ready_monotonic_ns"].values())):
                    raise ValueError("Release record does not match both actual readiness records")
                target = actual["release_monotonic_ns"]
                while True:
                    # Read once: CPU descheduling between two clock reads can
                    # otherwise turn a previously positive sleep into a negative one.
                    remaining_ns = target - time.monotonic_ns()
                    if remaining_ns <= 0:
                        break
                    if time.monotonic() >= deadline:
                        self.record("timeout", key)
                        raise TimeoutError(f"Contention release time exceeded deadline: {key}")
                    time.sleep(min(0.01, remaining_ns / 1_000_000_000))
                self.record("released", key, release_monotonic_ns=target,
                            ready_monotonic_ns=actual["ready_monotonic_ns"])
                return target
            time.sleep(0.01)


def install_hooks(worker, barrier, *, pair_has_polars_gpu=False):
    """Keep query functions and original action timer code intact."""
    original_save, original_counters = worker.save, worker.process_counters
    state = {"metadata": None, "setup_done": False, "actions_gated": set()}

    def save(path, value):
        result = original_save(path, value)
        state["metadata"] = value
        if not state["setup_done"] and "session_and_table_setup_seconds" in value:
            state["setup_done"] = True
            barrier.wait({"query": "setup", "phase": "setup", "trial": 0})
        return result

    def counters(processes):
        result = original_counters(processes)
        metadata = state["metadata"] or {}
        context = metadata.get("active_query")
        if context:
            identity = (context["query"], context["phase"], context["trial"])
            if identity not in state["actions_gated"]:
                state["actions_gated"].add(identity)
                key = dict(zip(("query", "phase", "trial"), identity))
                if (pair_has_polars_gpu and metadata.get("suite") == "complex"
                        and context["phase"] == "measured" and context["query"] in UNSUPPORTED_MEASURED):
                    barrier.skip(key, "No strict Polars GPU measured counterpart after expected warmup rejection")
                else:
                    barrier.wait(key)
        return result

    worker.save, worker.process_counters = save, counters
    return state


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        raise ValueError("Separate wrapper options and frozen worker arguments with --")
    split = argv.index("--")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--barrier-root", type=Path, required=True)
    parser.add_argument("--participant", required=True)
    parser.add_argument("--participants", nargs=2, required=True)
    parser.add_argument("--pair-has-polars-gpu", action="store_true")
    parser.add_argument("--barrier-timeout-seconds", type=float, default=1800)
    args = parser.parse_args(argv[:split])
    source = args.source.resolve(strict=True)
    path = source / "worker.py"
    if not path.is_file() or path.is_symlink():
        raise ValueError("A regular archived worker.py is required")
    barrier = FileBarrier(args.barrier_root, args.participant, args.participants, sha256(path),
                          timeout_seconds=args.barrier_timeout_seconds)
    sys.path.insert(0, str(source))
    spec = importlib.util.spec_from_file_location("contention_frozen_worker", path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    install_hooks(worker, barrier, pair_has_polars_gpu=args.pair_has_polars_gpu)
    barrier.record("wrapper_provenance", None, wrapper_sha256=sha256(__file__),
                   frozen_worker_sha256=sha256(path),
                   scope="Separate coordinated contention experiment; original worker action timers unchanged",
                   limitation="Counter deltas and memory samples may include untimed rendezvous waits")
    sys.argv = [str(path), *argv[split + 1:]]
    try:
        worker.main()
    except BaseException as exc:
        barrier.record("worker_failed", None, error_type=type(exc).__name__, message=str(exc)[:1000])
        raise
    else:
        barrier.record("worker_finished", None)


if __name__ == "__main__":
    main()
