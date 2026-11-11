#!/usr/bin/env python3
"""Read-only power-measurement probe for a fresh benchmark host (Paper 3 infrastructure).

Run it once on each new host type before any measurement, as the normal benchmark user (~30 s):

    python3 probe_power.py --output probe-$(date -u +%Y%m%dT%H%M%SZ).json

It answers, as JSON: which CPU/sockets/SMT this is, whether it is virtualized, whether RAPL
(powercap) exists per domain, whether `energy_uj` is readable as the current user and as root, whether
the counters really advance under load, which other CPU-energy paths exist (perf power PMU, hwmon,
MSR, turbostat), whether a BMC answers `ipmitool dcmi power reading`, the NVIDIA power fields and
limits, and the clock-sync state.

Side effects: none, apart from a CPU burn of --busy-seconds (default 5 s; --no-load skips it) used
to prove that the counters advance, and `sudo -n` log entries when the probe asks root-only questions
(disable with --no-sudo). It never writes to sysfs, loads kernel modules, installs packages or
changes a setting. turbostat is run only if /dev/cpu/0/msr already exists (turbostat can try to
modprobe msr otherwise). Hostnames, IP addresses, serial numbers and UUIDs are not recorded
(--include-hostname adds the hostname).

Standard library only (Python >= 3.8). Linux only for real use; elsewhere sections come back empty.
--root DIR reads /sys, /proc, /dev and /etc below DIR and runs no commands (used by the tests).
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

SCHEMA = "probe_power/1"
OUTPUT_LIMIT = 8000                      # characters kept from any command output
EXTRA_PATH = ("/usr/sbin", "/sbin", "/usr/local/sbin", "/usr/local/bin")
BUSY_CODE = (
    "import sys, time\n"
    "end = time.monotonic() + float(sys.argv[1]); x = 1.0001\n"
    "while time.monotonic() < end:\n"
    "    for _ in range(100000):\n"
    "        x = x * 1.0000001 + 1e-9\n"
)
INTEL_MSRS = {"unit": 0x606, "pkg_energy": 0x611}          # MSR_RAPL_POWER_UNIT, MSR_PKG_ENERGY_STATUS
AMD_MSRS = {"unit": 0xC0010299, "pkg_energy": 0xC001029D}  # MSR_AMD_RAPL_POWER_UNIT, MSR_AMD_PKG_ENERGY_STATUS
REDACT = re.compile(r"^(\s*)(Serial Number|UUID|Asset Tag|SKU Number):.*$", re.I | re.M)
PCI_HEADER = re.compile(r"^GPU [0-9A-Fa-f]{4,8}:[0-9A-Fa-f:.]+\s*$", re.M)        # `nvidia-smi -q` per-GPU header (PCI bus id)
# asked even when `nvidia-smi --help-query-gpu` does not list them (a driver may accept what it does not list)
GPU_POWER_FIELDS = ("power.draw", "power.draw.average", "power.draw.instant", "power.limit", "power.default_limit",
                    "power.min_limit", "power.max_limit", "enforced.power.limit", "total_energy_consumption",
                    "module.power.draw", "module.power.draw.average", "module.power.draw.instant", "module.power.limit",
                    "module.power.default_limit", "module.power.min_limit", "module.power.max_limit")
GPU_BASE_FIELDS = ("name", "driver_version", "memory.total", "pstate", "persistence_mode", "temperature.gpu",
                   "clocks.sm", "clocks.max.sm", "clocks_throttle_reasons.active")


class Ctx:
    """Everything a collector needs: the filesystem root, command policy, sleeping and shared state."""

    def __init__(self, root="/", commands=True, sudo=True, sleep=time.sleep):
        self.root = root
        self.commands = commands
        self.sudo_enabled = sudo
        self.sleep = sleep
        self.euid = os.geteuid() if hasattr(os, "geteuid") else -1
        self._sudo_mode = None
        self.errors = []
        self.sources = []        # energy counters found, consumed by the counter sanity check
        self.gpu_binary = None
        self.lscpu = None
        self.nvml = None         # (library, [device handles]) once collect_nvml has initialised it
        self.perf_plan = None    # {"events": [...], "via_sudo": bool} once collect_perf found a working perf stat

    def p(self, path):
        return os.path.join(self.root, path.lstrip("/"))

    @property
    def sudo_mode(self):
        """root | passwordless | unavailable | disabled"""
        if self._sudo_mode is None:
            if self.euid == 0:
                self._sudo_mode = "root"
            elif not self.commands or not self.sudo_enabled:
                self._sudo_mode = "disabled"
            elif which(self, "sudo") and run_cmd(self, ["sudo", "-n", "true"], timeout=5).get("rc") == 0:
                self._sudo_mode = "passwordless"
            else:
                self._sudo_mode = "unavailable"
        return self._sudo_mode


# ---- low-level helpers --------------------------------------------------------------------------

def read_file(path):
    """Text of a file. Raises OSError (monkeypatched by the tests to simulate EACCES)."""
    with open(path, "r", errors="replace") as handle:
        return handle.read()


def read_text(path):
    try:
        return read_file(path).strip()
    except OSError:
        return None


def read_int(path):
    text = read_text(path)
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


def try_read(path):
    """{'readable': True, 'value': str} or {'readable': False, 'error': 'EACCES'}"""
    try:
        return {"readable": True, "value": read_file(path).strip()}
    except OSError as exc:
        return {"readable": False, "error": errno.errorcode.get(exc.errno, type(exc).__name__)}


def list_dir(path):
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def which(ctx, name):
    if not ctx.commands:
        return None
    found = shutil.which(name)
    if found:
        return found
    for directory in EXTRA_PATH:
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def run_cmd(ctx, argv, timeout=15, sudo=False):
    """Run a command and describe the outcome; never raises. sudo=True runs it as root if that is possible."""
    result = {"argv": list(argv)}
    if not ctx.commands:
        result["skipped"] = "commands disabled"
        return result
    full = list(argv)
    if sudo and ctx.euid != 0:
        if ctx.sudo_mode != "passwordless":
            result["skipped"] = "needs root; passwordless sudo is %s" % ctx.sudo_mode
            return result
        full = ["sudo", "-n"] + full
        result["sudo"] = True
    exe = which(ctx, full[0])
    if exe is None:
        result["error"] = "not found: %s" % full[0]
        return result
    full[0] = exe
    start = time.monotonic()
    try:
        done = subprocess.run(full, capture_output=True, text=True, timeout=timeout, errors="replace",
                              env=dict(os.environ, LC_ALL="C"))
        result.update(rc=done.returncode, stdout=done.stdout[:OUTPUT_LIMIT], stderr=done.stderr[:OUTPUT_LIMIT])
    except subprocess.TimeoutExpired:
        result["error"] = "timeout after %ss" % timeout
    except OSError as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
    result["seconds"] = round(time.monotonic() - start, 3)
    return result


def out(result):
    """stdout of a successful command, else None."""
    if result.get("rc") == 0:
        return result.get("stdout", "")
    return None


def say(message):
    print("[probe] " + message, file=sys.stderr, flush=True)


# ---- collectors ---------------------------------------------------------------------------------

def parse_cpuinfo(text):
    blocks, current = [], {}
    for line in text.splitlines():
        if not line.strip():
            if current:
                blocks.append(current)
                current = {}
        elif ":" in line:
            key, _, value = line.partition(":")
            current[key.strip().lower()] = value.strip()
    if current:
        blocks.append(current)
    return blocks


def lscpu_fields(ctx):
    """lscpu's 'Key: value' lines as a dict, run once; empty when lscpu is unavailable."""
    if ctx.lscpu is None:
        ctx.lscpu = parse_keyvalue(out(run_cmd(ctx, ["lscpu"], timeout=10)) or "")
    return ctx.lscpu


