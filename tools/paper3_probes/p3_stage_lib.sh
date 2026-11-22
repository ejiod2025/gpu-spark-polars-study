# p3_stage_lib.sh: sourced by run_lambda_gpu_probe.sh and run_gcp_cpu_probe.sh (bash 3.2 compatible; macOS /bin/bash works).
#
# A long probe stage never depends on one live SSH session. Each stage is started DETACHED on the host (p3_detach.sh START: own
# session, every descriptor redirected, returns at once), and finished exactly when its exit-code file exists. The driver then
# polls with short, time-bounded SSH calls; a failed poll (laptop asleep, network down, stale TCP connection) is retried and
# never fatal; files are fetched with bounded, retried copies. What the host does never depends on the driver being awake.
#
# The sourcing script provides:
#   p3_log MESSAGE                     one timestamped line to its log
#   P3_SSH                             array: the ssh command up to and including the target, the command string is appended
#                                      (ssh ... ubuntu@IP, or gcloud compute ssh NAME ... --command)
#   P3_SCP_FROM                        array: the scp command up to the sources, "<P3_REMOTE_PREFIX><path>" "<dir>/" are appended
#   P3_SCP_TO                          array: same for copies to the host, "<files...>" "<P3_REMOTE_PREFIX><path>" are appended
#   P3_REMOTE_PREFIX                   "ubuntu@IP:" or "NAME:"
#   p3_host_alive (optional)           returns 1 when the host is known to be gone (API says so); anything else means "unknown"
# Tunables (environment): P3_POLL_SECONDS (30), P3_STAGE_TIMEOUT (5400: counted in polls, so it is awake time), P3_SSH_BOUND (60),
# P3_SCP_BOUND (300), P3_RETRIES (10), P3_START_RETRIES (20), P3_RETRY_PAUSE (15), P3_MAX_FAILED_POLLS (60), P3_SLEEP (the sleep command, for tests).
# P3_QUIET_LOG (unset): when set and not 0, the progress line does not echo the stage's last log line (the campaign driver sets it: a log line of a
# bring-up or a round can carry a wall time, which the operator must not see before the campaign ends, design 7.3). Probe drivers leave it unset.

P3_POLL_SECONDS=${P3_POLL_SECONDS:-30}
P3_STAGE_TIMEOUT=${P3_STAGE_TIMEOUT:-5400}
P3_SSH_BOUND=${P3_SSH_BOUND:-60}
P3_SCP_BOUND=${P3_SCP_BOUND:-300}
P3_RETRIES=${P3_RETRIES:-10}
P3_START_RETRIES=${P3_START_RETRIES:-20}
P3_RETRY_PAUSE=${P3_RETRY_PAUSE:-15}
P3_MAX_FAILED_POLLS=${P3_MAX_FAILED_POLLS:-60}
P3_SLEEP=${P3_SLEEP:-sleep}
P3_STAGE_RC=""        # set by p3_stage_wait: the stage's exit code
P3_STAGE_STATE=""     # set by p3_stage_wait when it returns 1: timeout | host-gone | died | not-started

p3_bounded() {        # SECONDS COMMAND [ARG...]: the command's own exit code, or 124 when SECONDS killed it. No leftover processes.
  local limit=$1 rc
  shift
  limit=${limit%%.*}; [ -n "$limit" ] && [ "$limit" -ge 1 ] 2>/dev/null || limit=1     # alarm() takes whole seconds
  if [ -z "${P3_NO_PERL:-}" ] && command -v perl >/dev/null 2>&1; then
    perl -e 'alarm shift; exec @ARGV or die "exec: $!\n"' "$limit" "$@"
    rc=$?
    [ "$rc" -eq 142 ] && rc=124                      # 128 + SIGALRM
    return $rc
  fi
  "$@" &
  local pid=$!
  ( "$P3_SLEEP" "$limit"; kill -TERM "$pid" 2>/dev/null; "$P3_SLEEP" 2; kill -KILL "$pid" 2>/dev/null ) >/dev/null 2>&1 &
  local killer=$!
  wait "$pid"
  rc=$?
  pkill -P "$killer" 2>/dev/null; kill "$killer" 2>/dev/null; wait "$killer" 2>/dev/null
  [ "$rc" -eq 143 ] || [ "$rc" -eq 137 ] && rc=124
  return $rc
}

