"""probe_power.py: the read-only power probe, driven by fake sysfs/procfs trees.

No hardware, root, network or external command is needed: collectors read a tree built in tmp_path
(`--root`), and command-backed sections are tested by monkeypatching `probe_power.run_cmd`/`which`.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
import probe_power as probe  # noqa: E402

TOP_LEVEL_KEYS = ["schema", "probe", "host", "cpu", "virtualization", "powercap", "hwmon", "perf", "msr",
                  "turbostat", "ipmi", "gpu", "nvml", "clock", "lease", "counter_sanity", "summary", "errors"]
MAX_RANGE = 262143328850


def put(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def build_tree(root: Path, *, vendor="GenuineIntel", model="Intel(R) Xeon(R) Platinum 8480+", sockets=2, cores=2,
               threads=2, hypervisor=False, rapl=True, mem_kb=16 * 1048576) -> Path:
    """x86 host: `sockets` x `cores` x `threads` logical CPUs, optional hypervisor flag and RAPL zones."""
    flags = "fpu vme sse4_2 constant_tsc aperfmperf" + (" hypervisor" if hypervisor else "")
    blocks = []
    per_socket = cores * threads
    for cpu in range(sockets * per_socket):
        socket, core = cpu // per_socket, (cpu // threads) % cores
        blocks.append(f"processor\t: {cpu}\nvendor_id\t: {vendor}\ncpu family\t: 6\nmodel\t\t: 143\n"
                      f"model name\t: {model}\nstepping\t: 8\nmicrocode\t: 0x2b000\nflags\t\t: {flags}\n"
                      f"physical id\t: {socket}\ncore id\t\t: {core}\n")
        topo = root / f"sys/devices/system/cpu/cpu{cpu}/topology"
        put(topo / "physical_package_id", f"{socket}\n")
        put(topo / "core_id", f"{core}\n")
    put(root / "proc/cpuinfo", "\n".join(blocks))
    put(root / "proc/meminfo", f"MemTotal:       {mem_kb} kB\nMemFree:        1 kB\n")
    put(root / "sys/devices/system/cpu/smt/active", "1\n" if threads > 1 else "0\n")
    put(root / "sys/devices/system/node/node0/cpulist", f"0-{sockets * per_socket - 1}\n")
    put(root / "etc/os-release", 'PRETTY_NAME="Ubuntu 22.04.5 LTS"\nID=ubuntu\n')
    put(root / "sys/class/dmi/id/sys_vendor", "Test Vendor\n")
    put(root / "sys/class/dmi/id/product_name", "Test Product\n")
    put(root / "proc/sys/kernel/perf_event_paranoid", "2\n")
    if rapl:
        for socket in range(sockets):
            zone = root / f"sys/class/powercap/intel-rapl:{socket}"
            put(zone / "name", f"package-{socket}\n")
            put(zone / "energy_uj", f"{1000000 * (socket + 1)}\n")
            put(zone / "max_energy_range_uj", f"{MAX_RANGE}\n")
            put(zone / "enabled", "1\n")
            put(zone / "constraint_0_name", "long_term\n")
            put(zone / "constraint_0_power_limit_uw", "350000000\n")
            put(zone / "constraint_0_time_window_us", "28000000\n")
            put(zone / "constraint_0_max_power_uw", "350000000\n")
            sub = root / f"sys/class/powercap/intel-rapl:{socket}:0"
            put(sub / "name", "dram\n")
            put(sub / "energy_uj", "5000000\n")
            put(sub / "max_energy_range_uj", f"{MAX_RANGE}\n")
    return root


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(str(path.relative_to(root)).encode())
        if path.is_file():
            digest.update(path.read_bytes())
            digest.update(str(path.stat().st_mtime_ns).encode())
    return digest.hexdigest()


def run_main(root: Path, *extra: str, load_fn=None, capsys=None):
    args = ["--root", str(root), "--idle-seconds", "0.01", "--busy-seconds", "0.01", *extra]
    kwargs = {"sleep": lambda seconds: None}
    if load_fn is not None:
        kwargs["load_fn"] = load_fn
    else:
        kwargs["load_fn"] = lambda ctx, seconds: 0
    assert probe.main(args, **kwargs) == 0
    return json.loads(capsys.readouterr().out)


def ctx_for(root: Path, *, commands=False, euid=1000) -> probe.Ctx:
    ctx = probe.Ctx(root=str(root), commands=commands, sleep=lambda seconds: None)
    ctx.euid = euid
    return ctx


def fake_commands(monkeypatch, handler):
    """Make every command 'exist' and answer through handler(argv) -> dict(rc=, stdout=, stderr=)."""
    monkeypatch.setattr(probe, "which", lambda ctx, name: "/fake/" + name)

    def run(ctx, argv, timeout=15, sudo=False):
        result = {"argv": list(argv)}
        if sudo and ctx.euid != 0:
            result["sudo"] = True
        answer = handler(argv)
        if answer is None:
            result["error"] = "not found: " + argv[0]
        else:
            result.update({"rc": 0, "stdout": "", "stderr": ""})
            result.update(answer)
        return result

    monkeypatch.setattr(probe, "run_cmd", run)


# ---- cpu ----------------------------------------------------------------------------------------

def test_cpu_topology_sockets_cores_smt(tmp_path):
    build_tree(tmp_path, sockets=2, cores=3, threads=2)
    cpu = probe.collect_cpu(ctx_for(tmp_path))
    assert (cpu["sockets"], cpu["physical_cores"], cpu["threads_per_core"], cpu["logical_cpus"]) == (2, 6, 2, 12)
    assert cpu["smt_active"] is True
    assert cpu["model_name"] == "Intel(R) Xeon(R) Platinum 8480+" and cpu["vendor"] == "GenuineIntel"
    assert cpu["mem_total_gib"] == 16.0
    assert cpu["hypervisor_flag"] is False
    assert "constant_tsc" in cpu["flags_of_interest"] and "aperfmperf" in cpu["flags_of_interest"]
    assert cpu["numa_nodes"] == {"node0": "0-11"}


def test_cpu_without_smt_and_with_hypervisor_flag(tmp_path):
    build_tree(tmp_path, sockets=1, cores=4, threads=1, hypervisor=True)
    cpu = probe.collect_cpu(ctx_for(tmp_path))
    assert (cpu["sockets"], cpu["physical_cores"], cpu["threads_per_core"], cpu["smt_active"]) == (1, 4, 1, False)
    assert cpu["hypervisor_flag"] is True


def test_cpu_aarch64_has_no_model_name_or_hypervisor_flag(tmp_path):
    put(tmp_path / "proc/cpuinfo", "processor\t: 0\nBogoMIPS\t: 2000.00\nFeatures\t: fp asimd\nCPU implementer\t: 0x41\nCPU part\t: 0xd4f\n")
    cpu = probe.collect_cpu(ctx_for(tmp_path))
    assert cpu["model_name"] is None and cpu["hypervisor_flag"] is None
    assert cpu["vendor"] == "0x41" and cpu["logical_cpus"] == 1 and cpu["sockets"] is None


def test_cpu_offline_cpus_are_not_counted(tmp_path):
    build_tree(tmp_path, sockets=1, cores=2, threads=2)
    for name in ("physical_package_id", "core_id"):
        (tmp_path / f"sys/devices/system/cpu/cpu3/topology/{name}").unlink()
    assert probe.collect_cpu(ctx_for(tmp_path))["logical_cpus"] == 3


def test_cpu_model_placeholder_falls_back_to_lscpu(tmp_path, monkeypatch):
    put(tmp_path / "proc/cpuinfo", "processor\t: 0\nmodel name\t: -\nCPU implementer\t: 0x61\n")
    ctx = ctx_for(tmp_path, commands=True)
    fake_commands(monkeypatch, lambda argv: {"stdout": "Architecture:  aarch64\nModel name:    Neoverse-V2\n"} if argv[0] == "lscpu" else None)
    assert probe.collect_cpu(ctx)["model_name"] == "Neoverse-V2"


# ---- virtualization -----------------------------------------------------------------------------

@pytest.mark.parametrize("hypervisor,detected,verdict", [
    (True, "kvm", "virtualized"),
    (True, None, "virtualized"),
    (False, "none", "bare_metal"),
    (False, None, "bare_metal"),
    (False, "amazon", "ambiguous"),       # EC2 .metal: DMI says Amazon, CPUID shows no hypervisor
    (None, "none", "bare_metal"),         # aarch64: no CPUID flag to consult
    (None, "kvm", "virtualized"),
    (None, None, "unknown"),
])
def test_virtualization_verdict(tmp_path, monkeypatch, hypervisor, detected, verdict):
    build_tree(tmp_path)
    cpu = {"hypervisor_flag": hypervisor}
    ctx = ctx_for(tmp_path, commands=True)
    fake_commands(monkeypatch, lambda argv: ({"rc": 0 if detected != "none" else 1, "stdout": detected + "\n"}
                                             if argv[0] == "systemd-detect-virt" and detected else
                                             (None if argv[0] == "systemd-detect-virt" else {"rc": 1})))
    section = probe.collect_virtualization(ctx, cpu)
    assert section["verdict"] == verdict
    assert section["dmi"]["sys_vendor"] == "Test Vendor"


def test_lscpu_hypervisor_vendor_marks_virtualized(tmp_path, monkeypatch):
    build_tree(tmp_path)
    ctx = ctx_for(tmp_path, commands=True)
    fake_commands(monkeypatch, lambda argv: {"stdout": "Hypervisor vendor:  KVM\nVirtualization type:  full\n"} if argv[0] == "lscpu" else None)
    section = probe.collect_virtualization(ctx, {"hypervisor_flag": None})
    assert section["verdict"] == "virtualized" and section["lscpu_hypervisor_vendor"] == "KVM"
    assert any("lscpu hypervisor vendor: KVM" in item for item in section["evidence"])


def test_no_hypervisor_flag_without_dmi_or_detect_virt_is_unknown(tmp_path):
    build_tree(tmp_path)
    (tmp_path / "sys/class/dmi/id/sys_vendor").unlink()
    section = probe.collect_virtualization(ctx_for(tmp_path), {"hypervisor_flag": False})
    assert section["verdict"] == "unknown" and any("cannot tell bare metal from emulation" in e for e in section["evidence"])


def test_sys_hypervisor_type_marks_virtualized(tmp_path):
    build_tree(tmp_path)
    put(tmp_path / "sys/hypervisor/type", "xen\n")
    assert probe.collect_virtualization(ctx_for(tmp_path), {"hypervisor_flag": False})["verdict"] == "virtualized"


DMIDECODE = """# dmidecode 3.3
Handle 0x0100, DMI type 1, 27 bytes
System Information
\tManufacturer: Acme
\tProduct Name: Box
\tSerial Number: SECRET-SERIAL
\tUUID: 11111111-2222-3333-4444-555555555555

