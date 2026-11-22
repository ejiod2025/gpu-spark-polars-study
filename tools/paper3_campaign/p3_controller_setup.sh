#!/usr/bin/env bash
# p3_controller_setup.sh [--dry-run]      (as root, on the controller VM; idempotent; docs/PAPER3_CONTROLLER.md)
#
# Makes a stock Ubuntu 24.04 VM into the Paper 3 campaign controller. It is the VM's `startup-script` (so a reboot, a rebuilt VM or a repeated
# run lands in the same state) and it can be run by hand. It holds and writes no secret: the Lambda API key and the Lambda SSH key are put in
# place by a person afterwards (the step list in the document), and this script only reports whether they are there and how well guarded.
#
#   1. packages        git jq curl perl (shasum) python3 openssh-client tmux: what the drivers and the planner use; nothing else
#   2. user            p3ops, no password, no sudo; owns the keys, the ledger (~/p3ledger) and the repository (~/study)
#   3. repository      a detached checkout of exactly P3_COMMIT (the campaign commit; or the instance metadata attribute p3-commit), cloned from
#                      P3_REPO_URL (public GitHub); refuses to move a checkout that has local changes
#   4. units           the systemd units of tools/paper3_campaign/controller/ copied from that checkout; the reaper timer enabled (it does
#                      nothing until the API key exists); driver units are enabled per launch by a person
#   5. report          key files present and their modes; unit states
#
# Environment: P3_COMMIT (40 hex; default: metadata attribute p3-commit; empty = no checkout yet), P3_REPO_URL, P3_USER (p3ops).
# Tests and containers: P3_SKIP_APT=1 skips package installation, P3_SKIP_SYSTEMD=1 skips systemctl.
set -euo pipefail
usage() { sed -n '2,/^set -euo pipefail/p' "$0" | sed '$d'; }
DRY=0
case "${1:-}" in --dry-run) DRY=1 ;; "") ;; *) usage; exit 2 ;; esac
[ "$DRY" = 1 ] || [ "$(id -u)" = 0 ] || { echo "p3_controller_setup.sh: run as root (or use --dry-run)" >&2; exit 2; }

