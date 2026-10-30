#!/usr/bin/env bash
# Idempotent bring-up for the Lambda gpu_1x_h100_sxm5 host (x86_64) or gpu_1x_gh200 host (aarch64),
# Ubuntu 22.04 Lambda Stack image.
#
# Safe to re-run: every step checks state first and nothing existing is deleted or overwritten
# (a wrong-Python .venv, a jar with a bad checksum, or too little disk stops the script instead).
#
#   ./setup_h100.sh --dry-run        print the plan and the read-only checks; change nothing
#   ./setup_h100.sh                  do it
#
# Steps: inventory log -> GPU/driver check -> apt (OpenJDK 17, only if missing) -> uv ->
# virtualenv(s) with pinned packages -> RAPIDS Spark jar (download if absent, sha256 against
# evidence/DEPENDENCY_SHA256.txt) -> data-volume selection and a source-able environment file.
#
# Two environments, both built from pin files (nothing is hard-coded to one Polars version):
#   gpu         .venv              Python 3.11, evidence/requirements.lock.txt minus its Polars pins
#                                  + requirements-gpu.txt (Polars 1.44.2 + cudf-polars-cu12 26.10.2)
#   a100compat  .venv-a100compat   Python 3.10, evidence/requirements.lock.txt exactly (Polars 2.0.0):
#                                  CPU engines (polars, spark_cpu, spark_gpu) with the A100-record stack
#
# Architecture: chosen from `uname -m`. The package VERSIONS are identical on x86_64 and aarch64; only
# platform artifacts differ (docs/POLARS_GPU_COMPAT.md, "GH200 / aarch64"):
#   aarch64  RAPIDS jar rapids-4-spark_2.12-<ver>-cuda12-arm64.jar (sha256 recorded below), JDK
#            /usr/lib/jvm/java-17-openjdk-arm64, GPU name GH200, GPU pins with sha256 from
#            requirements-gpu-aarch64.txt, and a platform-artifact record in the setup log directory.
#   x86_64   unchanged: unclassified (CUDA 12) jar, java-17-openjdk-amd64, GPU name H100.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_VERSION="3.11"                 # gpu env; cudf-polars >= 26.4 needs >= 3.11 (docs/POLARS_GPU_COMPAT.md)
A100COMPAT_PYTHON="3.10"              # Python of the A100 record (3.10.12)
A100COMPAT_VENV="$REPO_DIR/.venv-a100compat"
ENVS="gpu"                            # gpu | a100compat | both
UV_VERSION="0.8.13"                   # same uv that resolved requirements-gpu.txt
VENV="$REPO_DIR/.venv"
LOCK_FILE="$REPO_DIR/evidence/requirements.lock.txt"
GPU_REQUIREMENTS="$REPO_DIR/requirements-gpu.txt"
JAR_VERSION="26.08.1"
# Platform-specific artifacts. The aarch64 jar is Maven Central's `-cuda12-arm64` classifier of the same
# release (979,914,456 bytes; sha1 9cb2c720961e8514ab8599f4ca0d4eff40ee8ee6 and md5 from Maven match).
# Its sha256 is recorded here because evidence/DEPENDENCY_SHA256.txt only lists the x86_64 jar; an entry
# for it in that file is honoured and must agree with this value.
ARCH="$(uname -m)"
JAR_CLASSIFIER=""                     # x86_64: the unclassified jar is the CUDA 12 build
JAR_SHA_BUILTIN=""
JDK_ARCH="amd64"
EXPECT_GPU="H100"
case "$ARCH" in
  aarch64|arm64)
    JAR_CLASSIFIER="-cuda12-arm64"
    JAR_SHA_BUILTIN="44ac76f748b2d1091dcb057eb8effa624967622cdf648ff8d0940f7f19dd4735"
    JDK_ARCH="arm64"
    EXPECT_GPU="GH200"
    GPU_REQUIREMENTS="$REPO_DIR/requirements-gpu-aarch64.txt"
    ;;