p3_keep_awake() {     # [PID [WHAT]]: macOS: no idle or system sleep while PID (default: this script) lives. Call it again for the watchdog.
  [ -n "${P3_NO_CAFFEINATE:-}" ] && return 0
  local pid=${1:-$$} what=${2:-this run}
  if command -v caffeinate >/dev/null 2>&1; then
    caffeinate -i -s -w "$pid" >/dev/null 2>&1 &
    p3_log "caffeinate armed (pid $!) for $what (pid $pid): no idle/system sleep while it lives; closing the lid or battery power can still sleep the Mac, which the polling below survives and the watchdog's wall-clock deadline covers"
  fi
}

p3_ssh() {            # COMMAND-STRING: one bounded attempt; stdout is the command's, rc 124 on timeout
  p3_bounded "$P3_SSH_BOUND" "${P3_SSH[@]}" "$1" < /dev/null
}

p3_ssh_retry() {      # COMMAND-STRING: retried up to P3_RETRIES times (stdout of the successful attempt is printed); 1 when all fail
  local attempt rc out
  for attempt in $(seq 1 "$P3_RETRIES"); do
    out=$(p3_ssh "$1" 2>/dev/null); rc=$?
    if [ "$rc" -eq 0 ]; then printf '%s\n' "$out"; return 0; fi
    p3_log "ssh attempt $attempt/$P3_RETRIES failed (rc=$rc): ${1:0:60}"
    [ "$attempt" -lt "$P3_RETRIES" ] && "$P3_SLEEP" "$P3_RETRY_PAUSE"
  done
  return 1
}

p3_copy_to() {        # LOCAL_FILE... REMOTE_PATH: bounded and retried
  local attempt rc
  for attempt in $(seq 1 "$P3_RETRIES"); do
    p3_bounded "$P3_SCP_BOUND" "${P3_SCP_TO[@]}" "$@" >/dev/null 2>&1 && return 0
    rc=$?
    p3_log "copy to host, attempt $attempt/$P3_RETRIES failed (rc=$rc)"
    [ "$attempt" -lt "$P3_RETRIES" ] && "$P3_SLEEP" "$P3_RETRY_PAUSE"
  done
  return 1
}

p3_fetch() {          # REMOTE_PATH LOCAL_DIR: bounded and retried copy from the host
  local attempt rc
  for attempt in $(seq 1 "$P3_RETRIES"); do
    p3_bounded "$P3_SCP_BOUND" "${P3_SCP_FROM[@]}" "${P3_REMOTE_PREFIX}$1" "$2" >/dev/null 2>&1 && return 0
    rc=$?
    p3_log "fetch of $1, attempt $attempt/$P3_RETRIES failed (rc=$rc)"
    [ "$attempt" -lt "$P3_RETRIES" ] && "$P3_SLEEP" "$P3_RETRY_PAUSE"
  done
  return 1
}

p3_remote_names() {   # PATTERN...: names (relative to ~/p3out) of the remote files matching the globs; 1 only when ssh itself kept failing
  local patterns="$*" attempt rc out
  for attempt in $(seq 1 "$P3_RETRIES"); do
    out=$(p3_ssh "cd \"\$HOME/p3out\" 2>/dev/null && ls -1d $patterns 2>/dev/null; true" 2>/dev/null); rc=$?
    if [ "$rc" -eq 0 ]; then printf '%s\n' "$out" | sed '/^$/d'; return 0; fi
    [ "$attempt" -lt "$P3_RETRIES" ] && "$P3_SLEEP" "$P3_RETRY_PAUSE"
  done
  return 1
}

p3_stage_start_raw() {  # STAGE WORDS: WORDS is the command already quoted for the host's shell (it may use the host's $HOME)
  local stage=$1 words=$2 attempt out
  for attempt in $(seq 1 "$P3_START_RETRIES"); do
    out=$(p3_ssh "bash \"\$HOME/p3_detach.sh\" START $stage $words" 2>/dev/null) || out=""
    case "$out" in
      *"started $stage"*) p3_log "stage $stage: $(printf '%s' "$out" | tail -n 1)"; return 0 ;;   # "started" or "already-started"
    esac
    p3_log "stage $stage: start call attempt $attempt/$P3_START_RETRIES failed"
    [ "$attempt" -lt "$P3_START_RETRIES" ] && "$P3_SLEEP" "$P3_RETRY_PAUSE"
  done
  return 1
}

p3_stage_start() {    # STAGE COMMAND [ARG...]: every word quoted for the host
  local stage=$1 words="" word
  shift
  for word in "$@"; do words="$words $(printf '%q' "$word")"; done
  p3_stage_start_raw "$stage" "$words"
}

