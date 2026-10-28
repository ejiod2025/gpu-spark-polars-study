#!/usr/bin/env bash
# Separate user-authorized contention interlude; no original worker is stopped.
set -euo pipefail
test "$#" = 2
source /home/ubuntu/study/rerun/h100-env.sh
unset JAVA_TOOL_OPTIONS _JAVA_OPTIONS JDK_JAVA_OPTIONS SPARK_LAUNCHER_OPTS
export SPARK_SUBMIT_OPTS='-XX:+UnlockDiagnosticVMOptions -XX:GCLockerRetryAllocationCount=100 -XX:+PrintFlagsFinal'
export PYTHONDONTWRITEBYTECODE=1
cd /home/ubuntu/study-h100-research-c84ef6a
exec "$PYTHON_GPU_ENV" -B /home/ubuntu/study-h100-contention-staging-20261028/contention_handoff.py \
  --scheduler-pid "$1" --scheduler-created "$2" --supervisor-pid 92406 \
  --campaign /home/ubuntu/study-h100-research-evidence-20261027-gclocker100/campaign.json \
  --source /home/ubuntu/study-h100-research-c84ef6a \
  --profile /home/ubuntu/study-h100-research-evidence-20261027-gclocker100/EXECUTION_PROFILE.json \
  --driver-spec /home/ubuntu/study-h100-contention-staging-20261028/DRIVER_SPEC.json \
  --output /home/ubuntu/study-h100-contention-handoff-20261028 \
  --drain-timeout 2700 --driver-timeout 14400 --total-timeout 14400