esac
JAR_NAME="rapids-4-spark_2.12-${JAR_VERSION}${JAR_CLASSIFIER}.jar"
JAR_URL="${RAPIDS_JAR_URL:-https://repo.maven.apache.org/maven2/com/nvidia/rapids-4-spark_2.12/${JAR_VERSION}/${JAR_NAME}}"
JAR_DIR="$REPO_DIR/jars"
SHA_FILE="$REPO_DIR/evidence/DEPENDENCY_SHA256.txt"
MIN_FREE_GIB=500                      # SF300 Parquet is ~108 GB; v1 shuffle exhausted a smaller disk
MIN_DRIVER="525.60.13"                # documented minimum for CUDA 12 RAPIDS wheels and the cuda12 jar
LOG_DIR="$REPO_DIR/rerun/setup-logs"
DATA_ROOT_OVERRIDE=""
ENV_FILE="$REPO_DIR/rerun/h100-env.sh"
DRY_RUN=0
ALLOW_ANY_GPU=0
SKIP_APT=0
SKIP_PYTHON=0
SKIP_JAR=0

usage() {
  sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  cat <<'EOF'

Options:
  --dry-run              print actions, run only read-only checks
  --envs gpu|a100compat|both   which environment(s) to build (default gpu)
  --python X.Y           Python of the gpu env (default 3.11)
  --venv PATH            gpu env location (default <repo>/.venv)
  --a100compat-python X.Y   Python of the a100compat env (default 3.10)
  --a100compat-venv PATH    a100compat env location (default <repo>/.venv-a100compat)
  --gpu-requirements F   pin file layered over the lock in the gpu env
                         (default requirements-gpu.txt; requirements-gpu-aarch64.txt on aarch64)
  --data-root PATH       use this dataset directory instead of auto-selecting a volume
  --min-free-gib N       required free space on the data volume (default 500)
  --expect-gpu NAME      required substring of the GPU name (default H100; GH200 on aarch64)
  --allow-any-gpu        accept a GPU whose name does not contain the expected name (rehearsal)
  --skip-apt | --skip-python | --skip-jar
  -h, --help
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --envs) ENVS="$2"; shift ;;
    --python) PYTHON_VERSION="$2"; shift ;;
    --venv) VENV="$2"; shift ;;
    --a100compat-python) A100COMPAT_PYTHON="$2"; shift ;;
    --a100compat-venv) A100COMPAT_VENV="$2"; shift ;;
    --gpu-requirements) GPU_REQUIREMENTS="$2"; shift ;;
    --data-root) DATA_ROOT_OVERRIDE="$2"; shift ;;
    --min-free-gib) MIN_FREE_GIB="$2"; shift ;;
    --expect-gpu) EXPECT_GPU="$2"; shift ;;
    --allow-any-gpu) ALLOW_ANY_GPU=1 ;;
    --skip-apt) SKIP_APT=1 ;;
    --skip-python) SKIP_PYTHON=1 ;;
    --skip-jar) SKIP_JAR=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

case "$ENVS" in gpu|a100compat|both) ;; *) echo "--envs must be gpu, a100compat or both" >&2; exit 2 ;; esac
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WOULD_FAIL=0
SUMMARY=()
TEMP_FILES=()
cleanup() { if [ "${#TEMP_FILES[@]}" -gt 0 ]; then rm -f "${TEMP_FILES[@]}"; fi; }
trap cleanup EXIT

if [ "$DRY_RUN" -eq 0 ]; then
  mkdir -p "$LOG_DIR"
  exec > >(tee -a "$LOG_DIR/setup-$STAMP.log") 2>&1
fi

say()  { printf '[setup] %s\n' "$*"; }
note() { SUMMARY+=("$*"); }

