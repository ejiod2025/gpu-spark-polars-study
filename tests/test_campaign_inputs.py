"""The input gate of the campaign (design section 3): verify_generated_inputs.py over several scales in one call.

A previous attempt to verify SF10-SF300 never actually ran, so the contract tested here is mostly about
unambiguous outcomes: the last stdout line is exactly `RESULT: OK` or `RESULT: FAILED`, the exit status
matches, every requested scale is either verified or reported as failed, and a crash, a missing dataset or an
interrupt can never look like success. Datasets are tiny (SF0.01 and SF0.013) and made by the real generators.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

duckdb = pytest.importorskip("duckdb")
import generate_data  # noqa: E402
import verify_generated_inputs as vgi  # noqa: E402

SCALES = (0.01, 0.013)
FORMAL_SCALES = [1, 10, 30, 100, 300]


@pytest.fixture(scope="module")
def pristine(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("inputs")
    try:
        for scale in SCALES:
            generate_data.generate(root, scale, threads=2, memory_limit="1GB")
    except Exception as error:  # DuckDB tpch extension unavailable offline
        pytest.skip(f"cannot generate tiny TPC-H datasets: {error}")
    return root


@pytest.fixture()
def data(pristine, tmp_path) -> Path:
    root = tmp_path / "data"
    shutil.copytree(pristine, root)
    return root


def verify(data: Path, tmp_path: Path, capsys, *extra: str, scales=SCALES, name="report") -> tuple[int, str, dict | None]:
    report = tmp_path / f"{name}.json"
    code = vgi.main(["--data-root", str(data), "--scales", *map(str, scales), "--allow-no-reference", "--threads", "2",
                     "--workers", "2", "--memory-limit", "1GB", "--temp-dir", str(tmp_path), "--report", str(report), *extra])
    out = capsys.readouterr().out
    return code, out, json.loads(report.read_text()) if report.exists() else None


def last_line(out: str) -> str:
    return out.rstrip("\n").splitlines()[-1]


# ---- one call, several scales, unambiguous outcome -------------------------------------------------------------------

def test_two_scales_in_one_call_end_with_result_ok(data, tmp_path, capsys):
    code, out, report = verify(data, tmp_path, capsys)
    assert code == 0 and last_line(out) == "RESULT: OK"
    assert report["complete"] is True and report["ok"] is True and report["result"] == "OK"
    assert report["scales_requested"] == list(SCALES) and report["scales_verified"] == list(SCALES)
    assert [d["ok"] for d in report["datasets"]] == [True, True]
    assert "sf0p01" in out and "sf0p013" in out and "SUMMARY (verified 2 of 2 requested scales" in out
    assert out.count("== DONE") == 2


def test_result_line_is_exactly_ok_or_failed_and_nothing_follows_it(data, tmp_path, capsys):
    _, out, _ = verify(data, tmp_path, capsys)
    assert out.endswith("RESULT: OK\n")
    victim = sorted((data / "sf0p013" / "lineitem").glob("*.parquet"))[0]
    raw = bytearray(victim.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    victim.write_bytes(bytes(raw))
    code, out, report = verify(data, tmp_path, capsys, name="bad")
    assert code == 1 and out.endswith("RESULT: FAILED\n") and last_line(out) == "RESULT: FAILED"
    assert report["result"] == "FAILED" and report["ok"] is False


def test_a_corrupt_later_scale_fails_the_call_but_the_earlier_scale_is_still_reported(data, tmp_path, capsys):
    victim = sorted((data / "sf0p013" / "orders").glob("*.parquet"))[0]
    victim.write_bytes(victim.read_bytes()[:-50])
    code, out, report = verify(data, tmp_path, capsys)
    assert code == 1 and last_line(out) == "RESULT: FAILED"
    assert [d["ok"] for d in report["datasets"]] == [True, False]
    assert report["scales_verified"] == list(SCALES)  # both ran; the failure is attributed to the right scale
    summary = {m.group(1): m.group(2) for m in re.finditer(r"^  (sf0p013?) +(OK|FAILED) +PASS", out, re.M)}
    assert summary == {"sf0p01": "OK", "sf0p013": "FAILED"}


def test_a_missing_dataset_is_a_failure_of_that_scale_and_later_scales_still_run(data, tmp_path, capsys):
    shutil.rmtree(data / "sf0p01")
    code, out, report = verify(data, tmp_path, capsys)
    assert code == 1 and last_line(out) == "RESULT: FAILED"
    assert [d["ok"] for d in report["datasets"]] == [False, True]
    assert any(c["id"] == "new_manifest" and c["status"] == "FAIL" for c in report["datasets"][0]["checks"])


def test_a_crash_inside_a_scale_is_a_failure_not_silence(data, tmp_path, capsys, monkeypatch):
    original = vgi.check_footers

    def explode(ds, connection):
        if ds.scale == SCALES[0]:
            raise RuntimeError("simulated crash")
        return original(ds, connection)

    monkeypatch.setattr(vgi, "check_footers", explode)
    code, out, report = verify(data, tmp_path, capsys)
    assert code == 1 and last_line(out) == "RESULT: FAILED"
    # the exception is converted per check or per dataset; either way the scale is FAILED and the next one ran
    assert [d["ok"] for d in report["datasets"]] == [False, True]
    assert report["datasets"][0]["counts"]["FAIL"] >= 1


def test_a_crash_before_the_scale_loop_finishes_still_prints_a_result(data, tmp_path, capsys, monkeypatch):
    def explode(scale, root, args, ds=None):
        raise MemoryError("simulated")

    monkeypatch.setattr(vgi, "verify_dataset", explode)
    code, out, report = verify(data, tmp_path, capsys)
    assert code == 1 and last_line(out) == "RESULT: FAILED"
    assert [c["id"] for d in report["datasets"] for c in d["checks"] if c["status"] == "FAIL"] == ["crashed", "crashed"]


def test_interrupt_is_reported_as_failed_and_names_the_unverified_scales(data, tmp_path, capsys, monkeypatch):
    original = vgi.verify_dataset

    def interrupt(scale, root, args, ds=None):
        if scale == SCALES[1]:
            raise KeyboardInterrupt
        return original(scale, root, args, ds)

    monkeypatch.setattr(vgi, "verify_dataset", interrupt)
    code, out, report = verify(data, tmp_path, capsys)
    assert code == 1 and last_line(out) == "RESULT: FAILED"
    assert report["interrupted"] is True and report["ok"] is False and report["complete"] is True
    assert "INTERRUPTED" in out


def test_report_exists_after_every_scale_with_complete_false_until_the_end(data, tmp_path, capsys, monkeypatch):
    original = vgi.verify_dataset
    seen = {}
    report_path = tmp_path / "progress.json"

    def watch(scale, root, args, ds=None):
        if scale == SCALES[1]:
            seen["mid"] = json.loads(report_path.read_text())
        return original(scale, root, args, ds)

    monkeypatch.setattr(vgi, "verify_dataset", watch)
    code, _, final = verify(data, tmp_path, capsys, name="progress")
    assert code == 0
    assert seen["mid"]["complete"] is False and seen["mid"]["ok"] is False and seen["mid"]["result"] == "INCOMPLETE"
    assert seen["mid"]["scales_verified"] == [SCALES[0]]
    assert final["complete"] is True and final["ok"] is True


def test_usage_errors_exit_2_without_a_result_line(data, tmp_path, capsys):
    with pytest.raises(SystemExit) as stop:
        vgi.main(["--data-root", str(data), "--scales", "0.01", "--reference-manifest", str(tmp_path / "x.json"), "--scales", "0.01", "0.013"])
    assert stop.value.code == 2
    assert "RESULT" not in capsys.readouterr().out


# ---- --formal ----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("extra", [
    ["--scales", "1", "10"], ["--no-rehash"], ["--allow-no-reference"], ["--byte-identity", "ignore"], ["--unique-keys", "off"],
    ["--reference-manifest", "x.json"],
])
def test_formal_pins_the_scales_and_forbids_bypass_flags(tmp_path, extra):
    with pytest.raises(SystemExit) as stop:
        vgi.main(["--formal", "--data-root", str(tmp_path), "--report", str(tmp_path / "r.json"), *extra])
    assert stop.value.code == 2


def test_formal_requires_a_report(tmp_path):
    with pytest.raises(SystemExit) as stop:
        vgi.main(["--formal", "--data-root", str(tmp_path)])
    assert stop.value.code == 2


def test_formal_with_no_data_fails_every_scale_and_says_so(tmp_path, capsys):
    code = vgi.main(["--formal", "--data-root", str(tmp_path / "empty"), "--report", str(tmp_path / "r.json"), "--temp-dir", str(tmp_path)])
    out = capsys.readouterr().out
    report = json.loads((tmp_path / "r.json").read_text())
    assert code == 1 and last_line(out) == "RESULT: FAILED"
    assert report["scales_requested"] == [float(s) for s in FORMAL_SCALES] and report["scales_verified"] == report["scales_requested"]
    assert [d["ok"] for d in report["datasets"]] == [False] * 5
    for scale in ("sf1 ", "sf10", "sf30", "sf100", "sf300"):
        assert scale in out


@pytest.fixture()
def tiny_formal(monkeypatch, pristine, tmp_path):
    """--formal on SF0.01 with a stand-in 'A100 manifest' (the dataset's own single-process manifest: rows, schema and
    parameters, no fingerprint) and no generation proof: the situation of SF10 and SF30 on the real host."""
    manifests = tmp_path / "a100"
    (manifests / "ns").mkdir(parents=True)
    shutil.copy(pristine / "sf0p01" / "manifest.json", manifests / "ns" / "sf0p01.json")
    monkeypatch.setattr(vgi, "FORMAL_SCALES", (0.01,))
    monkeypatch.setattr(vgi, "proof_reference", lambda scale, proof_files=vgi.PROOF_FILES: (None, None))
    data = tmp_path / "data"
    shutil.copytree(pristine, data)
    return data, manifests


def run_formal(data, manifests, tmp_path, capsys, *extra, name="formal"):
    report = tmp_path / f"{name}.json"
    code = vgi.main(["--formal", "--data-root", str(data), "--manifest-dir", str(manifests), "--threads", "2", "--workers", "2",
                     "--memory-limit", "1GB", "--temp-dir", str(tmp_path), "--report", str(report), *extra])
    return code, capsys.readouterr().out, json.loads(report.read_text())


def test_formal_builds_the_dbgen_reference_where_the_a100_manifest_has_no_fingerprint(tiny_formal, tmp_path, capsys):
    data, manifests = tiny_formal
    code, out, report = run_formal(data, manifests, tmp_path, capsys)
    checks = {c["id"]: c["status"] for c in report["datasets"][0]["checks"]}
    assert code == 0 and last_line(out) == "RESULT: OK", [k for k, v in checks.items() if v == "FAIL"]
    assert checks["dbgen_reference_auto"] == "INFO"
    assert any(k.startswith("content_fingerprint[in-memory CALL dbgen") and v == "PASS" for k, v in checks.items())
    assert "content_fingerprint[external]" not in checks
    assert report["datasets"][0]["timings"]["dbgen_reference_seconds"] >= 0 and report["formal"] is True


def test_formal_fails_when_no_external_content_reference_exists(tiny_formal, tmp_path, capsys):
    data, manifests = tiny_formal
    code, out, report = run_formal(data, manifests, tmp_path, capsys, "--reference-dbgen-max-scale", "0.001")
    checks = {c["id"]: c["status"] for c in report["datasets"][0]["checks"]}
    assert code == 1 and last_line(out) == "RESULT: FAILED" and checks["content_fingerprint[external]"] == "FAIL"


def test_formal_catches_wrong_content_that_every_other_check_accepts(tiny_formal, tmp_path, capsys):
    """Rows, schemas, parameters and file hashes all agree (the stand-in manifest is rewritten to match), only a value changed."""
    data, manifests = tiny_formal
    folder = data / "sf0p01"
    manifest = json.loads((folder / "manifest.json").read_text())
    target = folder / "region" / "data_0.parquet"
    replacement = folder / "region" / "data_0.new"
    connection = duckdb.connect(":memory:")
    connection.execute(f"COPY (SELECT r_regionkey, lower(r_name) AS r_name, r_comment FROM read_parquet('{target}')) "
                       f"TO '{replacement}' (FORMAT PARQUET, COMPRESSION SNAPPY, ROW_GROUP_SIZE 122880)")
    replacement.replace(target)
    import hashlib
    entry = manifest["tables"]["region"]["files"][0]
    entry["bytes"], entry["sha256"] = target.stat().st_size, hashlib.sha256(target.read_bytes()).hexdigest()
    manifest["tables"]["region"]["parquet_bytes"] = entry["bytes"]
    (folder / "manifest.json").write_text(json.dumps(manifest))
    code, out, report = run_formal(data, manifests, tmp_path, capsys)
    checks = {c["id"]: c["status"] for c in report["datasets"][0]["checks"]}
    assert checks["disk_vs_new_manifest"] == "PASS"
    assert any(k.startswith("content_fingerprint[in-memory CALL dbgen") and v == "FAIL" for k, v in checks.items())
    assert code == 1 and last_line(out) == "RESULT: FAILED"


def test_formal_turns_a_parameter_warning_into_a_failure(tiny_formal, tmp_path, capsys):
    data, manifests = tiny_formal
    reference = json.loads((manifests / "ns" / "sf0p01.json").read_text())
    reference["generation_threads"] = 30  # the dataset was generated with 2 threads: not the A100 protocol
    (manifests / "ns" / "sf0p01.json").write_text(json.dumps(reference))
    code, out, report = run_formal(data, manifests, tmp_path, capsys)
    assert code == 1 and last_line(out) == "RESULT: FAILED"
    assert report["datasets"][0]["gating_warnings"] == ["param.generation_threads"]
    assert "gating warnings: param.generation_threads" in out
    # the same situation outside --formal is only a warning
    non_formal = tmp_path / "nf.json"
    code = vgi.main(["--data-root", str(data), "--scales", "0.01", "--manifest-dir", str(manifests), "--threads", "2",
                     "--memory-limit", "1GB", "--temp-dir", str(tmp_path), "--report", str(non_formal), "--reference-dbgen"])
    assert code == 0


# ---- the real A100 evidence: which formal scale is verified against what -----------------------------------------------------------

def test_formal_scales_are_the_design_scales():
    assert list(vgi.FORMAL_SCALES) == [float(s) for s in FORMAL_SCALES]


@pytest.mark.parametrize("scale, external", [(1, True), (10, False), (30, False), (100, True), (300, True)])
def test_every_formal_scale_has_an_a100_manifest_and_the_right_content_reference(scale, external):
    path, found = vgi.find_reference(scale, vgi.MANIFEST_DIR)
    assert path is not None and len({item["sha256"] for item in found}) == 1
    ref = json.loads(path.read_text())
    proof, _ = vgi.proof_reference(scale)
    assert vgi.has_external_fingerprint(ref, proof) is external
    if not external:  # SF10/SF30: only the automatic dbgen reference gives content assurance, within the memory bound
        assert scale <= 30
    assert ref["scale_factor"] == scale and ref["duckdb_version"] == vgi.DUCKDB_VERSION


@pytest.mark.skipif(not (REPO / "data" / "sf1" / "manifest.json").is_file(), reason="data/sf1 not present")
def test_real_sf1_verifies_against_the_a100_manifest_and_proof(tmp_path, capsys):
    report = tmp_path / "sf1.json"
    code = vgi.main(["--data-root", str(REPO / "data"), "--scales", "1", "--temp-dir", str(tmp_path), "--report", str(report)])
    out = capsys.readouterr().out
    data = json.loads(report.read_text())
    assert last_line(out) == ("RESULT: OK" if code == 0 else "RESULT: FAILED")
    checks = {c["id"]: c["status"] for c in data["datasets"][0]["checks"]}
    assert checks["rows_and_schema_vs_reference"] == "PASS"
    assert any(k.startswith("content_fingerprint[A100-host") and v == "PASS" for k, v in checks.items())


def test_the_documented_formal_command_parses(tmp_path):
    """The command in the module docstring is what the runbook tells people to type: keep it valid."""
    doc = vgi.__doc__
    block = doc[doc.index("python verify_generated_inputs.py"):doc.index("echo \"exit status")]
    command = block.replace("\\\n", " ").split("| tee")[0].replace("2>&1", "").split()
    flags = [w.strip('"') for w in command[2:]]
    flags = [w.replace("$DATA_ROOT", str(tmp_path)).replace("$CAMPAIGN_ROOT", str(tmp_path / "c")) for w in flags]
    parser = vgi.build_parser()
    args = parser.parse_args(flags)
    vgi.resolve_args(parser, args)
    assert args.formal and args.scales == [float(s) for s in FORMAL_SCALES]
    assert (args.threads, args.workers, args.memory_limit) == (26, 16, "64GB")
    assert args.report == tmp_path / "c" / "inputs-verification.json"
