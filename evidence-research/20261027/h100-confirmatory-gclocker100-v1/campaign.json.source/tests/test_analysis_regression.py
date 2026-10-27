"""The analyzers must reproduce the commit-06850ed (three-engine) outputs byte for byte on the saved A100 evidence.

Both versions run on the curated evidence with datetime.now() frozen (generated_at_utc is the only
nondeterministic field) and their output directories are compared file by file. This is the README
revalidation (analyze_crossover / analyze_complex) plus analyze_results on the production root.
"""
from __future__ import annotations

import filecmp
import os
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parent.parent
BASELINE_COMMIT = "06850ed"
FILES = ["analyze_results.py", "analyze_crossover.py", "analyze_complex.py", "complex_workloads.py",
         "complex_joins.py", "complex_analytics.py", "workloads.py"]
EVIDENCE = REPO / "evidence" / "results"
FREEZE = '''import datetime as _dt


class _Frozen(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 26, 0, 0, 0, tzinfo=tz)


_dt.datetime = _Frozen
'''


def git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    if git("cat-file", "-e", BASELINE_COMMIT + "^{commit}").returncode != 0:
        pytest.skip(f"commit {BASELINE_COMMIT} is not available in this clone")
    if not (EVIDENCE / "production-20261025").is_dir():
        pytest.skip("saved evidence is not present")
    directory = tmp_path_factory.mktemp("baseline-analyzers")
    for name in FILES:
        shown = git("show", f"{BASELINE_COMMIT}:{name}")
        assert shown.returncode == 0, shown.stderr
        (directory / name).write_text(shown.stdout)
    freeze = tmp_path_factory.mktemp("freeze")
    (freeze / "sitecustomize.py").write_text(FREEZE)
    return directory, freeze


def run(script_dir, freeze, script, *args):
    env = {**os.environ, "PYTHONPATH": str(freeze), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, str(script_dir / script), *map(str, args)], capture_output=True, text=True,
                            env=env, cwd=REPO)
    return result.returncode, result.stdout.replace(str(args[-1]), "<OUT>"), result.stderr


def same_tree(a, b):
    comparison = filecmp.dircmp(a, b)
    assert not comparison.left_only and not comparison.right_only, (comparison.left_only, comparison.right_only)
    different = [name for name in comparison.common_files if not filecmp.cmp(a / name, b / name, shallow=False)]
    assert not different, f"differing files: {different}"
    assert comparison.common_files, "no outputs were produced"


COMMANDS = {
    "crossover": ("analyze_crossover.py", ["--results", "production-20261025", "large-sf100-20261025", "large-sf300-20261025"]),
    "complex-v2": ("analyze_complex.py", ["--results", "complex-formal-v2-20261025"]),
    "production-report": ("analyze_results.py", ["--results", "production-20261025"]),
}


@pytest.mark.parametrize("name", COMMANDS)
def test_three_engine_evidence_outputs_are_byte_identical(baseline, tmp_path, name):
    directory, freeze = baseline
    script, arguments = COMMANDS[name]
    arguments = [a if a == "--results" else EVIDENCE / a for a in arguments]
    old_out, new_out = tmp_path / "old", tmp_path / "new"
    old = run(directory, freeze, script, *arguments, "--output", old_out)
    new = run(REPO, freeze, script, *arguments, "--output", new_out)
    assert new[0] == old[0] == 0, (old, new)
    assert new[1] == old[1] and new[2] == old[2]
    same_tree(old_out, new_out)
    assert {p.name for p in new_out.iterdir()} <= {"correctness.json", "summary.csv", "CROSSOVER_REPORT.md", "COMPLEX_REPORT.md", "REPORT.md"}, \
        "legacy evidence must not produce the four-engine pairwise_speedups.csv"
