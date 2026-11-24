#!/usr/bin/env python3
"""Paper 3 campaign planner: the one place that knows what each launch is (docs/PAPER3_RUNBOOK.md).

Offline. Nothing here calls a cloud, reads a credential or runs a study query; it computes names, commands, scripts and tables
from the locked design (docs/PAPER3_ENERGY_DESIGN.md sections 3, 7, 9) and the repository's own schedule (paper3_campaign.py), so
the runbook, the launcher, the driver and the tests cannot drift apart. The shell tools next to it only execute what it prints.

  p3_plan.py table [--json]                           wall time and cost per host class at P-NEW against the ~$1,364 core estimate
  p3_plan.py timeline [--json]                        the recommended order and overlap of the launches
  p3_plan.py lease HOST LAUNCH [--now ISO] [--ordinal N]   lease name, expiry and provider-side limit of one launch (ordinal 2: the relaunch, lease token lNr2)
  p3_plan.py launch-spec HOST LAUNCH --place P [--ordinal N]   the launch request (Lambda API body or gcloud command) as JSON; the environment may set
                                                      P3_LAMBDA_SSH_KEY_NAME, P3_GCP_TERMINATION_ACTION (STOP|DELETE), P3_GCP_OS_LOGIN (1),
                                                      P3_GCP_EXTRA_FLAGS (docs/PAPER3_CONTROLLER.md)
  p3_plan.py command HOST LAUNCH [--stop-after K]     the run_campaign.py command line of one launch (--record FILE for real values)
  p3_plan.py bringup HOST LAUNCH --record FILE        the on-host bring-up script (checkout, lease, setup, inputs, preflight)
  p3_plan.py stage-script HOST LAUNCH --stop-after K  the on-host script of one campaign stage
  p3_plan.py rounds HOST LAUNCH                       the round numbers of the launch, one per line
  p3_plan.py root|budget HOST LAUNCH                  the campaign root (relative to ~/study); the driver's poll budgets
  p3_plan.py check-lock                               the lock evidence run_campaign.py will check on every host, checked on this checkout
  p3_plan.py check-allowlist --old SHA --new SHA      a paper3_hosts.json change may only fill c-gcp exclusive_use.allowed_scheduled
  p3_plan.py allowlist-review --capture FILE --commit SHA   the items of a C-GCP exclusivity capture that the allow-list committed at SHA lacks (the short review of launch 2 and of a relaunch)
  p3_plan.py dest HOST LAUNCH [--ordinal N]           the launch directory under the repository, rerun/paper3/HOST/launchN or launchN-r2 for a relaunch
  p3_plan.py check-relaunch HOST LAUNCH --old RECORD  whether (HOST, LAUNCH) may be relaunched once: its record is terminated, its copy verifies, nothing was relaunched before
  p3_plan.py docs --check|--write FILE                the generated blocks of the runbook
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import paper3_campaign as pc  # noqa: E402
import probe_power  # noqa: E402

# ---- the lock and the fixed facts of the campaign ----------------------------------------------------------------------
LOCK = {"date": "20261116", "commit": "bd42401dd725d63bde3fbce2a1c7dcf81469b0b5", "doi": "10.5281/zenodo.24284712", "tag": pc.LOCK_TAG}
REPO_URL = "https://github.com/ejiod2025/gpu-spark-polars-study.git"
OWNER = "ejiod"                      # the <id> of the lease name (docs/PAPER3_INFRA.md section 5)
LAMBDA_API = "https://cloud.lambdalabs.com/api/v1"
LAMBDA_PRICE_URL = f"{LAMBDA_API}/instance-types"
GCP_PRICE_URL = "https://cloud.google.com/products/compute/pricing/general-purpose"
LAMBDA_SSH_KEY_NAME = "Key22"        # the key lambda.sh launches with; the private half is ~/.ssh/lambda_key22
TERMINATION_ACTIONS = ("STOP", "DELETE")   # --instance-termination-action at --max-run-duration. STOP (the default, the lead's decision 2026-11-22) keeps the disk and the round data when the
                                           # operator is gone; DELETE is the fallback if a metal host rejects STOP (P3_GCP_TERMINATION_ACTION=DELETE)
# flags the planner sets itself: an operator-supplied extra flag may not repeat or override them (the lease, labels and limits are the guard rails)
GCP_OWN_FLAGS = ("--zone", "--machine-type", "--image-family", "--image-project", "--image", "--boot-disk-size", "--boot-disk-type",
                 "--boot-disk-provisioned-iops", "--boot-disk-provisioned-throughput", "--maintenance-policy", "--max-run-duration",
                 "--termination-time", "--instance-termination-action", "--labels", "--project", "--provisioning-model",
                 "--no-service-account", "--service-account", "--no-scopes", "--scopes", "--metadata", "--metadata-from-file")
# A campaign host runs as NO service account (the project's default one holds Editor on a shared project; the bring-up pulls from apt, PyPI, Maven
# Central, the DuckDB extension repository and public GitHub, never from GCS or a Google API) and trusts none of the project-wide ssh-keys entries:
# access is the key gcloud puts in the instance's own metadata (or OS Login, P3_GCP_OS_LOGIN=1). docs/PAPER3_CONTROLLER.md section 3.
GCP_NO_IDENTITY = ("--no-service-account", "--no-scopes")
GCP_IMAGE = ("ubuntu-2404-lts-amd64", "ubuntu-os-cloud")
GCP_DISK = {"size": "800GB", "type": "hyperdisk-balanced", "iops": 16000, "throughput": 1000}   # design 2.6: 800 GiB, 16k IOPS, 1,000 MBps
ORDINALS = (1, 2)                    # a launch runs once and is relaunched at most once (amendment A1 item 2: a replacement after a technical failure; the stopped rounds are reported, never pooled)
ENGINE_TIMEOUT_S = 7200              # Paper 1 passed it on every host (REPRODUCE.md); the Paper 3 design only says "no timeout"
C_GCP_PIN = "0-12,96-108"            # 13 physical cores + SMT siblings on socket 0, NUMA node 0 (design 3.1, probe P5)
PAUSE_H = 0.12                       # per round: pack, copy, verify, restart (an assumption, not in the design's estimate)
LEASE_MARGIN_H = 1.0                 # the lease also covers one more round than the design's 1.2 allowance, plus this margin
STORAGE_EGRESS_USD = 50.0            # design section 9, "Storage, snapshots, egress"
DESIGN_CORE_TOTAL_USD = 1364.0
# timers masked on C-GCP before the G12 capture is reviewed; a unit that does not exist is skipped. The last five were masked by hand during launch 1's review (2026-11-23,
# amendment A6) and are listed here so that every later bring-up on this host class (launch 2, a --fresh re-bring-up) masks them before its first capture: both launches
# must have the same host configuration.
C_GCP_MASK_UNITS = ("apt-daily.timer", "apt-daily-upgrade.timer", "dpkg-db-backup.timer", "e2scrub_all.timer", "fstrim.timer",
                    "logrotate.timer", "man-db.timer", "motd-news.timer", "update-notifier-download.timer",
                    "update-notifier-motd.timer", "fwupd-refresh.timer", "ua-timer.timer", "sysstat-collect.timer",
                    "sysstat-summary.timer",
                    "apport-autoreport.timer", "snapd.snap-repair.timer", "systemd-tmpfiles-clean.timer", "gce-workload-cert-refresh.timer",
                    "google-oslogin-cache.timer")
# bytes of Parquet per scale, from evidence/INPUT_MANIFESTS (sum of the tables' parquet_bytes) and the recorded generation seconds
INPUT_BYTES = {"1": 325716220, "10": 3442803048, "30": 10568904879, "100": 35652242574, "300": 107978166411}
INPUT_GEN_SECONDS = {"1": 13.0, "10": 120.1, "30": 362.6, "100": 165.8, "300": 345.6}


class PlanError(ValueError):
    """The request does not fit the locked design or the runbook."""


@dataclass(frozen=True)
class Plan:
    host: str                  # key of paper3_campaign.HOSTS
    token: str                 # purpose token of the lease name
    provider: str              # lambda | gcp
    instance_type: str
    price: float               # P-NEW USD per hour (design 3.1, 2.6)
    places: tuple              # preference order: Lambda regions or GCP zones
    base_h: float              # design 9: base-run hours of the host (all launches)
    overhead_h: float          # design 9: instrumentation overhead hours
    probe_h: float             # design 9: probe and validation allowance (spent before the lock in part; not a launch)
    launches: int
    bringup_h: float           # share of a launch that is not rounds (set-up, inputs, preflight)
    rounds: int                # rounds per launch
    credit_funded: str | None  # --cost-credit-funded: yes | no | None = the author must say

    @property
    def launch_h(self) -> float:
        return (self.base_h + self.overhead_h) / self.launches

    @property
    def round_h(self) -> float:
        return (self.launch_h - self.bringup_h) / self.rounds

    @property
    def lease_h(self) -> float:
        return math.ceil((self.launch_h + self.rounds * PAUSE_H + self.round_h + LEASE_MARGIN_H) * 2) / 2


PLANS = {
    # Lambda regions: the pre-lock probe region first (design 7.2), then any region the API lists with capacity (nothing in the design pins a region)
    "a100": Plan("a100", "a100", "lambda", "gpu_1x_a100_sxm4", 1.99, ("us-east-1", "us-west-2", "asia-south-1"), 31.0, 23.0, 1.5, 2, 1.8, 4, "yes"),
    "h100": Plan("h100", "h100", "lambda", "gpu_1x_h100_sxm5", 4.89, ("us-south-2", "us-south-3"), 25.8, 23.0, 1.5, 2, 1.8, 4, "yes"),
    "gh200": Plan("gh200", "gh200", "lambda", "gpu_1x_gh200", 2.59, ("us-east-3",), 19.9, 23.0, 1.5, 2, 1.8, 4, "yes"),
    "b200": Plan("b200", "b200", "lambda", "gpu_1x_b200_sxm6", 7.99, ("australia-east-1",), 25.5, 23.0, 1.5, 2, 1.8, 4, "yes"),
    # GCP zones: us-east4 and us-central1 only, the two regions of the frozen P-NEW GCP table (design 2.6)
    "c-gcp": Plan("c-gcp", "cgcp", "gcp", "c3-highmem-192-metal", 12.701568, ("us-east4-a", "us-east4-c", "us-central1-a"), 20.0, 11.5, 2.0, 2, 1.5, 4, None),
    "c-vm": Plan("c-vm", "cvm", "gcp", "c3-highmem-44", 2.910776, ("us-east4-a", "us-east4-c", "us-central1-a"), 6.0, 0.0, 0.0, 1, 1.0, 2, None),
}
VM_TYPES = {"c-gcp": ("c3-highmem-192-metal", "c3-standard-192-metal", "c3-highcpu-192-metal"), "c-vm": ("c3-highmem-44",)}


def plan_of(host: str) -> Plan:
    if host not in PLANS:
        raise PlanError(f"unknown host {host!r}; expected one of {sorted(PLANS)}")
    return PLANS[host]


# ---- tables ------------------------------------------------------------------------------------------------------------
def cost_rows() -> dict:
    """Design section 9 at P-NEW, per host class and in total (USD), plus this runbook's own addition (the round pauses)."""
    rows, total = [], 0.0
    for plan in PLANS.values():
        campaign = (plan.base_h + plan.overhead_h) * plan.price
        probes = plan.probe_h * plan.price
        pauses = plan.launches * plan.rounds * PAUSE_H * plan.price
        rows.append({"host": plan.host, "instance_type": plan.instance_type, "usd_per_h": plan.price, "launches": plan.launches,
                     "hours_per_launch": round(plan.launch_h, 2), "round_hours": round(plan.round_h, 2),
                     "campaign_hours": plan.base_h + plan.overhead_h, "campaign_usd": round(campaign, 2), "probe_usd": round(probes, 2),
                     "design_usd": round(campaign + probes, 2), "pause_usd_not_in_design": round(pauses, 2),
                     "lease_hours": plan.lease_h, "exposure_at_lease_usd": round(plan.lease_h * plan.price, 2)})
        total += campaign + probes
    return {"rows": rows, "storage_egress_usd": STORAGE_EGRESS_USD, "design_core_total_usd": DESIGN_CORE_TOTAL_USD,
            "computed_core_total_usd": round(total + STORAGE_EGRESS_USD, 2),
            "lambda_subtotal_usd": round(sum(r["design_usd"] for r in rows if PLANS[r["host"]].provider == "lambda"), 2),
            "gcp_subtotal_usd": round(sum(r["design_usd"] for r in rows if PLANS[r["host"]].provider == "gcp"), 2),
            "pause_total_usd_not_in_design": round(sum(r["pause_usd_not_in_design"] for r in rows), 2)}