# Mutating commands go through act(): printed in dry-run, executed otherwise.
act() {
  if [ "$DRY_RUN" -eq 1 ]; then printf '[dry-run] would run:'; printf ' %q' "$@"; printf '\n'; return 0; fi
  printf '[setup] + '; printf '%q ' "$@"; printf '\n'
  "$@"
}

# Failures abort for real, but only report in dry-run so the whole plan can be reviewed on any machine.
die() {
  if [ "$DRY_RUN" -eq 1 ]; then say "WOULD FAIL here: $*"; WOULD_FAIL=$((WOULD_FAIL + 1)); return 0; fi
  echo "[setup] ERROR: $*" >&2
  exit 1
}

have() { command -v "$1" >/dev/null 2>&1; }

sudo_cmd() { if [ "$(id -u)" -eq 0 ]; then "$@"; elif have sudo; then sudo "$@"; else die "need root for: $*"; fi; }

sha256_of() {
  if have sha256sum; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

# version_ge A B: true if dotted version A >= B
version_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]; }

# ---------------------------------------------------------------------------------------------
say "repo: $REPO_DIR   dry-run: $DRY_RUN   utc: $STAMP"
[ -f "$LOCK_FILE" ] || die "missing $LOCK_FILE"
case "$ENVS" in gpu|both) [ -f "$GPU_REQUIREMENTS" ] || die "missing $GPU_REQUIREMENTS" ;; esac
[ -f "$SHA_FILE" ] || die "missing $SHA_FILE"
if [ "$(uname -s)" != "Linux" ]; then die "this script targets the Linux GPU host (found $(uname -s))"; fi
case "$ARCH" in x86_64|aarch64|arm64) ;; *) die "unsupported architecture '$ARCH' (x86_64 and aarch64 only)" ;; esac
[ -z "$JAR_CLASSIFIER" ] || say "architecture $ARCH: GH200 / aarch64 path (jar $JAR_NAME, JDK java-17-openjdk-$JDK_ARCH, GPU pins $(basename "$GPU_REQUIREMENTS"))"

# 1. inventory -------------------------------------------------------------------------------
inventory() {
  local cmd
  for cmd in "nvidia-smi" "nvidia-smi -q -d MEMORY,CLOCK,PERFORMANCE" "lscpu" "free -h" "df -hP" "lsblk" \
             "cat /etc/os-release" "uname -a" "nproc" "python3 --version" "java -version" "uv --version"; do
    printf '\n===== %s =====\n' "$cmd"
    # shellcheck disable=SC2086
    $cmd 2>&1 || echo "(unavailable: exit $?)"
  done
}
if [ "$DRY_RUN" -eq 1 ]; then
  say "would record inventory (nvidia-smi, lscpu, free, df, lsblk, os-release, versions) to $LOG_DIR/inventory-$STAMP.log"
else
  inventory > "$LOG_DIR/inventory-$STAMP.log" 2>&1 || true
  say "inventory: $LOG_DIR/inventory-$STAMP.log"
fi
note "inventory log: ${LOG_DIR}/inventory-${STAMP}.log"

# 2. GPU and driver ----------------------------------------------------------------------------
if have nvidia-smi && GPU_LINE="$(nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>/dev/null | head -n1)" && [ -n "$GPU_LINE" ]; then
  GPU_NAME="$(echo "$GPU_LINE" | cut -d, -f1 | sed 's/^ *//;s/ *$//')"
  DRIVER="$(echo "$GPU_LINE" | cut -d, -f2 | tr -d ' ')"
  say "GPU: $GPU_LINE"
  if [ "$EXPECT_GPU" = "H100" ]; then EXPECT_DESC="an H100"; else EXPECT_DESC="a $EXPECT_GPU"; fi
  case "$GPU_NAME" in
    *"$EXPECT_GPU"*) : ;;
    *) if [ "$ALLOW_ANY_GPU" -eq 1 ]; then say "WARNING: GPU is not $EXPECT_DESC (--allow-any-gpu)"; else die "GPU '$GPU_NAME' is not $EXPECT_DESC (use --allow-any-gpu for a rehearsal)"; fi ;;
  esac
  version_ge "$DRIVER" "$MIN_DRIVER" || die "driver $DRIVER < $MIN_DRIVER required by the CUDA 12 wheels/jar"
  note "GPU: $GPU_NAME, driver $DRIVER"
