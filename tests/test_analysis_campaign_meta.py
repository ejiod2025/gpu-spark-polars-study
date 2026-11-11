"""stats.json "meta" block: the integrity facts the paper prints, derived from campaign artifacts and never typed in.

The paper's own key list (paper/tools/stats_to_tex.py --list-keys) is the contract: every meta.* key it requires, for the primary
host and for the ladder hosts, must be produced from a synthetic campaign. The other tests recompute each value from the raw files
of a synthetic tree, independently of analyze_campaign's code path, and check the None / flagging rules.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import analyze_campaign as AC  # noqa: E402

QUIET = dict(round_sigma=0.0, query_sigma=0.0, trial_sigma=0.0, position_effect=0.0, gpu_interval=60.0)
PAPER_TOOL = REPO / "paper" / "tools" / "stats_to_tex.py"


def build(tmp_path, name="tree", host="H100", **kwargs):
    root = tmp_path / name
    AC.make_synthetic_campaign(root, seed=kwargs.pop("seed", 3), host=host, a100=False, side_study=False,
                               **{**QUIET, "plan_variants": (("complex", "30", "cx_q09"),), **kwargs})        # one variable plan cell, as on every real host
    return root


def analyze(root, **kwargs):
    return json.loads(json.dumps(AC.analyze(root, energy=False, **kwargs)))


def raw_sessions(root, rounds=(1, 2, 3, 4)):
    """results.json of every session of the given rounds, read straight from the files."""
    out = []
    for k in rounds:
        for path in sorted((Path(root) / f"round-{k}").glob("*/sf*/*/results.json")):
            out.append((k, path, json.loads(path.read_text())))
    return out


# ------------------------------------------------------------------ the paper's contract

def paper_meta_keys():
    if not PAPER_TOOL.is_file():
        pytest.skip("paper/tools/stats_to_tex.py not present")
    result = subprocess.run([sys.executable, str(PAPER_TOOL), "--list-keys"], capture_output=True, text=True, cwd=REPO, timeout=120)
    assert result.returncode == 0, result.stderr[-500:]
    keys = [line.strip() for line in result.stdout.splitlines() if re.match(r"^(?:[a-z0-9]+\.)?meta\.[A-Za-z0-9_]+$", line.strip())]
    assert keys, "the paper lists no meta.* key: its --list-keys output changed"
    return keys


def paper_loader():
    """The paper tooling's own stats loader (applies its key derivations, e.g. meta.later_amendments_list from meta.later_amendments)."""
    import importlib.util
    path = REPO / "paper" / "tools" / "statskeys.py"
    if not path.is_file():
        pytest.skip("paper/tools/statskeys.py not present")
    spec = importlib.util.spec_from_file_location("paper_statskeys", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_meta_key_the_paper_requires_is_produced_for_every_host(tmp_path):
    """Run the paper's --list-keys and its own loader on each host's stats.json: every required meta.* key must resolve to a value."""
    required = paper_meta_keys()
    assert {"meta.input_gate", "meta.amendments", "meta.max_rel_diff", "meta.campaign_start_utc", "meta.spark_gpu_plans_checked"} <= set(required)
    loader = paper_loader()
    for host in AC.SYNTH_HOSTS:
        out = tmp_path / f"out-{host}"
        assert AC.main(["--root", str(build(tmp_path, host, host=host)), "--output", str(out), "--no-energy"]) == 0
        loaded = loader.load_stats(out / "stats.json")[0]          # the first element is the flat key -> value map
        for key in required:
            bare = key.split("meta.", 1)[1]
            prefix = key.split(".meta.")[0] if ".meta." in key else None
            if prefix and prefix != host.lower():
                continue                     # a ladder host's keys are checked against that host's own stats
            assert f"meta.{bare}" in loaded, f"{host}: {key} is not produced (directly or by the paper's derivation)"
            assert loaded[f"meta.{bare}"] is not None, f"{host}: {key} is null although a synthetic campaign determines it"
    # the ladder hosts are required for exactly the keys the primary also gets
    ladder = {k.split(".meta.")[1] for k in required if ".meta." in k}
    primary = {k.split("meta.")[1] for k in required if k.startswith("meta.")}
    assert ladder <= primary


def test_meta_keys_are_in_the_written_stats_files_and_documented(tmp_path):
    out = tmp_path / "out"
    assert AC.main(["--root", str(build(tmp_path)), "--output", str(out), "--no-energy"]) == 0
    stats = json.loads((out / "stats.json").read_text())
    flat = json.loads((out / "stats_flat.json").read_text())
    meta_schema = {p for p, _, _ in AC.SCHEMA if p.startswith("meta.")}
    assert meta_schema and {f"meta.{k}" for k in stats["meta"]} == meta_schema
    assert all(flat[f"meta.{k}"] == v for k, v in stats["meta"].items())
    assert "`meta.input_gate`" in (out / "STATS_SCHEMA.md").read_text()


# ------------------------------------------------------------------ values recomputed from the raw files

def test_counts_match_an_independent_pass_over_the_raw_files(tmp_path):
    root = build(tmp_path)
    meta = analyze(root)["meta"]
    sessions = raw_sessions(root)
    assert meta["sessions_completed"] == sum(1 for _, _, s in sessions if s.get("completed_at_utc") and not s.get("failure")) == 4 * 32
    measured = sum(1 for _, _, s in sessions for r in s["records"] if r["phase"] == "measured")
    assert meta["measured_trials"] == measured and measured > 2000
    barriers = sum(1 for _, _, s in sessions for c in s.get("cleanup_records") or [] if c["status"] == "passed")
    assert meta["cleanup_barriers_passed"] == barriers and barriers > 0
    plans = sum(1 for _, p, s in sessions if s["engine"] == "spark_gpu" for r in s["records"] if r.get("plan"))
    assert meta["spark_gpu_plans_checked"] == plans > 0
    assert meta["rounds_completed"] == 4 and meta["rounds_repeated"] == 0 and meta["rounds_gated"] == 4
    assert meta["integrity_failures_in_accepted_rounds"] == 0


def test_output_agreement_is_recomputed_from_saved_rows(tmp_path):
    root = build(tmp_path)
    meta = analyze(root)["meta"]
    expected_compared, largest = 0, 0.0
    for k in (1, 2, 3, 4):
        for suite in AC.SUITES:
            blocks = {}
            for path in (root / f"round-{k}" / suite).glob("sf*/*/results.json"):
                blocks.setdefault(path.parent.parent.name, {})[path.parent.name] = json.loads(path.read_text())
            for scale, by_engine in blocks.items():
                reference = {r["query"]: r["rows"] for r in by_engine["polars"]["records"] if r["phase"] == "warmup"}
                for engine, session in by_engine.items():
                    if engine == "polars":
                        continue
                    for record in session["records"]:
                        expected_compared += 1
                        for left, right in zip(reference[record["query"]], record["rows"]):
                            largest = max(largest, abs(left[1] - right[1]) / max(abs(left[1]), abs(right[1])))
    assert meta["outputs_compared"] == expected_compared > 2000 and meta["outputs_failed"] == 0
    assert meta["max_rel_diff"] == pytest.approx(largest) and 0.0 < meta["max_rel_diff"] < 1e-8


def test_a_disagreeing_output_is_counted_and_flagged_as_an_integrity_disagreement(tmp_path):
    root = build(tmp_path)
    path = root / "round-2" / "baseline" / "sf10" / "spark_cpu" / "results.json"
    session = json.loads(path.read_text())
    session["records"][5]["rows"][0][1] *= 1.001           # 1e-3 relative: far outside gate G2
    path.write_text(json.dumps(session))
    extras = {}
    stats = json.loads(json.dumps(AC.analyze(root, energy=False, extras=extras)))
    assert stats["meta"]["outputs_failed"] == 1 and stats["meta"]["max_rel_diff"] == pytest.approx(1e-3, rel=0.05)
    assert stats["meta"]["integrity_failures_in_accepted_rounds"] == 1
    cell = extras["integrity"]["integrity_failures_in_accepted_rounds"][0]
    assert (cell["round"], cell["engine"], cell["kind"]) == (2, "spark_cpu", "output_disagreement")


def test_start_and_end_come_from_the_session_schedule(tmp_path):
    root = build(tmp_path)
    meta = analyze(root)["meta"]
    starts, ends = [], []
    for path in root.glob("round-*/*/schedule.json"):
        for item in json.loads(path.read_text()):
            start = AC.parse_utc(item["start_utc"])
            starts.append(start)
            ends.append(start.timestamp() + item["process_wall_seconds"])
    assert meta["campaign_start_utc"] == min(starts).strftime("%Y-%m-%d %H:%M UTC")
    assert meta["campaign_end_utc"] == AC.format_utc(AC.datetime.fromtimestamp(max(ends), tz=AC.timezone.utc))
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", meta["campaign_start_utc"]) and meta["campaign_start_utc"] < meta["campaign_end_utc"]


def test_excluded_rounds_do_not_enter_any_count(tmp_path):
    root = build(tmp_path, failed_gate_rounds=(3,))
    meta = analyze(root)["meta"]
    sessions = raw_sessions(root, rounds=(1, 2, 4))
    assert meta["rounds_completed"] == 3 and meta["sessions_completed"] == 3 * 32 == len(sessions)
    assert meta["measured_trials"] == sum(1 for _, _, s in sessions for r in s["records"] if r["phase"] == "measured")


def test_repeated_rounds_are_counted_and_the_latest_repeat_wins(tmp_path):
    root = build(tmp_path, failed_gate_rounds=(2,))
    for name in ("round-2-repeat-2", "round-2-repeat-10"):           # "repeat-10" sorts before "repeat-2" as text, after it as a number
        shutil.copytree(root / "round-1", root / name)
    stats = analyze(root)
    assert stats["meta"]["rounds_repeated"] == 1 and stats["meta"]["rounds_completed"] == 4
    assert stats["campaign"]["rounds_included_labels"] == ["round-1", "round-2-repeat-10", "round-3", "round-4"]
    assert {x["label"] for x in stats["campaign"]["rounds_excluded"]} == {"round-2", "round-2-repeat-2"}


# ------------------------------------------------------------------ design version per host (amendments, commit)

DOC_RELATIVE = Path("docs") / "H100_EXPERIMENT_DESIGN.md"


def doc_text(amendments):
    body = "# Design\n\n## 9. Statistics\n\n### A1 - a plan heading outside section 11\n\n## 11. Amendments\n\n"
    return body + "".join(f"### A{n} - 2026-10-{28 + n} - amendment {n}\n\nText.\n\n" for n in range(1, amendments + 1)) + "## 12. After\n\n### A9 - elsewhere\n"


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false", *args],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture()
def design_repo(tmp_path):
    """A throw-away repository with the design document committed at 3 amendments (c1), then 4 (c2), then back to the 3-amendment text (c3)."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    repo = tmp_path / "designrepo"
    (repo / "docs").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    doc = repo / DOC_RELATIVE
    commits = []
    for text in (doc_text(3), doc_text(4), doc_text(3)):
        doc.write_text(text)
        git(repo, "add", str(DOC_RELATIVE))
        git(repo, "commit", "-q", "-m", f"design {len(commits) + 1}")
        commits.append(git(repo, "rev-parse", "HEAD"))
    doc.write_text(doc_text(4))                      # the working tree has the 4-amendment text again (committed as c2)
    sha = {n: hashlib.sha256(doc_text(n).encode()).hexdigest() for n in (3, 4, 5)}
    return {"repo": repo, "doc": doc, "commits": commits, "sha": sha}


def with_recorded_sha(root, sha):
    meta = json.loads((root / "campaign.json").read_text())
    meta["provenance"]["design_doc_sha256"] = sha
    (root / "campaign.json").write_text(json.dumps(meta))


def test_the_design_commit_is_the_oldest_commit_holding_the_recorded_version(tmp_path, design_repo):
    root = build(tmp_path)
    c1, c2, c3 = design_repo["commits"]
    with_recorded_sha(root, design_repo["sha"][3])
    stats = analyze(root, design_doc=design_repo["doc"])
    meta = stats["meta"]
    assert meta["design_commit"] == c1 and meta["design_commit_short"] == c1[:7]            # c1 and c3 hold the same text: the oldest wins
    assert meta["amendments"] == 3 and meta["later_amendments"] == ["A4"] and meta["design_doc_matches_campaign"] is True
    assert meta["design_doc_sha256"] == design_repo["sha"][3]
    assert not [w for w in stats["campaign"]["warnings"] if "design" in w]                  # a resolved version is not a warning
    with_recorded_sha(root, design_repo["sha"][4])
    meta = analyze(root, design_doc=design_repo["doc"])["meta"]
    assert (meta["design_commit"], meta["amendments"], meta["later_amendments"], meta["design_doc_matches_campaign"]) == (c2, 4, [], True)


def test_amendments_count_section_11_only(tmp_path, design_repo):
    root = build(tmp_path)
    with_recorded_sha(root, design_repo["sha"][4])
    assert "A9" in design_repo["doc"].read_text() and "### A1 - a plan heading" in design_repo["doc"].read_text()   # distractors outside section 11
    assert analyze(root, design_doc=design_repo["doc"])["meta"]["amendments"] == 4
    assert AC.amendment_ids(doc_text(4)) == ["A1", "A2", "A3", "A4"] and AC.amendment_ids("# no amendments section\n### A1 - x\n") is None


def test_a_sha_that_matches_no_commit_warns_and_leaves_the_version_unknown(tmp_path, design_repo):
    root = build(tmp_path)
    with_recorded_sha(root, "ab" * 32)
    stats = analyze(root, design_doc=design_repo["doc"])
    meta, warnings = stats["meta"], " | ".join(stats["campaign"]["warnings"])
    assert meta["design_doc_matches_campaign"] is False and meta["design_commit"] is None and meta["amendments"] is None and meta["later_amendments"] is None
    assert "matches no commit" in warnings and "meta.amendments is null" in warnings and "meta.design_commit is null" in warnings


def test_an_uncommitted_working_tree_version_is_a_mismatch_with_a_clear_warning(tmp_path, design_repo):
    root = build(tmp_path)
    design_repo["doc"].write_text(doc_text(5))                                              # edited, not committed
    with_recorded_sha(root, design_repo["sha"][5])
    stats = analyze(root, design_doc=design_repo["doc"])
    assert stats["meta"]["design_doc_matches_campaign"] is False and stats["meta"]["amendments"] == 5 and stats["meta"]["later_amendments"] == []
    assert "uncommitted working-tree file" in " ".join(stats["campaign"]["warnings"])


def test_without_git_history_a_byte_identical_document_still_identifies_the_version(tmp_path):
    doc = tmp_path / "plain" / "design.md"
    doc.parent.mkdir()
    doc.write_text(doc_text(4))
    root = build(tmp_path)
    with_recorded_sha(root, AC.sha256_of(doc))
    stats = analyze(root, design_doc=doc)
    assert stats["meta"]["amendments"] == 4 and stats["meta"]["design_doc_matches_campaign"] is True and stats["meta"]["design_commit"] is None
    assert "no git history" in " ".join(stats["campaign"]["warnings"]) and "matches no commit" not in " ".join(stats["campaign"]["warnings"])


def test_a_missing_recorded_sha_is_called_out_and_not_a_mismatch(tmp_path, design_repo):
    root = build(tmp_path)
    with_recorded_sha(root, None)
    stats = analyze(root, design_doc=design_repo["doc"])
    assert stats["meta"]["design_doc_matches_campaign"] is None and stats["meta"]["design_commit"] is None
    assert "recorded no design_doc_sha256" in " ".join(stats["campaign"]["warnings"])


def test_each_host_resolves_its_own_design_version_and_the_comparison_says_they_differ(tmp_path, design_repo):
    c1, c2, _ = design_repo["commits"]
    roots = {}
    for host, sha in (("H100", design_repo["sha"][3]), ("A100", design_repo["sha"][4])):
        roots[host] = build(tmp_path, host, host=host)
        with_recorded_sha(roots[host], sha)
    stats = {h: analyze(r, design_doc=design_repo["doc"]) for h, r in roots.items()}
    assert (stats["H100"]["meta"]["design_commit"], stats["H100"]["meta"]["amendments"], stats["H100"]["meta"]["later_amendments"]) == (c1, 3, ["A4"])
    assert (stats["A100"]["meta"]["design_commit"], stats["A100"]["meta"]["amendments"], stats["A100"]["meta"]["later_amendments"]) == (c2, 4, [])
    cross = json.loads(json.dumps(AC.compare_hosts(stats)))
    assert cross["host_info"]["H100"]["amendments"] == 3 and cross["host_info"]["A100"]["design_commit"] == c2
    note = " ".join(cross["caveats_by_host"]["A100"])
    assert "different design versions" in note and f"{c2[:7]} (4 amendments)" in note and f"{c1[:7]} (3 amendments)" in note


def test_the_real_repository_history_resolves_the_two_preregistered_versions():
    """H100 started under ee2e6a7 (A1-A3) and A100 / GH200 under afd07c0 (A1-A4); skipped where the history is not available."""
    versions = AC.committed_doc_versions(AC.DESIGN_DOC)
    by_commit = {c: (sha, text) for c, sha, text in versions}
    first = next((c for c in by_commit if c.startswith("ee2e6a7")), None)
    second = next((c for c in by_commit if c.startswith("afd07c0")), None)
    if not (first and second):
        pytest.skip("the repository history with the pre-registration commits is not available")
    one = AC.design_version(by_commit[first][0], AC.DESIGN_DOC)
    two = AC.design_version(by_commit[second][0], AC.DESIGN_DOC)
    assert (one["commit"], one["amendments"], one["matches"]) == (first, 3, True) and one["later"][:1] == ["A4"]
    assert (two["commit"], two["amendments"], two["matches"]) == (second, 4, True) and "A4" not in two["later"]


# ------------------------------------------------------------------ input gate

def test_input_gate_reads_the_verification_report_and_falls_back_to_preflight(tmp_path):
    root = build(tmp_path)
    assert analyze(root)["meta"]["input_gate"] == "RESULT: OK"
    copied = tmp_path / "copied"
    shutil.move(str(root), str(copied))                # the recorded absolute path no longer exists: found by file name inside the root
    meta = analyze(copied)["meta"]
    assert meta["input_gate"] == "RESULT: OK" and meta["input_gate_source"].endswith("inputs-verification.json")
    (copied / "inputs-verification.json").unlink()
    meta = analyze(copied)["meta"]
    assert meta["input_gate"] == "RESULT: OK" and "preflight" in meta["input_gate_source"]
    for report in copied.glob("round-*/preflight/preflight-report.json"):
        report.unlink()
    meta = analyze(copied, allow_ungated=True)["meta"]
    assert meta["input_gate"] is None and meta["input_gate_source"] is None


@pytest.mark.parametrize("edit, expected", [
    ({"ok": False, "result": "FAILED"}, "RESULT: FAILED"),
    ({"complete": False, "result": "INCOMPLETE", "ok": False}, "RESULT: INCOMPLETE"),
    ({"scales_verified": [1.0, 10.0]}, "RESULT: INCOMPLETE")])
def test_input_gate_reports_failed_and_incomplete_verifications(tmp_path, edit, expected):
    root = build(tmp_path)
    report = root / "inputs-verification.json"
    data = json.loads(report.read_text())
    data.update(edit)
    report.write_text(json.dumps(data))
    # the report was edited after campaign.json hashed it, so supply it explicitly
    assert analyze(root, inputs_reports=[report])["meta"]["input_gate"] == expected


def split_reports(root, tmp_path, second_edit=None):
    """Replace the single report by two (SF1-30 and SF100-300, as in the H100 campaign), recorded in campaign.json with their hashes."""
    base = json.loads((root / "inputs-verification.json").read_text())
    (root / "inputs-verification.json").unlink()
    meta = json.loads((root / "campaign.json").read_text())
    hashes = {}
    for name, scales in (("verify-sf1-30.json", [1.0, 10.0, 30.0]), ("verify-sf100-300.json", [100.0, 300.0])):
        report = {**base, "scales_requested": scales, "scales_verified": scales, "datasets": [d for d in base["datasets"] if d["scale_factor"] in scales]}
        if name == "verify-sf100-300.json" and second_edit:
            report.update(second_edit)
        path = tmp_path / "reports" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(report))
        hashes[str(path)] = AC.sha256_of(path)
    meta["provenance"]["inputs"]["inputs_reports_sha256"] = hashes
    (root / "campaign.json").write_text(json.dumps(meta))
    return hashes


def test_input_gate_combines_reports_that_each_cover_part_of_the_scales(tmp_path):
    root = build(tmp_path)
    hashes = split_reports(root, tmp_path)
    meta = analyze(root)["meta"]
    assert meta["input_gate"] == "RESULT: OK" and all(Path(p).name in meta["input_gate_source"] for p in hashes)
    # one of the two reports is missing from the copied tree: the other alone cannot cover SF100 and SF300
    Path(sorted(hashes)[1]).unlink()
    stats = analyze(root)
    assert stats["meta"]["input_gate"] == "RESULT: INCOMPLETE"
    assert any("not found next to the campaign: verify-sf100-300.json" in w for w in stats["campaign"]["warnings"])


def test_a_failed_report_fails_the_gate_even_when_the_other_one_is_fine(tmp_path):
    root = build(tmp_path)
    split_reports(root, tmp_path, {"ok": False, "result": "FAILED"})
    assert analyze(root)["meta"]["input_gate"] == "RESULT: FAILED"


def test_reports_recorded_on_the_host_but_not_copied_fall_back_to_the_preflight_check_with_a_warning(tmp_path):
    root = build(tmp_path)
    hashes = split_reports(root, tmp_path)
    for path in hashes:
        Path(path).unlink()                                  # the host paths do not exist here
    stats = analyze(root)
    assert stats["meta"]["input_gate"] == "RESULT: OK" and "preflight" in stats["meta"]["input_gate_source"]
    assert any("verify-sf1-30.json" in w and "Copy them next to the campaign root" in w for w in stats["campaign"]["warnings"])


def test_reports_kept_next_to_the_campaign_root_are_found_by_name_and_hash_checked(tmp_path):
    root = build(tmp_path)
    hashes = split_reports(root, tmp_path)
    hold = tmp_path / "hold"
    hold.mkdir()
    shutil.move(str(root), str(hold / "root"))
    for path in hashes:
        shutil.move(path, hold / Path(path).name)             # copied beside the campaign, not into it
    stats = analyze(hold / "root")
    assert stats["meta"]["input_gate"] == "RESULT: OK" and str(hold) in stats["meta"]["input_gate_source"]
    assert not [w for w in stats["campaign"]["warnings"] if "input_gate" in w]
    (hold / "verify-sf1-30.json").write_text((hold / "verify-sf1-30.json").read_text().replace('"ok": true', '"ok": false'))
    tampered = analyze(hold / "root")
    assert any("sha256 differs" in w for w in tampered["campaign"]["warnings"])        # an edited report is not trusted


def test_a_missing_run_commit_is_logged_and_does_not_hide_the_design_commit(tmp_path):
    root = build(tmp_path)
    meta = json.loads((root / "campaign.json").read_text())
    meta["provenance"]["git"] = {"commit": None, "branch": None, "dirty_files": [], "diff_sha256": "e3b0"}     # a host without a git checkout
    (root / "campaign.json").write_text(json.dumps(meta))
    stats = analyze(root)
    assert stats["meta"]["run_commit"] is None and stats["meta"]["design_commit"] is not None
    assert any("meta.run_commit is null" in w and "no git checkout" in w for w in stats["campaign"]["warnings"])


H100_ROUND1 = REPO / "rerun" / "hosts" / "h100" / "campaign-h100"


def real_tree_invariants(root, tmp_path, rounds=None):
    """Integrity facts every accepted round of a real campaign tree must satisfy, whatever its timings (no performance content)."""
    out = tmp_path / "out"
    assert AC.main(["--root", str(root), "--output", str(out), "--no-energy"]) == 0
    stats = json.loads((out / "stats.json").read_text())
    integrity = json.loads((out / "integrity.json").read_text())
    included = [r for r in integrity["rounds"] if r["included"]]
    assert included and (rounds is None or len(included) >= rounds)
    for r in included:
        assert (r["sessions_found"], r["sessions_expected"], r["sessions_readable"]) == (32, 32, 32), r["label"]
        assert r["sessions_missing"] == [] and r["sessions_nonzero_returncode"] == 0
        assert r["cells"] == r["ok"] + sum(r["failed"].values()) and r["incomplete"] == r["session_failed"] == r["invalid"] == 0
        assert set(r["failed"]) <= set(AC.COVERAGE_CLASSES)                          # coverage outcomes only
    assert integrity["trials_per_ok_cell"] == [3] and integrity["cleanup_not_passed"] == 0 and integrity["integrity_failures_in_accepted_rounds"] == []
    meta = stats["meta"]
    n = len(included)
    assert meta["rounds_completed"] == n and meta["rounds_gated"] == n and meta["sessions_completed"] == 32 * n
    assert meta["outputs_failed"] == 0 and 0 < meta["max_rel_diff"] < AC.REL_TOL and meta["integrity_failures_in_accepted_rounds"] == 0
    assert not [w for w in stats["campaign"]["warnings"] if "DISAGREEMENT" in w]       # preflight counts == recomputed counts
    return stats, integrity


@pytest.mark.skipif(not (H100_ROUND1 / "round-1" / "gates.json").is_file(), reason="the copied H100 campaign is not in this checkout")
def test_the_real_h100_tree_is_complete_and_its_gate_counts_agree_with_results(tmp_path):
    """Integrity only (no timings): what the real tree must satisfy whatever its numbers are."""
    stats, _ = real_tree_invariants(H100_ROUND1, tmp_path)
    meta = stats["meta"]
    assert meta["input_gate"] == "RESULT: OK"
    assert (meta["design_commit_short"], meta["amendments"]) == ("ee2e6a7", 3) and meta["later_amendments"][:1] == ["A4"]
    assert stats["host"]["gpu_short"] == "H100" and stats["host"]["arch"] == "x86_64"
    assert stats["host"]["rapids_jar"] == "rapids-4-spark_2.12-26.08.1.jar"
    if (H100_ROUND1.parent / "verify-sf1-30-ref.json").is_file() and (H100_ROUND1.parent / "verify-sf100-300.json").is_file():
        assert "verify-sf1-30-ref.json" in meta["input_gate_source"] and "verify-sf100-300.json" in meta["input_gate_source"]   # read and hash-checked
        assert not [w for w in stats["campaign"]["warnings"] if "input_gate" in w]


B200_ROUND1 = REPO / "rerun" / "hosts" / "b200" / "campaign-b200"


@pytest.mark.skipif(not (B200_ROUND1 / "round-1" / "gates.json").is_file(), reason="the copied B200 campaign is not in this checkout")
def test_the_real_b200_round_one_tree_records_the_real_driver_and_agrees_with_polars_on_every_gpu_cell(tmp_path):
    """Amendment A4: the real B200 driver is recorded; any GPU output disagreement would be an integrity failure (none expected)."""
    stats, _ = real_tree_invariants(B200_ROUND1, tmp_path)
    campaign = json.loads((B200_ROUND1 / "campaign.json").read_text())
    host = stats["host"]
    assert host["driver_version"] and host["driver_version"] in campaign["provenance"]["host"]["gpu"]          # environment and campaign.json agree
    assert (host["gpu_short"], host["arch"], host["vcpus"], host["cpus_arg"]) == ("B200", "x86_64", 26, 26) and host["gpu_memory_gib"] > 170
    assert host["cuda_runtime_version"] and host["cuda_runtime_version"].startswith("12.9")
    meta = stats["meta"]
    assert (meta["design_commit_short"], meta["amendments"], meta["later_amendments"]) == ("afd07c0", 4, ["A5"])   # A5: post-campaign wording clarification
    assert meta["outputs_failed"] == 0 and meta["integrity_failures_in_accepted_rounds"] == 0 and meta["input_gate"] == "RESULT: OK"
    section = stats["output_agreement"]
    for suite, scales in (("baseline", ("sf1", "sf10", "sf30", "sf100", "sf300")), ("complex", ("sf30", "sf100", "sf300"))):
        for scale in scales:
            for engine in AC.GPU_ENGINES:
                cell = section[suite][scale][engine]
                assert cell["failed"] == 0 and cell["compared"] > 0 and cell["max_rel_diff"] is not None and cell["max_rel_diff"] <= AC.REL_TOL, (suite, scale, engine)


def write_rehearsal_preflight(directory, **args):
    directory.mkdir(parents=True)
    (directory / "preflight-report.json").write_text(json.dumps({
        "tool": "preflight", "format": 2, "verdict": "pass", "ok": True, "started_at_utc": "2026-10-30T04:36:08+00:00", "completed_at_utc": "2026-10-30T04:39:31+00:00",
        "counts": {"PASS": 78, "FAIL": 0, "WARN": 1, "INFO": 4, "SKIP": 0, "XFAIL": 2, "COVERAGE": 0}, "checks": [],
        "args": {"formal": "False", "runs": "1", "allow_any_gpu": "True", "min_free_gib": "None", **{k: str(v) for k, v in args.items()}}}))


def test_an_operational_preflight_threshold_is_in_the_host_provenance_and_the_warnings(tmp_path):
    campaigns = tmp_path / "hostdir"
    campaigns.mkdir()
    root = build(campaigns, "campaign-a100", host="A100")
    write_rehearsal_preflight(campaigns / "preflight-host-2", min_free_gib=250)
    out = tmp_path / "out"
    assert AC.main(["--root", str(root), "--output", str(out), "--no-energy"]) == 0
    stats = json.loads((out / "stats.json").read_text())
    entries = {e["path"]: e for e in stats["host"]["preflight"]}
    rehearsal = entries["preflight-host-2"]
    assert (rehearsal["scope"], rehearsal["min_free_gib"], rehearsal["formal"], rehearsal["allow_any_gpu"], rehearsal["runs"]) == ("host rehearsal", 250, False, True, 1)
    assert rehearsal["counts"]["WARN"] == 1 and rehearsal["verdict"] == "pass"
    round_entry = entries["round-1/preflight"]
    assert round_entry["scope"] == "round-1" and round_entry["min_free_gib"] is None and round_entry["formal"] is not None
    warnings = " | ".join(stats["campaign"]["warnings"])
    assert "--min-free-gib 250" in warnings and "not a design parameter" in warnings
    text = (out / "INTEGRITY.md").read_text()
    assert "Operational parameters recorded by preflight (not design parameters)" in text and "--min-free-gib 250" in text
    assert json.loads((out / "integrity.json").read_text())["operational_notes"]


def test_without_operational_thresholds_nothing_is_flagged(tmp_path):
    campaigns = tmp_path / "hostdir"
    campaigns.mkdir()
    root = build(campaigns, "campaign-h100")
    write_rehearsal_preflight(campaigns / "preflight-h100")
    stats = analyze(root)
    assert {e["path"] for e in stats["host"]["preflight"]} == {"preflight-h100", "round-1/preflight", "round-2/preflight", "round-3/preflight", "round-4/preflight"}
    assert not [w for w in stats["campaign"]["warnings"] if "min-free-gib" in w]


A100_ROUND1 = REPO / "rerun" / "hosts" / "a100" / "campaign-a100"


@pytest.mark.skipif(not (A100_ROUND1 / "round-1" / "gates.json").is_file(), reason="the copied A100 campaign is not in this checkout")
def test_the_real_a100_tree_satisfies_the_integrity_invariants_and_shows_its_operational_threshold(tmp_path):
    stats, _ = real_tree_invariants(A100_ROUND1, tmp_path)
    meta, host = stats["meta"], stats["host"]
    assert meta["input_gate"] == "RESULT: OK" and (meta["design_commit_short"], meta["amendments"], meta["later_amendments"]) == ("afd07c0", 4, ["A5"])   # A5: post-campaign wording clarification
    assert (host["gpu_short"], host["arch"], host["vcpus"], host["cpus_arg"], host["gpu_memory_gib"]) == ("A100", "x86_64", 30, 26, 40.0)
    assert "EPYC" in host["cpu_model"] and host["driver_version"] == "570.148.08"
    rehearsal = [e for e in host["preflight"] if e["scope"] == "host rehearsal"]
    if rehearsal:
        assert rehearsal[0]["min_free_gib"] == 250 and any("--min-free-gib 250" in w for w in stats["campaign"]["warnings"])


GH200_ROUND1 = REPO / "rerun" / "hosts" / "gh200" / "campaign-gh200"


@pytest.mark.skipif(not (GH200_ROUND1 / "round-1" / "gates.json").is_file(), reason="the copied GH200 campaign is not in this checkout")
def test_the_real_gh200_tree_parses_as_aarch64_and_satisfies_the_same_integrity_invariants(tmp_path):
    stats, _ = real_tree_invariants(GH200_ROUND1, tmp_path)
    meta, host = stats["meta"], stats["host"]
    assert (meta["design_commit_short"], meta["amendments"], meta["later_amendments"]) == ("afd07c0", 4, ["A5"])   # A5: post-campaign wording clarification
    # aarch64 specifics: lscpu 'Architecture: aarch64' / 'Model name: Neoverse-V2', GH200 from nvidia-smi, the arm64 jar classifier
    assert (host["gpu_short"], host["arch"], host["vcpus"], host["cpus_arg"]) == ("GH200", "aarch64", 64, 26)
    assert host["cpu_model"] and "Neoverse" in host["cpu_model"] and host["gpu_memory_gib"] > 90 and host["rapids_jar"].endswith("-cuda12-arm64.jar")
    assert host["gpu_policy"].startswith("any_gpu") and meta["input_gate"] == "RESULT: OK"


def test_a_verification_report_that_changed_since_the_campaign_started_is_ignored(tmp_path):
    root = build(tmp_path)
    report = root / "inputs-verification.json"
    data = json.loads(report.read_text())
    data["ok"], data["result"] = False, "FAILED"
    report.write_text(json.dumps(data))
    stats = analyze(root)                               # sha256 differs from campaign.json: not trusted, the preflight check decides
    assert "sha256 differs" in " ".join(stats["campaign"]["warnings"])
    assert stats["meta"]["input_gate"] == "RESULT: OK" and "preflight" in stats["meta"]["input_gate_source"]


# ------------------------------------------------------------------ null rules and flagging

def notes_of(stats):
    return [w for w in stats["campaign"]["warnings"] if w.startswith("meta.") or "meta." in w[:40]]


def test_gate_counts_come_from_the_preflight_reports_and_are_cross_checked_against_results(tmp_path):
    root = build(tmp_path)
    stats = analyze(root)
    meta = stats["meta"]
    gate_outputs = gate_barriers = 0
    for report in sorted(root.glob("round-*/preflight/preflight-report.json")):
        for check in json.loads(report.read_text())["checks"]:
            if check["id"].endswith(":agreement"):
                gate_outputs += int(re.match(r"all (\d+) records match", check["detail"])[1])
            if check["id"].endswith(":contract"):
                gate_barriers += int(re.match(r"(\d+) verified_gc barriers passed", check["detail"])[1])
    assert meta["outputs_compared"] == gate_outputs > 2000 and meta["cleanup_barriers_passed"] == gate_barriers > 1000
    assert not any("DISAGREEMENT" in w for w in stats["campaign"]["warnings"])        # results.json agrees with what the gate counted
    # change what the gate says it counted: the reported value follows the gate, the difference is logged
    path = root / "round-2" / "preflight" / "preflight-report.json"
    report = json.loads(path.read_text())
    for check in report["checks"]:
        if check["id"].endswith(":agreement"):
            check["detail"] = "all 5 records match the reference engine within rel 1e-08 / abs 1e-06 (integers exact)"
            break
    for check in report["checks"]:
        if check["id"].endswith(":contract"):
            check["detail"] = "1 verified_gc barriers passed"
            break
    path.write_text(json.dumps(report))
    changed = analyze(root)
    warnings = " | ".join(changed["campaign"]["warnings"])
    assert "DISAGREEMENT meta.outputs_compared" in warnings and "DISAGREEMENT meta.cleanup_barriers_passed" in warnings
    assert changed["meta"]["outputs_compared"] != meta["outputs_compared"] and changed["meta"]["cleanup_barriers_passed"] != meta["cleanup_barriers_passed"]


def test_a_barrier_after_a_failed_action_counts_like_the_gate_counts_it(tmp_path):
    root = build(tmp_path, failed_gate_rounds=())
    for report in root.glob("round-*/preflight/preflight-report.json"):
        report.unlink()                                   # no gate report: the count is recomputed from results.json
    base = analyze(root, allow_ungated=True)["meta"]["cleanup_barriers_passed"]
    path = root / "round-1" / "complex" / "sf30" / "polars_gpu" / "results.json"
    session = json.loads(path.read_text())
    session["failed_query_cleanup_records"] = [{"query": "cx_q13", "status": "passed", "policy": "verified_gc"}]
    path.write_text(json.dumps(session))
    stats = analyze(root, allow_ungated=True)
    assert stats["meta"]["cleanup_barriers_passed"] == base + 1
    assert any("not every accepted round has a passing preflight report" in w for w in stats["campaign"]["warnings"])


def test_a_round_whose_preflight_report_did_not_pass_is_not_a_source_for_gate_counts(tmp_path):
    root = build(tmp_path)
    path = root / "round-3" / "preflight" / "preflight-report.json"
    report = json.loads(path.read_text())
    report["verdict"] = "fail"
    path.write_text(json.dumps(report))
    stats = analyze(root)
    assert stats["meta"]["outputs_compared"] > 2000                              # recomputed from the rows instead
    assert any("not every accepted round has a passing preflight report" in w for w in stats["campaign"]["warnings"])


def test_values_that_cannot_be_determined_are_null_not_zero_and_the_reason_is_logged(tmp_path):
    root = build(tmp_path)
    for path in root.glob("round-*/*/sf*/*/results.json"):
        session = json.loads(path.read_text())
        for record in session["records"]:
            record.pop("rows", None)
        path.write_text(json.dumps(session))
    stats = analyze(root)
    meta = stats["meta"]
    assert meta["outputs_compared"] > 2000 and meta["outputs_failed"] == 0         # the gates counted them and passed G2
    assert meta["max_rel_diff"] is None                                            # the gate reports only pass/fail, not the size
    assert any("meta.max_rel_diff is null" in w and "no saved output rows" in w for w in stats["campaign"]["warnings"])
    for report in root.glob("round-*/preflight/preflight-report.json"):
        report.unlink()
    stats = analyze(root, allow_ungated=True)
    meta, warnings = stats["meta"], " | ".join(stats["campaign"]["warnings"])
    assert meta["outputs_compared"] is None and meta["outputs_failed"] is None and meta["max_rel_diff"] is None
    assert meta["spark_gpu_plans_checked"] is None
    for key in ("outputs_compared", "outputs_failed", "max_rel_diff", "spark_gpu_plans_checked"):
        assert f"meta.{key} is null" in warnings, key
    assert meta["sessions_completed"] == 4 * 32 and meta["measured_trials"] > 0 and meta["cleanup_barriers_passed"] > 0   # results.json alone
    assert any(w.startswith("meta.") or "meta." in w for w in stats["campaign"]["warnings"])


def test_every_null_meta_value_is_explained_in_the_warnings_and_in_integrity_md(tmp_path):
    root = build(tmp_path)
    (root / "inputs-verification.json").unlink()
    for report in root.glob("round-*/preflight/preflight-report.json"):
        report.unlink()
    out = tmp_path / "out"
    assert AC.main(["--root", str(root), "--output", str(out), "--no-energy", "--allow-ungated", "--design-doc", str(tmp_path / "nowhere.md")]) == 0
    meta = json.loads((out / "stats.json").read_text())["meta"]
    nulls = [k for k, v in meta.items() if v is None]
    assert {"input_gate", "amendments", "spark_gpu_plans_checked"} <= set(nulls) and "outputs_compared" not in nulls   # rows remain, so Polars' outputs can be compared
    warnings = " | ".join(json.loads((out / "stats.json").read_text())["campaign"]["warnings"])
    text = (out / "INTEGRITY.md").read_text()
    for key in nulls:
        if key in ("input_gate_source", "design_doc_matches_campaign", "design_commit_short"):         # explained by their parent keys
            continue
        assert f"meta.{key} is null" in warnings, key
        assert f"meta.{key} is null" in text, key
    assert "Sources, gaps and disagreements of the meta values" in text


def test_times_fall_back_to_status_json_when_sessions_carry_no_start(tmp_path):
    root = build(tmp_path)
    for path in root.glob("round-*/*/schedule.json"):
        path.write_text(json.dumps([{k: v for k, v in item.items() if k not in ("start_utc", "process_wall_seconds")} for item in json.loads(path.read_text())]))
    status = json.loads((root / "status.json").read_text())
    stats = analyze(root)
    assert stats["meta"]["campaign_start_utc"] == AC.format_utc(AC.parse_utc(status["started_utc"]))
    assert stats["meta"]["campaign_end_utc"] is not None
    assert any("campaign_start_utc: no session carries start_utc" in w for w in stats["campaign"]["warnings"])


def test_a_campaign_without_spark_gpu_has_zero_plans_and_a_campaign_without_rounds_has_none(tmp_path):
    root = build(tmp_path)
    for path in root.glob("round-*/*/sf*/spark_gpu"):
        shutil.rmtree(path)
    for path in root.glob("round-*/*/schedule.json"):
        path.write_text(json.dumps([i for i in json.loads(path.read_text()) if i["engine"] != "spark_gpu"]))
    assert analyze(root)["meta"]["spark_gpu_plans_checked"] == 0
    empty = build(tmp_path, "failed", failed_gate_rounds=(1, 2, 3, 4))
    meta = analyze(empty)["meta"]
    assert meta["rounds_completed"] == 0 and meta["sessions_completed"] is None and meta["spark_gpu_plans_checked"] is None
    assert meta["campaign_start_utc"] is None and meta["cleanup_barriers_passed"] is None


def test_an_unclassified_failure_in_an_accepted_round_is_flagged_loudly(tmp_path):
    extra = [(r, "complex", "300", "cx_distinct", "spark_gpu", ("RuntimeError", "CUDA error: illegal memory access")) for r in (1, 2, 3, 4)]
    extra.append((2, "baseline", "30", "q10", "spark_gpu", ("TimeoutError", "action exceeded the timeout")))
    extra.append((3, "baseline", "30", "q03", "polars_gpu", ("MemoryError", "std::bad_alloc: out_of_memory")))   # a coverage outcome: not flagged
    root = build(tmp_path, extra_failures=extra)
    out = tmp_path / "out"
    assert AC.main(["--root", str(root), "--output", str(out), "--no-energy"]) == 0
    stats = json.loads((out / "stats.json").read_text())
    assert stats["meta"]["integrity_failures_in_accepted_rounds"] == 5
    integrity = json.loads((out / "integrity.json").read_text())
    kinds = sorted((c["kind"], c["query"]) for c in integrity["integrity_failures_in_accepted_rounds"])
    assert kinds == [("error", "cx_distinct")] * 4 + [("timeout", "q10")]
    text = (out / "INTEGRITY.md").read_text()
    assert "INTEGRITY DISAGREEMENT: 5 cell(s) of ACCEPTED rounds" in text and "gate and this analysis disagree" in text
    assert "| 2 | baseline | sf30 | Spark GPU | q10 | timeout |" in text
    clean = tmp_path / "clean"
    AC.main(["--root", str(build(tmp_path, "ok")), "--output", str(clean), "--no-energy"])
    assert "INTEGRITY DISAGREEMENT" not in (clean / "INTEGRITY.md").read_text()


def test_unsupported_and_out_of_memory_are_coverage_outcomes_not_integrity_failures(tmp_path):
    root = build(tmp_path)          # the default tree has the pre-declared gaps (unsupported) and an H100 OOM
    stats = analyze(root)
    classes = {cell["reason_class"] for by_query in stats["coverage"]["matrix"].values() for by_scale in by_query.values()
               for by_engine in by_scale.values() for cell in by_engine.values() if cell["status"] == "failed"}
    assert classes == {"unsupported_operation", "out_of_memory"} and stats["meta"]["integrity_failures_in_accepted_rounds"] == 0


def test_cli_options_for_the_report_and_the_design_document(tmp_path):
    root = build(tmp_path)
    report = tmp_path / "elsewhere.json"
    shutil.copy(root / "inputs-verification.json", report)
    doc = tmp_path / "doc.md"
    doc.write_text(doc_text(2))
    out = tmp_path / "out"
    assert AC.main(["--root", str(root), "--output", str(out), "--no-energy", "--inputs-report", str(report), "--design-doc", str(doc)]) == 0
    stats = json.loads((out / "stats.json").read_text())
    meta = stats["meta"]
    assert str(report) in meta["input_gate_source"] and meta["input_gate"] == "RESULT: OK"
    # the document given is not the version the campaign recorded: no amendment count is claimed for it, and the reason is logged
    assert meta["design_doc_matches_campaign"] is False and meta["amendments"] is None
    assert any("matches no commit" in w for w in stats["campaign"]["warnings"])


def test_host_facts_in_meta_equal_the_host_block(tmp_path):
    for host in ("A100", "GH200"):
        stats = analyze(build(tmp_path, host, host=host))
        for key, source in (("gpu_name", "gpu"), ("cpu_model", "cpu_model"), ("driver_version", "driver_version")):
            assert stats["meta"][key] == stats["host"][source] is not None
