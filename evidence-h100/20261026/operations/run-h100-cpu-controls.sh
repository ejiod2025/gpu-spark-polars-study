#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/study-h100-c4c934c
trap 'controls_code=$?; if test "$controls_code" -ne 0; then printf "failed\n" > rerun/h100-cpu-controls.status; fi; printf "%s\n" "$controls_code" > rerun/h100-cpu-controls.exit; exit "$controls_code"' EXIT
printf 'waiting_for_main_complex\n' > rerun/h100-cpu-controls.status
while ! test -f rerun/h100-complex-formal-20261026.exit; do sleep 10; done
test "$(cat rerun/h100-complex-formal-20261026.exit)" = 0
source /home/ubuntu/study/rerun/h100-env.sh
printf 'baseline_running\n' > rerun/h100-cpu-controls.status
"$PYTHON_A100COMPAT_ENV" run_experiment.py --data-root "$DATA_ROOT" \
  --output rerun/h100-polars2-baseline-control-20261026 --scales 1 10 30 100 300 \
  --runs 3 --cpus 26 --driver-memory 96g --engines polars --jar "$RAPIDS_JAR" \
  --keep-going --engine-timeout-seconds 5400 > rerun/h100-polars2-baseline-control-20261026.log 2>&1
printf '0\n' > rerun/h100-polars2-baseline-control-20261026.exit
printf 'complex_running\n' > rerun/h100-cpu-controls.status
"$PYTHON_A100COMPAT_ENV" run_experiment.py --suite complex --query-cleanup verified_gc \
  --cleanup-timeout-seconds 60 --data-root "$DATA_ROOT" \
  --output rerun/h100-polars2-complex-control-20261026 --scales 30 100 300 \
  --runs 3 --cpus 26 --driver-memory 96g --engines polars --jar "$RAPIDS_JAR" \
  --keep-going --engine-timeout-seconds 5400 > rerun/h100-polars2-complex-control-20261026.log 2>&1
printf '0\n' > rerun/h100-polars2-complex-control-20261026.exit
printf 'complete\n' > rerun/h100-cpu-controls.status