else
  die "nvidia-smi missing or reports no GPU"
fi

# 3. apt: OpenJDK 17 only if missing --------------------------------------------------------------
JAVA_OK=0
if have java && java -version 2>&1 | head -n1 | grep -q '"17\.'; then JAVA_OK=1; fi
if [ "$SKIP_APT" -eq 1 ]; then
  say "apt skipped (--skip-apt)"
elif [ "$JAVA_OK" -eq 1 ]; then
  say "Java 17 present: $(java -version 2>&1 | head -n1)"
else
  say "Java 17 missing; installing openjdk-17-jre-headless"
  act sudo_cmd apt-get update -y
  act sudo_cmd env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openjdk-17-jre-headless ca-certificates curl
fi
JAVA_HOME_GUESS="/usr/lib/jvm/java-17-openjdk-$JDK_ARCH"
[ "$DRY_RUN" -eq 1 ] || [ -x "$JAVA_HOME_GUESS/bin/java" ] || die "expected $JAVA_HOME_GUESS/bin/java after install"
note "Java 17: $JAVA_HOME_GUESS"

# 4. uv and the virtualenv(s) -------------------------------------------------------------------
export PATH="$HOME/.local/bin:$PATH"

# build_env NAME VENV PYTHON_VERSION [EXTRA_PIN_FILE]
#   NAME gpu | a100compat. With an EXTRA_PIN_FILE that pins polars, the lock's Polars lines are dropped
#   and the file overrides them; installed in ONE resolver call so conflicts fail loudly.
build_env() {
  local name="$1" venv="$2" pyver="$3" extra="${4:-}" venv_py have_py filtered
  venv_py="$venv/bin/python"
  if [ -e "$venv_py" ]; then
    if ! have_py="$("$venv_py" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)"; then
      die "$venv exists but its interpreter does not run on this host (copied from another machine?); not touching it. Use another path or remove it yourself"
      have_py="$pyver"
    fi
    if [ "$have_py" != "$pyver" ]; then
      die "$venv already exists with Python $have_py (need $pyver); not touching it. Use a different path"
    fi
    say "[$name] venv present: $venv (Python $have_py)"
  else
    act uv venv --python "$pyver" "$venv"
  fi
  filtered="$(mktemp "${TMPDIR:-/tmp}/lock-$name.XXXXXX")"
  TEMP_FILES+=("$filtered")
  local installs=(-r "$filtered")
  if [ -n "$extra" ]; then
    if grep -qE '^polars==' "$extra"; then
      grep -vE '^(polars|polars-runtime-32)==' "$LOCK_FILE" > "$filtered"
      say "[$name] lock layered under $(basename "$extra"): its polars/polars-runtime-32 pins are dropped from the lock"
    else
      cp "$LOCK_FILE" "$filtered"
    fi
    installs+=(-r "$extra")
  else
    cp "$LOCK_FILE" "$filtered"
  fi
  act uv pip install --python "$venv_py" "${installs[@]}"
  act uv pip check --python "$venv_py"

  # Verify every pin is what is installed (a half-applied install must not look like success).
  if [ "$DRY_RUN" -eq 0 ]; then
    "$venv_py" - "${installs[@]:1:1}" ${extra:+"$extra"} <<'PYEOF'
import re, sys
from importlib import metadata
pins = {}
for path in sys.argv[1:]:
    for line in open(path):
        m = re.match(r"^([A-Za-z0-9_.-]+)==([^\s#;]+)", line.strip())
        if m:
            pins[re.sub(r"[-_.]+", "-", m.group(1)).lower()] = m.group(2)
bad = []
for name, want in sorted(pins.items()):
    try:
        have = metadata.version(name)
    except metadata.PackageNotFoundError:
        bad.append(f"{name}: not installed (want {want})")
        continue
    if have != want:
        bad.append(f"{name}: installed {have}, want {want}")
if bad:
    sys.exit("pin mismatch:\n  " + "\n  ".join(bad))
print(f"[setup] all {len(pins)} pins match the installed packages")
PYEOF
    "$venv_py" -c "import polars, pyspark, duckdb, pyarrow, psutil; print('[setup] imports ok: polars', polars.__version__, 'pyspark', pyspark.__version__)"
    if [ "$name" = "gpu" ]; then
      "$venv_py" -c "import cudf_polars; print('[setup] cudf_polars importable:', cudf_polars.__version__)" \
        || die "cudf_polars failed to import (driver/CUDA problem?)"
    fi
  else
    say "[$name] dry-run: would verify all pins against the installed packages and import the engines"
  fi
  note "env $name: $venv (Python $pyver), pins from $(basename "$LOCK_FILE")${extra:+ minus Polars + $(basename "$extra")}"
}