def timeline(gate_h: float | None = None, turnaround_h: float = 1.0) -> dict:
    """The recommended order: pathfinders (H100 and C-GCP launch 1) first, everything else once the pathfinder H100 has passed round 1
    and its copy is verified, launch 2 of a host after its launch 1 (and, on C-GCP, after the metal host is deleted: C3_CPUS 300 per
    region holds one 192-vCPU host). Hours from the start of the pathfinders; pauses included. B200 (admitted by amendment A2) starts at the gate with
    A100 and GH200; capacity decides in practice, and the lead may start it earlier."""
    h = PLANS["h100"]
    gate = gate_h if gate_h is not None else h.bringup_h + h.round_h + PAUSE_H
    start1 = {"h100": 0.0, "c-gcp": 0.0, "a100": gate, "gh200": gate, "b200": gate, "c-vm": gate}
    out, peak_events = [], []
    for host, s1 in start1.items():
        p = PLANS[host]
        dur = p.launch_h + p.rounds * PAUSE_H
        end1 = s1 + dur
        entry = {"host": host, "launch1_start_h": round(s1, 2), "launch1_end_h": round(end1, 2), "launch_hours": round(dur, 2)}
        peak_events += [(s1, p.price), (end1, -p.price)]
        if p.launches == 2:
            s2 = end1 + turnaround_h
            entry.update(launch2_start_h=round(s2, 2), launch2_end_h=round(s2 + dur, 2))
            peak_events += [(s2, p.price), (s2 + dur, -p.price)]
        out.append(entry)
    rate, peak = 0.0, 0.0
    for _, delta in sorted(peak_events, key=lambda e: (e[0], e[1])):
        rate += delta
        peak = max(peak, rate)
    end = max(e.get("launch2_end_h", e["launch1_end_h"]) for e in out)
    return {"pathfinder_gate_hours": round(gate, 2), "rows": out, "all_done_hours": round(end, 2),
            "all_done_days": round(end / 24, 2), "peak_usd_per_hour": round(peak, 2)}


