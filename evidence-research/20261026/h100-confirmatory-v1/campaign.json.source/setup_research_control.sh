#!/usr/bin/env bash
# Create the confirmatory CPU 2.0 control with the primary CPython interpreter.
# Existing environments are validated, never repaired or upgraded in place.
set -euo pipefail
RESEARCH_SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESEARCH_PRIMARY_PYTHON="${1:-/home/ubuntu/study/.venv/bin/python}"
RESEARCH_CPU_VENV="${2:-/home/ubuntu/study/.venv-research-cpu200}"
RESEARCH_UV="${3:-/home/ubuntu/.local/bin/uv}"
RESEARCH_PYTHON_BASE="$(readlink -f "$RESEARCH_PRIMARY_PYTHON")"
"$RESEARCH_PRIMARY_PYTHON" -c 'import sys; assert sys.version_info[:3] == (3,11,13), sys.version'
if [ ! -e "$RESEARCH_CPU_VENV" ]; then
  "$RESEARCH_UV" venv --python "$RESEARCH_PYTHON_BASE" "$RESEARCH_CPU_VENV"
  "$RESEARCH_UV" pip install --python "$RESEARCH_CPU_VENV/bin/python" -r "$RESEARCH_SOURCE_DIR/requirements-research-cpu200.txt"
fi
"$RESEARCH_UV" pip check --python "$RESEARCH_CPU_VENV/bin/python"
"$RESEARCH_CPU_VENV/bin/python" - "$RESEARCH_PRIMARY_PYTHON" <<'PY'
import importlib.metadata as metadata
import json
from pathlib import Path
import subprocess
import sys
common = ['duckdb','numpy','packaging','psutil','py4j','pyarrow','pyspark','typing-extensions']
probe = 'import sys,importlib.metadata as m,json; print(json.dumps({"python":sys.version,"packages":{n:m.version(n) for n in '+repr(common)+'}}))'
primary = json.loads(subprocess.check_output([sys.argv[1], '-c', probe], text=True))
control = json.loads(subprocess.check_output([sys.executable, '-c', probe], text=True))
assert primary == control, (primary, control)
assert metadata.version('polars') == '2.0.0'
assert metadata.version('polars-runtime-32') == '2.0.0'
assert Path(sys.executable).resolve() == Path(sys.argv[1]).resolve()
print(json.dumps({'status':'PASS','common_runtime':control,'control_polars':metadata.version('polars'),
                  'base_executable':str(Path(sys.executable).resolve())}, indent=2))
PY