def cpu_topology(ctx):
    base = ctx.p("/sys/devices/system/cpu")
    rows = []
    for name in list_dir(base):
        if not re.fullmatch(r"cpu\d+", name):
            continue
        topo = os.path.join(base, name, "topology")
        package, core = read_int(os.path.join(topo, "physical_package_id")), read_int(os.path.join(topo, "core_id"))
        if package is not None and core is not None:      # offline CPUs have no topology directory
            rows.append((package, core))
    return rows


def collect_cpu(ctx):
    cpuinfo = parse_cpuinfo(read_text(ctx.p("/proc/cpuinfo")) or "")
    first = cpuinfo[0] if cpuinfo else {}
    rows = cpu_topology(ctx)
    flags = set((first.get("flags") or first.get("features") or "").split())
    numa = {}
    for name in list_dir(ctx.p("/sys/devices/system/node")):
        if re.fullmatch(r"node\d+", name):
            numa[name] = read_text(ctx.p("/sys/devices/system/node/%s/cpulist" % name))
    meminfo = read_text(ctx.p("/proc/meminfo")) or ""
    mem = re.search(r"^MemTotal:\s+(\d+) kB", meminfo, re.M)
    cores = len(set(rows))
    model = first.get("model name")
    if model in (None, "", "-"):                            # aarch64 /proc/cpuinfo has none, or a placeholder
        model = lscpu_fields(ctx).get("Model name") or None
    freq = ctx.p("/sys/devices/system/cpu/cpu0/cpufreq")
    pstate = ctx.p("/sys/devices/system/cpu/intel_pstate")
    smt = read_text(ctx.p("/sys/devices/system/cpu/smt/active"))
    return {
        "architecture": platform.machine() if ctx.root == "/" else None,
        "vendor": first.get("vendor_id") or first.get("cpu implementer"),
        "model_name": model,
        "family": first.get("cpu family"),
        "model": first.get("model"),
        "stepping": first.get("stepping"),
        "microcode": first.get("microcode"),
        "logical_cpus": len(rows) or len(cpuinfo) or None,
        "sockets": len({package for package, _ in rows}) or None,
        "physical_cores": cores or None,
        "threads_per_core": (len(rows) // cores) if cores and len(rows) % cores == 0 else None,
        "smt_active": None if smt is None else smt == "1",
        "numa_nodes": numa,
        "mem_total_gib": round(int(mem.group(1)) / 1048576, 2) if mem else None,
        "flags_of_interest": sorted(flags & {"hypervisor", "tsc_reliable", "constant_tsc", "nonstop_tsc",
                                              "aperfmperf", "hwp", "cpb", "amx_tile", "avx512f"}),
        "hypervisor_flag": ("hypervisor" in flags) if flags and "flags" in first else None,
        "frequency": {
            "scaling_driver": read_text(os.path.join(freq, "scaling_driver")),
            "scaling_governor": read_text(os.path.join(freq, "scaling_governor")),
            "scaling_min_khz": read_int(os.path.join(freq, "scaling_min_freq")),
            "scaling_max_khz": read_int(os.path.join(freq, "scaling_max_freq")),
            "cpuinfo_max_khz": read_int(os.path.join(freq, "cpuinfo_max_freq")),
            "intel_pstate_status": read_text(os.path.join(pstate, "status")),
            "intel_pstate_no_turbo": read_text(os.path.join(pstate, "no_turbo")),
            "boost": read_text(ctx.p("/sys/devices/system/cpu/cpufreq/boost")),
        },
    }


def parse_dmidecode(text):
    processors, current = [], None
    for line in text.splitlines():
        if line.startswith("Processor Information"):
            current = {}
            processors.append(current)
        elif line and not line.startswith((" ", "\t")):
            current = None
        elif current is not None and ":" in line:
            key, _, value = line.strip().partition(":")
            wanted = {"Socket Designation": "socket", "Version": "version", "Core Count": "core_count",
                      "Core Enabled": "core_enabled", "Thread Count": "thread_count",
                      "Max Speed": "max_speed", "Current Speed": "current_speed"}
            if key in wanted:
                current[wanted[key]] = value.strip()
    return processors


def collect_virtualization(ctx, cpu):
    dmi = {}
    for name in ("sys_vendor", "product_name", "bios_vendor", "bios_version", "board_vendor", "board_name",
                 "chassis_type"):
        dmi[name] = read_text(ctx.p("/sys/class/dmi/id/" + name))
    detect = run_cmd(ctx, ["systemd-detect-virt"], timeout=10)
    detected = (detect.get("stdout") or "").strip() if "rc" in detect else None
    if detected == "":
        detected = None
    sys_hv = read_text(ctx.p("/sys/hypervisor/type"))
    lscpu_hv = lscpu_fields(ctx).get("Hypervisor vendor")
    flag = cpu.get("hypervisor_flag")
    evidence = []
    if flag is not None:
        evidence.append("cpuinfo hypervisor flag %s" % ("set" if flag else "absent"))
    if detected is not None:
        evidence.append("systemd-detect-virt: %s" % detected)
    if sys_hv:
        evidence.append("/sys/hypervisor/type: %s" % sys_hv)
    if lscpu_hv:
        evidence.append("lscpu hypervisor vendor: %s" % lscpu_hv)
    if flag is True or (sys_hv and sys_hv != "none") or lscpu_hv:
        verdict = "virtualized"
    elif flag is False and (detected == "none" or (detected is None and dmi["sys_vendor"])):
        verdict = "bare_metal"
    elif flag is False and detected is None:
        # no DMI and no systemd-detect-virt: an emulator or a container looks the same as bare metal here
        verdict = "unknown"
        evidence.append("no DMI data and no systemd-detect-virt: cannot tell bare metal from emulation")
    elif flag is False:
        # e.g. EC2 .metal: DMI vendor says Amazon, the CPU reports no hypervisor
        verdict = "ambiguous"
        evidence.append("CPU shows no hypervisor but systemd-detect-virt disagrees; trust the RAPL checks")
    elif detected == "none":
        verdict = "bare_metal"
    elif detected:
        verdict = "virtualized"
    else:
        verdict = "unknown"
    dmidecode = run_cmd(ctx, ["dmidecode", "-t", "system", "-t", "processor", "-t", "38"], timeout=15, sudo=True)
    section = {"verdict": verdict, "evidence": evidence, "systemd_detect_virt": detected, "sys_hypervisor_type": sys_hv,
               "lscpu_hypervisor_vendor": lscpu_hv, "dmi": dmi}
    if "rc" in dmidecode:
        text = REDACT.sub(r"\1\2: <redacted>", dmidecode.get("stdout", ""))
        section["dmidecode"] = {"rc": dmidecode["rc"], "processors": parse_dmidecode(text),
                                "ipmi_device_record": "IPMI Device Information" in text}
    else:
        section["dmidecode"] = {k: v for k, v in dmidecode.items() if k in ("skipped", "error")}
    return section


def collect_powercap(ctx):
    base = ctx.p("/sys/class/powercap")
    modules = {name: os.path.isdir(ctx.p("/sys/module/" + name))
               for name in ("intel_rapl_common", "intel_rapl_msr", "intel_rapl_mmio", "rapl", "amd_energy")}
    zones, root_state = [], ctx.sudo_mode
    for entry in list_dir(base):
        match = re.fullmatch(r"(intel-rapl(?:-mmio)?):(\d+(?::\d+)*)", entry)
        directory = os.path.join(base, entry)
        if not match or not os.path.isdir(directory):
            continue
        energy_path = os.path.join(directory, "energy_uj")
        record = {"id": entry, "control_type": match.group(1), "depth": match.group(2).count(":"),
                  "name": read_text(os.path.join(directory, "name"))}
        if os.path.exists(energy_path):
            status = os.stat(energy_path)
            reading = try_read(energy_path)
            record["energy_uj"] = {"mode": "%04o" % (status.st_mode & 0o7777), "owner_uid": status.st_uid,
                                   "readable_as_user": reading["readable"]}
            if reading["readable"]:
                record["energy_uj"]["sample"] = reading["value"]
            else:
                record["energy_uj"]["error"] = reading["error"]
            if ctx.euid == 0:
                record["energy_uj"]["readable_as_root"] = reading["readable"]
            elif not reading["readable"]:
                as_root = run_cmd(ctx, ["cat", energy_path], timeout=5, sudo=True)
                record["energy_uj"]["readable_as_root"] = (as_root.get("rc") == 0) if "rc" in as_root else None
            else:
                record["energy_uj"]["readable_as_root"] = True
        else:
            record["energy_uj"] = None
        record["max_energy_range_uj"] = read_int(os.path.join(directory, "max_energy_range_uj"))
        record["enabled"] = read_text(os.path.join(directory, "enabled"))
        constraints = []
        for index in range(4):
            cname = read_text(os.path.join(directory, "constraint_%d_name" % index))
            if cname is None:
                continue
            constraints.append({"name": cname,
                                "power_limit_uw": read_int(os.path.join(directory, "constraint_%d_power_limit_uw" % index)),
                                "time_window_us": read_int(os.path.join(directory, "constraint_%d_time_window_us" % index)),
                                "max_power_uw": read_int(os.path.join(directory, "constraint_%d_max_power_uw" % index))})
        record["constraints"] = constraints
        zones.append(record)
        if record["energy_uj"] and (record["energy_uj"]["readable_as_user"] or record["energy_uj"].get("readable_as_root")):
            ctx.sources.append({"id": "powercap:%s:%s" % (entry, record["name"]), "kind": "powercap", "path": energy_path,
                                "via_sudo": not record["energy_uj"]["readable_as_user"], "to_joules": 1e-6,
                                "max_range": record["max_energy_range_uj"]})
    named = [z for z in zones if (z["name"] or "").startswith("package")]
    # MSR-based zones are the reference; an MMIO zone duplicates the same package counter
    packages = [z for z in named if z["control_type"] == "intel-rapl"] or named
    return {
        "sysfs_present": os.path.isdir(base),
        "kernel_modules_loaded": modules,
        "zones": zones,
        "domains": sorted({z["name"] for z in zones if z["name"]}),
        "package_zones": len(packages),
        "all_packages_readable_as_user": bool(packages) and all((z["energy_uj"] or {}).get("readable_as_user") for z in packages),
        "all_packages_readable_as_root": bool(packages) and all((z["energy_uj"] or {}).get("readable_as_root") for z in packages),
        "sudo": root_state,
    }


def collect_hwmon(ctx):
    base = ctx.p("/sys/class/hwmon")
    devices = []
    for entry in list_dir(base):
        directory = os.path.join(base, entry)
        files = list_dir(directory)
        energy = [f for f in files if re.fullmatch(r"energy\d+_input", f)]
        power = [f for f in files if re.fullmatch(r"power\d+_(?:average|input|cap)", f)]
        record = {"id": entry, "name": read_text(os.path.join(directory, "name")), "energy": [], "power": []}
        for kind, names in (("energy", energy), ("power", power)):
            for fname in names:
                label = read_text(os.path.join(directory, fname.split("_")[0] + "_label"))
                reading = try_read(os.path.join(directory, fname))
                item = {"file": fname, "label": label, "readable_as_user": reading["readable"]}
                item["value" if reading["readable"] else "error"] = reading.get("value", reading.get("error"))
                record[kind].append(item)
                if kind == "energy" and reading["readable"]:
                    ctx.sources.append({"id": "hwmon:%s:%s:%s" % (entry, record["name"], fname), "kind": "hwmon",
                                        "path": os.path.join(directory, fname), "via_sudo": False, "to_joules": 1e-6,
                                        "max_range": None})
        devices.append(record)
    return {"all_names": [d["name"] for d in devices],
            "devices_with_energy_or_power": [d for d in devices if d["energy"] or d["power"]]}


def parse_perf_stat(text):
    values = {}
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) >= 3 and parts[2].startswith("power/"):
            item = {"value": parts[0], "unit": parts[1] or None}
            if len(parts) >= 4 and parts[3].isdigit():
                item["runtime_s"] = int(parts[3]) / 1e9              # time the counter was enabled
            values[parts[2]] = item
    return values


