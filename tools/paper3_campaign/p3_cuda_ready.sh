#!/usr/bin/env bash
# p3_cuda_ready.sh --python PY [--out DIR] [--retry-seconds 30] [--max-seconds 600]       (runs on the GPU HOST, from the repository root)
#
# The CUDA-readiness gate of the campaign bring-up and of the smoke test: after setup and before any data or engine work, a tiny CUDA init and kernel
# (`PY gpu_pulse.py --seconds 0.5`: cuInit, a context, a PTX kernel) must succeed. A host can be up, show its GPU in nvidia-smi and still answer every CUDA call with
# error 802 (cudaErrorSystemNotReady, "system not yet initialized": the 2026-11-22 smoke run 2 on a Lambda H100, where cuInit, the cudf-polars session guard and
# Spark RAPIDS all failed while the CPU engines were fine). That is a fault of the host, not of the harness, and it must stop the launch before a round, not after one.
#
#   record   nvidia-smi --query-gpu=fabric.state,fabric.status (when the installed nvidia-smi lists those fields), `nvidia-smi -q` (serial numbers, UUIDs, bus ids
#            and GUIDs blanked), and `systemctl is-active nvidia-fabricmanager` (when the unit exists): at the start, at every attempt (one line of cuda-ready.jsonl)
#            and, when the gate fails, at the end;
#   retry    on any CUDA init failure (802 or another) every --retry-seconds, for up to --max-seconds of wall-clock time (the fabric or the driver may still be
#            initializing). If the fabricmanager unit exists and is not active, `sudo systemctl start nvidia-fabricmanager` is tried ONCE and recorded;
#   verdict  exit 0 "CUDA ready"; or exit 82 with a `[FAIL    ] cuda.ready: host GPU not ready (CUDA <code>)` line (the line the driver's operator view shows). The driver
#            tears the host down on 82 and records an infrastructure failure before round 1.
#
# Writes to --out (default rerun/bringup-artifacts): cuda-ready.json (the verdict), cuda-ready.jsonl (attempts), cuda-ready-nvidia-smi-q-start.txt and, on a failure,
# -end.txt, cuda-ready-fabric.csv. Tests inject P3_CUDA_NOW (a command printing epoch seconds) and P3_CUDA_SLEEP (a command taking seconds).
set -uo pipefail
usage() { sed -n '2,/^set -uo pipefail/p' "$0" | sed '$d'; }
PY="" OUT=rerun/bringup-artifacts RETRY=30 MAX=600
while [ $# -gt 0 ]; do
  case "$1" in
    --python) PY=${2:-}; shift 2 ;;
    --out) OUT=${2:-}; shift 2 ;;
    --retry-seconds) RETRY=${2:-}; shift 2 ;;
    --max-seconds) MAX=${2:-}; shift 2 ;;
    *) usage; exit 2 ;;
  esac
done
[ -n "$PY" ] && [ -x "$PY" ] || { echo "p3_cuda_ready.sh: --python must name an executable (the repository's GPU environment)" >&2; exit 2; }
for v in "$RETRY" "$MAX"; do case "$v" in ''|*[!0-9]*) echo "p3_cuda_ready.sh: --retry-seconds and --max-seconds are whole numbers" >&2; exit 2 ;; esac; done
[ -f gpu_pulse.py ] || { echo "p3_cuda_ready.sh: run it from the repository root (gpu_pulse.py is not here)" >&2; exit 2; }
mkdir -p "$OUT" || exit 2
now() { ${P3_CUDA_NOW:-date +%s}; }
wsleep() { ${P3_CUDA_SLEEP:-sleep} "$1"; }
stamp() { date -u +%FT%TZ; }
UNIT=nvidia-fabricmanager

