"""Shared test setup."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import types

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import power_telemetry  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_host_salt_dir(tmp_path_factory, monkeypatch):
    """run_campaign.main() keeps a per-campaign host-id salt under the user's state directory; tests get a throwaway one."""
    monkeypatch.setenv("CAMPAIGN_HOST_SALT_DIR", str(tmp_path_factory.mktemp("host-salts")))


@pytest.fixture(autouse=True)
def no_host_cpu_sysfs(tmp_path_factory, monkeypatch):
    """The machine running the tests may be bare metal with RAPL (readable or not) and several sockets.

    Tests see an empty sysfs (no powercap, hwmon, CPU topology or NUMA tree) unless they point the module at a tree they built."""
    empty = tmp_path_factory.mktemp("no-sysfs")
    monkeypatch.setattr(power_telemetry, "POWERCAP_ROOT", empty / "powercap")
    monkeypatch.setattr(power_telemetry, "HWMON_ROOT", empty / "hwmon")
    monkeypatch.setattr(power_telemetry, "SYS_CPU_ROOT", empty / "cpu")
    monkeypatch.setattr(power_telemetry, "SYS_NODE_ROOT", empty / "node")


@pytest.fixture
def c3(tmp_path, monkeypatch):
    """The c3 metal's CPU topology as the module's default sysfs, and an affinity mask the tests can inspect."""
    from sysfs_fakes import make_cpu_tree
    cpu_root, node_root = make_cpu_tree(tmp_path / "sys")
    monkeypatch.setattr(power_telemetry, "SYS_CPU_ROOT", cpu_root)
    monkeypatch.setattr(power_telemetry, "SYS_NODE_ROOT", node_root)
    mask = {"cpus": set(range(192)), "set_calls": []}
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: set(mask["cpus"]), raising=False)

    def setaffinity(pid, cpus):
        mask["set_calls"].append(set(cpus))
        mask["cpus"] = set(cpus)
    monkeypatch.setattr(os, "sched_setaffinity", setaffinity, raising=False)
    from sysfs_fakes import FakeNumaApi, Membind
    membind = Membind()
    monkeypatch.setattr(power_telemetry, "NumaPolicyApi", lambda *a, **k: FakeNumaApi(membind))

    def probe(nodes, **kwargs):
        membind.probes.append(sorted(nodes))
        ok = membind.fail is None
        return {"ok": ok, "mechanism": membind.mechanism if ok else None, "numactl": None, "libnuma": None,
                "reasons": [] if ok else [f"OSError: {membind.fail}", "numactl is not installed"]}
    monkeypatch.setattr(power_telemetry, "probe_membind", probe)
    return types.SimpleNamespace(cpu_root=cpu_root, node_root=node_root, mask=mask, membind=membind)