def perf_argv(event_names, seconds):
    return ["perf", "stat", "-a", "-x,", "-e", ",".join("power/%s/" % name for name in event_names), "--", "sleep", "%g" % seconds]


def perf_counts(events):
    """True if any power event reported a positive reading. A VM can expose the event and count nothing
    ('0.00 Joules', '<not counted>', '<not supported>'); that is not a usable source."""
    for item in events.values():
        try:
            if float(item["value"]) > 0:
                return True
        except ValueError:
            continue
    return False


def perf_result(result):
    events = parse_perf_stat(result.get("stderr", ""))
    error = result.get("error") or (result.get("stderr", "")[:300] if result.get("rc") else None)
    return {"rc": result.get("rc"), "events": events, "counts": perf_counts(events), "error": error}


def collect_perf(ctx):
    devices_dir = ctx.p("/sys/bus/event_source/devices")
    devices = list_dir(devices_dir)
    events = {}
    events_dir = os.path.join(devices_dir, "power", "events")
    for name in list_dir(events_dir):
        if name.endswith((".scale", ".unit")):
            continue
        events[name] = {"config": read_text(os.path.join(events_dir, name)),
                        "scale": read_text(os.path.join(events_dir, name + ".scale")),
                        "unit": read_text(os.path.join(events_dir, name + ".unit"))}
    section = {"perf_event_paranoid": read_int(ctx.p("/proc/sys/kernel/perf_event_paranoid")),
               "pmu_devices": devices,
               "cpu_pmu_present": any(re.match(r"(cpu|cpu_core|cpu_atom|armv\d.*)$", d) for d in devices),
               "power_pmu_present": "power" in devices,
               "power_events": events,
               "perf_binary": which(ctx, "perf")}
    energy_events = [name for name in events if name.startswith("energy-")]
    if section["perf_binary"] and energy_events:
        argv = perf_argv(energy_events, 1)
        section["perf_stat_as_user"] = perf_result(run_cmd(ctx, argv, timeout=20))
        if not section["perf_stat_as_user"]["counts"] and ctx.sudo_mode in ("passwordless", "root") and ctx.euid != 0:
            section["perf_stat_as_root"] = perf_result(run_cmd(ctx, argv, timeout=20, sudo=True))
        # the sanity check re-measures these events under load, as root if only root can run perf at all
        if section["perf_stat_as_user"]["rc"] == 0:
            ctx.perf_plan = {"events": energy_events, "via_sudo": False}
        elif (section.get("perf_stat_as_root") or {}).get("rc") == 0:
            ctx.perf_plan = {"events": energy_events, "via_sudo": True}
    return section


