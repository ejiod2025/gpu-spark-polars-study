#!/usr/bin/env python3
"""Background CPU and node power telemetry with explicit source labels. Values are read, never modelled.

A PowerSampler runs beside a benchmark (in the runner process, never inside a worker) and writes

  power.csv            one row per reading: when, which source and domain, cumulative energy or power
  power_sources.json   sidecar: what was probed, what is sampled, why anything is unavailable, sampler overhead

CPU energy counters, tried in this order (the first one that can be read is sampled; the rest are recorded as skipped):
  powercap-rapl   /sys/class/powercap/intel-rapl*/energy_uj (Intel, and AMD Zen on recent kernels): cumulative uJ,
                  wraparound unwrapped with max_energy_range_uj
  amd-energy      hwmon "amd_energy" socket counters (Esocket*): cumulative uJ
  perf-stat       `perf stat -a -I` on power/energy-*/ events: joules per interval, summed into a cumulative uJ
  turbostat       `turbostat --Summary` PkgWatt/RAMWatt: average watts per interval
Whole-node power, sampled in addition when usable without interactive authentication:
  ipmi-dcmi       `ipmitool dcmi power reading`: instantaneous watts from the BMC (integer watts, about 1 s resolution)

When nothing can be read the sidecar says status "unavailable" and lists every reason probed; power.csv then holds only its
header. Nothing is estimated from TDP, utilisation or anything else.

power.csv columns (energy_uj and power_w are never both filled; the other one is empty):
  timestamp_utc    ISO-8601 UTC with microseconds, taken when the sample was read
  monotonic_ns     time.monotonic_ns() at the same moment; the clock worker.py uses for timed_*_monotonic_ns
  source           powercap-rapl | amd-energy | perf-stat | turbostat | ipmi-dcmi
  zone             sysfs zone (intel-rapl:0:1), hwmon channel, perf event or turbostat column; the exact series
  domain           what it measures: package-0, package-0/dram, package-0/core, socket-0, node, all-sockets:energy-pkg, ...
  energy_uj        cumulative energy in microjoules, unwrapped; take differences, the origin is arbitrary (counter
                   value for RAPL/AMD, zero at sampler start for perf-stat)
  power_w          watts (ipmi-dcmi instantaneous, turbostat average over its interval)
  raw_counter_uj   the counter exactly as read (RAPL/AMD), so wraparound handling can be audited
  flag             empty, or "+"-joined: wrap (counter wrapped and was unwrapped), decrease (counter went backwards
                   and could not be unwrapped: discard windows containing it), gap (more than 30 s since the previous
                   sample of this series: a multiple wrap would be undetectable)
  scale, engine, phase   what the runner was doing; phase "idle-baseline" is the pre-session idle window

A counter that exists but does not advance is not a zero-energy reading. At probe time every counter must change within
LIVENESS_SECONDS (RAPL/amd_energy zones that do not are not sampled), perf events that read 0 J, turbostat/IPMI values of
0 W and a flat sampled counter are all reported as unavailable with the reason. At stop() each series gets a verdict in the
sidecar ("liveness": advancing | never_advanced | stalled | insufficient_samples) and the status is demoted accordingly.

GPU board energy is separate from the sampler: GpuEnergyReader reads NVML's cumulative energy counter (mJ since driver
load) synchronously, so worker.py can read it immediately before and after each timed run and store the exact delta per
record. Source order: NVML through ctypes on libnvidia-ml.so.1 (no Python package needed), then pynvml, then
`nvidia-smi --query-gpu=total_energy_consumption` (absent on some drivers), else "unavailable". `--probe-gpu-energy` reports how the counter really advances (update interval, step size, read latency).

The sidecar does not record the hostname unless asked (include_hostname / --include-hostname): cloud hostnames can encode the
public IP and these files are meant to be published. Nothing else this module writes contains one.

Permissions. Since Linux 5.10 (CVE-2020-8694) energy_uj is root-only. A PermissionError is recorded per zone with the
setup step that fixes it (see "setup_hints" in the sidecar); the sampler never changes permissions itself. On a host where
the counters exist but this user cannot read them, check_cpu_energy() (preflight.py, run_experiment.py and run_campaign.py
call it) is a hard failure that names the fix, `./setup_h100.sh --power-only --enable-rapl-read`: that case must never
degrade silently to an "unavailable" sidecar. `--cpu-energy off` is the explicit way to run without CPU energy anyway.

Sockets. RAPL and amd_energy give one counter per socket and per DRAM domain; each is its own series in power.csv (domain
package-0, package-0/dram, package-1, ...), and the sidecar's "sockets" maps socket -> zones next to the host's CPU
topology. When a run pins its engines to one socket (resolve_pinning / apply_pinning: os.sched_setaffinity, inherited by
every child), the sidecar's "pinning" block says which zones are the pinned sockets' (S2-pinned) and which are not. The
engines' memory is bound to the same NUMA nodes (apply_membind: set_mempolicy MPOL_BIND through ctypes, else a re-exec under
`numactl --membind`), because DRAM energy is attributed to the pinned socket only; --no-membind is the recorded opt-out.

  python power_telemetry.py --probe                       # what can this host measure? (exit 3 if nothing)
  python power_telemetry.py --topology                    # sockets, CPU lists, cores, NUMA nodes (what --socket selects)
  python power_telemetry.py --check-cpu-energy            # exit 4 if RAPL exists but is unreadable (require: or unusable)
  python power_telemetry.py --probe-membind --nodes 0,1   # can memory be bound to these NUMA nodes? (exit 5 if not)
  python power_telemetry.py --output DIR --duration 60    # sample standalone, print the sidecar
  python power_telemetry.py --probe-gpu-energy            # GPU energy counter source and resolution (exit 3 if none)
"""
from __future__ import annotations

import argparse
import csv
import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import importlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time

POWERCAP_ROOT = Path("/sys/class/powercap")
HWMON_ROOT = Path("/sys/class/hwmon")
SYS_CPU_ROOT = Path("/sys/devices/system/cpu")
SYS_NODE_ROOT = Path("/sys/devices/system/node")
CSV_COLUMNS = ("timestamp_utc", "monotonic_ns", "source", "zone", "domain", "energy_uj", "power_w",
               "raw_counter_uj", "flag", "scale", "engine", "phase")
SIDECAR_FORMAT = 1
CPU_SOURCES = ("powercap-rapl", "amd-energy", "perf-stat", "turbostat")
IPMI_SOURCE = "ipmi-dcmi"
DEFAULT_INTERVAL_SECONDS = 0.2           # matches the 200 ms nvidia-smi sampler in run_experiment.py
DEFAULT_IPMI_INTERVAL_SECONDS = 1.0      # the BMC averages over about a second; a spawn per 200 ms only loads it
PROBE_TIMEOUT_SECONDS = 10
IPMI_MAX_CONSECUTIVE_FAILURES = 5
GAP_FLAG_SECONDS = 30.0
PERF_EVENTS = ("power/energy-pkg/", "power/energy-ram/", "power/energy-cores/", "power/energy-psys/")
LIVENESS_SECONDS = 0.3                   # a counter must change within this long at probe time, or it is not a reading
STALL_SECONDS = 5.0                      # an energy counter flat for longer than this during a run is reported as stalled
GPU_ENERGY_CTYPES = "nvml-ctypes:nvmlDeviceGetTotalEnergyConsumption"
GPU_ENERGY_NVML = "pynvml.nvmlDeviceGetTotalEnergyConsumption"
GPU_ENERGY_SMI = "nvidia-smi:total_energy_consumption"
GPU_ENERGY_UNAVAILABLE = "unavailable"
CPU_ENERGY_MODES = ("auto", "require", "off")
MPOL_BIND = 2
# set_mempolicy and get_mempolicy: Linux syscall numbers per architecture (used only when libnuma cannot be loaded)
MEMPOLICY_SYSCALLS = {"x86_64": (238, 239), "amd64": (238, 239), "aarch64": (237, 236), "arm64": (237, 236)}
MEMBIND_GUARD_ENV = "BENCHMARK_MEMBIND_NODES"     # set by the numactl re-exec so the new process verifies instead of looping
MEMBIND_MAX_NODES = 1024
MAX_CPU_NUMBER = 65535
SOCKET_DOMAIN = re.compile(r"^(?:package|socket)-(\d+)(?:/|$)")
TIMED_KEYS = ("timed_start_utc", "timed_end_utc", "timed_start_monotonic_ns", "timed_end_monotonic_ns")

# What to do when RAPL exists but this user cannot read it (preflight.py, run_campaign.py and run_experiment.py say it verbatim).
RAPL_READ_FIX = ("run ./setup_h100.sh --power-only --enable-rapl-read once on this host (single-tenant hosts only: it makes "
                 "energy_uj readable by every local user and adds a udev rule; undo with --revert-rapl-read), or run the "
                 "benchmark as root")

SETUP_HINTS = {
    "powercap-rapl": (
        "RAPL energy_uj is root-only since Linux 5.10 (CVE-2020-8694): " + RAPL_READ_FIX + ". Without the script: as root "
        "before the run, chmod a+r /sys/class/powercap/intel-rapl*/energy_uj (not persistent across reboot; it relaxes the "
        "CVE mitigation, acceptable on a dedicated benchmark host). Load the drivers if the tree is missing: "
        "sudo modprobe intel_rapl_common intel_rapl_msr. Cloud VMs usually do not expose RAPL at all."),
    "perf-stat": (
        "perf needs system-wide counting rights: run as root, or sudo sysctl -w kernel.perf_event_paranoid=0 "
        "(or grant CAP_PERFMON), and install perf (apt install linux-tools-$(uname -r) linux-tools-common)."),
    "turbostat": (
        "turbostat reads MSRs: run as root, or sudo modprobe msr && sudo chmod a+r /dev/cpu/*/msr and grant "
        "CAP_SYS_RAWIO; install it with apt install linux-tools-$(uname -r) linux-tools-common."),
    IPMI_SOURCE: (
        "ipmitool needs a local BMC: apt install ipmitool; sudo modprobe ipmi_si ipmi_devintf; /dev/ipmi0 is "
        "root-only, so run as root or point POWER_TELEMETRY_IPMI_COMMAND at 'sudo -n ipmitool' with a NOPASSWD rule. "
        "Cloud VMs have no BMC."),
}


# ---- clocks ------------------------------------------------------------------------------------------

