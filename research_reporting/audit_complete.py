#!/usr/bin/env python3
"""Offline completeness/configuration proof for the fixed C84 H100 campaign.

This adds no performance observations, exclusions, tuning, or statistical tests.
Run after the frozen validator/analyzer and before rendering publication figures.
Original artifacts and the 52-file snapshot are never modified.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys

REAL_CAMPAIGN = "69669dfbc74ffb2a0c378c319c7b886043ffccb8f301784fb2b73b9ab32581e0"
SOURCE_TREE = "9514f0f7ce384763d0a675a9075666707e0edabcc003c729a3e0735a52ea25d8"
SOURCE_COMMIT = "c84ef6a07341f350dae6636ab709c1b65fd894e0"
CONFIGS = {"polars_cpu_144": "polars", "polars_cpu_200": "polars", "polars_gpu": "polars_gpu",
           "spark_cpu": "spark_cpu", "spark_gpu": "spark_gpu"}
QUERIES = {"baseline": ("q01", "q03", "q06", "q10", "q12", "q14"),
           "complex": ("cx_q09", "cx_q13", "cx_semi_anti", "cx_window", "cx_distinct", "cx_self_join")}
CELLS = [("baseline", str(s)) for s in (1, 10, 30, 100, 300)] + [("complex", str(s)) for s in (30, 100, 300)]
COMMON_CONF = {"spark.master": "local[26]", "spark.driver.memory": "96g", "spark.sql.shuffle.partitions": "120",
               "spark.sql.adaptive.enabled": "true", "spark.sql.files.maxPartitionBytes": "134217728",
               "spark.sql.session.timeZone": "UTC", "spark.driver.maxResultSize": "1g"}
GPU_CONF = {"spark.rapids.sql.enabled": "true", "spark.rapids.sql.concurrentGpuTasks": "2",
            "spark.rapids.memory.pinnedPool.size": "2G", "spark.rapids.sql.variableFloatAgg.enabled": "true"}
EXPECTED_COUNTS = {"sessions": 400, "successful_outputs": 9360, "warmup_outputs": 2340,
                   "measured_outputs": 7020, "translation_rejections": 60,
                   "successful_complex_cleanups": 3360, "failed_complex_cleanups": 60}
DIAGNOSTIC_TOKENS = {"cx_q13": ("notimplementederror", "unsupported regex", "(?s)special.*requests", "gpu engine"),
                     "cx_window": ("notimplementederror", "no support for", "unaryfunction", "groupby/rolling")}


def canonical_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Inputs:
    """Hash every file read, and reject mutation during one offline audit."""
    def __init__(self):
        self.hashes = {}

    def record(self, path, checksum):
        key = str(Path(path).resolve())
        if key in self.hashes and self.hashes[key] != checksum:
            raise ValueError(f"Input changed during audit: {path}")
        self.hashes[key] = checksum
        return checksum

    def read(self, path):
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Missing/nonordinary audit input: {path}")
        data = path.read_bytes()
        self.record(path, digest(data))
        return data

    def sha(self, path):
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Missing/nonordinary audit input: {path}")
        value = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                value.update(chunk)
        return self.record(path, value.hexdigest())

    def json(self, path):
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError(f"Duplicate JSON key {key}: {path}")
                result[key] = value
            return result
        def nonfinite(value):
            raise ValueError(f"Nonfinite JSON {value}: {path}")
        return json.loads(self.read(path), object_pairs_hook=pairs, parse_constant=nonfinite)


def contained(root, relative):
    root, relative = Path(root), Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe artifact path: {relative}")
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()) or any(
            part.is_symlink() for part in (path, *path.parents) if part != root and root in part.parents):
        raise ValueError(f"Escaping/symlink artifact: {path}")
    return path


def mapped(plan, artifacts_root, original):
    return contained(artifacts_root, Path(original).relative_to(Path(plan["output_root"])))


def finite(value, positive=True):
    return (not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
            and (value > 0 if positive else value >= 0))


def utc(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Audit timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


class Checks:
    def __init__(self):
        self.findings = []
        self.evaluated = 0

    def test(self, condition, check_id, detail, session_id=None):
        self.evaluated += 1
        if not condition:
            self.findings.append({"status": "FAIL", "check_id": check_id, "detail": detail,
                                  **({"session_id": session_id} if session_id else {})})
        return bool(condition)


def action_key(record):
    if isinstance(record.get("trial"), bool) or not isinstance(record.get("trial"), int):
        raise ValueError("Action/cleanup trial must be an integer, never boolean")
    return record["query"], record["phase"], record["trial"]


def translation_rejection(error, readiness_error):
    """Require the observed translation shape, not a broad 'unsupported' substring."""
    query = error.get("query")
    tokens = DIAGNOSTIC_TOKENS.get(query, ())
    message = str(error.get("message", "")).lower()
    reference = str(readiness_error.get("message", "")).lower()
    return (bool(tokens) and error.get("type") == readiness_error.get("type") == "ComputeError"
            and error.get("phase") == "warmup" and type(error.get("trial")) is int and error["trial"] == 0
            and all(token in message and token in reference for token in tokens)
            and "query execution with gpu not possible: unsupported operations." in message)


def manifest_files(manifest):
    entries = [entry for table in manifest["tables"].values() for entry in table["files"]]
    paths = [entry["path"] for entry in entries]
    if len(paths) != len(set(paths)) or any(Path(p).is_absolute() or ".." in Path(p).parts for p in paths):
        raise ValueError("Duplicate/unsafe input manifest inventory")
    return {entry["path"]: entry for entry in entries}


def check_chronology(journal, attempts, inventories, checks):
    if not attempts:
        checks.test(False, "chronology", "No complete session timestamps")
        return
    bounds = [(utc(a["started_at_utc"]), utc(a["ended_at_utc"])) for a in attempts]
    checks.test(all(a <= b for a, b in bounds), "session_bounds", "Session start is after its end")
    checks.test(all(left[1] <= right[0] for left, right in zip(bounds, bounds[1:])),
                "session_overlap", "Session attempts overlap or differ from frozen execution order")
    first, last, completed = bounds[0][0], bounds[-1][1], utc(journal["completed_at_utc"])
    gates = journal.get("input_integrity_gates", [])
    gate_times = []
    for gate in gates:
        entries = gate.get("checks", [])
        scales = [entry.get("scale") for entry in entries]
        good = (len(entries) == len(inventories) and set(scales) == set(inventories) and
                all(entry.get("status") == "pass" and type(entry.get("files")) is int
                    and entry["files"] == len(inventories[entry["scale"]]) for entry in entries))
        checks.test(good, "byte_gate_inventory", "Byte gate must cover each frozen scale/file count exactly once")
        if good:
            gate_times.append(utc(gate["completed_at_utc"]))
    checks.test(any(t <= first for t in gate_times), "pre_byte_gate", "No passing full-byte gate precedes the first session")
    checks.test(any(last < t <= completed for t in gate_times), "post_byte_gate",
                "No passing full-byte gate follows the last session before recorded campaign completion")
    checks.test(last <= completed, "completion_time", "Campaign completion precedes its last session")


def check_cache(schedule, files, checks, identity):
    observations = schedule.get("cache_observations", {})
    checks.test(set(observations) == {"before_preheat", "after_preheat", "after_worker"},
                "cache_phases", "Exactly three cache observations are required", identity)
    for phase, observed in observations.items():
        cache = observed["cache"]
        actual = cache.get("files", {})
        good = not cache.get("errors") and set(actual) == set(files) and bool(actual)
        checks.test(good, "cache_inventory", f"{phase}: cache file set/errors differ from the entire scale", identity)
        if not good:
            continue
        for path, entry in actual.items():
            checks.test(entry.get("bytes") == files[path]["bytes"] and type(entry.get("pages")) is int and
                        type(entry.get("resident_pages")) is int and 0 <= entry["resident_pages"] <= entry["pages"],
                        "cache_file", f"{phase}: invalid page/byte observation for {path}", identity)
        pages = sum(entry["pages"] for entry in actual.values())
        resident = sum(entry["resident_pages"] for entry in actual.values())
        checks.test(cache.get("pages") == pages and cache.get("resident_pages") == resident and
                    cache.get("bytes") == sum(entry["bytes"] for entry in actual.values()),
                    "cache_totals", f"{phase}: cache totals differ from file observations", identity)
        fraction = cache.get("resident_fraction")
        checks.test(finite(fraction, False) and fraction <= 1 and pages > 0 and
                    math.isclose(fraction, resident / pages, rel_tol=1e-12, abs_tol=1e-12),
                    "cache_fraction", f"{phase}: invalid observed residency fraction (no residency threshold applied)", identity)
        checks.test(isinstance(observed.get("host"), dict) and bool(observed["host"]),
                    "cache_host", f"{phase}: host observation missing", identity)


def check_environment(environment, plan, session, config, frozen_run, checks):
    identity = session["session_id"]
    args = environment.get("arguments", {})
    expected = {"data_root": plan["data_root"], "output": str(frozen_run), "scales": [session["scale"]],
                "engines": [session["engine"]], "runs": 3, "query_seed": session["query_seed"], "cpus": 26,
                "driver_memory": "96g", "jar": plan["settings"]["jar"], "suite": session["suite"],
                "query_cleanup": "verified_gc" if session["suite"] == "complex" else "none",
                "cache_observation": True, "queries": None, "spark_gpu_profile": "a100-parity",
                "spark_gpu_conf_file": None, "polars_gpu_executor": "streaming", "polars_gpu_executor_options": "{}"}
    for name, value in expected.items():
        checks.test(args.get(name) == value, "runner_argument", f"Effective runner argument {name} differs", identity)
    for field in ("engine_timeout_seconds", "cleanup_timeout_seconds"):
        checks.test(args.get(field) == plan["settings"][field], "runner_argument", f"{field} differs", identity)
    checks.test(environment.get("python") == config["runtime"]["python_version"],
                "python_runtime", "Captured Python version/build differs from frozen configuration", identity)
    expected_sources = {p: value for p, value in plan["source"]["files_sha256"].items()
                        if "/" not in p and p.endswith(".py")}
    checks.test(environment.get("source_sha256") == expected_sources,
                "runtime_source", "Runner source map differs from the frozen top-level Python files", identity)
    inventories = [entry for entry in environment.get("inventory", []) if len(entry.get("command", [])) >= 3
                   and entry["command"][1] == "-c" and "m.distributions()" in entry["command"][2]
                   and "sorted((d.metadata" in entry["command"][2]]
    checks.test(len(inventories) == 1, "distribution_inventory", "Expected one full distribution inventory", identity)
    if len(inventories) == 1:
        entry = inventories[0]
        distributions = {name.lower().replace("_", "-"): version for name, version in json.loads(entry["output"])}
        checks.test(entry.get("returncode") == 0 and entry["command"][0] == config["python"] and
                    distributions == config["runtime"]["distributions"],
                    "distribution_inventory", "Actual interpreter/distribution inventory differs from frozen runtime", identity)


def check_config(value, session, readiness_gpu, frozen_checks, checks):
    identity, engine = session["session_id"], session["engine"]
    version = "2.0.0" if session["config_id"] == "polars_cpu_200" else "1.44.2" if engine.startswith("polars") else "3.5.9"
    checks.test(type(value.get("cpu_threads")) is int and value["cpu_threads"] == 26,
                "cpu_threads", "All configurations require the recorded 26-thread allocation", identity)
    checks.test(value.get("version") == version, "engine_version", "Engine version differs", identity)
    if engine == "polars":
        checks.test(value.get("polars_execution_engine") == "streaming", "cpu_polars_mode", "CPU Polars is not streaming", identity)
    if engine == "polars_gpu":
        try:
            frozen_checks.check_polars_gpu_metadata(value)
        except ValueError as exc:
            checks.test(False, "strict_gpu_guard", str(exc), identity)
        actual, expected = value.get("polars_gpu", {}), readiness_gpu["polars_gpu"]
        for field in ("executor", "executor_options", "effective_executor_options", "streaming_fallback_policy",
                      "device", "memory_resource", "environment", "cudf_polars_version"):
            checks.test(actual.get(field) == expected.get(field), "polars_gpu_configuration", f"{field} differs from readiness", identity)
    if engine.startswith("spark_"):
        conf = value.get("spark_conf", {})
        for key, expected in COMMON_CONF.items():
            checks.test(conf.get(key) == expected, "spark_configuration", f"{key} differs", identity)
        plugins = [item.strip() for item in conf.get("spark.plugins", "").split(",") if item.strip()]
        if engine == "spark_gpu":
            checks.test(plugins == ["com.nvidia.spark.SQLPlugin"], "spark_plugins", "GPU Spark plugin set differs", identity)
            checks.test(value.get("spark_gpu_profile") == "a100-parity", "spark_profile", "Spark GPU profile differs", identity)
            for key, expected in GPU_CONF.items():
                checks.test(conf.get(key) == expected, "spark_gpu_configuration", f"{key} differs", identity)
        else:
            checks.test(not plugins, "cpu_spark_plugins", "CPU Spark unexpectedly declares a plugin", identity)


def load_frozen_checks(snapshot):
    path = Path(snapshot) / "analyze_results.py"
    spec = importlib.util.spec_from_file_location("_complete_audit_frozen_checks", path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def check_cleanup(value, records, errors, checks, identity):
    if value["suite"] != "complex":
        checks.test(value.get("query_cleanup_policy") == "none" and not value.get("cleanup_records") and
                    not value.get("failed_query_cleanup_records"), "baseline_cleanup_policy",
                    "Baseline must preserve the declared no-explicit-cleanup lifecycle", identity)
        return 0, 0
    clean, failed = value.get("cleanup_records", []), value.get("failed_query_cleanup_records", [])
    checks.test(value.get("query_cleanup_policy") == "verified_gc", "complex_cleanup_policy", "Complex lifecycle differs", identity)
    checks.test(Counter(map(action_key, clean)) == Counter(map(action_key, records)),
                "successful_cleanup_slots", "Successful cleanup records do not exactly match all actions", identity)
    checks.test(Counter(map(action_key, failed)) == Counter(map(action_key, errors)),
                "failed_cleanup_slots", "Failed cleanup records do not exactly match translation rejections", identity)
    for entry in clean + failed:
        good = entry.get("status") == "passed" and entry.get("policy") == "verified_gc" and finite(entry.get("seconds"), False)
        for phase in ("scratch_before", "scratch_after"):
            scratch = entry.get(phase, {})
            good = good and all(type(scratch.get(key)) is int and scratch[key] >= 0 for key in ("files", "bytes"))
        if value["engine"].startswith("spark_"):
            good = good and entry.get("scratch_after") == {"files": 0, "bytes": 0}
        checks.test(good, "cleanup_barrier", "A cleanup barrier did not pass with valid owned-scratch evidence", identity)
    return len(clean), len(failed)


def sensitivity_rows(analysis):
    primary = {entry["contrast_id"]: entry for entry in analysis["primary"]}
    secondary = analysis["secondary_host_result"]
    expected = {(a, b, suite, scale) for a, b in (("polars_cpu_200", "spark_gpu"), ("spark_cpu", "spark_gpu"),
                ("polars_cpu_144", "polars_gpu"), ("polars_cpu_200", "polars_gpu")) for suite, scale in CELLS}
    keys = [(entry["numerator"], entry["denominator"], entry["suite"], entry["scale"]) for entry in secondary]
    expected_primary = {f"{a}_over_{b}-{suite}-sf{scale}" for a, b, suite, scale in expected
                        if b != "polars_gpu" or suite != "complex"}
    if len(keys) != 32 or set(keys) != expected or len(set(keys)) != 32 or set(primary) != expected_primary:
        raise ValueError("Secondary sensitivity must retain all 32 descriptive configuration/condition pairs")
    rows = []
    for entry in secondary:
        identity = f"{entry['numerator']}_over_{entry['denominator']}-{entry['suite']}-sf{entry['scale']}"
        if entry.get("contrast_id") != identity or entry.get("endpoint") != "host_result_seconds":
            raise ValueError("Secondary endpoint or contrast identity differs")
        action = primary.get(entry["contrast_id"], {})
        ratio, host = action.get("geometric_mean_speedup"), entry.get("geometric_mean_speedup")
        unsupported = entry["denominator"] == "polars_gpu" and entry["suite"] == "complex"
        ci = entry.get("ci95_pointwise")
        if unsupported:
            if entry.get("status") != "incomplete" or host is not None or ci is not None or entry.get("n_blocks") != 0:
                raise ValueError("Unsupported secondary suite must retain no imputed time/interval")
        elif (entry.get("status") != "complete" or entry.get("n_blocks") != 10 or entry.get("missing_blocks") or
                not finite(host) or not isinstance(ci, list) or len(ci) != 2 or
                not all(finite(v) for v in ci) or not ci[0] <= host <= ci[1]):
            raise ValueError("Descriptive host-result estimate requires all ten blocks and a valid pointwise interval")
        changed = ((ratio > 1) != (host > 1)) if ratio is not None and host is not None else None
        if changed != entry.get("direction_changed_vs_action_point_estimate"):
            raise ValueError("Secondary direction-change flag differs from endpoint point estimates")
        ci, action_ci = entry.get("ci95_pointwise"), action.get("ci95_pointwise")
        rows.append({"contrast_id": entry["contrast_id"], "numerator": entry["numerator"],
                     "denominator": entry["denominator"], "suite": entry["suite"], "scale": entry["scale"],
                     "host_status": entry["status"], "host_n_blocks": entry["n_blocks"],
                     "action_ratio": ratio, "action_ci95_lower": action_ci[0] if action_ci else None,
                     "action_ci95_upper": action_ci[1] if action_ci else None,
                     "host_result_ratio": host, "host_ci95_lower": ci[0] if ci else None,
                     "host_ci95_upper": ci[1] if ci else None, "point_estimate_direction_changed": changed,
                     "interpretation": "descriptive secondary endpoint; pointwise 95% interval; no additional hypothesis test"})
    return rows


def audit_complete(campaign, artifacts_root, evidence_root, validation, analysis, output, allow_synthetic=False):
    output, artifacts_root, evidence_root = Path(output), Path(artifacts_root).resolve(), Path(evidence_root).resolve()
    if output.exists():
        raise ValueError("Refusing to overwrite a complete-audit output directory")
    output.mkdir(parents=True)
    inputs, checks, summaries = Inputs(), Checks(), []
    counts = dict.fromkeys(EXPECTED_COUNTS, 0)
    report = {"schema_version": "research-complete-audit-v1", "status": "FAIL", "campaign_sha256": None,
              "journal_sha256": None, "counts": counts, "source_sha256": inputs.sha(Path(__file__)),
              "scope": "offline completeness/configuration proof; no new timing, exclusion or statistical rules"}
    try:
        plan = inputs.json(campaign)
        campaign_sha = inputs.sha(campaign)
        report.update(campaign_sha256=campaign_sha, synthetic=plan.get("synthetic") is True,
                      artifact_roots={"evidence_root": str(evidence_root), "artifacts_root": str(artifacts_root),
                                      "frozen_output_root": plan["output_root"]})
        if report["synthetic"]:
            if not allow_synthetic or "SYNTHETIC" not in plan["campaign_id"]:
                raise ValueError("SYNTHETIC audit requires explicit opt-in and a SYNTHETIC campaign label")
        elif campaign_sha != REAL_CAMPAIGN:
            raise ValueError("Auditor is scoped to the genuine frozen C84 H100 campaign")
        checks.test(inputs.read(str(campaign) + ".sha256").decode().strip() == campaign_sha,
                    "campaign_sidecar", "Campaign checksum sidecar differs")
        source = plan["source"]
        source_valid = checks.test(len(source["files_sha256"]) == 52 and source.get("git_head") == SOURCE_COMMIT and
                    digest(canonical_bytes(source["files_sha256"])) == source.get("source_tree_sha256") == SOURCE_TREE,
                    "frozen_source", "The genuine 52-file C84 source identity is required")
        snapshot = evidence_root / "campaign.json.source"
        for relative, expected in source["files_sha256"].items():
            source_valid = checks.test(inputs.sha(contained(snapshot, relative)) == expected,
                                       "source_file", f"Frozen source differs: {relative}") and source_valid
        if not source_valid:
            raise ValueError("Frozen source identity failed; refusing to execute its validation helper")
        frozen_checks = load_frozen_checks(snapshot)
        prestart = inputs.json(evidence_root / "PRESTART_MANIFEST.json")
        checks.test(prestart.get("campaign_sha256") == campaign_sha and prestart.get("status") == "PASS",
                    "prestart_manifest", "Prestart archive identity/completion differs")
        for relative, expected in prestart["files_sha256"].items():
            checks.test(inputs.sha(contained(evidence_root, relative)) == expected,
                        "prestart_file", f"Prestart artifact hash differs: {relative}")
        readiness = inputs.json(evidence_root / "readiness/complex-polars_gpu/sf1/polars_gpu/results.json")
        readiness_errors = {entry["query"]: entry for entry in readiness["query_errors"]}
        checks.test(all(translation_rejection(readiness_errors.get(q, {}), readiness_errors.get(q, {}))
                        for q in DIAGNOSTIC_TOKENS), "readiness_translation_shape", "Readiness rejection shape differs")
        configs = {entry["id"]: entry for entry in plan["configs"]}
        checks.test(set(configs) == set(CONFIGS) and len(plan["configs"]) == 5,
                    "configurations", "Five unique frozen configurations are required")
        journal_path = artifacts_root / "journal.json"
        journal = inputs.json(journal_path)
        report["journal_sha256"] = inputs.sha(journal_path)
        validation_value = inputs.json(validation)
        analysis_path = Path(analysis) / "analysis.json" if Path(analysis).is_dir() else Path(analysis)
        analysis_value = inputs.json(analysis_path)
        checks.test(journal.get("campaign_sha256") == campaign_sha and journal.get("completed_at_utc") is not None,
                    "journal_completion", "Journal identity or final completion missing")
        checks.test(validation_value.get("status") == "PASS" and validation_value.get("campaign_sha256") == campaign_sha
                    and validation_value.get("journal_sha256") == report["journal_sha256"],
                    "final_validation", "Final frozen validation is not PASS for the exact campaign/journal")
        checks.test(analysis_value.get("validation_sha256") == inputs.sha(validation) and
                    analysis_value.get("campaign_sha256") == campaign_sha and analysis_value.get("validation_status") == "PASS"
                    and analysis_value.get("analysis_source_sha256") == source["files_sha256"]["research_analysis.py"]
                    and analysis_value.get("primary_family_status") == "complete",
                    "analysis_binding", "Frozen analysis does not bind the exact final validation/campaign")
        sessions = plan["sessions"]
        expected_cells = {(f"block-{b:02d}", suite, scale, config) for b in range(1, 11) for suite, scale in CELLS for config in CONFIGS}
        cells = [(s["block_id"], s["suite"], s["scale"], s["config_id"]) for s in sessions]
        session_ids = [s["session_id"] for s in sessions]
        checks.test(len(sessions) == len(set(session_ids)) == len(set(cells)) == 400 and set(cells) == expected_cells
                    and set(journal["sessions"]) == set(session_ids), "complete_design", "All 400 unique sessions/cells are required")
        audits = {entry["session_id"]: entry for entry in validation_value.get("sessions", [])}
        checks.test(len(audits) == len(validation_value.get("sessions", [])) == 400 and set(audits) == set(session_ids),
                    "complete_validation", "Final validation must retain all 400 unique scientific audits")
        inventories, attempts = {}, []
        for session in sessions:
            identity = session["session_id"]
            before = len(checks.findings)
            summary = {"session_id": identity, "status": "FAIL"}
            try:
                item = journal["sessions"][identity]
                if item.get("status") != "succeeded" or len(item.get("attempts", [])) != 1:
                    raise ValueError("Exactly one successful attempt is required; no dropped/replaced cases")
                attempt = item["attempts"][0]
                attempts.append(attempt)
                if attempt.get("status") != "succeeded" or attempt.get("returncode") != 0:
                    raise ValueError("Attempt did not succeed")
                frozen_run = Path(attempt["run_root"])
                run = mapped(plan, artifacts_root, frozen_run)
                folder = run / f"sf{session['scale']}" / session["engine"]
                record = inputs.json(run.parent / "session.json")
                checks.test(record.get("session") == session and record.get("attempt") == attempt and
                            record.get("campaign_sha256") == campaign_sha, "session_binding", "Session record binding differs", identity)
                value_path = folder / "results.json"
                value = inputs.json(value_path)
                checks.test(inputs.sha(value_path) == attempt["artifacts"]["results_sha256"],
                            "results_hash", "Materialized results hash differs", identity)
                gate = item.get("scientific_validation", {})
                gate_path = mapped(plan, artifacts_root, gate["path"])
                gate_value = inputs.json(gate_path)
                checked = audits[identity]
                checks.test(gate.get("status") == "PASS" and inputs.sha(gate_path) == gate.get("sha256") and gate_value == checked
                            and checked.get("status") == "PASS" and not checked.get("errors"),
                            "scientific_gate", "Scientific gate/final audit differs or failed", identity)
                manifest_path = run / f"sf{session['scale']}" / "manifest.json"
                manifest = inputs.json(manifest_path)
                files = manifest_files(manifest)
                checks.test(inputs.sha(manifest_path) == plan["datasets"][session["scale"]]["manifest_sha256"]
                            == value.get("manifest_sha256"), "manifest_identity", "Dataset identity differs", identity)
                inventories.setdefault(session["scale"], files)
                oracle_path = evidence_root / "oracles" / f"{session['cell_id']}.json"
                oracle = inputs.json(oracle_path)
                checks.test(inputs.sha(oracle_path) == plan["oracles"][session["cell_id"]]["sha256"]
                            and oracle.get("manifest_sha256") == value.get("manifest_sha256"),
                            "oracle_identity", "Oracle/dataset identity differs", identity)
                checks.test(bool(oracle.get("input_files")), "oracle_input_inventory", "Oracle input inventory is missing", identity)
                for entries in oracle.get("input_files", {}).values():
                    for entry in entries:
                        reference = files[entry["path"]]
                        checks.test(entry.get("bytes") == reference["bytes"] and entry.get("declared_sha256") == reference["sha256"],
                                    "oracle_input_file", "Oracle input declaration differs from manifest", identity)
                environment = inputs.json(run / "environment.json")
                check_environment(environment, plan, session, configs[session["config_id"]], frozen_run, checks)
                check_config(value, session, readiness, frozen_checks, checks)
                schedules = inputs.json(run / "schedule.json")
                if len(schedules) != 1:
                    raise ValueError("Each fresh session must have exactly one runner schedule item")
                schedule = schedules[0]
                checks.test(inputs.sha(run / "schedule.json") == attempt["artifacts"]["schedule_sha256"] and
                            schedule.get("engine") == session["engine"] and schedule.get("scale") == session["scale"]
                            and schedule.get("query_seed") == session["query_seed"] and schedule.get("status") == "complete"
                            and schedule.get("returncode") == 0, "schedule_binding", "Schedule identity/completion differs", identity)
                check_cache(schedule, files, checks, identity)
                records, errors = value.get("records", []), value.get("query_errors", [])
                keys = [action_key(entry) for entry in records]
                unsupported = set(DIAGNOSTIC_TOKENS) if session["engine"] == "polars_gpu" and session["suite"] == "complex" else set()
                expected = {(q, phase, trial) for q in QUERIES[session["suite"]] if q not in unsupported
                            for phase, trial in (("warmup", 0), ("measured", 1), ("measured", 2), ("measured", 3))}
                checks.test(len(keys) == len(set(keys)) and set(keys) == expected,
                            "all_action_slots", "Missing, duplicate or extra warmup/measured action", identity)
                checks.test({entry.get("query") for entry in errors} == unsupported and len(errors) == len(unsupported)
                            and all(translation_rejection(entry, readiness_errors[entry["query"]]) for entry in errors),
                            "translation_rejections", "Unexpected/misclassified query failure or missing expected translation rejection", identity)
                compared = checked.get("comparisons", [])
                checks.test(Counter(map(action_key, compared)) == Counter(keys) and all(entry.get("passed") is True for entry in compared),
                            "all_oracle_slots", "Oracle audit must cover every successful action exactly once", identity)
                audited_plans = {action_key(entry): entry for entry in checked.get("plans", [])}
                for entry in records:
                    key = action_key(entry)
                    checks.test(finite(entry.get("seconds")) and finite(entry.get("host_result_seconds")) and
                                entry["host_result_seconds"] >= entry["seconds"], "timing_endpoints", "Invalid endpoint order/time", identity)
                    plan_path = contained(folder, entry["plan"])
                    plan_text = inputs.read(plan_path).decode()
                    checks.test(inputs.sha(plan_path) == audited_plans[key]["sha256"], "action_plan_hash", "Action plan hash differs", identity)
                    if session["engine"] == "spark_cpu":
                        final = plan_text.split("== Final Plan ==", 1)[-1].split("== Initial Plan ==", 1)[0]
                        checks.test(not re.search(r"\bGpu[A-Z][A-Za-z0-9_]*\b", final),
                                    "cpu_spark_plan", "CPU Spark final plan contains GPU operators", identity)
                clean, failed = check_cleanup(value, records, errors, checks, identity)
                session_counts = {"sessions": 1, "successful_outputs": len(records),
                    "warmup_outputs": sum(entry["phase"] == "warmup" for entry in records),
                    "measured_outputs": sum(entry["phase"] == "measured" for entry in records),
                    "translation_rejections": len(errors), "successful_complex_cleanups": clean, "failed_complex_cleanups": failed}
                for key, number in session_counts.items():
                    counts[key] += number
                summary["counts"] = session_counts
            except (OSError, ValueError, KeyError, TypeError, UnicodeError, RuntimeError) as exc:
                checks.test(False, "session_input", f"{type(exc).__name__}: {exc}", identity)
            summary["status"] = "PASS" if len(checks.findings) == before else "FAIL"
            summaries.append(summary)
        for key, expected in EXPECTED_COUNTS.items():
            checks.test(counts[key] == expected, "complete_counts", f"{key}: observed {counts[key]}, expected {expected}")
        checks.test(validation_value.get("successful_outputs_compared") == 9360,
                    "validation_count", "Final validation output count must be 9360")
        check_chronology(journal, attempts, inventories, checks)
        sensitivity = sensitivity_rows(analysis_value)
        write_sensitivity(output, sensitivity, report["synthetic"])
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, RuntimeError) as exc:
        checks.test(False, "audit_input", f"{type(exc).__name__}: {exc}")
    report.update(status="PASS" if not checks.findings else "FAIL", checks_evaluated=checks.evaluated,
                  findings=checks.findings, sessions=summaries, input_files_sha256=inputs.hashes,
                  completed_at_utc=datetime.now(timezone.utc).isoformat())
    (output / "AUDIT_COMPLETE.json").write_bytes(canonical_bytes(report))
    return report


def write_sensitivity(output, rows, synthetic=False):
    with (output / "HOST_RESULT_SENSITIVITY.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["synthetic", *rows[0]])
        writer.writeheader()
        writer.writerows({"synthetic": synthetic, **row} for row in rows)
    lines = ["# Common-host-result endpoint sensitivity", "",
             *( ["**SYNTHETIC TEST ONLY — not experimental results.**", ""] if synthetic else [] ),
             "Descriptive secondary estimates with pointwise 95% intervals. No new hypothesis tests or "
             "Holm-adjusted secondary claims. Ratios above one favor the GPU. N/A retains unsupported complete suites.", "",
             "| Contrast | Action ratio | Host-result ratio | Host pointwise 95% interval | Direction changed |",
             "|---|---:|---:|---|---|"]
    for row in rows:
        def fmt(value):
            return "N/A" if value is None else f"{value:.4g}"
        interval = "N/A" if row["host_ci95_lower"] is None else f"[{fmt(row['host_ci95_lower'])}, {fmt(row['host_ci95_upper'])}]"
        lines.append(f"| {row['contrast_id']} | {fmt(row['action_ratio'])} | {fmt(row['host_result_ratio'])} | "
                     f"{interval} | {row['point_estimate_direction_changed']} |")
    (output / "HOST_RESULT_SENSITIVITY.md").write_text("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("campaign", "artifacts-root", "evidence-root", "validation", "analysis", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--allow-synthetic", action="store_true")
    args = parser.parse_args(argv)
    result = audit_complete(args.campaign, args.artifacts_root, args.evidence_root, args.validation,
                            args.analysis, args.output, args.allow_synthetic)
    print(json.dumps({key: result[key] for key in ("status", "counts", "campaign_sha256")}))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