if [ "$SKIP_PYTHON" -eq 1 ]; then
  say "python environments skipped (--skip-python)"
else
  if ! have uv; then
    say "uv missing; installing uv==$UV_VERSION with pip --user (no curl|sh)"
    have python3 || die "python3 missing"
    python3 -m pip --version >/dev/null 2>&1 || act sudo_cmd apt-get install -y python3-pip
    act python3 -m pip install --user "uv==$UV_VERSION" || die "could not install uv; install uv $UV_VERSION manually and re-run"
  else
    say "uv present: $(uv --version)"
  fi
  case "$ENVS" in gpu|both) build_env gpu "$VENV" "$PYTHON_VERSION" "$GPU_REQUIREMENTS" ;; esac
  case "$ENVS" in a100compat|both) build_env a100compat "$A100COMPAT_VENV" "$A100COMPAT_PYTHON" ;; esac
fi

# 5. RAPIDS Spark jar ---------------------------------------------------------------------------
JAR_PATH="$JAR_DIR/$JAR_NAME"
EXPECTED_SHA="$(awk -v n="jars/$JAR_NAME" '$2==n {print $1}' "$SHA_FILE")"
if [ -n "$JAR_SHA_BUILTIN" ]; then
  if [ -z "$EXPECTED_SHA" ]; then
    EXPECTED_SHA="$JAR_SHA_BUILTIN"
  elif [ "$EXPECTED_SHA" != "$JAR_SHA_BUILTIN" ]; then
    die "$SHA_FILE lists $EXPECTED_SHA for jars/$JAR_NAME but this script records $JAR_SHA_BUILTIN; resolve before running"
  fi
fi
[ -n "$EXPECTED_SHA" ] || die "no checksum for jars/$JAR_NAME in $SHA_FILE"
if [ "$SKIP_JAR" -eq 1 ]; then
  say "jar skipped (--skip-jar)"
else
  if [ -f "$JAR_PATH" ]; then
    ACTUAL_SHA="$(sha256_of "$JAR_PATH")"
    [ "$ACTUAL_SHA" = "$EXPECTED_SHA" ] || die "existing $JAR_PATH has sha256 $ACTUAL_SHA, expected $EXPECTED_SHA (not deleting it; move it aside and re-run)"
    say "jar present and verified: $JAR_PATH"
  else
    act mkdir -p "$JAR_DIR"
    PARTIAL="$JAR_DIR/.$JAR_NAME.partial"
    act curl -fL --retry 3 --retry-delay 5 -o "$PARTIAL" "$JAR_URL"
    if [ "$DRY_RUN" -eq 0 ]; then
      ACTUAL_SHA="$(sha256_of "$PARTIAL")"
      if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then
        rm -f "$PARTIAL"
        die "downloaded jar sha256 $ACTUAL_SHA != expected $EXPECTED_SHA (partial file removed)"
      fi
      mv "$PARTIAL" "$JAR_PATH"
      say "jar downloaded and verified: $JAR_PATH"
    else
      say "dry-run: would verify sha256 == $EXPECTED_SHA, then move into place"
    fi
  fi
  note "jar: $JAR_PATH (sha256 $EXPECTED_SHA)"
