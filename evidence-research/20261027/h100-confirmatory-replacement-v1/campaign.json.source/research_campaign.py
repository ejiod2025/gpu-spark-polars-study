#!/usr/bin/env python3
"""Freeze and execute independent, paired H100 research sessions.

Planning and execution are deliberately separate. A campaign contains ten blocks,
eight suite/scale cells per block, and five fresh engine sessions per cell. The
three inner trials remain descriptive measurements nested inside each session.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import psutil

HERE = Path(__file__).resolve().parent
SCHEMA = "research-campaign-v1"
JOURNAL_SCHEMA = "research-campaign-journal-v1"
CONFIG_ENGINES = {
    "polars_cpu_144": "polars", "polars_cpu_200": "polars",
    "polars_gpu": "polars_gpu", "spark_cpu": "spark_cpu", "spark_gpu": "spark_gpu",
}
COMMON_PACKAGES = ("duckdb", "numpy", "packaging", "psutil", "py4j", "pyarrow", "pyspark", "typing-extensions")
CELLS = [("baseline", scale) for scale in ("1", "10", "30", "100", "300")] + [
    ("complex", scale) for scale in ("30", "100", "300")]
LAUNCH_GATE = ("import os,sys\n"
               "if sys.stdin.buffer.read(1) != b'G': raise SystemExit(99)\n"
               "os.execv(sys.argv[1], sys.argv[1:])\n")


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def interrupt_campaign(number, _frame):
    raise InterruptedError(f"Campaign received signal {number}")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(canonical_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def source_files(root=None):
    root = HERE if root is None else Path(root)
    fixed = ("docs/H100_CONFIRMATORY_PROTOCOL.md", "setup_research_control.sh", "pytest.ini")
    return sorted({*root.glob("*.py"), *root.glob("tests/*.py"), *root.glob("requirements*.txt"),
                   *(root / relative for relative in fixed)})


def source_identity(root=None):
    root = HERE if root is None else Path(root)
    files = {str(p.relative_to(root)): sha256(p) for p in source_files(root)}
    return {"files_sha256": files,
            "source_tree_sha256": hashlib.sha256(canonical_bytes(files)).hexdigest()}


def runtime_identity(python):
    probe = ("import sys,json,platform,importlib.metadata as m; from pathlib import Path; "
             "print(json.dumps({'python_version':sys.version,'python_info':list(sys.version_info[:3]),"
             "'python_build':platform.python_build(),'base_executable':str(Path(sys.executable).resolve()),"
             "'distributions':dict(sorted((d.metadata['Name'].lower().replace('_','-'),d.version) for d in m.distributions()))}))")
    return json.loads(subprocess.check_output([python, "-c", probe], text=True, timeout=120))


def williams_orders(config_ids, rng):
    """Odd-five Williams design: every position twice, directed predecessor twice."""
    if len(config_ids) != 5 or len(set(config_ids)) != 5:
        raise ValueError("Williams design requires five unique configurations")
    labels = list(config_ids)
    rng.shuffle(labels)
    base = [0, 1, 4, 2, 3]
    rows = [[labels[(position + shift) % 5] for position in base] for shift in range(5)]
    rows += [list(reversed(row)) for row in rows]
    rng.shuffle(rows)
    return rows


def make_schedule(configs, seed, output_root):
    rng = random.Random(seed)
    ids = [config["id"] for config in configs]
    if set(ids) != set(CONFIG_ENGINES) or len(ids) != 5:
        raise ValueError("Campaign requires the five distinct predefined configurations")
    if any(config["engine"] != CONFIG_ENGINES[config["id"]] for config in configs):
        raise ValueError("Configuration-to-engine mapping differs from the research protocol")
    config_map = {config["id"]: config for config in configs}
    designs = {cell: williams_orders(ids, rng) for cell in CELLS}
    blocks, sessions = [], []
    for block_number in range(1, 11):
        block_id = f"block-{block_number:02d}"
        cells = list(CELLS)
        rng.shuffle(cells)
        block = {"block_id": block_id, "cells": []}
        for suite, scale in cells:
            cell_id = f"{suite}-sf{scale}"
            order = designs[(suite, scale)][block_number - 1]
            query_seed = rng.randrange(2**31)
            cell = {"cell_id": cell_id, "suite": suite, "scale": scale,
                    "query_seed": query_seed, "config_order": order, "session_ids": []}
            for position, config_id in enumerate(order, 1):
                config = config_map[config_id]
                session_id = f"{block_id}-{cell_id}-{config_id}"
                cell["session_ids"].append(session_id)
                sessions.append({"session_id": session_id, "block_id": block_id,
                                 "cell_id": cell_id, "suite": suite, "scale": scale,
                                 "config_id": config_id, "engine": config["engine"],
                                 "python": config["python"], "query_seed": query_seed,
                                 "position": position, "output_root": str(output_root / session_id)})
            block["cells"].append(cell)
        blocks.append(block)
    return blocks, sessions


def primary_contrasts():
    contrasts = []
    for numerator, denominator, cells in (
            ("polars_cpu_200", "spark_gpu", CELLS), ("spark_cpu", "spark_gpu", CELLS),
            ("polars_cpu_144", "polars_gpu", CELLS[:5]), ("polars_cpu_200", "polars_gpu", CELLS[:5])):
        for suite, scale in cells:
            contrasts.append({"contrast_id": f"{numerator}_over_{denominator}-{suite}-sf{scale}",
                              "numerator": numerator, "denominator": denominator,
                              "suite": suite, "scale": scale})
    return contrasts


def validate_readiness_fixtures(readiness):
    """Bind five real fixture reports, including explicit strict-GPU coverage gaps."""
    from analyze_results import check_polars_gpu_metadata, compare_values
    import research_fixtures as fixtures

    entries = readiness.get("semantic_fixtures", [])
    config_ids = [entry.get("config_id") for entry in entries]
    if len(entries) != 5 or set(config_ids) != set(CONFIG_ENGINES):
        raise ValueError("Readiness must bind exactly five distinct semantic fixture configurations")
    frozen = {}
    for entry in entries:
        config_id = entry["config_id"]
        engine = CONFIG_ENGINES[config_id]
        root = Path(entry["output"]).resolve()
        path = root / "FIXTURES.json"
        digest = sha256(path)
        if digest != entry.get("report_sha256"):
            raise ValueError(f"Semantic fixture report bytes changed: {config_id}")
        value = json.loads(path.read_text())
        version = "2.0.0" if config_id == "polars_cpu_200" else "1.44.2" if engine.startswith("polars") else "3.5.9"
        if (value.get("schema_version") != "research-fixtures-v1" or
                value.get("suite_version") != fixtures.SUITE_VERSION or value.get("engine") != engine or
                value.get("version") != version or value.get("status") != "PASS" or
                not value.get("completed_at_utc") or set(value.get("queries", {})) != set(fixtures.QUERY_IDS)):
            raise ValueError(f"Incomplete or invalid semantic fixture identity/status: {config_id}")
        if engine == "polars_gpu":
            check_polars_gpu_metadata({**value, "polars_execution_engine": "gpu"})
        supported, unsupported, plans = 0, [], {}
        for query in fixtures.QUERY_IDS:
            record = value["queries"][query]
            if record.get("status") == "UNSUPPORTED":
                message = record.get("error", "")
                if (engine != "polars_gpu" or query not in {"fx_rank_ties", "fx_regex_distinct"} or
                        not isinstance(message, str) or not any(token in message.lower() for token in (
                            "notimplementederror", "query execution with gpu not possible: unsupported operations."))):
                    raise ValueError(f"Invalid semantic fixture rejection: {config_id}/{query}")
                unsupported.append(query)
                continue
            if (record.get("status") != "PASS" or record.get("comparison", {}).get("passed") is not True or
                    not compare_values(fixtures.EXPECTED_ROWS[query], record.get("rows", []))["passed"]):
                raise ValueError(f"Semantic fixture result did not pass: {config_id}/{query}")
            supported += 1
            plan = root / record["plan"]
            if not plan.resolve().is_relative_to(root) or not plan.is_file():
                raise ValueError(f"Missing/unsafe semantic fixture plan: {config_id}/{query}")
            plans[record["plan"]] = sha256(plan)
        if (value.get("supported_queries") != supported or value.get("unsupported_queries") != unsupported or
                entry.get("supported_queries") != supported or entry.get("unsupported_queries") != unsupported):
            raise ValueError(f"Semantic fixture coverage counts differ: {config_id}")
        anti_passed = value["queries"]["fx_semi_anti"].get("status") == "PASS"
        if value.get("nonempty_anti_semantics_passed") is not anti_passed or (engine != "polars_gpu" and not anti_passed):
            raise ValueError(f"Nonempty anti semantic fixture did not pass: {config_id}")
        if engine == "spark_gpu":
            anti_plan = (root / value["queries"]["fx_semi_anti"]["plan"]).read_text()
            final_plan = anti_plan.split("== Final Plan ==", 1)[-1].split("== Initial Plan ==", 1)[0]
            observed_gpu_anti = any("Gpu" in line and "LeftAnti" in line for line in final_plan.splitlines())
            if (value.get("gpu_left_anti_plan_observed") is not True or
                    entry.get("gpu_left_anti_plan_observed") is not True or not observed_gpu_anti):
                raise ValueError("GPU Spark semantic fixture must retain an observed GPU LeftAnti final plan")
        frozen[config_id] = {"path": str(path), "sha256": digest, "plans_sha256": plans}
    return frozen


def freeze_plan(args):
    path = args.plan.absolute()
    snapshot = path.with_name(path.name + ".source")
    digest_path = path.with_name(path.name + ".sha256")
    for reserved in (path, snapshot, digest_path):
        if reserved.exists():
            raise ValueError(f"Refusing to overwrite campaign artifact {reserved}")
    if args.output_root.exists() and any(args.output_root.iterdir()):
        raise ValueError("Campaign output root must be absent or empty at planning")
    data_root, output_root = args.data_root.resolve(), args.output_root.absolute()
    configs = [{"id": name, "engine": engine,
                # Preserve venv symlinks: resolving python can silently select its base environment.
                "python": os.path.abspath(args.polars_cpu_200_python if name == "polars_cpu_200"
                                           else args.python)} for name, engine in CONFIG_ENGINES.items()]
    for config in configs:
        if not Path(config["python"]).is_file():
            raise ValueError(f"Missing interpreter {config['python']}")
    runtimes = {python: runtime_identity(python) for python in {config["python"] for config in configs}}
    primary = runtimes[configs[0]["python"]]
    for config in configs:
        runtime = runtimes[config["python"]]
        if (runtime["python_info"] != [3, 11, 13] or any(runtime[field] != primary[field]
                for field in ("python_version", "python_build", "base_executable"))):
            raise ValueError("Both environments must use the same CPython 3.11.13 executable and build")
        if any(not runtime["distributions"].get(package) or runtime["distributions"].get(package) !=
               primary["distributions"].get(package) for package in COMMON_PACKAGES):
            raise ValueError("Common non-GPU package versions must match across environments")
        expected = "2.0.0" if config["id"] == "polars_cpu_200" else "1.44.2"
        if runtime["distributions"].get("polars") != expected:
            raise ValueError(f"{config['id']} requires Polars {expected}")
        config["runtime"] = runtime
    datasets = {}
    for scale in sorted({scale for _, scale in CELLS}, key=int):
        manifest = data_root / f"sf{scale}" / "manifest.json"
        value = json.loads(manifest.read_text())
        if float(value["scale_factor"]) != float(scale):
            raise ValueError(f"Manifest scale differs from SF{scale}")
        datasets[scale] = {"path": str(manifest.parent), "manifest_sha256": sha256(manifest),
                           "total_parquet_bytes": value.get("total_parquet_bytes")}
    if not args.jar.is_file():
        raise ValueError(f"Missing plugin jar {args.jar}")
    blocks, sessions = make_schedule(configs, args.seed, output_root)
    source = source_identity()
    readiness = json.loads(args.readiness.read_text())
    expected_readiness = {(suite, config) for suite in ("baseline", "complex") for config in CONFIG_ENGINES}
    observed_readiness = {(entry.get("suite"), entry.get("config_id")) for entry in readiness.get("sessions", [])}
    if (readiness.get("status") != "PASS" or readiness.get("errors") or
            readiness.get("source", {}).get("files_sha256") != source["files_sha256"] or
            not expected_readiness.issubset(observed_readiness) or
            readiness.get("successful_outputs_compared", 0) < 116 or
            len(readiness.get("sessions", [])) < 10):
        raise ValueError("Readiness must PASS all ten sessions/116 outputs at the exact frozen source")
    semantic_fixtures = validate_readiness_fixtures(readiness)
    from research_validate import load_oracle
    oracles = {}
    for suite, scale in CELLS:
        oracle = args.oracle_root / f"{suite}-sf{scale}.json"
        load_oracle(oracle, suite, scale, datasets[scale]["manifest_sha256"])
        oracles[f"{suite}-sf{scale}"] = {"path": str(oracle.resolve()), "sha256": sha256(oracle)}
    source["snapshot_directory"] = str(snapshot)
    try:
        source["git_head"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=HERE, text=True).strip()
        source["git_status"] = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=HERE, text=True)
    except (OSError, subprocess.CalledProcessError):
        source["git_head"] = source["git_status"] = None
    plan = {"schema_version": SCHEMA, "campaign_id": args.campaign_id or path.stem,
            "created_at_utc": utcnow(), "seed": args.seed,
            "protocol_sha256": source["files_sha256"]["docs/H100_CONFIRMATORY_PROTOCOL.md"],
            "analysis_predeclarations": {"primary_endpoint": "seconds", "inner_summary": "median of three",
                "suite_score": "sum of six query medians; complete cases only",
                "block_estimator": "geometric mean of paired block ratios",
                "confidence_interval": "two-sided Student t, 95%, log ratio",
                "primary_family_size": 26, "familywise_alpha": 0.05, "multiplicity": "Holm"},
            "primary_contrasts": primary_contrasts(),
            "design_name": "odd-five Williams: five cyclic shifts plus reversals; randomized labels and rows per cell",
            "inference_unit": "paired fresh-process sessions within independent block; inner trials are nested",
            "data_root": str(data_root), "output_root": str(output_root), "source": source,
            "oracles": oracles, "readiness": {"path": str(args.readiness.resolve()), "sha256": sha256(args.readiness)},
            "semantic_fixtures": semantic_fixtures,
            "datasets": datasets, "configs": configs, "blocks": blocks, "sessions": sessions,
            "settings": {"runs": 3, "cpus": args.cpus, "driver_memory": args.driver_memory,
                         "query_cleanup_by_suite": {"baseline": "none", "complex": "verified_gc"},
                         "engine_timeout_seconds": args.engine_timeout_seconds,
                         "cleanup_timeout_seconds": args.cleanup_timeout_seconds,
                         "jar": str(args.jar.resolve()), "jar_sha256": sha256(args.jar),
                         "spark_gpu_profile": "a100-parity", "polars_gpu_executor": "streaming",
                         "cache_observation": True}}
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot.mkdir()
    for relative in source["files_sha256"]:
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HERE / relative, target)
        if sha256(target) != source["files_sha256"][relative]:
            raise ValueError(f"Source changed while freezing {relative}")
    payload = canonical_bytes(plan)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    with digest_path.open("x") as handle:
        handle.write(hashlib.sha256(payload).hexdigest() + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return plan


def load_plan(path):
    path = Path(path)
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != path.with_name(path.name + ".sha256").read_text().strip():
        raise ValueError("Campaign plan checksum differs; refusing modified schedule")
    plan = json.loads(payload)
    if plan.get("schema_version") != SCHEMA or len(plan.get("sessions", [])) != 400:
        raise ValueError("Not a complete research-campaign-v1 plan")
    if plan.get("primary_contrasts") != primary_contrasts():
        raise ValueError("Primary contrast family differs from the frozen protocol")
    if set(plan.get("oracles", {})) != {f"{suite}-sf{scale}" for suite, scale in CELLS}:
        raise ValueError("Campaign must freeze all eight independent oracle identities")
    if set(plan.get("semantic_fixtures", {})) != set(CONFIG_ENGINES):
        raise ValueError("Campaign must freeze all five semantic fixture identities")
    if plan.get("protocol_sha256") != plan["source"]["files_sha256"].get("docs/H100_CONFIRMATORY_PROTOCOL.md"):
        raise ValueError("Protocol identity differs from the frozen source snapshot")
    ids = [session["session_id"] for session in plan["sessions"]]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate session IDs")
    # Check the randomized design against its frozen seed, rather than trusting a count alone.
    blocks, sessions = make_schedule(plan["configs"], plan["seed"], Path(plan["output_root"]))
    if blocks != plan["blocks"] or sessions != plan["sessions"]:
        raise ValueError("Frozen schedule does not match the declared design/seed")
    return plan, digest


def check_frozen_inputs(plan, *, include_jar=False):
    if source_identity()["files_sha256"] != plan["source"]["files_sha256"]:
        raise ValueError("Source drift since campaign planning; freeze a new campaign")
    for relative, expected in plan["source"]["files_sha256"].items():
        if sha256(Path(plan["source"]["snapshot_directory"]) / relative) != expected:
            raise ValueError(f"Frozen source snapshot changed: {relative}")
    for scale, dataset in plan["datasets"].items():
        if sha256(Path(dataset["path"]) / "manifest.json") != dataset["manifest_sha256"]:
            raise ValueError(f"Input manifest drift at SF{scale}")
    for cell_id, oracle in plan["oracles"].items():
        if sha256(oracle["path"]) != oracle["sha256"]:
            raise ValueError(f"Frozen oracle changed: {cell_id}")
    if sha256(plan["readiness"]["path"]) != plan["readiness"]["sha256"]:
        raise ValueError("Frozen readiness evidence changed")
    readiness = json.loads(Path(plan["readiness"]["path"]).read_text())
    if validate_readiness_fixtures(readiness) != plan["semantic_fixtures"]:
        raise ValueError("Frozen semantic fixture evidence changed")
    if include_jar and sha256(plan["settings"]["jar"]) != plan["settings"]["jar_sha256"]:
        raise ValueError("Plugin jar changed since planning")


def check_frozen_runtimes(plan):
    observed = {}
    for config in plan["configs"]:
        python = config["python"]
        if python not in observed:
            observed[python] = runtime_identity(python)
        if observed[python] != config["runtime"]:
            raise ValueError(f"Frozen runtime changed for {config['id']}")


def observe_exclusive_host():
    """Fresh sessions have no legitimate preexisting GPU client or benchmark worker."""
    try:
        probe = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                                "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=30)
        gpu = {"nvidia_smi_returncode": probe.returncode, "gpu_compute_clients": probe.stdout.strip(),
               "nvidia_smi_stderr": probe.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        gpu = {"nvidia_smi_returncode": None, "gpu_compute_clients": "",
               "nvidia_smi_stderr": f"{type(exc).__name__}: {exc}"}
    blockers = []
    known_scripts = {"worker.py", "run_experiment.py", "research_oracle.py", "complex_oracle.py"}
    known_modules = {"org.apache.spark.deploy.SparkSubmit", "pyspark.daemon", "pyspark.worker"}
    for process in psutil.process_iter(["pid", "name", "cmdline", "status"]):
        if process.pid == os.getpid() or process.info["status"] == psutil.STATUS_ZOMBIE:
            continue
        command = process.info.get("cmdline") or []
        markers = [arg for arg in command if Path(arg).name in known_scripts or arg in known_modules]
        if markers:
            blockers.append({"pid": process.pid, "name": process.info["name"], "markers": markers})
    return {"observed_at_utc": utcnow(), **gpu,
            "benchmark_processes": blockers,
            "eligible": gpu["nvidia_smi_returncode"] == 0 and not gpu["gpu_compute_clients"] and not blockers}


def verify_dataset_bytes(plan):
    """Full byte gate outside timed sessions; manifests are also checked before every session."""
    checks = []
    for scale, dataset in plan["datasets"].items():
        root = Path(dataset["path"])
        manifest = json.loads((root / "manifest.json").read_text())
        expected = [entry for table in manifest["tables"].values() for entry in table["files"]]
        paths = [entry["path"] for entry in expected]
        if len(paths) != len(set(paths)):
            raise ValueError(f"Duplicate input file paths at SF{scale}")
        actual = {str(path.relative_to(root)) for path in root.glob("*/*.parquet")}
        if actual != set(paths):
            raise ValueError(f"Missing/extra Parquet input files at SF{scale}")
        for entry in expected:
            path = root / entry["path"]
            if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
                raise ValueError(f"Unsafe input path at SF{scale}: {entry['path']}")
            if path.stat().st_size != entry["bytes"] or sha256(path) != entry["sha256"]:
                raise ValueError(f"Input bytes differ at SF{scale}: {entry['path']}")
        checks.append({"scale": scale, "files": len(paths), "status": "pass"})
    return {"completed_at_utc": utcnow(), "checks": checks}


def pid_matches(attempt):
    try:
        process = psutil.Process(attempt["pid"])
        return (process.status() != psutil.STATUS_ZOMBIE and
                abs(process.create_time() - attempt["pid_create_time"]) < 0.01)
    except (psutil.NoSuchProcess, KeyError):
        return False


def group_members(pgid):
    if not isinstance(pgid, int) or pgid <= 1:
        return []
    members = []
    for process in psutil.process_iter(["pid", "status"]):
        try:
            if process.info["status"] != psutil.STATUS_ZOMBIE and os.getpgid(process.pid) == pgid:
                members.append(process.pid)
        except (ProcessLookupError, psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return members


def terminate_owned_group(process, grace=25):
    """TERM lets the runner reap its separate worker group; KILL is the last resort."""
    # Capture descendants now, since a killed runner can no longer identify its worker PGID.
    owned = {}
    try:
        for child in psutil.Process(process.pid).children(recursive=True):
            try:
                owned[child.pid] = (child.create_time(), os.getpgid(child.pid))
            except (psutil.NoSuchProcess, ProcessLookupError):
                pass
    except psutil.NoSuchProcess:
        pass
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + grace
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    for pid, (created, pgid) in owned.items():
        if pid_matches({"pid": pid, "pid_create_time": created}):
            try:
                # Descendants inherited from this session only; never kill our caller's PGID.
                if pgid != os.getpgrp():
                    os.killpg(pgid, signal.SIGKILL)
                else:
                    os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()
    remaining = group_members(process.pid) + [pid for pid, (created, _) in owned.items()
                                             if pid_matches({"pid": pid, "pid_create_time": created})]
    deadline = time.monotonic() + 5
    while remaining and time.monotonic() < deadline:
        time.sleep(0.05)
        remaining = group_members(process.pid) + [pid for pid, (created, _) in owned.items()
                                                 if pid_matches({"pid": pid, "pid_create_time": created})]
    if remaining:
        raise RuntimeError(f"Owned benchmark descendants remain alive: {sorted(set(remaining))}")


@contextmanager
def campaign_lock(path):
    with Path(path).open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another executor holds this campaign lock") from exc
        handle.seek(0)
        handle.truncate()
        json.dump({"pid": os.getpid(), "started_at_utc": utcnow()}, handle)
        handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def reconcile_journal(journal, recover_interrupted=False):
    for session_id, item in journal["sessions"].items():
        if item["status"] != "running":
            continue
        attempt = item["attempts"][-1]
        if pid_matches(attempt):
            raise RuntimeError(f"Refusing resume: {session_id} still has live PID {attempt['pid']}")
        if group_members(attempt["pgid"]):
            raise RuntimeError(f"Refusing resume: stale session {session_id} still has a live process group")
        if not recover_interrupted:
            raise RuntimeError(f"Stale running session {session_id}; inspect it, then use --recover-interrupted")
        attempt.update(status="interrupted", ended_at_utc=utcnow(),
                       recovery_reason="explicit recovery of stale process identity; no live process group")
        item["status"] = "interrupted"


def command_for(plan, session, run_root):
    settings = plan["settings"]
    return [session["python"], str(HERE / "run_experiment.py"),
            "--data-root", plan["data_root"], "--output", str(run_root),
            "--engines", session["engine"], "--scales", session["scale"],
            "--suite", session["suite"], "--runs", str(settings["runs"]),
            "--query-seed", str(session["query_seed"]), "--cpus", str(settings["cpus"]),
            "--driver-memory", settings["driver_memory"], "--jar", settings["jar"],
            "--query-cleanup", settings["query_cleanup_by_suite"][session["suite"]],
            "--cleanup-timeout-seconds", str(settings["cleanup_timeout_seconds"]),
            "--engine-timeout-seconds", str(settings["engine_timeout_seconds"]),
            "--cache-observation", "--spark-gpu-profile", settings["spark_gpu_profile"],
            "--polars-gpu-executor", settings["polars_gpu_executor"]]


def completed_artifacts(plan, session, run_root):
    """Operational completeness only; independent correctness/GPU validation is downstream."""
    schedule = json.loads((run_root / "schedule.json").read_text())
    if len(schedule) != 1:
        raise ValueError("Expected exactly one engine session")
    item = schedule[0]
    if (item.get("status"), item.get("returncode"), item.get("engine"), item.get("scale")) != (
            "complete", 0, session["engine"], session["scale"]):
        raise ValueError("Runner schedule is not complete for the planned session")
    if group_members(item.get("pgid")):
        raise ValueError("Owned worker process group remains alive after the runner exited")
    observations = item.get("cache_observations", {})
    if set(observations) != {"before_preheat", "after_preheat", "after_worker"} or any(
            value["cache"].get("errors") for value in observations.values()):
        raise ValueError("Required cache observations are incomplete")
    results_path = run_root / f"sf{session['scale']}" / session["engine"] / "results.json"
    result = json.loads(results_path.read_text())
    if (result.get("engine"), result.get("suite"), result.get("runs")) != (
            session["engine"], session["suite"], plan["settings"]["runs"]):
        raise ValueError("Worker metadata does not match the planned session")
    if result.get("failure") or not result.get("completed_at_utc"):
        raise ValueError("Worker did not complete")
    if result.get("seed", result.get("query_seed")) != session["query_seed"]:
        raise ValueError("Worker query seed differs from frozen paired schedule")
    if result.get("manifest_sha256") != plan["datasets"][session["scale"]]["manifest_sha256"]:
        raise ValueError("Worker dataset differs from the frozen campaign")
    return {"results_path": str(results_path), "results_sha256": sha256(results_path),
            "schedule_sha256": sha256(run_root / "schedule.json"),
            "query_execution_status": result.get("query_execution_status")}


def scientific_gate(plan, digest, session, item, journal, journal_path):
    from research_validate import audit_session, load_oracle
    oracle_file = plan["oracles"][session["cell_id"]]
    oracle = load_oracle(oracle_file["path"], session["suite"], session["scale"],
                         plan["datasets"][session["scale"]]["manifest_sha256"])
    path = Path(item["attempts"][-1]["attempt_root"]) / "SCIENTIFIC_VALIDATION.json"
    previous = item.get("scientific_validation")
    if previous and (previous.get("path") != str(path) or sha256(path) != previous.get("sha256")):
        raise ValueError(f"Scientific validation evidence changed: {session['session_id']}")
    if previous and previous.get("status") != "PASS":
        raise ValueError("Scientific FAIL is retained; a reviewed protocol change/new campaign is required")
    audit = audit_session({**plan, "_sha256": digest}, session, item, oracle)
    if path.exists():
        if json.loads(path.read_text()) != audit:
            raise ValueError(f"Saved scientific audit differs from current evidence: {session['session_id']}")
    else:
        atomic_json(path, audit)
    item["scientific_validation"] = {"path": str(path), "sha256": sha256(path), "status": audit["status"]}
    atomic_json(journal_path, journal)
    if audit["status"] != "PASS":
        raise ValueError(f"Scientific validation failed: {session['session_id']}; see {path}")
    return audit


def execute_session(plan, digest, session, item, journal, journal_path):
    attempt_number = len(item["attempts"]) + 1
    attempt_root = Path(session["output_root"]) / f"attempt-{attempt_number:03d}"
    attempt_root.mkdir(parents=True, exist_ok=False)
    run_root = attempt_root / "run"
    command = command_for(plan, session, run_root)
    attempt = {"attempt": attempt_number, "status": "running", "started_at_utc": utcnow(),
               "command": command, "attempt_root": str(attempt_root), "run_root": str(run_root),
               "pid": None, "pgid": None, "pid_create_time": None}
    item["attempts"].append(attempt)
    item["status"] = "running"
    session_record = {"schema_version": "research-session-v1", "campaign_sha256": digest,
                      "session": session, "attempt": attempt}
    process = None
    started = time.monotonic()
    try:
        atomic_json(journal_path, journal)
        atomic_json(attempt_root / "session.json", session_record)
        with (attempt_root / "orchestrator.log").open("w") as log:
            # Child cannot begin the runner until its PID is durably journaled. If the
            # parent dies before writing G, EOF makes the gated child exit harmlessly.
            process = subprocess.Popen([sys.executable, "-c", LAUNCH_GATE, *command], cwd=HERE,
                                       stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            attempt.update(pid=process.pid, pgid=process.pid,
                           pid_create_time=psutil.Process(process.pid).create_time())
            atomic_json(journal_path, journal)
            atomic_json(attempt_root / "session.json", session_record)
            process.stdin.write(b"G")
            process.stdin.close()
            returncode = process.wait()
        attempt["returncode"] = returncode
        if returncode:
            raise RuntimeError(f"Runner exited {returncode}; see {attempt_root / 'orchestrator.log'}")
        if group_members(process.pid):
            terminate_owned_group(process)
            raise RuntimeError("Runner left live descendants after exit")
        attempt["artifacts"] = completed_artifacts(plan, session, run_root)
        attempt["status"] = item["status"] = "succeeded"
    except BaseException as exc:
        interrupted = isinstance(exc, (KeyboardInterrupt, InterruptedError, SystemExit))
        attempt["status"] = item["status"] = "interrupted" if interrupted else "failed"
        attempt["error"] = {"type": type(exc).__name__, "message": str(exc)}
        if process is not None:
            # The handler raises once; termination cannot be interrupted by repeated signals.
            old = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGTERM, signal.SIGINT)}
            try:
                terminate_owned_group(process)
            except BaseException as cleanup_error:
                attempt["termination_error"] = str(cleanup_error)
                raise
            finally:
                for sig, handler in old.items():
                    signal.signal(sig, handler)
        if process is not None:
            attempt["returncode"] = process.returncode
        if interrupted:
            raise
    finally:
        attempt["ended_at_utc"] = utcnow()
        attempt["orchestrator_wall_seconds"] = time.monotonic() - started
        atomic_json(attempt_root / "session.json", session_record)
        atomic_json(journal_path, journal)
    return item["status"]


def run_campaign(args):
    plan, digest = load_plan(args.run)
    output = Path(plan["output_root"])
    output.mkdir(parents=True, exist_ok=True)
    journal_path = output / "journal.json"
    with campaign_lock(Path(tempfile.gettempdir()) / "gpu-spark-polars-study-research.lock"), \
            campaign_lock(output / "campaign.lock"):
        check_frozen_inputs(plan, include_jar=True)
        check_frozen_runtimes(plan)
        if journal_path.exists():
            journal = json.loads(journal_path.read_text())
            if journal.get("schema_version") != JOURNAL_SCHEMA or journal.get("campaign_sha256") != digest:
                raise ValueError("Journal belongs to a different frozen campaign")
            if set(journal["sessions"]) != {session["session_id"] for session in plan["sessions"]}:
                raise ValueError("Journal session IDs differ from the plan")
            reconcile_journal(journal, args.recover_interrupted)
        else:
            journal = {"schema_version": JOURNAL_SCHEMA, "campaign_sha256": digest,
                       "created_at_utc": utcnow(),
                       "sessions": {session["session_id"]: {"status": "planned", "attempts": []}
                                    for session in plan["sessions"]}}
        atomic_json(journal_path, journal)
        # Rehash on every invocation, including resume. This is outside engine measurements.
        integrity = verify_dataset_bytes(plan)
        journal.setdefault("input_integrity_gates", []).append(integrity)
        atomic_json(journal_path, journal)
        launched = 0
        old = {sig: signal.signal(sig, interrupt_campaign) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            for session in plan["sessions"]:
                item = journal["sessions"][session["session_id"]]
                if item["status"] == "succeeded":
                    # Check durable evidence before treating a previous session as complete.
                    previous = item["attempts"][-1]
                    artifacts = completed_artifacts(plan, session, Path(previous["run_root"]))
                    if artifacts != previous.get("artifacts"):
                        raise ValueError(f"Saved successful evidence changed: {session['session_id']}")
                    scientific_gate(plan, digest, session, item, journal, journal_path)
                    continue
                if item["status"] == "failed" and not args.retry_failed:
                    raise RuntimeError(f"Failed session {session['session_id']}; explicit --retry-failed required")
                if item["status"] == "interrupted" and not args.recover_interrupted:
                    raise RuntimeError(f"Interrupted session {session['session_id']}; --recover-interrupted required")
                check_frozen_inputs(plan)
                check_frozen_runtimes(plan)
                eligibility = {"session_id": session["session_id"], **observe_exclusive_host()}
                journal.setdefault("eligibility_observations", []).append(eligibility)
                atomic_json(journal_path, journal)
                if not eligibility["eligible"]:
                    raise RuntimeError("Host has an unowned benchmark/GPU client or failed GPU probe; see journal")
                status = execute_session(plan, digest, session, item, journal, journal_path)
                launched += 1
                print(json.dumps({"event": "research_session_finished", "session_id": session["session_id"],
                                  "status": status, "attempts": len(item["attempts"])}), flush=True)
                if status != "succeeded":
                    raise RuntimeError(f"Campaign stopped after failed session {session['session_id']}")
                scientific_gate(plan, digest, session, item, journal, journal_path)
                if args.max_sessions and launched >= args.max_sessions:
                    break
            else:
                check_frozen_inputs(plan, include_jar=True)
                journal.setdefault("input_integrity_gates", []).append(verify_dataset_bytes(plan))
                journal["completed_at_utc"] = utcnow()
                atomic_json(journal_path, journal)
        finally:
            for sig, handler in old.items():
                signal.signal(sig, handler)
    return journal


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", type=Path, help="create a new immutable campaign JSON (does not run)")
    mode.add_argument("--run", type=Path, help="execute/resume an already frozen campaign JSON")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--jar", type=Path)
    parser.add_argument("--oracle-root", type=Path, help="eight complete independent suite/scale oracle JSON files")
    parser.add_argument("--readiness", type=Path, help="PASS SF1 bring-up report produced at the exact planned source")
    parser.add_argument("--python", default=sys.executable, help="common four-engine Python interpreter")
    parser.add_argument("--polars-cpu-200-python", help="Polars 2.0 CPU environment using matching Python 3.11")
    parser.add_argument("--campaign-id")
    parser.add_argument("--seed", type=int, default=20261026)
    parser.add_argument("--cpus", type=int, default=26)
    parser.add_argument("--driver-memory", default="96g")
    parser.add_argument("--engine-timeout-seconds", type=float, default=7200)
    parser.add_argument("--cleanup-timeout-seconds", type=float, default=60)
    parser.add_argument("--recover-interrupted", action="store_true",
                        help="explicitly recover stale/interrupted sessions; all attempts are retained")
    parser.add_argument("--retry-failed", action="store_true", help="explicitly retry failed sessions in new attempts")
    parser.add_argument("--max-sessions", type=int, help="operational pause after N new sessions; does not change the plan")
    args = parser.parse_args(argv)
    if args.cpus <= 0 or any(not math.isfinite(value) or value <= 0 for value in (
            args.engine_timeout_seconds, args.cleanup_timeout_seconds)):
        parser.error("CPU count and finite timeouts must be positive")
    if args.max_sessions is not None and args.max_sessions <= 0:
        parser.error("--max-sessions must be positive")
    if args.plan:
        for option in ("data_root", "output_root", "jar", "polars_cpu_200_python", "oracle_root", "readiness"):
            if getattr(args, option) is None:
                parser.error(f"--plan requires --{option.replace('_', '-')}")
        plan = freeze_plan(args)
        print(json.dumps({"event": "research_campaign_frozen", "path": str(args.plan.absolute()),
                          "sessions": len(plan["sessions"]), "blocks": len(plan["blocks"])}))
    else:
        run_campaign(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
