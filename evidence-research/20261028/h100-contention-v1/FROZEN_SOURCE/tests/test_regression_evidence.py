"""Golden-output regression: the analyzers must still reproduce the committed A100 reports.

The README "Revalidate saved evidence" commands are run twice on the same saved results: once with
the analyzers from `git HEAD` (a detached worktree under a unique temp directory) and once with the
working-tree analyzers. Required:

  * both runs exit 0 and print the same coverage counts;
  * their markdown, CSV and JSON outputs are identical (only generated_at_utc may differ);
  * both reproduce the committed reports/ files, after replacing the absolute results path embedded
    in them (the reports were produced on the original host);
  * `sha256sum -c evidence/SHA256SUMS` passes and tracked evidence is unmodified.

Set REGRESSION_BASE_REF to compare against another commit (default HEAD).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

REPO = Path(__file__).resolve().parents[1]
BASE_REF = os.environ.get("REGRESSION_BASE_REF", "HEAD")
RESULTS = REPO / "evidence" / "results"
ORIGINAL_RESULTS_PREFIX = "/Users/danielejiofor/AIRFLOW/benchmarks/spark_gpu/results"
BASELINE_ROOTS = ("production-20261025", "large-sf100-20261025", "large-sf300-20261025")
COMPLEX_ROOT = "complex-formal-v2-20261025"

# (analyzer, extra arguments after --output, committed report directory, files compared with the committed copies)
CASES = {
    "crossover": ("analyze_crossover.py", "reports/crossover-20261025",
                  ("CROSSOVER_REPORT.md", "summary.csv", "correctness.json")),
    "complex": ("analyze_complex.py", "reports/complex-formal-v2-20261025",
                ("COMPLEX_REPORT.md", "summary.csv", "correctness.json")),
}
EXPECTED_COUNTS = {
    "crossover": {"included_scales": 5, "comparisons": 360, "gpu_plans": 120, "errors": 0, "passed": True},
    "complex": {"included_cases": 18, "comparisons": 216, "gpu_plans": 72, "cleanup_checks": 216,
                "session_errors": 0, "passed": True},
}


def git(*args: str, cwd: Path = REPO, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=check)


def analyzer_command(case: str, output: Path) -> list[str]:
    script = CASES[case][0]
    if case == "crossover":
        return [sys.executable, script, "--results", *[str(RESULTS / name) for name in BASELINE_ROOTS], "--output", str(output)]
    return [sys.executable, script, "--results", str(RESULTS / COMPLEX_ROOT), "--output", str(output)]


def run_all(tree: Path, out: Path) -> dict[str, subprocess.CompletedProcess]:
    results = {}
    for case in CASES:
        results[case] = subprocess.run(analyzer_command(case, out / case), cwd=tree, text=True,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"})
    return results


def scrub(value, drop=("generated_at_utc",)):
    """Drop run-time stamps and neutralise absolute results paths so outputs are comparable."""
    if isinstance(value, dict):
        return {k: scrub(v, drop) for k, v in value.items() if k not in drop}
    if isinstance(value, list):
        return [scrub(v, drop) for v in value]
    if isinstance(value, str):
        for prefix in (str(RESULTS), str(RESULTS.resolve()), ORIGINAL_RESULTS_PREFIX):
            value = value.replace(prefix, "<RESULTS>")
        return value
    return value


def normalized(path: Path, drop=("generated_at_utc",)):
    text = path.read_text()
    if path.suffix == ".json":
        return scrub(json.loads(text), drop)
    return scrub(text)


@pytest.fixture(scope="module")
def head_tree():
    if shutil.which("git") is None or git("rev-parse", "--git-dir", check=False).returncode != 0:
        pytest.skip("not a git checkout")
    parent = Path(tempfile.mkdtemp(prefix="head-wt-dq-"))
    tree = parent / "wt"
    git("worktree", "add", "--detach", str(tree), BASE_REF)
    try:
        yield tree
    finally:
        git("worktree", "remove", "--force", str(tree), check=False)
        shutil.rmtree(parent, ignore_errors=True)
        git("worktree", "prune", check=False)


@pytest.fixture(scope="module")
def outputs(head_tree, tmp_path_factory):
    out = tmp_path_factory.mktemp("analyzer-outputs")
    head = run_all(head_tree, out / "head")
    work = run_all(REPO, out / "work")
    return {"head": (out / "head", head), "work": (out / "work", work)}


def test_saved_evidence_checksums():
    command = ["sha256sum", "-c", "SHA256SUMS"] if shutil.which("sha256sum") else ["shasum", "-a", "256", "-c", "SHA256SUMS"]
    result = subprocess.run(command, cwd=REPO / "evidence", text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    bad = [line for line in result.stdout.splitlines() if not line.endswith(": OK")]
    assert result.returncode == 0 and not bad, "\n".join(bad[:10])
    assert result.stdout.count(": OK") == len((REPO / "evidence" / "SHA256SUMS").read_text().splitlines())


def test_tracked_evidence_and_reports_are_unmodified():
    status = git("status", "--porcelain", "--untracked-files=no", "--", "evidence", "reports", "publication").stdout
    assert status.strip() == "", f"saved evidence or committed reports were modified:\n{status}"


@pytest.mark.parametrize("which", ["head", "work"])
@pytest.mark.parametrize("case", sorted(CASES))
def test_analyzers_exit_cleanly_with_expected_coverage(outputs, which, case):
    out, runs = outputs[which]
    assert runs[case].returncode == 0, runs[case].stdout[-2000:]
    correctness = json.loads((out / case / "correctness.json").read_text())
    for key, expected in EXPECTED_COUNTS[case].items():
        found = correctness[key]
        found = len(found) if isinstance(found, list) else found
        assert found == expected, (which, case, key, found)


@pytest.mark.parametrize("case", sorted(CASES))
def test_working_tree_outputs_equal_head_outputs(outputs, case):
    head_dir, work_dir = outputs["head"][0] / case, outputs["work"][0] / case
    head_files = sorted(p.name for p in head_dir.iterdir())
    assert head_files == sorted(p.name for p in work_dir.iterdir()), "output file sets differ"
    for name in head_files:
        assert normalized(head_dir / name) == normalized(work_dir / name), f"{case}/{name} differs from HEAD analyzers"


@pytest.mark.parametrize("which", ["head", "work"])
@pytest.mark.parametrize("case", sorted(CASES))
def test_outputs_reproduce_committed_reports(outputs, head_tree, which, case):
    _, report_dir, names = CASES[case]
    out = outputs[which][0] / case
    for name in names:
        committed = head_tree / report_dir / name  # the committed copy, never a possibly-regenerated one
        assert committed.is_file(), committed
        # The curated evidence omits each schedule entry's raw launch command, which the original
        # correctness.json copied; that single field cannot be reproduced and is ignored.
        drop = ("generated_at_utc", "command")
        assert normalized(out / name, drop) == normalized(committed, drop), (
            f"{which} analyzers do not reproduce committed {report_dir}/{name}")


def test_csv_outputs_are_byte_identical(outputs, head_tree):
    for case, (_, report_dir, names) in CASES.items():
        for which in ("head", "work"):
            produced = (outputs[which][0] / case / "summary.csv").read_bytes()
            assert produced == (head_tree / report_dir / "summary.csv").read_bytes(), (which, case)


def test_no_pycache_or_outputs_leaked_into_the_repository():
    status = git("status", "--porcelain", "--", "reports", "evidence").stdout
    assert status.strip() == ""
