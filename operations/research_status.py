#!/usr/bin/env python3
"""Read-only operational snapshot; never estimates performance from partial blocks."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("evidence", type=Path)
args = parser.parse_args()
payload = (args.evidence / "campaign-results" / "journal.json").read_bytes()
journal = json.loads(payload)
counts = dict(Counter(item["status"] for item in journal["sessions"].values()))
active = []
validated = 0
for session_id, item in journal["sessions"].items():
    if item.get("scientific_validation", {}).get("status") == "PASS":
        validated += 1
    if item["status"] != "running":
        continue
    attempt = item["attempts"][-1]
    record = {"session_id": session_id, "runner_pid": attempt.get("pid"),
              "started_at_utc": attempt.get("started_at_utc")}
    matches = list(Path(attempt["run_root"]).glob("sf*/*/results.json"))
    if matches:
        worker = json.loads(matches[0].read_text())
        record.update(worker_pid=worker.get("pid"), active_query=worker.get("active_query"),
                      successful_actions=len(worker.get("records", [])), query_errors=worker.get("query_errors", []))
    active.append(record)
out = {"observed_at_utc": datetime.now(timezone.utc).isoformat(),
       "scope": "Operational progress snapshot; no confirmatory performance inference before all ten blocks",
       "campaign_sha256": journal["campaign_sha256"], "journal_snapshot_sha256": hashlib.sha256(payload).hexdigest(),
       "planned_sessions": 400, "status_counts": counts, "scientifically_validated_sessions": validated,
       "input_integrity_gates": journal.get("input_integrity_gates", []), "active_sessions": active,
       "campaign_completed_at_utc": journal.get("completed_at_utc"),
       "driver_pid": (args.evidence / "confirm-driver.pid").read_text().strip(),
       "driver_exit": (args.evidence / "confirm-driver.exit").read_text().strip()
                      if (args.evidence / "confirm-driver.exit").exists() else None}
print(json.dumps(out, indent=2, allow_nan=False))