def input_table() -> dict:
    total = sum(INPUT_BYTES.values())
    rows = [{"scale": s, "bytes": b, "gib": round(b / 2**30, 2), "recorded_generation_s": INPUT_GEN_SECONDS[s]} for s, b in INPUT_BYTES.items()]
    return {"rows": rows, "total_bytes": total, "total_gib": round(total / 2**30, 1), "recorded_generation_total_min": round(sum(INPUT_GEN_SECONDS.values()) / 60, 1),
            "c_vm_bytes": sum(INPUT_BYTES[s] for s in pc.CVM_SCALES["baseline"] + ("1",)),
            "copy_minutes_at_mb_per_s": {str(r): round(total / (r * 1e6) / 60, 1) for r in (50, 125, 250, 1000)}}


# ---- leases and launch requests ----------------------------------------------------------------------------------------
def utc(text: str | None) -> datetime:
    if text is None:
        return datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def launch_token(launch: str, ordinal: int = 1) -> str:
    """l1 for the first instance of launch 1, l1r2 for its relaunch: the lease, the label and the host's instance name all carry it."""
    return f"l{launch}" + ("" if ordinal == 1 else f"r{ordinal}")


def check_ordinal(ordinal: int) -> int:
    if ordinal not in ORDINALS:
        raise PlanError(f"ordinal {ordinal!r}: a launch is run once and relaunched at most once, expected one of {ORDINALS}")
    return ordinal


def launch_dir_name(launch: str, ordinal: int = 1) -> str:
    """The directory of a launch under rerun/paper3/HOST/ on the operator's machine: launch1, and launch1-r2 for its relaunch (never the first launch's directory)."""
    return f"launch{launch}" + ("" if check_ordinal(ordinal) == 1 else f"-r{ordinal}")


def lease_name(plan: Plan, launch: str, expires: datetime, ordinal: int = 1) -> str:
    stamp = expires.strftime("%Y%m%dT%H%MZ")
    if plan.provider == "gcp":
        stamp = stamp.lower()            # GCP names and label values must be lower case (docs/PAPER3_INFRA.md section 5)
    name = f"p3-campaign-{plan.token}-{launch_token(launch, ordinal)}-owner-{OWNER}-expires-{stamp}"
    status = probe_power.lease_status(name, now=expires - timedelta(hours=1))
    if not status["valid"]:
        raise PlanError(f"lease name {name!r} does not satisfy the launch convention: {status['problem']}")
    if plan.provider == "gcp" and (len(name) > 63 or not re.fullmatch(r"[a-z]([-a-z0-9]*[a-z0-9])?", name)):
        raise PlanError(f"{name!r} is not a valid GCP instance name (lower case, at most 63 characters)")
    return name


def check_launch(host: str, launch: str) -> Plan:
    plan = plan_of(host)
    if launch not in ("1", "2") or (launch == "2" and plan.launches == 1):
        raise PlanError(f"host {host} has no launch {launch!r} ({plan.launches} launch{'es' if plan.launches > 1 else ''})")
    return plan


def lease_spec(host: str, launch: str, now: datetime, ordinal: int = 1) -> dict:
    plan = check_launch(host, launch)
    check_ordinal(ordinal)
    expires = now + timedelta(hours=plan.lease_h)
    out = {"host": host, "launch": int(launch), "ordinal": ordinal, "lease_name": lease_name(plan, launch, expires, ordinal), "now_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "expires_utc": expires.strftime("%Y-%m-%dT%H:%M:%SZ"), "lease_hours": plan.lease_h, "launch_hours": round(plan.launch_h, 2),
           "exposure_at_lease_usd": round(plan.lease_h * plan.price, 2)}
    if plan.provider == "gcp":
        minutes = int(plan.lease_h * 60)
        out["max_run_duration"] = f"{minutes // 60}h" + (f"{minutes % 60}m" if minutes % 60 else "")
    return out


def check_admitted(host: str, config: Path | None = None) -> None:
    """B200 is a conditional host (design 3.1): refuse a sheet until its t_min is in paper3_hosts.json (the section 12 amendment is made)."""
    entry = json.loads((config or REPO / "paper3_hosts.json").read_text())["hosts"][host]
    if entry.get("t_min_s") is None:
        raise PlanError(f"{host} is not admitted: t_min_s is unset in paper3_hosts.json (design 3.1: valid P6 rerun, then a section 12 "
                        "amendment before the first session)")


def check_extra_flags(flags) -> list:
    """Operator-supplied extra `gcloud compute instances create` flags: long options only, none that the planner sets itself."""
    flags = list(flags)
    for flag in flags:
        if not flag.startswith("--") or flag == "--":
            raise PlanError(f"extra flag {flag!r}: only --long-options are accepted, no positional words")
        if flag.split("=", 1)[0] in GCP_OWN_FLAGS:
            raise PlanError(f"extra flag {flag!r} would override a flag the planner sets (lease, limits, labels, disk, identity, ssh access): not allowed")
    return flags


