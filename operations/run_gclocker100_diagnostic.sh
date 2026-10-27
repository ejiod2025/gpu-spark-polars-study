#!/usr/bin/env bash
# One separately retained feasibility diagnostic; never part of primary inference.
set -euo pipefail
DIAGNOSTIC_ROOT=/home/ubuntu/study-h100-research-diagnostics-20261027-gclocker100
mkdir -p "$DIAGNOSTIC_ROOT"
test ! -e "$DIAGNOSTIC_ROOT/diagnostic.started-utc"
printf '%s\n' "$$" > "$DIAGNOSTIC_ROOT/diagnostic.pid"
date -u +%FT%TZ > "$DIAGNOSTIC_ROOT/diagnostic.started-utc"
finish_diagnostic() {
  DIAGNOSTIC_EXIT=$?
  printf '%s\n' "$DIAGNOSTIC_EXIT" > "$DIAGNOSTIC_ROOT/diagnostic.exit"
  date -u +%FT%TZ > "$DIAGNOSTIC_ROOT/diagnostic.ended-utc"
}
trap finish_diagnostic EXIT
source /home/ubuntu/study/rerun/h100-env.sh
unset JAVA_TOOL_OPTIONS _JAVA_OPTIONS JDK_JAVA_OPTIONS SPARK_LAUNCHER_OPTS
export SPARK_SUBMIT_OPTS='-XX:+UnlockDiagnosticVMOptions -XX:GCLockerRetryAllocationCount=100 -XX:+PrintFlagsFinal'
cd /home/ubuntu/study-h100-research-c84ef6a
test "$(git rev-parse HEAD)" = c84ef6a07341f350dae6636ab709c1b65fd894e0
"$PYTHON_GPU_ENV" -B - <<'PY' > "$DIAGNOSTIC_ROOT/eligibility.json"
import json
from research_campaign import observe_exclusive_host
value = observe_exclusive_host()
print(json.dumps(value, indent=2))
assert value['eligible'], 'An unowned engine/GPU client is present'
PY
"$PYTHON_GPU_ENV" -B run_experiment.py --data-root "$DATA_ROOT" \
  --output "$DIAGNOSTIC_ROOT/run" --engines spark_cpu --scales 100 \
  --suite complex --runs 1 --query-seed 838471203 --cpus 26 \
  --driver-memory 96g --jar "$RAPIDS_JAR" --query-cleanup verified_gc \
  --cleanup-timeout-seconds 60 --engine-timeout-seconds 7200 --cache-observation \
  > "$DIAGNOSTIC_ROOT/run-driver.log" 2>&1