def read_msr(path, register):
    fd = os.open(path, os.O_RDONLY)
    try:
        return int.from_bytes(os.pread(fd, 8, register), "little")
    finally:
        os.close(fd)


def collect_msr(ctx, cpu):
    device = ctx.p("/dev/cpu/0/msr")
    section = {"device_present": os.path.exists(device), "module_loaded": os.path.isdir(ctx.p("/sys/module/msr")),
               "readable_as_user": os.access(device, os.R_OK) if os.path.exists(device) else False}
    if not section["readable_as_user"]:
        return section
    registers = AMD_MSRS if (cpu.get("vendor") or "").startswith("Authentic") else INTEL_MSRS
    try:
        unit = read_msr(device, registers["unit"])
        first = read_msr(device, registers["pkg_energy"])
        ctx.sleep(0.5)
        second = read_msr(device, registers["pkg_energy"])
    except OSError as exc:
        section["error"] = errno.errorcode.get(exc.errno, type(exc).__name__)
        return section
    energy_unit_bits = (unit >> 8) & 0x1F
    section.update(vendor_registers="amd" if registers is AMD_MSRS else "intel", energy_unit_joules=1.0 / (1 << energy_unit_bits),
                   pkg_energy_raw=[first & 0xFFFFFFFF, second & 0xFFFFFFFF],
                   pkg_energy_advanced=(first & 0xFFFFFFFF) != (second & 0xFFFFFFFF))
    return section


def collect_turbostat(ctx):
    path = which(ctx, "turbostat")
    section = {"binary": path, "msr_device_present": os.path.exists(ctx.p("/dev/cpu/0/msr"))}
    if not path:
        section["skipped"] = "turbostat not installed (linux-tools); setup_h100.sh --install-power-tools installs it"
    elif not section["msr_device_present"]:
        section["skipped"] = "/dev/cpu/0/msr absent; the probe does not load kernel modules (turbostat may try to modprobe msr)"
    else:
        result = run_cmd(ctx, ["turbostat", "--quiet", "--Summary", "--show", "Avg_MHz,Busy%,PkgWatt,CorWatt,RAMWatt,PkgTmp",
                               "sleep", "1"], timeout=30, sudo=True)
        section.update(rc=result.get("rc"), stdout=(result.get("stdout") or "")[-2000:],
                       stderr=(result.get("stderr") or "")[-600:], skipped=result.get("skipped"), error=result.get("error"))
    return {k: v for k, v in section.items() if v is not None}


def collect_ipmi(ctx):
    devices = [p for p in ("/dev/ipmi0", "/dev/ipmi/0", "/dev/ipmidev/0") if os.path.exists(ctx.p(p))]
    section = {"binary": which(ctx, "ipmitool"), "devices": devices,
               "modules_loaded": {m: os.path.isdir(ctx.p("/sys/module/" + m)) for m in ("ipmi_devintf", "ipmi_si", "ipmi_msghandler")}}
    if not devices:
        section["skipped"] = "no /dev/ipmi* device (no in-band BMC path); the probe does not load kernel modules"
    elif not section["binary"]:
        section["skipped"] = "ipmitool not installed; setup_h100.sh --install-power-tools installs it"
    else:
        reading = run_cmd(ctx, ["ipmitool", "dcmi", "power", "reading"], timeout=15, sudo=True)
        info = run_cmd(ctx, ["ipmitool", "mc", "info"], timeout=15, sudo=True)
        text = reading.get("stdout") or ""
        watts = re.search(r"Instantaneous power reading:\s+(\d+)\s+Watts", text)
        section["dcmi_power_reading"] = {"rc": reading.get("rc"), "watts": int(watts.group(1)) if watts else None,
                                         "stdout": text[:1500], "stderr": (reading.get("stderr") or "")[:300],
                                         "skipped": reading.get("skipped"), "error": reading.get("error")}
        section["mc_info_rc"] = info.get("rc")
    return section


def nvidia_fields(ctx, binary):
    """Every field name `nvidia-smi --help-query-gpu` lists (including aliases)."""
    help_text = out(run_cmd(ctx, [binary, "--help-query-gpu"], timeout=15)) or ""
    names = []
    for line in help_text.splitlines():
        if line.strip().startswith('"'):
            names.extend(re.findall(r'"([A-Za-z0-9_.]+)"', line))
    return names


def nvidia_query(ctx, binary, field):
    result = run_cmd(ctx, [binary, "--query-gpu=%s" % field, "--format=csv,noheader,nounits"], timeout=15)
    text = out(result)
    return None if text is None else [line.strip() for line in text.splitlines() if line.strip()]


def parse_smi_power(text):
    """`nvidia-smi -q -d POWER` -> {'gpu0': {'GPU Power Readings': {'Average Power Draw': '71.16 W', ...}, ...}, ...}"""
    gpus, gpu, section = {}, None, None
    for line in text.splitlines():
        if re.match(r"^GPU [0-9A-Fa-f]{4,8}:\S+|^GPU <", line):
            gpu = gpus.setdefault("gpu%d" % len(gpus), {})
            section = None
        elif gpu is not None and re.match(r"^ {4}\S", line):
            if ":" in line:
                key, _, value = line.strip().partition(":")
                gpu.setdefault("", {})[key.strip()] = value.strip()
            else:
                section = gpu.setdefault(line.strip(), {})
        elif gpu is not None and section is not None and ":" in line:
            key, _, value = line.strip().partition(":")
            section[key.strip()] = value.strip()
    return gpus


SMI_LIMIT_KEYS = {"Current Power Limit": "power.limit", "Default Power Limit": "power.default_limit",
                  "Min Power Limit": "power.min_limit", "Max Power Limit": "power.max_limit",
                  "Enforced Power Limit": "enforced.power.limit"}


def limits_from_smi_q(parsed):
    """{'power.limit': ['700.00', ...], ...} from the parsed `nvidia-smi -q -d POWER` text (watts, one entry per GPU)."""
    limits = {}
    for gpu in (parsed or {}).values():
        for section in gpu.values():
            for key, field in SMI_LIMIT_KEYS.items():
                match = re.match(r"([0-9.]+) W$", section.get(key, ""))
                if match:
                    limits.setdefault(field, []).append(match.group(1))
    return limits


