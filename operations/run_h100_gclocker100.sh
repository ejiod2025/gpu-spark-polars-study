#!/usr/bin/env bash
# Operational wrapper only. Frozen benchmark source and sample remain unchanged.
# Run through setsid/nohup; poll status separately. Never restart on SSH timeout.
set -euo pipefail
RESEARCH_SOURCE="${RESEARCH_SOURCE:-/home/ubuntu/study-h100-research-c84ef6a}"
RESEARCH_EVIDENCE="${RESEARCH_EVIDENCE:-/home/ubuntu/study-h100-research-evidence-20261027-gclocker100}"
RESEARCH_BACKUP_ROOT="${RESEARCH_BACKUP_ROOT:-/home/ubuntu/study-h100-research-staging-20261027-gclocker100/backups}"
RESEARCH_BACKUP_TOOL="${RESEARCH_BACKUP_TOOL:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/research_backup.py}"
RESEARCH_ENV="${RESEARCH_ENV:-/home/ubuntu/study/rerun/h100-env.sh}"
RESEARCH_ANALYSIS_PYTHON="${RESEARCH_ANALYSIS_PYTHON:-/home/ubuntu/study/.venv-research-test/bin/python}"

mkdir -p "$RESEARCH_EVIDENCE"
exec 9> "$RESEARCH_EVIDENCE/gclocker100-supervisor.lock"
flock -n 9 || { echo 'A GCLocker100 recovery supervisor is already running' >&2; exit 1; }
# Restart needs explicit investigation, not an automatic attempt replacement.
test ! -e "$RESEARCH_EVIDENCE/confirm-driver.started-utc"
source "$RESEARCH_ENV"
unset JAVA_TOOL_OPTIONS _JAVA_OPTIONS JDK_JAVA_OPTIONS SPARK_LAUNCHER_OPTS
export SPARK_SUBMIT_OPTS='-XX:+UnlockDiagnosticVMOptions -XX:GCLockerRetryAllocationCount=100 -XX:+PrintFlagsFinal'
RESEARCH_PROFILE_TOOL="${RESEARCH_PROFILE_TOOL:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/jvm_execution_profile.py}"
profile_gate() {
  "$PYTHON_GPU_ENV" -B "$RESEARCH_PROFILE_TOOL" gate \
    --profile "$RESEARCH_EVIDENCE/EXECUTION_PROFILE.json" --source "$RESEARCH_SOURCE" \
    --gates "$RESEARCH_EVIDENCE/EXECUTION_PROFILE_GATES.jsonl" --phase "$1"
}
cd "$RESEARCH_SOURCE"
test "$(cat "$RESEARCH_EVIDENCE/prepare.exit")" = 0
test -e "$RESEARCH_EVIDENCE/READY_TO_ARCHIVE_PLAN"
printf '%s\n' "$$" > "$RESEARCH_EVIDENCE/confirm-driver.pid"
RESEARCH_PHASE=prestart-offhost-backup
finish_research_gclocker100() {
  RESEARCH_EXIT=$?
  printf '%s\n' "$RESEARCH_EXIT" > "$RESEARCH_EVIDENCE/confirm-driver.exit"
  printf '%s\n' "$RESEARCH_PHASE" > "$RESEARCH_EVIDENCE/confirm-driver.last-phase"
  date -u +%FT%TZ > "$RESEARCH_EVIDENCE/confirm-driver.ended-utc"
}
trap finish_research_gclocker100 EXIT
date -u +%FT%TZ > "$RESEARCH_EVIDENCE/confirm-driver.started-utc"

