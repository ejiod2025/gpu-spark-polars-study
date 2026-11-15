#!/usr/bin/env bash
# battery_runner.sh — runs ON the remote probe host (copied there, then started detached via
# `nohup bash ~/battery_runner.sh > ~/p3out/battery_launch.log 2>&1 < /dev/null &`), so no long-lived SSH
# session is ever required for the ~20-minute semantics+p6+g8 battery. Stages run sequentially, NOT
# &&-chained, so one stage failing does not skip the rest; each stage's exit code is appended to
# p3out/battery.log as it finishes, and p3out/DONE is written only once all three have returned (whatever
# their exit codes), so a poller can tell "still running" from "finished" without needing an open session.
set -uo pipefail
cd ~
mkdir -p p3out
: > p3out/battery.log

echo "$(date -u +%FT%TZ) semantics starting" >> p3out/battery.log
python3 p3_gpu_probes.py semantics --out p3out
echo "semantics_exit=$? $(date -u +%FT%TZ)" >> p3out/battery.log

echo "$(date -u +%FT%TZ) p6 starting" >> p3out/battery.log
python3 p3_gpu_probes.py p6 --out p3out
echo "p6_exit=$? $(date -u +%FT%TZ)" >> p3out/battery.log

echo "$(date -u +%FT%TZ) g8 starting" >> p3out/battery.log
python3 p3_gpu_probes.py g8 --out p3out
echo "g8_exit=$? $(date -u +%FT%TZ)" >> p3out/battery.log

date -u +%FT%TZ > p3out/DONE