def launch_spec(host: str, launch: str, place: str, now: datetime, *, instance_type: str | None = None,
                termination_action: str = "STOP", extra_flags=(), ssh_key_name: str = LAMBDA_SSH_KEY_NAME, os_login: bool = False, ordinal: int = 1) -> dict:
    """The request of one launch. The keyword options default to the runbook's values (so the generated blocks never depend on the
    environment); the command line reads P3_LAMBDA_SSH_KEY_NAME, P3_GCP_TERMINATION_ACTION, P3_GCP_OS_LOGIN and P3_GCP_EXTRA_FLAGS into them."""
    plan = check_launch(host, launch)
    if termination_action not in TERMINATION_ACTIONS:
        raise PlanError(f"termination action {termination_action!r}: expected one of {TERMINATION_ACTIONS}")
    extra_flags = check_extra_flags(extra_flags)
    if plan.provider == "lambda" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", ssh_key_name):
        raise PlanError(f"{ssh_key_name!r} does not look like a Lambda SSH key name")
    if host == "b200":
        check_admitted(host)
    spec = lease_spec(host, launch, now, ordinal)
    itype = instance_type or plan.instance_type
    if itype != plan.instance_type and itype not in VM_TYPES.get(host, ()):
        raise PlanError(f"{itype!r} is not an instance type of {host} (allowed: {', '.join((plan.instance_type,) + VM_TYPES.get(host, ()))})")
    spec.update(provider=plan.provider, instance_type=itype, price_usd_h=plan.price, credit_funded_default=plan.credit_funded)
    if plan.provider == "lambda":
        if place not in plan.places and not re.fullmatch(r"[a-z]+-[a-z]+-\d+", place):
            raise PlanError(f"{place!r} does not look like a Lambda region name")
        spec.update(region=place, api_path="/instance-operations/launch",
                    body={"region_name": place, "instance_type_name": itype, "ssh_key_names": [ssh_key_name],
                          "file_system_names": [], "quantity": 1, "name": spec["lease_name"]})
        return spec
    if not re.fullmatch(r"(us-east4|us-central1)-[a-f]", place):
        raise PlanError(f"zone {place!r}: only us-east4 and us-central1 are in the frozen P-NEW GCP table (design 2.6); another zone needs "
                        "its price added by a section 12 amendment first")
    labels = {"project": "paper3-energy", "owner": OWNER, "purpose": "campaign", "host": plan.token, "launch": launch_token(launch, ordinal),
              "expires": spec["lease_name"].rsplit("-", 1)[1]}
    command = ["gcloud", "compute", "instances", "create", spec["lease_name"], f"--zone={place}", f"--machine-type={itype}",
               f"--image-family={GCP_IMAGE[0]}", f"--image-project={GCP_IMAGE[1]}", f"--boot-disk-size={GCP_DISK['size']}",
               f"--boot-disk-type={GCP_DISK['type']}", f"--boot-disk-provisioned-iops={GCP_DISK['iops']}",
               f"--boot-disk-provisioned-throughput={GCP_DISK['throughput']}", "--maintenance-policy=TERMINATE",
               f"--max-run-duration={spec['max_run_duration']}", f"--instance-termination-action={termination_action}",
               "--labels=" + ",".join(f"{k}={v}" for k, v in labels.items()), *GCP_NO_IDENTITY,
               "--metadata=block-project-ssh-keys=TRUE" + (",enable-oslogin=TRUE" if os_login else ""), *extra_flags]
    spec.update(zone=place, labels=labels, command=command, boot_disk=GCP_DISK, termination_action=termination_action,
                access={"service_account": None, "scopes": None, "block_project_ssh_keys": True, "os_login": bool(os_login)})
    return spec


# ---- the campaign command ----------------------------------------------------------------------------------------------
class Raw(str):
    """A shell word written as is (so that $DATA_ROOT expands on the host); every other word is quoted."""


def words(args) -> str:
    return " ".join(a if isinstance(a, Raw) else shlex.quote(str(a)) for a in args)


def root_rel(host: str, launch: str) -> str:
    return f"rerun/paper3-{host}-l{launch}"


def inputs_reports(host: str) -> list:
    return ["rerun/verify-sf1-30-ref.json", "rerun/verify-sf300.json" if host == "c-vm" else "rerun/verify-sf100-300.json"]


PLACEHOLDERS = {"lease_name": "<LEASE_NAME>", "instance_id": "<INSTANCE_ID>", "launch_utc": "<LAUNCH_UTC>", "region": "<REGION_OR_ZONE>",
                "price_usd_h": "<PRICE_USD_PER_H>", "price_snapshot_utc": "<PRICE_SNAPSHOT_UTC>", "credit_funded": "<yes|no>",
                "campaign_commit": "<CAMPAIGN_COMMIT>"}


def context(host: str, launch: str, record: dict | None) -> dict:
    """Values of one launch: the launch record when given, else the placeholders the runbook prints."""
    plan = check_launch(host, launch)
    ctx = dict(PLACEHOLDERS)
    if plan.credit_funded:
        ctx["credit_funded"] = plan.credit_funded
    if record:
        for key in ("lease_name", "instance_id", "launch_utc", "price_usd_h", "price_snapshot_utc", "campaign_commit"):
            if record.get(key) is not None:
                ctx[key] = record[key]
        ctx["region"] = record.get("zone") or record.get("region") or ctx["region"]
        ctx["instance_type"] = record.get("instance_type", plan.instance_type)
        if record.get("credit_funded"):
            ctx["credit_funded"] = record["credit_funded"]
    ctx.setdefault("instance_type", plan.instance_type)
    return ctx


def campaign_args(host: str, launch: str, record: dict | None = None, stop_after: int | None = None) -> list:
    plan, spec = check_launch(host, launch), pc.HOSTS[host]
    ctx = context(host, launch, record)
    gpu = spec.kind == "gpu"
    args = ["python", "run_campaign.py", "--root", root_rel(host, launch), "--data-root", Raw('"$DATA_ROOT"'),
            "--jar", Raw('"$RAPIDS_JAR"') if gpu else "jars/not-used-on-a-cpu-host.jar",
            "--inputs-report", *inputs_reports(host), "--engine-timeout-seconds", ENGINE_TIMEOUT_S,
            "--host-label", ctx["instance_type"],
            "--paper3-host", host, "--paper3-launch", launch, "--paper3-lock-date", LOCK["date"], "--paper3-lock-commit", LOCK["commit"],
            "--paper3-lock-doi", LOCK["doi"],
            "--lease-name", ctx["lease_name"], "--lease-min-minutes", int(math.ceil((plan.round_h + 0.5) * 60)),
            "--cost-provider", "lambda-labs" if plan.provider == "lambda" else "gcp", "--cost-region", ctx["region"],
            "--cost-hourly-price-usd", ctx["price_usd_h"],
            "--cost-price-source-url", LAMBDA_PRICE_URL if plan.provider == "lambda" else GCP_PRICE_URL,
            "--cost-price-snapshot-utc", ctx["price_snapshot_utc"], "--cost-instance-id", ctx["instance_id"],
            "--cost-launch-utc", ctx["launch_utc"], "--cost-credit-funded", ctx["credit_funded"]]
    if spec.pinned:
        args += ["--cpu-list", C_GCP_PIN]
    if spec.scope is None:       # B200 had none until amendment A2 declared it (board) in paper3_campaign.HOSTS; no host is without one now, and none is invented here
        raise PlanError(f"host {host} declares no GPU energy scope in paper3_campaign.HOSTS: the campaign command takes it from the host class, so it must be declared there")
    if stop_after is not None:
        args += ["--stop-after-round", stop_after]
    return args


def rounds_of(host: str, launch: str) -> list:
    check_launch(host, launch)
    plan = pc.build_plan(host, LOCK["date"])
    return [r.round for r in pc.launch_rounds(plan, launch)]


def budget(host: str, launch: str) -> dict:
    """Poll budgets of the driver, in seconds of polling: twice the estimate of a stage plus half an hour (a stage still running after
    that is held for a person, never killed)."""
    plan = check_launch(host, launch)
    return {"bringup_s": int((plan.bringup_h * 2 + 1) * 3600), "round_s": int((plan.round_h * 2 + 0.5) * 3600),
            "lease_hours": plan.lease_h, "round_hours": round(plan.round_h, 2)}


def read_record(path: str | None) -> dict | None:
    return json.loads(Path(path).read_text()) if path else None


