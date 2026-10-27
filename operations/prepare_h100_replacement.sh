#!/usr/bin/env bash
# Provision and gate a NEW H100 campaign. Never executes confirmatory timings.
set -euo pipefail
REPLACEMENT_SOURCE=/home/ubuntu/study-h100-research-c84ef6a
REPLACEMENT_STUDY=/home/ubuntu/study
REPLACEMENT_EVIDENCE=/home/ubuntu/study-h100-research-evidence-20261027
REPLACEMENT_UV=/home/ubuntu/.local/bin/uv
mkdir -p "$REPLACEMENT_EVIDENCE"
test ! -e "$REPLACEMENT_EVIDENCE/prepare.started-utc"
printf '%s\n' "$$" > "$REPLACEMENT_EVIDENCE/prepare.pid"
date -u +%FT%TZ > "$REPLACEMENT_EVIDENCE/prepare.started-utc"
REPLACEMENT_PHASE=provision
finish_replacement_preparation() {
  REPLACEMENT_EXIT=$?
  printf '%s\n' "$REPLACEMENT_EXIT" > "$REPLACEMENT_EVIDENCE/prepare.exit"
  printf '%s\n' "$REPLACEMENT_PHASE" > "$REPLACEMENT_EVIDENCE/prepare.last-phase"
  date -u +%FT%TZ > "$REPLACEMENT_EVIDENCE/prepare.ended-utc"
}
trap finish_replacement_preparation EXIT
set_phase() {
  REPLACEMENT_PHASE="$1"
  printf '%s\n' "$REPLACEMENT_PHASE" > "$REPLACEMENT_EVIDENCE/prepare.phase"
  date -u +%FT%TZ
  printf 'PHASE %s\n' "$REPLACEMENT_PHASE"
}
cd "$REPLACEMENT_STUDY"
test "$(git rev-parse HEAD)" = c84ef6a07341f350dae6636ab709c1b65fd894e0
test "$(git -C "$REPLACEMENT_SOURCE" rev-parse HEAD)" = c84ef6a07341f350dae6636ab709c1b65fd894e0
python3 -m pip install --user uv==0.8.13
"$REPLACEMENT_UV" python install 3.11.13
"$REPLACEMENT_UV" venv --python 3.11.13 "$REPLACEMENT_STUDY/.venv"
bash setup_h100.sh --data-root "$REPLACEMENT_STUDY/data"
source "$REPLACEMENT_STUDY/rerun/h100-env.sh"
cd "$REPLACEMENT_SOURCE"
set_phase matching-cpu-control
bash setup_research_control.sh "$PYTHON_GPU_ENV" "$REPLACEMENT_STUDY/.venv-research-cpu200" "$REPLACEMENT_UV" \
  > "$REPLACEMENT_EVIDENCE/cpu200-setup.log" 2>&1
set_phase analysis-test-environment
"$REPLACEMENT_UV" venv --python "$(readlink -f "$PYTHON_GPU_ENV")" "$REPLACEMENT_STUDY/.venv-research-test"
"$REPLACEMENT_UV" pip install --python "$REPLACEMENT_STUDY/.venv-research-test/bin/python" \
  -r requirements-research-analysis.txt -r requirements-research-cpu200.txt
"$REPLACEMENT_UV" pip install --python "$REPLACEMENT_STUDY/.venv-research-test/bin/python" \
  polars==1.44.2 polars-runtime-32==1.44.2 matplotlib==3.10.8
"$REPLACEMENT_UV" pip check --python "$REPLACEMENT_STUDY/.venv-research-test/bin/python"
mkdir -p "$REPLACEMENT_EVIDENCE/inventory"
cp -a "$REPLACEMENT_STUDY/rerun/setup-logs/." "$REPLACEMENT_EVIDENCE/inventory/"
"$PYTHON_GPU_ENV" -c 'import sys,platform,json,importlib.metadata as m; print(json.dumps({"python":sys.version,"build":platform.python_build(),"packages":{d.metadata["Name"]:d.version for d in m.distributions()}},indent=2))' \
  > "$REPLACEMENT_EVIDENCE/inventory/primary-runtime.json"
