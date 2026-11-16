#!/usr/bin/env bash
# p3_detach.sh START STAGE COMMAND [ARG...] | STATUS STAGE     (runs on the probe HOST, copied there by the drivers)
#
# Starts COMMAND as a stage that does not depend on the SSH session that started it. START returns at once; the stage runs
# in its own session with all three file descriptors redirected, so neither a dropped connection, a sleeping laptop nor the
# end of the SSH call can stop it or hold the call open.
#
#   ~/p3out/<stage>.started/   created atomically by START; a second START of the same stage (an SSH call retried after a
#                              timeout whose first attempt did reach the host) finds it and starts nothing
#   ~/p3out/<stage>.log        the stage's stdout and stderr
#   ~/p3out/<stage>.rc         the stage's exit code, written last and atomically: a stage is finished exactly when this
#                              file exists (127 when COMMAND could not be run)
#   ~/p3out/<stage>.driver.log what the wrapper itself printed (normally nothing)
#   STATUS prints RC= (empty while running), STARTED=0|1, RUNNING=0|1 (the wrapper process is alive) and LOG= (last log line)
#
# Why not `cmd & disown`: in `a && b && nohup c > /dev/null 2>&1 < /dev/null & echo started` the `&` applies to the whole
# AND-list; the backgrounded list is a subshell that still holds the SSH channel's stdout and stderr, so ssh waits for it
# and the "detached" start call lasts as long as the stage (seen: a 17 minute start call in the GCP P6-CPU run). Here the
# backgrounded unit is one simple command with its own redirections.
#
# STAGE is [A-Za-z0-9._-]+. COMMAND runs in $HOME, stdin /dev/null.
set -u
mode=${1:-}
stage=${2:-}
case "$stage" in ''|*[!A-Za-z0-9._-]*) echo "p3_detach.sh: bad stage name '$stage'" >&2; exit 2 ;; esac
out=$HOME/p3out

case "$mode" in
  START)
    shift 2
    [ $# -ge 1 ] || { echo "p3_detach.sh START: no command" >&2; exit 2; }
    mkdir -p "$out" || exit 1
    if ! mkdir "$out/$stage.started" 2>/dev/null; then
      echo "already-started $stage"
      exit 0
    fi
    rm -f "$out/$stage.rc" "$out/$stage.rc.tmp"
    launcher=(nohup)
    command -v setsid >/dev/null 2>&1 && launcher=(setsid nohup)
    # one simple command, every descriptor redirected; the subshell around it exits at once
    ( "${launcher[@]}" bash "$0" RUN "$stage" "$@" > "$out/$stage.driver.log" 2>&1 < /dev/null & )
    echo "started $stage"
    exit 0
    ;;
  RUN)
    shift 2
    cd "$HOME" || exit 1
    "$@" > "$out/$stage.log" 2>&1 < /dev/null
    rc=$?
    echo "$rc" > "$out/$stage.rc.tmp" && mv "$out/$stage.rc.tmp" "$out/$stage.rc"
    exit 0
    ;;
  STATUS)       # what a poll needs, in one short call: the exit code (empty while running), whether the stage was started,
                # whether its process is alive, and the last log line
    printf 'RC=%s\n' "$(cat "$out/$stage.rc" 2>/dev/null)"
    if [ -d "$out/$stage.started" ]; then printf 'STARTED=1\n'; else printf 'STARTED=0\n'; fi
    if pgrep -f "p3_detach.sh RUN $stage( |\$)" >/dev/null 2>&1; then printf 'RUNNING=1\n'; else printf 'RUNNING=0\n'; fi
    printf 'LOG=%s\n' "$(tail -n 1 "$out/$stage.log" 2>/dev/null | cut -c1-160)"
    exit 0
    ;;
  *)
    echo "usage: p3_detach.sh START|RUN|STATUS STAGE [COMMAND [ARG...]]" >&2
    exit 2
    ;;
esac