# ---- on-host scripts ---------------------------------------------------------------------------------------------------
HEX40 = re.compile(r"[0-9a-f]{40}")
HEADER = """#!/usr/bin/env bash
# {what}
# Generated by tools/paper3_campaign/p3_plan.py; runs on the host as the login user, detached (p3_detach.sh). Do not edit on the host.
set -u
STUDY="$HOME/study"
ROOT_REL={root}
note() {{ echo "$(date -u +%FT%TZ) $*"; }}
"""
ENV_SOURCE = {"gpu": ". rerun/h100-env.sh", "cpu": ". rerun/cpu-env.sh"}


def env_line(host: str) -> str:
    return ENV_SOURCE["gpu" if pc.HOSTS[host].kind == "gpu" else "cpu"]


def setup_flags(host: str) -> str:
    return {"h100": "--envs gpu", "a100": "--envs gpu --expect-gpu A100 --min-free-gib 450", "gh200": "--envs gpu",
            "b200": "--envs gpu --expect-gpu B200", "c-gcp": "--cpu-only --enable-rapl-read --install-power-tools",
            "c-vm": "--cpu-only"}[host]


def generate_lines(host: str) -> list:
    lines = ['python generate_data.py --root "$DATA_ROOT" --scales 1 10 30 --threads 30 --memory-limit 64GB']
    if host != "c-vm":
        lines.append('python generate_large_data.py --root "$DATA_ROOT" --scales 100 --workers 12')
    lines.append('python generate_large_data.py --root "$DATA_ROOT" --scales 300 --workers 16')
    return lines


def verify_lines(host: str) -> list:
    first, second = inputs_reports(host)
    lines = [f'python verify_generated_inputs.py --data-root "$DATA_ROOT" --scales 1 10 30 --reference-dbgen --report {first}']
    scales = "300" if host == "c-vm" else "100 300"
    return lines + [f'python verify_generated_inputs.py --data-root "$DATA_ROOT" --scales {scales} --report {second}']


def preflight_line(host: str) -> str:
    spec = pc.HOSTS[host]
    if spec.kind == "gpu":
        extra = "" if host == "h100" else " --allow-any-gpu"
        extra += " --min-free-gib 250" if host == "a100" else ""
        return ('python preflight.py --data-root "$DATA_ROOT" --output rerun/preflight-bringup --cpus 26 --driver-memory 96g '
                f'--jar "$RAPIDS_JAR" --skip-inputs --expect-gpu-unsupported cx_q13 cx_window{extra}')
    pin = f" --cpu-list {C_GCP_PIN} --cpu-energy require" if spec.pinned else ""
    return f'python preflight.py --cpu-only{pin} --data-root "$DATA_ROOT" --output rerun/preflight-bringup --cpus 26 --driver-memory 96g --skip-inputs'


PINCHECK = """python3 - <<'PY'
import os, sys
def read(path):
    with open(path) as handle:
        return handle.read().strip()
base = os.environ.get("P3_SYS_CPU", "/sys/devices/system/cpu") + "/cpu%d/topology/"
bad = []
for cpu in range(0, 13):
    for number in (cpu, cpu + 96):
        if read(base % number + "physical_package_id") != "0":
            bad.append("cpu%d is not on socket 0" % number)
    if read(base % cpu + "core_id") != read(base % (cpu + 96) + "core_id"):
        bad.append("cpu%d and cpu%d are not SMT siblings" % (cpu, cpu + 96))
if bad:
    sys.exit("the pin 0-12,96-108 does not match this host: " + "; ".join(bad))
print("pin 0-12,96-108: 13 physical cores plus their siblings, all on socket 0")
PY"""


def indented(lines, pad="  "):
    return "\n".join(pad + line for line in lines)


ALWAYS_RERUN_STEPS = ("checkout", "lease")     # a resumed bring-up still moves the host to the record's commit and writes the lease


def bringup_script(host: str, launch: str, record: dict, skip=()) -> str:
    """The on-host bring-up. `skip` names steps that finished ok in an earlier attempt of THIS launch (the driver's --resume-bringup reads them from that attempt's log): they are
    not run again, only noted as skipped. checkout and lease always run. 2026-11-23: a step that fails late (verify) should not cost the earlier ones again, and a rerun of
    setup can itself fail on an A100 whose data already fills the disk (--min-free-gib 450)."""
    check_launch(host, launch)
    if host == "b200":
        check_admitted(host)
    commit, lease = record.get("campaign_commit", ""), record.get("lease_name", "")
    if not HEX40.fullmatch(commit):
        raise PlanError("the launch record needs campaign_commit, a full 40-character commit hash")
    if not probe_power.LEASE_NAME.match(lease):
        raise PlanError(f"the launch record's lease_name {lease!r} does not satisfy the launch convention")
    spec, env = pc.HOSTS[host], env_line(host)
    reports = " ".join(inputs_reports(host))
    parts = [HEADER.format(what=f"bring-up of {host} launch {launch}: checkout, lease, setup, inputs, preflight", root=shlex.quote(root_rel(host, launch))),
             'run_step() {  # NAME FUNCTION: a step is a subshell with set -e; the first failure ends the stage with its code\n'
             '  note "STEP $1 begin"\n'
             '  ( set -e; [ -d "$STUDY" ] && cd "$STUDY"; "$2" ); rc=$?\n'
             '  if [ "$rc" -ne 0 ]; then note "STEP $1 FAILED rc=$rc"; exit "$rc"; fi\n'
             '  note "STEP $1 ok"\n'
             '}\n'
             'if [ -e "$STUDY/$ROOT_REL/campaign.json" ]; then note "$ROOT_REL/campaign.json exists: the campaign has started, bring-up must not run again"; exit 90; fi\n'
             "s_boot_wait() { timeout 900 cloud-init status --wait >/dev/null 2>&1 || true; }   # first-boot package jobs hold the apt lock\n"
             "s_checkout() {\n"
             f'  [ -d "$STUDY/.git" ] || git clone --no-checkout {shlex.quote(REPO_URL)} "$STUDY"\n'
             '  git -C "$STUDY" fetch --tags origin\n'
             f'  git -C "$STUDY" checkout --detach {commit}\n'
             f'  test "$(git -C "$STUDY" rev-parse HEAD)" = {commit}\n'
             f'  git -C "$STUDY" rev-parse --verify "refs/tags/{LOCK["tag"]}^{{commit}}" >/dev/null\n'
             '  test -z "$(git -C "$STUDY" status --porcelain)"\n'
             "}\n"
             f"s_lease() {{ printf '%s\\n' {shlex.quote(lease)} | sudo tee /etc/paper3-lease >/dev/null; }}\n"
             f"s_setup() {{ ./setup_h100.sh {setup_flags(host)}; }}\n"
             f"s_hostinfo() {{ mkdir -p rerun/bringup-artifacts; python3 probe_power.py --lease-name {shlex.quote(lease)} --no-load --output rerun/bringup-artifacts/probe-power.json "
             ">/dev/null 2>&1 || echo \"probe_power.py failed (evidence only, not a gate)\"; }\n"
             f"s_generate() {{\n  {env}\n{indented(generate_lines(host))}\n}}\n"
             f"s_verify() {{\n  {env}\n  mkdir -p rerun; rm -f {reports}\n{indented(verify_lines(host))}\n}}\n"
             f"s_preflight() {{\n  {env}\n  rm -rf rerun/preflight-bringup\n  {preflight_line(host)}\n}}\n"]
    steps = ["boot_wait", "checkout", "lease", "setup", "hostinfo", "generate", "verify"]
    if spec.kind == "gpu":
        # The CUDA-readiness gate (2026-11-22, smoke run 2: a host that answered every CUDA call with error 802): right after setup, before data and engines. It retries for
        # up to 10 minutes, then fails with exit 82, which the driver reads as an infrastructure failure before round 1 (it tears the host down).
        parts.append(f's_cudaready() {{\n  {env}\n  tools/paper3_campaign/p3_cuda_ready.sh --python "$PYTHON_GPU_ENV" --out rerun/bringup-artifacts\n}}\n')
        steps.insert(steps.index("setup") + 1, "cudaready")
    if spec.pinned:
        parts.append(f"s_pincheck() {{\n  {env}\n{PINCHECK}\n}}\n"
                     f'capture() {{ {env}; mkdir -p "$HOME/p3out"; python paper3_gates.py --capture-exclusivity > "$HOME/p3out/$1"; }}\n'
                     'quiesce_unit() { if systemctl list-unit-files "$1" 2>/dev/null | grep -q "^$1"; then sudo systemctl mask --now "$1" && echo "masked $1"; else echo "absent $1"; fi; }\n'
                     f"s_capture_before() {{ capture exclusivity-before.json; }}\n"
                     f"s_quiesce() {{ for unit in {' '.join(C_GCP_MASK_UNITS)} cron.service; do quiesce_unit \"$unit\"; done; }}\n"
                     "s_capture_after() { capture exclusivity-after.json; }\n")
        steps += ["pincheck", "preflight", "capture_before", "quiesce", "capture_after"]
    else:
        steps += ["preflight"]
    parts.append('s_artifacts() { mkdir -p rerun/bringup-artifacts; cp "$HOME"/p3out/exclusivity-*.json rerun/bringup-artifacts/ 2>/dev/null || true; }\n')
    steps.append("artifacts")
    unknown = [s for s in skip if s not in steps]
    if unknown or any(s in ALWAYS_RERUN_STEPS for s in skip):
        raise PlanError(f"--skip names {sorted(set(skip) - set(steps)) or sorted(set(skip) & set(ALWAYS_RERUN_STEPS))}: only steps of this host's bring-up other than {ALWAYS_RERUN_STEPS} can be skipped ({', '.join(steps)})")
    parts += [(f'note "STEP {name} skipped: it finished ok in an earlier attempt of this launch (resume)"' if name in skip else f"run_step {name} s_{name}") for name in steps]
    note = "REVIEW-REQUIRED exclusivity-after.json" if spec.pinned else "no review step on this host class"
    parts.append(f'note "BRINGUP-OK {host} launch {launch}: {note}"\n')
    return "\n".join(parts)


