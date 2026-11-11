"""Fake sysfs for the CPU-only bare-metal tests: laid out like the GCP c3-highmem-192-metal probe
(docs/paper3-evidence/probes/gcp-c3-highmem-192-metal-20261111T150427Z.json: 2 sockets x 48 cores x 2 threads, sub-NUMA
clustering = 4 NUMA nodes, RAPL package-0/1 + dram, root-only energy_uj)."""
from __future__ import annotations

from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import power_telemetry as pt  # noqa: E402

C3_SOCKET_0 = "0-47,96-143"
C3_SOCKET_1 = "48-95,144-191"


def make_cpu_tree(root: Path, sockets=2, cores=48, threads=2, nodes_per_socket=2):
    """(cpu_root, node_root) numbered like the c3 metal: cpu = thread * all_cores + socket * cores + core."""
    cpu_root, node_root = root / "cpu", root / "node"
    cpu_root.mkdir(parents=True)
    (cpu_root / "cpufreq").mkdir()                                  # not a cpuN directory: ignored
    nodes = {}
    for thread in range(threads):
        for socket in range(sockets):
            for core in range(cores):
                cpu = thread * sockets * cores + socket * cores + core
                topology = cpu_root / f"cpu{cpu}" / "topology"
                topology.mkdir(parents=True)
                (topology / "physical_package_id").write_text(f"{socket}\n")
                (topology / "core_id").write_text(f"{core}\n")
                nodes.setdefault(socket * nodes_per_socket + core // (cores // nodes_per_socket), []).append(cpu)
    for node, cpus in nodes.items():
        (node_root / f"node{node}").mkdir(parents=True)
        (node_root / f"node{node}" / "cpulist").write_text(pt.format_cpu_list(cpus) + "\n")
    return cpu_root, node_root


def make_zone(root: Path, zone: str, name: str, energy: int = 1_000_000, max_range: int = 262143328850) -> None:
    directory = root / zone
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "name").write_text(name + "\n")
    (directory / "energy_uj").write_text(f"{energy}\n")
    (directory / "max_energy_range_uj").write_text(f"{max_range}\n")


def c3_powercap(root: Path) -> Path:
    """What the probe saw: package-0 with its dram, package-1 with its dram (no core sub-zones, no MMIO copy)."""
    for socket in (0, 1):
        make_zone(root, f"intel-rapl:{socket}", f"package-{socket}", 5_000_000 * (socket + 1))
        make_zone(root, f"intel-rapl:{socket}:0", "dram", 700_000 * (socket + 1))
    return root


def bump(tree: Path, step: int = 50_000) -> None:
    for counter in Path(tree).glob("intel-rapl*/energy_uj"):
        temporary = counter.with_name("energy_uj.tmp")        # replaced atomically: a reader never sees a truncated file
        temporary.write_text(f"{int(counter.read_text()) + step}\n")
        temporary.replace(counter)


def ticking_sleep(tree: Path):
    """A sleep() that advances every counter, as running hardware does during the liveness wait."""
    return lambda seconds: bump(tree)


def deny(monkeypatch, *paths: Path):
    """read_int() raises PermissionError for these energy files (a root-only RAPL tree, without needing a non-root user)."""
    real = pt.read_int

    def read_int(path):
        if Path(path) in paths:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path)
    monkeypatch.setattr(pt, "read_int", read_int)


class Membind:
    """What the fake NUMA policy saw: every set_membind() call, the policy now in force, and a failure to inject."""

    def __init__(self):
        self.calls, self.policy, self.fail, self.mechanism = [], (0, []), None, "fake set_mempolicy"
        self.probes = []


class FakeNumaApi:
    """NumaPolicyApi stand-in (the test machine may have neither libnuma nor Linux): binds are recorded and read back."""

    def __init__(self, state: Membind):
        self.state, self.reasons = state, []

    @property
    def mechanism(self):
        return self.state.mechanism

    @property
    def usable(self):
        return self.state.fail is None

    def set_membind(self, nodes):
        self.state.calls.append(sorted(nodes))
        if self.state.fail is not None:
            raise OSError(1, self.state.fail)
        self.state.policy = (pt.MPOL_BIND, sorted(nodes))

    def get_policy(self):
        return self.state.policy
