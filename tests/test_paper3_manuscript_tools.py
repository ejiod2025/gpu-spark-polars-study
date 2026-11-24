"""Paper 3 manuscript tools: the figure script (paper3/tools/make_figures.py) and the display mapping of the stats converter
(paper3/tools/stats_json_to_tex.py), on a synthetic campaign tree (tests/paper3_tree_fakes.py; no campaign data)."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
for path in (REPO, REPO / "tests", REPO / "tools" / "paper3_campaign"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import analyze_paper3 as A  # noqa: E402
import paper3_tree_fakes as F  # noqa: E402

TOOLS = REPO / "paper3" / "tools"
FIGURES = ("f01_energy_forest.pdf", "f02_time_vs_price.pdf")


def load_tool(name: str):
    path = TOOLS / f"{name}.py"
    if not path.is_file():
        pytest.skip(f"no {path.name} in this checkout")
    spec = importlib.util.spec_from_file_location(f"paper3_tool_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def stats_json(tmp_path_factory):
    """A synthetic tree shaped like the campaign after A9: two launches on each GPU host, one C-GCP square, one C-VM launch."""
    pytest.importorskip("matplotlib")
    root = tmp_path_factory.mktemp("p3figs")
    builder = F.TreeBuilder(root / "tree", F.World().truth())
    builder.add_launch("h100", 1, [1, 2, 3, 4], price=F.PRICE_BILLED["h100"])
    builder.add_launch("h100", 2, [5, 6, 7, 8], price=F.PRICE_BILLED["h100"])
    builder.add_launch("a100", 1, [1, 2, 3, 4], price=F.PRICE_BILLED["a100"])
    builder.add_launch("a100", 2, [5, 6, 7, 8], price=F.PRICE_BILLED["a100"])
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4], price=F.PRICE_BILLED["c-gcp"])
    builder.add_launch("c-vm", 1, [1, 2], price=F.PRICE_BILLED["c-vm"])
    A.run_analysis(root / "tree", root / "out", unblind=True, allow_synthetic=True)
    return root / "out" / "stats.json"


def digests(directory: Path) -> dict:
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in FIGURES}


def test_the_figures_are_written_as_pdf_and_are_byte_identical_on_a_rerun(stats_json, tmp_path):
    figures = load_tool("make_figures")
    assert figures.main(["--stats", str(stats_json), "--output", str(tmp_path / "a")]) == 0
    assert figures.main(["--stats", str(stats_json), "--output", str(tmp_path / "b")]) == 0
    for name in FIGURES:
        data = (tmp_path / "a" / name).read_bytes()
        assert data.startswith(b"%PDF") and len(data) > 2000, name
    assert digests(tmp_path / "a") == digests(tmp_path / "b")


def test_a_host_absent_from_stats_json_and_cells_that_are_not_estimable_are_drawn_not_dropped(stats_json, tmp_path):
    """Rule 2 of the script: a missing host gives a "not run" panel and a cell without an estimate a cross; neither breaks the run."""
    figures = load_tool("make_figures")
    stats = copy.deepcopy(json.loads(stats_json.read_text()))
    for contrast in ("x1", "x2", "x3"):
        stats[contrast].pop("a100", None)
    stats["price_ratios"].pop("a100", None)
    cell = stats["x1"]["h100"]["baseline"]["sf1"]["energy_total"]
    cell.update(estimate=None, ci_low=None, ci_high=None)
    cell["class"] = "not_estimable"
    one_round = stats["x2"]["h100"]["baseline"]["sf10"]["energy_total"]
    one_round.update(ci_low=None, ci_high=None)
    edited = tmp_path / "edge.json"
    edited.write_text(json.dumps(stats))
    assert figures.main(["--stats", str(edited), "--output", str(tmp_path / "edge")]) == 0
    for name in FIGURES:
        assert (tmp_path / "edge" / name).read_bytes().startswith(b"%PDF")


@pytest.mark.parametrize("key, value, expected", [
    ("break_even.x3.h100.baseline.cost_b1_p_new.text", "<= SF30", "$\\le$\\,SF30"),
    ("break_even.x3.h100.baseline.cost_b1_p_new.text", "> SF300", "$>$\\,SF300"),
    ("break_even.x1.a100.complex.energy_total.text", "<= SF10 (SF1 not estimable)", "$\\le$\\,SF10 (SF1 not estimable)"),
    ("break_even.x1.a100.complex.energy_total.text", "(SF10, SF30]", "(SF10, SF30]"),
    ("break_even.x1.a100.complex.energy_total.text", "not determinable (SF300 not estimable)", "not determinable (SF300 not estimable)"),
    ("x3.h100.baseline.sf300.cost_b1_p_new.estimate", "<= 2", "<= 2"),
])
def test_the_converter_typesets_only_break_even_brackets_and_only_le_and_gt(key, value, expected):
    converter = load_tool("stats_json_to_tex")
    assert converter.display(key, value) == expected