unit_exists() { systemctl list-unit-files "$UNIT.service" 2>/dev/null | grep -q "^$UNIT"; }
unit_state() { if unit_exists; then systemctl is-active "$UNIT" 2>/dev/null || true; else echo absent; fi; }
fabric_listed() { nvidia-smi --help-query-gpu 2>/dev/null | grep -q 'fabric\.state'; }
fabric_now() {   # state,status of the first GPU, or "unlisted" / "unavailable"
  if ! fabric_listed; then echo unlisted; return; fi
  nvidia-smi --query-gpu=fabric.state,fabric.status --format=csv,noheader 2>/dev/null | head -n 1 | tr -d ' ' || echo unavailable
}
smi_q() {        # FILE: nvidia-smi -q with what identifies a board blanked (serial numbers, UUIDs, bus ids, GUIDs)
  nvidia-smi -q 2>&1 | sed -E -e 's/^( *[^:]*(UUID|Serial Number|Bus Id|GUID)[^:]*:).*/\1 <redacted>/' \
    -e 's/[0-9A-Fa-f]{8}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.[0-9]/<bus-id>/g' > "$1" || true             # the "GPU 00000000:00:05.0" header carries the bus id too
}
json_str() { "$PY" -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$1"; }

smi_q "$OUT/cuda-ready-nvidia-smi-q-start.txt"
{ echo "# $(stamp) nvidia-smi --query-gpu=fabric.state,fabric.status"; fabric_now; } > "$OUT/cuda-ready-fabric.csv"
: > "$OUT/cuda-ready.jsonl"
start=$(now); deadline=$(( start + MAX ))
attempt=0 started_unit=0 code="" error="" ok=0
while :; do
  attempt=$((attempt + 1))
  pulse=$("$PY" gpu_pulse.py --seconds 0.5 --label ready 2>/dev/null); rc=$?
  parsed=$(printf '%s' "$pulse" | "$PY" -c '
import json, re, sys
try:
    record = json.loads(sys.stdin.read().strip().splitlines()[-1])
except Exception:
    print(0); print(""); print("no JSON record from gpu_pulse.py"); sys.exit()
error = " ".join(str(record.get("error", "")).split())
m = re.search(r"CUDA error (\d+)", error)
print(1 if record.get("ok") else 0); print(m.group(1) if m else ("802" if "SystemNotReady" in error else "")); print(error)
')
  { read -r ok; read -r code; read -r error; } <<< "$parsed"
  [ "$rc" -ne 0 ] && ok=0
  fabric=$(fabric_now); unit=$(unit_state)
  printf '{"attempt": %d, "utc": "%s", "ok": %s, "code": %s, "error": %s, "fabric": %s, "fabricmanager": %s}\n' \
    "$attempt" "$(stamp)" "$([ "$ok" = 1 ] && echo true || echo false)" "${code:-null}" "$(json_str "$error")" "$(json_str "$fabric")" "$(json_str "$unit")" >> "$OUT/cuda-ready.jsonl"
  [ "$ok" = 1 ] && break
  echo "$(stamp) attempt $attempt: CUDA is not usable (${error:-no error text}); fabricmanager $unit, fabric $fabric"
  if [ "$started_unit" = 0 ] && unit_exists && [ "$unit" != active ]; then      # once: the unit exists and is not running
    started_unit=1
    if sudo systemctl start "$UNIT"; then action="systemctl start $UNIT: ok"; else action="systemctl start $UNIT: failed"; fi
    echo "$(stamp) $action"
    printf '{"attempt": %d, "utc": "%s", "action": %s}\n' "$attempt" "$(stamp)" "$(json_str "$action")" >> "$OUT/cuda-ready.jsonl"
  fi
  [ "$(now)" -ge "$deadline" ] && break
  wsleep "$RETRY"                                                                  # the attempt after the last sleep is still made: a failing host gets its full window
done

end=$(now); seconds=$(( end - start ))
if [ "$ok" = 1 ]; then
  printf '{"ok": true, "attempts": %d, "seconds": %d, "fabricmanager": %s, "fabric": %s}\n' "$attempt" "$seconds" "$(json_str "$(unit_state)")" "$(json_str "$(fabric_now)")" > "$OUT/cuda-ready.json"
  echo "[PASS    ] cuda.ready: CUDA initialized and a kernel ran (attempt $attempt)"
  exit 0
fi
smi_q "$OUT/cuda-ready-nvidia-smi-q-end.txt"
{ echo "# $(stamp) nvidia-smi --query-gpu=fabric.state,fabric.status (after the last attempt)"; fabric_now; } >> "$OUT/cuda-ready-fabric.csv"
printf '{"ok": false, "reason": "host GPU not ready (CUDA %s)", "code": %s, "error": %s, "attempts": %d, "seconds": %d, "fabricmanager": %s, "fabricmanager_started": %s, "fabric": %s}\n' \
  "${code:-unknown}" "${code:-null}" "$(json_str "$error")" "$attempt" "$seconds" "$(json_str "$(unit_state)")" "$([ "$started_unit" = 1 ] && echo true || echo false)" "$(json_str "$(fabric_now)")" > "$OUT/cuda-ready.json"
echo "[FAIL    ] cuda.ready: host GPU not ready (CUDA ${code:-unknown}): ${error:-no error text} after $attempt attempts"
exit 82
