"""Explicit SYNTHETIC artifacts for offline replay tests, never study results.

Only the supplied temporary directory is written. No engine, GPU, JVM, query,
SSH connection, or source Parquet input is started or read. The genuine frozen
52-file C84 source snapshot is copied without alteration. Fake timings, outputs,
plans and oracle rows exist solely to test its validator/analyzer plumbing.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys


REPO = Path(__file__).resolve().parents[2]
REAL_ARCHIVE = REPO / "evidence-research/20261026/h100-confirmatory-v1"
NOTICE = "SYNTHETIC REPLAY TEST ONLY: not a benchmark measurement or experimental result"


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cleanup(query, phase, trial):
    return {"synthetic": True, "synthetic_notice": NOTICE, "query": query, "phase": phase, "trial": trial,
            "policy": "verified_gc", "status": "passed", "seconds": .001,
            "scratch_before": {"files": 0, "bytes": 0}, "scratch_after": {"files": 0, "bytes": 0}}


def gpu_metadata(template=None):
    if template is not None:
        metadata = copy.deepcopy(template["polars_gpu"])
        metadata.update({"synthetic": True, "synthetic_notice": NOTICE})
        return {"polars_execution_engine": "gpu", "polars_gpu": metadata}
    return {"polars_execution_engine": "gpu", "polars_gpu": {
        "synthetic": True, "synthetic_notice": NOTICE, "raise_on_fail": True,
        "cpu_fallback_policy": "raise", "executor": "streaming", "streaming_fallback_policy": "warn",
        "session_guard": {"synthetic": True, "effective_config_verified": True, "tiny_query_passed": True,
            "unsupported_canary_raised": True, "unsupported_canary_reason_verified": True,
            "unsupported_canary_error": "SYNTHETIC NotImplementedError: unsupported anonymousfunction",
            "effective_config": {"raise_on_fail": True, "executor": {"name": "streaming", "fallback_mode": "warn"}}}}}


def create_synthetic_replay(tmp_path):
    """Return (archive, artifacts_root) for a complete, visibly SYNTHETIC campaign.

    Scientific gate plan sources deliberately retain frozen remote paths, as on
    the real VM. A portable replay must exercise its documented path-only
    canonicalization adapter before the exact frozen analyzer accepts them.
    """
    root = Path(tmp_path) / "SYNTHETIC-replay-fixture"
    archive, artifacts_root = root / "SYNTHETIC-prestart", root / "campaign-results"
    archive.mkdir(parents=True, exist_ok=False)
    artifacts_root.mkdir()
    original = json.loads((REAL_ARCHIVE / "campaign.json").read_text())
    snapshot = archive / "campaign.json.source"
    source_files = original["source"]["files_sha256"]
    if len(source_files) != 52:
        raise ValueError("The test requires the genuine frozen 52-file C84 snapshot")
    for relative, expected in source_files.items():
        src, target = REAL_ARCHIVE / "campaign.json.source" / relative, snapshot / relative
        if digest(src) != expected:
            raise ValueError(f"Genuine source fixture drift: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    # Pure validation/schedule helpers are loaded from those exact copied bytes.
    # Their ordinary local imports are the same frozen C84 source; no worker runs.
    sys.path.insert(0, str(snapshot))
    previous_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        campaign = load_module("synthetic_frozen_campaign", snapshot / "research_campaign.py")
        validator = load_module("synthetic_frozen_validator", snapshot / "research_validate.py")
        baseline = load_module("synthetic_frozen_workloads", snapshot / "workloads.py")
        complex_suite = load_module("synthetic_frozen_complex", snapshot / "complex_workloads.py")
    finally:
        sys.path.remove(str(snapshot))
        sys.dont_write_bytecode = previous_bytecode
    remote_root = Path("/SYNTHETIC-FROZEN-REPLAY-TEST")
    frozen_outputs = remote_root / "campaign-results"
    plan = copy.deepcopy(original)
    plan.update({"synthetic": True, "synthetic_notice": NOTICE, "campaign_id": "SYNTHETIC-REPLAY-TEST-ONLY",
                 "created_at_utc": "2026-10-26T18:00:00+00:00", "output_root": str(frozen_outputs),
                 "data_root": str(remote_root / "NO-PARQUET-EXISTS")})
    plan["source"]["snapshot_directory"] = str(remote_root / "campaign.json.source")
    manifests = {}
    for scale, dataset in plan["datasets"].items():
        dataset["path"] = str(remote_root / "NO-PARQUET-EXISTS" / f"sf{scale}")
        entry = {"path": "SYNTHETIC/data.parquet", "bytes": 32,
                 "sha256": hashlib.sha256(f"SYNTHETIC unmaterialized parquet SF{scale}".encode()).hexdigest()}
        manifest = {"synthetic": True, "synthetic_notice": NOTICE, "format_version": 1,
                    "scale_factor": float(scale), "total_parquet_bytes": 32,
                    "tables": {"SYNTHETIC": {"rows": 1, "parquet_bytes": 32, "files": [entry]}}}
        manifest_path = archive / "synthetic-manifests" / f"sf{scale}" / "manifest.json"
        write(manifest_path, manifest)
        dataset["manifest_sha256"] = digest(manifest_path)
        dataset["total_parquet_bytes"] = 32
        manifests[scale] = manifest
    plan["blocks"], plan["sessions"] = campaign.make_schedule(plan["configs"], plan["seed"], frozen_outputs)
    oracles = {}
    for suite, scale in campaign.CELLS:
        module = baseline if suite == "baseline" else complex_suite
        oracle = {"synthetic": True, "synthetic_notice": NOTICE, "status": "PASS", "suite": suite,
                  "scale_factor": float(scale), "suite_version": getattr(module, "SUITE_VERSION", "baseline-v1"),
                  "completed_at_utc": "2026-10-26T18:05:00+00:00",
                  "manifest_sha256": plan["datasets"][scale]["manifest_sha256"], "queries": {}}
        file_entry = manifests[scale]["tables"]["SYNTHETIC"]["files"][0]
        oracle["input_files"] = {"SYNTHETIC": [{"path": file_entry["path"], "bytes": file_entry["bytes"],
                                                "declared_sha256": file_entry["sha256"]}]}
        for number, query in enumerate(module.QUERY_IDS):
            oracle["queries"][query] = {"query_sha256": hashlib.sha256(module.spark_sql(query).encode()).hexdigest(),
                "rows": [{"SYNTHETIC_TEST_ONLY": NOTICE, "fixture_query": query, "value": number + 1}]}
        relative = f"oracles/{suite}-sf{scale}.json"
        write(archive / relative, oracle)
        plan["oracles"][f"{suite}-sf{scale}"] = {"path": str(remote_root / relative), "sha256": digest(archive / relative)}
        oracles[(suite, scale)] = oracle
    readiness_path = archive / "readiness/READINESS.json"
    write(readiness_path, {"synthetic": True, "synthetic_notice": NOTICE, "status": "PASS"})
    plan["readiness"] = {"path": str(remote_root / "readiness/READINESS.json"), "sha256": digest(readiness_path)}
    readiness_gpu = json.loads((REAL_ARCHIVE / "readiness/complex-polars_gpu/sf1/polars_gpu/results.json").read_text())
    readiness_gpu.update({"synthetic": True, "synthetic_notice": NOTICE, "records": [], "cleanup_records": [],
                          "failed_query_cleanup_records": []})
    for error in readiness_gpu["query_errors"]:
        error["message"] = "SYNTHETIC reference translation fixture: " + error["message"]
    write(archive / "readiness/complex-polars_gpu/sf1/polars_gpu/results.json", readiness_gpu)
    (archive / "README.md").write_text(NOTICE + "\n")
    write(archive / "campaign.json", plan)
    plan_sha = digest(archive / "campaign.json")
    (archive / "campaign.json.sha256").write_text(plan_sha + "\n")
    journal = {"schema_version": "research-campaign-journal-v1", "synthetic": True, "synthetic_notice": NOTICE,
               "campaign_sha256": plan_sha, "sessions": {}, "completed_at_utc": "2026-10-26T21:00:00+00:00",
               "input_integrity_gates": [{"synthetic": True, "completed_at_utc": completed,
                    "checks": [{"scale": scale, "files": 1, "status": "pass"} for scale in plan["datasets"]]}
                   for completed in ("2026-10-26T19:59:59+00:00", "2026-10-26T20:14:00+00:00")]}
    start = datetime(2026, 10, 26, 20, tzinfo=timezone.utc)
    environments = {config["id"]: json.loads((REAL_ARCHIVE / f"readiness/baseline-{config['id']}/environment.json").read_text())
                    for config in plan["configs"]}
    result_templates = {}
    for suite in ("baseline", "complex"):
        for config in plan["configs"]:
            result_templates[(suite, config["id"])] = json.loads((REAL_ARCHIVE /
                f"readiness/{suite}-{config['id']}/sf1/{config['engine']}/results.json").read_text())
    for number, session in enumerate(plan["sessions"]):
        frozen_attempt = Path(session["output_root"]) / "attempt-001"
        frozen_run = frozen_attempt / "run"
        local_attempt = artifacts_root / frozen_attempt.relative_to(frozen_outputs)
        run = local_attempt / "run"
        folder = run / f"sf{session['scale']}" / session["engine"]
        plans = folder / "plans"
        plans.mkdir(parents=True)
        plan_file = plans / "SYNTHETIC-plan.txt"
        plan_nodes = ("GpuHashAggregate SYNTHETIC\n+- GpuFileSourceScan SYNTHETIC\n" if session["engine"] == "spark_gpu"
                      else "HashAggregate SYNTHETIC\n+- FileScan SYNTHETIC\n")
        plan_file.write_text("SYNTHETIC TEST PLAN ONLY; no engine execution occurred\n"
                             "AdaptiveSparkPlan isFinalPlan=true\n== Final Plan ==\n" + plan_nodes)
        oracle = oracles[(session["suite"], session["scale"])]
        queries = list(oracle["queries"])
        unsupported = {"cx_q13", "cx_window"} if session["engine"] == "polars_gpu" and session["suite"] == "complex" else set()
        value = {"synthetic": True, "synthetic_notice": NOTICE, "suite": session["suite"],
                 "suite_version": oracle["suite_version"], "engine": session["engine"],
                 "scale_factor": float(session["scale"]), "runs": 3, "query_seed": session["query_seed"],
                 "manifest_sha256": oracle["manifest_sha256"], "queries": queries, "records": [],
                 "cleanup_records": [], "failed_query_cleanup_records": [], "query_errors": [],
                 "completed_at_utc": (start + timedelta(seconds=number * 2 + 1)).isoformat(),
                 "version": {"polars_cpu_144": "1.44.2", "polars_cpu_200": "2.0.0", "polars_gpu": "1.44.2",
                             "spark_cpu": "3.5.9", "spark_gpu": "3.5.9"}[session["config_id"]],
                 "query_execution_status": "partial" if unsupported else "complete", "cpu_threads": 26,
                 "query_cleanup_policy": "verified_gc" if session["suite"] == "complex" else "none"}
        template = result_templates[(session["suite"], session["config_id"])]
        for field in ("query_descriptions", "cache_policy", "timing_scope", "host_result_timing_scope", "failure_policy"):
            if field in template:
                value[field] = copy.deepcopy(template[field])
        if session["engine"] == "polars_gpu":
            value.update(gpu_metadata(readiness_gpu))
        elif session["engine"] == "polars":
            value["polars_execution_engine"] = "streaming"
        elif session["engine"].startswith("spark_"):
            value["spark_conf"] = copy.deepcopy(template["spark_conf"])
            if session["engine"] == "spark_gpu":
                value["spark_gpu_profile"] = "a100-parity"
        factor = {"polars_cpu_144": 4., "polars_cpu_200": 2.5, "polars_gpu": .8,
                  "spark_cpu": 6., "spark_gpu": 1.5}[session["config_id"]]
        for query_number, query in enumerate(queries):
            if query in unsupported:
                original_error = next(error for error in template["query_errors"] if error["query"] == query)
                value["query_errors"].append({"query": query, "phase": "warmup", "trial": 0,
                    "type": original_error["type"], "message": "SYNTHETIC translation fixture: " + original_error["message"]})
                value["failed_query_cleanup_records"].append(cleanup(query, "warmup", 0))
                continue
            base_time = .0001 * factor * (1 + math.log1p(float(session["scale"]))) * (1 + query_number / 8)
            base_time *= math.exp(.035 * math.sin((number + 1) * (query_number + 1)))
            for phase, trial, multiplier in [("warmup", 0, 1.2), ("measured", 1, .9),
                                               ("measured", 2, 1), ("measured", 3, 1.1)]:
                value["records"].append({"synthetic": True, "query": query, "phase": phase, "trial": trial,
                    "seconds": base_time * multiplier, "host_result_seconds": base_time * multiplier + .01,
                    "rows": oracle["queries"][query]["rows"], "plan": "plans/SYNTHETIC-plan.txt"})
                if session["suite"] == "complex":
                    value["cleanup_records"].append(cleanup(query, phase, trial))
        results_path = folder / "results.json"
        write(results_path, value)
        write(run / f"sf{session['scale']}" / "manifest.json", manifests[session["scale"]])
        environment = copy.deepcopy(environments[session["config_id"]])
        environment.update({"synthetic": True, "synthetic_notice": NOTICE,
                            "started_at_utc": (start + timedelta(seconds=number * 2)).isoformat()})
        environment["arguments"].update({"data_root": plan["data_root"], "output": str(frozen_run),
            "scales": [session["scale"]], "engines": [session["engine"]], "runs": 3,
            "query_seed": session["query_seed"], "suite": session["suite"],
            "query_cleanup": "verified_gc" if session["suite"] == "complex" else "none"})
        write(run / "environment.json", environment)
        entry = manifests[session["scale"]]["tables"]["SYNTHETIC"]["files"][0]
        cache = {"observed_at_utc": (start + timedelta(seconds=number * 2)).isoformat(), "method": "SYNTHETIC mincore fixture",
                 "scope": "all manifest files", "files": {entry["path"]: {"bytes": entry["bytes"], "pages": 1, "resident_pages": 1}},
                 "errors": [], "bytes": entry["bytes"], "pages": 1, "resident_pages": 1, "resident_fraction": 1., "seconds": .001}
        schedule = {"synthetic": True, "status": "complete", "returncode": 0, "scale": session["scale"],
                    "engine": session["engine"], "query_seed": session["query_seed"],
                    "start_utc": (start + timedelta(seconds=number * 2)).isoformat(),
                    "end_utc": (start + timedelta(seconds=number * 2 + 1)).isoformat(), "process_wall_seconds": 1.,
                    "preheat": {"bytes": entry["bytes"], "seconds": .001},
                    "cache_observations": {phase: {"cache": copy.deepcopy(cache), "host": {"synthetic": True,
                        "synthetic_notice": NOTICE, "observed_at_utc": cache["observed_at_utc"]}}
                        for phase in ("before_preheat", "after_preheat", "after_worker")}}
        write(run / "schedule.json", [schedule])
        attempt = {"synthetic": True, "attempt": 1, "status": "succeeded", "attempt_root": str(frozen_attempt),
                   "run_root": str(frozen_run), "started_at_utc": (start + timedelta(seconds=number * 2)).isoformat(),
                   "ended_at_utc": (start + timedelta(seconds=number * 2 + 1)).isoformat(), "returncode": 0,
                   "artifacts": {"results_path": str(frozen_run / f"sf{session['scale']}" / session["engine"] / "results.json"),
                                 "results_sha256": digest(results_path), "schedule_sha256": digest(run / "schedule.json")}}
        item = {"status": "succeeded", "attempts": [attempt]}
        write(local_attempt / "session.json", {"schema_version": "research-session-v1", "synthetic": True,
               "campaign_sha256": plan_sha, "session": session, "attempt": attempt})
        audit = validator.audit_session({**plan, "_sha256": plan_sha}, session, item, oracle, artifacts_root)
        if audit["status"] != "PASS":
            raise ValueError(f"SYNTHETIC raw fixture does not satisfy genuine validator: {audit['errors']}")
        # VM-created gates retain remote paths; portable replay must canonicalize only these provenance fields.
        for plan_record in audit["plans"]:
            if "source" in plan_record:
                plan_record["source"] = str(frozen_outputs / Path(plan_record["source"]).relative_to(artifacts_root))
        gate_path = local_attempt / "SCIENTIFIC_VALIDATION.json"
        write(gate_path, audit)
        item["scientific_validation"] = {"path": str(frozen_attempt / "SCIENTIFIC_VALIDATION.json"),
                                          "sha256": digest(gate_path), "status": "PASS"}
        journal["sessions"][session["session_id"]] = item
    write(artifacts_root / "journal.json", journal)
    manifest = {"schema_version": "h100-confirmatory-prestart-archive-v1", "synthetic": True,
                "synthetic_notice": NOTICE, "archived_at_utc": "2026-10-26T18:10:00+00:00", "status": "PASS",
                "phase": "before-first-confirmatory-session", "source_commit": plan["source"]["git_head"],
                "source_tree_sha256": plan["source"]["source_tree_sha256"], "campaign_sha256": plan_sha,
                "planned_sessions": 400, "planned_blocks": 10, "primary_contrasts": 26,
                "files_sha256": {str(path.relative_to(archive)): digest(path) for path in sorted(archive.rglob("*"))
                                 if path.is_file() and "__pycache__" not in path.parts}}
    write(archive / "PRESTART_MANIFEST.json", manifest)
    return archive, artifacts_root
