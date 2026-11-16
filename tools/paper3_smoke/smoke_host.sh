#!/usr/bin/env bash
# smoke_host.sh setup | data | run | gates | bundle      (runs on the Lambda H100 HOST; run_h100_smoke.sh starts each as a detached stage)
#
#   setup   ./setup_h100.sh exactly as the campaign host is brought up (no flags), timed: the duration is a result of the smoke test
#   data    the synthetic dataset (generate_smoke_data.py) into ~/p3smoke/data/sf1
#   run     host-exclusivity capture, then run_experiment.py with the campaign's flags for round 1 (smoke_command.py: --suite smoke
#           --synthetic-smoke on the synthetic data, four engines, scope board, window 10 s, pulses, idle windows 30 s after a 60 s
#           settle), then the end capture
#   gates   paper3_gates.py --host h100 with the repository's host config over the run directory (exit 1 = a gate failed, not a tool error)
#   bundle  ~/p3out/smoke-evidence.tar.gz: the run directory without Spark event logs, the gates report, captures, timings, stage logs
#
# Every stage writes ~/p3out/<name>-timing.json {name, start_utc, end_utc, start_epoch, end_epoch, seconds, rc}.
# Environment: P3_SMOKE_RUNS (measured trials per query, 3), P3_SMOKE_DATA_ARGS (extra generate_smoke_data.py arguments, for rehearsals).
set -uo pipefail
REPO=${P3_SMOKE_REPO:-$HOME/gpu-spark-polars-study}
SMOKE=${P3_SMOKE_DIR:-$HOME/p3smoke}
OUT=$HOME/p3out
export DEBIAN_FRONTEND=noninteractive
mkdir -p "$OUT" "$SMOKE"
cd "$REPO" || { echo "smoke_host.sh: no repository at $REPO" >&2; exit 1; }

utc_of() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ; }   # GNU date, else BSD date

timed() {   # NAME COMMAND [ARG...]: run it, write NAME-timing.json (whole seconds), return its exit code
  local name=$1 rc start end
  shift
  start=$(date +%s)
  "$@"
  rc=$?
  end=$(date +%s)
  printf '{"name": "%s", "start_utc": "%s", "end_utc": "%s", "start_epoch": %d, "end_epoch": %d, "seconds": %d, "rc": %d}\n' \
    "$name" "$(utc_of "$start")" "$(utc_of "$end")" "$start" "$end" "$(( end - start ))" "$rc" > "$OUT/$name-timing.json.tmp" \
    && mv "$OUT/$name-timing.json.tmp" "$OUT/$name-timing.json"
  return "$rc"
}

use_env() {  # the environment file setup_h100.sh wrote
  [ -f "$REPO/rerun/h100-env.sh" ] || { echo "smoke_host.sh: rerun/h100-env.sh is missing: run the setup stage first" >&2; exit 1; }
  # shellcheck disable=SC1091
  . "$REPO/rerun/h100-env.sh"
  [ -x "${PYTHON_GPU_ENV:-}" ] || { echo "smoke_host.sh: PYTHON_GPU_ENV ($PYTHON_GPU_ENV) is not executable" >&2; exit 1; }
}

capture() {  # LABEL: the host-exclusivity record paper3_gates.py reads (G12)
  "$PYTHON_GPU_ENV" -c 'import json, sys; sys.path.insert(0, "."); import paper3_gates; print(json.dumps(paper3_gates.capture_host_exclusivity(sys.argv[1], gpu=True), indent=1))' "$1" \
    > "$SMOKE/host-exclusivity-$1.json"
}

case "${1:-}" in
  setup)
    timed setup ./setup_h100.sh
    ;;
  data)
    use_env
    # shellcheck disable=SC2086
    timed data "$PYTHON_GPU_ENV" tools/paper3_smoke/generate_smoke_data.py --root "$SMOKE/data" ${P3_SMOKE_DATA_ARGS:-}
    ;;
  run)
    use_env
    [ -d "$SMOKE/run" ] && { echo "smoke_host.sh: $SMOKE/run exists already; a run directory is never reused" >&2; exit 1; }
    capture start
    timed run "$PYTHON_GPU_ENV" tools/paper3_smoke/smoke_command.py --python "$PYTHON_GPU_ENV" --data-root "$SMOKE/data" \
      --output "$SMOKE/run" --jar "$RAPIDS_JAR" --runs "${P3_SMOKE_RUNS:-3}" --exec
    rc=$?
    capture end
    exit "$rc"
    ;;
  gates)
    use_env
    timed gates "$PYTHON_GPU_ENV" paper3_gates.py --host h100 --config paper3_hosts.json --run-dir "$SMOKE/run" --suite smoke \
      --host-capture "$SMOKE/host-exclusivity-start.json" --host-capture "$SMOKE/host-exclusivity-end.json" --output "$SMOKE/gates.json"
    ;;
  bundle)
    stage="$SMOKE/evidence"
    rm -rf "$stage"
    mkdir -p "$stage/run" "$stage/stages"
    if [ -d "$SMOKE/run" ]; then      # everything but Spark's event logs (large, per-task, carry host names)
      ( cd "$SMOKE/run" && find . -type f ! -path '*-events/*' -print0 | tar --null -T - -cf - ) | tar -xf - -C "$stage/run"
    fi
    for f in gates.json host-exclusivity-start.json host-exclusivity-end.json; do
      [ -f "$SMOKE/$f" ] && cp "$SMOKE/$f" "$stage/$f"
    done
    [ -f "$SMOKE/data/sf1/manifest.json" ] && cp "$SMOKE/data/sf1/manifest.json" "$stage/data-manifest.json"
    for f in "$OUT"/*-timing.json "$OUT/setup.log" "$OUT/data.log" "$OUT/run.log" "$OUT/gates.log"; do
      [ -f "$f" ] && cp "$f" "$stage/stages/"
    done
    [ -d "$REPO/rerun/setup-logs" ] && cp -R "$REPO/rerun/setup-logs" "$stage/setup-logs"
    [ -f "$REPO/rerun/h100-env.sh" ] && cp "$REPO/rerun/h100-env.sh" "$stage/h100-env.sh"
    tar -czf "$OUT/smoke-evidence.tar.gz.tmp" -C "$stage" . && mv "$OUT/smoke-evidence.tar.gz.tmp" "$OUT/smoke-evidence.tar.gz"
    ;;
  *)
    sed -n 2,14p "$0" >&2
    exit 2
    ;;
esac
