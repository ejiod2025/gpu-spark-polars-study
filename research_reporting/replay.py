#!/usr/bin/env python3
"""Replay the frozen H100 artifact, using archived validation and analysis code.

    python research_reporting/replay.py --archive /path/to/prestart-archive \
        --artifacts-root /path/to/campaign-results --output /path/to/new-replay

No GPU, JVM, source Parquet data, SSH or benchmark execution is involved. First
verify every PRESTART_MANIFEST checksum, the exact 52-file C84 source identity,
the immutable plan/protocol and all eight oracle identities. Then execute the
archived validator and analyzer using this Python interpreter, followed by the
current separately versioned reporting layer. Existing output is never replaced;
failed commands, raw validation, logs and a FAIL manifest remain reviewable.

Portability adapter v1 changes only validation.sessions[].plans[].source from a
verified local plan path to its original frozen VM path. Raw validation and all
harvested inputs remain unchanged. This is required because the frozen analyzer
compares each audit exactly with the VM's saved scientific gate. Every adapted
path must match its action's recorded plan path and SHA256. Top-level oracle
paths and validation timestamps are not normalized.

Install dependencies explicitly; replay never changes its environment:
    python -m pip install -r ARCHIVE/campaign.json.source/requirements-research-analysis.txt
    python -m pip install matplotlib==3.10.8
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import time

try:
    from .campaign_identity import registered_identity
except ImportError:  # Direct script execution.
    from campaign_identity import registered_identity

HERE = Path(__file__).resolve().parent
SOURCE_COMMIT = "c84ef6a07341f350dae6636ab709c1b65fd894e0"
SOURCE_TREE_SHA256 = "9514f0f7ce384763d0a675a9075666707e0edabcc003c729a3e0735a52ea25d8"
REAL_CAMPAIGN_SHA256 = "69669dfbc74ffb2a0c378c319c7b886043ffccb8f301784fb2b73b9ab32581e0"
REAL_PRESTART_SHA256 = "f31bca1eeaf8d203fab606ed5e43a1edcdcb23ff5a46de335af1761b3ea36783"
ADAPTER_VERSION = "frozen-plan-source-path-v1"
CELLS = tuple(f"baseline-sf{s}" for s in (1, 10, 30, 100, 300)) + tuple(f"complex-sf{s}" for s in (30, 100, 300))


class ReplayError(RuntimeError):
    """Replay failed; its output directory retains the evidence and failure."""


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(canonical_bytes(value))
    os.replace(temporary, path)


def read_json(path):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError(f"Duplicate JSON key {key}: {path}")
            value[key] = item
        return value

    def nonfinite(value):
        raise ValueError(f"Nonfinite JSON constant {value}: {path}")

    return json.loads(Path(path).read_text(), object_pairs_hook=pairs, parse_constant=nonfinite)


def safe_file(root, relative):
    """Require a contained ordinary file; archives and harvested inputs stay inert."""
    root = Path(root)
    relative = PurePosixPath(str(relative))
    if relative.is_absolute() or any(part in ("..", "") for part in relative.parts) or "\\" in str(relative):
        raise ValueError(f"Unsafe relative artifact path: {relative}")
    path = root.joinpath(*relative.parts)
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError(f"Symlink artifact is not permitted: {path}")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Missing or escaping artifact: {path}")
    return path


def inventory(root):
    root = Path(root)
    observed = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symlink artifact is not permitted: {path}")
        if path.is_file():
            observed[str(path.relative_to(root))] = sha256(path)
    return observed


def verify_archive(archive, allow_synthetic=False):
    archive = Path(archive).resolve()
    manifest_path = safe_file(archive, "PRESTART_MANIFEST.json")
    manifest = read_json(manifest_path)
    campaign_path = safe_file(archive, "campaign.json")
    plan = read_json(campaign_path)
    synthetic = manifest.get("synthetic") is True or plan.get("synthetic") is True
    identity = None
    if synthetic:
        if (not allow_synthetic or manifest.get("synthetic") is not True or plan.get("synthetic") is not True
                or "SYNTHETIC" not in plan.get("campaign_id", "")):
            raise ValueError("Synthetic replay requires explicit --allow-synthetic and matching SYNTHETIC labels")
    else:
        identity = registered_identity(sha256(campaign_path), sha256(manifest_path))
    if (manifest.get("schema_version") != "h100-confirmatory-prestart-archive-v1"
            or manifest.get("status") != "PASS" or manifest.get("phase") != "before-first-confirmatory-session"
            or manifest.get("source_commit") != SOURCE_COMMIT or manifest.get("source_tree_sha256") != SOURCE_TREE_SHA256
            or manifest.get("planned_sessions") != 400 or manifest.get("planned_blocks") != 10
            or manifest.get("primary_contrasts") != 26):
        raise ValueError("Prestart identity/design differs from the frozen C84 campaign")
    expected = manifest.get("files_sha256", {})
    if not expected or "PRESTART_MANIFEST.json" in expected:
        raise ValueError("Invalid prestart checksum inventory")
    for relative, digest in expected.items():
        if sha256(safe_file(archive, relative)) != digest:
            raise ValueError(f"Prestart checksum mismatch: {relative}")
    actual = inventory(archive)
    if set(actual) != set(expected) | {"PRESTART_MANIFEST.json"}:
        raise ValueError("Archive contains missing/extra files outside its prestart inventory")
    campaign_digest = sha256(campaign_path)
    if (campaign_digest != manifest.get("campaign_sha256")
            or safe_file(archive, "campaign.json.sha256").read_text().strip() != campaign_digest):
        raise ValueError("Campaign checksum/sidecar differs")
    source = plan["source"]
    files = source.get("files_sha256", {})
    tree = hashlib.sha256(canonical_bytes(files)).hexdigest()
    if (len(files) != 52 or tree != SOURCE_TREE_SHA256 or source.get("source_tree_sha256") != tree
            or source.get("git_head") != SOURCE_COMMIT or source.get("git_status")):
        raise ValueError("The complete clean 52-file C84 source identity is required")
    snapshot = archive / "campaign.json.source"
    if inventory(snapshot) != files:
        raise ValueError("Snapshot differs from the complete 52-file frozen source map")
    for relative, digest in files.items():
        if expected.get("campaign.json.source/" + relative) != digest:
            raise ValueError(f"Source identity differs from prestart manifest: {relative}")
    protocol = "docs/H100_CONFIRMATORY_PROTOCOL.md"
    if (plan.get("protocol_sha256") != files[protocol]
            or sha256(safe_file(snapshot, protocol)) != plan["protocol_sha256"]):
        raise ValueError("Frozen protocol identity differs")
    if set(plan.get("oracles", {})) != set(CELLS):
        raise ValueError("All eight frozen oracle identities are required")
    for cell, oracle in plan["oracles"].items():
        path = safe_file(archive, f"oracles/{cell}.json")
        if sha256(path) != oracle["sha256"] or expected.get(f"oracles/{cell}.json") != oracle["sha256"]:
            raise ValueError(f"Frozen oracle checksum differs: {cell}")
    return {"plan": plan, "synthetic": synthetic, "archive": archive, "snapshot": snapshot,
            "campaign_sha256": campaign_digest, "prestart_sha256": sha256(manifest_path), "source_files_sha256": files,
            "archive_files_sha256": actual, "registered_campaign_identity": identity}


def artifacts_directory(path):
    root = Path(path).resolve()
    candidates = [candidate for candidate in (root, root / "campaign-results") if (candidate / "journal.json").is_file()]
    if len(candidates) != 1:
        raise ValueError("--artifacts-root must identify exactly one campaign-results directory containing journal.json")
    return candidates[0]


def mapped_file(plan, root, frozen_path):
    try:
        relative = Path(frozen_path).relative_to(Path(plan["output_root"]))
    except ValueError as exc:
        raise ValueError(f"Path lies outside frozen campaign output_root: {frozen_path}") from exc
    return safe_file(root, relative.as_posix())


def check_harvest_references(plan, root):
    """Confine frozen-validator file access before running its older path mapper."""
    journal = read_json(root / "journal.json")
    for session in plan["sessions"]:
        item = journal.get("sessions", {}).get(session["session_id"], {})
        for attempt in item.get("attempts", []):
            frozen_run = Path(attempt["run_root"])
            expected_result = frozen_run / f"sf{session['scale']}" / session["engine"] / "results.json"
            if attempt.get("artifacts", {}).get("results_path") != str(expected_result):
                raise ValueError("Attempt results path differs from its planned run location")
            mapped_file(plan, root, expected_result)
            mapped_file(plan, root, frozen_run / "schedule.json")
            mapped_file(plan, root, frozen_run.parent / "session.json")
        gate = item.get("scientific_validation", {})
        if gate.get("path"):
            mapped_file(plan, root, gate["path"])


def adapt_validation(raw_path, canonical_path, plan, root, changes_path):
    """Canonicalize only per-plan source paths; all scientific fields are retained."""
    raw = read_json(raw_path)
    if raw.get("status") != "PASS":
        raise ValueError("Frozen validation did not PASS")
    canonical = copy.deepcopy(raw)
    journal = read_json(root / "journal.json")
    planned = {session["session_id"]: session for session in plan["sessions"]}
    changes = []
    for session_index, audited in enumerate(canonical["sessions"]):
        session = planned[audited["session_id"]]
        attempts = journal["sessions"][session["session_id"]]["attempts"]
        if len(attempts) != 1:
            raise ValueError("Retried sessions require a separately reviewed protocol-deviation analysis")
        attempt = attempts[0]
        frozen_result = Path(attempt["artifacts"]["results_path"])
        value = read_json(mapped_file(plan, root, frozen_result))
        actions = {(record["query"], record["phase"], record["trial"]): record for record in value["records"]}
        for plan_index, entry in enumerate(audited.get("plans", [])):
            if "source" not in entry:
                continue
            if session["engine"] != "spark_gpu":
                raise ValueError("Unexpected source-path field outside frozen Spark GPU inspector")
            action = actions[(entry["query"], entry["phase"], entry["trial"])]
            relative = PurePosixPath(action["plan"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Unsafe action plan path")
            frozen_plan = frozen_result.parent.joinpath(*relative.parts)
            actual = mapped_file(plan, root, frozen_plan)
            observed = Path(entry["source"])
            if not observed.is_absolute() or observed != actual or sha256(actual) != entry["sha256"]:
                raise ValueError("Audited plan source path/SHA does not match its exact recorded action")
            entry["source"] = str(frozen_plan)
            if str(observed) != entry["source"]:
                changes.append({"json_pointer": f"/sessions/{session_index}/plans/{plan_index}/source",
                                "session_id": session["session_id"], "from": str(observed),
                                "to": entry["source"], "plan_sha256": entry["sha256"]})
    details = {"schema_version": "research-replay-path-adapter-v1", "adapter_version": ADAPTER_VERSION,
               "allowed_field": "sessions[].plans[].source", "source_prefix": str(root),
               "canonical_prefix": plan["output_root"], "raw_validation_sha256": sha256(raw_path),
               "adapter_code_sha256": sha256(Path(__file__)), "changes": changes}
    write_json(changes_path, details)
    canonical["portability_adapter"] = {key: details[key] for key in (
        "adapter_version", "allowed_field", "source_prefix", "canonical_prefix", "raw_validation_sha256", "adapter_code_sha256")}
    canonical["portability_adapter"].update(changes_file=changes_path.name, changes_sha256=sha256(changes_path),
                                              changed_source_paths=len(changes))
    write_json(canonical_path, canonical)
    return canonical["portability_adapter"]


def execute(command, cwd, output, name, manifest, timeout):
    record = {"name": name, "command": list(map(str, command)), "cwd": str(cwd),
              "started_at_utc": utcnow(), "log": f"{name}.log", "status": "running"}
    manifest["commands"].append(record)
    write_json(output / "REPLAY_MANIFEST.json", manifest)
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    started = time.monotonic()
    try:
        with (output / record["log"]).open("w") as handle:
            result = subprocess.run(command, cwd=cwd, env=environment, stdout=handle, stderr=subprocess.STDOUT,
                                    timeout=timeout, check=False)
        record["returncode"] = result.returncode
        record["status"] = "PASS" if result.returncode == 0 else "FAIL"
        if result.returncode:
            raise ReplayError(f"{name} exited {result.returncode}; see {output / record['log']}")
    except BaseException:
        record["status"] = "FAIL"
        raise
    finally:
        record.update(ended_at_utc=utcnow(), wall_seconds=time.monotonic() - started)
        write_json(output / "REPLAY_MANIFEST.json", manifest)


def replay(archive, artifacts_root, output, allow_synthetic=False, timeout_seconds=1800):
    archive, artifacts_root, output = Path(archive).resolve(), Path(artifacts_root).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Refusing to overwrite a replay output directory")
    if output.is_relative_to(archive) or output.is_relative_to(artifacts_root):
        raise ValueError("Replay output must be separate from both immutable input directories")
    if timeout_seconds <= 0:
        raise ValueError("Timeout must be positive")
    output.mkdir(parents=True)
    manifest = {"schema_version": "research-artifact-replay-v1", "status": "RUNNING", "phase": "archive-verification",
                "started_at_utc": utcnow(), "archive": str(archive), "artifacts_root_argument": str(artifacts_root),
                "commands": [], "replay_code_sha256": sha256(Path(__file__)), "renderer_sha256": sha256(HERE / "render.py"),
                "python": platform.python_version(), "interpreter": sys.executable, "platform": platform.platform()}
    write_json(output / "REPLAY_MANIFEST.json", manifest)
    try:
        checked = verify_archive(archive, allow_synthetic)
        manifest.update({key: checked[key] for key in ("synthetic", "campaign_sha256", "prestart_sha256", "source_files_sha256", "archive_files_sha256")})
        manifest["registered_campaign_identity"] = checked["registered_campaign_identity"]
        manifest["phase"] = "harvest-identification"
        root = artifacts_directory(artifacts_root)
        plan, snapshot = checked["plan"], checked["snapshot"]
        manifest.update(source_commit=SOURCE_COMMIT, source_tree_sha256=SOURCE_TREE_SHA256,
                        artifacts_root=str(root), campaign_id=plan["campaign_id"],
                        requirements={"archived_analysis": str(snapshot / "requirements-research-analysis.txt"),
                                      "rendering": str(HERE / "requirements-local.txt")})
        manifest["packages"] = {name: importlib.metadata.version(name) for name in ("numpy", "scipy", "psutil", "matplotlib")}
        manifest["artifact_files_sha256"] = inventory(root)
        manifest["phase"] = "harvest-path-containment"
        check_harvest_references(plan, root)
        manifest["phase"] = "frozen-validation"
        execute([sys.executable, "-B", str(snapshot / "research_validate.py"), "--campaign", str(archive / "campaign.json"),
                 "--oracles", str(archive / "oracles"), "--artifacts-root", str(root),
                 "--output", str(output / "validation.raw.json")], snapshot, output, "validation", manifest, timeout_seconds)
        manifest["phase"] = "path-only-portability-adapter"
        manifest["portability_adapter"] = adapt_validation(output / "validation.raw.json", output / "validation.json",
                                                           plan, root, output / "PATH_NORMALIZATION.json")
        manifest["phase"] = "frozen-analysis"
        execute([sys.executable, "-B", str(snapshot / "research_analysis.py"), "--campaign", str(archive / "campaign.json"),
                 "--validation", str(output / "validation.json"), "--artifacts-root", str(root),
                 "--output", str(output / "analysis")], snapshot, output, "analysis", manifest, timeout_seconds)
        manifest["phase"] = "complete-evidence-audit"
        auditor = HERE / "audit_complete.py"
        manifest["auditor_source_sha256"] = sha256(auditor)
        audit_command = [sys.executable, "-B", str(auditor), "--campaign", str(archive / "campaign.json"),
                         "--artifacts-root", str(root), "--evidence-root", str(archive),
                         "--validation", str(output / "validation.json"), "--analysis", str(output / "analysis" / "analysis.json"),
                         "--output", str(output / "complete-audit")]
        if checked["synthetic"]:
            audit_command.append("--allow-synthetic")
        execute(audit_command, HERE, output, "complete_audit", manifest, timeout_seconds)
        audit_path = output / "complete-audit" / "AUDIT_COMPLETE.json"
        audited = read_json(audit_path)
        if (audited.get("status") != "PASS" or audited.get("campaign_sha256") != checked["campaign_sha256"]
                or audited.get("journal_sha256") != sha256(root / "journal.json")
                or audited.get("source_sha256") != manifest["auditor_source_sha256"]
                or audited.get("counts", {}).get("sessions") != 400
                or audited.get("counts", {}).get("successful_outputs") != 9360):
            raise ReplayError("Complete evidence audit is not a matching complete PASS; figures were not rendered")
        manifest["complete_audit"] = {"path": str(audit_path.relative_to(output)), "sha256": sha256(audit_path), "status": "PASS"}
        manifest["phase"] = "reporting"
        render_input = output / "analysis"
        if checked["synthetic"]:
            # Preserve the exact frozen analyzer output; tag only a separate rendering input.
            render_input = output / "SYNTHETIC-render-input"
            render_input.mkdir()
            tagged = read_json(output / "analysis" / "analysis.json")
            tagged["synthetic"] = True
            write_json(render_input / "analysis.json", tagged)
            shutil.copyfile(output / "analysis" / "block_ratios.csv", render_input / "block_ratios.csv")
            manifest["synthetic_render_adapter"] = {"added_field": "synthetic=true", "original_analysis_sha256":
                sha256(output / "analysis" / "analysis.json"), "tagged_analysis_sha256": sha256(render_input / "analysis.json")}
        command = [sys.executable, "-B", str(HERE / "render.py"), "--analysis", str(render_input), "--output", str(output / "figures")]
        if checked["synthetic"]:
            command.append("--allow-synthetic")
        execute(command, HERE, output, "rendering", manifest, timeout_seconds)
        rendered_path = output / "figures" / "RENDER_MANIFEST.json"
        rendered = read_json(rendered_path)
        if (rendered.get("campaign_sha256") != checked["campaign_sha256"]
                or rendered.get("renderer_sha256") != manifest["renderer_sha256"]
                or rendered.get("synthetic") != checked["synthetic"]
                or rendered.get("input_files_sha256", {}).get("analysis.json") != sha256(render_input / "analysis.json")):
            raise ReplayError("Rendered figure provenance differs from replay inputs/source")
        manifest["render_manifest"] = {"path": str(rendered_path.relative_to(output)), "sha256": sha256(rendered_path)}
        if inventory(root) != manifest["artifact_files_sha256"] or inventory(archive) != checked["archive_files_sha256"]:
            raise ValueError("Immutable archive or harvested inputs changed during replay")
        if (checked["registered_campaign_identity"] is not None and
                registered_identity(checked["campaign_sha256"], checked["prestart_sha256"]) != checked["registered_campaign_identity"]):
            raise ValueError("Committed campaign identity registry changed during replay")
        manifest.update(status="PASS", phase="complete")
    except BaseException as exc:
        manifest.update(status="FAIL", error={"type": type(exc).__name__, "message": str(exc)})
        write_json(output / "FAILURE.json", {"phase": manifest["phase"], **manifest["error"]})
        raise ReplayError(f"Replay failed during {manifest['phase']}: {exc}. Evidence retained at {output}") from exc
    finally:
        manifest["ended_at_utc"] = utcnow()
        manifest["output_files_sha256"] = {str(path.relative_to(output)): sha256(path) for path in sorted(output.rglob("*"))
                                            if path.is_file() and path.name != "REPLAY_MANIFEST.json"}
        write_json(output / "REPLAY_MANIFEST.json", manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--artifacts-root", type=Path, required=True, help="harvested campaign-results directory, or its parent")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-synthetic", action="store_true", help="only for explicitly labeled, watermarked test fixtures")
    parser.add_argument("--timeout-seconds", type=int, default=1800, help="maximum seconds for each local subprocess")
    args = parser.parse_args(argv)
    try:
        manifest = replay(args.archive, args.artifacts_root, args.output, args.allow_synthetic, args.timeout_seconds)
    except (OSError, ValueError, ReplayError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps({key: manifest[key] for key in ("status", "campaign_id", "synthetic", "phase")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