def limits_from_nvml(nvml):
    names = {"current": "power.limit", "default": "power.default_limit", "min": "power.min_limit", "max": "power.max_limit",
             "enforced": "enforced.power.limit"}
    limits = {}
    for device in (nvml or {}).get("devices", []):
        for key, field in names.items():
            value = (device.get("power_limit_w") or {}).get(key)
            if value is not None:
                limits.setdefault(field, []).append("%.2f" % value)
    return limits


def collect_gpu(ctx):
    binary = which(ctx, "nvidia-smi")
    if not binary:
        return {"present": False}
    available = nvidia_fields(ctx, binary)
    discovered = [f for f in available if re.search(r"power|energy", f)]
    wanted = list(dict.fromkeys(list(GPU_BASE_FIELDS) + list(GPU_POWER_FIELDS) + discovered))
    per_field, unsupported = {}, []
    for field in wanted:
        values = nvidia_query(ctx, binary, field)
        if values is None or all(v.startswith("[") or v == "N/A" for v in values):
            unsupported.append(field)
        else:
            per_field[field] = values
    ctx.gpu_binary = binary
    section = {"present": True, "binary": binary, "fields_listed_by_driver": len(available), "fields_listed": available,
               "per_field": per_field, "unsupported_or_na": unsupported, "gpu_count": len(per_field.get("name", []))}
    limits = {k: per_field[k] for k in ("power.limit", "power.default_limit", "power.min_limit", "power.max_limit",
                                       "enforced.power.limit") if k in per_field}
    section["power_limits_w"] = limits
    section["energy_counter_available"] = "total_energy_consumption" in per_field
    power_q = run_cmd(ctx, [binary, "-q", "-d", "POWER"], timeout=20)
    if power_q.get("rc") == 0:
        section["nvidia_smi_q_power"] = parse_smi_power(power_q.get("stdout") or "")
        section["nvidia_smi_q_power_raw"] = PCI_HEADER.sub("GPU <pci bus id redacted>", (power_q.get("stdout") or "")[:6000])
    else:
        section["nvidia_smi_q_power"] = None
    section["power_limits_source"] = "nvidia-smi --query-gpu" if limits else None
    if not limits and section["nvidia_smi_q_power"]:
        section["power_limits_w"] = limits_from_smi_q(section["nvidia_smi_q_power"])
        section["power_limits_source"] = "nvidia-smi -q -d POWER" if section["power_limits_w"] else None
    if section["energy_counter_available"]:
        for index in range(section["gpu_count"]):
            ctx.sources.append({"id": "nvml_smi:gpu%d:total_energy_consumption" % index, "kind": "nvml_smi", "index": index,
                                "via_sudo": False, "to_joules": 1e-3, "max_range": None})
    return section


def load_nvml():
    for name in ("libnvidia-ml.so.1", "libnvidia-ml.so"):
        try:
            return ctypes.CDLL(name), name
        except OSError:
            continue
    return None, None


def nvml_call(lib, name, *args):
    """rc of lib.<name>(*args); a missing symbol counts as 'not found' (-1000)."""
    function = getattr(lib, name, None)
    return -1000 if function is None else function(*args)


def nvml_text(lib, name, *prefix):
    buffer = ctypes.create_string_buffer(128)
    rc = nvml_call(lib, name, *prefix, buffer, ctypes.c_uint(128))
    return buffer.value.decode(errors="replace") if rc == 0 else None


def nvml_uint(lib, name, handle, kind=ctypes.c_uint):
    value = kind(0)
    rc = nvml_call(lib, name, handle, ctypes.pointer(value))
    return value.value if rc == 0 else None


def collect_nvml(ctx):
    """NVML through ctypes: nvidia-smi cannot query the cumulative energy counter, the library can."""
    if not ctx.commands:
        return {"skipped": "commands disabled"}
    lib, name = load_nvml()
    if lib is None:
        return {"present": False}
    init = nvml_call(lib, "nvmlInit_v2")
    section = {"present": True, "library": name, "init_rc": init, "devices": [], "energy_counter_available": False}
    if init != 0:
        return section
    count = ctypes.c_uint(0)
    if nvml_call(lib, "nvmlDeviceGetCount_v2", ctypes.pointer(count)) != 0:
        return section
    section["driver_version"] = nvml_text(lib, "nvmlSystemGetDriverVersion")
    handles = []
    for index in range(count.value):
        handle = ctypes.c_void_p()
        if nvml_call(lib, "nvmlDeviceGetHandleByIndex_v2", ctypes.c_uint(index), ctypes.pointer(handle)) != 0:
            continue
        handles.append(handle)
        energy = nvml_uint(lib, "nvmlDeviceGetTotalEnergyConsumption", handle, ctypes.c_ulonglong)
        low, high = ctypes.c_uint(0), ctypes.c_uint(0)
        constraints = nvml_call(lib, "nvmlDeviceGetPowerManagementLimitConstraints", handle, ctypes.pointer(low), ctypes.pointer(high)) == 0
        milli = lambda value: None if value is None else round(value / 1000.0, 3)
        section["devices"].append({
            "index": index, "name": nvml_text(lib, "nvmlDeviceGetName", handle),
            "total_energy_mj": energy, "power_usage_w": milli(nvml_uint(lib, "nvmlDeviceGetPowerUsage", handle)),
            "power_limit_w": {"current": milli(nvml_uint(lib, "nvmlDeviceGetPowerManagementLimit", handle)),
                              "enforced": milli(nvml_uint(lib, "nvmlDeviceGetEnforcedPowerLimit", handle)),
                              "default": milli(nvml_uint(lib, "nvmlDeviceGetPowerManagementDefaultLimit", handle)),
                              "min": milli(low.value) if constraints else None, "max": milli(high.value) if constraints else None}})
        if energy is not None:
            section["energy_counter_available"] = True
            ctx.sources.append({"id": "nvml:gpu%d:total_energy_consumption" % index, "kind": "nvml", "index": index,
                                "via_sudo": False, "to_joules": 1e-3, "max_range": None})
    if section["energy_counter_available"]:                    # the library reads it directly: drop the nvidia-smi duplicates
        ctx.sources[:] = [s for s in ctx.sources if s["kind"] != "nvml_smi"]
    ctx.nvml = (lib, handles)
    return section


def parse_keyvalue(text, separator=":"):
    pairs = {}
    for line in text.splitlines():
        if separator in line:
            key, _, value = line.partition(separator)
            pairs[key.strip()] = value.strip()
    return pairs