"$REPLACEMENT_STUDY/.venv-research-test/bin/python" -m pytest -q \
  > "$REPLACEMENT_EVIDENCE/linux-functional-tests.log" 2>&1
set_phase generate-sf1
"$PYTHON_GPU_ENV" generate_data.py --root "$DATA_ROOT" --scales 1 --threads 30 --memory-limit 64GB \
  > "$REPLACEMENT_EVIDENCE/generate-sf1.log" 2>&1
set_phase readiness
"$PYTHON_GPU_ENV" research_readiness.py --data-root "$DATA_ROOT" \
  --output "$REPLACEMENT_EVIDENCE/readiness" --jar "$RAPIDS_JAR" --python "$PYTHON_GPU_ENV" \
  --polars-cpu-200-python "$REPLACEMENT_STUDY/.venv-research-cpu200/bin/python" \
  > "$REPLACEMENT_EVIDENCE/readiness-driver.log" 2>&1
set_phase generate-remaining-scales
"$PYTHON_GPU_ENV" generate_data.py --root "$DATA_ROOT" --scales 10 30 100 300 --threads 30 --memory-limit 64GB \
  > "$REPLACEMENT_EVIDENCE/generate-remaining.log" 2>&1
mkdir -p "$REPLACEMENT_EVIDENCE/manifests" "$REPLACEMENT_EVIDENCE/oracles" "$REPLACEMENT_EVIDENCE/oracle-spill"
for REPLACEMENT_SCALE in 1 10 30 100 300; do
  cp "$DATA_ROOT/sf$REPLACEMENT_SCALE/manifest.json" "$REPLACEMENT_EVIDENCE/manifests/sf$REPLACEMENT_SCALE.json"
done
set_phase baseline-oracles
for REPLACEMENT_SCALE in 1 10 30 100 300; do
  "$PYTHON_GPU_ENV" research_oracle.py --suite baseline --data "$DATA_ROOT/sf$REPLACEMENT_SCALE" \
    --output "$REPLACEMENT_EVIDENCE/oracles/baseline-sf$REPLACEMENT_SCALE.json" \
    --threads 26 --memory-limit 128GB --temp-directory "$REPLACEMENT_EVIDENCE/oracle-spill" \
    > "$REPLACEMENT_EVIDENCE/oracles/baseline-sf$REPLACEMENT_SCALE.log" 2>&1
done
set_phase complex-oracles
for REPLACEMENT_SCALE in 30 100 300; do
  "$PYTHON_GPU_ENV" research_oracle.py --suite complex --data "$DATA_ROOT/sf$REPLACEMENT_SCALE" \
    --output "$REPLACEMENT_EVIDENCE/oracles/complex-sf$REPLACEMENT_SCALE.json" \
    --threads 26 --memory-limit 128GB --temp-directory "$REPLACEMENT_EVIDENCE/oracle-spill" \
    > "$REPLACEMENT_EVIDENCE/oracles/complex-sf$REPLACEMENT_SCALE.log" 2>&1
done
set_phase freeze-new-campaign
"$PYTHON_GPU_ENV" research_campaign.py --plan "$REPLACEMENT_EVIDENCE/campaign.json" \
  --campaign-id h100-confirmatory-replacement-20261027 --seed 20261026 \
  --data-root "$DATA_ROOT" --output-root "$REPLACEMENT_EVIDENCE/campaign-results" \
  --jar "$RAPIDS_JAR" --python "$PYTHON_GPU_ENV" \
  --polars-cpu-200-python "$REPLACEMENT_STUDY/.venv-research-cpu200/bin/python" \
  --oracle-root "$REPLACEMENT_EVIDENCE/oracles" --readiness "$REPLACEMENT_EVIDENCE/readiness/READINESS.json" \
  > "$REPLACEMENT_EVIDENCE/plan.log" 2>&1
set_phase ready-for-off-host-prestart-archive
date -u +%FT%TZ > "$REPLACEMENT_EVIDENCE/READY_TO_ARCHIVE_PLAN"
