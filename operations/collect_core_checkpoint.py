#!/usr/bin/env python3
"""Copy only immutable completed-block metadata/plans; never read source Parquet.

This is a partial evidence checkpoint, not a complete-study bundle or analysis.
Raw event/telemetry/log collection remains required after the campaign ends.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tarfile

CAMPAIGN = "69669dfbc74ffb2a0c378c319c7b886043ffccb8f301784fb2b73b9ab32581e0"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--block", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.evidence.resolve(strict=True)
    output = args.output.absolute()
    if output.exists() or output.resolve().is_relative_to(root):
        parser.error("output must be new and outside the original evidence tree")
    plan_bytes = (root / "campaign.json").read_bytes()
    if hashlib.sha256(plan_bytes).hexdigest() != CAMPAIGN:
        parser.error("not the frozen H100 v1 campaign")
    plan = json.loads(plan_bytes)
    journal_bytes = (root / "campaign-results/journal.json").read_bytes()
    journal = json.loads(journal_bytes)
    if journal.get("campaign_sha256") != CAMPAIGN:
        parser.error("journal campaign differs")
    selected = [session for session in plan["sessions"] if session["block_id"] == args.block]
    if len(selected) != 40:
        parser.error("block must identify exactly 40 planned sessions")
    paths = {root / "campaign.json", root / "campaign.json.sha256"}
    identities = {}
    for session in selected:
        item = journal["sessions"][session["session_id"]]
        if (item["status"] != "succeeded" or len(item["attempts"]) != 1
                or item.get("scientific_validation", {}).get("status") != "PASS"):
            parser.error(f"block is incomplete: {session['session_id']}")
        attempt = item["attempts"][0]
        run = Path(attempt["run_root"])
        result = Path(attempt["artifacts"]["results_path"])
        gate = Path(item["scientific_validation"]["path"])
        value = json.loads(result.read_text())
        paths.update((run.parent / "session.json", gate, run / "environment.json", run / "schedule.json",
                      run / f"sf{session['scale']}" / "manifest.json", result))
        paths.update(result.parent / record["plan"] for record in value["records"])
        identities[session["session_id"]] = {
            "results_sha256": attempt["artifacts"]["results_sha256"],
            "schedule_sha256": attempt["artifacts"]["schedule_sha256"],
            "scientific_gate_sha256": item["scientific_validation"]["sha256"]}
    sizes = {}
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            parser.error(f"unsafe or absent core evidence path: {path}")
        sizes[str(path.relative_to(root))] = path.stat().st_size
    index = {"schema_version": "h100-completed-block-core-checkpoint-v1",
             "status": "PARTIAL_CAMPAIGN_CORE_CHECKPOINT", "campaign_sha256": CAMPAIGN,
             "block_id": args.block, "checkpoint_sessions": 40, "planned_sessions": 400,
             "created_at_utc": datetime.now(timezone.utc).isoformat(),
             "collector_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             "scope": "Completed block core metadata/plans only; no inferential analysis or final completion claim. Raw events/logs/telemetry and post-run gates remain required.",
             "journal_snapshot_sha256": hashlib.sha256(journal_bytes).hexdigest(),
             "files_bytes": sizes, "session_identities": identities}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "x") as archive:
        for path in sorted(paths):
            archive.add(path, arcname=str(path.relative_to(root)), recursive=False)
        for name, body in (("CHECKPOINT.json", (json.dumps(index, indent=2) + "\n").encode()),
                           ("JOURNAL_SNAPSHOT.json", journal_bytes)):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    print(json.dumps({"block": args.block, "sessions": 40, "core_files": len(paths),
                      "uncompressed_core_bytes": sum(sizes.values()), "output": str(output)}))


if __name__ == "__main__":
    main()