def utc_now() -> str:
    """UTC wall clock as ISO-8601 with microseconds always present (datetime.isoformat() drops a zero fraction)."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def stamp_open():
    """(utc, monotonic_ns) read immediately before a timer starts, so the window contains the timed region."""
    wall = utc_now()
    return wall, time.monotonic_ns()


def stamp_close():
    """(utc, monotonic_ns) read immediately after a timer stops."""
    mono = time.monotonic_ns()
    return utc_now(), mono


def clock_anchor() -> dict:
    """A UTC reading and a monotonic reading of the same instant, good to uncertainty_ns (the bracketing reads)."""
    before = time.monotonic_ns()
    wall = utc_now()
    after = time.monotonic_ns()
    return {"utc": wall, "monotonic_ns": (before + after) // 2, "uncertainty_ns": after - before}


def host_timezone() -> dict:
    """What turns nvidia-smi's local timestamps into UTC: the process timezone and its current offset."""
    offset = datetime.now().astimezone().utcoffset()
    info = {"tz_env": os.environ.get("TZ"), "tzname": list(time.tzname),
            "utc_offset_seconds": int(offset.total_seconds()) if offset is not None else None,
            "dst_in_effect": time.localtime().tm_isdst > 0, "has_dst_rules": bool(time.daylight)}
    try:
        info["etc_localtime"] = os.readlink("/etc/localtime")
    except OSError:
        info["etc_localtime"] = None
    return info


def record_window(record: dict) -> dict:
    """The window of one results.json record: the tight timed window when present, else the legacy one.

    Records written before timed_* existed carry only started_at_utc/ended_at_utc, which also cover sampler start-up and
    plan serialisation; basis says which one you got. Never raises on a malformed record.
    """
    def integer(value):
        return isinstance(value, int) and not isinstance(value, bool)
    if (isinstance(record, dict) and all(isinstance(record.get(k), str) and record[k] for k in TIMED_KEYS[:2])
            and all(integer(record.get(k)) for k in TIMED_KEYS[2:])):
        return {"basis": "timed", "start_utc": record["timed_start_utc"], "end_utc": record["timed_end_utc"],
                "start_monotonic_ns": record["timed_start_monotonic_ns"],
                "end_monotonic_ns": record["timed_end_monotonic_ns"]}
    record = record if isinstance(record, dict) else {}
    return {"basis": "legacy", "start_utc": record.get("started_at_utc"), "end_utc": record.get("ended_at_utc"),
            "start_monotonic_ns": None, "end_monotonic_ns": None}


# ---- reading ----------------------------------------------------------------------------------------

def describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:240]


def read_int(path: Path) -> int:
    with open(path, "rb") as handle:
        return int(handle.read(64).strip())


def read_text(path: Path):
    try:
        return Path(path).read_text().strip() or None
    except (OSError, UnicodeDecodeError):
        return None


def exists(path: Path) -> bool:
    try:
        return Path(path).exists()
    except OSError:
        return False


def euid():
    return os.geteuid() if hasattr(os, "geteuid") else None


@dataclass
class Zone:
    """One cumulative energy counter."""
    source: str
    zone: str
    domain: str
    kind: str                 # package | dram | core | uncore | psys | socket | other
    interface: str            # msr | mmio (powercap) | hwmon
    counter: Path
    max_range_uj: int | None
    error: str | None = None
    duplicate_of: str | None = None
    advancing: bool | None = None      # None: not checked; False: readable but constant, so not an energy reading

    @property
    def sampled(self) -> bool:
        return self.error is None and self.duplicate_of is None

    @property
    def socket(self):
        """Package number from the domain name (powercap package-N[/dram], amd_energy socket-N); None for psys and others."""
        match = SOCKET_DOMAIN.match(self.domain)
        return int(match.group(1)) if match else None

    def describe(self) -> dict:
        return {"zone": self.zone, "domain": self.domain, "socket": self.socket, "kind": self.kind, "interface": self.interface,
                "path": str(self.counter), "max_energy_range_uj": self.max_range_uj,
                "readable": self.error is None or self.advancing is False, "advancing": self.advancing,
                "error": self.error, "duplicate_of": self.duplicate_of, "sampled": self.sampled}


def check_advancing(zones, seconds, sleep=time.sleep):
    """Mark readable counters that do not change within `seconds`; returns those zones.

    An idle package, dram or socket counter still ticks (RAPL energy units are about 61 uJ), so a constant value means
    the counter is not wired to anything (virtualised host, firmware-disabled domain), not that no energy was used.
    """
    live = [z for z in zones if z.sampled]
    if not live:
        return []
    first = {}
    for zone in live:
        try:
            first[zone.zone] = read_int(zone.counter)
        except (OSError, ValueError) as exc:
            zone.error = describe(exc)
    sleep(seconds)
    stuck = []
    for zone in live:
        if zone.zone not in first:
            continue
        try:
            second = read_int(zone.counter)
        except (OSError, ValueError) as exc:
            zone.error = describe(exc)
            continue
        zone.advancing = second != first[zone.zone]
        if not zone.advancing:
            zone.error = (f"counter present but not advancing: {second} uJ unchanged over {seconds:g} s "
                          "(a constant counter is not a zero-energy reading)")
            stuck.append(zone)
    return stuck


def discover_powercap(root: Path):
    """(zones, reasons) for every intel-rapl*/energy_uj under a powercap root; unreadable zones carry their error."""
    root = Path(root)
    if not root.is_dir():
        return [], [f"{root} does not exist: kernel without the powercap/RAPL driver, or a VM that does not expose RAPL"]
    try:
        entries = sorted(p for p in root.iterdir() if p.name.startswith("intel-rapl") and exists(p / "energy_uj"))
    except OSError as exc:
        return [], [f"cannot list {root}: {describe(exc)}"]
    if not entries:
        return [], [f"{root} has no intel-rapl*/energy_uj zone (driver not loaded, or RAPL not exposed to this host)"]
    zones = []
    for path in entries:
        parts = path.name.split(":")
        name = read_text(path / "name") or path.name
        parent = ":".join(parts[:-1]) if len(parts) > 2 else None
        domain = name if parent is None else f"{read_text(root / parent / 'name') or parent}/{name}"
        kind = name.split("-")[0] if name.split("-")[0] in ("package", "dram", "core", "uncore", "psys") else "other"
        try:
            max_range = read_int(path / "max_energy_range_uj")
        except (OSError, ValueError):
            max_range = None
        zone = Zone("powercap-rapl", path.name, domain, kind, "mmio" if parts[0].endswith("mmio") else "msr",
                    path / "energy_uj", max_range if max_range and max_range > 0 else None)
        try:
            read_int(zone.counter)
        except (OSError, ValueError) as exc:
            zone.error = describe(exc)
        zones.append(zone)
    # The same package can appear through the MSR and the MMIO interface: sample one, never sum both.
    primary = {z.domain: z.zone for z in zones if z.interface == "msr" and z.error is None}
    for zone in zones:
        if zone.interface == "mmio" and zone.error is None and zone.domain in primary:
            zone.duplicate_of = primary[zone.domain]
    reasons = []
    denied = [z for z in zones if z.error and "PermissionError" in z.error]
    other = [z for z in zones if z.error and z not in denied]
    if denied:
        reasons.append(f"{len(denied)} of {len(zones)} RAPL zones are not readable by uid {euid()}: "
                       f"{denied[0].error} (energy_uj is root-only since Linux 5.10, CVE-2020-8694)")
    if other:
        reasons.append(f"{len(other)} of {len(zones)} RAPL zones failed to read: {other[0].error}")
    return zones, reasons


def discover_amd_energy(root: Path):
    """(zones, reasons) for the socket-level counters of the hwmon amd_energy driver."""
    root = Path(root)
    if not root.is_dir():
        return [], [f"{root} does not exist"]
    try:
        devices = sorted(p for p in root.iterdir() if p.name.startswith("hwmon") and read_text(p / "name") == "amd_energy")
    except OSError as exc:
        return [], [f"cannot list {root}: {describe(exc)}"]
    if not devices:
        return [], [f"no hwmon device named amd_energy under {root}"]
    zones, reasons = [], []
    for device in devices:
        for counter in sorted(device.glob("energy*_input")):
            channel = counter.name[:-len("_input")]
            label = read_text(device / f"{channel}_label") or ""
            if not label.startswith("Esocket"):
                continue                      # per-core counters exclude uncore: not a package total, not summed here
            zone = Zone("amd-energy", f"{device.name}/{channel}", f"socket-{label[len('Esocket'):]}", "socket", "hwmon",
                        counter, None)
            try:
                read_int(counter)
            except (OSError, ValueError) as exc:
                zone.error = describe(exc)
            zones.append(zone)
    if not zones:
        reasons.append("amd_energy is present but exposes no Esocket* counter (per-core counters are not summed)")
    failed = [z for z in zones if z.error]
    if failed:
        reasons.append(f"{len(failed)} of {len(zones)} amd_energy counters unreadable: {failed[0].error}")
    return zones, reasons


class CounterSeries:
    """Unwraps one cumulative counter: energy_uj = raw + wraps * max_energy_range_uj, so differences stay exact."""

    def __init__(self, max_range_uj):
        self.max_range = max_range_uj
        self.wraps = 0
        self.last_raw = None
        self.last_mono = None

    def update(self, raw: int, mono_ns: int):
        flags = []
        if self.last_raw is not None:
            if raw < self.last_raw:
                if self.max_range:
                    self.wraps += 1
                    flags.append("wrap")
                else:
                    flags.append("decrease")
            if mono_ns - self.last_mono > GAP_FLAG_SECONDS * 1e9:
                flags.append("gap")
        self.last_raw, self.last_mono = raw, mono_ns
        return raw + self.wraps * (self.max_range or 0), "+".join(flags)


# ---- stream parsers (perf stat, turbostat) -----------------------------------------------------------

class PerfStatParser:
    """`perf stat -I -x,` lines `time,count,unit,event,...`: joules per interval, summed into cumulative uJ."""

    def __init__(self):
        self.cumulative = {}

    def parse(self, line: str):
        fields = [f.strip() for f in line.split(",")]
        try:
            float(fields[0])
        except ValueError:
            return []                         # comment, blank or summary line
        if len(fields) < 4:
            return []
        if fields[1] in ("Joules", "J"):
            return []                         # an end-of-run summary line (count,unit,event): no interval time column
        count, unit, event = fields[1], fields[2], fields[3]
        if count.startswith("<"):
            raise ValueError(f"{event}: {count}")
        if unit not in ("Joules", "J"):
            raise ValueError(f"{event}: unexpected unit {unit!r}")
        match = re.fullmatch(r"power/(energy-[a-z]+)/", event)
        if not match:
            raise ValueError(f"unexpected event {event!r}")
        domain = f"all-sockets:{match.group(1)}"
        self.cumulative[domain] = self.cumulative.get(domain, 0) + round(float(count) * 1e6)
        return [(match.group(1), domain, self.cumulative[domain], None)]


