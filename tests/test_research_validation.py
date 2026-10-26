"""The independent-oracle gate must reject missing, altered and duplicate evidence."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import research_validate as rv
import worker
import workloads


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(tmp_path):
    run = tmp_path / "attempt-001" / "run"
    folder = run / "sf1" / "polars"
    (folder / "plans").mkdir(parents=True)
    (folder / "plans" / "plan.txt").write_text("CPU plan\n")
    session = {"session_id": "s1", "suite": "baseline", "scale": "1", "engine": "polars",
               "config_id": "polars_cpu_200", "query_seed": 123}
    rows = [{"count": 9007199254740993, "amount": 1.25, "label": None}]
    oracle = {"manifest_sha256": "a" * 64, "queries": {q: {"rows": rows} for q in workloads.QUERY_IDS}}
    value = {"suite": "baseline", "engine": "polars", "runs": 3, "query_seed": 123,
             "suite_version": "baseline-v1",
             "scale_factor": 1, "manifest_sha256": "a" * 64, "queries": workloads.QUERY_IDS,
             "completed_at_utc": "2026-10-26T00:00:00+00:00", "version": "2.0.0", "records": []}
    for query in workloads.QUERY_IDS:
        for phase, trial in [("warmup", 0), *(("measured", t) for t in (1, 2, 3))]:
            value["records"].append({"query": query, "phase": phase, "trial": trial, "seconds": 1.0,
                                     "host_result_seconds": 1.1, "rows": rows, "plan": "plans/plan.txt"})
    write(folder / "results.json", value)
    write(run / "schedule.json", [{"status": "complete"}])
    attempt = {"run_root": str(run), "artifacts": {"results_path": str(folder / "results.json"),
                "results_sha256": rv.sha256(folder / "results.json"), "schedule_sha256": rv.sha256(run / "schedule.json")}}
    item = {"status": "succeeded", "attempts": [attempt]}
    write(run.parent / "session.json", {"campaign_sha256": "b" * 64, "session": session, "attempt": attempt})
    return {"_sha256": "b" * 64}, session, item, oracle, value


def refresh(item, session, value):
    attempt = item["attempts"][0]
    path = Path(attempt["artifacts"]["results_path"])
    write(path, value)
    attempt["artifacts"]["results_sha256"] = rv.sha256(path)
    write(Path(attempt["run_root"]).parent / "session.json",
          {"campaign_sha256": "b" * 64, "session": session, "attempt": attempt})


def test_every_action_is_compared_and_large_integers_exact(tmp_path):
    plan, session, item, oracle, value = fixture(tmp_path)
    audit = rv.audit_session(plan, session, item, oracle)
    assert audit["status"] == "PASS"
    assert len(audit["comparisons"]) == 24
    value = copy.deepcopy(value)
    value["records"][-1]["rows"][0]["count"] += 1
    refresh(item, session, value)
    audit = rv.audit_session(plan, session, item, oracle)
    assert audit["status"] == "FAIL"
    assert any("Oracle mismatch" in error for error in audit["errors"])


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "nonfinite", "bad_endpoint", "unknown_query"])
def test_invalid_action_evidence_is_rejected(tmp_path, mutation):
    plan, session, item, oracle, value = fixture(tmp_path)
    if mutation == "duplicate":
        value["records"].append(copy.deepcopy(value["records"][0]))
    elif mutation == "missing":
        value["records"].pop()
    elif mutation == "nonfinite":
        value["records"][0]["seconds"] = float("nan")
    elif mutation == "bad_endpoint":
        value["records"][0]["host_result_seconds"] = 0.5
    else:
        value["records"][0]["query"] = "invented"
    refresh(item, session, value)
    assert rv.audit_session(plan, session, item, oracle)["status"] == "FAIL"


def test_hash_drift_and_unreviewed_retry_are_not_silent(tmp_path):
    plan, session, item, oracle, value = fixture(tmp_path)
    Path(item["attempts"][0]["artifacts"]["results_path"]).write_text("{}")
    assert rv.audit_session(plan, session, item, oracle)["status"] == "FAIL"
    item["attempts"].append(copy.deepcopy(item["attempts"][0]))
    assert "Replacement attempt" in rv.audit_session(plan, session, item, oracle)["errors"][0]


def test_worker_process_counters_read_current_process():
    import psutil
    import os
    counters = worker.process_counters([psutil.Process()])
    assert str(os.getpid()) in counters
    assert counters[str(os.getpid())]["cpu_times"]["user"] >= 0


@pytest.mark.skipif(sys.platform != "linux", reason="mincore is Linux-specific")
def test_residency_counts_pages_without_modifying_file(tmp_path):
    import cache_residency
    path = tmp_path / "data.parquet"
    path.write_bytes(b"x" * 8193)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    observed = cache_residency.residency(path)
    assert observed["pages"] >= 3
    assert 0 <= observed["resident_pages"] <= observed["pages"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