Handle 0x0400, DMI type 4, 48 bytes
Processor Information
\tSocket Designation: CPU0
\tVersion: Intel(R) Xeon(R) Platinum 8480+
\tMax Speed: 4000 MHz
\tCurrent Speed: 2000 MHz
\tSerial Number: SECRET-CPU
\tCore Count: 56
\tCore Enabled: 56
\tThread Count: 112

Handle 0x0500, DMI type 4, 48 bytes
Processor Information
\tSocket Designation: CPU1
\tCore Count: 56
\tThread Count: 112

Handle 0x2600, DMI type 38, 18 bytes
IPMI Device Information
\tInterface Type: KCS (Keyboard Control Style)
"""


def test_dmidecode_is_parsed_and_redacted(tmp_path, monkeypatch):
    build_tree(tmp_path)
    ctx = ctx_for(tmp_path, commands=True)
    ctx._sudo_mode = "passwordless"
    seen = {}

    def handler(argv):
        if argv[0] == "dmidecode":
            seen["argv"] = argv
            return {"stdout": DMIDECODE}
        return None

    fake_commands(monkeypatch, handler)
    section = probe.collect_virtualization(ctx, {"hypervisor_flag": False})["dmidecode"]
    assert [p["socket"] for p in section["processors"]] == ["CPU0", "CPU1"]
    assert section["processors"][0]["core_count"] == "56" and section["ipmi_device_record"] is True
    assert "SECRET" not in json.dumps(section)


def test_dmidecode_skipped_without_root_or_sudo(tmp_path):
    build_tree(tmp_path)
    ctx = ctx_for(tmp_path, commands=True)
    ctx._sudo_mode = "unavailable"
    ctx.commands = True
    section = probe.collect_virtualization(ctx, {"hypervisor_flag": False})
    assert "skipped" in section["dmidecode"] or "error" in section["dmidecode"]


# ---- powercap / RAPL ----------------------------------------------------------------------------

def test_powercap_domains_and_limits(tmp_path):
    build_tree(tmp_path, sockets=2)
    section = probe.collect_powercap(ctx_for(tmp_path))
    assert section["package_zones"] == 2 and section["domains"] == ["dram", "package-0", "package-1"]
    zone = next(z for z in section["zones"] if z["id"] == "intel-rapl:0")
    assert zone["depth"] == 0 and zone["name"] == "package-0" and zone["max_energy_range_uj"] == MAX_RANGE
    assert zone["constraints"] == [{"name": "long_term", "power_limit_uw": 350000000, "time_window_us": 28000000,
                                    "max_power_uw": 350000000}]
    sub = next(z for z in section["zones"] if z["id"] == "intel-rapl:0:0")
    assert sub["depth"] == 1 and sub["name"] == "dram"
    assert section["all_packages_readable_as_user"] is True
    assert zone["energy_uj"]["readable_as_user"] is True and zone["energy_uj"]["sample"] == "1000000"
    assert zone["energy_uj"]["mode"].startswith("06") or zone["energy_uj"]["mode"].startswith("04")


def test_powercap_absent_on_a_vm(tmp_path):
    build_tree(tmp_path, hypervisor=True, rapl=False)
    section = probe.collect_powercap(ctx_for(tmp_path))
    assert section["sysfs_present"] is False and section["zones"] == [] and section["package_zones"] == 0
    assert section["all_packages_readable_as_user"] is False


def test_energy_uj_unreadable_as_user_but_readable_as_root(tmp_path, monkeypatch):
    build_tree(tmp_path, sockets=1)
    denied = str(tmp_path / "sys/class/powercap/intel-rapl:0/energy_uj")
    real = probe.read_file

    def read_file(path):
        if path == denied:
            raise PermissionError(13, "Permission denied")
        return real(path)

    monkeypatch.setattr(probe, "read_file", read_file)
    ctx = ctx_for(tmp_path, commands=True)
    ctx._sudo_mode = "passwordless"
    fake_commands(monkeypatch, lambda argv: {"stdout": "1234\n"} if argv[0] == "cat" else None)
    zone = probe.collect_powercap(ctx)["zones"][0]["energy_uj"]
    assert zone["readable_as_user"] is False and zone["error"] == "EACCES" and zone["readable_as_root"] is True
    assert any(s["via_sudo"] and s["path"] == denied for s in ctx.sources)


def test_energy_uj_unreadable_for_everyone(tmp_path, monkeypatch):
    build_tree(tmp_path, sockets=1)
    denied = str(tmp_path / "sys/class/powercap/intel-rapl:0/energy_uj")
    real = probe.read_file
    monkeypatch.setattr(probe, "read_file",
                        lambda path: (_ for _ in ()).throw(PermissionError(13, "denied")) if path == denied else real(path))
    ctx = ctx_for(tmp_path, commands=True)
    ctx._sudo_mode = "unavailable"
    fake_commands(monkeypatch, lambda argv: None)
    zone = probe.collect_powercap(ctx)["zones"][0]["energy_uj"]
    assert zone["readable_as_user"] is False
    assert zone["readable_as_root"] is None            # could not ask
    assert not any(s["path"] == denied for s in ctx.sources)


def test_root_user_reads_are_root_reads(tmp_path):
    build_tree(tmp_path, sockets=1)
    zone = probe.collect_powercap(ctx_for(tmp_path, euid=0))["zones"][0]["energy_uj"]
    assert zone["readable_as_user"] is True and zone["readable_as_root"] is True


def test_mmio_zones_are_not_counted_as_packages(tmp_path):
    build_tree(tmp_path, sockets=1)
    mmio = tmp_path / "sys/class/powercap/intel-rapl-mmio:0"
    put(mmio / "name", "package-0\n")
    put(mmio / "energy_uj", "42\n")
    section = probe.collect_powercap(ctx_for(tmp_path))
    assert {z["control_type"] for z in section["zones"]} == {"intel-rapl", "intel-rapl-mmio"}
    assert section["package_zones"] == 1
    report = full_report(tmp_path)
    assert [c for c in report["summary"]["cpu_energy"]["candidates"] if c["source"] == "powercap"][0]["present"] is True


def full_report(tmp_path, *, load_fn=None, capsys=None):
    ctx = ctx_for(tmp_path)
    report = {"cpu": probe.collect_cpu(ctx)}
    report["virtualization"] = probe.collect_virtualization(ctx, report["cpu"])
    report["powercap"] = probe.collect_powercap(ctx)
    report["hwmon"] = probe.collect_hwmon(ctx)
    report["perf"] = probe.collect_perf(ctx)
    report["msr"] = probe.collect_msr(ctx, report["cpu"])
    report["gpu"] = {"present": False}
    report["ipmi"] = {}
    report["clock"] = {}
    report["counter_sanity"] = probe.collect_counter_sanity(ctx, 0.01, 0.01, load_fn or (lambda c, s: 0))
    return probe.summarize(report) and {**report, "summary": probe.summarize(report)}


# ---- counter sanity -----------------------------------------------------------------------------

def bump(tmp_path: Path, delta: int):
    def load(ctx, seconds):
        for source in ctx.sources:
            path = Path(source["path"])
            path.write_text(f"{int(path.read_text()) + delta}\n")
        return 4
    return load


def test_counters_advance_under_load(tmp_path):
    build_tree(tmp_path, sockets=1)
    ctx = ctx_for(tmp_path)
    probe.collect_powercap(ctx)
    section = probe.collect_counter_sanity(ctx, 0.02, 0.02, bump(tmp_path, 5_000_000))
    assert section["load_workers"] == 4
    by_id = {s["id"]: s for s in section["sources"]}
    package = by_id["powercap:intel-rapl:0:package-0"]
    assert package["advanced"] is True and package["read_via"] == "direct"
    assert package["idle"]["delta_native"] == 0 and package["busy"]["delta_native"] == 5_000_000
    assert package["busy"]["delta_j"] == 5.0 and package["busy"]["avg_w"] > 0


def test_stuck_counters_are_reported_and_noted(tmp_path):
    build_tree(tmp_path, sockets=1)
    ctx = ctx_for(tmp_path)
    probe.collect_powercap(ctx)
    section = probe.collect_counter_sanity(ctx, 0.01, 0.01, lambda c, s: 1)
    assert all(s["advanced"] is False for s in section["sources"])
    summary = full_report(tmp_path)["summary"]
    assert summary["cpu_energy"]["best_source"] == "powercap" and summary["cpu_energy"]["counters_advance"] is False
    assert any("did not advance" in note for note in summary["notes"])


def test_counter_wraparound_is_unwrapped(tmp_path):
    build_tree(tmp_path, sockets=1)
    zone = tmp_path / "sys/class/powercap/intel-rapl:0/energy_uj"
    zone.write_text(f"{MAX_RANGE - 1_000_000}\n")
    ctx = ctx_for(tmp_path)
    probe.collect_powercap(ctx)

    def wrap(c, seconds):
        zone.write_text("2000000\n")
        return 1

    section = probe.collect_counter_sanity(ctx, 0.01, 0.01, wrap)
    package = next(s for s in section["sources"] if s["id"].endswith("package-0"))
    assert package["busy"]["delta_native"] == 3_000_000


def test_no_sources_is_explained(tmp_path):
    build_tree(tmp_path, hypervisor=True, rapl=False)
    section = probe.collect_counter_sanity(ctx_for(tmp_path), 0.01, 0.01, lambda c, s: 1)
    assert section["sources"] == [] and "no readable energy counter" in section["note"]


def test_idle_only_run_has_no_busy_window(tmp_path):
    build_tree(tmp_path, sockets=1)
    ctx = ctx_for(tmp_path)
    probe.collect_powercap(ctx)
    section = probe.collect_counter_sanity(ctx, 0.01, 0.0, lambda c, s: pytest.fail("load must not run"))
    assert all(s["busy"] is None for s in section["sources"])


def test_busy_load_starts_and_reaps_workers(monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    monkeypatch.delattr(os, "sched_getaffinity", raising=False)
    assert probe.busy_load(probe.Ctx(), 0.2) == 2


# ---- hwmon, perf, msr ---------------------------------------------------------------------------

def test_hwmon_amd_energy_is_a_cpu_energy_source(tmp_path):
    build_tree(tmp_path, vendor="AuthenticAMD", model="AMD EPYC 9R45", rapl=False)
    hw = tmp_path / "sys/class/hwmon/hwmon3"
    put(hw / "name", "amd_energy\n")
    put(hw / "energy1_input", "1000000\n")
    put(hw / "energy1_label", "Esocket0\n")
    put(tmp_path / "sys/class/hwmon/hwmon0/name", "nvme\n")
    ctx = ctx_for(tmp_path)
    section = probe.collect_hwmon(ctx)
    assert section["all_names"] == ["nvme", "amd_energy"]
    (device,) = section["devices_with_energy_or_power"]
    assert device["energy"] == [{"file": "energy1_input", "label": "Esocket0", "readable_as_user": True, "value": "1000000"}]
    assert ctx.sources[0]["kind"] == "hwmon"
    summary = full_report(tmp_path)["summary"]
    assert summary["cpu_energy"]["best_source"] == "hwmon" and summary["cpu_energy"]["readable_as_user"] is True


def test_perf_power_pmu_events(tmp_path):
    build_tree(tmp_path)
    events = tmp_path / "sys/bus/event_source/devices/power/events"
    put(events / "energy-pkg", "event=0x02\n")
    put(events / "energy-pkg.scale", "2.3283064365386962890625e-10\n")
    put(events / "energy-pkg.unit", "Joules\n")
    put(events / "energy-ram", "event=0x03\n")
    put(tmp_path / "sys/bus/event_source/devices/cpu/type", "4\n")
    section = probe.collect_perf(ctx_for(tmp_path))
    assert section["power_pmu_present"] is True and section["cpu_pmu_present"] is True
    assert section["perf_event_paranoid"] == 2 and section["perf_binary"] is None
    assert section["power_events"]["energy-pkg"] == {"config": "event=0x02", "scale": "2.3283064365386962890625e-10", "unit": "Joules"}
    assert sorted(section["power_events"]) == ["energy-pkg", "energy-ram"]


def test_arm_pmu_counts_as_a_cpu_pmu(tmp_path):
    put(tmp_path / "sys/bus/event_source/devices/armv8_pmuv3_0/type", "8\n")
    put(tmp_path / "sys/bus/event_source/devices/software/type", "1\n")
    section = probe.collect_perf(ctx_for(tmp_path))
    assert section["cpu_pmu_present"] is True and section["power_pmu_present"] is False


def test_software_only_pmus_are_not_a_cpu_pmu(tmp_path):
    for name in ("software", "tracepoint", "breakpoint"):
        put(tmp_path / f"sys/bus/event_source/devices/{name}/type", "1\n")
    assert probe.collect_perf(ctx_for(tmp_path))["cpu_pmu_present"] is False


def test_perf_stat_output_is_parsed(tmp_path, monkeypatch):
    build_tree(tmp_path)
    put(tmp_path / "sys/bus/event_source/devices/power/events/energy-pkg", "event=0x02\n")
    ctx = ctx_for(tmp_path, commands=True)
    ctx._sudo_mode = "unavailable"
    fake_commands(monkeypatch, lambda argv: {"rc": 0, "stderr": "12.50,Joules,power/energy-pkg/,1000000000,100.00,,\n"})
    section = probe.collect_perf(ctx)
    assert section["perf_stat_as_user"]["events"] == {"power/energy-pkg/": {"value": "12.50", "unit": "Joules", "runtime_s": 1.0}}
    assert "perf_stat_as_root" not in section


def test_perf_stat_denied_falls_back_to_root(tmp_path, monkeypatch):
    build_tree(tmp_path)
    put(tmp_path / "sys/bus/event_source/devices/power/events/energy-pkg", "event=0x02\n")
    ctx = ctx_for(tmp_path, commands=True)
    ctx._sudo_mode = "passwordless"
    calls = []

    def run(ctx_, argv, timeout=15, sudo=False):
        calls.append(sudo)
        if sudo:
            return {"rc": 0, "stderr": "3.0,Joules,power/energy-pkg/,1000,100.00,,\n"}
        return {"rc": 255, "stderr": "Permission denied: perf_event_paranoid\n"}

    monkeypatch.setattr(probe, "which", lambda c, n: "/fake/" + n)
    monkeypatch.setattr(probe, "run_cmd", run)
    section = probe.collect_perf(ctx)
    assert calls == [False, True]
    assert section["perf_stat_as_user"]["events"] == {} and "Permission denied" in section["perf_stat_as_user"]["error"]
    assert section["perf_stat_as_root"]["events"]["power/energy-pkg/"]["value"] == "3.0"


def test_msr_reads_intel_registers_and_decodes_the_unit(tmp_path):
    build_tree(tmp_path, vendor="GenuineIntel")
    device = tmp_path / "dev/cpu/0/msr"
    device.parent.mkdir(parents=True)
    with open(device, "wb") as handle:
        handle.truncate(0x700)
        handle.seek(0x606)
        handle.write((0xE << 8).to_bytes(8, "little"))            # energy status unit 2^-14 J
        handle.seek(0x611)
        handle.write((1234).to_bytes(8, "little"))
    section = probe.collect_msr(ctx_for(tmp_path), {"vendor": "GenuineIntel"})
    assert section["vendor_registers"] == "intel" and section["energy_unit_joules"] == 1 / 16384
    assert section["pkg_energy_raw"] == [1234, 1234] and section["pkg_energy_advanced"] is False


def test_msr_picks_amd_registers(tmp_path, monkeypatch):
    device = tmp_path / "dev/cpu/0/msr"
    put(device, "x")
    asked = []
    counter = iter([100, 200, 300])

    def fake_read(path, register):
        asked.append(register)
        return next(counter) if register != probe.AMD_MSRS["unit"] else (0x10 << 8)

    monkeypatch.setattr(probe, "read_msr", fake_read)
    section = probe.collect_msr(ctx_for(tmp_path), {"vendor": "AuthenticAMD"})
    assert section["vendor_registers"] == "amd" and set(asked) == set(probe.AMD_MSRS.values())
    assert section["energy_unit_joules"] == 1 / (1 << 16) and section["pkg_energy_advanced"] is True


def test_msr_absent_or_unreadable(tmp_path):
    assert probe.collect_msr(ctx_for(tmp_path), {}) == {"device_present": False, "module_loaded": False, "readable_as_user": False}


def test_turbostat_is_not_run_without_the_msr_device(tmp_path, monkeypatch):
    build_tree(tmp_path)
    ctx = ctx_for(tmp_path, commands=True)
    fake_commands(monkeypatch, lambda argv: pytest.fail("turbostat must not run: " + " ".join(argv)))
    section = probe.collect_turbostat(ctx)
    assert section["binary"] == "/fake/turbostat" and "does not load kernel modules" in section["skipped"]


def test_turbostat_runs_when_the_msr_device_exists(tmp_path, monkeypatch):
    put(tmp_path / "dev/cpu/0/msr", "")
    ctx = ctx_for(tmp_path, commands=True)
    ctx._sudo_mode = "passwordless"
    fake_commands(monkeypatch, lambda argv: {"stdout": "Avg_MHz Busy% PkgWatt\n12 1.0 85.2\n"})
    assert "PkgWatt" in probe.collect_turbostat(ctx)["stdout"]


# ---- ipmi, gpu, clock ---------------------------------------------------------------------------

def test_ipmi_without_device_is_skipped_not_probed(tmp_path, monkeypatch):
    build_tree(tmp_path)
    fake_commands(monkeypatch, lambda argv: pytest.fail("ipmitool must not run without /dev/ipmi*"))
    section = probe.collect_ipmi(ctx_for(tmp_path, commands=True))
    assert section["devices"] == [] and "no /dev/ipmi* device" in section["skipped"]


def test_ipmi_dcmi_power_reading(tmp_path, monkeypatch):
    put(tmp_path / "dev/ipmi0", "")
    ctx = ctx_for(tmp_path, commands=True)
    ctx._sudo_mode = "passwordless"
    reading = ("    Instantaneous power reading:                   312 Watts\n"
               "    Minimum during sampling period:                300 Watts\n")
    fake_commands(monkeypatch, lambda argv: {"stdout": reading} if "dcmi" in argv else {"stdout": "Device ID : 32\n"})
    section = probe.collect_ipmi(ctx)
    assert section["dcmi_power_reading"]["watts"] == 312 and section["mc_info_rc"] == 0


def test_ipmi_dcmi_unsupported_is_reported(tmp_path, monkeypatch):
    put(tmp_path / "dev/ipmi0", "")
    ctx = ctx_for(tmp_path, commands=True)
    ctx._sudo_mode = "root"
    ctx.euid = 0
    fake_commands(monkeypatch, lambda argv: {"rc": 1, "stderr": "Get Power Reading failed: Invalid command\n"})
    section = probe.collect_ipmi(ctx)
    assert section["dcmi_power_reading"]["watts"] is None and section["dcmi_power_reading"]["rc"] == 1


HELP_QUERY = '''List of valid properties to query for the switch "--query-gpu=":

"timestamp"
The timestamp of where the query was made.

"name" or "gpu_name"
The official product name of the GPU.

"power.draw" or "power.draw.average"
Last measured average power draw.

"power.draw.instant"
Last measured instantaneous power draw.

"power.limit"
The software power limit.

"power.default_limit"
Default software power limit.

"enforced.power.limit"
Enforced limit.

"total_energy_consumption"
Total energy consumption since the driver was loaded, in mJ.

"module.power.draw.average"
Module power (Grace Hopper).

"memory.total"
Total memory.
'''


def fake_nvidia(argv):
    if "--help-query-gpu" in argv:
        return {"stdout": HELP_QUERY}
    if argv[1:2] == ["-q"]:
        return {"stdout": "GPU 00000000:06:00.0\n    GPU Power Readings\n        Power Draw                        : 61.2 W\n"}
    field = argv[1].split("=", 1)[1].split(",")[0]
    table = {"name": "NVIDIA H100 80GB HBM3\nNVIDIA H100 80GB HBM3\n", "driver_version": "580.105.08\n580.105.08\n",
             "power.draw": "61.20\n60.10\n", "power.draw.average": "61.20\n60.10\n", "power.draw.instant": "[N/A]\n[N/A]\n",
             "power.limit": "700.00\n700.00\n", "power.default_limit": "700.00\n700.00\n", "enforced.power.limit": "700.00\n700.00\n",
             "total_energy_consumption": "1000\n2000\n", "module.power.draw.average": "[Not Supported]\n[Not Supported]\n",
             "memory.total": "81559\n81559\n"}
    return {"stdout": table[field]} if field in table else {"rc": 2, "stderr": "Field not found"}


def test_nvidia_smi_fields_limits_and_energy_counter(tmp_path, monkeypatch):
    ctx = ctx_for(tmp_path, commands=True)
    fake_commands(monkeypatch, fake_nvidia)
    section = probe.collect_gpu(ctx)
    assert section["gpu_count"] == 2 and section["energy_counter_available"] is True
    assert section["power_limits_w"] == {"power.limit": ["700.00", "700.00"], "power.default_limit": ["700.00", "700.00"],
                                          "enforced.power.limit": ["700.00", "700.00"]}
    assert "power.draw.instant" in section["unsupported_or_na"] and "module.power.draw.average" in section["unsupported_or_na"]
    assert section["per_field"]["power.draw"] == ["61.20", "60.10"]
    assert "pstate" in section["unsupported_or_na"]            # asked, driver rejected it
    assert [s["id"] for s in ctx.sources] == ["nvml_smi:gpu0:total_energy_consumption", "nvml_smi:gpu1:total_energy_consumption"]
    assert section["nvidia_smi_q_power"] == {"gpu0": {"GPU Power Readings": {"Power Draw": "61.2 W"}}}


def test_gpu_energy_counter_in_the_sanity_check(tmp_path, monkeypatch):
    ctx = ctx_for(tmp_path, commands=True)
    readings = iter([["1000", "2000"], ["1500", "2400"], ["4000", "6000"]])

    def handler(argv):
        if "--help-query-gpu" in argv:
            return {"stdout": HELP_QUERY}
        if argv[1:2] == ["-q"]:
            return {"rc": 1}
        if argv[1] == "--query-gpu=total_energy_consumption":
            return {"stdout": "\n".join(next(readings)) + "\n"}
        return fake_nvidia(argv)

    fake_commands(monkeypatch, handler)
    probe.collect_gpu(ctx)
    assert {s["kind"] for s in ctx.sources} == {"nvml_smi"}
    readings = iter([["1500", "2400"], ["1800", "2700"], ["4000", "6000"]])
    section = probe.collect_counter_sanity(ctx, 0.01, 0.01, lambda c, s: 8)
    gpu0 = section["sources"][0]
    assert gpu0["advanced"] is True and gpu0["idle"]["delta_j"] == 0.3 and gpu0["busy"]["delta_j"] == 2.2


def test_no_nvidia_smi(tmp_path, monkeypatch):
    fake_commands(monkeypatch, lambda argv: None)
    monkeypatch.setattr(probe, "which", lambda ctx, name: None)
    assert probe.collect_gpu(ctx_for(tmp_path, commands=True)) == {"present": False}


CHRONY = """Reference ID    : A9FEA97B (169.254.169.123)
Stratum         : 4
Ref time (UTC)  : Tue Nov 10 10:00:00 2026
System time     : 0.000001234 seconds slow of NTP time
Last offset     : -0.000000890 seconds
RMS offset      : 0.000004000 seconds
Root delay      : 0.000200000 seconds
Update interval : 16.0 seconds
Leap status     : Normal
"""


def test_clock_sync_from_chrony(tmp_path, monkeypatch):
    put(tmp_path / "sys/devices/system/clocksource/clocksource0/current_clocksource", "tsc\n")
    put(tmp_path / "dev/ptp0", "")
    ctx = ctx_for(tmp_path, commands=True)
    fake_commands(monkeypatch, lambda argv: {"stdout": CHRONY} if argv[:2] == ["chronyc", "tracking"] else (
        {"stdout": "NTPSynchronized=yes\nNTP=yes\nTimezone=Etc/UTC\n"} if argv[0] == "timedatectl" else {"stdout": "MS Name\n^* 169.254.169.123\n"}))
    section = probe.collect_clock(ctx)
    assert section["synchronized"] is True and section["chrony_system_time_offset_s"] == pytest.approx(-1.234e-6)
    assert section["clocksource"] == "tsc" and section["ptp_devices"] == ["ptp0"]
    assert section["chrony"]["Stratum"] == "4"


def test_clock_unsynchronized_chrony(tmp_path, monkeypatch):
    bad = CHRONY.replace("Leap status     : Normal", "Leap status     : Not synchronised")
    fake_commands(monkeypatch, lambda argv: {"stdout": bad} if argv[0] == "chronyc" else {"stdout": "NTPSynchronized=no\n"})
    assert probe.collect_clock(ctx_for(tmp_path, commands=True))["synchronized"] is False


def test_clock_falls_back_to_timedatectl(tmp_path, monkeypatch):
    fake_commands(monkeypatch, lambda argv: None if argv[0] == "chronyc" else {"stdout": "NTPSynchronized=yes\nNTP=yes\n"})
    section = probe.collect_clock(ctx_for(tmp_path, commands=True))
    assert section["synchronized"] is True and section["chrony_system_time_offset_s"] is None


class FakeNvml:
    """Stands in for libnvidia-ml: every function takes ctypes pointers and returns an NVML rc."""

    def __init__(self, devices=2, energy=True, init_rc=0):
        self.energy = [1000 * (i + 1) for i in range(devices)] if energy else None
        self.devices, self.init_rc, self.calls = devices, init_rc, []

    def nvmlInit_v2(self):
        return self.init_rc

    def nvmlDeviceGetCount_v2(self, count):
        count.contents.value = self.devices
        return 0

    def nvmlSystemGetDriverVersion(self, buffer, length):
        buffer.value = b"580.105.08"
        return 0

    def nvmlDeviceGetHandleByIndex_v2(self, index, handle):
        handle.contents.value = 0x1000 + index.value
        return 0

    def nvmlDeviceGetName(self, handle, buffer, length):
        buffer.value = b"NVIDIA H100 80GB HBM3"
        return 0

    def nvmlDeviceGetTotalEnergyConsumption(self, handle, out_):
        self.calls.append(handle.value)
        if self.energy is None:
            return 3                                  # NVML_ERROR_NOT_SUPPORTED
        self.energy[handle.value - 0x1000] += 500
        out_.contents.value = self.energy[handle.value - 0x1000]
        return 0

    def nvmlDeviceGetPowerUsage(self, handle, out_):
        out_.contents.value = 71200
        return 0

    def nvmlDeviceGetPowerManagementLimit(self, handle, out_):
        out_.contents.value = 700000
        return 0

    nvmlDeviceGetEnforcedPowerLimit = nvmlDeviceGetPowerManagementLimit
    nvmlDeviceGetPowerManagementDefaultLimit = nvmlDeviceGetPowerManagementLimit

    def nvmlDeviceGetPowerManagementLimitConstraints(self, handle, low, high):
        low.contents.value, high.contents.value = 200000, 700000
        return 0


def test_nvml_energy_counter_via_ctypes(tmp_path, monkeypatch):
    fake = FakeNvml()
    monkeypatch.setattr(probe, "load_nvml", lambda: (fake, "libnvidia-ml.so.1"))
    ctx = ctx_for(tmp_path, commands=True)
    section = probe.collect_nvml(ctx)
    assert section["energy_counter_available"] is True and section["driver_version"] == "580.105.08"
    assert [d["name"] for d in section["devices"]] == ["NVIDIA H100 80GB HBM3"] * 2
    assert section["devices"][0]["power_limit_w"] == {"current": 700.0, "enforced": 700.0, "default": 700.0, "min": 200.0, "max": 700.0}
    assert section["devices"][0]["power_usage_w"] == 71.2 and section["devices"][0]["total_energy_mj"] == 1500
    assert [s["id"] for s in ctx.sources] == ["nvml:gpu0:total_energy_consumption", "nvml:gpu1:total_energy_consumption"]


def test_nvml_counter_is_what_the_sanity_check_reads(tmp_path, monkeypatch):
    fake = FakeNvml(devices=1)
    monkeypatch.setattr(probe, "load_nvml", lambda: (fake, "libnvidia-ml.so.1"))
    ctx = ctx_for(tmp_path, commands=True)
    probe.collect_nvml(ctx)
    section = probe.collect_counter_sanity(ctx, 0.01, 0.01, lambda c, s: 1)
    (gpu,) = section["sources"]
    assert gpu["advanced"] is True and gpu["idle"]["delta_native"] == 500 and gpu["busy"]["delta_native"] == 500
    assert gpu["idle"]["delta_j"] == 0.5


def test_nvml_replaces_the_nvidia_smi_energy_duplicate(tmp_path, monkeypatch):
    ctx = ctx_for(tmp_path, commands=True)
    fake_commands(monkeypatch, fake_nvidia)
    probe.collect_gpu(ctx)
    assert {s["kind"] for s in ctx.sources} == {"nvml_smi"}
    monkeypatch.setattr(probe, "load_nvml", lambda: (FakeNvml(devices=2), "libnvidia-ml.so.1"))
    probe.collect_nvml(ctx)
    assert {s["kind"] for s in ctx.sources} == {"nvml"} and len(ctx.sources) == 2


def test_nvml_without_an_energy_counter_or_library(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "load_nvml", lambda: (FakeNvml(energy=False), "libnvidia-ml.so.1"))
    ctx = ctx_for(tmp_path, commands=True)
    section = probe.collect_nvml(ctx)
    assert section["energy_counter_available"] is False and section["devices"][0]["total_energy_mj"] is None and ctx.sources == []
    monkeypatch.setattr(probe, "load_nvml", lambda: (None, None))
    assert probe.collect_nvml(ctx_for(tmp_path, commands=True)) == {"present": False}
    monkeypatch.setattr(probe, "load_nvml", lambda: (FakeNvml(init_rc=9), "libnvidia-ml.so.1"))
    assert probe.collect_nvml(ctx_for(tmp_path, commands=True))["init_rc"] == 9
    assert probe.collect_nvml(ctx_for(tmp_path, commands=False)) == {"skipped": "commands disabled"}


def test_known_power_fields_are_asked_even_if_the_help_does_not_list_them(tmp_path, monkeypatch):
    asked = []

    def handler(argv):
        if "--help-query-gpu" in argv:
            return {"stdout": '"timestamp"\nThe timestamp.\n\n  "name" or "gpu_name"\nThe name.\n'}      # no power names, indented
        if argv[1:2] == ["-q"]:
            return {"rc": 1}
        asked.append(argv[1].split("=", 1)[1])
        return fake_nvidia(argv)

    fake_commands(monkeypatch, handler)
    section = probe.collect_gpu(ctx_for(tmp_path, commands=True))
    assert {"power.draw", "power.limit", "power.draw.instant", "total_energy_consumption"} <= set(asked)
    assert section["power_limits_w"]["power.limit"] == ["700.00", "700.00"]
    assert section["fields_listed"] == ["timestamp", "name", "gpu_name"]


def test_pci_bus_id_is_not_recorded(tmp_path, monkeypatch):
    ctx = ctx_for(tmp_path, commands=True)
    fake_commands(monkeypatch, fake_nvidia)
    section = probe.collect_gpu(ctx)
    assert "00000000:06:00.0" not in json.dumps(section)
    assert "GPU <pci bus id redacted>" in section["nvidia_smi_q_power_raw"]
    assert probe.parse_smi_power(section["nvidia_smi_q_power_raw"]) == section["nvidia_smi_q_power"]


def test_nvidia_smi_q_power_is_parsed_per_gpu_and_section():
    text = """
