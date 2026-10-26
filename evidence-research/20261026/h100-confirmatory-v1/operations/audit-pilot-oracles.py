#!/usr/bin/env python3
"""Post-hoc independent correctness supplement; no pilot timings enter inference."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

repository = Path(sys.argv[1]).resolve()
oracles = Path(sys.argv[2]).resolve()
output = Path(sys.argv[3]).resolve()
sys.path.insert(0, str(repository))
from analyze_results import compare_values
from research_validate import load_oracle

if output.exists():
    raise FileExistsError(output)
evidence = {"schema_version": "pilot-independent-oracle-supplement-v1", "status": "RUNNING",
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "scope": "Post-hoc correctness audit of every saved pilot output; exploratory timings are not pooled into prospective inference",
            "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "sessions": [], "comparisons": [], "oracle_files": {}}
for path in sorted((repository / "evidence-h100" / "20261026" / "results").glob("**/results.json")):
    session = json.loads(path.read_text())
    suite = session["suite"]
    scale = str(int(float(session["scale_factor"])))
    oracle_path = oracles / f"{suite}-sf{scale}.json"
    oracle = load_oracle(oracle_path, suite, scale, session["manifest_sha256"])
    evidence["oracle_files"][oracle_path.name] = hashlib.sha256(oracle_path.read_bytes()).hexdigest()
    label = str(path.relative_to(repository))
    evidence["sessions"].append({"path": label, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                  "suite": suite, "scale": scale, "engine": session["engine"],
                                  "version": session["version"], "query_errors": session.get("query_errors", [])})
    for record in session["records"]:
        check = compare_values(oracle["queries"][record["query"]]["rows"], record["rows"])
        evidence["comparisons"].append({"session": label, "query": record["query"],
                                        "phase": record["phase"], "trial": record["trial"], **check})
evidence["successful_outputs_compared"] = len(evidence["comparisons"])
evidence["sessions_compared"] = len(evidence["sessions"])
evidence["mismatches"] = sum(not row["passed"] for row in evidence["comparisons"])
evidence["status"] = "PASS" if (len(evidence["comparisons"]) == 936 and len(evidence["sessions"]) == 40
                                      and evidence["mismatches"] == 0) else "FAIL"
evidence["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
output.parent.mkdir(parents=True, exist_ok=True)
with output.open("x") as handle:
    json.dump(evidence, handle, indent=2, allow_nan=False)
    handle.write("\n")
print(json.dumps({key: evidence[key] for key in ("status", "sessions_compared", "successful_outputs_compared", "mismatches")}))
raise SystemExit(0 if evidence["status"] == "PASS" else 1)