class TurbostatParser:
    """`turbostat --quiet --Summary --show PkgWatt,RAMWatt`: a header line, then one value line per interval."""
    COLUMNS = ("PkgWatt", "RAMWatt")

    def __init__(self):
        self.header = None

    def parse(self, line: str):
        tokens = line.split()
        if not tokens:
            return []
        try:
            values = [float(t) for t in tokens]
        except ValueError:
            self.header = tokens
            return []
        if self.header is None or len(values) != len(self.header):
            raise ValueError(f"turbostat data line without a matching header: {line.strip()[:80]!r}")
        return [(name, f"all-sockets:{name}", None, value) for name, value in zip(self.header, values)
                if name in self.COLUMNS]


IPMI_POWER = re.compile(r"Instantaneous power reading:\s+(\d+)\s+Watts", re.IGNORECASE)
IPMI_STATE = re.compile(r"Power reading state is:\s+(\w+)", re.IGNORECASE)


def parse_ipmi_power(text: str) -> int:
    """Watts from `ipmitool dcmi power reading`; ValueError when the BMC reports no usable reading."""
    state = IPMI_STATE.search(text)
    if state and state.group(1).lower() != "activated":
        raise ValueError(f"power reading state is {state.group(1)!r}")
    match = IPMI_POWER.search(text)
    if not match:
        raise ValueError(f"no 'Instantaneous power reading' in output: {text.strip()[:120]!r}")
    return int(match.group(1))


# ---- GPU board energy (NVML cumulative counter) ---------------------------------------------------------

class NvmlError(RuntimeError):
    pass


class CtypesNvml:
    """The three NVML calls the energy read needs, through ctypes on libnvidia-ml (no pynvml needed).

    It exposes the pynvml function names, so GpuEnergyReader treats it and the pynvml module alike. loader is
    ctypes.CDLL unless a test passes a fake.
    """
    LIBRARIES = ("libnvidia-ml.so.1", "libnvidia-ml.so")

    def __init__(self, loader=None):
        loader, errors, lib = loader or ctypes.CDLL, [], None
        for name in self.LIBRARIES:
            try:
                lib = loader(name)
                break
            except OSError as exc:
                errors.append(f"{name}: {exc}")
        if lib is None:
            raise NvmlError("cannot load libnvidia-ml (" + "; ".join(errors) + ")")
        self.init = getattr(lib, "nvmlInit_v2", None) or lib.nvmlInit
        self.handle_by_index = getattr(lib, "nvmlDeviceGetHandleByIndex_v2", None) or lib.nvmlDeviceGetHandleByIndex
        self.energy = lib.nvmlDeviceGetTotalEnergyConsumption
        self.error_string = getattr(lib, "nvmlErrorString", None)
        self.handle_by_index.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
        self.energy.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulonglong)]
        for function in (self.init, self.handle_by_index, self.energy):
            function.restype = ctypes.c_int
        if self.error_string is not None:
            self.error_string.argtypes, self.error_string.restype = [ctypes.c_int], ctypes.c_char_p

    def check(self, code, call):
        if code != 0:
            text = ""
            try:
                text = (self.error_string(code) or b"").decode(errors="replace") if self.error_string else ""
            except Exception:
                pass
            raise NvmlError(f"{call} returned {code}" + (f": {text}" if text else ""))

    def nvmlInit(self):
        self.check(self.init(), "nvmlInit")

    def nvmlDeviceGetHandleByIndex(self, index):
        handle = ctypes.c_void_p()
        self.check(self.handle_by_index(ctypes.c_uint(index), ctypes.byref(handle)), "nvmlDeviceGetHandleByIndex")
        return handle

    def nvmlDeviceGetTotalEnergyConsumption(self, handle):
        value = ctypes.c_ulonglong()
        self.check(self.energy(handle, ctypes.byref(value)), "nvmlDeviceGetTotalEnergyConsumption")
        return int(value.value)


class GpuEnergyReader:
    """Cumulative GPU board energy, millijoules since the driver was loaded, read synchronously. read() never raises.

    The source is chosen once: NVML through ctypes, else pynvml, else nvidia-smi, else unavailable (reasons say why).
    Each read returns the counter and the monotonic_ns taken just before and just after the call, so the instant it
    describes is known to within the call latency (microseconds for NVML, a process spawn for nvidia-smi). nvml replaces
    both NVML candidates with an injected module; ctypes_loader, which and run are injectable for tests.
    """

    def __init__(self, device=0, *, nvml=None, ctypes_loader=None, which=None, run=None):
        self.device = device
        self.which, self.run = which or shutil.which, run or subprocess.run
        self.source, self.reasons = GPU_ENERGY_UNAVAILABLE, []
        self.nvml = self.handle = None
        candidates = ([("pynvml", GPU_ENERGY_NVML, lambda: nvml)] if nvml is not None else
                      [("nvml-ctypes", GPU_ENERGY_CTYPES, lambda: CtypesNvml(ctypes_loader)),
                       ("pynvml", GPU_ENERGY_NVML, lambda: importlib.import_module("pynvml"))])
        for label, source, load in candidates:
            try:
                module = load()
                module.nvmlInit()
                handle = module.nvmlDeviceGetHandleByIndex(device)
                module.nvmlDeviceGetTotalEnergyConsumption(handle)       # NotSupported before Volta: next candidate
                self.nvml, self.handle, self.source = module, handle, source
                return
            except Exception as exc:
                self.reasons.append(f"{label}: {describe(exc)}")
        if self.which("nvidia-smi") is None:
            self.reasons.append("nvidia-smi: 'nvidia-smi' not found on PATH")
            return
        try:
            self.smi_value()
            self.source = GPU_ENERGY_SMI
        except Exception as exc:
            self.reasons.append(f"nvidia-smi: {describe(exc)}")

    def smi_value(self) -> int:
        done = self.run(["nvidia-smi", "-i", str(self.device), "--query-gpu=total_energy_consumption",
                         "--format=csv,noheader,nounits"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                        timeout=PROBE_TIMEOUT_SECONDS)
        lines = (done.stdout or "").strip().splitlines()
        if done.returncode != 0 or not lines:
            tail = ((done.stderr or "") + (done.stdout or "")).strip().splitlines()
            raise RuntimeError(f"exit {done.returncode}: {tail[-1][:200] if tail else 'no output'}")
        if not re.fullmatch(r"\d+(\.\d+)?", lines[0].strip()):
            raise ValueError(f"not a counter value: {lines[0].strip()[:60]!r}")
        return int(round(float(lines[0].strip())))

    def read(self) -> dict:
        before = time.monotonic_ns()
        value, error = None, None
        try:
            if self.nvml is not None:
                value = int(self.nvml.nvmlDeviceGetTotalEnergyConsumption(self.handle))
            elif self.source == GPU_ENERGY_SMI:
                value = self.smi_value()
            else:
                error = GPU_ENERGY_UNAVAILABLE
        except Exception as exc:                  # a failed read is recorded, never raised into a benchmark action
            error = describe(exc)
        out = {"mj": value, "monotonic_ns": [before, time.monotonic_ns()]}
        if error:
            out["error"] = error
        return out

    def describe(self) -> dict:
        return {"source": self.source, "unit": "mJ", "device_index": self.device, "reasons": list(self.reasons),
                "semantics": "cumulative whole-board energy of this device since the driver was loaded; take the "
                             "difference of two reads; update granularity is host-specific (see --probe-gpu-energy)"}


def gpu_energy_window(source: str, device: int, start: dict, end: dict, timed_start_mono=None, timed_end_mono=None) -> dict:
    """The per-record "gpu_energy" value from the reads just before and just after a timed run.

    Each counter value belongs to some instant inside its read bracket [before, after]; the brackets sit outside the timed
    region, so the delta covers the timed region plus at most overhang_ns_max of time around it: bound the error by
    (peak power) x overhang_ns_max. The read latencies are stored on their own as well."""
    record = {"source": source, "unit": "mJ", "device_index": device, "start_mj": None, "end_mj": None, "delta_mj": None}
    if source == GPU_ENERGY_UNAVAILABLE:
        return record
    record.update(start_mj=start["mj"], end_mj=end["mj"], start_read_monotonic_ns=start["monotonic_ns"],
                  end_read_monotonic_ns=end["monotonic_ns"],
                  start_read_latency_ns=start["monotonic_ns"][1] - start["monotonic_ns"][0],
                  end_read_latency_ns=end["monotonic_ns"][1] - end["monotonic_ns"][0])
    if timed_start_mono is not None and timed_end_mono is not None:
        record["overhang_ns_max"] = ((timed_start_mono - start["monotonic_ns"][0]) +
                                     (end["monotonic_ns"][1] - timed_end_mono))
    errors = [e for e in (start.get("error"), end.get("error")) if e]
    if start["mj"] is not None and end["mj"] is not None:
        if end["mj"] >= start["mj"]:
            record["delta_mj"] = end["mj"] - start["mj"]
        else:
            errors.append("counter decreased between the reads (driver reload?); delta unusable")
    if errors:
        record["error"] = "; ".join(errors)
    return record


def gpu_energy_delta_j(record):
    """Joules of GPU board energy over a results.json record's timed run, or None (old record, unavailable, bad read)."""
    energy = record.get("gpu_energy") if isinstance(record, dict) else None
    delta = energy.get("delta_mj") if isinstance(energy, dict) else None
    return delta / 1000.0 if isinstance(delta, int) and not isinstance(delta, bool) and delta >= 0 else None


def gpu_energy_resolution_verdict(record, update_interval_ms, factor=10):
    """"below_resolution" when the timed run is shorter than factor x the counter's update interval, else "ok".

    The interval is a property of the host (environment.json gpu_energy_counter.resolution.update_interval_ms.median), not
    of a record, so the worker does not decide this; the analysis calls it. None when either input is unusable."""
    seconds = record.get("seconds") if isinstance(record, dict) else None
    numbers = (seconds, update_interval_ms)
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and x > 0 for x in numbers):
        return None
    return "below_resolution" if seconds * 1000.0 < factor * update_interval_ms else "ok"


def probe_gpu_energy_resolution(reader, duration=1.0, clock=time.monotonic_ns) -> dict:
    """Read the counter back to back for `duration` seconds and report how it actually advances."""
    stop = clock() + int(duration * 1e9)
    reads, errors = [], 0
    while True:
        sample = reader.read()
        if sample["mj"] is None:
            errors += 1
        else:
            reads.append(((sample["monotonic_ns"][0] + sample["monotonic_ns"][1]) // 2, sample["mj"],
                          sample["monotonic_ns"][1] - sample["monotonic_ns"][0]))
        if clock() >= stop or errors >= 5:
            break
    changes = [(t, v, v - reads[i][1]) for i, (t, v, _) in enumerate(reads[1:]) if v != reads[i][1]]
    gaps_ms = [(b[0] - a[0]) / 1e6 for a, b in zip(changes, changes[1:])]
    steps = [step for _, _, step in changes if step > 0]
    latency_us = [lat / 1e3 for _, _, lat in reads]

    def summary(values):
        return {"min": min(values), "median": statistics.median(values), "max": max(values)} if values else None
    return {"source": reader.source, "unit": "mJ", "reads": len(reads), "failed_reads": errors,
            "duration_seconds": (reads[-1][0] - reads[0][0]) / 1e9 if len(reads) > 1 else 0.0,
            "changes_observed": len(changes), "update_interval_ms": summary(gaps_ms), "step_mj": summary(steps),
            "read_latency_us": summary(latency_us),
            "nondecreasing": all(b[1] >= a[1] for a, b in zip(reads, reads[1:])),
            "note": "update_interval_ms is the spacing of observed value changes: the finest time resolution of a "
                    "counter difference; a timed run shorter than it has an energy delta of 0 or one step"}