==============NVSMI LOG==============

Driver Version                            : 580.105.08

Attached GPUs                             : 1
GPU 00000000:06:00.0
    Product Name                          : NVIDIA GH200 480GB
    GPU Power Readings
        Average Power Draw                : 71.16 W
        Current Power Limit               : 700.00 W
    Module Power Readings
        Average Power Draw                : 160.10 W
GPU 00000000:07:00.0
    GPU Power Readings
        Average Power Draw                : 60.00 W
"""
    parsed = probe.parse_smi_power(text)
    assert parsed["gpu0"][""] == {"Product Name": "NVIDIA GH200 480GB"}
    assert parsed["gpu0"]["GPU Power Readings"]["Current Power Limit"] == "700.00 W"
    assert parsed["gpu0"]["Module Power Readings"] == {"Average Power Draw": "160.10 W"}
    assert parsed["gpu1"]["GPU Power Readings"] == {"Average Power Draw": "60.00 W"}


@pytest.mark.parametrize("value,counts", [("12.5", True), ("0.00", False), ("<not counted>", False), ("<not supported>", False)])
def test_perf_counts_requires_a_positive_reading(value, counts):
    assert probe.perf_counts({"power/energy-psys/": {"value": value, "unit": "Joules"}}) is counts


def test_vm_power_pmu_that_counts_zero_is_not_a_source(tmp_path, monkeypatch):
    """Lambda gpu_1x_h100_sxm5 (2026-11-11): energy-psys is exposed, perf as root runs, and the reading is 0.00 J."""
    build_tree(tmp_path, hypervisor=True, rapl=False)
    put(tmp_path / "sys/bus/event_source/devices/power/events/energy-psys", "event=0x05\n")
    put(tmp_path / "proc/sys/kernel/perf_event_paranoid", "4\n")
    ctx = ctx_for(tmp_path, commands=True)
    ctx._sudo_mode = "passwordless"

    def run(ctx_, argv, timeout=15, sudo=False):
        if sudo:
            return {"rc": 0, "stderr": "0.00,Joules,power/energy-psys/,5000000000,100.00,,\n"}
        return {"rc": 255, "stderr": "Access to performance monitoring and observability operations is limited.\n"}

    monkeypatch.setattr(probe, "which", lambda c, n: "/fake/" + n)
    monkeypatch.setattr(probe, "run_cmd", run)
    perf = probe.collect_perf(ctx)
    assert perf["perf_stat_as_user"]["counts"] is False and perf["perf_stat_as_root"]["counts"] is False
    report = {"cpu": {}, "powercap": {"zones": [], "domains": [], "all_packages_readable_as_user": False, "all_packages_readable_as_root": False},
              "hwmon": {"devices_with_energy_or_power": []}, "perf": perf, "msr": {"device_present": False, "readable_as_user": False},
              "gpu": {}, "nvml": {}, "ipmi": {}, "clock": {}, "counter_sanity": {}, "virtualization": {"verdict": "virtualized"}}
    summary = probe.summarize(report)
    assert summary["cpu_energy"]["best_source"] == "none" and summary["cpu_energy"]["needs_root"] is False
    assert any("counted nothing" in note for note in summary["notes"])
    assert any("perf_event_paranoid=4" in note for note in summary["notes"])
    assert any("modelled, not measured" in note for note in summary["notes"])


def test_power_pmu_that_counts_for_root_only_needs_root(tmp_path, monkeypatch):
    build_tree(tmp_path, rapl=False)
    put(tmp_path / "sys/bus/event_source/devices/power/events/energy-pkg", "event=0x02\n")
    ctx = ctx_for(tmp_path, commands=True)
    ctx._sudo_mode = "passwordless"
    monkeypatch.setattr(probe, "which", lambda c, n: "/fake/" + n)
    monkeypatch.setattr(probe, "run_cmd", lambda c, argv, timeout=15, sudo=False: (
        {"rc": 0, "stderr": "41.5,Joules,power/energy-pkg/,1000000000,100.00,,\n"} if sudo else {"rc": 255, "stderr": "denied"}))
    perf = probe.collect_perf(ctx)
    report = {"cpu": {}, "powercap": {"zones": [], "domains": [], "all_packages_readable_as_user": False, "all_packages_readable_as_root": False},
              "hwmon": {"devices_with_energy_or_power": []}, "perf": perf, "msr": {"device_present": False, "readable_as_user": False},
              "gpu": {}, "nvml": {}, "ipmi": {}, "clock": {}, "counter_sanity": {}, "virtualization": {"verdict": "bare_metal"}}
    cpu_energy = probe.summarize(report)["cpu_energy"]
    assert (cpu_energy["best_source"], cpu_energy["needs_root"], cpu_energy["readable_as_user"]) == ("perf_power_pmu", True, False)


def test_summary_reports_the_gpu_energy_source(tmp_path, monkeypatch):
    base = {"cpu": {}, "powercap": {"zones": [], "domains": [], "all_packages_readable_as_user": False, "all_packages_readable_as_root": False},
            "hwmon": {"devices_with_energy_or_power": []}, "perf": {"power_events": {}}, "msr": {"device_present": False, "readable_as_user": False},
            "ipmi": {}, "clock": {}, "counter_sanity": {}, "virtualization": {"verdict": "bare_metal"}}
    smi = probe.summarize({**base, "gpu": {"present": True, "energy_counter_available": True}, "nvml": {}})
    lib = probe.summarize({**base, "gpu": {"present": True, "energy_counter_available": False}, "nvml": {"energy_counter_available": True}})
    none = probe.summarize({**base, "gpu": {"present": True, "energy_counter_available": False}, "nvml": {"present": False}})
    assert (smi["gpu_energy_counter_via"], lib["gpu_energy_counter_via"], none["gpu_energy_counter_via"]) == ("nvidia-smi", "nvml", None)
    assert none["gpu_energy_counter"] is False and any("neither nvidia-smi nor NVML" in n for n in none["notes"])


def test_ipmi_device_is_checked_before_the_binary(tmp_path, monkeypatch):
    build_tree(tmp_path)
    monkeypatch.setattr(probe, "which", lambda ctx, name: None)
    assert "no /dev/ipmi* device" in probe.collect_ipmi(ctx_for(tmp_path, commands=True))["skipped"]
    put(tmp_path / "dev/ipmi0", "")
    assert "ipmitool not installed" in probe.collect_ipmi(ctx_for(tmp_path, commands=True))["skipped"]


# ---- perf power events in the counter sanity check ---------------------------------------------------

def perf_line(joules, seconds, event="energy-psys"):
    return f"{joules},Joules,power/{event}/,{int(seconds * 1e9)},100.00,,\n"


def perf_runner(by_seconds, calls=None):
    """run_cmd stand-in: `perf stat ... sleep N` answers by_seconds[N] on stderr (perf prints its counts there)."""
    def run(ctx, argv, timeout=15, sudo=False):
        if calls is not None:
            calls.append({"argv": argv, "sudo": sudo})
        seconds = float(argv[-1])
        return {"rc": 0, "stdout": "", "stderr": by_seconds[seconds]}
    return run


def sanity_with_perf(tmp_path, monkeypatch, by_seconds, *, via_sudo=True, events=("energy-psys",), idle=3.0, busy=5.0, calls=None):
    ctx = ctx_for(tmp_path, commands=True)
    ctx.perf_plan = {"events": list(events), "via_sudo": via_sudo}
    monkeypatch.setattr(probe, "run_cmd", perf_runner(by_seconds, calls))
    seen = {}

    def load(ctx_, seconds):
        seen["seconds"] = seconds
        return 26

    section = probe.collect_counter_sanity(ctx, idle, busy, load)
    return section, seen


def test_a_perf_event_that_counts_zero_under_load_is_not_advancing(tmp_path, monkeypatch):
    """The Lambda gpu_1x_h100_sxm5 case: energy-psys is exposed, perf as root runs, and the counter reads 0.00 J."""
    calls = []
    section, seen = sanity_with_perf(tmp_path, monkeypatch, {3.0: perf_line("0.00", 3), 5.0: perf_line("0.00", 5)}, calls=calls)
    (entry,) = section["sources"]
    assert entry["id"] == "perf:power/energy-psys/" and entry["kind"] == "perf" and entry["read_via"] == "sudo"
    assert entry["advanced"] is False and entry["idle"]["delta_j"] == 0.0 and entry["busy"]["avg_w"] == 0.0
    assert [c["sudo"] for c in calls] == [True, True] and seen["seconds"] > 5.0      # burn lasts busy_seconds plus the ramp


def test_a_perf_event_that_advances_under_load(tmp_path, monkeypatch):
    section, _ = sanity_with_perf(tmp_path, monkeypatch, {3.0: perf_line("20.00", 3), 5.0: perf_line("200.00", 5)}, via_sudo=False)
    (entry,) = section["sources"]
    assert entry["advanced"] is True and entry["read_via"] == "direct"
    assert entry["idle"]["avg_w"] == pytest.approx(6.667, abs=0.001) and entry["busy"]["avg_w"] == 40.0
    assert entry["busy_over_idle"] == 6.0


def test_perf_windows_use_perf_stat_sleep_and_each_event(tmp_path, monkeypatch):
    calls = []
    sanity_with_perf(tmp_path, monkeypatch, {3.0: perf_line("1.0", 3), 5.0: perf_line("1.0", 5)}, events=("energy-pkg", "energy-ram"),
                     via_sudo=False, calls=calls)
    assert sorted(c["argv"][-1] for c in calls) == ["3", "5"]
    assert all(c["argv"][:5] == ["perf", "stat", "-a", "-x,", "-e"] and c["argv"][5] == "power/energy-pkg/,power/energy-ram/" for c in calls)


def test_perf_event_without_a_number_is_reported_as_not_advancing(tmp_path, monkeypatch):
    bad = "<not counted>,Joules,power/energy-psys/,0,0.00,,\n"
    section, _ = sanity_with_perf(tmp_path, monkeypatch, {3.0: bad, 5.0: bad})
    (entry,) = section["sources"]
    assert entry["advanced"] is False and entry["idle"] is None and entry["busy"] is None


def test_perf_events_sit_next_to_file_counters(tmp_path, monkeypatch):
    build_tree(tmp_path, sockets=1)
    ctx = ctx_for(tmp_path, commands=True)
    probe.collect_powercap(ctx)
    ctx.perf_plan = {"events": ["energy-pkg"], "via_sudo": False}
    monkeypatch.setattr(probe, "run_cmd", perf_runner({3.0: perf_line("9.0", 3, "energy-pkg"), 5.0: perf_line("90.0", 5, "energy-pkg")}))
    section = probe.collect_counter_sanity(ctx, 3.0, 5.0, bump(tmp_path, 1_000_000))
    assert [s["kind"] for s in section["sources"]] == ["powercap", "powercap", "perf"]
    assert all(s["advanced"] for s in section["sources"] if s["kind"] == "perf")


def test_perf_plan_follows_who_can_run_perf(tmp_path, monkeypatch):
    build_tree(tmp_path)
    put(tmp_path / "sys/bus/event_source/devices/power/events/energy-psys", "event=0x05\n")
    put(tmp_path / "sys/bus/event_source/devices/power/events/energy-pkg.scale", "1e-9\n")

    def collect(user_rc, root_rc):
        ctx = ctx_for(tmp_path, commands=True)
        ctx._sudo_mode = "passwordless"
        monkeypatch.setattr(probe, "which", lambda c, n: "/fake/" + n)
        monkeypatch.setattr(probe, "run_cmd", lambda c, argv, timeout=15, sudo=False: {
            "rc": root_rc if sudo else user_rc, "stderr": "0.00,Joules,power/energy-psys/,1000000000,100.00,,\n"})
        probe.collect_perf(ctx)
        return ctx.perf_plan

    assert collect(0, 0) == {"events": ["energy-psys"], "via_sudo": False}
    assert collect(255, 0) == {"events": ["energy-psys"], "via_sudo": True}
    assert collect(255, 1) is None


def test_lambda_h100_vm_end_to_end_cpu_energy_is_not_measurable(tmp_path, monkeypatch):
    """No powercap, no MSR, no IPMI, a dead psys event, perf only as root: the probe says so without a manual test."""
    build_tree(tmp_path, hypervisor=True, rapl=False)
    put(tmp_path / "sys/bus/event_source/devices/power/events/energy-psys", "event=0x05\n")
    put(tmp_path / "proc/sys/kernel/perf_event_paranoid", "4\n")
    ctx = ctx_for(tmp_path, commands=True)
    ctx._sudo_mode = "passwordless"
    monkeypatch.setattr(probe, "which", lambda c, n: "/fake/" + n)

    def run(ctx_, argv, timeout=15, sudo=False):
        if argv[0] == "perf":
            return {"rc": 0, "stderr": perf_line("0.00", float(argv[-1]))} if sudo else {"rc": 255, "stderr": "Access to performance monitoring is limited"}
        return {"error": "not found: " + argv[0]}

    monkeypatch.setattr(probe, "run_cmd", run)
    report = {"cpu": probe.collect_cpu(ctx)}
    report["virtualization"] = {"verdict": "virtualized"}
    report["powercap"] = probe.collect_powercap(ctx)
    report["hwmon"] = probe.collect_hwmon(ctx)
    report["perf"] = probe.collect_perf(ctx)
    report["msr"] = probe.collect_msr(ctx, report["cpu"])
    report.update(gpu={}, nvml={}, ipmi={}, clock={})
    report["counter_sanity"] = probe.collect_counter_sanity(ctx, 3.0, 5.0, lambda c, s: 26)
    summary = probe.summarize(report)
    assert summary["cpu_energy"]["best_source"] == "none" and summary["cpu_energy"]["needs_root"] is False
    perf = next(c for c in summary["cpu_energy"]["candidates"] if c["source"] == "perf_power_pmu")
    assert perf["present"] is True and perf["counters_advance"] is False and perf["readable_as_root"] is not True
    assert any("counted nothing under load" in note for note in summary["notes"])
    assert any("no CPU energy source is readable" in note for note in summary["notes"])


# ---- GPU power limits --------------------------------------------------------------------------------

SMI_Q_POWER_H100 = """
==============NVSMI LOG==============