P3_USER=${P3_USER:-p3ops}
REPO_URL=${P3_REPO_URL:-https://github.com/ejiod2025/gpu-spark-polars-study.git}
COMMIT=${P3_COMMIT:-}
if [ -z "$COMMIT" ] && [ "$DRY" != 1 ]; then
  COMMIT=$(curl -fsS -m 5 -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/attributes/p3-commit 2>/dev/null || true)
fi
if [ -n "$COMMIT" ] && ! printf '%s' "$COMMIT" | grep -Eq '^[0-9a-f]{40}$'; then echo "p3_controller_setup.sh: P3_COMMIT must be a full 40-character commit hash" >&2; exit 2; fi
PACKAGES="git jq curl perl libdigest-sha-perl python3 openssh-client tmux ca-certificates"
HOME_DIR=/home/$P3_USER
STUDY=$HOME_DIR/study
note() { echo "$(date -u +%FT%TZ) p3_controller_setup: $*"; }
run() { if [ "$DRY" = 1 ]; then echo "DRY RUN: $*"; else "$@"; fi; }
u() { runuser -u "$P3_USER" -- env HOME="$HOME_DIR" "$@"; }       # the campaign user with its own HOME (runuser keeps root's)
as_user() { if [ "$DRY" = 1 ]; then echo "DRY RUN (as $P3_USER): $*"; else u "$@"; fi; }

# ---- 1. packages ----------------------------------------------------------------------------------------------------------
if [ -z "${P3_SKIP_APT:-}" ]; then
  note "packages: $PACKAGES"
  run env DEBIAN_FRONTEND=noninteractive apt-get update -qq
  # shellcheck disable=SC2086
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $PACKAGES
fi

# ---- 2. the campaign user -------------------------------------------------------------------------------------------------
if [ "$DRY" = 1 ] || ! id "$P3_USER" >/dev/null 2>&1; then run useradd --create-home --shell /bin/bash "$P3_USER"; fi
run install -d -m 0700 -o "$P3_USER" -g "$P3_USER" "$HOME_DIR/.ssh" "$HOME_DIR/.config" "$HOME_DIR/.config/lambda" "$HOME_DIR/p3ledger"
run chmod 0700 "$HOME_DIR"
run install -d -m 0755 /etc/p3
for f in driver.env reaper.env; do
  [ "$DRY" != 1 ] && [ -e "/etc/p3/$f" ] && continue
  run install -m 0644 /dev/null "/etc/p3/$f"
done
# the operator's login shell as p3ops reads the knobs of the launch commands (names and flags only, no secret)
if [ "$DRY" = 1 ] || [ ! -e /etc/p3/operator.env ]; then
  if [ "$DRY" = 1 ]; then echo "DRY RUN: write /etc/p3/operator.env"; else cat > /etc/p3/operator.env <<'EOF'
# Read by the interactive shell of p3ops (~/.bashrc): the launch knobs of p3_plan.py launch-spec (docs/PAPER3_CONTROLLER.md). Names and flags only.
# The name of the SSH key as registered at Lambda (its private half is ~p3ops/.ssh/lambda_key22), instead of the Mac's Key22:
#export P3_LAMBDA_SSH_KEY_NAME=p3-controller-key
EOF
  fi
fi
if [ "$DRY" = 1 ]; then echo "DRY RUN: source /etc/p3/operator.env from ~$P3_USER/.bashrc"
elif ! grep -q 'p3 operator.env' "$HOME_DIR/.bashrc" 2>/dev/null; then
  printf '\n# p3 operator.env\nif [ -f /etc/p3/operator.env ]; then set -a; . /etc/p3/operator.env; set +a; fi\n' >> "$HOME_DIR/.bashrc"
  chown "$P3_USER:$P3_USER" "$HOME_DIR/.bashrc"
fi

# ---- 3. the repository at exactly the campaign commit ----------------------------------------------------------------------
if [ -z "$COMMIT" ]; then
  note "no P3_COMMIT / metadata p3-commit: the repository is not checked out yet (set it and run this script again)"
else
  if [ "$DRY" = 1 ]; then
    echo "DRY RUN: clone $REPO_URL to $STUDY (or fetch there), git checkout --detach $COMMIT, check HEAD and a clean tree"
  else
    [ -d "$STUDY/.git" ] || as_user git clone --quiet "$REPO_URL" "$STUDY"
    if [ -n "$(u git -C "$STUDY" status --porcelain)" ]; then
      echo "p3_controller_setup.sh: $STUDY has local changes; not moving it (inspect, then git -C $STUDY checkout -- . )" >&2; exit 1
    fi
    # bash reads a script as it runs it: never move the checkout under a live driver
    if [ "$(u git -C "$STUDY" rev-parse HEAD)" != "$COMMIT" ] && [ -d /run/systemd/system ] && systemctl list-units 'p3-driver@*' --state=active --no-legend | grep -q .; then
      echo "p3_controller_setup.sh: a driver is running; not moving $STUDY to $COMMIT (stop it with p3_ctl.sh stop, or wait for its launch to end)" >&2; exit 1
    fi
    as_user git -C "$STUDY" fetch --quiet --tags origin
    as_user git -C "$STUDY" checkout --quiet --detach "$COMMIT"
    [ "$(u git -C "$STUDY" rev-parse HEAD)" = "$COMMIT" ] || { echo "p3_controller_setup.sh: HEAD is not $COMMIT after the checkout" >&2; exit 1; }
    note "repository at $COMMIT"
  fi
fi

# ---- 4. systemd units -----------------------------------------------------------------------------------------------------
UNITS=$STUDY/tools/paper3_campaign/controller
if [ "$DRY" != 1 ] && [ ! -d "$UNITS" ]; then
  note "no $UNITS yet (no checkout): units not installed"
else
  for unit in 'p3-driver@.service' p3-reaper.service p3-reaper.timer; do run install -m 0644 "$UNITS/$unit" "/etc/systemd/system/$unit"; done
  if [ -n "${P3_SKIP_SYSTEMD:-}" ] || { [ "$DRY" != 1 ] && [ ! -d /run/systemd/system ]; }; then
    note "systemd is not running here (or P3_SKIP_SYSTEMD): units copied, not enabled"
  else
    run systemctl daemon-reload
    run systemctl enable --now p3-reaper.timer
  fi
fi

# ---- 5. report ------------------------------------------------------------------------------------------------------------
if [ "$DRY" != 1 ]; then
  for f in "$HOME_DIR/.config/lambda/api_key" "$HOME_DIR/.ssh/lambda_key22"; do
    if [ -f "$f" ]; then
      mode=$(stat -c %a "$f"); note "$(basename "$f"): present, mode $mode, owner $(stat -c %U "$f")"
      case "$mode" in 600|400) ;; *) note "WARNING: $(basename "$f") must be mode 600 (chmod 600 $f)" ;; esac
    else note "$(basename "$f"): MISSING (a person puts it there; see the document)"; fi
  done
fi
note "done"
