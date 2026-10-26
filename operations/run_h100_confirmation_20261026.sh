#!/usr/bin/env bash
# Fixed full sample. The one-session pause is an operational launch gate only.
set -euo pipefail
RESEARCH_SOURCE=/home/ubuntu/study-h100-research-c84ef6a
RESEARCH_EVIDENCE=/home/ubuntu/study-h100-research-evidence-20261026
printf '%s\n' "$$" > "$RESEARCH_EVIDENCE/confirm-driver.pid"
RESEARCH_PHASE=first-session-launch-gate
finish_research_confirmation() {
  RESEARCH_EXIT=$?
  printf '%s\n' "$RESEARCH_EXIT" > "$RESEARCH_EVIDENCE/confirm-driver.exit"
  printf '%s\n' "$RESEARCH_PHASE" > "$RESEARCH_EVIDENCE/confirm-driver.last-phase"
  date -u +%FT%TZ > "$RESEARCH_EVIDENCE/confirm-driver.ended-utc"
}
trap finish_research_confirmation EXIT
date -u +%FT%TZ > "$RESEARCH_EVIDENCE/confirm-driver.started-utc"
source /home/ubuntu/study/rerun/h100-env.sh
cd "$RESEARCH_SOURCE"
test "$(cat "$RESEARCH_EVIDENCE/prepare.exit")" = 0
test -e "$RESEARCH_EVIDENCE/READY_TO_ARCHIVE_PLAN"
"$PYTHON_GPU_ENV" research_campaign.py --run "$RESEARCH_EVIDENCE/campaign.json" --max-sessions 1 \
  > "$RESEARCH_EVIDENCE/confirm-first-session.log" 2>&1
RESEARCH_PHASE=remaining-fixed-sample
date -u +%FT%TZ > "$RESEARCH_EVIDENCE/FIRST_SESSION_GATE_PASSED"
"$PYTHON_GPU_ENV" research_campaign.py --run "$RESEARCH_EVIDENCE/campaign.json" \
  > "$RESEARCH_EVIDENCE/confirm-remaining-sessions.log" 2>&1
RESEARCH_PHASE=independent-final-validation
"$PYTHON_GPU_ENV" research_validate.py --campaign "$RESEARCH_EVIDENCE/campaign.json" \
  --oracles "$RESEARCH_EVIDENCE/oracles" --output "$RESEARCH_EVIDENCE/validation.json" \
  > "$RESEARCH_EVIDENCE/final-validation.log" 2>&1
RESEARCH_PHASE=predefined-analysis
/home/ubuntu/study/.venv-research-test/bin/python research_analysis.py \
  --campaign "$RESEARCH_EVIDENCE/campaign.json" --validation "$RESEARCH_EVIDENCE/validation.json" \
  --output "$RESEARCH_EVIDENCE/analysis" > "$RESEARCH_EVIDENCE/analysis.log" 2>&1
RESEARCH_PHASE=complete-ready-for-reporting
date -u +%FT%TZ > "$RESEARCH_EVIDENCE/ANALYSIS_READY"