Driver Version                            : 580.105.08
CUDA Version                              : 13.0

Attached GPUs                             : 1
GPU 00000000:06:00.0
    GPU Power Readings
        Average Power Draw                : 71.16 W
        Instantaneous Power Draw          : 71.20 W
        Current Power Limit               : 700.00 W
        Requested Power Limit             : 700.00 W
        Default Power Limit               : 700.00 W
        Min Power Limit                   : 200.00 W
        Max Power Limit                   : 700.00 W
"""


def test_power_limits_come_from_nvidia_smi_q_when_the_query_fields_fail(tmp_path, monkeypatch):
    def handler(argv):
        if "--help-query-gpu" in argv:
            return {"stdout": '"name"\nthe name\n'}
        if argv[1:2] == ["-q"]:
            return {"stdout": SMI_Q_POWER_H100}
        field = argv[1].split("=", 1)[1]
        return {"stdout": "NVIDIA H100 80GB HBM3\n"} if field == "name" else {"rc": 2, "stderr": "Field not found"}

    fake_commands(monkeypatch, handler)
    section = probe.collect_gpu(ctx_for(tmp_path, commands=True))
    assert section["power_limits_w"] == {"power.limit": ["700.00"], "power.default_limit": ["700.00"], "power.min_limit": ["200.00"],
                                          "power.max_limit": ["700.00"]}
    assert section["power_limits_source"] == "nvidia-smi -q -d POWER"


def test_power_limits_prefer_the_query_fields(tmp_path, monkeypatch):
    fake_commands(monkeypatch, fake_nvidia)
    section = probe.collect_gpu(ctx_for(tmp_path, commands=True))
    assert section["power_limits_w"]["power.limit"] == ["700.00", "700.00"] and section["power_limits_source"] == "nvidia-smi --query-gpu"


def test_summary_falls_back_to_nvml_for_power_limits():
    nvml = {"devices": [{"power_limit_w": {"current": 700.0, "default": 700.0, "min": 200.0, "max": 700.0, "enforced": 700.0}}]}
    base = {"cpu": {}, "powercap": {"zones": [], "domains": [], "all_packages_readable_as_user": False, "all_packages_readable_as_root": False},
            "hwmon": {"devices_with_energy_or_power": []}, "perf": {"power_events": {}}, "msr": {"device_present": False, "readable_as_user": False},
            "ipmi": {}, "clock": {}, "counter_sanity": {}, "virtualization": {"verdict": "bare_metal"}}
    summary = probe.summarize({**base, "gpu": {"present": True, "power_limits_w": {}}, "nvml": nvml})
    assert summary["gpu_power_limits_w"]["power.limit"] == ["700.00"] and summary["gpu_power_limits_source"] == "nvml"
    smi = probe.summarize({**base, "gpu": {"present": True, "power_limits_w": {"power.limit": ["600.00"]}, "power_limits_source": "x"}, "nvml": nvml})
    assert smi["gpu_power_limits_w"] == {"power.limit": ["600.00"]} and smi["gpu_power_limits_source"] == "x"
    none = probe.summarize({**base, "gpu": {"present": True, "power_limits_w": {}}, "nvml": {}})
    assert none["gpu_power_limits_w"] is None


# ---- lease: name, owner, expiry -----------------------------------------------------------------------

NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("name,valid,problem,owner,minutes", [
    ("p3-probe-h100-owner-ejiod-expires-20261111T1807Z", True, None, "ejiod", 187.0),
    ("p3-campaign-b200-owner-ejiod-expires-20261112T0000Z", True, None, "ejiod", 540.0),
    ("p3-probe-h100-owner-ejiod-expires-20261111T1500Z", False, "expired", "ejiod", 0.0),
    ("p3-probe-h100-owner-ejiod-expires-20261110T1500Z", False, "expired", "ejiod", -1440.0),
    ("p3-probe-owner-ejiod-expires-20261111T1900Z", True, None, "ejiod", 240.0),
    ("p3-cpu-c3hm-owner-ejiod-expires-20261111t1807z", True, None, "ejiod", 187.0),  # GCP names and labels are lower case
    ("p3-cpu-c3hm-owner-ejiod-expires-20261111t1500z", False, "expired", "ejiod", 0.0),
    ("p3-cpu-c3hm-owner-ejiod-expires-20261311t1807z", False, "expiry is not a valid UTC time", None, None),
    ("p3-probe-h100-owner-ejiod-expires-20261311T1807Z", False, "expiry is not a valid UTC time", None, None),
    ("p3-probe-h100-ejiod-expires-20261111T1807Z", False, "does not match", None, None),
    ("probe-h100", False, "does not match", None, None),
    ("P3-probe-h100-owner-ejiod-expires-20261111T1807Z", False, "does not match", None, None),
    ("", False, "does not match", None, None),
    (None, False, "does not match", None, None),
])
def test_lease_name_convention(name, valid, problem, owner, minutes):
    status = probe.lease_status(name, now=NOW)
    assert status["valid"] is valid and status["owner"] == owner and status["minutes_left"] == minutes
    assert (problem is None and status["problem"] is None) or (problem in status["problem"])


def test_lease_minimum_time_left():
    name = "p3-probe-h100-owner-ejiod-expires-20261111T1807Z"
    assert probe.lease_status(name, now=NOW, min_minutes=120)["valid"] is True
    short = probe.lease_status(name, now=NOW, min_minutes=240)
    assert short["valid"] is False and short["problem"] == "less than 240 minutes left"


def test_lease_from_the_option_the_file_or_nothing(tmp_path):
    ctx = ctx_for(tmp_path)
    assert probe.collect_lease(ctx, None)["present"] is False
    put(tmp_path / "etc/paper3-lease", "p3-probe-h100-owner-ejiod-expires-20990101T0000Z\n")
    from_file = probe.collect_lease(ctx, None)
    assert from_file["present"] is True and from_file["valid"] is True and from_file["source"] == "/etc/paper3-lease"
    option = probe.collect_lease(ctx, "p3-x-owner-bob-expires-20000101T0000Z")
    assert option["source"] == "--lease-name" and option["valid"] is False and option["problem"] == "expired"


def test_lease_shows_up_in_the_summary(tmp_path, capsys):
    root = build_tree(tmp_path / "root")
    without = run_main(root, capsys=capsys)
    assert without["lease"]["present"] is False and without["summary"]["lease"]["present"] is False
    assert any("no lease recorded" in note for note in without["summary"]["notes"])
    good = run_main(root, "--lease-name", "p3-probe-h100-owner-ejiod-expires-20990101T0000Z", capsys=capsys)
    assert good["summary"]["lease"]["valid"] is True and not any("lease" in note for note in good["summary"]["notes"])
    stale = run_main(root, "--lease-name", "p3-probe-h100-owner-ejiod-expires-20000101T0000Z", capsys=capsys)
    assert any("lease problem: expired" in note for note in stale["summary"]["notes"])


# ---- summary ------------------------------------------------------------------------------------

def test_summary_on_a_vm_without_counters(tmp_path):
    build_tree(tmp_path, hypervisor=True, rapl=False)
    summary = full_report(tmp_path)["summary"]
    assert summary["virtualization"] == "virtualized"
    assert summary["cpu_energy"]["best_source"] == "none" and summary["cpu_energy"]["counters_advance"] is None
    assert any("modelled, not measured" in note for note in summary["notes"])


def test_summary_needs_root_when_only_root_can_read(tmp_path, monkeypatch):
    build_tree(tmp_path, sockets=1)
    denied = {str(tmp_path / "sys/class/powercap/intel-rapl:0/energy_uj"), str(tmp_path / "sys/class/powercap/intel-rapl:0:0/energy_uj")}
    real = probe.read_file
    monkeypatch.setattr(probe, "read_file",
                        lambda path: (_ for _ in ()).throw(PermissionError(13, "denied")) if path in denied else real(path))
    ctx = ctx_for(tmp_path, commands=True)
    ctx._sudo_mode = "passwordless"
    fake_commands(monkeypatch, lambda argv: {"stdout": "10\n"} if argv[0] == "cat" else None)
    report = {"cpu": probe.collect_cpu(ctx), "powercap": probe.collect_powercap(ctx), "hwmon": {"devices_with_energy_or_power": []},
              "perf": {"power_events": {}}, "msr": {"device_present": False, "readable_as_user": False}, "gpu": {}, "ipmi": {},
              "clock": {}, "counter_sanity": {}, "virtualization": {"verdict": "bare_metal"}}
    summary = probe.summarize(report)
    assert summary["cpu_energy"] == {**summary["cpu_energy"], "best_source": "powercap", "readable_as_user": False, "needs_root": True}
    assert any("--enable-rapl-read" in note for note in summary["notes"])


def test_summary_flags_package_count_mismatch(tmp_path):
    build_tree(tmp_path, sockets=2)
    for path in (tmp_path / "sys/class/powercap/intel-rapl:1:0", tmp_path / "sys/class/powercap/intel-rapl:1"):
        for child in path.iterdir():
            child.unlink()
        path.rmdir()
    notes = full_report(tmp_path)["summary"]["notes"]
    assert any("package zones (1) differ from sockets" in note for note in notes)


def test_summary_notes_missing_gpu_energy_counter():
    base = {"cpu": {}, "powercap": {"zones": [], "domains": [], "all_packages_readable_as_user": False, "all_packages_readable_as_root": False},
            "hwmon": {"devices_with_energy_or_power": []}, "perf": {"power_events": {}}, "msr": {"device_present": False, "readable_as_user": False},
            "ipmi": {}, "clock": {}, "counter_sanity": {}, "virtualization": {"verdict": "bare_metal"}}
    summary = probe.summarize({**base, "gpu": {"present": True, "gpu_count": 1, "energy_counter_available": False}})
    assert any("integrate power.draw" in note for note in summary["notes"])


# ---- whole-program behaviour ---------------------------------------------------------------------

def test_main_json_schema(tmp_path, capsys):
    root = build_tree(tmp_path / "root", sockets=2)
    report = run_main(root, load_fn=bump(root, 1_000_000), capsys=capsys)
    assert list(report) == TOP_LEVEL_KEYS and report["schema"] == "probe_power/1"
    assert report["errors"] == [] and report["probe"]["commands_enabled"] is False and report["probe"]["root"] == str(root)
    assert report["host"]["os"] == "Ubuntu 22.04.5 LTS" and report["host"]["hostname"] is None
    assert report["summary"]["sockets"] == 2 and report["summary"]["cpu_energy"]["best_source"] == "powercap"
    assert report["summary"]["cpu_energy"]["counters_advance"] is True
    assert report["summary"]["cpu_energy"]["rapl_domains"] == ["dram", "package-0", "package-1"]
    assert report["gpu"] == {"present": False} and report["counter_sanity"]["load_workers"] == 4


def test_main_writes_the_output_file(tmp_path, capsys):
    root = build_tree(tmp_path / "root")
    target = tmp_path / "out.json"
    printed = run_main(root, "--output", str(target), capsys=capsys)
    assert json.loads(target.read_text()) == printed


def test_main_is_read_only(tmp_path, capsys):
    root = build_tree(tmp_path / "root")
    before = tree_digest(root)
    run_main(root, capsys=capsys)
    assert tree_digest(root) == before


def test_root_option_never_runs_commands(tmp_path, monkeypatch, capsys):
    root = build_tree(tmp_path / "root")

    def boom(*args, **kwargs):
        raise AssertionError("a command was run")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    report = run_main(root, capsys=capsys)
    assert report["probe"]["sudo"] in ("disabled", "root")


def test_a_failing_section_does_not_abort_the_probe(tmp_path, monkeypatch, capsys):
    root = build_tree(tmp_path / "root")
    monkeypatch.setattr(probe, "collect_hwmon", lambda ctx: 1 / 0)
    report = run_main(root, capsys=capsys)
    assert report["hwmon"] == {"error": "ZeroDivisionError: division by zero"}
    assert report["errors"] == [{"section": "hwmon", "error": "ZeroDivisionError: division by zero"}]
    assert report["powercap"]["package_zones"] == 2 and report["summary"]["sockets"] == 2


def test_hostname_only_with_the_flag(tmp_path, capsys):
    root = build_tree(tmp_path / "root")
    assert run_main(root, "--include-hostname", capsys=capsys)["host"]["hostname"]


def test_no_load_skips_the_burn(tmp_path, capsys):
    root = build_tree(tmp_path / "root")
    report = run_main(root, "--no-load", load_fn=lambda c, s: pytest.fail("burn must not run"), capsys=capsys)
    assert report["counter_sanity"]["busy_seconds"] == 0.0 and "load_workers" not in report["counter_sanity"]


def test_command_line_entry_point(tmp_path):
    root = build_tree(tmp_path / "root")
    result = subprocess.run([sys.executable, str(REPO / "probe_power.py"), "--root", str(root), "--no-load", "--idle-seconds", "0.05"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["summary"]["cpu_energy"]["best_source"] == "powercap"


def test_bad_option_exits_2():
    result = subprocess.run([sys.executable, str(REPO / "probe_power.py"), "--nope"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 2


def test_probe_is_standard_library_only():
    if not hasattr(sys, "stdlib_module_names"):
        pytest.skip("needs Python 3.10+")
    tree = ast.parse((REPO / "probe_power.py").read_text())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add((node.module or "").split(".")[0])
    assert modules <= set(sys.stdlib_module_names), modules - set(sys.stdlib_module_names)


def test_probe_compiles_for_python_38_syntax():
    ast.parse((REPO / "probe_power.py").read_text(), feature_version=(3, 8))