fi

# 6. data volume --------------------------------------------------------------------------------
avail_kib() { df -Pk "$1" 2>/dev/null | awk 'NR==2 {print $4}'; }
device_of() { df -P "$1" 2>/dev/null | awk 'NR==2 {print $1}'; }

pick_volume() {
  # largest writable real filesystem: prints "<avail_kib> <mountpoint>"
  df -Pk 2>/dev/null | awk 'NR>1 && $1 !~ /^(tmpfs|devtmpfs|overlay|squashfs|udev|none|efivarfs)$/ && $1 !~ /^\/dev\/loop/ \
      && $6 !~ /^\/(boot|snap|sys|proc|run|dev)/ {print $4, $6}' | sort -rn | while read -r avail mount; do
        if [ -d "$mount" ] && [ -w "$mount" ]; then echo "$avail $mount"; break; fi
      done
}

if [ -n "$DATA_ROOT_OVERRIDE" ]; then
  DATA_ROOT="$DATA_ROOT_OVERRIDE"
else
  BEST="$(pick_volume)"
  BEST_AVAIL="${BEST%% *}"; BEST_MOUNT="${BEST#* }"
  REPO_AVAIL="$(avail_kib "$REPO_DIR")"
  say "largest writable volume: ${BEST_MOUNT:-none} ($(( ${BEST_AVAIL:-0} / 1024 / 1024 )) GiB free); repo volume: $(( ${REPO_AVAIL:-0} / 1024 / 1024 )) GiB free"
  # Lambda on-demand: the 2.75 TiB root volume holds /home/ubuntu, so the repo's data/ is normally right.
  if [ -n "${REPO_AVAIL:-}" ] && [ "${REPO_AVAIL:-0}" -ge $(( ${BEST_AVAIL:-0} * 9 / 10 )) ]; then
    DATA_ROOT="$REPO_DIR/data"
  elif [ -n "${BEST_MOUNT:-}" ]; then
    DATA_ROOT="$BEST_MOUNT/gpu-spark-polars-study/data"
  else
    DATA_ROOT="$REPO_DIR/data"
  fi
fi
act mkdir -p "$DATA_ROOT"
CHECK_PATH="$DATA_ROOT"; [ -d "$CHECK_PATH" ] || CHECK_PATH="$(dirname "$DATA_ROOT")"
[ -d "$CHECK_PATH" ] || CHECK_PATH="$REPO_DIR"
FREE_GIB=$(( $(avail_kib "$CHECK_PATH") / 1024 / 1024 ))
say "data root: $DATA_ROOT  (${FREE_GIB} GiB free on its volume, need >= ${MIN_FREE_GIB})"
[ "$FREE_GIB" -ge "$MIN_FREE_GIB" ] || die "only ${FREE_GIB} GiB free on the data volume; need ${MIN_FREE_GIB} GiB (SF300 data ~108 GB plus Spark shuffle)"

if [ "$(device_of /tmp)" = "$(device_of "$CHECK_PATH")" ]; then
  SPARK_DIRS_LINE="# Spark's default scratch (/tmp) is on the data volume; no SPARK_LOCAL_DIRS override needed."
else
  SPARK_DIRS="$(dirname "$DATA_ROOT")/spark-local"
  SPARK_DIRS_LINE="export SPARK_LOCAL_DIRS=\"$SPARK_DIRS\"   # /tmp is on a different, smaller volume; query_cleanup.py honours this variable"
  act mkdir -p "$SPARK_DIRS"
fi
note "data root: $DATA_ROOT (${FREE_GIB} GiB free)"

