#!/usr/bin/env bash
# p3_ctl.sh COMMAND [ARGS]       (runs on the operator's Mac; talks to the always-on controller through IAP, docs/PAPER3_CONTROLLER.md)
#
#   status                 every open launch of the controller (stages and the QA view), the p3-* units, and a report-only reaper pass
#   logs [N]               the last N (default 60) journal lines of the controller's drivers and reaper
#   ssh [COMMAND...]       a shell on the controller as the OS Login user, or one command; `sudo -u p3ops -H -i` becomes the campaign user
#   pull HOST LAUNCH [DIR] copy rerun/paper3/HOST/launchN from the controller into DIR/rerun/paper3/... (default: this repository) and check
#                          it against its LAUNCH-SHA256SUMS; read-only on the controller, nothing is deleted there
#   ledger [DIR]           copy the controller's launch records and ledger.jsonl into DIR (default ~/p3ledger-controller), the P-BILLED inputs
#   snapshots [DIR]        copy the price snapshots taken on the controller (rerun/paper3/snapshots) into DIR/rerun/paper3 (default: this repository)
#   open-log LEASE LOGNAME [N]   the one sanctioned way to read an archived log: only a stage that failed and held its driver, every opening logged
#                          (p3_open_log.sh; design 7.3 exception); LOGNAME is the name the driver printed, for example bringup-a1.log
#   start LEASE [ARGS]     enable and start the driver unit of one launch record (p3-driver@LEASE); ARGS become its driver options, e.g. "--fresh"
#   stop LEASE             disable and stop that driver unit. It does NOT touch the instance: a Lambda host keeps billing until it is terminated
#                          (p3_teardown.sh on the controller, or the lease watchdog and reaper)
#
# Environment: P3_CONTROLLER (the instance name, must start with p3-controller-) and P3_CONTROLLER_ZONE are required; P3_CONTROLLER_PROJECT
# (default siriusmedai) is the project the controller lives in: deliberately not P3_GCP_PROJECT, which the campaign tools use for the campaign hosts,
# so a controller in a project of its own cannot redirect a campaign launch. Every call names that one instance and its zone and goes through the
# IAP tunnel (no address, no open port); nothing here lists, creates, changes or deletes any other resource.
set -uo pipefail
usage() { sed -n '2,/^set -uo pipefail/p' "$0" | sed '$d'; }
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
export CLOUDSDK_CORE_PROJECT=${P3_CONTROLLER_PROJECT:-siriusmedai}
die() { echo "p3_ctl.sh: $*" >&2; exit 2; }

