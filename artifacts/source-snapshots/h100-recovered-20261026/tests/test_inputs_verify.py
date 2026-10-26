"""Tests for verify_generated_inputs.py: reference selection, pass path and corruption detection.

Datasets are tiny (SF0.01, a second or two) and generated with the real generators. They need the
DuckDB tpch extension (cached after the first INSTALL); tests skip if it cannot be loaded.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

duckdb = pytest.importorskip("duckdb")
import generate_data  # noqa: E402
import verify_generated_inputs as vgi  # noqa: E402

SCALE = 0.01
EVIDENCE = REPO / "evidence" / "INPUT_MANIFESTS"


def run(root: Path, tmp_path: Path, *extra: str) -> tuple[int, dict]:
    report = tmp_path / f"report-{len(list(tmp_path.glob('report-*.json')))}.json"
    code = vgi.main(["--data-root", str(root), "--scales", str(SCALE), "--allow-no-reference",
                     "--threads", "2", "--workers", "2", "--memory-limit", "1GB",
                     "--temp-dir", str(tmp_path), "--report", str(report), *extra])
    return code, json.loads(report.read_text())


def statuses(report: dict) -> dict[str, str]:
    return {c["id"]: c["status"] for c in report["datasets"][0]["checks"]}


@pytest.fixture(scope="module")
def pristine(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("pristine")
    try:
        generate_data.generate(root, SCALE, threads=2, memory_limit="1GB")
    except Exception as error:  # extension download unavailable offline
        pytest.skip(f"cannot generate SF{SCALE} with DuckDB tpch: {error}")
    return root


@pytest.fixture()
def dataset(pristine, tmp_path) -> Path:
    root = tmp_path / "data"
    shutil.copytree(pristine, root)
    return root


def dataset_dir(root: Path) -> Path:
    return root / generate_data.scale_name(SCALE)


# ---- reference selection against the real evidence ---------------------------------------------

@pytest.mark.parametrize("scale, namespace", [
    (1, "production-20261025"), (10, "production-20261025"), (30, "production-20261025"),
    (100, "large-sf100-20261025"), (300, "large-sf300-20261025"),
])
def test_reference_namespace_per_scale(scale, namespace):
    path, found = vgi.find_reference(scale, vgi.MANIFEST_DIR)
    assert path is not None and path.parent.name == namespace
    # the namespaces reused one dataset per scale: their manifests must be byte-identical
    assert len({item["sha256"] for item in found}) == 1, found


def test_reference_missing_scale():
    path, found = vgi.find_reference(0.01, vgi.MANIFEST_DIR)
    assert path is None and found == []


def test_a100_manifests_declare_the_expected_generation_parameters():
    for scale in (1, 10, 30):
        manifest = json.loads(vgi.find_reference(scale, vgi.MANIFEST_DIR)[0].read_text())
        assert manifest["duckdb_version"] == "1.5.6"
        assert manifest["generation_sql"] == f"CALL dbgen(sf = {float(scale)!r})"
        assert manifest["generation_threads"] == 30
        assert manifest["parquet"] == vgi.PARQUET_EXPECTED
    for scale in (100, 300):
        manifest = json.loads(vgi.find_reference(scale, vgi.MANIFEST_DIR)[0].read_text())
        assert vgi.is_chunked(manifest)
        for table in vgi.TABLES:
            assert {"row_hash_sum", "row_hash_xor", "key_hash_sum", "key_hash_xor"} <= set(manifest["tables"][table])


def test_proof_covers_sf1_and_smoke_scales():
    for scale in (0.01, 0.013, 1.0):
        tables, source = vgi.proof_reference(scale)
        assert tables and set(tables) == set(vgi.TABLES) and source


# ---- pass and fail paths on a real tiny dataset -------------------------------------------------

def test_pristine_dataset_passes(dataset, tmp_path):
    code, report = run(dataset, tmp_path)
    assert code == 0 and report["ok"]
    result = statuses(report)
    assert result["disk_vs_new_manifest"] == "PASS"
    assert result["parquet_footers"] == "PASS"
    assert result["unique_primary_keys"] == "PASS"
    assert any(k.startswith("content_fingerprint[A100-host") and v == "PASS" for k, v in result.items())
    assert "FAIL" not in result.values()


def test_report_is_never_overwritten(dataset, tmp_path):
    target = tmp_path / "once.json"
    args = ["--data-root", str(dataset), "--scales", str(SCALE), "--allow-no-reference", "--threads", "2",
            "--temp-dir", str(tmp_path), "--report", str(target)]
    assert vgi.main(args) == 0
    with pytest.raises(SystemExit):
        vgi.main(args)


def test_flipped_byte_is_detected(dataset, tmp_path):
    victim = sorted((dataset_dir(dataset) / "lineitem").glob("*.parquet"))[0]
    data = bytearray(victim.read_bytes())
    data[len(data) // 2] ^= 0xFF
    victim.write_bytes(bytes(data))
    code, report = run(dataset, tmp_path)
    assert code == 1
    assert statuses(report)["disk_vs_new_manifest"] == "FAIL"


def test_truncated_file_is_detected(dataset, tmp_path):
    victim = sorted((dataset_dir(dataset) / "orders").glob("*.parquet"))[0]
    victim.write_bytes(victim.read_bytes()[:-100])
    code, report = run(dataset, tmp_path, "--no-rehash")
    assert code == 1 and statuses(report)["disk_vs_new_manifest"] == "FAIL"


def test_missing_and_extra_files_are_detected(dataset, tmp_path):
    folder = dataset_dir(dataset)
    extra = folder / "supplier" / "stray.parquet"
    shutil.copy(next((folder / "supplier").glob("*.parquet")), extra)
    code, report = run(dataset, tmp_path)
    assert code == 1
    detail = next(c for c in report["datasets"][0]["checks"] if c["id"] == "disk_vs_new_manifest")["detail"]
    assert "unlisted supplier/stray.parquet" in detail
    extra.unlink()
    next((folder / "region").glob("*.parquet")).unlink()
    code, report = run(dataset, tmp_path)
    assert code == 1
    detail = next(c for c in report["datasets"][0]["checks"] if c["id"] == "disk_vs_new_manifest")["detail"]
    assert "missing region/" in detail


def test_partial_write_leftovers_are_detected(dataset, tmp_path):
    (dataset / ".sf0p01.building").mkdir()
    code, report = run(dataset, tmp_path)
    assert code == 1 and statuses(report)["disk_vs_new_manifest"] == "FAIL"


def test_wrong_content_with_consistent_manifest_is_caught_by_fingerprint(dataset, tmp_path):
    """Silently wrong data: files and manifest agree, but a value changed. Only the fingerprint sees it."""
    folder = dataset_dir(dataset)
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    target = folder / "region" / "data_0.parquet"
    connection = duckdb.connect(":memory:")
    replacement = folder / "region" / "data_0.new"
    connection.execute(
        f"COPY (SELECT r_regionkey, lower(r_name) AS r_name, r_comment FROM read_parquet('{target}')) "
        f"TO '{replacement}' (FORMAT PARQUET, COMPRESSION SNAPPY, ROW_GROUP_SIZE 122880)")
    replacement.replace(target)
    entry = manifest["tables"]["region"]["files"][0]
    entry["bytes"], entry["sha256"] = target.stat().st_size, hashlib.sha256(target.read_bytes()).hexdigest()
    manifest["tables"]["region"]["parquet_bytes"] = entry["bytes"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    code, report = run(dataset, tmp_path)
    result = statuses(report)
    assert result["disk_vs_new_manifest"] == "PASS"  # self-consistent
    assert any(k.startswith("content_fingerprint[A100-host") and v == "FAIL" for k, v in result.items())
    assert code == 1


def test_reference_row_count_and_schema_mismatch(dataset, tmp_path):
    reference = json.loads((dataset_dir(dataset) / "manifest.json").read_text())
    mutated = copy.deepcopy(reference)
    mutated["tables"]["orders"]["rows"] += 1
    path = tmp_path / "ref-rows.json"
    path.write_text(json.dumps(mutated))
    code, report = run(dataset, tmp_path, "--reference-manifest", str(path))
    assert code == 1 and statuses(report)["rows_and_schema_vs_reference"] == "FAIL"

    mutated = copy.deepcopy(reference)
    mutated["tables"]["part"]["schema"][0]["type"] = "VARCHAR"
    path = tmp_path / "ref-schema.json"
    path.write_text(json.dumps(mutated))
    code, report = run(dataset, tmp_path, "--reference-manifest", str(path))
    assert code == 1 and statuses(report)["rows_and_schema_vs_reference"] == "FAIL"


@pytest.mark.parametrize("key, value", [
    ("duckdb_version", "1.5.5"),
    ("generation_sql", "CALL dbgen(sf = 0.02)"),
    ("parquet", {"compression": "zstd", "row_group_size": 122880, "file_size_target": "128MB", "per_thread_output": True}),
])
def test_reference_header_mismatch_fails(dataset, tmp_path, key, value):
    reference = json.loads((dataset_dir(dataset) / "manifest.json").read_text())
    reference[key] = value
    path = tmp_path / "ref-header.json"
    path.write_text(json.dumps(reference))
    code, report = run(dataset, tmp_path, "--reference-manifest", str(path))
    assert code == 1 and "FAIL" in statuses(report).values()


def test_thread_count_mismatch_only_warns_and_strict_fails(dataset, tmp_path):
    reference = json.loads((dataset_dir(dataset) / "manifest.json").read_text())
    reference["generation_threads"] = 30
    path = tmp_path / "ref-threads.json"
    path.write_text(json.dumps(reference))
    code, report = run(dataset, tmp_path, "--reference-manifest", str(path))
    assert code == 0 and statuses(report)["param.generation_threads"] == "WARN"
    code, _ = run(dataset, tmp_path, "--reference-manifest", str(path), "--strict")
    assert code == 1


def test_byte_identity_policies(dataset, tmp_path):
    reference = json.loads((dataset_dir(dataset) / "manifest.json").read_text())
    reference["tables"]["customer"]["files"][0]["sha256"] = "0" * 64
    path = tmp_path / "ref-bytes.json"
    path.write_text(json.dumps(reference))
    code, report = run(dataset, tmp_path, "--reference-manifest", str(path))
    assert code == 0 and statuses(report)["byte_identity"] == "INFO"
    code, report = run(dataset, tmp_path, "--reference-manifest", str(path), "--byte-identity", "require")
    assert code == 1 and statuses(report)["byte_identity"] == "FAIL"


def test_chunked_generator_output_verifies(tmp_path):
    generate_large_data = pytest.importorskip("generate_large_data")
    root = tmp_path / "chunked"
    root.mkdir()
    try:
        generate_large_data.generate(root, SCALE, workers=2, children_override=3, memory_limit="1GB")
    except Exception as error:
        pytest.skip(f"chunked generation unavailable: {error}")
    code, report = run(root, tmp_path)
    result = statuses(report)
    assert code == 0, result
    assert any(k.startswith("content_fingerprint[new manifest") and v == "PASS" for k, v in result.items())
    assert any(k.startswith("content_fingerprint[A100-host") and v == "PASS" for k, v in result.items())


def test_chunked_fingerprints_are_independent_of_the_chunk_count(tmp_path):
    """The A100 SF100/SF300 manifests rest on this: different `children` give the same content fingerprint."""
    generate_large_data = pytest.importorskip("generate_large_data")
    roots = {}
    for children in (3, 7):
        roots[children] = tmp_path / f"chunks{children}"
        roots[children].mkdir()
        try:
            generate_large_data.generate(roots[children], SCALE, workers=2, children_override=children, memory_limit="1GB")
        except Exception as error:
            pytest.skip(f"chunked generation unavailable: {error}")
    reference = roots[7] / generate_data.scale_name(SCALE) / "manifest.json"
    code, report = run(roots[3], tmp_path, "--reference-manifest", str(reference))
    result = statuses(report)
    assert code == 0, result
    assert result["generation_sql"] == "WARN"  # chunk count differs: reported, not fatal
    assert result["file_layout_vs_reference"] == "WARN"
    assert result["rows_and_schema_vs_reference"] == "PASS"
    assert result["content_fingerprint[A100 manifest]"] == "PASS"
    assert result["byte_identity"] == "INFO"


def test_chunked_reference_with_different_content_fails(tmp_path):
    generate_large_data = pytest.importorskip("generate_large_data")
    root = tmp_path / "chunks"
    root.mkdir()
    try:
        generate_large_data.generate(root, SCALE, workers=2, children_override=3, memory_limit="1GB")
    except Exception as error:
        pytest.skip(f"chunked generation unavailable: {error}")
    manifest = json.loads((root / generate_data.scale_name(SCALE) / "manifest.json").read_text())
    manifest["tables"]["lineitem"]["row_hash_sum"] = str(int(manifest["tables"]["lineitem"]["row_hash_sum"]) + 1)
    reference = tmp_path / "ref.json"
    reference.write_text(json.dumps(manifest))
    code, report = run(root, tmp_path, "--reference-manifest", str(reference))
    assert code == 1 and statuses(report)["content_fingerprint[A100 manifest]"] == "FAIL"