ENV_TEXT="# generated by setup_h100.sh $STAMP -- source this before any benchmark command
export JAVA_HOME=\"$JAVA_HOME_GUESS\"
export PATH=\"\$JAVA_HOME/bin:$VENV/bin:\$HOME/.local/bin:\$PATH\"
export PYTHON_GPU_ENV=\"$VENV/bin/python\"          # polars 1.44.2 + cudf-polars (all four engines)
export PYTHON_A100COMPAT_ENV=\"$A100COMPAT_VENV/bin/python\"   # polars 2.0.0, Python 3.10 (CPU-Polars continuity)
export DATA_ROOT=\"$DATA_ROOT\"
export RAPIDS_JAR=\"$JAR_PATH\"
$SPARK_DIRS_LINE
"
if [ "$DRY_RUN" -eq 1 ]; then
  say "would write $ENV_FILE:"; printf '%s' "$ENV_TEXT" | sed 's/^/    /'
else
  mkdir -p "$(dirname "$ENV_FILE")"
  printf '%s' "$ENV_TEXT" > "$ENV_FILE"
  say "environment file: $ENV_FILE   (source it)"
fi

# 6b. aarch64: record the platform-specific artifacts (amendment A3 of docs/H100_EXPERIMENT_DESIGN.md) ----
# Wheels are pinned by version and sha256 in requirements-gpu-aarch64.txt (GPU stack) and listed in
# docs/POLARS_GPU_COMPAT.md; this file records what is on the host: JDK build, jar, interpreters, packages.
record_platform_artifacts() {
  local out="$LOG_DIR/platform-artifacts-$STAMP.txt" jf venv_py
  note "platform artifacts: $out"
  if [ "$DRY_RUN" -eq 1 ]; then
    say "would record JDK package and java/libjvm sha256, glibc, jar sha256, uv, interpreters and pip freeze to $out"
    return 0
  fi
  {
    echo "# platform artifacts recorded by setup_h100.sh $STAMP"
    echo "machine: $ARCH   kernel: $(uname -sr)   $(ldd --version 2>&1 | head -n1)"
    echo "jdk package: $(dpkg-query -W -f='${Package} ${Version} ${Architecture}' openjdk-17-jre-headless 2>/dev/null || echo 'not installed via dpkg')"
    for jf in "$JAVA_HOME_GUESS/bin/java" "$JAVA_HOME_GUESS/lib/server/libjvm.so"; do
      if [ -f "$jf" ]; then echo "sha256 $(sha256_of "$jf")  $jf"; fi
    done
    if [ -f "$JAR_PATH" ]; then echo "sha256 $(sha256_of "$JAR_PATH")  $JAR_PATH"; fi
    echo "uv: $(uv --version 2>&1)"
    for venv_py in "$VENV/bin/python" "$A100COMPAT_VENV/bin/python"; do
      if [ -x "$venv_py" ]; then
        echo "== $venv_py"
        "$venv_py" -c 'import os, sys; print(os.path.realpath(sys.executable)); print(sys.version)'
        echo "sha256 $(sha256_of "$("$venv_py" -c 'import os, sys; print(os.path.realpath(sys.executable))')")  (interpreter)"
        uv pip freeze --python "$venv_py"
      fi
    done
  } > "$out" 2>&1
  say "platform artifacts: $out"
}
if [ -n "$JAR_CLASSIFIER" ]; then record_platform_artifacts; fi

# 7. summary ------------------------------------------------------------------------------------
echo
if [ "$DRY_RUN" -eq 1 ]; then
  say "DRY RUN COMPLETE: $WOULD_FAIL prerequisite check(s) would fail on this host."
  for line in "${SUMMARY[@]}"; do say "  plan: $line"; done
  exit 0
fi
say "OK -- setup complete"
for line in "${SUMMARY[@]}"; do say "  $line"; done
say "next: source $ENV_FILE && python preflight.py --data-root \"\$DATA_ROOT\""
