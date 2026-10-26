#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/study-h100-c4c934c
trap 'post_code=$?; printf "%s\n" "$post_code" > rerun/h100-postrun-validation.exit; exit "$post_code"' EXIT
while ! test -f rerun/h100-cpu-controls.exit; do sleep 10; done
test "$(cat rerun/h100-cpu-controls.exit)" = 0
/home/ubuntu/study/.venv/bin/python /home/ubuntu/study-h100-postcheck/verify_generated_inputs.py \
  --data-root /home/ubuntu/study/data --scales 1 10 30 100 300 --threads 26 --workers 8 \
  --memory-limit 64GB --unique-keys off --report rerun/h100-postrun-validation.json \
  > rerun/h100-postrun-validation.log 2>&1
