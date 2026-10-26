"""Archive mechanism tests use marked tiny synthetic inputs, never study results."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research_reporting import package_evidence as pack


def fixture(root):
    root.mkdir()
    (root / "SYNTHETIC.json").write_text(json.dumps({"synthetic": True}))
    (root / "raw").mkdir()
    (root / "raw" / "text.txt").write_text("SYNTHETIC ONLY — δ\n" * 50)
    (root / "raw" / "bytes.bin").write_bytes(bytes(range(256)) * 19)
    return root


def joined(output, manifest):
    return b"".join((output / part["path"]).read_bytes() for part in manifest["parts"])


def test_real_compression_chunk_hashes_reassembly_and_original_bytes(tmp_path):
    root = fixture(tmp_path / "input")
    original = pack.inventory(root)
    output = tmp_path / "bundle"
    value = pack.package(root, output, allow_synthetic=True, chunk_bytes=257)
    assert value["status"] == "PASS_SYNTHETIC"
    assert value["eligibility"]["synthetic"] is True
    assert len(value["parts"]) > 1
    assert all(0 < part["bytes"] <= 257 for part in value["parts"])
    for part in value["parts"]:
        assert pack.sha256(output / part["path"]) == part["sha256"]
    raw = joined(output, value)
    assert hashlib.sha256(raw).hexdigest() == value["archive_sha256"]
    assert len(raw) == value["archive_bytes"]
    assert gzip.decompress(raw)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        assert {member.name for member in archive} == {"evidence/" + name for name in original}
        for name, meta in original.items():
            body = archive.extractfile("evidence/" + name).read()
            assert body == (root / name).read_bytes()
            assert hashlib.sha256(body).hexdigest() == meta["sha256"]
    assert pack.inventory(root) == original
    assert "SYNTHETIC" in (output / "REASSEMBLE.md").read_text()


def test_gzip_stream_is_deterministic_across_outputs_and_chunk_sizes(tmp_path):
    root = fixture(tmp_path / "input")
    first, second = tmp_path / "a", tmp_path / "b"
    a = pack.package(root, first, allow_synthetic=True, chunk_bytes=127)
    b = pack.package(root, second, allow_synthetic=True, chunk_bytes=5000)
    assert joined(first, a) == joined(second, b)
    assert a["archive_sha256"] == b["archive_sha256"]


def test_no_overwrite_or_self_inclusion(tmp_path):
    root = fixture(tmp_path / "input")
    output = tmp_path / "bundle"
    pack.package(root, output, allow_synthetic=True)
    before = (output / "BUNDLE_MANIFEST.json").read_bytes()
    with pytest.raises(FileExistsError):
        pack.package(root, output, allow_synthetic=True)
    assert (output / "BUNDLE_MANIFEST.json").read_bytes() == before
    with pytest.raises(ValueError, match="outside evidence"):
        pack.package(root, root / "bundle", allow_synthetic=True)


def test_unmarked_or_incomplete_evidence_cannot_be_packaged_as_real(tmp_path):
    root = fixture(tmp_path / "input")
    with pytest.raises(ValueError, match="explicit"):
        pack.package(root, tmp_path / "forbidden")
    (root / "SYNTHETIC.json").unlink()
    with pytest.raises(ValueError, match="requires --audit-complete"):
        pack.package(root, tmp_path / "real")
    with pytest.raises(ValueError, match="only valid"):
        pack.package(root, tmp_path / "wrong-label", allow_synthetic=True)


@pytest.mark.parametrize("bad", ["symlink", "parquet", "private-key"])
def test_outside_scope_inputs_fail_without_leaking_target_contents(tmp_path, bad):
    root = fixture(tmp_path / "input")
    if bad == "symlink":
        target = tmp_path / "unrelated"
        target.write_text("not part of evidence")
        (root / "link").symlink_to(target)
    else:
        (root / ("data.parquet" if bad == "parquet" else "id_ed25519")).write_text("outside scope")
    output = tmp_path / "bundle"
    with pytest.raises(ValueError):
        pack.package(root, output, allow_synthetic=True)
    assert not (output / "BUNDLE_MANIFEST.json").exists()
    assert json.loads((output / "BUNDLE_FAILED.json").read_text())["status"] == "FAIL"


def test_transient_same_size_mutation_is_caught_in_the_tar_byte_stream(tmp_path, monkeypatch):
    root = fixture(tmp_path / "input")
    path = root / "raw" / "bytes.bin"
    original = path.read_bytes()
    compress = pack.compress_files

    def mutate_and_restore(root, output, entries, limit):
        path.write_bytes(b"z" * len(original))
        try:
            return compress(root, output, entries, limit)
        finally:
            path.write_bytes(original)

    monkeypatch.setattr(pack, "compress_files", mutate_and_restore)
    output = tmp_path / "bundle"
    with pytest.raises(ValueError, match="while archiving"):
        pack.package(root, output, allow_synthetic=True)
    assert path.read_bytes() == original
    assert not (output / "BUNDLE_MANIFEST.json").exists()
    assert (output / "BUNDLE_FAILED.json").is_file()


def test_new_file_during_packaging_is_not_silently_excluded(tmp_path, monkeypatch):
    root = fixture(tmp_path / "input")
    compress = pack.compress_files

    def add_late_file(root, output, entries, limit):
        value = compress(root, output, entries, limit)
        (root / "late.json").write_text("{}")
        return value

    monkeypatch.setattr(pack, "compress_files", add_late_file)
    output = tmp_path / "bundle"
    with pytest.raises(ValueError, match="changed during packaging"):
        pack.package(root, output, allow_synthetic=True)
    assert not (output / "BUNDLE_MANIFEST.json").exists()
    assert (output / "BUNDLE_FAILED.json").is_file()


def test_late_output_failure_cannot_leave_a_pass_manifest(tmp_path, monkeypatch):
    root = fixture(tmp_path / "input")
    write_text = Path.write_text

    def fail_readme(path, *args, **kwargs):
        if path.name == "REASSEMBLE.md":
            raise OSError("simulated output storage failure")
        return write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_readme)
    output = tmp_path / "bundle"
    with pytest.raises(OSError, match="storage failure"):
        pack.package(root, output, allow_synthetic=True)
    assert list(output.glob("evidence.tar.gz.part-*"))
    assert not (output / "BUNDLE_MANIFEST.json").exists()
    assert (output / "BUNDLE_FAILED.json").is_file()
