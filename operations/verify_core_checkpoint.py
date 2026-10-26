#!/usr/bin/env python3
"""Verify a copied completed block with the frozen per-session oracle gate.

This checks a partial core backup only. It does not run statistical analysis,
replace final post-run hashing, or certify the complete 400-session study.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    with path.open("x") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help="immutable pre-start source/oracle archive")
    parser.add_argument("--checkpoint-tar", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.absolute()
    if output.exists():
        parser.error("refusing to overwrite a core-checkpoint directory")
    output.mkdir(parents=True)
    with tarfile.open(args.checkpoint_tar, "r:") as archive:
        names = [member.name for member in archive.getmembers()]
        if len(names) != len(set(names)):
            raise ValueError("duplicate checkpoint tar paths")
        index = json.load(archive.extractfile("CHECKPOINT.json"))
        expected = set(index["files_bytes"]) | {"CHECKPOINT.json", "JOURNAL_SNAPSHOT.json"}
        if set(names) != expected or index.get("status") != "PARTIAL_CAMPAIGN_CORE_CHECKPOINT":
            raise ValueError("checkpoint inventory/scope differs")
        for member in archive.getmembers():
            relative = PurePosixPath(member.name)
            if not member.isfile() or relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"unsafe checkpoint member: {member.name}")
            path = output.joinpath(*relative.parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as handle:
                source = archive.extractfile(member)
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    handle.write(chunk)
            if member.name in index["files_bytes"] and path.stat().st_size != index["files_bytes"][member.name]:
                raise ValueError(f"checkpoint size differs: {member.name}")
    plan = json.loads((output / "campaign.json").read_text())
    digest = sha(output / "campaign.json")
    if digest != index["campaign_sha256"] or digest != (args.archive / "campaign.json.sha256").read_text().strip():
        raise ValueError("checkpoint campaign differs from immutable archive")
    journal = json.loads((output / "JOURNAL_SNAPSHOT.json").read_text())
    if sha(output / "JOURNAL_SNAPSHOT.json") != index["journal_snapshot_sha256"]:
        raise ValueError("journal snapshot differs")
    snapshot = (args.archive / "campaign.json.source").resolve()
    for relative, expected_hash in plan["source"]["files_sha256"].items():
        if sha(snapshot / relative) != expected_hash:
            raise ValueError(f"frozen helper source differs: {relative}")
    plan["_sha256"] = digest
    selected = [s for s in plan["sessions"] if s["block_id"] == index["block_id"]]
    if len(selected) != index["checkpoint_sessions"] or len(selected) != 40:
        raise ValueError("not a complete 40-session block")
    old_path, old_bytecode = list(sys.path), sys.dont_write_bytecode
    raw_audits, canonical_audits, changes, oracles = [], [], [], {}
    sys.path.insert(0, str(snapshot))
    sys.dont_write_bytecode = True
    try:
        frozen = importlib.import_module("research_validate")
        if Path(frozen.__file__).resolve() != snapshot / "research_validate.py":
            raise ValueError("frozen validator import was shadowed")
        for session in selected:
            item = journal["sessions"][session["session_id"]]
            key = f"{session['suite']}-sf{session['scale']}"
            if key not in oracles:
                oracle_path = args.archive / "oracles" / (key + ".json")
                if sha(oracle_path) != plan["oracles"][key]["sha256"]:
                    raise ValueError("frozen oracle changed")
                oracles[key] = frozen.load_oracle(oracle_path, session["suite"], session["scale"],
                                                  plan["datasets"][session["scale"]]["manifest_sha256"])
            audited = frozen.audit_session(plan, session, item, oracles[key], output / "campaign-results")
            if audited["status"] != "PASS":
                raise ValueError(f"Copied block session failed: {session['session_id']}: {audited['errors']}")
            raw_audits.append(copy.deepcopy(audited))
            attempt = item["attempts"][0]
            original_result = Path(attempt["artifacts"]["results_path"])
            local_result = output / "campaign-results" / original_result.relative_to(plan["output_root"])
            value = json.loads(local_result.read_text())
            actions = {(r["query"], r["phase"], r["trial"]): r for r in value["records"]}
            for entry in audited["plans"]:
                if "source" not in entry:
                    continue
                action = actions[(entry["query"], entry["phase"], entry["trial"])]
                original = original_result.parent / action["plan"]
                local = output / "campaign-results" / original.relative_to(plan["output_root"])
                if session["engine"] != "spark_gpu" or str(local) != entry["source"] or sha(local) != entry["sha256"]:
                    raise ValueError("unexpected inspected-plan source/SHA")
                changes.append({"session_id": session["session_id"], "from": entry["source"], "to": str(original)})
                entry["source"] = str(original)
            gate = item["scientific_validation"]
            local_gate = output / "campaign-results" / Path(gate["path"]).relative_to(plan["output_root"])
            if sha(local_gate) != gate["sha256"] or audited != json.loads(local_gate.read_text()):
                raise ValueError(f"Copied gate differs: {session['session_id']}")
            canonical_audits.append(audited)
    finally:
        sys.path[:] = old_path
        sys.dont_write_bytecode = old_bytecode
    count = sum(len(a["comparisons"]) for a in canonical_audits)
    if count != 936 or len(changes) != 192:
        raise ValueError("block action/plan-source counts differ from the fixed design")
    write(output / "CORE_AUDITS_RAW.json", {"scope": "one completed block only", "sessions": raw_audits})
    write(output / "CORE_AUDITS_CANONICAL.json", {"scope": "one completed block only", "sessions": canonical_audits})
    write(output / "PLAN_SOURCE_MAPPING.json", {"rule": "frozen-plan-source-path-v1", "changes": changes})
    files = {str(path.relative_to(output)): sha(path) for path in sorted(output.rglob("*")) if path.is_file()}
    report = {"schema_version": "h100-block-core-copy-verification-v1", "status": "CORE_CHECKPOINT_VERIFIED",
              "scope": "One completed block core backup; campaign incomplete. No inferential estimate or final input-integrity/metadata audit claim.",
              "created_at_utc": datetime.now(timezone.utc).isoformat(), "campaign_sha256": digest,
              "source_commit": plan["source"]["git_head"], "source_tree_sha256": plan["source"]["source_tree_sha256"],
              "verifier_source_sha256": sha(__file__), "checkpoint_tar_sha256": sha(args.checkpoint_tar),
              "block_id": index["block_id"], "sessions_checked": 40, "successful_outputs_compared": count,
              "path_only_adaptations": len(changes), "planned_campaign_sessions": 400, "files_sha256": files}
    write(output / "CORE_VERIFICATION.json", report)
    print(json.dumps({key: report[key] for key in ("status", "block_id", "sessions_checked", "successful_outputs_compared", "path_only_adaptations")}))


if __name__ == "__main__":
    main()