def stage_script(host: str, launch: str, stop_after: int | None, record: dict | None) -> str:
    args = campaign_args(host, launch, record, stop_after)
    return (HEADER.format(what=f"campaign stage of {host} launch {launch}" + (f", stop after round {stop_after}" if stop_after else ", to the end of the launch"),
                          root=shlex.quote(root_rel(host, launch)))
            + f'cd "$STUDY" || exit 97\n{env_line(host)} || exit 98\nnote "start: {host} launch {launch}"\nexec {words(args)}\n')


# ---- allow-list change guard -------------------------------------------------------------------------------------------
def git_show(commit: str, path: str) -> str:
    done = subprocess.run(["git", "-C", str(REPO), "show", f"{commit}:{path}"], capture_output=True, text=True)
    if done.returncode:
        raise PlanError(f"git show {commit}:{path}: {done.stderr.strip()[:200]}")
    return done.stdout


# what may differ between the campaign commit and the commit that fills the allow-list: the host config and the design file (section 12 only;
# run_campaign.py verifies that), this runbook, its planner and tests. Nothing that runs during a measurement (harness, run_campaign, gates).
# verify_generated_inputs.py and its test are the bring-up's input checker: it runs once per host before round 1 and never during a measurement (its 2026-11-23 prefix fix may travel)
ALLOWLIST_COMMIT_FILES = ("paper3_hosts.json", pc.DESIGN_DOC_REL, "docs/PAPER3_RUNBOOK.md", "verify_generated_inputs.py", "tests/test_inputs_verify.py")
# docs/paper3-evidence/ is archived evidence (price snapshots, probe and smoke runs): the campaign's own snapshots are committed while launches run, and nothing in it is read by a host
# paper3/ is the manuscript (LaTeX): it is written while the campaign runs and no host reads it
ALLOWLIST_COMMIT_PREFIXES = ("tools/paper3_campaign/", "tests/test_paper3_runbook", "docs/paper3-evidence/", "paper3/")


def check_allowlist_change(old: str, new: str) -> dict:
    """paper3_hosts.json may differ between the two commits only in c-gcp exclusive_use.allowed_scheduled, null before and a list of
    strings after; every other entry (so every other host's design digest) and every other field of c-gcp stays as it was. Of the files,
    only those in ALLOWLIST_COMMIT_FILES / ALLOWLIST_COMMIT_PREFIXES may differ."""
    names = subprocess.run(["git", "-C", str(REPO), "diff", "--name-only", old, new], capture_output=True, text=True)
    if names.returncode:
        raise PlanError(f"git diff {old} {new}: {names.stderr.strip()[:200]}")
    changed = sorted(n for n in names.stdout.split() if n)
    extra = [n for n in changed if n not in ALLOWLIST_COMMIT_FILES and not n.startswith(ALLOWLIST_COMMIT_PREFIXES)]
    if extra:
        raise PlanError(f"files other than the host config, the design file, the runbook and the tools of this runbook differ between {old} and {new}: {extra}")
    before, after = (json.loads(git_show(c, "paper3_hosts.json")) for c in (old, new))
    if before.get("hosts", {}).keys() != after.get("hosts", {}).keys() or {k: v for k, v in before.items() if k != "hosts"} != {k: v for k, v in after.items() if k != "hosts"}:
        raise PlanError("paper3_hosts.json: the host set or a top-level field changed")
    for host, entry in before["hosts"].items():
        if host == "c-gcp":
            continue
        if entry != after["hosts"][host]:
            raise PlanError(f"paper3_hosts.json: the entry of {host} changed (a host's design digest would change mid-campaign)")
    old_entry, new_entry = before["hosts"]["c-gcp"], after["hosts"]["c-gcp"]
    old_list = old_entry["exclusive_use"].get("allowed_scheduled")
    new_list = new_entry["exclusive_use"].get("allowed_scheduled")
    stripped = lambda e: {**e, "exclusive_use": {k: v for k, v in e["exclusive_use"].items() if k != "allowed_scheduled"}}  # noqa: E731
    if stripped(old_entry) != stripped(new_entry):
        raise PlanError("paper3_hosts.json: c-gcp changed in a field other than exclusive_use.allowed_scheduled")
    if old_list is not None and old_list != new_list:
        raise PlanError("c-gcp allowed_scheduled was already set and is now different: it is fixed for the whole campaign once a C-GCP launch has started")
    if not isinstance(new_list, list) or not all(isinstance(i, str) for i in new_list):
        raise PlanError("c-gcp allowed_scheduled must be a list of strings after the review")
    return {"old": old, "new": new, "changed_files": changed, "allowed_scheduled": new_list, "filled_now": old_list is None}