# ---- the sampler -------------------------------------------------------------------------------------

def write_json(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n")
    temporary.replace(path)


class PowerSampler:
    """Probe the host once, then sample every usable source on background threads until stop().

    labels() returns (scale, engine, phase) for the csv; it is called from the sampler threads. which/run/popen are the
    only external effects besides sysfs reads, so tests substitute them.
    """

    def __init__(self, output_dir, *, interval=DEFAULT_INTERVAL_SECONDS, ipmi_interval=DEFAULT_IPMI_INTERVAL_SECONDS,
                 cpu_sources=CPU_SOURCES, ipmi=True, powercap_root=None, hwmon_root=None, ipmi_command=None,
                 labels=None, csv_name="power.csv", sidecar_name="power_sources.json", liveness_seconds=None,
                 include_hostname=False, pinning=None, cpu_root=None, node_root=None, which=None, run=None, popen=None,
                 sleep=None):
        self.dir = Path(output_dir)
        self.csv_path, self.sidecar_path = self.dir / csv_name, self.dir / sidecar_name
        self.interval, self.ipmi_interval = float(interval), float(ipmi_interval)
        self.cpu_sources, self.ipmi = tuple(cpu_sources), ipmi
        self.powercap_root, self.hwmon_root = powercap_root, hwmon_root
        env_command = os.environ.get("POWER_TELEMETRY_IPMI_COMMAND")
        self.ipmi_command = list(ipmi_command or (env_command.split() if env_command else ["ipmitool"]))
        self.labels = labels or (lambda: ("", "", ""))
        self.which, self.run, self.popen = which or shutil.which, run or subprocess.run, popen or subprocess.Popen
        self.liveness_seconds, self.sleep = liveness_seconds, sleep or time.sleep    # None: LIVENESS_SECONDS; 0 skips
        self.include_hostname = include_hostname    # off by default: cloud hostnames often encode the public IP
        self.pinning = pinning                      # resolve_pinning() of the run, recorded with its zone attribution
        self.cpu_root, self.node_root = cpu_root, node_root
        self.live: dict = {}               # key -> running liveness tally of one sampled series
        self.report: dict = {}
        self.stop_event = threading.Event()
        self.threads: list = []
        self.processes: list = []
        self.lock = threading.Lock()
        self.handle = self.writer = None
        self.series: list = []             # (Zone, CounterSeries) of the active counter source
        self.stream = None                 # (source, command, parser factory) of the active perf/turbostat source
        self.ipmi_active = False
        self.rows = {}
        self.errors = {}
        self.cpu_seconds = {}
        self.polls = self.overruns = 0
        self.poll_seconds_total = self.poll_seconds_max = self.gap_max = 0.0
        self.started_mono = self.started_perf = None

    # -- probing
    def probe(self) -> dict:
        """Look at the host and describe it. Reads sysfs and runs short probe commands; writes nothing."""
        powercap_root = Path(self.powercap_root or POWERCAP_ROOT)
        hwmon_root = Path(self.hwmon_root or HWMON_ROOT)
        sources, chosen = [], None
        self.series, self.stream, self.ipmi_active = [], None, False
        probes = {"powercap-rapl": lambda: self._probe_zones(discover_powercap(powercap_root)),
                  "amd-energy": lambda: self._probe_zones(discover_amd_energy(hwmon_root)),
                  "perf-stat": self._probe_perf, "turbostat": self._probe_turbostat}
        for name in self.cpu_sources:
            if chosen is not None:
                sources.append({"source": name, "status": "skipped",
                                "reasons": [f"not probed: {chosen} is already the CPU energy source"]})
                continue
            try:
                entry = probes[name]()
            except Exception as exc:       # a probe bug must not stop the campaign
                entry = {"source": name, "status": "unavailable", "reasons": [f"probe failed: {describe(exc)}"]}
            entry.setdefault("source", name)
            entry["setup_hint"] = SETUP_HINTS.get(name) if entry["status"] != "active" else None
            sources.append(entry)
            if entry["status"] in ("active", "partial"):
                chosen = name
        if self.ipmi:
            try:
                entry = self._probe_ipmi()
            except Exception as exc:
                entry = {"status": "unavailable", "reasons": [f"probe failed: {describe(exc)}"]}
            entry.update(source=IPMI_SOURCE, setup_hint=SETUP_HINTS[IPMI_SOURCE] if entry["status"] != "active" else None)
            sources.append(entry)
        active = [s for s in sources if s["status"] in ("active", "partial")]
        cpu = next((s["source"] for s in active if s["source"] in CPU_SOURCES), None)
        node = IPMI_SOURCE if any(s["source"] == IPMI_SOURCE for s in active) else None
        if not active:
            status = "unavailable"
        elif cpu and all(s["status"] == "active" for s in active if s["source"] == cpu):
            status = "available"
        else:
            status = "partial"
        reasons = [f"{s['source']}: {r}" for s in sources if s["status"] != "skipped" for r in s["reasons"]]
        sockets = self._socket_report(sources, cpu)
        return {
            "format": SIDECAR_FORMAT, "tool": "power_telemetry.py", "status": status,
            "cpu_energy_source": cpu, "node_power_source": node,
            "interval_seconds": self.interval, "ipmi_interval_seconds": self.ipmi_interval if node else None,
            "reasons": reasons, "sources": sources, "sockets": sockets,
            **({"pinning": self._pinning_report(sockets)} if self.pinning else {}),
            "setup_hints": sorted({s["setup_hint"] for s in sources if s.get("setup_hint") and s["status"] != "skipped"}),
            "csv": self.csv_path.name, "csv_columns": list(CSV_COLUMNS),
            "notes": ["energy_uj is cumulative and unwrapped; use differences between samples of one (source, zone)",
                      "RAPL package/dram/psys are separate domains; core and uncore are subsets of package",
                      "ipmi-dcmi rows are stamped when ipmitool returns; the BMC value is averaged over its own period",
                      "perf-stat rows are stamped at line arrival and cover the preceding interval"],
            "host": {**({"hostname": platform.node()} if self.include_hostname else {}),
                     "platform": platform.platform(), "kernel": platform.release(),
                     "euid": euid(), "timezone": host_timezone()},
        }

    def _socket_report(self, sources, cpu) -> dict:
        """Per-socket energy zones of the chosen CPU source, next to the host's socket layout.

        powercap and amd_energy expose one counter per socket (and per DRAM domain), each sampled and written as its own
        series, so package and DRAM energy of each socket are separate in power.csv; perf-stat and turbostat sum all
        sockets and cannot be attributed to one."""
        topology = cpu_topology(self.cpu_root, self.node_root)
        entry = next((s for s in sources if s["source"] == cpu), None)
        per_socket = bool(entry) and cpu in ("powercap-rapl", "amd-energy")
        zones, not_sampled = {}, []
        for domain in (entry.get("domains") or []) if per_socket else []:
            if domain.get("socket") is None:
                continue
            if domain["sampled"]:
                zones.setdefault(str(domain["socket"]), {}).setdefault(domain["kind"], []).append(domain["zone"])
            elif domain["duplicate_of"] is None:
                not_sampled.append(domain["zone"])
        problems = list(topology["problems"])
        known = {str(s["socket"]) for s in topology["sockets"]}
        if zones and known and set(zones) - known:
            problems.append(f"energy zones name socket(s) {sorted(set(zones) - known)} that the CPU topology does not have "
                            f"(sockets {sorted(known)}): per-socket attribution is unverified")
        return {"per_socket_counters": per_socket and bool(zones), "zones": zones, "zones_not_sampled": not_sampled,
                "mapping": "socket N is the N of the powercap zone package-N / amd_energy Esocket N, taken to be the "
                           "physical_package_id of the CPUs listed under topology",
                "topology": topology["sockets"], "problems": problems}

    def _pinning_report(self, sockets) -> dict:
        """The run's CPU pinning with the zones it makes S2-pinned (pinned sockets) and the rest (S2-all minus pinned)."""
        pinned = self.pinning.get("sockets")
        report = dict(self.pinning)
        if not sockets["per_socket_counters"]:
            report["attribution"] = {"possible": False, "reason": "the CPU energy source is not per socket (or unavailable): "
                                     "energy cannot be attributed to the pinned socket(s)"}
        elif pinned is None:
            report["attribution"] = {"possible": False, "reason": "the sockets of the pinned CPUs are unknown (no topology)"}
        else:
            keys = {str(s) for s in pinned}
            report["attribution"] = {
                "possible": True, "pinned_sockets": sorted(pinned),
                "pinned_zones": {k: v for k, v in sockets["zones"].items() if k in keys},
                "other_zones": {k: v for k, v in sockets["zones"].items() if k not in keys},
                "note": "S2-pinned = the pinned sockets' zones (package + dram); the other sockets are logged, not attributed"}
        return report

    def _probe_zones(self, found):
        zones, reasons = found
        seconds = LIVENESS_SECONDS if self.liveness_seconds is None else self.liveness_seconds
        stuck = check_advancing(zones, seconds, self.sleep) if seconds > 0 else []
        if stuck:
            reasons.append(f"{len(stuck)} of {len(zones)} counters are readable but do not advance over {seconds:g} s "
                           f"({stuck[0].zone} stays at one value): not an energy reading, not sampled, never reported as 0 J")
        sampled = [z for z in zones if z.sampled]
        if not sampled:
            return {"status": "unavailable", "reasons": reasons or ["no readable counter"],
                    "domains": [z.describe() for z in zones]}
        self.series = [(z, CounterSeries(z.max_range_uj)) for z in sampled]
        degraded = any(z.error for z in zones)
        return {"status": "partial" if degraded else "active", "kind": "energy_counter", "unit": "uJ",
                "reasons": reasons, "domains": [z.describe() for z in zones]}

    def _run_probe(self, command, timeout=PROBE_TIMEOUT_SECONDS):
        """(completed, reason): the finished process, or why the command could not run."""
        if self.which(command[0]) is None:
            return None, f"{command[0]!r} not found on PATH"
        try:
            return self.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout), None
        except subprocess.TimeoutExpired:
            return None, f"{' '.join(command[:3])} timed out after {timeout}s"
        except OSError as exc:
            return None, describe(exc)

    def _probe_perf(self):
        interval_ms = max(10, int(self.interval * 1000))
        base = ["perf", "stat", "-a", "-I", str(interval_ms), "-x,"]
        done, reason = self._run_probe(base + ["-e", ",".join(PERF_EVENTS), "--", "sleep", "0.25"])
        if done is None:
            return {"status": "unavailable", "reasons": [reason]}
        parser, supported, reasons = PerfStatParser(), [], []
        for line in (done.stderr or "").splitlines() + (done.stdout or "").splitlines():
            try:
                for _, domain, _, _ in parser.parse(line):
                    if domain not in supported:
                        supported.append(domain)
            except ValueError as exc:
                reasons.append(str(exc))
        dead = [d for d in supported if parser.cumulative.get(d, 0) == 0]
        for domain in dead:
            reasons.append(f"{domain}: reads 0 J over the probe: counter present but not counting (not sampled; "
                           "a zero is not a measurement)")
        supported = [d for d in supported if d not in dead]
        events = [e for e in PERF_EVENTS if f"all-sockets:{e.split('/')[1]}" in supported]
        if not events and dead:
            return {"status": "unavailable", "reasons": reasons}
        if not events:
            tail = ((done.stderr or "") + (done.stdout or "")).strip().splitlines()
            return {"status": "unavailable", "reasons": reasons + [f"no power/energy-*/ event counted (perf exit "
                    f"{done.returncode}): {tail[-1][:200] if tail else 'no output'}"]}
        command = base + ["-e", ",".join(events)]
        self.stream = ("perf-stat", command, PerfStatParser)
        return {"status": "active", "kind": "energy_counter", "unit": "uJ", "reasons": reasons, "events": events,
                "command": command,
                "domains": [{"domain": d, "kind": "system-wide (all sockets summed by perf)"} for d in supported]}

    def _probe_turbostat(self):
        command = ["turbostat", "--quiet", "--Summary", "--show", ",".join(TurbostatParser.COLUMNS)]
        done, reason = self._run_probe(command + ["--interval", "0.2", "--num_iterations", "1"])
        if done is None:
            return {"status": "unavailable", "reasons": [reason]}
        parser, seen, reasons, peak = TurbostatParser(), [], [], {}
        for line in (done.stdout or "").splitlines():
            try:
                for _, domain, _, watts in parser.parse(line):
                    seen.append(domain)
                    peak[domain] = max(peak.get(domain, 0.0), watts)
            except ValueError as exc:
                reasons.append(str(exc))
        if seen and not any(peak.values()):
            return {"status": "unavailable", "reasons": reasons + [
                "turbostat reports 0 W for every domain: counters present but not counting (a zero is not a measurement)"]}
        if not seen:
            tail = ((done.stderr or "") + (done.stdout or "")).strip().splitlines()
            return {"status": "unavailable", "reasons": reasons + [f"no PkgWatt/RAMWatt values (turbostat exit "
                    f"{done.returncode}): {tail[-1][:200] if tail else 'no output'}"]}
        self.stream = ("turbostat", command + ["--interval", f"{max(self.interval, 0.1):g}"], TurbostatParser)
        return {"status": "active", "kind": "power_reading", "unit": "W", "reasons": reasons, "command": self.stream[1],
                "domains": [{"domain": d, "kind": "system-wide (all sockets summed by turbostat)"} for d in seen]}

    def _probe_ipmi(self):
        done, reason = self._run_probe(self.ipmi_command + ["dcmi", "power", "reading"])
        if done is None:
            return {"status": "unavailable", "reasons": [reason]}
        if done.returncode != 0:
            tail = ((done.stderr or "") + (done.stdout or "")).strip().splitlines()
            return {"status": "unavailable", "reasons": [f"exit {done.returncode}: {tail[-1][:200] if tail else 'no output'}"]}
        try:
            watts = parse_ipmi_power(done.stdout or "")
        except ValueError as exc:
            return {"status": "unavailable", "reasons": [str(exc)]}
        if watts <= 0:
            return {"status": "unavailable", "reasons": [
                f"BMC reports {watts} W for the whole node: not a measurement (a running node never draws 0 W)"]}
        self.ipmi_active = True
        return {"status": "active", "kind": "power_reading", "unit": "W", "reasons": [], "probe_reading_w": watts,
                "command": self.ipmi_command + ["dcmi", "power", "reading"],
                "domains": [{"domain": "node", "kind": "whole node (BMC, integer watts)"}]}

    # -- sampling
    def _write(self, rows):
        if not rows:
            return
        with self.lock:
            self.writer.writerows(rows)
            self.handle.flush()
            for row in rows:
                self.rows[row[2]] = self.rows.get(row[2], 0) + 1

    def _error(self, key, exc):
        with self.lock:
            entry = self.errors.setdefault(key, {"count": 0, "last": None})
            entry["count"] += 1
            entry["last"] = describe(exc) if isinstance(exc, BaseException) else str(exc)

    def poll_counters(self):
        """One pass over the active counter zones; every row of the pass shares one timestamp pair."""
        began = time.perf_counter()
        wall, mono = utc_now(), time.monotonic_ns()
        labels = tuple(self.labels())
        rows = []
        for zone, series in self.series:
            try:
                raw = read_int(zone.counter)
            except (OSError, ValueError) as exc:
                self._error(zone.zone, exc)
                continue
            energy, flag = series.update(raw, mono)
            self._track(f"{zone.source}/{zone.zone}", raw, mono, "energy")
            rows.append((wall, mono, zone.source, zone.zone, zone.domain, energy, "", raw, flag, *labels))
        self._write(rows)
        took = time.perf_counter() - began
        self.polls += 1
        self.poll_seconds_total += took
        self.poll_seconds_max = max(self.poll_seconds_max, took)

    def poll_ipmi(self):
        """One `ipmitool dcmi power reading`; returns False after a failure."""
        try:
            done = self.run(self.ipmi_command + ["dcmi", "power", "reading"], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS)
            if done.returncode != 0:
                raise RuntimeError(f"exit {done.returncode}: {(done.stderr or done.stdout or '').strip()[:160]}")
            watts = parse_ipmi_power(done.stdout or "")
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            self._error(IPMI_SOURCE, exc)
            return False
        wall, mono = utc_now(), time.monotonic_ns()
        self._track(f"{IPMI_SOURCE}/dcmi", watts, mono, "power")
        self._write([(wall, mono, IPMI_SOURCE, "dcmi", "node", "", watts, "", "", *tuple(self.labels()))])
        return True

    def _track(self, key, value, mono, kind):
        """Running tally of one series: how often its value changed and how long it stayed flat."""
        tally = self.live.get(key)
        if tally is None:
            self.live[key] = {"kind": kind, "samples": 1, "changes": 0, "zeros": int(value == 0), "last": value,
                              "last_change": mono, "last_mono": mono, "longest_flat": 0}
            return
        tally["samples"] += 1
        tally["last_mono"] = mono
        tally["zeros"] += int(value == 0)
        if value != tally["last"]:
            tally["changes"] += 1
            tally["longest_flat"] = max(tally["longest_flat"], mono - tally["last_change"])
            tally["last"], tally["last_change"] = value, mono

    def liveness_report(self) -> dict:
        """Per series: did it advance? Energy counters must change; power readings must not be all zero."""
        out = {}
        for key, tally in sorted(self.live.items()):
            flat = max(tally["longest_flat"], tally["last_mono"] - tally["last_change"]) / 1e9
            if tally["kind"] == "energy":
                verdict = ("insufficient_samples" if tally["samples"] < 3 else "never_advanced" if tally["changes"] == 0
                           else "stalled" if flat > STALL_SECONDS else "advancing")
            else:
                verdict = "all_zero" if tally["zeros"] == tally["samples"] else "reading"
            out[key] = {"kind": tally["kind"], "verdict": verdict, "samples": tally["samples"],
                        "changes": tally["changes"], "longest_flat_seconds": round(flat, 3)}
        return out

    def _paced(self, name, interval, step, final=None):
        """Run step() every interval on a deadline schedule (no drift; missed ticks are skipped, not bursted)."""
        deadline = time.monotonic()
        try:
            while True:
                try:
                    stop = step() is False
                except Exception as exc:      # one failed pass must not end the sampler
                    self._error(name, exc)
                    stop = False
                if stop:
                    break
                deadline += interval
                delay = deadline - time.monotonic()
                if delay < 0:
                    self.overruns += 1
                    deadline, delay = time.monotonic(), 0
                if self.stop_event.wait(delay):
                    if final is not None:
                        final()
                    break
        finally:
            self.cpu_seconds[name] = time.thread_time()

    def _counter_loop(self):
        last = [None]

        def step():
            now = time.monotonic()
            if last[0] is not None:
                self.gap_max = max(self.gap_max, now - last[0])
            last[0] = now
            self.poll_counters()
        self._paced("counters", self.interval, step, final=self.poll_counters)   # final: closes the last interval

    def _ipmi_loop(self):
        failures = [0]

        def step():
            failures[0] = 0 if self.poll_ipmi() else failures[0] + 1
            if failures[0] >= IPMI_MAX_CONSECUTIVE_FAILURES:
                self._error(IPMI_SOURCE, f"stopped after {failures[0]} consecutive failures")
                return False
        self._paced("ipmi", self.ipmi_interval, step)

    def _stream_loop(self, process, source, parser):
        try:
            for line in process.stdout:
                wall, mono = utc_now(), time.monotonic_ns()
                try:
                    items = parser.parse(line)
                except ValueError as exc:
                    self._error(source, exc)
                    continue
                labels = tuple(self.labels())
                for name, domain, energy, power in items:
                    self._track(f"{source}/{name}", energy if power is None else power, mono,
                                "energy" if power is None else "power")
                self._write([(wall, mono, source, name, domain, "" if energy is None else energy,
                              "" if power is None else power, "", "", *labels) for name, domain, energy, power in items])
        finally:
            self.cpu_seconds[source] = time.thread_time()

    def start(self, background=True) -> dict:
        """Probe, create power.csv and the sidecar, start the sampler threads. Returns the sidecar dict.

        background=False leaves the threads off so a test can drive poll_counters()/poll_ipmi() itself."""
        self.dir.mkdir(parents=True, exist_ok=True)
        self.report = self.probe()
        self.report["clock"] = {"start": clock_anchor()}
        self.handle = self.csv_path.open("w", newline="")
        self.writer = csv.writer(self.handle)
        self.writer.writerow(CSV_COLUMNS)
        self.handle.flush()
        self.started_mono, self.started_perf = time.monotonic(), time.perf_counter()
        if self.series and background:
            self._spawn(self._counter_loop, "power-counters")
        if self.ipmi_active and background:
            self._spawn(self._ipmi_loop, "power-ipmi")
        if self.stream and background:
            source, command, parser = self.stream
            try:
                process = self.popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, bufsize=1)
            except OSError as exc:
                self._error(source, exc)
            else:
                self.processes.append(process)
                self._spawn(lambda: self._stream_loop(process, source, parser()), f"power-{source}")
        self.report["state"] = "running"
        write_json(self.sidecar_path, self.report)
        return self.report

    def _spawn(self, target, name):
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self.threads.append(thread)

    def stop(self) -> dict:
        """Stop the threads, close the csv, finalise the sidecar. Safe to call twice."""
        if self.handle is None or self.handle.closed:
            return self.report
        self.stop_event.set()
        for process in self.processes:
            try:
                process.terminate()
            except OSError:
                pass
        for thread in self.threads:
            thread.join(timeout=15)
        for process in self.processes:
            try:
                process.wait(timeout=5)
            except Exception:
                process.kill()
        wall_seconds = time.perf_counter() - self.started_perf
        cpu = sum(self.cpu_seconds.values())
        self.report.update(state="finished", samples={"rows": dict(self.rows), "total": sum(self.rows.values())},
                           read_errors=self.errors, liveness=self.liveness_report())
        self._demote_dead_series()
        self.report["clock"]["stop"] = clock_anchor()
        self.report["overhead"] = {
            "sampler_thread_cpu_seconds": {k: round(v, 6) for k, v in sorted(self.cpu_seconds.items())},
            "wall_seconds": round(wall_seconds, 3),
            "cpu_fraction_of_one_core": round(cpu / wall_seconds, 6) if wall_seconds > 0 else None,
            "counter_polls": self.polls,
            "mean_poll_ms": round(1000 * self.poll_seconds_total / self.polls, 4) if self.polls else None,
            "max_poll_ms": round(1000 * self.poll_seconds_max, 4),
            "max_gap_between_polls_seconds": round(self.gap_max, 4),
            "overruns": self.overruns,
            "note": "thread CPU time of the sampler threads; child processes (ipmitool, perf, turbostat) are not included"}
        with self.lock:
            self.handle.close()
        write_json(self.sidecar_path, self.report)
        return self.report

    def _demote_dead_series(self):
        """A series that never advanced (or stalled, or read only 0 W) is not a measurement: say so in the sidecar."""
        dead = {k: v for k, v in self.report["liveness"].items() if v["verdict"] in ("never_advanced", "stalled", "all_zero")}
        if not dead:
            return
        self.report["status_at_start"] = self.report["status"]
        self.report["reasons"] += [f"{key}: {v['verdict']} over {v['samples']} samples (flat for {v['longest_flat_seconds']} s): "
                                   "its rows are not energy or power readings and must not be used" for key, v in dead.items()]
        energy = [k for k, v in self.report["liveness"].items() if v["kind"] == "energy"]
        if energy and all(k in dead for k in energy) and self.report["cpu_energy_source"]:
            self.report["cpu_energy_source_rejected"] = self.report["cpu_energy_source"]
            self.report["cpu_energy_source"] = None
        if len(dead) == len(self.report["liveness"]):
            self.report["status"] = "unavailable"
        elif self.report["status"] == "available":
            self.report["status"] = "partial"

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()


