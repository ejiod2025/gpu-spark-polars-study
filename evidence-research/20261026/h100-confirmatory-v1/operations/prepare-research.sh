#!/usr/bin/env bash
# Preparation only: timed confirmation requires separately archiving the plan.
set -euo pipefail
RESEARCH_SOURCE=/home/ubuntu/study-h100-research-c84ef6a
RESEARCH_EVIDENCE=/home/ubuntu/study-h100-research-evidence-20261026
mkdir -p "$RESEARCH_EVIDENCE"
printf '%s\n' "$$" > "$RESEARCH_EVIDENCE/prepare.pid"
RESEARCH_PHASE=starting
finish_research_preparation() {
  RESEARCH_EXIT=$?
  printf '%s\n' "$RESEARCH_EXIT" > "$RESEARCH_EVIDENCE/prepare.exit"
  printf '%s\n' "$RESEARCH_PHASE" > "$RESEARCH_EVIDENCE/prepare.last-phase"
  date -u +%FT%TZ > "$RESEARCH_EVIDENCE/prepare.ended-utc"
}
trap finish_research_preparation EXIT
date -u +%FT%TZ > "$RESEARCH_EVIDENCE/prepare.started-utc"
cd "$RESEARCH_SOURCE"
source /home/ubuntu/study/rerun/h100-env.sh
RESEARCH_CPU200=/home/ubuntu/study/.venv-research-cpu200/bin/python
RESEARCH_PHASE=linux-tests
/home/ubuntu/study/.venv-research-test/bin/python -m pytest -q > "$RESEARCH_EVIDENCE/linux-tests.log" 2>&1
RESEARCH_PHASE=sf1-readiness
"$PYTHON_GPU_ENV" research_readiness.py --data-root "$DATA_ROOT" \
  --output "$RESEARCH_EVIDENCE/readiness" --jar "$RAPIDS_JAR" \
  --python "$PYTHON_GPU_ENV" --polars-cpu-200-python "$RESEARCH_CPU200" \
  > "$RESEARCH_EVIDENCE/readiness.log" 2>&1
mkdir -p "$RESEARCH_EVIDENCE/oracles" "$RESEARCH_EVIDENCE/oracle-spill"
for RESEARCH_SCALE in 1 10 30 100 300; do
  RESEARCH_PHASE="baseline-oracle-sf$RESEARCH_SCALE"
  printf '%s\n' "$RESEARCH_PHASE"
  "$PYTHON_GPU_ENV" research_oracle.py --suite baseline --data "$DATA_ROOT/sf$RESEARCH_SCALE" \
    --output "$RESEARCH_EVIDENCE/oracles/baseline-sf$RESEARCH_SCALE.json" \
    --threads 26 --memory-limit 128GB --temp-directory "$RESEARCH_EVIDENCE/oracle-spill" \
    > "$RESEARCH_EVIDENCE/oracles/baseline-sf$RESEARCH_SCALE.log" 2>&1
done
for RESEARCH_SCALE in 30 100 300; do
  RESEARCH_PHASE="complex-oracle-sf$RESEARCH_SCALE"
  printf '%s\n' "$RESEARCH_PHASE"
  "$PYTHON_GPU_ENV" research_oracle.py --suite complex --data "$DATA_ROOT/sf$RESEARCH_SCALE" \
    --output "$RESEARCH_EVIDENCE/oracles/complex-sf$RESEARCH_SCALE.json" \
    --threads 26 --memory-limit 128GB --temp-directory "$RESEARCH_EVIDENCE/oracle-spill" \
    > "$RESEARCH_EVIDENCE/oracles/complex-sf$RESEARCH_SCALE.log" 2>&1
done
RESEARCH_PHASE=freeze-campaign
"$PYTHON_GPU_ENV" research_campaign.py --plan "$RESEARCH_EVIDENCE/campaign.json" \
  --data-root "$DATA_ROOT" --output-root "$RESEARCH_EVIDENCE/campaign-results" \
  --jar "$RAPIDS_JAR" --python "$PYTHON_GPU_ENV" --polars-cpu-200-python "$RESEARCH_CPU200" \
  --seed 20261026 --oracle-root "$RESEARCH_EVIDENCE/oracles" \
  --readiness "$RESEARCH_EVIDENCE/readiness/READINESS.json"
RESEARCH_PHASE=ready-to-archive-plan
date -u +%FT%TZ > "$RESEARCH_EVIDENCE/READY_TO_ARCHIVE_PLAN"