def collect_clock(ctx):
    section = {"clocksource": read_text(ctx.p("/sys/devices/system/clocksource/clocksource0/current_clocksource")),
               "clocksource_available": read_text(ctx.p("/sys/devices/system/clocksource/clocksource0/available_clocksource")),
               "ptp_devices": [d for d in list_dir(ctx.p("/dev")) if re.fullmatch(r"ptp\d+|ptp_\w+", d)]}
    chrony = run_cmd(ctx, ["chronyc", "tracking"], timeout=10)
    synchronized, offset = None, None
    if out(chrony) is not None:
        fields = parse_keyvalue(chrony["stdout"])
        sysmatch = re.match(r"([0-9.]+) seconds (fast|slow)", fields.get("System time", ""))
        offset = float(sysmatch.group(1)) * (1 if sysmatch.group(2) == "fast" else -1) if sysmatch else None
        synchronized = fields.get("Leap status") == "Normal" and not fields.get("Reference ID", "").startswith("00000000")
        section["chrony"] = {k: fields.get(k) for k in ("Reference ID", "Stratum", "System time", "Last offset", "RMS offset",
                                                         "Root delay", "Root dispersion", "Update interval", "Leap status")}
        sources = run_cmd(ctx, ["chronyc", "-n", "sources"], timeout=10)
        section["chrony_sources"] = (out(sources) or "").splitlines()[:12]
    else:
        section["chrony"] = {k: v for k, v in chrony.items() if k in ("skipped", "error", "rc")}
    timedatectl = run_cmd(ctx, ["timedatectl", "show", "--property=NTP,NTPSynchronized,Timezone"], timeout=10)
    if out(timedatectl) is not None:
        fields = parse_keyvalue(timedatectl["stdout"], "=")
        section["timedatectl"] = fields
        if synchronized is None and "NTPSynchronized" in fields:
            synchronized = fields["NTPSynchronized"] == "yes"
    else:
        section["timedatectl"] = {k: v for k, v in timedatectl.items() if k in ("skipped", "error", "rc")}
    section["synchronized"] = synchronized
    section["chrony_system_time_offset_s"] = offset
    return section


# ---- lease (name, owner, expiry) -----------------------------------------------------------------

LEASE_NAME = re.compile(r"^p3-(?P<purpose>[a-z0-9]+(?:-[a-z0-9]+)*)-owner-(?P<owner>[a-z0-9]+)-expires-(?P<expires>\d{8}[Tt]\d{4}[Zz])$")
LEASE_FILE = "/etc/paper3-lease"


def lease_status(name, now=None, min_minutes=0):
    """Check an instance name against the launch convention `p3-<purpose>-owner-<id>-expires-<YYYYMMDDTHHMMZ>`
    (lower case apart from the T and Z of the expiry, which may be lower case too because GCP instance names and
    label values must be: p3-probe-h100-owner-ejiod-expires-20261111T1807Z, p3-cpu-c3-owner-ejiod-expires-20261111t1807z).
    valid means: the name matches and more than `min_minutes` remain before the expiry. Reusable by campaign scripts
    that must refuse to start without a lease."""
    result = {"name": name, "valid": False, "problem": None, "purpose": None, "owner": None, "expires_utc": None,
              "minutes_left": None}
    match = LEASE_NAME.match(name or "")
    if not match:
        result["problem"] = "name does not match p3-<purpose>-owner-<id>-expires-<YYYYMMDDTHHMMZ>"
        return result
    try:
        expires = datetime.strptime(match.group("expires").upper(), "%Y%m%dT%H%MZ").replace(tzinfo=timezone.utc)
    except ValueError:
        result["problem"] = "expiry is not a valid UTC time"
        return result
    now = now or datetime.now(timezone.utc)
    left = round((expires - now).total_seconds() / 60.0, 1)
    result.update(purpose=match.group("purpose"), owner=match.group("owner"), expires_utc=expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  minutes_left=left)
    if left <= min_minutes:
        result["problem"] = "expired" if left <= 0 else "less than %s minutes left" % min_minutes
    else:
        result["valid"] = True
    return result


def collect_lease(ctx, name):
    source = "--lease-name"
    if not name:
        lines = (read_text(ctx.p(LEASE_FILE)) or "").splitlines()
        name, source = (lines[0].strip() if lines else None), LEASE_FILE
    if not name:
        return {"present": False, "note": "no lease: pass --lease-name or write the instance name to %s at launch" % LEASE_FILE}
    return dict(lease_status(name), present=True, source=source)


# ---- counter sanity check -------------------------------------------------------------------------

def read_sources(ctx, sources):
    """{id: counter in its native unit or None} for every source, one sudo call for all root-only files."""
    values = {s["id"]: None for s in sources}
    file_kinds = ("powercap", "hwmon")
    sudo_files = [s for s in sources if s["kind"] in file_kinds and s["via_sudo"]]
    for source in sources:
        if source["kind"] in file_kinds and not source["via_sudo"]:
            text = read_text(source["path"])
            try:
                values[source["id"]] = int(text) if text is not None else None
            except ValueError:
                pass
    if sudo_files:
        script = 'for f in "$@"; do cat "$f" 2>/dev/null || echo ERR; done'
        result = run_cmd(ctx, ["sh", "-c", script, "sh"] + [s["path"] for s in sudo_files], timeout=20, sudo=True)
        lines = (result.get("stdout") or "").split()
        if result.get("rc") == 0 and len(lines) == len(sudo_files):
            for source, line in zip(sudo_files, lines):
                values[source["id"]] = int(line) if line.isdigit() else None
    smi = [s for s in sources if s["kind"] == "nvml_smi"]
    if smi and ctx.gpu_binary:
        reading = nvidia_query(ctx, ctx.gpu_binary, "total_energy_consumption") or []
        for source in smi:
            if source["index"] < len(reading) and reading[source["index"]].isdigit():
                values[source["id"]] = int(reading[source["index"]])
    for source in sources:
        if source["kind"] == "nvml" and ctx.nvml and source["index"] < len(ctx.nvml[1]):
            values[source["id"]] = nvml_uint(ctx.nvml[0], "nvmlDeviceGetTotalEnergyConsumption", ctx.nvml[1][source["index"]],
                                             ctypes.c_ulonglong)
    return values


def window(source, before, after, seconds):
    if before is None or after is None or seconds <= 0:
        return None
    delta = after - before
    if delta < 0 and source.get("max_range"):
        delta += source["max_range"]
    joules = delta * source["to_joules"]
    return {"seconds": round(seconds, 3), "delta_native": delta, "delta_j": round(joules, 6),
            "avg_w": round(joules / seconds, 3)}


def busy_load(ctx, seconds):
    """Burn every allowed CPU for `seconds`; returns the number of workers."""
    count = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    count = min(count, 512)
    workers = [subprocess.Popen([sys.executable, "-c", BUSY_CODE, str(seconds)], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL) for _ in range(count)]
    for worker in workers:
        try:
            worker.wait(timeout=seconds + 15)
        except subprocess.TimeoutExpired:
            worker.kill()
    return count


def perf_window(ctx, seconds):
    """Joules per power event over a `perf stat -a ... sleep seconds` window (None where perf printed no number)."""
    plan = ctx.perf_plan
    result = run_cmd(ctx, perf_argv(plan["events"], seconds), timeout=seconds + 30, sudo=plan["via_sudo"])
    joules = {}
    for event, item in parse_perf_stat(result.get("stderr", "")).items():
        try:
            joules[event] = (float(item["value"]), item.get("runtime_s") or seconds)
        except ValueError:
            joules[event] = (None, item.get("runtime_s") or seconds)
    return joules


