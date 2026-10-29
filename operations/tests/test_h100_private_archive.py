"""Fabricated local byte fixtures; no release or measured-study PASS evidence."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

from operations import h100_private_archive as archive


def test_parts_reader_and_binary_split_preserve_original(tmp_path):
    source = tmp_path / "original.tar"
    body = bytes(range(251)) * 19
    source.write_bytes(body)
    stage = tmp_path / "stage"
    stage.mkdir()
    expected = {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    pieces = archive.split_original(source, stage, "recovery-final.tar", expected, tmp_path, piece_bytes=107)
    paths = [tmp_path / p["path"] for p in pieces]
    assert all(p["bytes"] <= 107 for p in pieces)
    assert archive.hash_stream(paths) == expected
    with archive.PartsReader(paths) as joined:
        assert b"".join(iter(lambda: joined.read(53), b"")) == body
    assert source.read_bytes() == body


def test_split_refuses_overwrite_and_wrong_original(tmp_path):
    source = tmp_path / "original.tar"
    source.write_bytes(b"original content")
    stage = tmp_path / "stage"
    stage.mkdir()
    existing = stage / "f.tar.part-00000"
    existing.write_bytes(b"retain")
    expected = {"bytes": 16, "sha256": hashlib.sha256(b"original content").hexdigest()}
    with pytest.raises(FileExistsError):
        archive.split_original(source, stage, "f.tar", expected, tmp_path, piece_bytes=8)
    assert existing.read_bytes() == b"retain"
    with pytest.raises(ValueError, match="historical control"):
        archive.split_original(source, stage, "g.tar", {"bytes": 1, "sha256": "0" * 64}, tmp_path)
    assert source.read_bytes() == b"original content"


def test_supplemental_uses_exact_allowlist_and_preserves_paths(tmp_path):
    source = tmp_path / "specific" / "record.json"
    source.parent.mkdir()
    source.write_bytes(b'{"actual":"fixture"}\n')
    (tmp_path / "unrelated.json").write_bytes(b"excluded")
    stage = tmp_path / "stage"
    stage.mkdir()
    identity = archive.ref(source, tmp_path)
    result = archive.supplemental(stage, "metadata.tar.gz", {identity["path"]: identity}, tmp_path)
    with tarfile.open(tmp_path / result["path"], "r:gz") as opened:
        assert opened.getnames() == ["specific/record.json"]
        assert opened.extractfile("specific/record.json").read() == source.read_bytes()
    with pytest.raises(FileExistsError):
        archive.supplemental(stage, "metadata.tar.gz", {identity["path"]: identity}, tmp_path)


def test_supplemental_refuses_source_drift_and_symlink(tmp_path):
    source = tmp_path / "record.json"
    source.write_bytes(b"before")
    identity = archive.ref(source, tmp_path)
    source.write_bytes(b"after")
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(ValueError, match="changed before"):
        archive.supplemental(stage, "changed.tar.gz", {identity["path"]: identity}, tmp_path)
    link = tmp_path / "link.json"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="Symlink"):
        archive.ref(link, tmp_path)


def test_bundle_rejects_synthetic_before_any_extraction(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    manifest = {"schema_version": "research-evidence-bundle-v1", "status": "PASS_SYNTHETIC",
                "eligibility": {"synthetic": True}}
    (bundle / "BUNDLE_MANIFEST.json").write_text(json.dumps(manifest))
    records = tmp_path / "records"
    records.mkdir()
    with pytest.raises(ValueError, match="genuine completed"):
        archive.verify_bundle(bundle, tmp_path, records)
    assert list(records.iterdir()) == []


def test_stage_refuses_missing_real_inputs_before_creation(tmp_path):
    stage, records = tmp_path / "stage", tmp_path / "records"
    with pytest.raises((ValueError, FileNotFoundError)):
        archive.stage_study(tmp_path, stage, records)
    assert not stage.exists()
    assert not records.exists()


def test_historical_source_binding_uses_git_commit_bytes(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Fixture"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "fixture@example.invalid"], check=True)
    source = tmp_path / "archival_tool.py"
    source.write_bytes(b"preserved earlier source\n")
    expected = archive.ref(source, tmp_path)
    subprocess.run(["git", "-C", str(tmp_path), "add", "--", source.name], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "fabricated source fixture"], check=True)
    commit = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    source.write_bytes(b"later catalog-only changes\n")
    assert archive.verify_git_reference(tmp_path, commit, expected) == expected
    with pytest.raises(ValueError, match="commit bytes differ"):
        archive.verify_git_reference(tmp_path, commit, archive.ref(source, tmp_path))
    with pytest.raises(ValueError, match="Unsafe"):
        archive.verify_git_reference(tmp_path, commit, {**expected, "path": "../outside"})
