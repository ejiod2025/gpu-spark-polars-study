#!/usr/bin/env python3
"""Offline, byte-bound replay of the separate H100 contention experiment.

No GPU, JVM, source Parquet, SSH, cloud API or benchmark execution is used.
The original 400-session analysis and its primary contrasts are untouched.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import csv
import hashlib
import importlib.util
from importlib import metadata as package_metadata
import json
import math
from pathlib import Path
import re
import stat
import sys


PLAN_SHA = "670f37af6c1a6f2dbea5fc110d213b148a0acb83858786a21add8252acc89031"
PRESTART_SHA = "4088b5302a7d42c8aa87ebaed06e229bed3b0783bd15cf6556a6110981bf9f25"
REFERENCE_SHA = "3142874b918a2df2d66c8dfb32c26f59a09353edcb4cb73e51c4ba46ff387641"
PROFILE_SHA = "a07569ffa3be9cd138ed63084ff59ed668b8bceeef355b5a4f63adef53464c69"
SOURCE_COMMIT = "c84ef6a07341f350dae6636ab709c1b65fd894e0"
SOURCE_TREE = "9514f0f7ce384763d0a675a9075666707e0edabcc003c729a3e0735a52ea25d8"
EXPERIMENT_ID = "h100-contention-20261028"
UNSUPPORTED = {"cx_q13", "cx_window"}


def registered_identity(plan_sha256, prestart_sha256):
    """The only genuine contention tuple; tests may mock this boundary explicitly."""
    require(plan_sha256 == PLAN_SHA and prestart_sha256 == PRESTART_SHA,
            "Unknown contention plan/prestart identity")
    return {"experiment_id": EXPERIMENT_ID, "plan_sha256": PLAN_SHA, "prestart_sha256": PRESTART_SHA,
            "reference_campaign_sha256": REFERENCE_SHA, "reference_execution_profile_sha256": PROFILE_SHA,
            "source_commit": SOURCE_COMMIT, "source_tree_sha256": SOURCE_TREE}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def no_duplicates(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "Duplicate JSON key")
        value[key] = item
    return value


def finite(value, label):
    require(type(value) in (int, float) and math.isfinite(value), "Invalid " + label)
    return value


def instant(value):
    result = datetime.fromisoformat(value)
    require(result.tzinfo is not None, "Timestamp must have an explicit timezone")
    return result.timestamp()


def contained(root, relative):
    relative = Path(relative)
    require(not relative.is_absolute() and relative.parts and ".." not in relative.parts,
            "Unsafe relative artifact path")
    path = Path(root) / relative
    require(path.resolve().is_relative_to(Path(root).resolve()), "Artifact escapes root")
    return path


class Inputs:
    def __init__(self):
        self.hashes = {}

    def file(self, path):
        path = Path(path).absolute()
        require(not path.is_symlink() and path.is_file(), "Missing or linked input: " + str(path))
        for parent in path.parents:
            require(not parent.is_symlink(), "Linked input parent: " + str(parent))
        before = path.stat()
        digest = sha(path)
        after = path.stat()
        require((before.st_ino, before.st_size, before.st_mtime_ns) ==
                (after.st_ino, after.st_size, after.st_mtime_ns), "Input changed while reading")
        key = str(path)
        require(key not in self.hashes or self.hashes[key] == digest, "Input changed between reads")
        self.hashes[key] = digest
        return digest

    def json(self, path):
        self.file(path)
        return json.loads(Path(path).read_bytes(), object_pairs_hook=no_duplicates,
                          parse_constant=lambda x: (_ for _ in ()).throw(ValueError("Nonfinite JSON constant")))

    def jsonl(self, path):
        self.file(path)
        return [json.loads(line, object_pairs_hook=no_duplicates) for line in Path(path).read_text().splitlines()]

    def tree(self, root):
        root = Path(root).absolute()
        require(root.is_dir() and not root.is_symlink(), "Expected a regular artifact directory")
        found = {}
        for path in sorted(root.rglob("*")):
            mode = path.lstat().st_mode
            require(not stat.S_ISLNK(mode), "Artifact tree contains a symlink")
            if stat.S_ISDIR(mode):
                continue
            require(stat.S_ISREG(mode), "Artifact tree contains a special file")
            found[str(path.relative_to(root))] = self.file(path)
        return found

    def recheck(self):
        for name, digest in self.hashes.items():
            require(sha(name) == digest, "Audit input changed: " + name)


def module_from(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


@contextmanager
def frozen_imports(source):
    """Do not let an already-imported working-tree module replace frozen code."""
    names = {p.stem for p in Path(source).glob("*.py")}
    old = {name: sys.modules.pop(name) for name in names if name in sys.modules}
    previous_path, previous_bytecode = list(sys.path), sys.dont_write_bytecode
    sys.path.insert(0, str(source))
    sys.dont_write_bytecode = True
    try:
        yield
    finally:
        for name in names:
            sys.modules.pop(name, None)
        sys.modules.update(old)
        sys.path[:] = previous_path
        sys.dont_write_bytecode = previous_bytecode


def verify_canonical(root, inputs):
    root = Path(root).resolve(strict=True)
    inventory = inputs.tree(root)
    registered_identity(inventory.get("CONTENTION_PLAN.json"), inventory.get("CONTENTION_PRESTART_MANIFEST.json"))
    seal = inputs.json(root / "CONTENTION_PRESTART_MANIFEST.json")
    require(seal.get("schema_version") == "h100-contention-prestart-v1" and
            seal.get("status") == "FROZEN_NOT_RUN" and seal.get("plan_sha256") == PLAN_SHA,
            "Invalid genuine contention prestart")
    require(len(inventory) == 66 and set(inventory) == set(seal["files_sha256"]) |
            {"CONTENTION_PRESTART_MANIFEST.json"}, "Canonical prestart must contain its exact 66 files")
    for name, digest in seal["files_sha256"].items():
        require(inventory.get(name) == digest, "Canonical prestart byte mismatch: " + name)
    plan = inputs.json(root / "CONTENTION_PLAN.json")
    require(plan.get("schema_version") == "h100-contention-plan-v1" and plan.get("experiment_id") == EXPERIMENT_ID
            and plan.get("synthetic") is False and plan.get("source_commit") == SOURCE_COMMIT,
            "Not the genuine separately frozen contention design")
    require((root / "CONTENTION_PLAN.sha256").read_text().strip() == PLAN_SHA, "Plan checksum differs")
    require(inventory["REFERENCE_CAMPAIGN.json"] == REFERENCE_SHA and
            inventory["REFERENCE_EXECUTION_PROFILE.json"] == PROFILE_SHA, "Reference campaign/profile differs")
    reference = inputs.json(root / "REFERENCE_CAMPAIGN.json")
    profile = inputs.json(root / "REFERENCE_EXECUTION_PROFILE.json")
    mapping = plan["source_files_sha256"]
    source_payload = (json.dumps(mapping, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    require(len(mapping) == 52 and hashlib.sha256(source_payload).hexdigest() == SOURCE_TREE and
            mapping == reference["source"]["files_sha256"] == profile["source_files_sha256"],
            "Frozen C84 source identity differs")
    for name, digest in mapping.items():
        require(inventory.get("FROZEN_SOURCE/" + name) == digest, "Frozen source differs: " + name)
    require(plan["reference_campaign_sha256"] == REFERENCE_SHA and
            plan["reference_execution_profile_sha256"] == PROFILE_SHA and
            plan["configs"] == reference["configs"] and
            plan["settings"] == reference["settings"] | {"runs": 1}, "Contention configuration differs")
    for name, digest in plan["tools_sha256"].items():
        relative = Path(name).relative_to(Path(plan["root"]))
        require(inventory.get(str(relative)) == digest, "Frozen instrumentation differs")
    for key, entry in plan["oracles"].items():
        require(inventory["oracles/" + key + ".json"] == entry["sha256"] == reference["oracles"][key]["sha256"],
                "SQL oracle differs")
    for scale in ("30", "100"):
        require(inventory["manifests/sf" + scale + ".json"] == plan["datasets"][scale]["manifest_sha256"]
                == reference["datasets"][scale]["manifest_sha256"], "Input manifest differs")
    runner = module_from(root / "tools/contention_runner.py", "_verified_contention_runner")
    require(plan["pilot"] == runner.waves("pilot") and plan["main"] == runner.waves("main"),
            "Frozen wave matrix/order differs")
    return root, plan, profile, inventory, runner


def verify_snapshots(raw, canonical_inventory, inputs):
    try:
        from operations import contention_backup as backup
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from operations import contention_backup as backup
    inputs.file(Path(backup.__file__))
    inputs.file(Path(backup.common.__file__))
    snapshots, payloads = {}, {}
    for phase in ("pilot", "main", "failed"):
        control_path = Path(raw) / (phase + ".json")
        if not control_path.exists():
            continue
        control = inputs.json(control_path)
        backup.validate_control(control, PLAN_SHA, phase)
        archive, directory, receipt_path = Path(raw) / (phase + ".tar"), Path(raw) / phase, Path(raw) / (phase + ".verified.json")
        inputs.file(archive)
        receipt = inputs.json(receipt_path)
        require(receipt.get("status") == "OFFHOST_SHA256_VERIFIED" and
                all(receipt.get(k) == control[k] for k in backup.IDENTITY_FIELDS), "Off-host receipt differs")
        require(Path(receipt["local_archive"]).resolve() == archive.resolve() and
                Path(receipt["local_directory"]).resolve() == directory.resolve(), "Historical transport receipt paths differ")
        manifest = backup.verify_archive(archive, control, directory)
        inventory = inputs.tree(directory)
        for name, digest in canonical_inventory.items():
            require(inventory.get(name) == digest, "Raw snapshot changes frozen prestart: " + name)
        payloads[phase] = inventory
        snapshots[phase] = {"archive_path": str(archive.resolve()), "archive_sha256": control["tar_sha256"],
                            "control_path": str(control_path.resolve()), "control_sha256": inputs.file(control_path),
                            "receipt_path": str(receipt_path.resolve()), "receipt_sha256": inputs.file(receipt_path),
                            "directory": str(directory.resolve()), "manifest_sha256": control["manifest_sha256"],
                            "files": control["files"], "created_at_utc": manifest["created_at_utc"]}
        snapshots[phase].update(archive={"path": str(archive.resolve()), "sha256": control["tar_sha256"]},
            control={"path": str(control_path.resolve()), "sha256": inputs.file(control_path)},
            receipt={"path": str(receipt_path.resolve()), "sha256": inputs.file(receipt_path)})
    for earlier, later in (("pilot", "main"), ("pilot", "failed"), ("main", "failed")):
        if earlier in payloads and later in payloads:
            for name, digest in payloads[earlier].items():
                if name.startswith(("pilot/", "main/")):
                    require(payloads[later].get(name) == digest, "Completed phase changed across snapshots: " + name)
    selected = next((p for p in ("failed", "main", "pilot") if p in snapshots), None)
    return snapshots, Path(snapshots[selected]["directory"]) if selected else None


def adapted_plan(plan, archive):
    value = json.loads(json.dumps(plan))
    changes = []
    original = value["source_root"]
    value["source_root"] = str(archive / "FROZEN_SOURCE")
    changes.append({"selector": "source_root", "from": original, "to": value["source_root"]})
    for key, entry in value["oracles"].items():
        original = entry["path"]
        entry["path"] = str(archive / "oracles" / (key + ".json"))
        changes.append({"selector": "oracles." + key + ".path", "from": original, "to": entry["path"]})
    return value, changes


def check_gate(value, scales, plan, archive, label):
    require(isinstance(value, dict) and value.get("completed_at_utc"), "Missing " + label)
    expected = {}
    for scale in scales:
        manifest = json.loads((archive / "manifests" / ("sf" + scale + ".json")).read_bytes())
        expected[scale] = sum(len(table["files"]) for table in manifest["tables"].values())
    checks = value.get("checks", [])
    require(len(checks) == len(expected) and {c.get("scale"): c.get("files") for c in checks} == expected
            and all(c.get("status") == "PASS" for c in checks), "Incomplete recorded byte gate: " + label)
    instant(value["completed_at_utc"])


def action_key(query, phase, trial):
    return {"query": query, "phase": phase, "trial": trial}


def check_barriers(plan, wave, directory, results, inputs, complete=True, queries=None):
    root = directory / "barriers"
    if not root.exists():
        require(not complete, "Successful wave has no actual barrier evidence")
        return {"status": "PARTIAL_FAILURE", "verified_releases": 0}
    ids = sorted(wave["participants"])
    identity = {"schema_version": "h100-contention-barrier-v1",
                "root": str(Path(plan["root"]) / directory.parent.name / wave["wave_id"] / "barriers"),
                "participants": ids, "worker_sha256": plan["source_files_sha256"]["worker.py"]}
    barrier_id = hashlib.sha256(canonical(identity)).hexdigest()
    require(inputs.json(root / "IDENTITY.json") == identity | {"barrier_id": barrier_id}, "Barrier identity differs")
    if queries is None:
        module = sys.modules["complex_workloads" if wave["suite"] == "complex" else "workloads"]
        queries = list(module.QUERY_IDS)
    keys = [action_key("setup", "setup", 0)] + [action_key(q, "warmup", 0) for q in queries]
    common = [q for q in queries if not (wave["suite"] == "complex" and "polars_gpu" in ids and q in UNSUPPORTED)]
    keys += [action_key(q, "measured", 1) for q in common]
    expected_dirs = {hashlib.sha256(canonical(key)).hexdigest(): key for key in keys}
    actual_dirs = {p.name for p in root.iterdir() if p.is_dir()}
    require(actual_dirs <= set(expected_dirs), "Unplanned barrier action")
    if complete:
        require(actual_dirs == set(expected_dirs), "Missing planned rendezvous")
    events, claims = {}, {}
    for member in ids:
        claim_path = root / ("participant-" + member + ".json")
        if not claim_path.exists():
            require(not complete, "Missing barrier participant claim")
            continue
        claim = inputs.json(claim_path)
        require(claim.get("barrier_id") == barrier_id and claim.get("participant") == member and
                type(claim.get("pid")) is int and claim["pid"] > 1, "Barrier participant claim differs")
        if results.get(member):
            require(claim["pid"] == results[member]["pid"], "Barrier claim differs from actual worker PID")
        claims[member] = claim
        rows = inputs.jsonl(root / ("events-" + member + ".jsonl"))
        previous = -1
        for row in rows:
            require(row.get("schema_version") == identity["schema_version"] and row.get("barrier_id") == barrier_id
                    and row.get("participant") == member and row.get("pid") == claim["pid"], "Barrier event identity differs")
            now = finite(row.get("monotonic_ns"), "barrier clock")
            require(now >= previous, "Barrier event clock went backwards")
            previous = now
            instant(row["at_utc"])
            require(row.get("event") in {"participant_claimed", "wrapper_provenance", "ready", "released",
                    "rendezvous_skipped", "worker_finished", "worker_failed", "timeout"}, "Unknown barrier event")
            if row.get("key") is not None:
                require(row["key"] in keys or (row["event"] == "rendezvous_skipped" and
                        row["key"] in [action_key(q, "measured", 1) for q in UNSUPPORTED]), "Unplanned barrier event key")
        provenance = [row for row in rows if row.get("event") == "wrapper_provenance"]
        require(len(provenance) <= 1, "Duplicate wrapper provenance")
        if provenance:
            require(provenance[0].get("frozen_worker_sha256") == identity["worker_sha256"] and
                    provenance[0].get("wrapper_sha256") == plan["tools_sha256"][str(Path(plan["root"]) / "tools/contention_worker.py")],
                    "Executed wrapper/source provenance differs")
        if complete:
            require(len(provenance) == 1 and sum(row.get("event") == "worker_finished" for row in rows) == 1
                    and sum(row.get("event") == "participant_claimed" for row in rows) == 1
                    and not any(row.get("event") in ("worker_failed", "timeout") for row in rows), "Successful worker barrier did not finish")
            expected_keys = Counter(json.dumps(key, sort_keys=True) for key in keys)
            for event_name in ("ready", "released"):
                observed_keys = Counter(json.dumps(row.get("key"), sort_keys=True) for row in rows if row.get("event") == event_name)
                require(observed_keys == expected_keys, "Participant action rendezvous coverage differs")
        events[member] = rows
    releases = 0
    for name in actual_dirs:
        key = expected_dirs[name]
        folder = root / name
        release_path = folder / "RELEASE.json"
        if not release_path.exists():
            require(not complete, "Missing actual release")
            continue
        release = inputs.json(release_path)
        ready_ns = {}
        for member in ids:
            ready = inputs.json(folder / ("ready-" + member + ".json"))
            require(ready.get("barrier_id") == barrier_id and ready.get("key") == key and
                    ready.get("participant") == member and ready.get("pid") == claims[member]["pid"], "Actual readiness differs")
            ready_ns[member] = finite(ready.get("ready_monotonic_ns"), "readiness clock")
            ready_events = [row for row in events[member] if row.get("event") == "ready" and row.get("key") == key]
            if complete:
                require(len(ready_events) == 1 and ready_events[0]["monotonic_ns"] >= ready_ns[member],
                        "Readiness file is not bound to the participant event")
        target = finite(release.get("release_monotonic_ns"), "release clock")
        require(release.get("barrier_id") == barrier_id and release.get("key") == key and
                release.get("participants") == ids and release.get("ready_monotonic_ns") == ready_ns and
                target > max(ready_ns.values()), "Release differs from both actual readiness records")
        for member in ids:
            rows = [row for row in events[member] if row.get("event") == "released" and row.get("key") == key]
            require(len(rows) <= 1, "Duplicate action release")
            if complete:
                require(len(rows) == 1, "Missing participant release event")
            if rows:
                event = rows[0]
                require(event.get("release_monotonic_ns") == target and event.get("ready_monotonic_ns") == ready_ns and
                        event["monotonic_ns"] >= target, "Participant did not use the shared release")
                record = next((r for r in (results.get(member) or {}).get("records", [])
                               if (r["query"], r["phase"], r["trial"]) == (key["query"], key["phase"], key["trial"])), None)
                if record:
                    require(record["action_start_monotonic_seconds"] * 1e9 + 10000 >= event["monotonic_ns"],
                            "Action timer started before its rendezvous release")
        releases += 1
    if complete:
        for member in ids:
            skipped = [r["key"] for r in events[member] if r.get("event") == "rendezvous_skipped"]
            expected_skips = ([action_key(q, "measured", 1) for q in sorted(UNSUPPORTED)]
                              if wave["suite"] == "complex" and "polars_gpu" in ids and member == "spark_cpu" else [])
            require(sorted(skipped, key=lambda x: x["query"]) == expected_skips, "Unpaired diagnostic barrier coverage differs")
    return {"status": "PASS" if complete else "PARTIAL_FAILURE", "barrier_id": barrier_id,
            "verified_releases": releases, "planned_releases": len(keys)}


def wave_identity(observed, planned):
    for key in ("wave_id", "pair", "participants", "suite", "scale", "repetition", "query_seed"):
        require(observed.get(key) == planned[key], "Wave identity differs: " + key)


def strict_rejection(error, wave, config_id):
    reason = str(error).lower()
    runtime_failure = any(token in reason for token in ("out of memory", "cuda error", "cudaerror", "std::bad_alloc", "rmm::bad_alloc"))
    runtime_failure |= bool(re.search(r"\boom\b", reason))
    return (config_id == "polars_gpu" and wave["suite"] == "complex" and error.get("query") in UNSUPPORTED
            and error.get("phase") == "warmup" and error.get("trial") == 0 and not runtime_failure
            and any(token in reason for token in ("unsupported", "not supported", "notimplementederror")))


def audit_wave(plan, adapted, runner, wave, directory, inputs):
    value = inputs.json(directory / "WAVE.json")
    if value.get("stage") == "prelaunch":
        require(value.get("wave_id") == wave["wave_id"] and value.get("status") == "FAIL" and value.get("error"),
                "Malformed prelaunch failure")
        return {"wave_id": wave["wave_id"], "status": "FAIL", "failure_stage": "prelaunch",
                "recorded_error": value["error"], "members": [], "action_rows": [], "overlap": []}
    wave_identity(value, wave)
    require(value.get("status") in ("PASS", "FAIL", "RUNNING"), "Unknown wave disposition")
    members, result_values, action_rows = [], {}, []
    for name in wave["participants"]:
        path = directory / name / "results.json"
        results = inputs.json(path) if path.exists() else None
        result_values[name] = results
        report = runner.validate_member(adapted, wave, name, directory)
        members.append(report)
        if results:
            suite_module = sys.modules["complex_workloads" if wave["suite"] == "complex" else "workloads"]
            require(results.get("engine") == next(c["engine"] for c in plan["configs"] if c["id"] == name)
                    and results.get("suite") == wave["suite"] and results.get("queries") == suite_module.QUERY_IDS
                    and results.get("query_seed") == wave["query_seed"]
                    and results.get("manifest_sha256") == plan["datasets"][wave["scale"]]["manifest_sha256"],
                    "Partial worker result belongs to a different task")
            oracle_entry = adapted["oracles"][f"{wave['suite']}-sf{wave['scale']}"]
            oracle = inputs.json(oracle_entry["path"])
            compare = sys.modules["analyze_results"].compare_values
            report["all_available_output_comparisons"] = []
            report["recorded_query_errors"] = [{"expected_strict_rejection": strict_rejection(error, wave, name),
                                               "error": error} for error in results.get("query_errors", [])]
            record_keys = [(r["query"], r["phase"], r["trial"]) for r in results.get("records", [])]
            require(len(record_keys) == len(set(record_keys)) and all(q in suite_module.QUERY_IDS and
                    (phase, trial) in (("warmup", 0), ("measured", 1)) for q, phase, trial in record_keys),
                    "Malformed or duplicate partial output record")
            for record in results.get("records", []):
                comparison = compare(oracle["queries"][record["query"]]["rows"], record["rows"])
                report["all_available_output_comparisons"].append({"query": record["query"], "phase": record["phase"],
                    "trial": record["trial"], **comparison})
                for key in ("seconds", "host_result_seconds", "action_start_monotonic_seconds", "action_end_monotonic_seconds"):
                    finite(record[key], key)
                require(record["seconds"] > 0 and record["host_result_seconds"] >= record["seconds"] and
                        record["action_end_monotonic_seconds"] > record["action_start_monotonic_seconds"] and
                        instant(record["action_started_at_utc"]) <= instant(record["action_ended_at_utc"]) <=
                        instant(record["host_result_ended_at_utc"]), "Malformed actual action interval")
                action_rows.append({"wave_id": wave["wave_id"], "pair": wave["pair"], "suite": wave["suite"],
                    "scale": wave["scale"], "repetition": wave["repetition"], "config_id": name,
                    "query": record["query"], "phase": record["phase"], "trial": record["trial"],
                    "seconds": record["seconds"], "host_result_seconds": record["host_result_seconds"],
                    "action_started_at_utc": record["action_started_at_utc"], "action_ended_at_utc": record["action_ended_at_utc"],
                    "host_result_ended_at_utc": record["host_result_ended_at_utc"],
                    "oracle_passed": comparison["passed"],
                    "paired_condition": not (wave["suite"] == "complex" and "polars_gpu" in wave["participants"]
                                              and name == "spark_cpu" and record["query"] in UNSUPPORTED
                                              and record["phase"] == "measured")})
    complete = value["status"] == "PASS"
    barriers = check_barriers(plan, wave, directory, result_values, inputs, complete)
    overlap = runner.overlap(*[v for v in result_values.values()]) if all(result_values.values()) else []
    if complete:
        require(all(m["status"] == "PASS" for m in members), "A recorded successful wave fails independent SQL/runtime replay")
        validation = inputs.json(directory / "VALIDATION.json")
        wave_identity(validation, wave)
        require(all(error["expected_strict_rejection"] for m in members for error in m.get("recorded_query_errors", [])),
                "Recorded successful wave misclassified an unexpected runtime/query failure")
        original_member_reports = [{k: v for k, v in m.items() if k not in
                                  {"all_available_output_comparisons", "recorded_query_errors"}} for m in members]
        require(validation.get("status") == "PASS" and validation.get("correctness_status") == "PASS" and
                validation.get("overlap_status") == "PASS" and validation.get("validations") == original_member_reports and
                validation.get("measured_action_overlap") == overlap == value.get("measured_action_overlap"),
                "Stored wave validation differs from independent replay")
        expected_common = 4 if wave["suite"] == "complex" and "polars_gpu" in wave["participants"] else 6
        require(len(overlap) == expected_common and all(r["positive_actual_overlap"] for r in overlap),
                "Recorded successful wave has no genuine common-action overlap")
        require(set(value.get("members", {})) == set(wave["participants"]) and
                all(m.get("returncode") == 0 for m in value["members"].values()) and not value.get("cleanup_errors"),
                "Successful wave process cleanup differs")
        for key in ("cache_before_preheat", "cache_after_preheat", "cache_after_workers"):
            require(value.get(key, {}).get("files") and not value[key].get("errors"), "Missing successful-wave cache observation")
        require((directory / "gpu.csv").stat().st_size > 0 and (directory / "host-processes.jsonl").stat().st_size > 0,
                "Successful wave lacks recorded telemetry")
    else:
        require(value.get("error") or value.get("cleanup_errors") or value["status"] == "RUNNING", "Failed wave lacks its reason")
    return {"wave_id": wave["wave_id"], "status": value["status"], "recorded_error": value.get("error"),
            "members": members, "barriers": barriers, "action_rows": action_rows, "overlap": overlap}


def audit_phase(plan, adapted, runner, phase, root, archive, inputs):
    folder = root / phase
    planned = plan[phase]
    disposition = {"status": "NOT_OBSERVED", "planned_waves": len(planned), "attempted_waves": 0,
                   "passed_waves": 0, "failed_waves": 0, "unattempted_waves": [w["wave_id"] for w in planned]}
    if not folder.exists():
        return disposition, []
    value = inputs.json(folder / "PHASE.json")
    require(value.get("schema_version") == "h100-contention-phase-v1" and value.get("phase") == phase and
            value.get("plan_sha256") == PLAN_SHA and value.get("status") in ("PASS", "FAIL", "RUNNING"),
            "Phase identity/status differs")
    directory_ids = {p.name for p in folder.iterdir() if p.is_dir()}
    require(directory_ids <= {w["wave_id"] for w in planned}, "Unplanned or repeated contention wave")
    attempted = [w for w in planned if w["wave_id"] in directory_ids]
    require([w["wave_id"] for w in attempted] == [w["wave_id"] for w in planned[:len(attempted)]],
            "Attempted waves are not the original fixed prefix")
    summaries = [audit_wave(plan, adapted, runner, wave, folder / wave["wave_id"], inputs) for wave in attempted]
    entries = value.get("waves", [])
    require([entry["wave_id"] for entry in entries] == [s["wave_id"] for s in summaries if s["status"] == "PASS"],
            "Phase completion list differs from actual successful waves")
    for entry in entries:
        directory = folder / entry["wave_id"]
        require(entry.get("status") == "PASS" and entry.get("wave_sha256") == inputs.file(directory / "WAVE.json") and
                entry.get("validation_sha256") == inputs.file(directory / "VALIDATION.json"), "Phase wave/validation hash differs")
    if attempted:
        check_gate(value.get("initial_input_gate"), {w["scale"] for w in planned}, plan, archive, phase + " initial gate")
        runtime = value.get("runtime_gate", {})
        profile = inputs.json(archive / "REFERENCE_EXECUTION_PROFILE.json")
        require(runtime.get("status") == "PASS" and runtime.get("java_files_sha256") == profile["java"]["files_sha256"] and
                runtime.get("python_runtime_identities") == {c["python"]: c["runtime"] for c in plan["configs"]},
                "Recorded phase runtime identities differ")
    if value["status"] == "PASS":
        require(len(attempted) == len(planned) and all(s["status"] == "PASS" for s in summaries) and
                value.get("active_wave") is None, "Passing phase is incomplete")
        check_gate(value.get("post_input_gate"), {w["scale"] for w in planned}, plan, archive, phase + " post gate")
        require(instant(value["initial_input_gate"]["completed_at_utc"]) <=
                min(instant(inputs.json(folder / w["wave_id"] / "WAVE.json")["started_at_utc"]) for w in attempted) and
                instant(value["post_input_gate"]["completed_at_utc"]) >=
                max(instant(inputs.json(folder / w["wave_id"] / "WAVE.json")["ended_at_utc"]) for w in attempted),
                "Input gates do not bracket actual contention waves")
    elif value["status"] == "FAIL":
        require(value.get("error") and value.get("ended_at_utc"), "Terminal phase failure lacks recorded disposition")
        require(sum(s["status"] != "PASS" for s in summaries) <= 1, "Unexpected attempts after a failed wave")
    disposition.update(status=value["status"], attempted_waves=len(attempted),
        passed_waves=sum(s["status"] == "PASS" for s in summaries), failed_waves=sum(s["status"] == "FAIL" for s in summaries),
        unattempted_waves=[w["wave_id"] for w in planned if w["wave_id"] not in directory_ids],
        phase_path=str((folder / "PHASE.json").resolve()), phase_sha256=inputs.file(folder / "PHASE.json"),
        recorded_error=value.get("error"))
    return disposition, summaries


def terminal_driver(metadata, plan, snapshots, disposition, inputs):
    if metadata is None:
        return {"status": "NOT_PROVIDED", "terminal": False}
    root = Path(metadata)
    path = root / "PHASE_DRIVER.json"
    value = inputs.json(path)
    require(value.get("schema_version") == "h100-contention-phases-driver-v1" and value.get("plan_sha256") == PLAN_SHA
            and value.get("prestart_sha256") == PRESTART_SHA, "Final driver metadata identity differs")
    checks = value.get("offhost_checkpoints", [])
    require(len({e.get("phase") for e in checks}) == len(checks), "Duplicate driver checkpoint")
    require({e.get("phase") for e in checks} <= set(snapshots), "Driver ACK references an absent verified raw snapshot")
    remote_receipts = {}
    for phase in snapshots:
        control = inputs.json(snapshots[phase]["control_path"])
        receipt_path = root / "backup-receipts" / (phase + ".json")
        receipt = inputs.json(receipt_path)
        require(receipt.get("status") == "OFFHOST_SHA256_VERIFIED" and
                all(receipt.get(k) == control[k] for k in ("schema_version", "phase", "plan_sha256", "tar_sha256",
                                                          "tar_bytes", "manifest_sha256", "files")), "Final driver ACK bytes differ")
        remote_receipts[phase] = {"path": str(receipt_path.resolve()), "sha256": inputs.file(receipt_path)}
    for entry in checks:
        phase = entry["phase"]
        require(entry.get("receipt_sha256") == remote_receipts[phase]["sha256"] and
                entry.get("control_sha256") == snapshots[phase]["control_sha256"], "Driver-observed ACK bytes differ")
    status = value.get("status")
    terminal = False
    if status == "COMPLETE_OFFHOST":
        require([e["phase"] for e in checks] == ["pilot", "main"] and
                all(disposition[p]["status"] == "PASS" for p in ("pilot", "main")) and value.get("completed_at_utc"),
                "Complete driver lacks both completed off-host phases")
        require(value.get("active_phase") is None and value.get("waiting_phase") is None, "Complete driver is still waiting")
        require([p["phase"] for p in value.get("phases", [])] == ["pilot", "main"] and
                all(p.get("returncode") == 0 and p.get("phase_sha256") == disposition[p["phase"]]["phase_sha256"]
                    for p in value["phases"]), "Final driver phase disposition differs")
        terminal = True
    elif status == "FAILED":
        require(value.get("error") and value.get("completed_at_utc") and "failed" in snapshots,
                "Failed driver lacks preserved terminal evidence")
        terminal = any(disposition[p]["status"] == "FAIL" for p in ("pilot", "main"))
    return {"status": status, "terminal": terminal, "path": str(path.resolve()), "sha256": inputs.file(path),
            "completed_at_utc": value.get("completed_at_utc"), "offhost_checkpoints": checks,
            "remote_receipts": remote_receipts}


def handoff_proof(handoff, held_original, original_attempt, action_rows, inputs, role_plan=None, reference_plan=None):
    if handoff is None or held_original is None:
        return {"status": "NOT_PROVIDED", "same_scheduler_resumed": False, "no_action_overlap": False}
    root, held = Path(handoff), Path(held_original)
    attempt = Path(original_attempt) if original_attempt is not None else None
    files = inputs.tree(root)
    if not (root / "RESUMED.json").exists():
        return {"status": "PENDING_RESUME", "same_scheduler_resumed": False, "no_action_overlap": False}
    config = inputs.json(root / "CONFIG.json")
    state = inputs.json(root / "STATE.json")
    resumed = inputs.json(root / "RESUMED.json")
    before = inputs.json(root / "PROFILE_BEFORE_CONTENTION.json")
    after = inputs.json(root / "PROFILE_BEFORE_RESUME.json")
    drained = inputs.json(root / "ORIGINAL_RUNNER_DRAINED.json")
    inventory = inputs.json(root / "ORIGINAL_ATTEMPT_FILES.json")
    require(role_plan is not None and reference_plan is not None, "Verified reference plans are required for terminal handoff proof")
    reference_bytes = (json.dumps(reference_plan, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    require(hashlib.sha256(reference_bytes).hexdigest() == REFERENCE_SHA, "Handoff reference plan differs")
    snapshot = state["snapshot"]
    original_root = Path(reference_plan["output_root"]).parent
    scheduler = config.get("scheduler", {})
    require(snapshot.get("plan") == reference_plan and config.get("campaign") == str(original_root / "campaign.json")
            and config.get("profile") == str(original_root / "EXECUTION_PROFILE.json")
            and config.get("source") == role_plan["original_source_root"] == scheduler.get("cwd")
            and type(scheduler.get("pid")) is int and scheduler["pid"] > 1
            and finite(scheduler.get("created"), "scheduler creation time") > 0
            and scheduler.get("ppid") == snapshot.get("supervisor", {}).get("pid")
            and "--run" in scheduler.get("cmdline", [])
            and scheduler["cmdline"][scheduler["cmdline"].index("--run") + 1] == config["campaign"]
            and not any(v in scheduler["cmdline"] for v in ("--retry-failed", "--recover-interrupted")),
            "Handoff does not bind the original frozen campaign/scheduler")
    require(config.get("schema_version") == "h100-contention-handoff-v1" and
            resumed.get("schema_version") == "h100-contention-handoff-resume-v1" and resumed.get("status") == "RESUMED"
            and resumed.get("scheduler") == config.get("scheduler") and resumed.get("contention_started") is True,
            "No matching same-scheduler actual resume")
    helper = Path(__file__).resolve().parents[1] / "operations/contention_handoff.py"
    require(config.get("handoff_source_sha256") == inputs.file(helper), "Handoff helper source differs from executed source")
    require(state.get("contention_started") is True and
            (state.get("phase") == "COMPLETE_RESUMED" or
             (state.get("phase") == "HANDOFF_ERROR" and state.get("error")) or
             resumed.get("actor") == "detached-guardian"), "Final handoff state is not terminal/resumed")
    expected = {"1": 57, "10": 123, "30": 185, "100": 371, "300": 918}
    for label, check in (("before-contention", before), ("before-original-resume", after)):
        gate = check.get("input_integrity", {})
        require(check.get("schema_version") == "h100-contention-interlude-profile-check-v1" and check.get("label") == label
                and check.get("status") == "PASS" and check.get("campaign_sha256") == REFERENCE_SHA
                and check.get("execution_profile_sha256") == PROFILE_SHA
                and len(gate.get("checks", [])) == 5
                and {g.get("scale"): g.get("files") for g in gate["checks"]} == expected
                and all(g.get("status") == "pass" for g in gate["checks"])
                and check.get("exclusive_host_before", {}).get("eligible") is True
                and check.get("exclusive_host_after", {}).get("eligible") is True, "Interlude full-input/profile/idle proof differs")
        for key in ("exclusive_host_before", "exclusive_host_after"):
            probe = check[key]
            require(probe.get("nvidia_smi_returncode") == 0 and probe.get("gpu_compute_clients") == "" and
                    probe.get("benchmark_processes") == [], "Interlude idle probe contradicts eligibility")
    require(inputs.json(root / "OWNED_GROUPS_AFTER_CLEANUP.json").get("live_members") == [], "Contention group remained live")
    for name in ("RESIDUAL_SCAN_before-profile.json", "RESIDUAL_SCAN_before-signal.json"):
        scan = inputs.json(root / name)
        require(scan.get("status") == "CLEAR" and scan.get("contention_processes") == [], "Unknown contention process remained")
    held_journal = inputs.json(held / "ORIGINAL_HELD_JOURNAL.json")
    held_session = inputs.json(held / "ORIGINAL_HELD_SESSION.json")
    require(inputs.file(held / "ORIGINAL_HELD_JOURNAL.json") == snapshot["journal_sha256"] and
            held_journal.get("campaign_sha256") == REFERENCE_SHA, "Held original journal bytes differ")
    require(held_session.get("attempt") == snapshot["attempt"] and held_session.get("campaign_sha256") == REFERENCE_SHA,
            "Held original session/attempt differs")
    planned = next((s for s in reference_plan["sessions"] if s["session_id"] == snapshot["session_id"]), None)
    item = held_journal.get("sessions", {}).get(snapshot["session_id"], {})
    require(planned is not None and held_session.get("session") == planned and item.get("status") == "running"
            and item.get("attempts") == [snapshot["attempt"]], "Held original session is not its actual planned first attempt")
    spec = inputs.json(root / "DRIVER_SPEC.json")
    require(inputs.file(root / "DRIVER_SPEC.json") == config.get("driver_spec_sha256")
            and spec.get("schema_version") == "h100-contention-driver-spec-v1", "Actual driver specification differs")
    require(isinstance(inventory, dict) and inventory, "Original attempt inventory is absent")
    for name, identity in inventory.items():
        relative = Path(name)
        require(not relative.is_absolute() and relative.parts and ".." not in relative.parts
                and isinstance(identity, dict) and type(identity.get("bytes")) is int and identity["bytes"] >= 0
                and isinstance(identity.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", identity["sha256"]),
                "Original attempt inventory entry is invalid")
    require(inventory.get("session.json", {}).get("sha256") == inputs.file(held / "ORIGINAL_HELD_SESSION.json")
            and inventory["session.json"]["bytes"] == (held / "ORIGINAL_HELD_SESSION.json").stat().st_size,
            "Held session bytes differ from frozen attempt")
    result_relative = Path(drained["results_path"]).relative_to(Path(snapshot["attempt"]["attempt_root"]))
    schedule_relative = Path(drained["schedule_path"]).relative_to(Path(snapshot["attempt"]["attempt_root"]))
    require(inventory.get(str(result_relative), {}).get("sha256") == drained["results_sha256"] and
            inventory.get(str(schedule_relative), {}).get("sha256") == drained["schedule_sha256"] and
            drained.get("runner_exit", {}).get("exit_code") == 0, "Original drained inventory binding differs")
    first = min((instant(r["action_started_at_utc"]) for r in action_rows), default=None)
    last = max((instant(r["host_result_ended_at_utc"]) for r in action_rows), default=None)
    require(instant(drained["completed_at_utc"]) <= instant(before["completed_at_utc"]),
            "Contention profile verification preceded the original drain")
    if first is not None:
        require(instant(before["completed_at_utc"]) <= first and
                last <= instant(after["completed_at_utc"]) <= instant(resumed["resumed_at_utc"]),
                "Contention actions overlap profile verification or actual resume")
    references = {"config": root / "CONFIG.json", "resume": root / "RESUMED.json", "state": root / "STATE.json",
        "profile_before": root / "PROFILE_BEFORE_CONTENTION.json", "profile_after": root / "PROFILE_BEFORE_RESUME.json",
        "held_journal": held / "ORIGINAL_HELD_JOURNAL.json", "held_session": held / "ORIGINAL_HELD_SESSION.json",
        "attempt_inventory": root / "ORIGINAL_ATTEMPT_FILES.json", "original_runner_drained": root / "ORIGINAL_RUNNER_DRAINED.json",
        "owned_groups": root / "OWNED_GROUPS_AFTER_CLEANUP.json", "residual_before_profile": root / "RESIDUAL_SCAN_before-profile.json",
        "residual_before_signal": root / "RESIDUAL_SCAN_before-signal.json", "driver_spec": root / "DRIVER_SPEC.json"}
    proof = {"status": "PENDING_ORIGINAL_ATTEMPT", "same_scheduler_resumed": True, "no_action_overlap": False,
            "original_payload_verified": False,
            "pending_reasons": ["Actual original attempt payload and worker action endpoints are not yet supplied"],
            "reference_campaign_sha256": REFERENCE_SHA, "execution_profile_sha256": PROFILE_SHA,
            "files": {key: {"path": str(path.resolve()), "sha256": inputs.file(path)} for key, path in references.items()},
            "all_original_input_files": 1654, "source_sha256": inputs.file(helper), "scheduler": config["scheduler"],
            "directory": str(root.resolve()), "files_sha256": files, "driver_returncode": state.get("driver_returncode"),
            "recorded_operational_error": state.get("error"),
            "config_path": str((root / "CONFIG.json").resolve()), "resume_path": str((root / "RESUMED.json").resolve()),
            "held_journal_path": str((held / "ORIGINAL_HELD_JOURNAL.json").resolve()),
            "held_journal_sha256": inputs.file(held / "ORIGINAL_HELD_JOURNAL.json"),
            "held_session_path": str((held / "ORIGINAL_HELD_SESSION.json").resolve()),
            "held_session_sha256": inputs.file(held / "ORIGINAL_HELD_SESSION.json"),
            "original_attempt_files_expected": len(inventory),
            "recorded_original_results_sha256": drained["results_sha256"],
            "recorded_original_schedule_sha256": drained["schedule_sha256"],
            "first_contention_action_utc_epoch": first, "last_contention_host_result_utc_epoch": last,
            "resumed_at_utc": resumed["resumed_at_utc"]}
    if attempt is None:
        return proof
    actual = inputs.tree(attempt)
    require(set(actual) <= set(inventory) | {"SCIENTIFIC_VALIDATION.json"} and set(inventory) <= set(actual),
            "Original attempt inventory changed outside documented parent finalization")
    for name, identity in inventory.items():
        if name == "session.json":
            require(inputs.file(held / "ORIGINAL_HELD_SESSION.json") == identity["sha256"], "Held session bytes differ from frozen attempt")
            finalized = inputs.json(attempt / name)
            old, new = held_session["attempt"], finalized["attempt"]
            allowed = {"ended_at_utc", "orchestrator_wall_seconds", "status", "returncode", "artifacts"}
            require({k: v for k, v in new.items() if k not in allowed} == {k: v for k, v in old.items() if k not in allowed}
                    and finalized["session"] == held_session["session"] and new.get("status") == "succeeded"
                    and new.get("returncode") == 0, "Original parent finalization changed identity or failed")
        else:
            require(actual[name] == identity["sha256"] and (attempt / name).stat().st_size == identity["bytes"],
                    "Original finished worker payload changed: " + name)
    original = inputs.json(contained(attempt, result_relative))
    schedule = inputs.json(contained(attempt, schedule_relative))
    require(inputs.file(contained(attempt, result_relative)) == drained["results_sha256"] and
            inputs.file(contained(attempt, schedule_relative)) == drained["schedule_sha256"], "Original drained endpoint bytes differ")
    require(drained.get("runner_exit", {}).get("exit_code") == 0 and len(schedule) == 1
            and schedule[0].get("status") == "complete" and schedule[0].get("returncode") == 0
            and schedule[0].get("engine") == planned["engine"] and schedule[0].get("scale") == planned["scale"]
            and original.get("engine") == planned["engine"] and original.get("suite") == planned["suite"]
            and original.get("manifest_sha256") == reference_plan["datasets"][planned["scale"]]["manifest_sha256"]
            and original.get("completed_at_utc") and not original.get("failure"), "Original drain did not finish its actual task")
    original_end = max(instant(r["host_result_ended_at_utc"]) for r in original["records"])
    require(instant(original["completed_at_utc"]) <= instant(drained["completed_at_utc"]) <= instant(before["completed_at_utc"]),
            "Contention was released before the original worker/profile verification finished")
    if first is not None:
        require(original_end <= first and instant(before["completed_at_utc"]) <= first and
                last <= instant(after["completed_at_utc"]) <= instant(resumed["resumed_at_utc"]),
                "Contention actions overlap original work or actual resume")
    references.update(original_results=contained(attempt, result_relative), original_schedule=contained(attempt, schedule_relative))
    proof.update(status="PASS", no_action_overlap=True, original_payload_verified=True, pending_reasons=[])
    proof.update({"files": {key: {"path": str(path.resolve()), "sha256": inputs.file(path)} for key, path in references.items()},
            "original_attempt_directory": str(attempt.resolve()), "original_attempt_files_verified": len(inventory),
            "original_results_path": str(contained(attempt, result_relative).resolve()), "original_results_sha256": drained["results_sha256"],
            "original_schedule_path": str(contained(attempt, schedule_relative).resolve()), "original_schedule_sha256": drained["schedule_sha256"],
            "original_worker_last_host_result_at_utc": original_end, "first_contention_action_utc_epoch": first,
            "last_contention_host_result_utc_epoch": last, "resumed_at_utc": resumed["resumed_at_utc"]})
    return proof


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def csv_rows(path, rows, fields):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def replay(args):
    output = Path(args.output).resolve()
    require(not output.exists(), "Replay output must be a new directory")
    protected = [args.archive, args.raw_backups, args.final_metadata, args.handoff, args.held_original, args.original_attempt]
    require(not any(output.is_relative_to(Path(root).resolve()) for root in protected if root is not None),
            "Replay output cannot be inside immutable audit inputs")
    output.mkdir(parents=True)
    inputs = Inputs()
    report = {"schema_version": "h100-contention-replay-v1", "experiment_id": EXPERIMENT_ID,
              "synthetic": False, "status": "PARTIAL", "integrity_status": "PENDING", "publication_ready": False,
              "plan_sha256": PLAN_SHA, "prestart_sha256": PRESTART_SHA, "audit_source_sha256": inputs.file(__file__),
              "started_at_utc": utcnow(), "findings": [], "scope": "Separate descriptive contention only; no primary400 inference"}
    report["analysis_runtime"] = {"python": sys.version, "packages": {}}
    (output / "AUDITOR_SOURCE.py").write_bytes(Path(__file__).read_bytes())
    for name in ("psutil", "numpy", "scipy"):
        try:
            report["analysis_runtime"]["packages"][name] = package_metadata.version(name)
        except package_metadata.PackageNotFoundError:
            report["analysis_runtime"]["packages"][name] = None
    try:
        archive, plan, profile, canonical_inventory, runner = verify_canonical(args.archive, inputs)
        report["canonical_files_sha256"] = canonical_inventory
        report["canonical_directory"] = str(archive)
        report["canonical_plan"] = {"path": str(archive / "CONTENTION_PLAN.json"), "sha256": PLAN_SHA}
        report["canonical_prestart"] = {"path": str(archive / "CONTENTION_PRESTART_MANIFEST.json"), "sha256": PRESTART_SHA}
        report["reference_campaign_sha256"] = REFERENCE_SHA
        report["reference_execution_profile_sha256"] = PROFILE_SHA
        snapshots, raw = verify_snapshots(args.raw_backups, canonical_inventory, inputs)
        report["snapshots"] = snapshots
        adapted, changes = adapted_plan(plan, archive)
        write_json(output / "PATH_ADAPTER.json", {"schema_version": "h100-contention-path-adapter-v1",
                   "raw_files_rewritten": False, "changes": changes, "scope": "Derived in-memory source/oracle lookup paths only"})
        disposition, summaries = {}, []
        with frozen_imports(archive / "FROZEN_SOURCE"):
            for phase in ("pilot", "main"):
                if raw is None:
                    disposition[phase] = {"status": "NOT_OBSERVED", "planned_waves": len(plan[phase]), "attempted_waves": 0,
                        "passed_waves": 0, "failed_waves": 0, "unattempted_waves": [w["wave_id"] for w in plan[phase]]}
                else:
                    disposition[phase], rows = audit_phase(plan, adapted, runner, phase, raw, archive, inputs)
                    summaries.extend(rows)
        require(disposition["main"]["attempted_waves"] == 0 or disposition["pilot"]["status"] == "PASS",
                "Expansion ran without the complete pilot gate")
        report["disposition"] = disposition
        report["disposition_scope"] = "Selected immutable off-host snapshots only; absent future phases are not claims that they never ran."
        actions = [row for wave in summaries for row in wave.get("action_rows", [])]
        overlaps = [dict(row, wave_id=wave["wave_id"]) for wave in summaries for row in wave.get("overlap", [])]
        report["waves"] = [{k: v for k, v in wave.items() if k != "action_rows"} for wave in summaries]
        report["counts"] = {"attempted_waves": len(summaries), "recorded_outputs": len(actions),
                            "successful_outputs": sum(r["oracle_passed"] for r in actions),
                            "oracle_failed_outputs": sum(not r["oracle_passed"] for r in actions),
                            "measured_outputs": sum(r["phase"] == "measured" for r in actions),
                            "warmup_outputs": sum(r["phase"] == "warmup" for r in actions),
                            "common_measured_pairs": len(overlaps),
                            "unpaired_cpu_measured_diagnostics": sum(not r["paired_condition"] for r in actions),
                            "expected_strict_rejections_observed": sum(e["expected_strict_rejection"] for w in summaries
                                for m in w.get("members", []) for e in m.get("recorded_query_errors", [])),
                            "unexpected_query_errors_observed": sum(not e["expected_strict_rejection"] for w in summaries
                                for m in w.get("members", []) for e in m.get("recorded_query_errors", []))}
        terminal = terminal_driver(args.final_metadata, plan, snapshots, disposition, inputs)
        handoff = handoff_proof(args.handoff, args.held_original, args.original_attempt, actions, inputs,
                                role_plan=plan, reference_plan=inputs.json(archive / "REFERENCE_CAMPAIGN.json"))
        report["terminal_driver"], report["handoff"] = terminal, handoff
        if terminal["terminal"] and handoff["status"] == "PASS":
            if terminal["status"] == "COMPLETE_OFFHOST":
                require(report["counts"]["successful_outputs"] == 460 and report["counts"]["expected_strict_rejections_observed"] == 10,
                        "Completed contention output/rejection accounting differs")
                report["status"] = "COMPLETE"
            else:
                report["status"] = "STOPPED_WITH_FAILURE"
                for value in disposition.values():
                    if value["status"] == "NOT_OBSERVED":
                        value["status"] = "NOT_ATTEMPTED_AFTER_STOP"
                report["disposition_scope"] = "Terminal source-bound stopped experiment; unattempted waves remain explicitly unmeasured."
            report["publication_ready"] = True
        report["integrity_status"] = "PASS"
        fields = ["wave_id", "pair", "suite", "scale", "repetition", "config_id", "query", "phase", "trial", "seconds",
                  "host_result_seconds", "paired_condition", "oracle_passed", "action_started_at_utc", "action_ended_at_utc", "host_result_ended_at_utc"]
        csv_rows(output / "QUERY_ACTIONS_DESCRIPTIVE.csv", actions, fields)
        csv_rows(output / "COMMON_QUERY_OVERLAP.csv", overlaps,
                 ["wave_id", "query", "phase", "trial", "overlap_seconds", "utc_overlap_seconds", "actual_start_skew_seconds",
                  "left_seconds", "right_seconds", "positive_actual_overlap"])
        inputs.recheck()
    except Exception as exc:
        report.update(status="FAIL", integrity_status="FAIL", publication_ready=False)
        report["findings"].append({"type": type(exc).__name__, "message": str(exc)})
    report["completed_at_utc"] = utcnow()
    report["input_files_sha256"] = inputs.hashes
    report["output_files_sha256"] = {p.name: sha(p) for p in sorted(output.iterdir()) if p.is_file()}
    write_json(output / "CONTENTION_REPLAY.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--raw-backups", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--final-metadata", type=Path)
    parser.add_argument("--handoff", type=Path)
    parser.add_argument("--held-original", type=Path)
    parser.add_argument("--original-attempt", type=Path)
    args = parser.parse_args(argv)
    result = replay(args)
    print(json.dumps({"status": result["status"], "integrity_status": result["integrity_status"],
                      "publication_ready": result["publication_ready"], "findings": result["findings"]}, indent=2))
    return 1 if result["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