def ramp_seconds():
    """Time for the burn workers to all be running; the perf busy window starts after it."""
    count = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    return min(5.0, 0.5 + 0.025 * count)


def perf_entry(event, idle, busy, via_sudo):
    def joules(measured):
        if measured is None or measured[0] is None:
            return None
        value, seconds = measured
        return {"seconds": round(seconds, 3), "delta_native": value, "delta_j": round(value, 6),
                "avg_w": round(value / seconds, 3) if seconds > 0 else None}
    idle_w, busy_w = joules(idle), joules(busy)
    entry = {"id": "perf:" + event, "kind": "perf", "read_via": "sudo" if via_sudo else "direct", "idle": idle_w, "busy": busy_w,
             "advanced": any(w is not None and w["delta_native"] > 0 for w in (idle_w, busy_w))}
    if busy_w and idle_w and idle_w["avg_w"]:
        entry["busy_over_idle"] = round(busy_w["avg_w"] / idle_w["avg_w"], 2)
    return entry


def collect_counter_sanity(ctx, idle_seconds, busy_seconds, load_fn):
    sources, plan = ctx.sources, ctx.perf_plan
    section = {"idle_seconds": idle_seconds, "busy_seconds": busy_seconds, "sources": []}
    if not sources and not plan:
        section["note"] = "no readable energy counter found (user or root)"
        return section
    t0, before = time.monotonic(), read_sources(ctx, sources)
    perf_idle = perf_busy = None
    if plan and idle_seconds > 0:
        perf_idle = perf_window(ctx, idle_seconds)                # perf's own `sleep` is the idle wait
    else:
        ctx.sleep(idle_seconds)
    t1, mid = time.monotonic(), read_sources(ctx, sources)
    after, t2 = None, t1
    if busy_seconds > 0:
        perf_thread, ramp = None, 0.0
        if plan:
            ramp, perf_result_box = ramp_seconds(), {}

            def measure():
                ctx.sleep(ramp)
                perf_result_box.update(perf_window(ctx, busy_seconds))

            perf_thread = threading.Thread(target=measure)
            perf_thread.start()
        section["load_workers"] = load_fn(ctx, busy_seconds + ramp)
        if perf_thread:
            perf_thread.join()
            perf_busy = perf_result_box
        t2, after = time.monotonic(), read_sources(ctx, sources)
    for source in sources:
        idle = window(source, before[source["id"]], mid[source["id"]], t1 - t0)
        busy = window(source, mid[source["id"]], after[source["id"]], t2 - t1) if after is not None else None
        entry = {"id": source["id"], "kind": source["kind"], "read_via": "sudo" if source["via_sudo"] else "direct",
                 "idle": idle, "busy": busy,
                 "advanced": any(w is not None and w["delta_native"] > 0 for w in (idle, busy))}
        if busy and idle and idle["avg_w"] > 0:
            entry["busy_over_idle"] = round(busy["avg_w"] / idle["avg_w"], 2)
        section["sources"].append(entry)
    if plan:
        for event in sorted(set(perf_idle or {}) | set(perf_busy or {})):
            section["sources"].append(perf_entry(event, (perf_idle or {}).get(event), (perf_busy or {}).get(event), plan["via_sudo"]))
    return section


# ---- summary ------------------------------------------------------------------------------------

def summarize(report):
    cpu, virt, pcap = report["cpu"], report["virtualization"], report["powercap"]
    sanity = {s["id"]: s for s in report["counter_sanity"].get("sources", [])}

    def advanced(prefix):
        hits = [s["advanced"] for key, s in sanity.items() if key.startswith(prefix)]
        return any(hits) if hits else None

    packages = [z for z in pcap["zones"] if (z["name"] or "").startswith("package") and z["control_type"] == "intel-rapl"]
    hwmon_energy = [item for dev in report["hwmon"]["devices_with_energy_or_power"] for item in dev["energy"]]
    msr = report["msr"]
    perf = report["perf"]
    perf_user = bool((perf.get("perf_stat_as_user") or {}).get("counts"))
    perf_root = bool((perf.get("perf_stat_as_root") or {}).get("counts"))
    perf_sanity = advanced("perf:")
    if perf_sanity is False:                       # present, runs, reports numbers, but does not advance under load
        perf_user = perf_root = False
    candidates = [
        {"source": "powercap", "present": bool(packages), "domains": pcap["domains"],
         "readable_as_user": pcap["all_packages_readable_as_user"],
         "readable_as_root": pcap["all_packages_readable_as_root"], "counters_advance": advanced("powercap:")},
        {"source": "hwmon", "present": bool(hwmon_energy), "readable_as_user": bool(hwmon_energy) and all(i["readable_as_user"] for i in hwmon_energy),
         "readable_as_root": None, "counters_advance": advanced("hwmon:")},
        {"source": "perf_power_pmu", "present": bool(perf["power_events"]), "readable_as_user": perf_user,
         "readable_as_root": perf_root or perf_user or None,
         "counters_advance": perf_sanity if perf_sanity is not None else (True if (perf_user or perf_root) else None)},
        {"source": "msr", "present": msr["device_present"], "readable_as_user": msr["readable_as_user"],
         "readable_as_root": None, "counters_advance": msr.get("pkg_energy_advanced")},
    ]
    chosen = next((c for c in candidates if c["present"] and c["readable_as_user"]), None)
    needs_root = False
    if chosen is None:
        chosen = next((c for c in candidates if c["present"] and c["readable_as_root"]), None)
        needs_root = chosen is not None
    gpu = report["gpu"]
    nvml = report.get("nvml") or {}
    gpu_energy = bool(gpu.get("energy_counter_available") or nvml.get("energy_counter_available"))
    gpu_limits, gpu_limits_source = gpu.get("power_limits_w") or {}, gpu.get("power_limits_source")
    if not gpu_limits and nvml.get("devices"):
        gpu_limits = limits_from_nvml(nvml)
        gpu_limits_source = "nvml" if gpu_limits else None
    ipmi_watts = (report["ipmi"].get("dcmi_power_reading") or {}).get("watts")
    notes = []
    lease = report.get("lease") or {}
    if lease.get("present") is False:
        notes.append("no lease recorded (instance name with owner and expiry): see docs/PAPER3_INFRA.md section 5")
    elif lease.get("present") and not lease.get("valid"):
        notes.append("lease problem: %s" % lease.get("problem"))
    perf_ran = [r for r in (perf.get("perf_stat_as_user"), perf.get("perf_stat_as_root")) if r and r.get("rc") == 0]
    if perf["power_events"] and perf_ran and not perf_user and not perf_root:
        notes.append("the power PMU exposes %s but counted nothing%s: emulated or non-functional (typical for a VM), not a source"
                     % (", ".join(sorted(perf["power_events"])), " under load" if perf_sanity is False else ""))
    if perf.get("perf_event_paranoid") is not None and perf["perf_event_paranoid"] > 2 and perf["power_events"]:
        notes.append("kernel.perf_event_paranoid=%d: perf needs root here" % perf["perf_event_paranoid"])
    if chosen is None:
        notes.append("no CPU energy source is readable (user or root)%s: CPU energy can only be modelled, not measured"
                     % (" on this virtualized host" if virt["verdict"] == "virtualized" else ""))
    if needs_root:
        notes.append("CPU energy is readable only as root: run setup_h100.sh --enable-rapl-read (single-tenant hosts only)")
    if chosen and chosen["counters_advance"] is False:
        notes.append("the chosen counter did not advance in the sanity window: treat it as emulated or stuck")
    if packages and cpu.get("sockets") and len(packages) != cpu["sockets"]:
        notes.append("package zones (%d) differ from sockets seen by the guest (%s): the counter may cover other sockets" % (len(packages), cpu["sockets"]))
    if gpu.get("present") and not gpu_energy:
        notes.append("neither nvidia-smi nor NVML reports total energy: integrate power.draw samples instead")
    return {
        "virtualization": virt["verdict"],
        "cpu_model": cpu.get("model_name"),
        "sockets": cpu.get("sockets"),
        "physical_cores": cpu.get("physical_cores"),
        "smt_active": cpu.get("smt_active"),
        "cpu_energy": {"best_source": chosen["source"] if chosen else "none",
                       "readable_as_user": bool(chosen and chosen["readable_as_user"]),
                       "needs_root": needs_root,
                       "counters_advance": chosen["counters_advance"] if chosen else None,
                       "rapl_domains": pcap["domains"], "candidates": candidates},
        "bmc_power_watts": ipmi_watts,
        "bmc_power_available": ipmi_watts is not None,
        "gpu_present": bool(gpu.get("present")),
        "gpu_count": gpu.get("gpu_count"),
        "gpu_energy_counter": gpu_energy,
        "gpu_energy_counter_via": "nvml" if nvml.get("energy_counter_available") else (
            "nvidia-smi" if gpu.get("energy_counter_available") else None),
        "gpu_power_limits_w": gpu_limits or None,
        "gpu_power_limits_source": gpu_limits_source,
        "clock_synchronized": report["clock"].get("synchronized"),
        "lease": {k: lease.get(k) for k in ("present", "valid", "owner", "expires_utc", "minutes_left")} if lease else None,
        "notes": notes,
    }