p3_stage_wait() {     # STAGE: 0 with P3_STAGE_RC set once the host has written the stage's exit code; 1 with P3_STAGE_STATE otherwise
  local stage=$1 polls=0 failures=0 consecutive=0 idle=0 last_failed_rc="" max gap_limit out rc code running started last now gap
  max=$(awk -v t="$P3_STAGE_TIMEOUT" -v p="$P3_POLL_SECONDS" 'BEGIN { n = int(t / p); print (n < 1 ? 1 : n) }')   # poll intervals may be fractional (tests)
  gap_limit=$(awk -v p="$P3_POLL_SECONDS" -v b="$P3_SSH_BOUND" 'BEGIN { print int(4 * p + b) + 1 }')
  P3_STAGE_RC=""; P3_STAGE_STATE=""
  last=$(date +%s)
  while [ "$polls" -lt "$max" ]; do
    polls=$((polls + 1))
    now=$(date +%s); gap=$(( now - last )); last=$now
    if [ "$gap" -gt "$gap_limit" ]; then
      p3_log "stage $stage: ${gap} s since the previous poll (the Mac was asleep or suspended); polling resumes, the host was not affected"
    fi
    out=$(p3_ssh "bash \"\$HOME/p3_detach.sh\" STATUS $stage" 2>/dev/null); rc=$?
    if [ "$rc" -eq 0 ]; then
      [ "$consecutive" -gt 0 ] && p3_log "stage $stage: polling works again after $consecutive failed poll(s)"
      consecutive=0
      code=$(printf '%s\n' "$out" | sed -n 's/^RC=//p' | head -n 1 | tr -d '[:space:]')
      running=$(printf '%s\n' "$out" | sed -n 's/^RUNNING=//p' | head -n 1 | tr -d '[:space:]')
      started=$(printf '%s\n' "$out" | sed -n 's/^STARTED=//p' | head -n 1 | tr -d '[:space:]')
      case "$code" in ''|*[!0-9]*) ;; *) P3_STAGE_RC=$code; return 0 ;; esac
      if [ "$started" = 0 ] || [ "$running" = 0 ]; then       # no exit code, and nothing is running: the stage is not going to finish
        idle=$((idle + 1))
        if [ "$idle" -ge 3 ]; then
          if [ "$started" = 0 ]; then P3_STAGE_STATE="not-started"; else P3_STAGE_STATE="died"; fi
          p3_log "stage $stage: ${P3_STAGE_STATE}: no exit code file and no running process on three consecutive polls"
          return 1
        fi
      else
        idle=0
      fi
      if [ $(( polls % ${P3_PROGRESS_EVERY:-10} )) -eq 1 ]; then
        if [ -n "${P3_QUIET_LOG:-}" ] && [ "$P3_QUIET_LOG" != 0 ]; then p3_log "stage $stage running (poll $polls/$max); last log line withheld (P3_QUIET_LOG)"
        else p3_log "stage $stage running (poll $polls/$max); last log line: $(printf '%s\n' "$out" | sed -n 's/^LOG=//p' | head -n 1)"; fi
      fi
    else
      failures=$((failures + 1)); consecutive=$((consecutive + 1))
      if [ "$consecutive" -eq 1 ] || [ $(( consecutive % 10 )) -eq 0 ] || [ "$rc" != "$last_failed_rc" ]; then
        p3_log "stage $stage: poll failed (rc=$rc, $consecutive in a row, $failures in all); retrying, the stage runs on regardless"
      fi
      last_failed_rc=$rc
      if [ "$consecutive" -ge "$P3_MAX_FAILED_POLLS" ]; then
        if declare -F p3_host_alive >/dev/null 2>&1 && ! p3_host_alive; then
          P3_STAGE_STATE="host-gone"; p3_log "stage $stage: $P3_MAX_FAILED_POLLS polls failed and the provider no longer lists the host"; return 1
        fi
        consecutive=0
      fi
    fi
    "$P3_SLEEP" "$P3_POLL_SECONDS"
  done
  for _ in 1 2 3; do      # the budget is spent: one last look before giving up on the stage
    out=$(p3_ssh "bash \"\$HOME/p3_detach.sh\" STATUS $stage" 2>/dev/null) || { "$P3_SLEEP" "$P3_RETRY_PAUSE"; continue; }
    code=$(printf '%s\n' "$out" | sed -n 's/^RC=//p' | head -n 1 | tr -d '[:space:]')
    case "$code" in ''|*[!0-9]*) break ;; *) P3_STAGE_RC=$code; return 0 ;; esac
  done
  P3_STAGE_STATE="timeout"
  p3_log "stage $stage: no exit code after $polls polls (budget ${P3_STAGE_TIMEOUT} s of polling)"
  return 1
}
