"""Design 3.1: the hypervisor and the storage type are recorded with the host in campaign.json (Paper 3 campaigns only)."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import run_campaign as rc  # noqa: E402


def fake_run_text(table):
    def run(command, timeout=120):
        for prefix, answer in table.items():
            if tuple(command[:len(prefix)]) == prefix:
                return answer
        return None, "FileNotFoundError: no such command"
    return run


def test_systemd_detect_virt_answers_are_recorded_even_when_it_exits_1_for_none(monkeypatch):
    for answer, expected in (((0, "kvm\n"), "kvm"), ((0, "google\n"), "google"), ((1, "none\n"), "none"), ((None, "FileNotFoundError: x"), None), ((1, "Failed to connect to bus: no\n"), None)):
        monkeypatch.setattr(rc, "run_text", fake_run_text({("systemd-detect-virt",): answer}))
        assert rc.detect_virt() == expected, answer


def test_the_storage_under_the_data_root_is_the_parent_disk_without_a_serial(monkeypatch):
    seen = []
    table = {("findmnt",): (0, "/dev/nvme0n1p1 ext4\n"), ("lsblk", "-n", "-o", "PKNAME"): (0, "nvme0n1\n"),
             ("lsblk", "-d"): (0, 'NAME="nvme0n1" ROTA="0" TYPE="disk" TRAN="nvme" MODEL="nvme_card-pd"\n')}
    run = fake_run_text(table)
    monkeypatch.setattr(rc, "run_text", lambda command, timeout=120: (seen.append(command), run(command, timeout))[1])
    storage = rc.storage_identity("/data")
    assert storage == {"data_root_source": "/dev/nvme0n1p1", "data_root_fstype": "ext4", "device": "nvme0n1", "rotational": False, "type": "disk",
                       "model": "nvme_card-pd", "transport": "nvme"}
    assert not any("SERIAL" in word.upper() for command in seen for word in command)       # no serial number is ever requested
    assert ["findmnt", "-n", "-T", "/data", "-o", "SOURCE,FSTYPE"] in seen


def test_a_host_that_cannot_answer_gives_none_not_an_error(monkeypatch):
    monkeypatch.setattr(rc, "run_text", fake_run_text({}))
    assert rc.storage_identity("/data") == {"data_root_source": None, "data_root_fstype": None, "device": None, "rotational": None, "type": None,
                                           "model": None, "transport": None}
    assert rc.storage_identity(None)["device"] is None


def test_only_paper_3_campaigns_record_them(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "run_text", fake_run_text({("systemd-detect-virt",): (0, "kvm\n"), ("findmnt",): (0, "/dev/sda1 ext4\n"), ("lsblk",): (0, 'NAME="sda" ROTA="1" TYPE="disk" TRAN="" MODEL="Some SSD"\n')}))
    paper1 = rc.resolve_config(rc.parse_args(["--root", str(tmp_path / "c1")]))
    assert "hypervisor" not in rc.host_identity(paper1) and "storage" not in rc.host_identity(paper1)
    paper3 = rc.resolve_config(rc.parse_args(["--root", str(tmp_path / "c3"), "--data-root", str(tmp_path), "--paper3-host", "h100", "--paper3-launch", "1",
                                              "--paper3-lock-date", "20261116", "--no-lease-check"]))
    identity = rc.host_identity(paper3)
    assert identity["hypervisor"] == "kvm" and identity["storage"]["data_root_source"] == "/dev/sda1" and identity["storage"]["rotational"] is True
    assert identity["storage"]["transport"] is None and identity["storage"]["model"] == "Some SSD"          # an empty column stays empty, not shifted
    assert "serial" not in json.dumps(identity).lower()