wait_for_backup() {
  local phase="$1"
  printf '%s\n' "$phase" > "$RESEARCH_EVIDENCE/WAITING_FOR_OFFHOST_BACKUP"
  while [ ! -e "$RESEARCH_EVIDENCE/backup-receipts/$phase.json" ]; do sleep 10; done
  "$PYTHON_GPU_ENV" "$RESEARCH_BACKUP_TOOL" verify-receipt \
    --control "$RESEARCH_BACKUP_ROOT/$phase.json" \
    --receipt "$RESEARCH_EVIDENCE/backup-receipts/$phase.json"
  rm "$RESEARCH_EVIDENCE/WAITING_FOR_OFFHOST_BACKUP"
}

# Parent archives and commits the exact verified prestart files before ACK.
if [ ! -e "$RESEARCH_BACKUP_ROOT/prestart.json" ]; then
  "$PYTHON_GPU_ENV" "$RESEARCH_BACKUP_TOOL" snapshot --evidence "$RESEARCH_EVIDENCE" \
    --snapshot-root "$RESEARCH_BACKUP_ROOT" --phase prestart
fi
wait_for_backup prestart

for RESEARCH_BLOCK_NUMBER in $(seq 1 10); do
  printf -v RESEARCH_BLOCK 'block-%02d' "$RESEARCH_BLOCK_NUMBER"
  RESEARCH_PHASE="fixed-sample-$RESEARCH_BLOCK"
  printf '%s\n' "$RESEARCH_PHASE" > "$RESEARCH_EVIDENCE/confirm-driver.phase"
  profile_gate "$RESEARCH_BLOCK-before"
  "$PYTHON_GPU_ENV" -B research_campaign.py --run "$RESEARCH_EVIDENCE/campaign.json" \
    --max-sessions 40 > "$RESEARCH_EVIDENCE/confirm-$RESEARCH_BLOCK.log" 2>&1
  profile_gate "$RESEARCH_BLOCK-after"
  RESEARCH_PHASE="offhost-backup-$RESEARCH_BLOCK"
  "$PYTHON_GPU_ENV" "$RESEARCH_BACKUP_TOOL" snapshot --evidence "$RESEARCH_EVIDENCE" \
    --snapshot-root "$RESEARCH_BACKUP_ROOT" --phase "$RESEARCH_BLOCK"
  wait_for_backup "$RESEARCH_BLOCK"
done

# --max-sessions40 breaks before run_campaign's loop-else, including block10.
# This no-new-session invocation creates the true post-final-session byte gate.
profile_gate final-before
RESEARCH_PHASE=final-input-integrity
printf '%s\n' "$RESEARCH_PHASE" > "$RESEARCH_EVIDENCE/confirm-driver.phase"
"$PYTHON_GPU_ENV" -B research_campaign.py --run "$RESEARCH_EVIDENCE/campaign.json" \
  > "$RESEARCH_EVIDENCE/confirm-final-integrity.log" 2>&1
RESEARCH_PHASE=independent-final-validation
"$PYTHON_GPU_ENV" research_validate.py --campaign "$RESEARCH_EVIDENCE/campaign.json" \
  --oracles "$RESEARCH_EVIDENCE/oracles" --output "$RESEARCH_EVIDENCE/validation.json" \
  > "$RESEARCH_EVIDENCE/final-validation.log" 2>&1
RESEARCH_PHASE=predefined-analysis
"$RESEARCH_ANALYSIS_PYTHON" research_analysis.py --campaign "$RESEARCH_EVIDENCE/campaign.json" \
  --validation "$RESEARCH_EVIDENCE/validation.json" --output "$RESEARCH_EVIDENCE/analysis" \
  > "$RESEARCH_EVIDENCE/analysis.log" 2>&1
profile_gate final-after
date -u +%FT%TZ > "$RESEARCH_EVIDENCE/ANALYSIS_READY"
RESEARCH_PHASE=final-offhost-backup
"$PYTHON_GPU_ENV" "$RESEARCH_BACKUP_TOOL" snapshot --evidence "$RESEARCH_EVIDENCE" \
  --snapshot-root "$RESEARCH_BACKUP_ROOT" --phase final
wait_for_backup final
RESEARCH_PHASE=complete-ready-for-reporting