def allowlist_review(capture: Path, commit: str) -> dict:
    """The short review of C-GCP launch 2 or of a relaunch (docs/PAPER3_RUNBOOK.md section 6): the items of a host's exclusivity capture (rerun/bringup-artifacts/
    exclusivity-after.json, key allow_list_items) that the allow-list committed at `commit` does not contain. Nothing printed there means the capture holds nothing new; an item
    that is printed is masked on the host before round 1, never added to the list (the guard refuses a changed list). Reads two files; decides nothing."""
    try:
        items = json.loads(Path(capture).read_text())["allow_list_items"]
    except (OSError, ValueError, KeyError) as exc:
        raise PlanError(f"{capture}: cannot read allow_list_items of the capture ({exc.__class__.__name__}: {exc})") from None
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
        raise PlanError(f"{capture}: allow_list_items is not a list of strings")
    allowed = json.loads(git_show(commit, "paper3_hosts.json"))["hosts"]["c-gcp"]["exclusive_use"].get("allowed_scheduled")
    if not isinstance(allowed, list):
        raise PlanError(f"the commit {commit} holds no allow-list for c-gcp yet (allowed_scheduled is {allowed!r}): this is the first review, not the short one")
    return {"commit": commit, "capture_items": len(items), "allow_list_items": len(allowed), "not_in_list": sorted(set(items) - set(allowed))}


