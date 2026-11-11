"""What new runs publish about the host: a salted host id instead of the hostname in campaign.json, and nvidia-smi -q
stored without the GPU UUID, serial number and PCI bus id in environment.json.

Old trees are never rewritten, and the analyzers read only the facts these changes keep (Product Name, Driver Version,
CUDA Version, Attached GPUs, FB Memory Usage Total, instance_type, cpu_model, platform): the host_shape test proves it.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import sys
import types

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import analyze_results  # noqa: E402
import run_campaign as rc  # noqa: E402
import run_experiment as rx  # noqa: E402

LAMBDA_HOSTNAME = "192-222-55-32"
SMI_Q = """\
==============NVSMI LOG==============

Timestamp                                 : Wed Nov 11 18:02:11 2026
Driver Version                            : 580.105.08
CUDA Version                              : 13.0

Attached GPUs                             : 1
GPU 00000000:08:00.0
    Product Name                          : NVIDIA H100 80GB HBM3
    Serial Number                         : 1654321098765
    GPU UUID                              : GPU-3c2f1a6e-0b7d-9f53-1111-222233334444
    Minor Number                          : 0
    Board ID                              : 0x800
    GPU PDI                               : 0x8a1b2c3d4e5f6071
    MIG Mode
        Current                           : N/A
    PCI
        Bus                               : 0x08
        Device                            : 0x00
        Domain                            : 0x0000
        Device Id                         : 0x233010DE
        Bus Id                            : 00000000:08:00.0
        Sub System Id                     : 0x16C110DE
    FB Memory Usage
        Total                             : 81559 MiB
        Used                              : 1 MiB
