"""The committed records (results/campaign-20261030/analysis for Paper 1, analysis-paper2 for Paper 2) must stay reproducible value for value.

Analyzer changes may add outputs, but re-analyzing the raw trees (when they are present) must give every committed stats.json key the same value, and the
cross-host file must be reproduced from the committed per-host stats alone. Only generated_at_utc may differ. A key that is new in a re-analysis is a failure
unless it is listed in ALLOWED_NEW_KEYS, which forces the addition to be a conscious, documented decision.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import analyze_campaign as AC  # noqa: E402

COMMITTED = REPO / "results" / "campaign-20261030" / "analysis"
COMMITTED2 = REPO / "results" / "campaign-20261030" / "analysis-paper2"
EARLIER_A100 = [Path("evidence") / "results" / name for name in
                ("production-20261025", "large-sf100-20261025", "large-sf300-20261025", "complex-formal-v2-20261025")]     # relative: the committed stats record these paths
HOSTS = {   # label -> (committed directory, raw tree, extra command-line arguments)
    "H100": ("h100-final", Path("rerun") / "hosts" / "h100" / "final" / "campaign-h100", ["--a100", *map(str, EARLIER_A100)]),
    "GH200": ("gh200-final", Path("rerun") / "hosts" / "gh200" / "final" / "campaign-gh200", []),
    "A100": ("a100-main", Path("rerun") / "hosts" / "a100" / "campaign-a100", ["--no-side-study"]),
    "B200": ("b200-main", Path("rerun") / "hosts" / "b200" / "campaign-b200", ["--no-side-study"]),
}
FINAL_HOSTS = {   # the four -final analyses Paper 2 uses: label -> (directory holding it, name, raw tree, extra arguments)
    "H100": (COMMITTED, "h100-final", HOSTS["H100"][1], HOSTS["H100"][2]),
    "GH200": (COMMITTED, "gh200-final", HOSTS["GH200"][1], HOSTS["GH200"][2]),
    "A100": (COMMITTED2, "a100-final", Path("rerun") / "hosts" / "a100" / "final" / "campaign-a100", []),
    "B200": (COMMITTED2, "b200-final", Path("rerun") / "hosts" / "b200" / "final" / "campaign-b200", []),
}
IGNORED = {"generated_at_utc"}
ALLOWED_NEW_KEYS: set[str] = set()      # dotted keys a re-analysis may add to a committed stats.json; empty: nothing has been added since the commit


def walk(value, prefix=""):
    """Dotted-key -> leaf view of nested dicts (lists and scalars are leaves)."""
    if isinstance(value, dict) and value:
        for key, item in value.items():
            yield from walk(item, f"{prefix}.{key}" if prefix else str(key))
    else:
        yield prefix, value


def compare(committed, fresh, ignored=None):
    """(keys missing from the re-analysis, keys whose value differs, keys new in the re-analysis); generated_at_utc ignored."""
    a, b = dict(walk(committed)), dict(walk(fresh))
    for k in (IGNORED if ignored is None else ignored):
        a.pop(k, None)
        b.pop(k, None)
    missing = sorted(k for k in a if k not in b)
    changed = sorted(k for k in a if k in b and a[k] != b[k])
    new = sorted(k for k in b if k not in a)
    return missing, changed, new


needs_committed = pytest.mark.skipif(not (COMMITTED / "xhost-final" / "cross_host.json").is_file(), reason="results/campaign-20261030 is not in this checkout")


def test_the_comparison_flags_missing_changed_and_new_keys():
    assert compare({"a": 1, "b": {"c": [1, 2], "d": 3}, "generated_at_utc": "x"}, {"a": 1, "b": {"c": [1, 3], "e": 4}, "generated_at_utc": "y"}) == (["b.d"], ["b.c"], ["b.e"])
    assert compare({"a": {}}, {"a": {}}) == ([], [], []) and compare({"a": 1}, {"a": 1.0}) == ([], [], [])


@needs_committed
def test_the_committed_cross_host_file_is_reproduced_from_the_committed_host_stats():
    stats = {label: json.loads((COMMITTED / directory / "stats.json").read_text()) for label, (directory, _, _) in HOSTS.items()}
    fresh = json.loads(json.dumps(AC.compare_hosts(stats, "H100")))
    committed = json.loads((COMMITTED / "xhost-final" / "cross_host.json").read_text())
    missing, changed, new = compare(committed, fresh)
    assert (missing, changed) == ([], []) and set(new) <= ALLOWED_NEW_KEYS, (missing[:5], changed[:5], new[:5])
    assert committed["hosts"] == ["A100", "H100", "GH200", "B200"] and committed["partial"] is False and committed["hosts_not_included"] == []


@needs_committed
@pytest.mark.parametrize("label", sorted(HOSTS))
def test_the_committed_stats_are_reproduced_value_for_value_from_the_raw_tree(label, tmp_path, monkeypatch):
    monkeypatch.chdir(REPO)                    # the committed stats record the paths they were produced with (relative to the repository root)
    directory, root, extra = HOSTS[label]
    if not (root / "round-4" / "gates.json").is_file():
        pytest.skip(f"the raw {label} tree is not in this checkout (it is not in git; see results/campaign-20261030/raw-manifests)")
    out = tmp_path / directory
    assert AC.main(["--root", str(root), "--output", str(out), "--label", label, *extra]) == 0
    committed = json.loads((COMMITTED / directory / "stats.json").read_text())
    fresh = json.loads((out / "stats.json").read_text())
    missing, changed, new = compare(committed, fresh)
    assert missing == [] and changed == [], (missing[:5], changed[:5])
    assert set(new) <= ALLOWED_NEW_KEYS, f"keys added since the commit (list them in ALLOWED_NEW_KEYS if intended): {new[:8]}"
    flat_committed = json.loads((COMMITTED / directory / "stats_flat.json").read_text())
    flat_fresh = json.loads((out / "stats_flat.json").read_text())
    assert compare(flat_committed, flat_fresh)[:2] == ([], [])


@needs_committed
@pytest.mark.parametrize("label", sorted(HOSTS))
def test_the_committed_tables_are_reproduced_from_the_raw_tree(label, tmp_path, monkeypatch):
    monkeypatch.chdir(REPO)
    directory, root, extra = HOSTS[label]
    if not (root / "round-4" / "gates.json").is_file():
        pytest.skip(f"the raw {label} tree is not in this checkout")
    out = tmp_path / directory
    assert AC.main(["--root", str(root), "--output", str(out), "--label", label, *extra]) == 0
    checked = 0
    for committed in sorted((COMMITTED / directory / "tables").glob("*.csv")):
        fresh = out / "tables" / committed.name
        assert fresh.is_file(), f"{committed.name} is no longer written"
        old, new = list(csv.reader(committed.open())), list(csv.reader(fresh.open()))
        assert old == new, committed.name
        checked += 1
    assert checked >= 20


# ------------------------------------------------------------------ Paper 2 record (analysis-paper2)

needs_paper2 = pytest.mark.skipif(not (COMMITTED2 / "paper2-final" / "paper2.json").is_file(), reason="results/campaign-20261030/analysis-paper2 is not in this checkout")


def committed_final_stats():
    return {label: AC.load_host_stats(label, directory / name / "stats.json")[0] for label, (directory, name, _, _) in FINAL_HOSTS.items()}


@needs_paper2
def test_the_committed_paper2_file_is_rebuilt_value_for_value_from_the_four_committed_final_stats():
    fresh = json.loads(json.dumps(AC.build_paper2(committed_final_stats(), "H100")))
    committed = json.loads((COMMITTED2 / "paper2-final" / "paper2.json").read_text())
    missing, changed, new = compare(committed, fresh, {"meta.generated_at_utc"})
    assert missing == [] and changed == [], (missing[:5], changed[:5])
    assert set(new) <= ALLOWED_NEW_KEYS, f"keys added since the commit (list them in ALLOWED_NEW_KEYS if intended): {new[:8]}"
    assert committed["meta"]["hosts"] == ["a100", "h100", "gh200", "b200"] and all(h["bridge_available"] for h in committed["hosts"].values())


@needs_paper2
def test_the_committed_paper2_file_is_what_the_command_line_writes_from_those_stats(tmp_path):
    args = ["--paper2", "--reference", "H100", "--output", str(tmp_path)]
    for label, (directory, name, _, _) in FINAL_HOSTS.items():
        args += ["--host", f"{label}={directory / name / 'stats.json'}"]
    assert AC.main(args) == 0
    fresh = json.loads((tmp_path / "paper2.json").read_text())
    committed = json.loads((COMMITTED2 / "paper2-final" / "paper2.json").read_text())
    assert compare(committed, fresh, {"meta.generated_at_utc"}) == ([], [], [])


@needs_paper2
def test_the_committed_paper2_cross_host_file_is_rebuilt_from_the_four_committed_final_stats():
    fresh = json.loads(json.dumps(AC.compare_hosts(committed_final_stats(), "H100")))
    committed = json.loads((COMMITTED2 / "xhost-final-v2" / "cross_host.json").read_text())
    missing, changed, new = compare(committed, fresh)
    assert (missing, changed) == ([], []) and set(new) <= ALLOWED_NEW_KEYS, (missing[:5], changed[:5], new[:5])
    assert committed["hosts"] == ["A100", "H100", "GH200", "B200"] and committed["partial"] is False and committed["hosts_not_included"] == []
    # the Paper 1 record is the same comparison apart from where two hosts' files were read from
    paper1 = json.loads((COMMITTED / "xhost-final" / "cross_host.json").read_text())
    differing = compare(paper1, committed)[1]
    assert set(differing) <= {"host_info.A100.preflight", "host_info.A100.root", "host_info.B200.root"}, differing


@needs_paper2
@pytest.mark.parametrize("label", ["A100", "B200"])
def test_the_committed_a100_and_b200_final_stats_and_tables_are_reproduced_from_the_final_raw_trees(label, tmp_path, monkeypatch):
    monkeypatch.chdir(REPO)
    directory, name, root, extra = FINAL_HOSTS[label]
    if not (root / "round-4" / "gates.json").is_file() or not (root / "side-study" / "round-4" / "gates.json").is_file():
        pytest.skip(f"the final raw {label} tree is not in this checkout (it is not in git)")
    out = tmp_path / name
    assert AC.main(["--root", str(root), "--output", str(out), "--label", label, *extra]) == 0
    committed = json.loads((directory / name / "stats.json").read_text())
    missing, changed, new = compare(committed, json.loads((out / "stats.json").read_text()))
    assert missing == [] and changed == [] and set(new) <= ALLOWED_NEW_KEYS, (missing[:5], changed[:5], new[:5])
    for table in sorted((directory / name / "tables").glob("*.csv")):
        assert list(csv.reader(table.open())) == list(csv.reader((out / "tables" / table.name).open())), table.name


@needs_paper2
def test_the_paper1_main_stats_of_a100_and_b200_agree_with_the_final_ones_outside_the_side_study():
    for label in ("A100", "B200"):
        main = json.loads((COMMITTED / HOSTS[label][0] / "stats.json").read_text())
        final = json.loads((COMMITTED2 / FINAL_HOSTS[label][1] / "stats.json").read_text())
        missing, changed, _ = compare(main, final)
        outside = [k for k in changed if not k.startswith(("side_study", "exploratory"))]
        assert set(outside) <= {"campaign.root", "meta.input_gate_source", "host.preflight"}, outside
        assert [k for k in missing if not k.startswith("side_study")] == []
