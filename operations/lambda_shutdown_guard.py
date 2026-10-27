#!/usr/bin/env python3
"""Fail-closed, offhost completion gate for one explicitly identified H100.

prepare is offline; status makes one read-only Lambda request. terminate is the
only mutating command and rechecks every completion gate. No SSH, guest poweroff,
job signals, credential files, environment credentials, redirects or API retries.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

API_BASE = "https://cloud.lambda.ai/api/v1"
SCHEMA = "h100-conditional-termination-v1"
AUTHORIZED_IP = "68.209.73.247"
AUTHORIZED_INSTANCE_ID = "8809d4738226458a80a100bb4eb2f906"
AUTHORIZED_INSTANCE_TYPE = "gpu_1x_h100_sxm5"
AUTHORIZED_GPU_UUID = "GPU-aa9e9485-5551-d7be-dfc9-9e433a7284da"
AUTHORIZED_GPU_NAME = "NVIDIA H100 80GB HBM3"
AUTHORIZED_DRIVER = "580.105.08"
RECOVERY_CAMPAIGN_ID = "h100-confirmatory-gclocker100-20261027"
PROFILE_NAME = "EXECUTION_PROFILE.json"
PROFILE_GATES_NAME = "EXECUTION_PROFILE_GATES.jsonl"
PROFILE_GATE_PHASES = ("readiness-before", "readiness-after", *[f"block-{n:02d}-{edge}" for n in range(1, 11)
                                                           for edge in ("before", "after")], "final-before", "final-after")
AUDITOR_PATH = Path(__file__).resolve().parents[1] / "research_reporting/audit_complete.py"
COUNTS = {"sessions": 400, "successful_outputs": 9360, "warmup_outputs": 2340,
          "measured_outputs": 7020, "translation_rejections": 60,
          "successful_complex_cleanups": 3360, "failed_complex_cleanups": 60}
SHA = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
INSTANCE_ID = re.compile(r"(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\Z")

_spec = importlib.util.spec_from_file_location("shutdown_backup_verifier", Path(__file__).with_name("research_backup.py"))
backup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backup)
_identity_spec = importlib.util.spec_from_file_location(
    "shutdown_campaign_identity", Path(__file__).resolve().parents[1] / "research_reporting/campaign_identity.py")
campaign_identity = importlib.util.module_from_spec(_identity_spec)
_identity_spec.loader.exec_module(campaign_identity)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return backup.sha(path)


def strict_json(data):
    def pairs(entries):
        result = {}
        for key, value in entries:
            require(key not in result, "Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def read_json(path):
    return strict_json(Path(path).read_bytes())


def local_path(base, name, directory=False):
    """All evidence must be regular, offhost local bytes beneath manifest parent."""
    require(isinstance(name, str) and bool(name), "Missing evidence path")
    path = Path(name)
    path = path if path.is_absolute() else base / path
    require(".." not in path.parts, "Parent traversal in evidence path")
    require(path.is_relative_to(base), "Evidence is outside the local evidence base")
    cursor = base
    for part in path.relative_to(base).parts:
        cursor /= part
        require(not cursor.is_symlink(), "Symlink is not completion evidence")
    require(path.is_dir() if directory else path.is_file(), "Missing regular completion evidence")
    require(path.resolve(strict=True).is_relative_to(base), "Evidence escapes local evidence base")
    return path


def reference(base, entry):
    require(isinstance(entry, dict) and SHA.fullmatch(entry.get("sha256", "")), "Invalid evidence SHA256 reference")
    path = local_path(base, entry.get("path"))
    require(sha(path) == entry["sha256"], "Completion evidence SHA256 differs")
    return path


def inventory(base, root, expected):
    require(isinstance(expected, dict) and bool(expected), "Missing complete file inventory")
    root = local_path(base, str(root), directory=True)
    for relative, digest in expected.items():
        require(not Path(relative).is_absolute() and SHA.fullmatch(digest or ""), "Invalid inventory entry")
        path = local_path(base, str(root / relative))
        require(sha(path) == digest, "Replay inventory SHA256 differs")


def utc(value):
    require(isinstance(value, str), "Evidence timestamp is missing")
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(moment.utcoffset() is not None, "Evidence timestamp needs a timezone")
    return moment.astimezone(timezone.utc)


def expected_identity(args):
    identity = {key: getattr(args, "expected_" + key) for key in
                ("instance_id", "ip", "gpu_uuid", "gpu_name", "driver_version", "api_instance_type", "api_gpu_description")}
    require(INSTANCE_ID.fullmatch(identity["instance_id"]), "Expected Lambda instance UUID is required")
    require(identity["instance_id"] == AUTHORIZED_INSTANCE_ID, "Instance UUID is not the authorized replacement VM")
    require(str(ipaddress.IPv4Address(identity["ip"])) == AUTHORIZED_IP, "IP is not the authorized replacement VM")
    require(identity["gpu_uuid"] == AUTHORIZED_GPU_UUID and identity["gpu_name"] == AUTHORIZED_GPU_NAME
            and identity["driver_version"] == AUTHORIZED_DRIVER, "GPU/driver is not the authorized replacement VM")
    require(identity["api_instance_type"] == AUTHORIZED_INSTANCE_TYPE and identity["api_gpu_description"],
            "Exact cloud GPU/type identity is required")
    return identity


def verify_backup(base, entry, phase, campaign):
    control_path = reference(base, entry["control"])
    receipt_path = reference(base, entry["receipt"])
    archive_path = reference(base, entry["archive"])
    control = read_json(control_path)
    require(control.get("schema_version") == backup.SCHEMA and control.get("phase") == phase
            and control.get("campaign_sha256") == campaign and control.get("sessions") == 400,
            "Raw backup is not the full matching final/completion snapshot")
    require(control.get("tar_sha256") == sha(archive_path) and control.get("tar_bytes") == archive_path.stat().st_size,
            "Offhost raw archive hash/size differs")
    receipt = backup.verify_receipt(control_path, receipt_path)
    directory = local_path(base, entry["directory"], directory=True)
    require(Path(receipt["local_directory"]).resolve() == directory, "Offhost receipt names a different local directory")
    backup.verify_directory(directory, control)
    return directory


def verify_posthash(plan, journal):
    sessions = journal.get("sessions", {})
    require(len(plan.get("sessions", [])) == len(sessions) == 400, "All 400 sessions are required")
    require(set(sessions) == {s["session_id"] for s in plan["sessions"]}, "Final journal sessions differ from campaign")
    ends = []
    for value in sessions.values():
        attempts = value.get("attempts", [])
        require(value.get("status") == "succeeded" and len(attempts) == 1
                and attempts[0].get("status") == "succeeded" and attempts[0].get("returncode") == 0,
                "A session is incomplete, retried, running or failed")
        ends.append(utc(attempts[0]["ended_at_utc"]))
    # The complete auditor independently proves every scale's exact file count.
    scales = set(plan["datasets"])
    completed = utc(journal["completed_at_utc"])
    gates = []
    for gate in journal.get("input_integrity_gates", []):
        checks = gate.get("checks", [])
        if (len(checks) == len(scales) and {str(v.get("scale")) for v in checks} == scales
                and all(v.get("status") == "pass" and type(v.get("files")) is int and v["files"] > 0 for v in checks)
                and max(ends) < utc(gate["completed_at_utc"]) <= completed):
            gates.append(gate)
    require(gates, "No passing full post-final-action hash gate")


def verify_recovery_profile(base, plan, campaign, audit, replay, identity, raw):
    """Bind the sealed profile and final receipts to the actual complete-audit inputs."""
    require(plan.get("campaign_id") == replay.get("campaign_id") == identity.get("campaign_id") == RECOVERY_CAMPAIGN_ID
            and campaign not in {campaign_identity.ORIGINAL_CAMPAIGN_SHA256,
                                 campaign_identity.ABORTED_V1_CAMPAIGN_SHA256}
            and SHA.fullmatch(identity.get("execution_profile_sha256", "")),
            "Only the exact registered recovery campaign/profile can permit termination")
    require(audit.get("registered_campaign_identity") == replay.get("registered_campaign_identity") == identity,
            "Audit/replay registered recovery identity differs")
    profile_sha = identity["execution_profile_sha256"]
    require(audit.get("execution_profile") == {"path": PROFILE_NAME, "sha256": profile_sha,
                "spark_driver_logs": 160, "profile_gates": 24, "status": "PASS"},
            "Complete recovery profile proof requires all 160 Spark drivers and 24 gates")
    require(audit.get("source_sha256") == replay.get("auditor_source_sha256") == sha(AUDITOR_PATH),
            "Complete recovery audit source differs from the current auditor")
    archive = local_path(base, replay["archive"], directory=True)
    profile_path = local_path(base, str(archive / PROFILE_NAME))
    prestart_path = local_path(base, str(archive / "PRESTART_MANIFEST.json"))
    archived_gates = local_path(base, str(archive / PROFILE_GATES_NAME))
    artifacts = local_path(base, replay["artifacts_root"], directory=True)
    full_gates = local_path(base, str(artifacts.parent / PROFILE_GATES_NAME))
    prestart = read_json(prestart_path)
    require(sha(prestart_path) == replay.get("prestart_sha256") == identity.get("prestart_sha256")
            and prestart.get("schema_version") == "h100-confirmatory-prestart-archive-v1"
            and prestart.get("status") == "PASS" and prestart.get("campaign_sha256") == campaign
            and prestart.get("files_sha256", {}).get(PROFILE_NAME) == profile_sha
            and prestart.get("files_sha256", {}).get(PROFILE_GATES_NAME) == sha(archived_gates)
            and sha(profile_path) == profile_sha,
            "Registered prestart does not seal the exact recovery profile/readiness receipts")
    profile = read_json(profile_path)
    require(profile.get("schema_version") == "h100-jvm-execution-profile-v1"
            and profile.get("campaign_id") == RECOVERY_CAMPAIGN_ID
            and profile.get("source_commit") == identity.get("source_commit")
            and profile.get("source_tree_sha256") == identity.get("source_tree_sha256"),
            "Sealed recovery profile source/campaign identity differs")
    inputs = audit["input_files_sha256"]
    for path in (profile_path, prestart_path, archived_gates, full_gates):
        require(inputs.get(str(path.resolve())) == sha(path),
                "Recovery profile/prestart/receipts are missing from exact complete-audit inputs")
    prefix, full = archived_gates.read_bytes(), full_gates.read_bytes()
    require(prefix.endswith(b"\n") and full.endswith(b"\n") and full.startswith(prefix),
            "Final profile receipts do not preserve the sealed readiness prefix")
    sealed = [strict_json(line) for line in prefix.splitlines()]
    gates = [strict_json(line) for line in full.splitlines()]
    require(all(isinstance(g, dict) for g in sealed + gates), "Profile receipts must be JSON objects")
    require([g.get("phase") for g in sealed] == list(PROFILE_GATE_PHASES[:2])
            and [g.get("phase") for g in gates] == list(PROFILE_GATE_PHASES)
            and all(g.get("schema_version") == "h100-jvm-execution-profile-gate-v1"
                    and g.get("profile_sha256") == profile_sha and g.get("status") == "PASS" for g in gates),
            "Exactly 24 ordered passing recovery profile receipts are required")
    for root in raw.values():
        require(sha(local_path(base, str(root / PROFILE_NAME))) == profile_sha
                and sha(local_path(base, str(root / PROFILE_GATES_NAME))) == sha(full_gates),
                "Raw final/completion profile or receipts differ from the audited recovery bytes")


def verify_completion(manifest_path, expected, now=None):
    manifest_path = Path(manifest_path).absolute()
    require(not manifest_path.is_symlink(), "Manifest cannot be a symlink")
    base = manifest_path.parent.resolve(strict=True)
    manifest_hash = sha(manifest_path)
    manifest = read_json(manifest_path)
    require(manifest.get("schema_version") == SCHEMA and manifest.get("status") == "COMPLETE"
            and manifest.get("instance") == expected and manifest.get("offhost") is True,
            "Completion manifest is incomplete, onhost, or identifies a different VM")
    artifacts = manifest["artifacts"]
    paths = {name: reference(base, artifacts[name]) for name in
             ("campaign", "journal", "complete_audit", "replay_manifest", "paper_pdf", "paper_source",
              "publication_receipt", "web_receipt", "private_git_receipt", "identity_idle_receipt")}
    campaign = sha(paths["campaign"])
    require(manifest.get("campaign_sha256") == campaign, "Completion manifest campaign differs")
    plan, journal = read_json(paths["campaign"]), read_json(paths["journal"])
    require(plan.get("synthetic") is False and plan.get("campaign_id") == RECOVERY_CAMPAIGN_ID
            and journal.get("campaign_sha256") == campaign,
            "Only the genuine recovery campaign can permit termination")
    verify_posthash(plan, journal)
    audit = read_json(paths["complete_audit"])
    require(audit.get("schema_version") == "research-complete-audit-v1" and audit.get("status") == "PASS"
            and audit.get("synthetic") is False and audit.get("campaign_sha256") == campaign
            and audit.get("journal_sha256") == sha(paths["journal"]) and audit.get("counts") == COUNTS
            and audit.get("findings") == [] and audit.get("checks_evaluated", 0) > 0,
            "Genuine complete audit must PASS all 400 sessions / 9360 outputs")
    inputs = audit.get("input_files_sha256", {})
    require(bool(inputs), "Complete audit has no preserved input hashes")
    for name, digest in inputs.items():
        reference(base, {"path": name, "sha256": digest})
    replay = read_json(paths["replay_manifest"])
    require(replay.get("schema_version") == "research-artifact-replay-v1" and replay.get("status") == "PASS"
            and replay.get("phase") == "complete" and replay.get("synthetic") is False
            and replay.get("campaign_sha256") == campaign
            and replay.get("complete_audit", {}).get("sha256") == sha(paths["complete_audit"]),
            "Real portable replay must PASS for the same complete audit")
    identity = campaign_identity.registered_identity(campaign, replay.get("prestart_sha256"))
    commands = replay.get("commands", [])
    require([c.get("name") for c in commands] == ["validation", "analysis", "complete_audit", "rendering"]
            and all(c.get("status") == "PASS" and c.get("returncode") == 0 for c in commands),
            "Portable replay commands are incomplete or failed")
    inventory(base, replay["archive"], replay.get("archive_files_sha256"))
    inventory(base, replay["artifacts_root"], replay.get("artifact_files_sha256"))
    inventory(base, paths["replay_manifest"].parent, replay.get("output_files_sha256"))
    raw = {phase: verify_backup(base, manifest["raw_backups"][phase], phase, campaign)
           for phase in ("final", "completion")}
    inventory(base, raw["final"] / "campaign-results", replay["artifact_files_sha256"])
    verify_recovery_profile(base, plan, campaign, audit, replay, identity, raw)
    require(sha(raw["completion"] / "campaign-results/journal.json") == sha(paths["journal"])
            and (raw["completion"] / "confirm-driver.exit").read_text().strip() == "0"
            and (raw["completion"] / "confirm-driver.ended-utc").is_file(),
            "Offhost completion backup does not prove successful actual driver exit")
    with paths["paper_pdf"].open("rb") as handle:
        require(handle.read(5) == b"%PDF-", "Full paper PDF is missing")
    paper = read_json(paths["publication_receipt"])
    require(paper.get("schema_version") == "h100-final-publication-v1" and paper.get("status") == "PASS"
            and paper.get("full_paper") is True and paper.get("final_claim_audit") == "PASS"
            and paper.get("campaign_sha256") == campaign and paper.get("paper_pdf_sha256") == sha(paths["paper_pdf"])
            and paper.get("paper_source_sha256") == sha(paths["paper_source"]),
            "Full paper/source and final claims audit are not complete")
    web = read_json(paths["web_receipt"])
    require(web.get("schema_version") == "h100-public-web-deployment-v1" and web.get("status") == "succeeded"
            and web.get("audience") == "public" and bool(web.get("version"))
            and web.get("campaign_sha256") == campaign and web.get("paper_pdf_sha256") == sha(paths["paper_pdf"])
            and SHA.fullmatch(web.get("page_sha256", "")), "Public versioned deployment is incomplete")
    public_url(web.get("version_url"))
    private = read_json(paths["private_git_receipt"])
    require(private.get("schema_version") == "h100-private-git-evidence-v1"
            and private.get("status") == "PRIVATE_PUSH_VERIFIED" and private.get("private") is True
            and private.get("campaign_sha256") == campaign and COMMIT.fullmatch(private.get("commit", ""))
            and private.get("remote_commit") == private.get("commit") and bool(private.get("repository_url"))
            and bool(private.get("files_sha256")), "Private Git evidence is not preserved and pushed")
    repository = local_path(base, private["local_repository"], directory=True)
    for name, digest in private["files_sha256"].items():
        require(not Path(name).is_absolute() and ".." not in Path(name).parts and SHA.fullmatch(digest),
                "Invalid private Git evidence path/hash")
        body = subprocess.check_output(["git", "-C", str(repository), "show", f"{private['commit']}:{name}"], stderr=subprocess.DEVNULL)
        require(hashlib.sha256(body).hexdigest() == digest, "Private Git commit does not preserve exact evidence bytes")
    require(set(artifacts[name]["sha256"] for name in ("campaign", "journal", "complete_audit", "replay_manifest", "paper_pdf", "paper_source"))
            <= set(private["files_sha256"].values()), "Private Git commit is missing final scientific/publication evidence")
    idle = read_json(paths["identity_idle_receipt"])
    now = now or datetime.now(timezone.utc)
    require(idle.get("schema_version") == "h100-offhost-identity-idle-v1" and idle.get("instance") == expected
            and idle.get("campaign_sha256") == campaign and idle.get("driver_exit") == 0
            and idle.get("driver_alive") is False and idle.get("benchmark_processes") == []
            and idle.get("gpu_compute_processes") == [] and bool(idle.get("capture_log_sha256")),
            "Latest offhost identity/idle evidence is incomplete or jobs are still running")
    reference(base, {"path": idle["capture_log_path"], "sha256": idle["capture_log_sha256"]})
    age = (now - utc(idle["captured_at_utc"])).total_seconds()
    require(0 <= age <= 300 and utc(idle["captured_at_utc"]) >= utc(journal["completed_at_utc"]),
            "Identity/idle evidence is stale or precedes completion; capture fresh read-only evidence")
    require(sha(manifest_path) == manifest_hash, "Completion manifest changed during verification")
    return {"manifest_sha256": manifest_hash, "campaign_sha256": campaign, "web": web}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Redirect refused")


def public_url(url):
    require(isinstance(url, str), "Missing public deployment version URL")
    parts = urlsplit(url)
    require(parts.scheme == "https" and bool(parts.hostname) and not parts.username and not parts.password
            and not parts.fragment and not parts.query, "Public version URL must be direct HTTPS without credentials/query")
    require(parts.hostname not in {"localhost", "localhost.localdomain"} and not parts.hostname.endswith(".local"),
            "Deployment URL is local")
    try:
        address = ipaddress.ip_address(parts.hostname)
    except ValueError:
        return url
    require(address.is_global, "Deployment URL is not public")
    return url


def read_api_key(stream):
    require(not stream.isatty() and stat.S_ISFIFO(os.fstat(stream.fileno()).st_mode),
            "Supply the API key only through a secure stdin pipe; terminal/files/env/arguments are unsupported")
    value = stream.read(1025)
    require(len(value) <= 1024 and ("\n" not in value or (value.endswith("\n") and value.count("\n") == 1))
            and 16 <= len(value.strip()) <= 512 and all(33 <= ord(c) <= 126 for c in value.strip()),
            "Stdin must contain exactly one API-key line")
    return value.strip()


def request_json(method, path, key, body=None):
    require(path == "/instances/" + AUTHORIZED_INSTANCE_ID or path == "/instance-operations/terminate", "Unapproved API endpoint")
    require((method == "GET" and path == "/instances/" + AUTHORIZED_INSTANCE_ID and body is None)
            or (method == "POST" and path == "/instance-operations/terminate"), "Unapproved API operation")
    require(method != "POST" or body == {"instance_ids": [AUTHORIZED_INSTANCE_ID]}, "Unapproved termination target")
    data = None if body is None else json.dumps(body).encode()
    request = Request(API_BASE + path, method=method, data=data,
                      headers={"Authorization": "Bearer " + key, "Accept": "application/json", "Content-Type": "application/json"})
    try:
        with build_opener(NoRedirect).open(request, timeout=30) as response:
            return json.loads(response.read(2 * 1024 * 1024))
    except HTTPError as exc:
        raise ValueError(f"Lambda API HTTP {exc.code}; no automatic retry") from None
    except (URLError, TimeoutError, OSError):
        raise ValueError("Lambda API transport failed; outcome may be unknown after POST; check read-only status") from None


def cloud_identity(value, expected, terminating=False):
    require(isinstance(value, dict) and value.get("id") == expected["instance_id"], "Lambda instance UUID differs")
    require(value.get("ip") == expected["ip"], "Lambda instance IP differs")
    gpu = value.get("instance_type", {})
    require(gpu.get("name") == expected["api_instance_type"]
            and gpu.get("gpu_description") == expected["api_gpu_description"]
            and gpu.get("specs", {}).get("gpus") == 1, "Lambda GPU/type identity differs")
    if terminating:
        require(value.get("status") == "active" and value.get("actions", {}).get("terminate", {}).get("available") is True,
                "Instance is not active/available for termination")
    return {"instance_id": value["id"], "ip": value["ip"], "status": value.get("status"),
            "api_instance_type": gpu["name"], "api_gpu_description": gpu["gpu_description"]}


def terminate(manifest_path, expected, key, api=request_json, page_fetch=None):
    """The sole mutation path; all checks run again within this invocation."""
    verified = verify_completion(manifest_path, expected)
    instance = api("GET", "/instances/" + expected["instance_id"], key)["data"]
    cloud_identity(instance, expected, terminating=True)
    web = verified["web"]
    if page_fetch is None:
        def page_fetch(url):
            with build_opener(NoRedirect).open(url, timeout=30) as response:
                require(response.status == 200, "Public deployment did not return HTTP 200")
                return response.read(16 * 1024 * 1024 + 1)
    page = page_fetch(public_url(web["version_url"]))
    require(len(page) <= 16 * 1024 * 1024 and hashlib.sha256(page).hexdigest() == web["page_sha256"],
            "Public deployed version content differs or is not available")
    require(sha(manifest_path) == verified["manifest_sha256"], "Manifest changed before termination")
    # Lambda permits about one request/second. Never retry the destructive POST.
    time.sleep(1.05)
    response = api("POST", "/instance-operations/terminate", key, {"instance_ids": [expected["instance_id"]]})
    instances = response.get("data", {}).get("terminated_instances", [])
    require(len(instances) == 1 and instances[0].get("id") == expected["instance_id"]
            and instances[0].get("status") in {"terminated", "terminating"},
            "Termination response is not definitive for this instance; use read-only status; do not retry blindly")
    return {"status": instances[0]["status"], "instance_id": expected["instance_id"],
            "campaign_sha256": verified["campaign_sha256"], "manifest_sha256": verified["manifest_sha256"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "status", "terminate"))
    parser.add_argument("--manifest", type=Path)
    for field in ("instance-id", "ip", "gpu-uuid", "gpu-name", "driver-version", "api-instance-type", "api-gpu-description"):
        parser.add_argument("--expected-" + field, required=True)
    args = parser.parse_args(argv)
    try:
        expected = expected_identity(args)
        if args.command == "prepare":
            require(args.manifest is not None, "prepare requires --manifest")
            verified = verify_completion(args.manifest, expected)
            result = {"status": "LOCAL_COMPLETION_VERIFIED", "manifest_sha256": verified["manifest_sha256"],
                      "campaign_sha256": verified["campaign_sha256"], "mutation_performed": False}
        else:
            require(args.command != "terminate" or args.manifest is not None, "terminate requires --manifest")
            # Fail before accepting a credential when completion is absent.
            if args.command == "terminate":
                verify_completion(args.manifest, expected)
            key = read_api_key(sys.stdin)
            if args.command == "status":
                value = request_json("GET", "/instances/" + expected["instance_id"], key)["data"]
                if value.get("id") == expected["instance_id"] and value.get("status") == "terminated":
                    result = {"instance_id": value["id"], "status": "terminated", "mutation_performed": False}
                else:
                    result = {**cloud_identity(value, expected), "mutation_performed": False}
            else:
                result = terminate(args.manifest, expected, key)
            key = None
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as exc:
        # Errors never include API responses, request headers, keys or URLs.
        print(json.dumps({"status": "REFUSED", "reason": str(exc) if isinstance(exc, ValueError) else type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
