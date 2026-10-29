"""Fabricated byte envelopes; no measured evidence or network operation."""
import hashlib

import pytest

from operations import h100_private_transport_v2 as transport


def test_gzip_envelope_is_deterministic_and_restores_exact_original(tmp_path):
    source = tmp_path / "original.tar"
    body = b"fabricated raw tar byte fixture\n" * 100
    source.write_bytes(body)
    expected = {"path": "original.tar", "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    first = transport.compress_envelope(source, tmp_path / "first.gz", expected, tmp_path)
    second = transport.compress_envelope(source, tmp_path / "second.gz", expected, tmp_path)
    assert first["wire_gzip"]["sha256"] == second["wire_gzip"]["sha256"]
    assert first["decoded_verification_sha256"] == expected["sha256"]
    assert first["decoded_verification_bytes"] == len(body)
    assert source.read_bytes() == body
    with pytest.raises(FileExistsError):
        transport.compress_envelope(source, tmp_path / "first.gz", expected, tmp_path)


def test_wrong_original_identity_cannot_get_successful_envelope(tmp_path):
    source = tmp_path / "original.tar"
    source.write_bytes(b"actual fixture")
    with pytest.raises(ValueError, match="historical control"):
        transport.compress_envelope(source, tmp_path / "bad.gz", {"bytes": 14, "sha256": "0" * 64}, tmp_path)
