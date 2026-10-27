#!/usr/bin/env bash
# One recovery cohort; preparation repeats readiness without installing/generating.
set -euo pipefail
RECOVERY_SOURCE="${RESEARCH_SOURCE:-/home/ubuntu/study-h100-research-c84ef6a}"
RECOVERY_STUDY="${RESEARCH_STUDY:-/home/ubuntu/study}"
RECOVERY_EVIDENCE="${RESEARCH_EVIDENCE:-/home/ubuntu/study-h100-research-evidence-20261027-gclocker100}"
RECOVERY_OPERATIONS="${RESEARCH_OPERATIONS:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
RECOVERY_ENV="${RESEARCH_ENV:-/home/ubuntu/study/rerun/h100-env.sh}"
mkdir -p "$RECOVERY_EVIDENCE"
exec 9> "$RECOVERY_EVIDENCE/gclocker100-prepare.lock"
flock -n 9 || { echo 'Recovery preparation is already running' >&2; exit 1; }
test ! -e "$RECOVERY_EVIDENCE/prepare.started-utc"
test ! -e "$RECOVERY_EVIDENCE/campaign.json"
test ! -e "$RECOVERY_EVIDENCE/campaign-results/journal.json"
test -s "$RECOVERY_EVIDENCE/EXECUTION_PROFILE.json"
printf '%s\n' "$$" > "$RECOVERY_EVIDENCE/prepare.pid"
date -u +%FT%TZ > "$RECOVERY_EVIDENCE/prepare.started-utc"
RECOVERY_PHASE=profile-prerequisites
finish_recovery_preparation() {
  RECOVERY_EXIT=$?
  printf '%s\n' "$RECOVERY_EXIT" > "$RECOVERY_EVIDENCE/prepare.exit"
  printf '%s\n' "$RECOVERY_PHASE" > "$RECOVERY_EVIDENCE/prepare.last-phase"
  date -u +%FT%TZ > "$RECOVERY_EVIDENCE/prepare.ended-utc"
}
trap finish_recovery_preparation EXIT
set_phase() {
  RECOVERY_PHASE="$1"
  printf '%s\n' "$RECOVERY_PHASE" > "$RECOVERY_EVIDENCE/prepare.phase"
  printf 'PHASE %s\n' "$RECOVERY_PHASE"
}
source "$RECOVERY_ENV"
unset JAVA_TOOL_OPTIONS _JAVA_OPTIONS JDK_JAVA_OPTIONS SPARK_LAUNCHER_OPTS
export SPARK_SUBMIT_OPTS='-XX:+UnlockDiagnosticVMOptions -XX:GCLockerRetryAllocationCount=100 -XX:+PrintFlagsFinal'
cd "$RECOVERY_SOURCE"
test "$(git rev-parse HEAD)" = c84ef6a07341f350dae6636ab709c1b65fd894e0
test -z "$(git status --porcelain)"
# Copies original JSON/log/manifests byte-for-byte after a full 1654-file hash
# verification. No source data or old evidence is ever modified.
"$PYTHON_GPU_ENV" -B "$RECOVERY_OPERATIONS/jvm_execution_profile.py" copy-original \
  --profile "$RECOVERY_EVIDENCE/EXECUTION_PROFILE.json" --source "$RECOVERY_SOURCE" \
  --evidence "$RECOVERY_EVIDENCE" > "$RECOVERY_EVIDENCE/verify-original-inputs.log" 2>&1
profile_gate() {
  "$PYTHON_GPU_ENV" -B "$RECOVERY_OPERATIONS/jvm_execution_profile.py" gate \
    --profile "$RECOVERY_EVIDENCE/EXECUTION_PROFILE.json" --source "$RECOVERY_SOURCE" \
    --gates "$RECOVERY_EVIDENCE/EXECUTION_PROFILE_GATES.jsonl" --phase "$1"
}
set_phase readiness
profile_gate readiness-before
"$PYTHON_GPU_ENV" -B research_readiness.py --data-root "$DATA_ROOT" \
  --output "$RECOVERY_EVIDENCE/readiness" --jar "$RAPIDS_JAR" --python "$PYTHON_GPU_ENV" \
  --polars-cpu-200-python "$RECOVERY_STUDY/.venv-research-cpu200/bin/python" \
  > "$RECOVERY_EVIDENCE/readiness-driver.log" 2>&1
profile_gate readiness-after
set_phase freeze-new-campaign
"$PYTHON_GPU_ENV" -B research_campaign.py --plan "$RECOVERY_EVIDENCE/campaign.json" \
  --campaign-id h100-confirmatory-gclocker100-20261027 --seed 20261026 \
  --data-root "$DATA_ROOT" --output-root "$RECOVERY_EVIDENCE/campaign-results" \
  --jar "$RAPIDS_JAR" --python "$PYTHON_GPU_ENV" \
  --polars-cpu-200-python "$RECOVERY_STUDY/.venv-research-cpu200/bin/python" \
  --oracle-root "$RECOVERY_EVIDENCE/oracles" --readiness "$RECOVERY_EVIDENCE/readiness/READINESS.json" \
  > "$RECOVERY_EVIDENCE/plan.log" 2>&1
set_phase ready-for-off-host-prestart-archive
date -u +%FT%TZ > "$RECOVERY_EVIDENCE/READY_TO_ARCHIVE_PLAN"
