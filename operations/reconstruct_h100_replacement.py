#!/usr/bin/env python3
"""Reconstruct a new offline harvest from verified replacement backup snapshots.

No network, engine, query, original evidence rewrite or scientific PASS occurs.
The output contains harvest/, lineage/ and RECONSTRUCTION_MANIFEST.json. Every
transport phase is required; only canonical prestart, final and completion
contribute payload bytes. The original snapshots and JSON paths stay intact.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))
from operations import research_backup as backup
from research_reporting import replay
from research_reporting import campaign_identity
from research_reporting import audit_complete

COHORTS = {"replacement": "h100-confirmatory-replacement-20261027",
           "gclocker100": campaign_identity.RECOVERY_CAMPAIGN_ID}
CAMPAIGN_ID = COHORTS["replacement"]  # Historical v1 transport remains inspectable.
PHASES = ("prestart", *(f"block-{i:02d}" for i in range(1, 11)), "final", "completion")
TRANSPORT_NAMES = {"BACKUP_MANIFEST.json", "PRESTART_MANIFEST.json"}
# The preparation process has exited before prestart. Its logs/markers and every
# scientific file are immutable. Supervisor status files and append-only idle
# profile receipts can advance after prestart. New markers preserve old bytes.
MUTABLE_ROOT_FILES = {
    "confirm-driver.log": "Supervisor stdout remains open until its actual exit.",
    "confirm-driver.phase": "The supervisor advances its operational phase marker.",
    "EXECUTION_PROFILE_GATES.jsonl": "Idle profile receipts append in their frozen order.",
}
def tool_paths():
    return (Path(__file__).resolve(), Path(backup.__file__).resolve(),
            Path(replay.__file__).resolve(), Path(campaign_identity.__file__).resolve(),
            campaign_identity.REGISTRY_PATH.resolve(), Path(audit_complete.__file__).resolve(),
            REPOSITORY / "operations/jvm_execution_profile.py")


def read_json(path):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError(f"Duplicate JSON key in {path}: {key}")
            value[key] = item
        return value
    return json.loads(Path(path).read_bytes(), object_pairs_hook=pairs)


def identity(path):
    return {"sha256": backup.sha(path), "bytes": Path(path).stat().st_size}


def inventory(root):
    return {backup.safe_file(path, root): identity(path)
            for path in sorted(backup.tree_files(root))}


def copy_exact(source, target, expected):
    target.parent.mkdir(parents=True, exist_ok=True)
    with Path(source).open("rb") as src, target.open("xb") as dst:
        shutil.copyfileobj(src, dst, 8 * 1024 * 1024)
    if identity(target) != expected or identity(source) != expected:
        raise ValueError(f"Input changed during reconstruction: {source}")


def utc(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.utcoffset() is None:
        raise ValueError("Evidence timestamps must include a timezone")
    return stamp


def recorded_upper_bound(value):
    """Shell terminal markers record seconds; preserve their one-second precision."""
    stamp = utc(value)
    return stamp + timedelta(seconds=1) if "." not in value else stamp


def session_prefixes(plan, cohort="gclocker100"):
    if cohort not in COHORTS:
        raise ValueError("Unknown reconstruction cohort")
    sessions = plan.get("sessions", [])
    ids = [session["session_id"] for session in sessions]
    blocks = {phase: [] for phase in PHASES if phase.startswith("block-")}
    if (plan.get("campaign_id") != COHORTS[cohort] or plan.get("synthetic") is True
            or "SYNTHETIC" in plan.get("campaign_id", "").upper()
            or len(ids) != 400 or len(set(ids)) != 400
            or len(plan.get("blocks", [])) != 10 or len(plan.get("primary_contrasts", [])) != 26
            or plan.get("seed") != 20261026 or plan.get("settings", {}).get("runs") != 3
            or plan["settings"].get("cpus") != 26):
        raise ValueError("Expected the exact registered 400-session replacement design")
    root = Path(plan["output_root"])
    if not root.is_absolute() or root.name != "campaign-results":
        raise ValueError("Campaign output root is not an absolute campaign-results path")
    prefixes = {}
    for session in sessions:
        relative = Path(session["output_root"]).relative_to(root).as_posix()
        backup.safe_relative(relative)
        if relative != session["session_id"] or session["block_id"] not in blocks:
            raise ValueError("Session output path/block differs from its frozen identity")
        prefix = "campaign-results/" + relative + "/"
        prefixes[session["session_id"]] = prefix
        blocks[session["block_id"]].append(prefix)
    if any(len(selected) != 40 for selected in blocks.values()):
        raise ValueError("Each of the ten original blocks must contain 40 sessions")
    return prefixes, blocks


def mapped(plan, frozen):
    relative = Path(frozen).relative_to(Path(plan["output_root"])).as_posix()
    backup.safe_relative(relative)
    return "campaign-results/" + relative


def verify_completed_journal(plan, campaign, root, archive):
    journal = read_json(root / "campaign-results/journal.json")
    sessions = journal.get("sessions", {})
    if (journal.get("schema_version") != "research-campaign-journal-v1"
            or journal.get("campaign_sha256") != campaign or journal.get("synthetic") is True
            or not journal.get("completed_at_utc")
            or set(sessions) != {session["session_id"] for session in plan["sessions"]}):
        raise ValueError("A complete post-final 400-session journal is required")
    bounds = []
    for session in plan["sessions"]:
        item = sessions[session["session_id"]]
        attempts = item.get("attempts", [])
        if item.get("status") != "succeeded" or len(attempts) != 1:
            raise ValueError("Every original session must succeed with exactly one attempt")
        attempt = attempts[0]
        if attempt.get("status") != "succeeded" or attempt.get("returncode") != 0:
            raise ValueError("A raw successful attempt record is required")
        attempt_root = Path(session["output_root"]) / "attempt-001"
        run_root = attempt_root / "run"
        if attempt.get("attempt_root") != str(attempt_root) or attempt.get("run_root") != str(run_root):
            raise ValueError("Attempt paths differ from the original session")
        gate = item.get("scientific_validation", {})
        gate_name = mapped(plan, gate.get("path", ""))
        expected_gate = mapped(plan, attempt_root / "SCIENTIFIC_VALIDATION.json")
        if (gate.get("status") != "PASS" or gate_name != expected_gate
                or backup.sha(root / gate_name) != gate.get("sha256")):
            raise ValueError("Raw scientific gate path/hash/status differs")
        artifacts = attempt.get("artifacts", {})
        result = run_root / f"sf{session['scale']}" / session["engine"] / "results.json"
        if (artifacts.get("results_path") != str(result)
                or backup.sha(root / mapped(plan, result)) != artifacts.get("results_sha256")
                or backup.sha(root / mapped(plan, run_root / "schedule.json")) != artifacts.get("schedule_sha256")):
            raise ValueError("Raw result/schedule identity differs")
        for file in (attempt_root / "session.json", attempt_root / "orchestrator.log", run_root / "environment.json"):
            backup.safe_file(root / mapped(plan, file), root)
        bounds.append((utc(attempt["started_at_utc"]), utc(attempt["ended_at_utc"])))
    if (any(start > end for start, end in bounds)
            or any(left[1] > right[0] for left, right in zip(bounds, bounds[1:]))):
        raise ValueError("Session chronology overlaps or differs from frozen order")
    completed = utc(journal["completed_at_utc"])
    scales = set(plan["datasets"])
    file_counts = {}
    for scale, dataset in plan["datasets"].items():
        candidates = [archive / name / f"sf{scale}" / "manifest.json" for name in ("manifests", "dataset-manifests")]
        candidates += [archive / name / f"sf{scale}.json" for name in ("manifests", "dataset-manifests")]
        manifest_path = next((path for path in candidates if path.is_file()
                              and backup.sha(path) == dataset["manifest_sha256"]), None)
        if manifest_path is None:
            raise ValueError(f"No frozen copied dataset manifest for SF{scale}")
        dataset_manifest = read_json(manifest_path)
        file_counts[scale] = sum(len(table["files"]) for table in dataset_manifest["tables"].values())
    passing = []
    for gate in journal.get("input_integrity_gates", []):
        checks = gate.get("checks", [])
        if (len(checks) != len(scales) or {entry.get("scale") for entry in checks} != scales
                or any(entry.get("status") != "pass" or type(entry.get("files")) is not int
                       or entry["files"] != file_counts.get(entry.get("scale")) for entry in checks)):
            raise ValueError("Full-byte integrity gates must cover every dataset scale")
        passing.append(utc(gate["completed_at_utc"]))
    if not (any(stamp <= bounds[0][0] for stamp in passing)
            and any(bounds[-1][1] < stamp <= completed for stamp in passing)):
        raise ValueError("A byte gate before timing and a true post-final byte gate are required")
    return journal, bounds


def verify_snapshot(raw, phase, campaign, scratch):
    paths = {"control": raw / f"{phase}.json", "tar": raw / f"{phase}.tar",
             "verified_report": raw / f"{phase}.verified.json", "directory": raw / phase}
    for key, path in paths.items():
        if path.is_symlink() or not (path.is_dir() if key == "directory" else path.is_file()):
            raise ValueError(f"Missing/nonregular {phase} {key}; partial backups are not accepted")
    initial_inputs = {key: {"path": str(path), **identity(path)}
                      for key, path in paths.items() if key != "directory"}
    control, report = read_json(paths["control"]), read_json(paths["verified_report"])
    count = 0 if phase == "prestart" else 40 if phase.startswith("block-") else 400
    if (control.get("schema_version") != backup.SCHEMA or control.get("phase") != phase
            or control.get("campaign_sha256") != campaign or control.get("sessions") != count
            or control.get("synthetic") is True or report.get("synthetic") is True
            or report.get("status") != "OFFHOST_SHA256_VERIFIED"
            or any(report.get(key) != value for key, value in control.items())
            or report.get("local_directory") != str(paths["directory"])
            or report.get("local_archive") != str(paths["tar"])):
        raise ValueError(f"Control/report identity differs for {phase}")
    # Always inspect/extract the actual tar in a new temporary directory, even
    # when the original extracted tree exists. The helper's reuse fast path
    # alone would only inspect the existing directory after hashing the tar.
    extracted = scratch / phase
    try:
        manifest = backup.verify_archive(paths["tar"], control, extracted)
        if manifest.get("synthetic") is True:
            raise ValueError("Synthetic transport manifests are never replacement evidence")
        if backup.verify_directory(paths["directory"], control) != manifest:
            raise ValueError(f"Tar/extracted manifest differs for {phase}")
    finally:
        shutil.rmtree(extracted, ignore_errors=True)
    if any(identity(Path(row["path"])) != {key: row[key] for key in ("sha256", "bytes")}
           for row in initial_inputs.values()):
        raise ValueError(f"Transport inputs changed while validating {phase}")
    return {"paths": paths, "control": control, "manifest": manifest,
            "inputs": initial_inputs, "files": inventory(paths["directory"])}


def reconstruct(archive, raw_backups, output, cohort="gclocker100"):
    if cohort not in COHORTS:
        raise ValueError("Unknown reconstruction cohort")
    if Path(archive).is_symlink() or Path(raw_backups).is_symlink():
        raise ValueError("Original input roots cannot be symlinks")
    archive, raw = Path(archive).resolve(strict=True), Path(raw_backups).resolve(strict=True)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Refusing to reuse reconstruction output: {output}")
    if (output.resolve().is_relative_to(archive) or output.resolve().is_relative_to(raw)
            or archive.is_relative_to(output.resolve()) or raw.is_relative_to(output.resolve())):
        raise ValueError("Output must be separate from all original input trees")
    tools = {str(path): identity(path) for path in tool_paths()}
    canonical = replay.verify_archive(archive)  # No synthetic bypass, registry override or TOFU.
    if canonical["synthetic"]:
        raise ValueError("Synthetic prestart archives are never replacement evidence")
    plan, campaign = canonical["plan"], canonical["campaign_sha256"]
    prefixes, blocks = session_prefixes(plan, cohort)
    before_canonical = inventory(archive)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=f".{output.name}.reconstruct-") as temporary:
        staging = Path(temporary)
        scratch = staging / "checks"
        snapshots = {phase: verify_snapshot(raw, phase, campaign, scratch) for phase in PHASES}
        prestart = snapshots["prestart"]
        backup.verify_directory(archive, prestart["control"])
        if {name: value for name, value in before_canonical.items() if name != "PRESTART_MANIFEST.json"} != prestart["files"]:
            raise ValueError("Canonical archive differs from the actual prestart snapshot")
        for phase, snapshot in snapshots.items():
            if any(snapshot["files"].get(name) != before_canonical[name]
                   for name in ("campaign.json", "campaign.json.sha256")):
                raise ValueError(f"Immutable campaign bytes differ: {phase}")
        final, completion = snapshots["final"], snapshots["completion"]
        journal, bounds = verify_completed_journal(plan, campaign, final["paths"]["directory"], archive)
        profile_context = audit_complete.check_execution_profile(plan, canonical["registered_campaign_identity"],
            archive, final["paths"]["directory"] / "campaign-results", read_json(archive / "PRESTART_MANIFEST.json"),
            audit_complete.Inputs())
        if profile_context is not None:
            attempts = [journal["sessions"][session["session_id"]]["attempts"][0] for session in plan["sessions"]]
            for session, attempt in zip(plan["sessions"], attempts):
                run = final["paths"]["directory"] / mapped(plan, attempt["run_root"])
                audit_complete.check_spark_driver_profile(profile_context, session, run, audit_complete.Inputs())
            audit_complete.check_profile_chronology(profile_context, plan, journal, attempts)
        if final["files"]["campaign-results/journal.json"] != completion["files"].get("campaign-results/journal.json"):
            raise ValueError("Completion journal differs from the post-final journal")
        completed_root = completion["paths"]["directory"]
        ended = (completed_root / "confirm-driver.ended-utc").read_text().strip()
        ready = (completed_root / "ANALYSIS_READY").read_text().strip()
        if ((completed_root / "confirm-driver.exit").read_text().strip() != "0"
                or not (completed_root / "ANALYSIS_READY").read_text().strip()
                or not (utc(journal["completed_at_utc"]) < recorded_upper_bound(ready)
                        and utc(ready) < recorded_upper_bound(ended))):
            raise ValueError("Successful actual driver exit/end and ANALYSIS_READY are required")
        # Completion contains remote ACKs for prestart, all blocks and final.
        # It cannot contain its own later ACK; no local watcher receipt is assumed.
        receipts = {}
        for phase in PHASES[:-1]:
            receipt = backup.verify_receipt(raw / f"{phase}.json", completed_root / "backup-receipts" / f"{phase}.json")
            if utc(receipt["verified_at_utc"]) >= recorded_upper_bound(ended):
                raise ValueError("Backup receipt postdates supervisor completion")
            receipts[phase] = receipt
        receipt = receipts["prestart"]
        if (receipt["prestart_manifest_sha256"] != canonical["prestart_sha256"]
                or utc(receipt["verified_at_utc"]) > bounds[0][0]):
            raise ValueError("Prestart ACK must bind this canonical seal before the first timing")
        git_repository = Path(canonical["registered_campaign_identity"]["registry_path"]).parents[1]
        backup.check_git_commit(archive, git_repository, receipt["git_commit"])
        registry_bytes = subprocess.check_output(["git", "-C", str(git_repository), "show",
            f"{receipt['git_commit']}:research_reporting/campaign_identities.json"], timeout=60)
        committed_registry = json.loads(registry_bytes)
        entry = committed_registry.get("campaigns", {}).get(campaign, {})
        entry_keys = {"label", "prestart_sha256", "source_commit", "source_tree_sha256"}
        if cohort == "gclocker100":
            entry_keys |= {"campaign_id", "execution_profile_sha256"}
        expected_entry = {key: canonical["registered_campaign_identity"][key] for key in entry_keys}
        if (committed_registry.get("schema_version") != "h100-registered-campaign-identities-v1"
                or committed_registry.get("campaigns", {}).get(campaign_identity.ORIGINAL_CAMPAIGN_SHA256, {}).get("prestart_sha256")
                    != campaign_identity.ORIGINAL_PRESTART_SHA256
                or entry != expected_entry):
            raise ValueError("Replacement identity was not registered in the acknowledged prestart commit")
        block_comparisons = {}
        for phase, selected in blocks.items():
            snapshot = snapshots[phase]
            expected = {name: value for name, value in final["files"].items()
                        if any(name.startswith(prefix) for prefix in selected)}
            actual = {name: value for name, value in snapshot["files"].items()
                      if name not in {"campaign.json", "campaign.json.sha256", "campaign-results/journal.json", "BACKUP_MANIFEST.json"}}
            if actual != expected or any(not any(name.startswith(prefix) for name in expected) for prefix in selected):
                raise ValueError(f"Immutable block/raw bytes differ from final: {phase}")
            block_journal = read_json(snapshot["paths"]["directory"] / "campaign-results/journal.json")
            if (block_journal.get("campaign_sha256") != campaign
                    or set(block_journal.get("sessions", {})) != set(journal["sessions"])):
                raise ValueError(f"Block journal belongs to another campaign/sample: {phase}")
            for session in plan["sessions"]:
                item = block_journal["sessions"][session["session_id"]]
                if session["block_id"] <= phase:
                    if item != journal["sessions"][session["session_id"]]:
                        raise ValueError(f"Completed session journal changed after {phase}")
                elif item.get("status") != "planned" or item.get("attempts") != []:
                    raise ValueError(f"Unexpected future session in block journal: {phase}")
            block_comparisons[phase] = {"decision": "all_selected_raw_bytes_identical_to_final", "files": len(actual)}
        harvest, lineage = staging / "harvest", staging / "lineage"
        provenance, decisions = {}, []
        sources = (("prestart", archive, before_canonical),
                   ("final", final["paths"]["directory"], final["files"]),
                   ("completion", completed_root, completion["files"]))
        for phase, root, files in sources:
            for name, value in sorted(files.items()):
                if name in TRANSPORT_NAMES:
                    continue
                previous = provenance.get(name)
                record = {"phase": phase, **value}
                if previous:
                    old = previous["versions"][-1]
                    advance = {key: old[key] for key in ("sha256", "bytes")} != value
                    if advance and (name not in MUTABLE_ROOT_FILES
                                    or name == "EXECUTION_PROFILE_GATES.jsonl" and cohort != "gclocker100"):
                        raise ValueError(f"Immutable overlapping evidence changed: {name} ({old['phase']} -> {phase})")
                    if advance and name in {"confirm-driver.log", "EXECUTION_PROFILE_GATES.jsonl"}:
                        old_root = next(source_root for source_phase, source_root, _ in sources if source_phase == old["phase"])
                        with (old_root / name).open("rb") as old_log, (root / name).open("rb") as new_log:
                            while chunk := old_log.read(8 * 1024 * 1024):
                                if new_log.read(len(chunk)) != chunk:
                                    raise ValueError("Append-only evidence advance must preserve its existing prefix")
                    decisions.append({"path": name, "decision": "permitted_operational_advance" if advance else "identical",
                                      "old": old, "new": record, "reason": MUTABLE_ROOT_FILES.get(name) if advance else None})
                    previous["versions"].append(record)
                    previous["selected_phase"] = phase
                else:
                    provenance[name] = {"selected_phase": phase, "versions": [record]}
        for name, record in sorted(provenance.items()):
            selected = record["versions"][-1]
            root = dict((phase, root) for phase, root, _ in sources)[selected["phase"]]
            copy_exact(root / name, harvest / name, {key: selected[key] for key in ("sha256", "bytes")})
        for phase, snapshot in snapshots.items():
            for key in ("control", "verified_report"):
                source = snapshot["paths"][key]
                copy_exact(source, lineage / phase / source.name, {field: snapshot["inputs"][key][field] for field in ("sha256", "bytes")})
            for name in ("BACKUP_MANIFEST.json", "campaign-results/journal.json"):
                if name in snapshot["files"]:
                    copy_exact(snapshot["paths"]["directory"] / name, lineage / phase / name, snapshot["files"][name])
        copy_exact(archive / "PRESTART_MANIFEST.json", lineage / "canonical-prestart/PRESTART_MANIFEST.json", before_canonical["PRESTART_MANIFEST.json"])
        # Reverify all source bytes after copying. No input mutation can receive
        # a published reconstruction manifest, and failure removes only staging.
        for snapshot in snapshots.values():
            backup.verify_archive(snapshot["paths"]["tar"], snapshot["control"], snapshot["paths"]["directory"])
            if inventory(snapshot["paths"]["directory"]) != snapshot["files"] or any(
                    identity(Path(row["path"])) != {key: row[key] for key in ("sha256", "bytes")}
                    for row in snapshot["inputs"].values()):
                raise ValueError("Backup inputs changed during reconstruction")
        if (inventory(archive) != before_canonical or replay.verify_archive(archive)["campaign_sha256"] != campaign
                or {str(path): identity(path) for path in tool_paths()} != tools):
            raise ValueError("Canonical archive or current verification tools changed")
        manifest = {"schema_version": "h100-replacement-reconstruction-v1", "status": "RECONSTRUCTED_TRANSPORT_VERIFIED",
            "created_at_utc": datetime.now(timezone.utc).isoformat(), "campaign_sha256": campaign,
            "cohort": cohort, "campaign_id": plan["campaign_id"],
            "prestart_sha256": canonical["prestart_sha256"], "canonical_archive": str(archive),
            "raw_backups": str(raw), "harvest": str(output / "harvest"), "sessions": 400,
            "scope": "Offline byte reconstruction only; scientific replay and complete audit are still required.",
            "registered_campaign_identity": canonical["registered_campaign_identity"], "current_tools": tools,
            "canonical_files": before_canonical, "inputs": {phase: snapshot["inputs"] for phase, snapshot in snapshots.items()},
            "prestart_registration_commit": receipt["git_commit"], "committed_registry_sha256": hashlib.sha256(registry_bytes).hexdigest(),
            "block_comparisons": block_comparisons, "operational_advance_allowlist": MUTABLE_ROOT_FILES,
            "overlap_decisions": decisions, "payload_provenance": provenance,
            "harvest_files": inventory(harvest), "lineage_files": inventory(lineage),
            "excluded_payload_manifests": sorted(TRANSPORT_NAMES)}
        if profile_context is not None:
            manifest["execution_profile"] = {"path": audit_complete.PROFILE_NAME, "sha256": profile_context["sha256"],
                "spark_driver_logs": profile_context["driver_logs"], "profile_gates": len(profile_context["gates"]),
                "status": "PASS"}
        backup.exclusive_json(staging / "RECONSTRUCTION_MANIFEST.json", manifest)
        scratch.rmdir()
        # Never replace an existing output, including one created concurrently.
        output.mkdir()
        try:
            for name in ("harvest", "lineage", "RECONSTRUCTION_MANIFEST.json"):
                (staging / name).rename(output / name)
        except BaseException:
            # Keep any partially published output inspectable, without success
            # manifest, if a local filesystem operation itself failed.
            (output / "RECONSTRUCTION_MANIFEST.json").unlink(missing_ok=True)
            raise
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path, help="Exact registered canonical prestart archive")
    parser.add_argument("--raw-backups", required=True, type=Path, help="All verified prestart/block/final/completion backups")
    parser.add_argument("--output", required=True, type=Path, help="New reconstruction directory; never overwritten")
    parser.add_argument("--cohort", choices=tuple(COHORTS), default="gclocker100",
                        help="Fixed recovery cohort (default), or historical v1 transport inspection")
    args = parser.parse_args()
    value = reconstruct(args.archive, args.raw_backups, args.output, args.cohort)
    print(json.dumps({key: value[key] for key in ("status", "campaign_sha256", "sessions", "harvest")}))


if __name__ == "__main__":
    main()