# ---- CPU topology, socket pinning and the CPU-energy gate ---------------------------------------------

class PinningError(ValueError):
    """The requested CPU pinning cannot be applied on this host; the message says why."""


def parse_cpu_list(text, allow_empty=False) -> list:
    """The kernel's cpulist syntax ("0-47,96-143") as a sorted list of unique CPU numbers; ValueError on anything else."""
    cpus = set()
    for part in str(text).strip().split(","):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if match is None:
            raise ValueError(f"not a CPU list: {str(text).strip()!r} (use e.g. 0-47,96-143)")
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) is not None else first
        if last < first or last > MAX_CPU_NUMBER:
            raise ValueError(f"not a CPU list: {str(text).strip()!r} (bad range {part!r})")
        cpus.update(range(first, last + 1))
    if not cpus and not allow_empty:
        raise ValueError(f"empty CPU list: {str(text).strip()!r}")
    return sorted(cpus)


def format_cpu_list(cpus) -> str:
    """[0, 1, 2, 3, 8] -> "0-3,8" (the inverse of parse_cpu_list)."""
    ordered, parts = sorted(set(cpus)), []
    start = previous = None
    for cpu in ordered:
        if start is None:
            start = previous = cpu
        elif cpu == previous + 1:
            previous = cpu
        else:
            parts.append(str(start) if start == previous else f"{start}-{previous}")
            start = previous = cpu
    if start is not None:
        parts.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(parts)