# ---- driver -------------------------------------------------------------------------------------

def guarded(ctx, name, function, *args):
    say(name)
    try:
        return function(ctx, *args)
    except Exception as exc:                       # a probe must report, not crash
        ctx.errors.append({"section": name, "error": "%s: %s" % (type(exc).__name__, exc)})
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def build_parser():
    parser = argparse.ArgumentParser(description="Read-only power-measurement probe (see the module docstring).")
    parser.add_argument("--output", help="also write the JSON to this file")
    parser.add_argument("--idle-seconds", type=float, default=3.0, help="idle window of the counter sanity check")
    parser.add_argument("--busy-seconds", type=float, default=5.0, help="CPU-burn window of the counter sanity check")
    parser.add_argument("--no-load", action="store_true", help="skip the CPU burn (idle window only)")
    parser.add_argument("--no-sudo", action="store_true", help="never call sudo; root-only questions come back as skipped")
    parser.add_argument("--no-commands", action="store_true", help="read files only; run no external command")
    parser.add_argument("--root", default="/", help="read /sys, /proc, /dev, /etc below this directory; implies --no-commands")
    parser.add_argument("--lease-name", help="instance name carrying owner and expiry (default: first line of %s)" % LEASE_FILE)
    parser.add_argument("--include-hostname", action="store_true", help="record the hostname (off by default)")
    return parser


def main(argv=None, load_fn=busy_load, sleep=time.sleep):
    args = build_parser().parse_args(argv)
    started = datetime.now(timezone.utc)
    ctx = Ctx(root=args.root, commands=not (args.no_commands or args.root != "/"), sudo=not args.no_sudo, sleep=sleep)
    busy_seconds = 0.0 if args.no_load else args.busy_seconds
    report = {"schema": SCHEMA, "probe": {}}
    os_release = parse_keyvalue(read_text(ctx.p("/etc/os-release")) or "", "=")
    report["host"] = {"os": os_release.get("PRETTY_NAME", "").strip('"') or None, "kernel": platform.release() if ctx.root == "/" else None,
                      "machine": platform.machine() if ctx.root == "/" else None, "python": platform.python_version(),
                      "hostname": platform.node() if args.include_hostname else None}
    report["cpu"] = guarded(ctx, "cpu", collect_cpu)
    cpu = report["cpu"] if "error" not in report["cpu"] else {}
    report["virtualization"] = guarded(ctx, "virtualization", collect_virtualization, cpu)
    report["powercap"] = guarded(ctx, "powercap (RAPL)", collect_powercap)
    report["hwmon"] = guarded(ctx, "hwmon", collect_hwmon)
    report["perf"] = guarded(ctx, "perf power events", collect_perf)
    report["msr"] = guarded(ctx, "msr", collect_msr, cpu)
    report["turbostat"] = guarded(ctx, "turbostat", collect_turbostat)
    report["ipmi"] = guarded(ctx, "ipmi / dcmi", collect_ipmi)
    report["gpu"] = guarded(ctx, "nvidia-smi", collect_gpu)
    report["nvml"] = guarded(ctx, "nvml (ctypes)", collect_nvml)
    report["clock"] = guarded(ctx, "clock sync", collect_clock)
    report["lease"] = guarded(ctx, "lease", collect_lease, args.lease_name)
    report["counter_sanity"] = guarded(ctx, "counter sanity (idle %.0fs, busy %.0fs)" % (args.idle_seconds, busy_seconds),
                                       collect_counter_sanity, args.idle_seconds, busy_seconds, load_fn)
    try:
        report["summary"] = summarize(_with_defaults(report))
    except Exception as exc:
        ctx.errors.append({"section": "summary", "error": "%s: %s" % (type(exc).__name__, exc)})
        report["summary"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
    finished = datetime.now(timezone.utc)
    report["probe"] = {"version": 1, "started_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "finished_utc": finished.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "duration_s": round((finished - started).total_seconds(), 1), "argv": sys.argv[1:] if argv is None else list(argv),
                       "root": args.root, "commands_enabled": ctx.commands, "sudo": ctx.sudo_mode, "euid": ctx.euid}
    report["errors"] = ctx.errors
    text = json.dumps(report, indent=2, sort_keys=False)
    if args.output:
        with open(args.output, "w") as handle:
            handle.write(text + "\n")
        say("wrote %s" % args.output)
    print(text)
    return 0


def _with_defaults(report):
    """summarize() reads fixed keys; a section that failed contributes empty defaults instead of aborting."""
    defaults = {"cpu": {}, "virtualization": {"verdict": "unknown"},
                "powercap": {"zones": [], "domains": [], "all_packages_readable_as_user": False, "all_packages_readable_as_root": False},
                "hwmon": {"devices_with_energy_or_power": []}, "perf": {"power_events": {}}, "nvml": {},
                "msr": {"device_present": False, "readable_as_user": False}, "gpu": {}, "ipmi": {}, "clock": {},
                "lease": {}, "counter_sanity": {}}
    return {key: (report[key] if "error" not in report[key] else default) for key, default in defaults.items()}


if __name__ == "__main__":
    raise SystemExit(main())