# ---- relaunch ------------------------------------------------------------------------------------------------------------------------
def ledger_records(ledger: Path) -> list:
    """Every launch record (<lease>.json) in the operator's ledger directory; ledger.jsonl and the watchdog logs are not records."""
    out = []
    for path in sorted(Path(ledger).glob("p3-*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and record.get("lease_name"):
            out.append((path, record))
    return out


def verify_launch_copy(directory: Path) -> int:
    """LAUNCH-SHA256SUMS of a copied launch directory (written by the driver after the last file arrived), checked here the way `shasum -a 256 -c` does: every listed
    file exists and has the listed hash. Returns the number of files; PlanError for a missing manifest or a damaged copy."""
    import hashlib
    manifest = Path(directory) / "LAUNCH-SHA256SUMS"
    if not manifest.is_file():
        raise PlanError(f"{directory} has no LAUNCH-SHA256SUMS: the launch's copy is not finished (the driver writes it last, after teardown); a launch is replaced only after its data is safe here")
    lines = [line for line in manifest.read_text().splitlines() if line.strip()]
    if not lines:
        raise PlanError(f"{manifest} is empty")
    bad = []
    for line in lines:
        digest, _, name = line.partition("  ")
        target = Path(directory) / name.strip()
        try:
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            bad.append(f"{name.strip()} (missing)")
            continue
        if actual != digest.strip():
            bad.append(f"{name.strip()} (differs)")
    if bad:
        raise PlanError(f"{directory} does not match its LAUNCH-SHA256SUMS: {', '.join(bad[:4])}{' ...' if len(bad) > 4 else ''}: copy the launch again before relaunching")
    return len(lines)


def check_relaunch(host: str, launch: str, old: str, ledger: Path, old_dest: Path | None = None) -> dict:
    """May (host, launch) be relaunched now? Refused unless the old launch's record says its instance is confirmed terminated and its directory holds a copied, intact
    LAUNCH-SHA256SUMS, and unless nothing in the ledger is a relaunch of this (host, launch) already. Returns what the launcher records: ordinal 2 and the old lease."""
    check_launch(host, launch)
    path = Path(old)
    if not path.is_file():
        path = Path(ledger) / (old if old.endswith(".json") else f"{old}.json")
    if not path.is_file():
        raise PlanError(f"no launch record {old!r} (a path, or a lease name in {ledger})")
    record = json.loads(path.read_text())
    if record.get("host") != host or str(record.get("launch")) != str(launch):
        raise PlanError(f"{path.name} is the record of {record.get('host')} launch {record.get('launch')}, not of {host} launch {launch}")
    old_ordinal = int(record.get("ordinal") or 1)
    if old_ordinal >= max(ORDINALS):
        raise PlanError(f"{record['lease_name']} is already the relaunch of {record.get('relaunch_of')}: a launch is relaunched once (amendment A1 item 2)")
    if not record.get("terminate_confirmed_utc"):
        raise PlanError(f"{record['lease_name']} has no terminate_confirmed_utc: its instance may still be billing and its data may not be copied (p3_teardown.sh {path} --yes first)")
    for other_path, other in ledger_records(ledger):
        if other.get("host") == host and str(other.get("launch")) == str(launch) and int(other.get("ordinal") or 1) > old_ordinal:
            raise PlanError(f"{other['lease_name']} (ordinal {other.get('ordinal')}) is already a relaunch of {host} launch {launch}: a launch is relaunched once (amendment A1 item 2)")
    directory = Path(old_dest) if old_dest else REPO / "rerun" / "paper3" / host / launch_dir_name(launch, old_ordinal)
    count = verify_launch_copy(directory)
    ordinal = old_ordinal + 1
    return {"host": host, "launch": int(launch), "ordinal": ordinal, "relaunch_of": record["lease_name"], "old_record": str(path), "old_dest": str(directory),
            "old_files_verified": count, "new_dest": str(REPO / "rerun" / "paper3" / host / launch_dir_name(launch, ordinal)), "root": root_rel(host, launch)}


def check_lock() -> dict:
    """What run_campaign.py will verify on every host before its first session (design 7.1), checked here on this checkout first."""
    def git(*args):
        done = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)
        return done.returncode, done.stdout if done.returncode == 0 else done.stderr
    return pc.verify_lock(LOCK["date"], LOCK["commit"], git=git, design_doc=REPO / pc.DESIGN_DOC_REL)


# ---- generated blocks of the runbook -----------------------------------------------------------------------------------
GEN = re.compile(r"(<!-- p3-gen: (?P<kind>[a-z-]+)(?P<args>[^>]*?) -->\n)(?P<body>.*?)(<!-- /p3-gen -->)", re.S)


def fenced(text: str, language: str = "bash") -> str:
    return f"```{language}\n" + text.rstrip("\n") + "\n```\n"


def render(kind: str, args: list) -> str:
    if kind == "command":
        host, launch = args[:2]
        stop = int(args[2]) if len(args) > 2 else None
        return fenced(words(campaign_args(host, launch, None, stop)).replace(" --", " \\\n    --"))
    if kind == "table":
        rows = cost_rows()
        lines = ["| Host | Instance | $/h | Launches | h per launch | h per round | Lease h | Max at lease $ | Design $ (P-NEW) | Pauses $ (not in design) |",
                 "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for r in rows["rows"]:
            lines.append(f"| {r['host']} | `{r['instance_type']}` | {r['usd_per_h']} | {r['launches']} | {r['hours_per_launch']} | {r['round_hours']} | "
                         f"{r['lease_hours']} | {r['exposure_at_lease_usd']} | {r['design_usd']:.2f} | {r['pause_usd_not_in_design']} |")
        lines.append(f"| Lambda subtotal / GCP subtotal | | | | | | | | {rows['lambda_subtotal_usd']:.2f} / {rows['gcp_subtotal_usd']:.2f} | |")
        lines.append(f"| Storage, snapshots, egress (design) | | | | | | | | {rows['storage_egress_usd']:.2f} | |")
        lines.append(f"| **Core total** (design says ~{DESIGN_CORE_TOTAL_USD:,.0f}) | | | | | | | | **{rows['computed_core_total_usd']:,.2f}** | {rows['pause_total_usd_not_in_design']} |")
        return "\n".join(lines) + "\n"
    if kind == "timeline":
        t = timeline()
        lines = ["| Host | Launch 1 start h | Launch 1 end h | Launch 2 start h | Launch 2 end h |", "|---|---:|---:|---:|---:|"]
        for r in t["rows"]:
            lines.append(f"| {r['host']} | {r['launch1_start_h']} | {r['launch1_end_h']} | {r.get('launch2_start_h', '-')} | {r.get('launch2_end_h', '-')} |")
        lines.append(f"\nPathfinder gate at {t['pathfinder_gate_hours']} h (H100 bring-up plus round 1 plus its copy); everything done at {t['all_done_hours']} h "
                     f"({t['all_done_days']} days); peak spend rate ${t['peak_usd_per_hour']}/h.")
        return "\n".join(lines) + "\n"
    if kind == "inputs":
        t = input_table()
        lines = ["| Scale | Parquet bytes | GiB | Recorded generation s |", "|---|---:|---:|---:|"]
        lines += [f"| SF{r['scale']} | {r['bytes']:,} | {r['gib']} | {r['recorded_generation_s']} |" for r in t["rows"]]
        lines.append(f"| all five | {t['total_bytes']:,} | {t['total_gib']} | {t['recorded_generation_total_min']} min |")
        return "\n".join(lines) + "\n"
    if kind == "launch":
        host, launch, place = args[:3]
        spec = launch_spec(host, launch, place, utc("2026-11-17T06:00:00Z"))
        if spec["provider"] == "lambda":
            return fenced(f"POST {LAMBDA_API}{spec['api_path']}\n" + json.dumps(spec["body"], indent=2), "json")
        return fenced(" \\\n    ".join([" ".join(spec["command"][:5])] + spec["command"][5:]), "bash")
    raise PlanError(f"unknown generated block {kind!r}")


def docs(path: Path, write: bool) -> list:
    text = path.read_text()
    stale = []

    def replace(match):
        kind, args = match.group("kind"), match.group("args").split()
        fresh = render(kind, args)
        if match.group("body") != fresh:
            stale.append(f"{kind} {' '.join(args)}")
        return match.group(1) + fresh + match.group(5)

    new = GEN.sub(replace, text)
    if write and new != text:
        path.write_text(new)
    return stale


# ---- command line ------------------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("table", "timeline"):
        sub.add_parser(name).add_argument("--json", action="store_true")
    for name in ("lease", "launch-spec", "command", "bringup", "stage-script", "rounds", "root", "budget", "dest"):
        p = sub.add_parser(name)
        p.add_argument("host")
        p.add_argument("launch")
        p.add_argument("--ordinal", type=int, default=1)
        p.add_argument("--now")
        p.add_argument("--place")
        p.add_argument("--record")
        p.add_argument("--stop-after", type=int)
        p.add_argument("--type")
        p.add_argument("--skip", default="")
    sub.add_parser("check-lock")
    p = sub.add_parser("check-allowlist")
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p = sub.add_parser("allowlist-review")
    p.add_argument("--capture", required=True, type=Path)
    p.add_argument("--commit", required=True)
    p = sub.add_parser("check-relaunch")
    p.add_argument("host")
    p.add_argument("launch")
    p.add_argument("--old", required=True)
    p.add_argument("--ledger", type=Path, default=Path(os.environ.get("P3_LEDGER_DIR") or Path.home() / "p3ledger"))
    p.add_argument("--old-dest", type=Path)
    p = sub.add_parser("docs")
    p.add_argument("file", type=Path)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "table":
            print(json.dumps(cost_rows(), indent=2) if args.json else render("table", []))
        elif args.cmd == "timeline":
            print(json.dumps(timeline(), indent=2) if args.json else render("timeline", []))
        elif args.cmd == "lease":
            print(json.dumps(lease_spec(args.host, args.launch, utc(args.now), args.ordinal), indent=2))
        elif args.cmd == "launch-spec":
            if not args.place:
                parser.error("launch-spec needs --place (a Lambda region or a GCP zone)")
            env = os.environ
            print(json.dumps(launch_spec(args.host, args.launch, args.place, utc(args.now), instance_type=args.type,
                                         termination_action=env.get("P3_GCP_TERMINATION_ACTION") or "STOP",
                                         extra_flags=shlex.split(env.get("P3_GCP_EXTRA_FLAGS", "")),
                                         ssh_key_name=env.get("P3_LAMBDA_SSH_KEY_NAME") or LAMBDA_SSH_KEY_NAME,
                                         os_login=env.get("P3_GCP_OS_LOGIN", "") not in ("", "0"), ordinal=args.ordinal), indent=2))
        elif args.cmd == "command":
            print(words(campaign_args(args.host, args.launch, read_record(args.record), args.stop_after)))
        elif args.cmd == "bringup":
            record = read_record(args.record)
            if record is None:
                parser.error("bringup needs --record")
            print(bringup_script(args.host, args.launch, record, skip=tuple(s for s in args.skip.split(",") if s)), end="")
        elif args.cmd == "stage-script":
            print(stage_script(args.host, args.launch, args.stop_after, read_record(args.record)), end="")
        elif args.cmd == "rounds":
            print("\n".join(str(r) for r in rounds_of(args.host, args.launch)))
        elif args.cmd == "root":
            check_launch(args.host, args.launch)
            print(root_rel(args.host, args.launch))
        elif args.cmd == "budget":
            print(json.dumps(budget(args.host, args.launch)))
        elif args.cmd == "check-lock":
            print(json.dumps(check_lock(), indent=2))
        elif args.cmd == "check-allowlist":
            print(json.dumps(check_allowlist_change(args.old, args.new), indent=2))
        elif args.cmd == "allowlist-review":
            print(json.dumps(allowlist_review(args.capture, args.commit), indent=2))
        elif args.cmd == "dest":
            check_launch(args.host, args.launch)
            print(f"rerun/paper3/{args.host}/{launch_dir_name(args.launch, args.ordinal)}")
        elif args.cmd == "check-relaunch":
            print(json.dumps(check_relaunch(args.host, args.launch, args.old, args.ledger, args.old_dest), indent=2))
        elif args.cmd == "docs":
            stale = docs(args.file, args.write)
            if stale and args.check:
                print("stale generated blocks: " + "; ".join(stale), file=sys.stderr)
                return 1
    except (PlanError, pc.PlanError) as exc:
        print(f"p3_plan.py: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