def cpu_topology(cpu_root=None, node_root=None) -> dict:
    """Sockets of this host from sysfs: {"sockets": [{socket, cpus, cpu_count, physical_cores, numa_nodes}],
    "numa": {node: cpulist}, "problems": [...]}.

    A CPU belongs to the socket in its topology/physical_package_id; offline CPUs have no topology directory and are not
    listed. numa_nodes are the nodes holding any of the socket's CPUs (a socket can span several: sub-NUMA clustering).
    Never raises; a tree that cannot be read comes back with no sockets and the reason in "problems"."""
    cpu_root, node_root = Path(cpu_root or SYS_CPU_ROOT), Path(node_root or SYS_NODE_ROOT)
    problems, package, core = [], {}, {}
    try:
        entries = [p for p in cpu_root.iterdir() if re.fullmatch(r"cpu\d+", p.name)]
    except OSError as exc:
        return {"sockets": [], "numa": {}, "problems": [f"cannot list {cpu_root}: {describe(exc)}"]}
    for entry in entries:
        number = int(entry.name[3:])
        try:
            package[number] = int(read_text(entry / "topology" / "physical_package_id") or "")
        except ValueError:
            continue                                  # offline, or no topology exported
        try:
            core[number] = int(read_text(entry / "topology" / "core_id") or "")
        except ValueError:
            core[number] = number
    if not package:
        problems.append(f"no cpu*/topology/physical_package_id under {cpu_root}")
    nodes = {}
    try:
        for entry in node_root.iterdir():
            if re.fullmatch(r"node\d+", entry.name):
                try:
                    nodes[int(entry.name[4:])] = set(parse_cpu_list(read_text(entry / "cpulist") or "", allow_empty=True))
                except ValueError:
                    problems.append(f"{entry / 'cpulist'} is not a CPU list")
    except OSError:
        pass                                          # no NUMA information: numa_nodes stays empty
    sockets = []
    for socket in sorted(set(package.values())):
        cpus = sorted(c for c, s in package.items() if s == socket)
        sockets.append({"socket": socket, "cpus": format_cpu_list(cpus), "cpu_count": len(cpus),
                        "physical_cores": len({core[c] for c in cpus}),
                        "numa_nodes": sorted(n for n, members in nodes.items() if members & set(cpus))})
    return {"sockets": sockets, "numa": {str(n): format_cpu_list(members) for n, members in sorted(nodes.items())},
            "problems": problems}


def available_cpus():
    """CPUs this process may run on (os.sched_getaffinity), or None where the platform has no such call."""
    getter = getattr(os, "sched_getaffinity", None)
    return sorted(getter(0)) if getter else None


def resolve_pinning(socket=None, cpu_list=None, *, topology=None, allowed="detect"):
    """What `--socket N` / `--cpu-list L` mean on this host, or None when neither was asked for.

    Returns {"requested": {"socket", "cpu_list"}, "cpus": "0-47,96-143", "cpu_count", "sockets": [...] or None,
    "numa_nodes": [...], "topology_basis"}. PinningError when both were given, the socket does not exist, a listed CPU is
    not online, or the CPUs are not all allowed to this process (cgroup cpuset, an earlier taskset). `topology` defaults
    to cpu_topology(); `allowed` to available_cpus() (None skips that check)."""
    if socket is None and cpu_list is None:
        return None
    if socket is not None and cpu_list is not None:
        raise PinningError("--socket and --cpu-list are alternatives: give one")
    topology = cpu_topology() if topology is None else topology
    by_socket = {entry["socket"]: entry for entry in topology["sockets"]}
    if socket is not None:
        if socket not in by_socket:
            known = ", ".join(map(str, sorted(by_socket))) or "none readable"
            raise PinningError(f"--socket {socket}: no such socket on this host (sockets: {known}"
                               + (f"; {topology['problems'][0]}" if topology["problems"] else "") + ")")
        wanted = parse_cpu_list(by_socket[socket]["cpus"])
    else:
        try:
            wanted = parse_cpu_list(cpu_list)
        except ValueError as exc:
            raise PinningError(f"--cpu-list: {exc}") from None
        if by_socket:
            online = {c for entry in by_socket.values() for c in parse_cpu_list(entry["cpus"])}
            absent = [c for c in wanted if c not in online]
            if absent:
                raise PinningError(f"--cpu-list: CPU(s) {format_cpu_list(absent)} are not online on this host "
                                   f"(online: {format_cpu_list(online)})")
    allowed = available_cpus() if allowed == "detect" else allowed
    if allowed is not None:
        denied = [c for c in wanted if c not in set(allowed)]
        if denied:
            raise PinningError(f"CPU(s) {format_cpu_list(denied)} are not allowed to this process (cpuset or an earlier "
                               f"taskset limits it to {format_cpu_list(allowed)})")
    covered = [entry for entry in by_socket.values() if set(parse_cpu_list(entry["cpus"])) & set(wanted)]
    return {"requested": {"socket": socket, "cpu_list": cpu_list}, "cpus": format_cpu_list(wanted), "cpu_count": len(wanted),
            "sockets": sorted(entry["socket"] for entry in covered) if by_socket else None,
            "whole_sockets": sorted(entry["socket"] for entry in covered
                                    if set(parse_cpu_list(entry["cpus"])) <= set(wanted)) if by_socket else None,
            "numa_nodes": sorted(int(n) for n, members in topology.get("numa", {}).items()
                                 if set(parse_cpu_list(members, allow_empty=True)) & set(wanted)),
            "topology_basis": "/sys/devices/system/cpu/cpu*/topology/physical_package_id" if by_socket else "unavailable"}