"""
IDENTIFIERS = ["1654321098765", "GPU-3c2f1a6e-0b7d-9f53-1111-222233334444", "0x8a1b2c3d4e5f6071", "00000000:08:00.0", "0x800"]
KEPT = ["Driver Version                            : 580.105.08", "CUDA Version                              : 13.0",
        "Attached GPUs                             : 1", "Product Name                          : NVIDIA H100 80GB HBM3",
        "Device Id                         : 0x233010DE", "Sub System Id                     : 0x16C110DE",
        "Total                             : 81559 MiB", "Current                           : N/A", "Minor Number                          : 0"]


# ---- nvidia-smi -q redaction ----------------------------------------------------------------------------

def test_identifiers_are_redacted_and_everything_else_survives():
    text, counts = rx.redact_gpu_identifiers(SMI_Q)
    for identifier in IDENTIFIERS:
        assert identifier not in text
    for line in KEPT:
        assert line in text
    assert counts == {"Serial Number": 1, "GPU UUID": 1, "Board ID": 1, "GPU PDI": 1, "PCI bus": 1, "PCI device": 1,
                      "PCI domain": 1, "Bus Id": 1, "GPU bus id header": 1}
    assert len(text.splitlines()) == len(SMI_Q.splitlines())                       # lines are replaced, never dropped


def test_redaction_is_idempotent_and_leaves_other_text_alone():
    once, _ = rx.redact_gpu_identifiers(SMI_Q)
    assert rx.redact_gpu_identifiers(once) == (once, {})
    assert rx.redact_gpu_identifiers("") == ("", {})
    other = "Architecture:  x86_64\nModel name: Intel(R) Xeon(R) Platinum 8480+\nBus clock: 100 MHz\nDevice drivers : none\n"
    assert rx.redact_gpu_identifiers(other) == (other, {})


def test_not_applicable_values_are_not_counted_as_redactions():
    text = "    Serial Number                         : N/A\n    GPU UUID                              : GPU-abc\n"
    out, counts = rx.redact_gpu_identifiers(text)
    assert "N/A" in out and "GPU-abc" not in out and counts == {"GPU UUID": 1}


def test_the_analysis_reads_the_same_host_facts_from_a_redacted_inventory():
    def environment(smi):
        return {"arguments": {"cpus": 26, "driver_memory": "96g"}, "inventory": [
            {"command": ["lscpu"], "returncode": 0, "output": "CPU(s):  26\nModel name:  Intel(R) Xeon(R) Platinum 8480+\n"},
            {"command": ["free", "-b"], "returncode": 0, "output": "Mem:   241591910400  1  2\n"},
            {"command": ["nvidia-smi", "-q"], "returncode": 0, "output": smi}]}
    raw, redacted = analyze_results.host_shape(environment(SMI_Q)), analyze_results.host_shape(environment(rx.redact_gpu_identifiers(SMI_Q)[0]))
    assert raw == redacted and raw["gpu"] == "NVIDIA H100 80GB HBM3" and raw["gpu_memory_mib"] == 81559
    assert raw["driver_version"] == "580.105.08" and raw["gpu_count"] == 1


# ---- campaign host id -----------------------------------------------------------------------------------

@pytest.fixture
def salt_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CAMPAIGN_HOST_SALT_DIR", str(tmp_path / "state" / "salts"))
    return tmp_path / "state" / "salts"


def config(tmp_path, *flags):
    return rc.resolve_config(rc.parse_args(["--root", str(tmp_path / "campaign"), *flags]))


def test_the_salt_is_created_once_private_and_outside_the_campaign_root(salt_dir, tmp_path):
    root = tmp_path / "campaign"
    first, created = rc.load_or_create_salt(root)
    again, created_again = rc.load_or_create_salt(root)
    assert created and not created_again and first == again and len(first) == 32
    path = rc.host_salt_path(root)
    assert path.parent == salt_dir and root.resolve() not in path.parents
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    other, _ = rc.load_or_create_salt(tmp_path / "another-campaign")
    assert other != first                                                              # one salt per campaign root


def test_host_identity_has_a_salted_hash_and_no_hostname_by_default(salt_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(rc.platform, "node", lambda: LAMBDA_HOSTNAME)
    cfg = config(tmp_path)
    cfg.host_salt, _ = rc.load_or_create_salt(cfg.root)
    identity = rc.host_identity(cfg)
    assert "hostname" not in identity and len(identity["host_id_sha256"]) == 64
    assert LAMBDA_HOSTNAME not in json.dumps(identity)
    assert identity["host_id_sha256"] == rc.host_id(cfg.host_salt)
    assert identity["host_id_sha256"] != hashlib.sha256(LAMBDA_HOSTNAME.encode()).hexdigest()    # not a guessable plain hash
    assert rc.host_id(b"another salt") != identity["host_id_sha256"]                    # unlinkable across campaigns
    assert {"instance_type", "platform", "cpus", "cpu_model", "memory_total_bytes", "gpu"} <= set(identity)   # facts the analysis reads


def test_the_raw_hostname_is_opt_in(salt_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(rc.platform, "node", lambda: LAMBDA_HOSTNAME)
    assert config(tmp_path, "--include-hostname").include_hostname is True
    assert config(tmp_path).include_hostname is False
    cfg = config(tmp_path, "--include-hostname")
    assert rc.host_identity(cfg)["hostname"] == LAMBDA_HOSTNAME and "host_id_sha256" not in rc.host_identity(cfg)   # no salt loaded yet


PROV = {"design_doc_sha256": "a" * 64, "git": {"commit": None}, "source_sha256": {"run_campaign.py": "b" * 64},
        "inputs": {"manifest_sha256": {}, "inputs_reports_sha256": {}, "rapids_jar_sha256": None}}


def start_file(tmp_path, host_id):
    cfg = config(tmp_path)
    cfg.root.mkdir(exist_ok=True)
    prov = {**PROV, "host": ({"host_id_sha256": host_id} if host_id else {"gpu": "x"})}
    rc.start_campaign_file(cfg, {"format": 1}, prov, types.SimpleNamespace(accept_design_doc_change=False))
    return [json.loads(line) for line in (cfg.root / "invocations.jsonl").read_text().splitlines()]


def test_resuming_on_another_host_is_reported_as_drift(tmp_path):
    start_file(tmp_path, "1" * 64)
    same = start_file(tmp_path, "1" * 64)
    assert same[1]["drift"] == [] and same[1]["host_id_sha256"] == "1" * 64
    other = start_file(tmp_path, "2" * 64)
    assert other[2]["drift"] == [{"host_id_differs_from_campaign_json": {"salt_created_this_invocation": False}}]
    assert json.loads((tmp_path / "campaign" / "campaign.json").read_text())["provenance"]["host"]["host_id_sha256"] == "1" * 64


def test_a_campaign_started_before_the_host_id_existed_is_not_flagged(tmp_path):
    start_file(tmp_path, None)
    later = start_file(tmp_path, "3" * 64)
    assert later[1]["drift"] == []


def test_main_creates_the_salt_and_campaign_json_carries_no_hostname(salt_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(rc.platform, "node", lambda: LAMBDA_HOSTNAME)
    monkeypatch.setattr(rc, "startup_checks", lambda *a, **k: None)
    monkeypatch.setattr(rc.Campaign, "run", lambda self: "complete")
    handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    argv = ["--root", str(tmp_path / "campaign"), "--data-root", str(tmp_path / "data"), "--jar", str(tmp_path / "x.jar"),
            "--rounds", "1", "--engines-override", "polars", "--scales-override", "1", "--no-lease-check"]
    try:
        assert rc.main(argv) == 0
        assert rc.main(argv) == 0                                                      # a resume on the same host
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    root = tmp_path / "campaign"
    text = (root / "campaign.json").read_text()
    host = json.loads(text)["provenance"]["host"]
    assert LAMBDA_HOSTNAME not in text and "hostname" not in host and len(host["host_id_sha256"]) == 64
    assert LAMBDA_HOSTNAME not in (root / "invocations.jsonl").read_text()
    assert [json.loads(line)["drift"] for line in (root / "invocations.jsonl").read_text().splitlines()] == [[], []]
    assert not [p for p in root.rglob("*") if p.is_file() and p.read_bytes().find(rc.host_salt_path(root).read_text().strip().encode()) >= 0]
    assert len(list(salt_dir.iterdir())) == 1
    rc.host_salt_path(root).unlink()                                                   # the original host's salt is gone
    try:
        assert rc.main(argv) == 0
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    assert json.loads((root / "invocations.jsonl").read_text().splitlines()[2])["drift"] == [
        {"host_id_differs_from_campaign_json": {"salt_created_this_invocation": True}}]


def test_fabric_cluster_and_chassis_identifiers_are_redacted_too():
    text = ("    Platform Info\n        Chassis Serial Number             : 1320923012345\n        Slot Number                       : 3\n"
            "    Fabric\n        State                             : Completed\n"
            "        CliqueId                          : 32766\n"
            "        ClusterUUID                       : 5f3c2a10-aaaa-bbbb-cccc-0123456789ab\n")
    out, counts = rx.redact_gpu_identifiers(text)
    assert "1320923012345" not in out and "5f3c2a10" not in out
    assert counts == {"Chassis Serial Number": 1, "ClusterUUID": 1}
    assert "Slot Number                       : 3" in out and "CliqueId                          : 32766" in out
