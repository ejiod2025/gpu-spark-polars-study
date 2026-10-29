"""Fabricated transfer/extraction boundaries; no network or release mutation."""
import hashlib
import io
import json
import tarfile

import pytest

from operations import h100_private_release_transfer as transfer
from operations import h100_private_retrieval_verify as verify


def test_draft_lookup_reconciles_paginated_list_without_creating(monkeypatch):
    calls = []
    def fake(args, **kwargs):
        calls.append(args)
        if "/releases/tags/" in args[1]:
            return None
        return json.dumps([[{"id": 17, "tag_name": transfer.TAG, "target_commitish": transfer.B, "draft": True}]])
    monkeypatch.setattr(transfer, "gh", fake)
    assert transfer.release()["id"] == 17
    assert all(c[0] == "api" for c in calls)
    assert calls[-1][-2:] == ["--paginate", "--slurp"]


def test_remote_set_rejects_conflicts_and_incomplete_assets():
    expected = {"evidence": {"bytes": 3, "sha256": "a" * 64}}
    good = {"id": 9, "name": "evidence", "state": "uploaded", "size": 3, "digest": "sha256:" + "a" * 64}
    assert transfer.match_remote([good], expected, complete=True)["evidence"] == good
    for bad in [{**good, "state": "starter"}, {**good, "size": 2}, {**good, "digest": "sha256:" + "b" * 64}, {**good, "name": "extra"}]:
        with pytest.raises(ValueError):
            transfer.match_remote([bad], expected, complete=True)


def test_asset_locator_rejects_query_redirect(monkeypatch):
    row = {"id": 3, "name": "x", "url": "https://api.github.com/assets/3", "browser_download_url": "https://example.invalid/x?signature=not-a-real-secret"}
    monkeypatch.setattr(transfer, "gh", lambda *args, **kwargs: json.dumps([[row]]))
    with pytest.raises(ValueError, match="stable non-query"):
        transfer.assets(1)


def make_tar(path, names):
    with tarfile.open(path, "w:gz") as opened:
        for name in names:
            body = b"fixture"
            member = tarfile.TarInfo(name)
            member.size = len(body)
            opened.addfile(member, io.BytesIO(body))


def test_safe_fresh_extraction_checks_exact_bytes(tmp_path):
    source = tmp_path / "fixture.tar.gz"
    make_tar(source, ["a/record.json"])
    expected = {"a/record.json": {"bytes": 7, "sha256": hashlib.sha256(b"fixture").hexdigest()}}
    destination = tmp_path / "new"
    observed = verify.extract_members(source, destination, expected)
    assert observed["a/record.json"]["sha256"] == expected["a/record.json"]["sha256"]
    with pytest.raises(ValueError, match="already exists"):
        verify.extract_members(source, destination, expected)


@pytest.mark.parametrize("names", [["../escape"], ["a", "a"], ["unlisted"]])
def test_unsafe_duplicate_unlisted_extraction_rejected(tmp_path, names):
    source = tmp_path / "fixture.tar.gz"
    make_tar(source, names)
    expected = {"a": {"bytes": 7, "sha256": hashlib.sha256(b"fixture").hexdigest()}}
    with pytest.raises(ValueError):
        verify.extract_members(source, tmp_path / "new", expected)
    assert not (tmp_path.parent / "escape").exists()


def test_cleanup_cannot_remove_original_tree(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    with pytest.raises(ValueError, match="restricted"):
        verify.cleanup_new_tree(original, "fabricated test")
    assert original.is_dir()