def apply_pinning(cpus, setaffinity=None, getaffinity=None) -> str:
    """Pin this process, and so every child it starts, to `cpus` (cpulist text); returns the affinity read back.

    The mask is inherited across fork and exec, so the runner pins itself once, before it starts any worker, Spark JVM or
    sampler. Memory is not bound: Linux allocates on the node of the CPU that touches a page. PinningError off Linux or
    when the kernel does not end up with exactly the requested set."""
    setter = setaffinity or getattr(os, "sched_setaffinity", None)
    getter = getaffinity or getattr(os, "sched_getaffinity", None)
    if setter is None or getter is None:
        raise PinningError(f"CPU pinning needs Linux (os.sched_setaffinity); this is {platform.system() or 'unknown'}")
    wanted = parse_cpu_list(cpus)
    try:
        setter(0, set(wanted))
    except OSError as exc:
        raise PinningError(f"sched_setaffinity({format_cpu_list(wanted)}) failed: {describe(exc)}") from None
    got = sorted(getter(0))
    if got != wanted:
        raise PinningError(f"affinity after pinning is {format_cpu_list(got)}, wanted {format_cpu_list(wanted)}")
    return format_cpu_list(got)


def process_affinity(getaffinity=None) -> dict:
    """The CPUs the calling process may run on, as recorded in results.json: {"cpus": "0-47,96-143", "count": 96}."""
    getter = getaffinity or getattr(os, "sched_getaffinity", None)
    if getter is None:
        return {"cpus": None, "count": None, "source": "unavailable: no os.sched_getaffinity on this platform"}
    cpus = sorted(getter(0))
    return {"cpus": format_cpu_list(cpus), "count": len(cpus), "source": "os.sched_getaffinity(0) in the worker"}


# ---- NUMA memory binding for socket mode --------------------------------------------------------------