[ $# -ge 1 ] || { usage; exit 2; }
CMD=$1; shift
NAME=${P3_CONTROLLER:-}; ZONE=${P3_CONTROLLER_ZONE:-}
[ -n "$NAME" ] && [ -n "$ZONE" ] || die "set P3_CONTROLLER (the instance name) and P3_CONTROLLER_ZONE"
[[ "$NAME" =~ ^p3-controller-[a-z0-9-]+$ ]] || die "refusing: '$NAME' is not a p3-controller-* instance name"
[[ "$ZONE" =~ ^[a-z]+-[a-z]+[0-9]+-[a-f]$ ]] || die "'$ZONE' does not look like a zone"

GSSH=(gcloud compute ssh "$NAME" --zone="$ZONE" --tunnel-through-iap --quiet)
remote() { "${GSSH[@]}" --command "$1"; }                       # one command on the controller; its stdout is the command's
as_p3ops() { remote "sudo -u p3ops -H bash -c $(printf '%q' "$1")"; }

case "$CMD" in
  status)
    as_p3ops 'cd "$HOME/study" || exit 1
      for r in "$HOME"/p3ledger/p3-*.json; do
        [ -f "$r" ] && jq -e ".terminate_confirmed_utc == null" "$r" >/dev/null 2>&1 || continue
        tools/paper3_campaign/p3_status.sh "$r"; echo
      done
      systemctl list-units "p3-*" --all --no-legend --no-pager
      tools/paper3_campaign/p3_reaper.sh; echo "reaper exit $?"'
    ;;
  logs)
    n=${1:-60}; [[ "$n" =~ ^[0-9]+$ ]] || die "logs takes a number of lines"
    remote "sudo journalctl -u 'p3-driver@*' -u p3-reaper.service -n $n --no-pager"
    ;;
  ssh)
    if [ $# -eq 0 ]; then "${GSSH[@]}"; else remote "$*"; fi
    ;;
  pull)
    [ $# -ge 2 ] || die "usage: pull HOST LAUNCH [DIR]"
    host=$1 launch=$2 dir=${3:-$REPO}
    [[ "$host" =~ ^(a100|h100|gh200|b200|c-gcp|c-vm)$ ]] && [[ "$launch" =~ ^[12]$ ]] || die "pull: HOST is a100 h100 gh200 b200 c-gcp c-vm, LAUNCH is 1 or 2"
    mkdir -p "$dir/rerun" || die "cannot create $dir/rerun"
    rm -f "$dir/rerun/paper3/$host/launch$launch/LAUNCH-SHA256SUMS"      # only the manifest that arrives now may vouch for what arrives now
    # stream a tar of the launch directory (without the duplicate archives of .incoming) over the IAP ssh channel
    remote "sudo tar -C /home/p3ops/study/rerun --exclude=.incoming -cf - paper3/$host/launch$launch" | tar -xf - -C "$dir/rerun" || die "the copy failed (nothing on the controller was changed; run it again)"
    cd "$dir/rerun/paper3/$host/launch$launch" 2>/dev/null || die "nothing arrived for $host launch $launch (has the driver finished it?)"
    [ -f LAUNCH-SHA256SUMS ] || die "arrived without LAUNCH-SHA256SUMS: the launch has not finished (the driver writes it last); the copy is partial"
    shasum -a 256 -c LAUNCH-SHA256SUMS > /dev/null && echo "verified: every file of $host launch $launch matches LAUNCH-SHA256SUMS" \
      || { echo "p3_ctl.sh: a file of $host launch $launch differs from LAUNCH-SHA256SUMS (the copy is damaged: run pull again)" >&2; exit 1; }
    ;;
  ledger)
    dir=${1:-$HOME/p3ledger-controller}
    mkdir -p "$dir" || die "cannot create $dir"
    remote "sudo tar -C /home/p3ops -cf - p3ledger" | tar -xf - -C "$dir" --strip-components=1 || die "the copy failed"
    echo "records and ledger.jsonl of the controller are in $dir (they hold the hosts' addresses: keep them private)"
    ;;
  snapshots)
    dir=${1:-$REPO}
    mkdir -p "$dir/rerun/paper3" || die "cannot create $dir/rerun/paper3"
    remote "sudo tar -C /home/p3ops/study/rerun -cf - paper3/snapshots" | tar -xf - -C "$dir/rerun" || die "the copy failed (nothing on the controller was changed)"
    echo "price snapshots copied to $dir/rerun/paper3/snapshots; each has a SHA256SUMS (cd into it and: shasum -a 256 -c SHA256SUMS)"
    ;;
  open-log)
    [ $# -ge 2 ] || die "usage: open-log LEASE LOGNAME [LINES]"
    lease=$1 name=$2 lines=${3:-}
    [[ "$lease" =~ ^p3-[A-Za-z0-9-]+$ ]] || die "'$lease' is not a lease name (p3-...)"
    [[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*\.log$ ]] || die "'$name' is not a log file name"
    [ -z "$lines" ] || [[ "$lines" =~ ^[0-9]+$ ]] || die "the last argument is a number of lines"
    as_p3ops "cd \"\$HOME/study\" && tools/paper3_campaign/p3_open_log.sh \"\$HOME/p3ledger/$lease.json\" $name${lines:+ --tail $lines}"
    ;;
  start|stop)
    [ $# -ge 1 ] || die "usage: $CMD LEASE [DRIVER-ARGS]"
    lease=$1; shift
    [[ "$lease" =~ ^p3-[A-Za-z0-9-]+$ ]] || die "'$lease' is not a lease name (p3-...)"
    if [ "$CMD" = stop ]; then
      remote "sudo systemctl disable --now p3-driver@$lease.service" && echo "driver of $lease stopped; the instance itself was not touched"
    else
      args="$*"
      [[ "$args" =~ ^[-A-Za-z0-9_=.\ /]*$ ]] || die "driver options may only use letters, digits, space and - _ = . /"
      # the options go to /etc/p3/driver-LEASE.env, which the unit reads; an empty ARGS clears them
      remote "printf 'P3_DRIVER_ARGS=\"%s\"\n' $(printf '%q' "$args") | sudo tee /etc/p3/driver-$lease.env > /dev/null && sudo systemctl enable --now p3-driver@$lease.service && systemctl is-active p3-driver@$lease.service"
    fi
    ;;
  *) usage; exit 2 ;;
esac
