"""Lifecycle boundaries and read-only genuine evidence; no fabricated timings."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil

import pytest

from research_reporting import contention_figures as figures


ROOT = Path(__file__).resolve().parents[2]
GENUINE = ROOT / "rerun/contention-20261028/offline-full-audit-v3"


def lifecycle_only(status="PARTIAL"):
    """Visible test-only lifecycle record: contains no observations or results."""
    return {"schema_version": "h100-contention-replay-v1", "synthetic": False,
        "experiment_id": figures.audit.EXPERIMENT_ID, "plan_sha256": figures.audit.PLAN_SHA,
        "prestart_sha256": figures.audit.PRESTART_SHA, "integrity_status": "PASS", "findings": [],
        "status": status, "publication_ready": status != "PARTIAL", "terminal_driver": {"terminal": True},
        "handoff": {"status": "PASS", "same_scheduler_resumed": True,
                    "original_payload_verified": True, "no_action_overlap": True}}


def test_partial_requires_explicit_draft_and_never_becomes_final():
    report = lifecycle_only()
    with pytest.raises(ValueError, match="PARTIAL requires --draft"):
        figures.disposition_gate(report)
    assert figures.disposition_gate(report, draft=True) is False


@pytest.mark.parametrize("status", ["COMPLETE", "STOPPED_WITH_FAILURE"])
def test_terminal_boundary_requires_actual_separation_and_publication_ready(status):
    report = lifecycle_only(status)
    assert figures.disposition_gate(report) is True
    assert figures.disposition_gate(report, draft=True) is False
    for field in ("original_payload_verified", "same_scheduler_resumed", "no_action_overlap"):
        changed = copy.deepcopy(report)
        changed["handoff"][field] = False
        with pytest.raises(ValueError, match="separation"):
            figures.disposition_gate(changed)
    with pytest.raises(ValueError, match="not publication ready"):
        figures.disposition_gate(report | {"publication_ready": False})


@pytest.mark.parametrize("mutation", [{"status": "FAIL"}, {"integrity_status": "FAIL"},
    {"findings": [{"message": "TEST ONLY"}]}, {"synthetic": True}, {"plan_sha256": "0" * 64},
    {"prestart_sha256": "0" * 64}])
def test_draft_cannot_override_integrity_or_identity(mutation):
    with pytest.raises(ValueError):
        figures.disposition_gate(lifecycle_only() | mutation, draft=True)


@pytest.fixture()
def genuine_copy(tmp_path):
    if not GENUINE.is_dir():
        pytest.skip("Private genuine contention audit is unavailable")
    target = tmp_path / "GENUINE_AUDIT_COPY"
    shutil.copytree(GENUINE, target)
    return target


@pytest.mark.parametrize("filename", ["QUERY_ACTIONS_DESCRIPTIVE.csv", "COMMON_QUERY_OVERLAP.csv"])
def test_both_csv_hashes_checked_before_replay(genuine_copy, monkeypatch, filename):
    with (genuine_copy / filename).open("a") as handle:
        handle.write("DELIBERATE TEST ONLY CORRUPTION\n")
    monkeypatch.setattr(figures, "fresh_replay", lambda _: pytest.fail("Unverified CSV reached replay"))
    with pytest.raises(ValueError, match="output hash differs"):
        figures.load_audit(genuine_copy, draft=True)


def test_stale_auditor_rejected(genuine_copy):
    with (genuine_copy / "AUDITOR_SOURCE.py").open("a") as handle:
        handle.write("\n# TEST ONLY STALE SOURCE\n")
    with pytest.raises(ValueError, match="Auditor source"):
        figures.load_audit(genuine_copy, draft=True)


def test_genuine_partial_refuses_final_export_before_output_creation(genuine_copy, tmp_path):
    output = tmp_path / "FINAL_MUST_NOT_EXIST"
    with pytest.raises(ValueError, match="PARTIAL requires --draft"):
        figures.render(genuine_copy, output)
    assert not output.exists()


def test_edited_terminal_receipt_cannot_substitute_for_actual_proof(genuine_copy):
    path = genuine_copy / "CONTENTION_REPLAY.json"
    report = json.loads(path.read_text())
    report.update(status="COMPLETE", publication_ready=True)
    report["handoff"].update(status="PASS", original_payload_verified=True, no_action_overlap=True)
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="freshly reproduced"):
        figures.load_audit(genuine_copy)


def test_genuine_partial_export_all_formats_and_retains_exact_intervals(genuine_copy, tmp_path):
    output = tmp_path / "DRAFT_FIGURES"
    manifest = figures.render(genuine_copy, output, draft=True)
    assert manifest["draft"] is True and manifest["final"] is False and manifest["publication_ready"] is False
    assert manifest["audit_status"] == "PARTIAL"
    assert manifest["audit_counts"]["common_measured_pairs"] == 110
    assert manifest["phase_measured_rows"] == {"pilot": {"True": 44, "False": 2}, "main": {"True": 176, "False": 8}}
    for phase in ("pilot", "main"):
        for stem in (f"{phase}_pair_A_common_actions", f"{phase}_pair_B_common_actions", f"{phase}_actual_action_timeline"):
            for extension in ("svg", "png", "pdf"):
                assert (output / f"{stem}.{extension}").stat().st_size > 1000
            assert "DRAFT" in (output / f"{stem}.svg").read_text()
            assert "<dc:date>" not in (output / f"{stem}.svg").read_text()
            assert "CreationDate" not in (output / f"{stem}.pdf").read_bytes().decode("latin-1")
    for name, digest in manifest["output_files_sha256"].items():
        assert figures.audit.sha(output / name) == digest
    # Read the genuine CSV only; no synthetic study timings are generated.
    import csv
    from datetime import datetime
    rows = list(csv.DictReader((genuine_copy / "QUERY_ACTIONS_DESCRIPTIVE.csv").open()))
    data = {"actions": rows}
    measured = [r for r in rows if r["phase"] == "measured"]
    waves = {r["wave_id"] for r in measured}
    assert len([w for w in waves if w.startswith("main-")]) == 16
    for wave in waves:
        segments = figures.timeline_segments(data, wave)
        original = [r for r in measured if r["wave_id"] == wave]
        assert len(segments) == len(original)
        for segment, row in zip(segments, original):
            assert segment["width"] == (datetime.fromisoformat(row["action_ended_at_utc"]).timestamp() -
                                       datetime.fromisoformat(row["action_started_at_utc"]).timestamp())