class NumaPolicyApi:
    """set_mempolicy / get_mempolicy through ctypes.

    libnuma's wrappers when libnuma loads (they know the syscall numbers of every architecture); otherwise the raw
    syscalls through libc on Linux x86_64 and aarch64. Anything else leaves .set empty and says why in .reasons.
    loader is ctypes.CDLL unless a test passes a fake; machine and system are platform.machine() / sys.platform."""

    def __init__(self, loader=None, machine=None, system=None):
        loader = loader or (lambda name: ctypes.CDLL(name, use_errno=True))
        self.mechanism, self.reasons, self._set, self._get = None, [], None, None
        if not (system or sys.platform).startswith("linux"):
            self.reasons.append(f"NUMA memory policy needs Linux (this is {system or sys.platform})")
            return
        for name in ("libnuma.so.1", "libnuma.so"):
            try:
                lib = loader(name)
                set_policy, get_policy = lib.set_mempolicy, lib.get_mempolicy
            except (OSError, AttributeError) as exc:
                self.reasons.append(f"{name}: {describe(exc)}")
                continue
            self._set = lambda mode, mask, maxnode, f=set_policy: f(ctypes.c_int(mode), mask, ctypes.c_ulong(maxnode))
            self._get = lambda mode_ref, mask, maxnode, f=get_policy: f(mode_ref, mask, ctypes.c_ulong(maxnode), None, ctypes.c_int(0))
            self.mechanism = f"libnuma ({name}) set_mempolicy"
            return
        numbers = MEMPOLICY_SYSCALLS.get((machine or platform.machine()).lower())
        if numbers is None:
            self.reasons.append(f"no known set_mempolicy syscall number for {machine or platform.machine()}")
            return
        try:
            syscall = loader(None).syscall
        except (OSError, AttributeError, TypeError) as exc:
            self.reasons.append(f"libc syscall(): {describe(exc)}")
            return
        set_number, get_number = numbers
        self._set = lambda mode, mask, maxnode: syscall(ctypes.c_long(set_number), ctypes.c_long(mode), mask, ctypes.c_ulong(maxnode))
        self._get = lambda mode_ref, mask, maxnode: syscall(ctypes.c_long(get_number), mode_ref, mask, ctypes.c_ulong(maxnode),
                                                           None, ctypes.c_ulong(0))
        self.mechanism = f"set_mempolicy syscall {set_number} through libc"

    @property
    def usable(self) -> bool:
        return self._set is not None

    @staticmethod
    def _mask(nodes, words=None):
        highest = max(nodes) if nodes else 0
        words = words or (highest // 64 + 1)
        mask = (ctypes.c_ulong * words)()
        for node in nodes:
            mask[node // 64] |= 1 << (node % 64)
        return mask, words * 64 + 1                  # the kernel reads maxnode - 1 bits

    @staticmethod
    def _check(result, call):
        if result not in (0, None):
            code = ctypes.get_errno() or errno.EINVAL
            raise OSError(code, f"{call}: {os.strerror(code)}")

    def set_membind(self, nodes) -> None:
        """MPOL_BIND to `nodes` for this task (inherited by fork, kept across exec)."""
        if not self.usable:
            raise OSError(errno.ENOSYS, "; ".join(self.reasons) or "set_mempolicy is not available")
        mask, maxnode = self._mask(list(nodes))
        self._check(self._set(MPOL_BIND, mask, maxnode), "set_mempolicy")

    def get_policy(self):
        """(mode, [nodes]) of this task's policy, as the kernel reports it."""
        if not self.usable:
            raise OSError(errno.ENOSYS, "; ".join(self.reasons) or "get_mempolicy is not available")
        mode = ctypes.c_int(-1)
        mask, maxnode = self._mask([], words=MEMBIND_MAX_NODES // 64)
        self._check(self._get(ctypes.byref(mode), mask, maxnode), "get_mempolicy")
        nodes = [64 * word + bit for word in range(len(mask)) for bit in range(64) if mask[word] >> bit & 1]
        return int(mode.value), nodes


def read_numa_maps_policy(path="/proc/self/numa_maps"):
    """The task memory policy as /proc/self/numa_maps shows it: "bind:0-1", "default", ... or None when unreadable.

    Every mapping without a policy of its own is printed with the task policy; mappings that say otherwise are listed
    by their policy too, so the result is the set of distinct policies."""
    text = read_text(Path(path))
    if text is None:
        return None
    policies = set()
    for line in text.splitlines():
        words = line.split()
        if len(words) >= 2:
            policies.add(words[1])
    return sorted(policies)


def plan_membind(pinning, no_membind=False):
    """What socket mode does about memory (None when the run is not pinned): bind to the NUMA nodes holding the pinned CPUs.

    The design attributes S2-pinned DRAM energy to the pinned socket only, so the engine's memory must come from that
    socket's nodes. {"enabled", "policy", "nodes", "reason"}; enabled is False with --no-membind (recorded, not silent) and
    when sysfs lists no NUMA node (nothing to bind to)."""
    if pinning is None:
        return None
    nodes = list(pinning.get("numa_nodes") or [])
    if no_membind:
        return {"enabled": False, "policy": "default", "nodes": nodes,
                "reason": "--no-membind: memory is not bound; the kernel may place this run's pages on other sockets, "
                          "so DRAM energy attributed to the pinned socket is not guaranteed to be this run's"}
    if not nodes:
        return {"enabled": False, "policy": "default", "nodes": [], "reason": "sysfs lists no NUMA node for the pinned CPUs "
                "(a kernel without NUMA, or one memory node): there is nothing to bind to"}
    return {"enabled": True, "policy": "MPOL_BIND", "nodes": nodes,
            "reason": "memory of the engine processes is bound to the NUMA nodes of the pinned CPUs"}


def apply_membind(nodes, *, api=None, environ=None, which=None, reexec=None, argv=None, numa_maps=None) -> dict:
    """Bind this process's memory, and so every child's, to NUMA `nodes`; returns the record for environment.json.

    First choice set_mempolicy through ctypes, read back with get_mempolicy. If that cannot be done (a seccomp profile, an
    unknown architecture) and numactl is installed, the process re-executes itself under `numactl --membind=...` (the
    environment variable MEMBIND_GUARD_ENV tells the new process to verify instead of re-executing) and never returns
    here. PinningError when neither works."""
    environ = os.environ if environ is None else environ
    wanted = sorted(set(nodes))
    text = format_cpu_list(wanted)
    if environ.get(MEMBIND_GUARD_ENV) == text:                    # re-executed under numactl by an earlier call
        record = {"policy": "MPOL_BIND", "nodes": wanted, "mechanism": "numactl --membind (re-executed run_experiment.py)"}
        api = api or NumaPolicyApi()
        try:
            mode, got = api.get_policy()
            verified = mode == MPOL_BIND and got == wanted
            record["readback"] = {"source": "get_mempolicy", "mode": mode, "nodes": got}
        except OSError:
            policies = numa_maps() if numa_maps else read_numa_maps_policy()
            record["readback"] = {"source": "/proc/self/numa_maps", "policies": policies}
            verified = policies is not None and f"bind:{text}" in policies and not [p for p in policies if p.startswith(("interleave", "prefer"))]
        if not verified:
            raise PinningError(f"numactl --membind={text} did not leave the process bound to NUMA node(s) {text}: {record['readback']}")
        return {**record, "verified": True}
    api = api or NumaPolicyApi()
    failure = None
    try:
        api.set_membind(wanted)
        mode, got = api.get_policy()
        if mode == MPOL_BIND and got == wanted:
            return {"policy": "MPOL_BIND", "nodes": wanted, "mechanism": api.mechanism, "verified": True,
                    "readback": {"source": "get_mempolicy", "mode": mode, "nodes": got}}
        failure = f"get_mempolicy reports mode {mode} nodes {got} after set_mempolicy(MPOL_BIND, {wanted})"
    except OSError as exc:
        failure = describe(exc)
    numactl = (which or shutil.which)("numactl")
    if numactl is None:
        raise PinningError(f"memory cannot be bound to NUMA node(s) {text}: set_mempolicy failed ({failure}) and numactl is not "
                           "installed. Install numactl (apt install numactl) so the run can re-execute under it, or pass "
                           "--no-membind to run without memory binding (recorded; DRAM attribution is then not guaranteed)")
    environ[MEMBIND_GUARD_ENV] = text
    command = [numactl, f"--membind={text}", "--", sys.executable, *(sys.argv if argv is None else argv)]
    (reexec or os.execv)(numactl, command)
    raise PinningError("re-execution under numactl returned")   # only reachable with a test double


def probe_membind(nodes, *, run=None, script=None, timeout=60) -> dict:
    """Can this host bind memory to `nodes`? Answered by a short child process, so the caller's own policy is untouched.

    {"ok", "mechanism", "numactl", "libnuma", "reasons"}: the child tries set_mempolicy through ctypes (and reads it back),
    then, if that fails and numactl exists, runs itself once under numactl and reads the policy from /proc/self/numa_maps."""
    run = run or subprocess.run
    command = [sys.executable, str(script or Path(__file__).resolve()), "--probe-membind", "--nodes", format_cpu_list(nodes)]
    try:
        done = run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "mechanism": None, "numactl": shutil.which("numactl"), "libnuma": None,
                "reasons": [f"membind probe could not run: {describe(exc)}"]}
    output = done.stdout or ""
    try:
        return json.loads(output[output.index("{"):])
    except ValueError:
        tail = ((done.stderr or "") + output).strip().splitlines()
        return {"ok": False, "mechanism": None, "numactl": shutil.which("numactl"), "libnuma": None,
                "reasons": [f"membind probe printed no JSON (exit {done.returncode}): {tail[-1][:200] if tail else 'no output'}"]}


def membind_probe_report(nodes, *, api=None, which=None, run=None, python=None) -> dict:
    """What `--probe-membind` prints: see probe_membind."""
    api = api or NumaPolicyApi()
    which = which or shutil.which
    wanted = sorted(set(nodes))
    report = {"ok": False, "nodes": wanted, "mechanism": api.mechanism, "numactl": which("numactl"),
              "libnuma": api.mechanism if (api.mechanism or "").startswith("libnuma") else None, "reasons": []}
    try:
        api.set_membind(wanted)
        mode, got = api.get_policy()
        if mode == MPOL_BIND and got == wanted:
            return {**report, "ok": True, "readback": {"mode": mode, "nodes": got}, "notes": list(api.reasons)}
        report["reasons"].append(f"get_mempolicy reports mode {mode} nodes {got}")
    except OSError as exc:
        report["reasons"].append(describe(exc))
    if report["numactl"] is None:
        report["reasons"].append("numactl is not installed")
        return report
    text = format_cpu_list(wanted)
    run = run or subprocess.run
    try:
        done = run([report["numactl"], f"--membind={text}", "--", python or sys.executable, str(Path(__file__).resolve()),
                    "--read-membind"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        policies = json.loads((done.stdout or "")[(done.stdout or "").index("["):]) if done.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        report["reasons"].append(f"numactl --membind={text}: {describe(exc)}")
        return report
    if policies is not None and f"bind:{text}" in policies:
        return {**report, "ok": True, "mechanism": "numactl --membind (re-exec)", "readback": {"policies": policies}}
    report["reasons"].append(f"numactl --membind={text} did not bind the child (policies seen: {policies})"
                             if policies is not None else f"numactl --membind={text} failed (exit {done.returncode}): "
                             f"{((done.stderr or '') + (done.stdout or '')).strip()[-200:]}")
    return report


def rapl_zones(powercap_root=None, hwmon_root=None):
    """(zones, absent_reasons): every CPU energy counter file the kernel exposes, readable by this user or not."""
    found_powercap, reasons_powercap = discover_powercap(Path(powercap_root or POWERCAP_ROOT))
    found_amd, reasons_amd = discover_amd_energy(Path(hwmon_root or HWMON_ROOT))
    zones = found_powercap + found_amd
    return zones, ([] if zones else reasons_powercap + reasons_amd)


def check_cpu_energy(mode="auto", *, powercap_root=None, hwmon_root=None, sleep=None, sampler=None) -> dict:
    """Is CPU energy measurable here, and if the counters exist, can this user read them?

    {"mode", "ok", "state", "problems", "warnings", "zones": {"total", "unreadable", "not_advancing"}, "euid"}. state is
      skipped      mode off
      absent       no RAPL/amd_energy counter file at all (a VM, or a CPU without it); fine in auto, a problem in require
                   unless perf/turbostat can supply the energy
      unreadable   counters exist but this user cannot read them: never fall back silently to "unavailable"; ok is False
      readable     every counter reads; counters that do not advance are a warning (auto) or a problem (require)
    mode require additionally runs the sampler's own probe and needs a usable CPU energy source from it."""
    if mode not in CPU_ENERGY_MODES:
        raise ValueError(f"mode must be one of {CPU_ENERGY_MODES}, got {mode!r}")
    result = {"mode": mode, "ok": True, "state": "skipped", "problems": [], "warnings": [],
              "zones": {"total": 0, "unreadable": 0, "not_advancing": 0}, "euid": euid()}
    if mode == "off":
        return result
    zones, absent = rapl_zones(powercap_root, hwmon_root)
    result["zones"]["total"] = len(zones)
    unreadable = [z for z in zones if z.error]
    if unreadable:
        result.update(ok=False, state="unreadable")
        result["zones"]["unreadable"] = len(unreadable)
        denied = any("PermissionError" in z.error for z in unreadable)
        result["problems"].append(
            f"CPU energy counters exist ({len(zones)} zones) but {len(unreadable)} cannot be read by uid {euid()}: "
            f"{unreadable[0].zone}: {unreadable[0].error}. "
            + ("They are root-only since Linux 5.10 (CVE-2020-8694): " + RAPL_READ_FIX if denied else
               "Fix the read error, or pass --cpu-energy off to run without CPU energy"))
        return result
    if not zones:
        result["state"] = "absent"
        if mode == "auto":
            result["warnings"].append("no CPU energy counter (RAPL, amd_energy) on this host: CPU energy will be recorded as "
                                      "unavailable (" + "; ".join(absent[:2]) + ")")
    else:
        result["state"] = "readable"
        stuck = check_advancing(zones, LIVENESS_SECONDS, sleep or time.sleep)
        result["zones"]["not_advancing"] = len(stuck)
        if stuck:
            result["warnings" if mode == "auto" else "problems"].append(
                f"{len(stuck)} of {len(zones)} CPU energy counters are readable but do not advance over {LIVENESS_SECONDS:g} s "
                f"({stuck[0].zone} stays at one value): not an energy reading")
    if mode == "require":
        probe = (sampler or PowerSampler(Path("."), ipmi=False, powercap_root=powercap_root, hwmon_root=hwmon_root,
                                         **({"sleep": sleep} if sleep else {}))).probe()
        result["sampler_status"], result["cpu_energy_source"] = probe["status"], probe["cpu_energy_source"]
        if probe["cpu_energy_source"] is None or probe["status"] != "available":
            result["ok"] = False
            result["problems"].append(
                f"--cpu-energy require: the CPU energy sampler is {probe['status']} here"
                + (" (" + "; ".join(probe["reasons"][:3]) + ")" if probe["reasons"] else "")
                + (": " + "; ".join(probe["setup_hints"][:1]) if probe["setup_hints"] else ""))
    if result["problems"]:
        result["ok"] = False
    return result


# ---- command line -------------------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--probe", action="store_true", help="print what this host can measure and exit (3 if nothing)")
    parser.add_argument("--probe-gpu-energy", action="store_true",
                        help="report the GPU energy counter source and how it advances over --duration seconds (3 if none)")
    parser.add_argument("--gpu-device", type=int, default=0)
    parser.add_argument("--topology", action="store_true",
                        help="print this host's sockets (CPU lists, physical cores, NUMA nodes): what --socket/--cpu-list select")
    parser.add_argument("--probe-membind", action="store_true",
                        help="bind this process's memory to --nodes (set_mempolicy, else numactl) and report whether it worked; "
                             "exit 5 when it did not")
    parser.add_argument("--read-membind", action="store_true",
                        help="print the memory policies /proc/self/numa_maps shows (used to verify a numactl --membind child)")
    parser.add_argument("--nodes", default="0", help="NUMA nodes for --probe-membind (cpulist syntax)")
    parser.add_argument("--check-cpu-energy", nargs="?", const="auto", choices=["auto", "require"],
                        help="exit 4 when the CPU energy counters exist but this user cannot read them (also, with require, "
                             "when no usable CPU energy source exists); prints the verdict as JSON")
    parser.add_argument("--output", type=Path, help="directory for power.csv and power_sources.json")
    parser.add_argument("--duration", type=float, default=60.0, help="seconds to sample standalone")
    parser.add_argument("--interval-ms", type=float, default=DEFAULT_INTERVAL_SECONDS * 1000)
    parser.add_argument("--ipmi-interval-seconds", type=float, default=DEFAULT_IPMI_INTERVAL_SECONDS)
    parser.add_argument("--no-ipmi", action="store_true")
    parser.add_argument("--include-hostname", action="store_true",
                        help="record the hostname in the sidecar (off by default: cloud hostnames can encode the public IP)")
    parser.add_argument("--liveness-seconds", type=float, default=LIVENESS_SECONDS,
                        help="probe-time check that each energy counter changes within this long (0 skips it)")
    parser.add_argument("--powercap-root", type=Path, help="alternative powercap tree (tests, containers)")
    parser.add_argument("--hwmon-root", type=Path)
    parser.add_argument("--cpu-root", type=Path, help="alternative /sys/devices/system/cpu (tests, containers)")
    parser.add_argument("--node-root", type=Path, help="alternative /sys/devices/system/node")
    args = parser.parse_args(argv)
    if args.interval_ms <= 0 or args.ipmi_interval_seconds <= 0 or args.duration < 0:
        parser.error("--interval-ms, --ipmi-interval-seconds must be positive and --duration non-negative")
    if args.topology:
        json.dump(cpu_topology(args.cpu_root, args.node_root), sys.stdout, indent=2)
        print()
        return 0
    if args.read_membind:
        json.dump(read_numa_maps_policy(), sys.stdout)
        print()
        return 0
    if args.probe_membind:
        try:
            nodes = parse_cpu_list(args.nodes)
        except ValueError as exc:
            parser.error(f"--nodes: {exc}")
        report = membind_probe_report(nodes)
        json.dump(report, sys.stdout, indent=2, default=str)
        print()
        return 0 if report["ok"] else 5
    if args.check_cpu_energy:
        verdict = check_cpu_energy(args.check_cpu_energy, powercap_root=args.powercap_root, hwmon_root=args.hwmon_root)
        json.dump(verdict, sys.stdout, indent=2, default=str)
        print()
        return 0 if verdict["ok"] else 4
    if args.probe_gpu_energy:
        reader = GpuEnergyReader(args.gpu_device)
        report = {**reader.describe(), "resolution": probe_gpu_energy_resolution(reader, args.duration)
                  if reader.source != GPU_ENERGY_UNAVAILABLE else None}
        json.dump(report, sys.stdout, indent=2, default=str)
        print()
        return 3 if reader.source == GPU_ENERGY_UNAVAILABLE else 0
    if not args.probe and args.output is None:
        parser.error("--output is required unless --probe")
    sampler = PowerSampler(args.output or Path("."), interval=args.interval_ms / 1000,
                           ipmi_interval=args.ipmi_interval_seconds, ipmi=not args.no_ipmi,
                           powercap_root=args.powercap_root, hwmon_root=args.hwmon_root, cpu_root=args.cpu_root,
                           node_root=args.node_root, liveness_seconds=args.liveness_seconds,
                           include_hostname=args.include_hostname)
    if args.probe:
        report = sampler.probe()
    else:
        sampler.start()
        time.sleep(args.duration)
        report = sampler.stop()
    json.dump(report, sys.stdout, indent=2, default=str)
    print()
    return 3 if report["status"] == "unavailable" else 0


if __name__ == "__main__":
    raise SystemExit(main())
