"""Cross-host analysis (analyze_campaign.py, amendments A2/A3): host identity, Welch-type ratios, caveats, outputs.

Every host is its own synthetic campaign (analyze_campaign.make_synthetic_campaign(host=...)) with injected per-engine
time factors versus H100 and its own out-of-memory cells. Noise-free trees must recover the injected host/H100 ratios
exactly; noisy trees must bracket them. Data are never pooled across hosts.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import analyze_campaign as AC  # noqa: E402

QUIET = dict(round_sigma=0.0, query_sigma=0.0, trial_sigma=0.0, position_effect=0.0, gpu_interval=60.0)
HOSTS = tuple(AC.SYNTH_HOSTS)          # H100 first, then B200, A100, GH200


def build_hosts(base, seed, energy=False, **kwargs):
    """{host: (root, truth, stats)} for every synthetic host."""
    out = {}
    for host in HOSTS:
        root = Path(base) / host
        truth = AC.make_synthetic_campaign(root, seed=seed, host=host, a100=False, side_study=False, **kwargs)
        out[host] = (root, truth, json.loads(json.dumps(AC.analyze(root, energy=energy))))
    return out


@pytest.fixture(scope="module")
def quiet(tmp_path_factory):
    hosts = build_hosts(tmp_path_factory.mktemp("quiet-hosts"), 3, **QUIET)
    cross = json.loads(json.dumps(AC.compare_hosts({h: v[2] for h, v in hosts.items()})))
    return hosts, cross


@pytest.fixture(scope="module")
def noisy(tmp_path_factory):
    hosts = build_hosts(tmp_path_factory.mktemp("noisy-hosts"), 11, energy=True, gpu_interval=60.0)
    cross = json.loads(json.dumps(AC.compare_hosts({h: v[2] for h, v in hosts.items()})))
    return hosts, cross


# ------------------------------------------------------------------ Welch-type interval

def pooled_t_interval(a, b):
    """Textbook equal-variance, equal-n two-sample t interval for the difference of mean logs, exponentiated."""
    la, lb = [math.log(x) for x in a], [math.log(x) for x in b]
    n = len(a)
    ma, mb = sum(la) / n, sum(lb) / n
    sp2 = (sum((x - ma) ** 2 for x in la) + sum((x - mb) ** 2 for x in lb)) / (2 * n - 2)
    half = AC.t_critical(2 * n - 2) * math.sqrt(sp2 * 2 / n)
    return math.exp(ma - mb - half), math.exp(ma - mb), math.exp(ma - mb + half)


def test_welch_equals_pooled_t_when_variances_and_sizes_are_equal():
    a, b = [2.0, 2.2, 1.9, 2.1], [1.0, 1.1, 0.95, 1.05]
    # equalise the log-variances exactly: shift b's logs so both samples have the same spread
    la = [math.log(x) for x in a]
    mean_a = sum(la) / 4
    b = [math.exp(math.log(0.5) + (x - mean_a)) for x in la]
    summary = AC.welch_summary(a, b)
    low, est, high = pooled_t_interval(a, b)
    assert summary["df"] == pytest.approx(6.0)             # 2 (n - 1)
    assert (summary["ci_low"], summary["estimate"], summary["ci_high"]) == pytest.approx((low, est, high))
    assert summary["estimate"] == pytest.approx(math.sqrt(summary["ci_low"] * summary["ci_high"]))  # symmetric on the log scale


def test_welch_df_is_satterthwaite_for_unequal_variances():
    a, b = [1.0, 1.01, 0.99, 1.0], [2.0, 3.0, 1.5, 4.0]
    summary = AC.welch_summary(a, b)
    va = AC.mean_sd([math.log(x) for x in a])[1] ** 2 / 4
    vb = AC.mean_sd([math.log(x) for x in b])[1] ** 2 / 4
    expected_df = (va + vb) ** 2 / (va ** 2 / 3 + vb ** 2 / 3)
    assert summary["df"] == pytest.approx(expected_df)
    assert 3.0 <= summary["df"] < 6.0                      # between n - 1 and 2 (n - 1), close to the noisy sample's df
    half = AC.t_quantile(0.975, expected_df) * math.sqrt(va + vb)
    mean_diff = sum(math.log(x) for x in a) / 4 - sum(math.log(x) for x in b) / 4
    assert summary["ci_low"] == pytest.approx(math.exp(mean_diff - half))
    assert summary["ci_high"] == pytest.approx(math.exp(mean_diff + half))


def test_welch_is_antisymmetric_in_the_two_hosts():
    a, b = [1.2, 1.3, 1.1, 1.25], [2.0, 2.4, 1.9, 2.2, 2.1]
    forward, backward = AC.welch_summary(a, b), AC.welch_summary(b, a)
    assert forward["estimate"] * backward["estimate"] == pytest.approx(1.0)
    assert forward["ci_low"] == pytest.approx(1.0 / backward["ci_high"])
    assert forward["ci_high"] == pytest.approx(1.0 / backward["ci_low"])
    assert forward["df"] == pytest.approx(backward["df"])
    assert (forward["n_host"], forward["n_ref"]) == (4, 5)


def test_welch_edge_cases_single_round_zero_spread_and_empty():
    one = AC.welch_summary([2.0], [1.0, 1.1, 0.9])
    assert one["estimate"] == pytest.approx(2.0 / (1.0 * 1.1 * 0.9) ** (1 / 3)) and one["ci_low"] is None and one["df"] is None
    flat = AC.welch_summary([2.0, 2.0, 2.0], [1.0, 1.0, 1.0])
    assert flat["estimate"] == pytest.approx(2.0) and flat["ci_low"] == flat["ci_high"] == flat["estimate"] and flat["df"] == 4.0
    empty = AC.welch_summary([], [1.0, 2.0])
    assert empty["estimate"] is None and empty["n_host"] == 0
    half_flat = AC.welch_summary([2.0, 2.0, 2.0], [1.0, 1.2, 0.8, 1.1])      # one side without spread
    assert half_flat["df"] == pytest.approx(3.0) and half_flat["ci_low"] < half_flat["estimate"] < half_flat["ci_high"]


# ------------------------------------------------------------------ host identity

def test_host_identity_is_read_from_the_environment_inventory_and_campaign_json(quiet):
    hosts, _ = quiet
    h100, gh200, a100 = hosts["H100"][2]["host"], hosts["GH200"][2]["host"], hosts["A100"][2]["host"]
    assert (h100["label"], h100["gpu_short"], h100["arch"], h100["vcpus"], h100["host_ram_gib"]) == ("H100", "H100", "x86_64", 26, 225.0)
    assert (gh200["label"], gh200["arch"], gh200["vcpus"], gh200["cpus_arg"], gh200["gpu_memory_gib"]) == ("GH200", "aarch64", 64, 26, 95.6)
    assert a100["gpu_memory_gib"] == 40.0 and a100["driver_version"] == "570.0.1" and a100["cuda_version"] == "12.8"
    assert a100["instance_type"] == "gpu_1x_a100_sxm4" and "aarch64" in gh200["cpu_model"]
    assert h100["gpu_policy"] == "h100_required" and a100["gpu_policy"].startswith("any_gpu")
    assert h100["synthetic"] is True and "inventory:lscpu" in h100["source"]


def test_host_label_override_and_unrecorded_fields_stay_none(tmp_path):
    root = tmp_path / "tree"
    AC.make_synthetic_campaign(root, seed=2, a100=False, side_study=False, **QUIET)
    for path in root.rglob("environment.json"):
        env = json.loads(path.read_text())
        env.pop("inventory")
        env["resource_allocation"].pop("host_ram_gib_approx")
        path.write_text(json.dumps(env))
    meta = json.loads((root / "campaign.json").read_text())
    meta["provenance"]["host"].update(cpu_model=None, memory_total_bytes=None)
    (root / "campaign.json").write_text(json.dumps(meta))
    host = AC.analyze(root, energy=False, label="MyBox")["host"]
    assert host["label"] == "MyBox"
    assert host["cpu_model"] is None and host["host_ram_gib"] is None and host["driver_version"] == "580.0.1"
    # the architecture falls back to the platform string recorded by run_campaign.py
    assert host["arch"] == "x86_64"


@pytest.mark.parametrize("lscpu, platform, cpu, expected", [
    ("Architecture:  aarch64\n", None, None, "aarch64"), ("Architecture:  x86_64\n", None, None, "x86_64"),
    (None, "Linux-6.8.0-1021-nvidia-64k-aarch64-with-glibc2.35", None, "aarch64"),
    (None, None, "Neoverse-V2", "aarch64"), (None, None, "Intel(R) Xeon(R) Platinum 8480+", "x86_64"),
    (None, "macOS-26.6-arm64-arm-64bit", None, "aarch64"), (None, None, None, None)])
def test_host_architecture_sources(lscpu, platform, cpu, expected):
    assert AC.host_architecture(lscpu, platform, cpu) == expected


def test_real_run_campaign_layout_is_understood(tmp_path):
    """campaign.json as run_campaign.py writes it: design block and provenance, no legacy top-level keys."""
    root = tmp_path / "tree"
    AC.make_synthetic_campaign(root, seed=2, a100=False, side_study=False, **QUIET)
    meta = json.loads((root / "campaign.json").read_text())
    real = {"design": meta["design"], "design_sha256": "abc", "provenance": {**meta["provenance"], "design_doc_sha256": "def"}}
    (root / "campaign.json").write_text(json.dumps(real))
    stats = AC.analyze(root, energy=False)
    assert stats["campaign"]["design"]["seed"] == 20261029 and stats["campaign"]["design"]["sequences"] == ["ABDC", "BCAD", "CDBA", "DACB"]
    assert stats["campaign"]["provenance"]["design_doc_sha256"] == "def" and stats["campaign"]["provenance"]["design_sha256"] == "abc"
    assert stats["host"]["instance_type"] == "gpu_1x_h100_sxm5" and stats["synthetic"] is False


# ------------------------------------------------------------------ recovery of the injected host ratios

def test_noise_free_hosts_recover_every_injected_ratio_exactly(quiet):
    hosts, cross = quiet
    checked = suite_checked = 0
    for host in ("B200", "A100", "GH200"):
        truth = hosts[host][1]["vs_reference"]
        for engine, by_suite in cross["ratio"][host].items():
            for suite, by_scale in by_suite.items():
                for scale, record in by_scale.items():
                    expected = truth[suite][scale]["suite"][engine]
                    assert record["estimate"] == pytest.approx(expected, rel=1e-9), (host, engine, suite, scale)
                    assert record["ci_low"] == pytest.approx(record["estimate"]) == pytest.approx(record["ci_high"])
                    suite_checked += 1
                    for query, q in cross["ratio_query"][host][engine][suite][scale].items():
                        expected_q = truth[suite][scale]["query"][engine][query]
                        if q["status"] == "ok":
                            assert q["estimate"] == pytest.approx(expected_q, rel=1e-9), (host, engine, suite, scale, query)
                            checked += 1
                        else:
                            assert expected_q is None or q["status"] != "ok"
    assert checked > 400 and suite_checked == 3 * 4 * 8


def test_reference_host_is_h100_and_ladder_order_is_by_gpu_generation(quiet):
    _, cross = quiet
    assert cross["reference"] == "H100" and cross["hosts"] == ["A100", "H100", "GH200", "B200"]
    assert "H100" not in cross["ratio"] and set(cross["ratio"]) == {"A100", "GH200", "B200"}
    assert AC.ladder_sorted(["B200", "X1", "H100", "A100"]) == ["A100", "H100", "B200", "X1"]
    assert cross["host_info"]["H100"]["role"] == "reference" and cross["host_info"]["B200"]["role"] == "comparison"


def test_gpu_generation_ordering_of_the_injected_gpu_engines(quiet):
    """The injected factors put A100 slowest and B200 fastest for the GPU engines at every scale."""
    _, cross = quiet
    for engine in AC.GPU_ENGINES:
        for suite, by_scale in cross["ratio"]["A100"][engine].items():
            for scale in by_scale:
                a100 = cross["ratio"]["A100"][engine][suite][scale]["estimate"]
                gh200 = cross["ratio"]["GH200"][engine][suite][scale]["estimate"]
                b200 = cross["ratio"]["B200"][engine][suite][scale]["estimate"]
                assert a100 > 1.0 > gh200 > b200, (engine, suite, scale)


def test_cells_that_ran_out_of_memory_on_one_host_leave_its_ratios_and_are_listed(quiet):
    hosts, cross = quiet
    record = cross["ratio"]["A100"]["polars_gpu"]["baseline"]["sf300"]
    assert record["n_queries"] == 4 and set(record["queries_excluded"]) == {"q03", "q10"}
    assert "out_of_memory" in record["queries_excluded"]["q03"] and "A100" in record["queries_excluded"]["q03"]
    q = cross["ratio_query"]["A100"]["polars_gpu"]["baseline"]["sf300"]["q03"]
    assert q["status"] == "unavailable" and q["estimate"] is None and q["blocked_by"] == [{"host": "A100", "reason_class": "out_of_memory"}]
    # the same engine at a scale where nothing failed keeps all six queries
    assert cross["ratio"]["A100"]["polars_gpu"]["baseline"]["sf100"]["n_queries"] == 6
    rows = cross["coverage_differences"]["A100"]
    cells = {(r["suite"], r["scale"], r["query"], r["engine"]) for r in rows}
    assert ("baseline", "sf300", "q03", "polars_gpu") in cells and ("complex", "sf100", "cx_self_join", "polars_gpu") in cells
    assert all(r["host_status"] != r["ref_status"] or r["host_reason_class"] != r["ref_reason_class"] for r in rows)
    # the pre-declared Polars GPU gaps fail the same way on every host, so they are not differences
    assert not any(r["query"] in ("cx_q13", "cx_window") for r in rows)
    assert cross["coverage_differences"]["B200"] == [] or all(r["ref_status"] != "supported" for r in cross["coverage_differences"]["B200"])
    # A100 fails cx_self_join at SF100 and SF300 while H100 fails only SF300: the SF100 cell exists on H100 only
    q = cross["ratio_query"]["A100"]["polars_gpu"]["complex"]["sf100"]["cx_self_join"]
    assert q["status"] == "unavailable" and q["blocked_by"][0]["host"] == "A100"


def test_cell_that_failed_on_the_reference_only_is_unavailable_on_the_reference_side(quiet):
    _, cross = quiet
    q = cross["ratio_query"]["B200"]["polars_gpu"]["complex"]["sf300"]["cx_self_join"]    # H100 ran out of memory, B200 did not
    assert q["status"] == "unavailable" and q["blocked_by"] == [{"host": "H100", "reason_class": "out_of_memory"}]
    assert q["host_seconds"] is not None and q["ref_seconds"] is None and q["estimate"] is None
    assert "cx_self_join" in cross["ratio"]["B200"]["polars_gpu"]["complex"]["sf300"]["queries_excluded"]


def test_noisy_hosts_have_welch_intervals_that_bracket_the_truth(noisy):
    hosts, cross = noisy
    hits = total = 0
    for host in ("B200", "A100", "GH200"):
        truth = hosts[host][1]["vs_reference"]
        for engine, by_suite in cross["ratio"][host].items():
            for suite, by_scale in by_suite.items():
                for scale, record in by_scale.items():
                    assert record["n_host"] == 4 and record["n_ref"] == 4
                    assert 3.0 <= record["df"] <= 6.0 + 1e-9
                    assert record["ci_low"] < record["estimate"] < record["ci_high"]
                    assert record["estimate"] == pytest.approx(math.sqrt(record["ci_low"] * record["ci_high"]))
                    expected = truth[suite][scale]["suite"][engine]
                    assert record["estimate"] == pytest.approx(expected, rel=0.2)
                    hits += record["ci_low"] <= expected <= record["ci_high"]
                    total += 1
    assert hits / total >= 0.8, f"only {hits}/{total} 95% intervals contain the injected host ratio"


def test_reciprocal_when_the_reference_is_switched(quiet):
    hosts, _ = quiet
    stats = {h: v[2] for h, v in hosts.items()}
    forward, backward = AC.compare_hosts(stats, "H100"), AC.compare_hosts(stats, "B200")
    a = forward["ratio"]["B200"]["polars"]["baseline"]["sf30"]["estimate"]
    b = backward["ratio"]["H100"]["polars"]["baseline"]["sf30"]["estimate"]
    assert a * b == pytest.approx(1.0) and backward["reference"] == "B200"
    with pytest.raises(ValueError):
        AC.compare_hosts(stats, "TPU")


def test_rounds_are_not_pooled_across_hosts(tmp_path):
    """A failed-gate round on one host leaves that host with 3 rounds; the other host keeps 4; df and caveat follow."""
    h100 = AC.make_synthetic_campaign(tmp_path / "h100", seed=5, a100=False, side_study=False)
    AC.make_synthetic_campaign(tmp_path / "b200", seed=5, host="B200", a100=False, side_study=False, failed_gate_rounds=(3,))
    stats = {"H100": AC.analyze(tmp_path / "h100", energy=False), "B200": AC.analyze(tmp_path / "b200", energy=False)}
    cross = json.loads(json.dumps(AC.compare_hosts(stats)))
    record = cross["ratio"]["B200"]["polars"]["baseline"]["sf30"]
    assert (record["n_host"], record["n_ref"]) == (3, 4) and record["host_rounds"] == [1, 2, 4] and record["ref_rounds"] == [1, 2, 3, 4]
    assert 2.0 <= record["df"] <= 5.0
    assert any("Rounds differ" in note for note in cross["caveats_by_host"]["B200"])
    assert h100["host"] == "H100"


# ------------------------------------------------------------------ caveats and speedups per host

def test_caveats_name_architecture_cpu_memory_and_driver_differences(quiet):
    _, cross = quiet
    gh200 = " ".join(cross["caveats_by_host"]["GH200"])
    assert "architecture differs (aarch64 vs x86_64)" in gh200 and "CPU model differs" in gh200
    assert "vCPU count differs (64 vs 26)" in gh200 and "--cpus 26" in gh200 and "Host RAM differs" in gh200
    a100 = " ".join(cross["caveats_by_host"]["A100"])
    assert "Device memory differs (40.0 vs 79.6 GiB)" in a100 and "Driver / CUDA differ" in a100 and "570.0.1" in a100
    assert "architecture differs" not in a100
    assert cross["identity_differences"]["GH200"]["arch"] == ["aarch64", "x86_64"]
    assert any("confounds GPU generation" in c for c in cross["caveats"]) and any("never pooled" in c for c in cross["caveats"])


def test_unrecorded_identity_is_called_out(tmp_path):
    for host in ("H100", "B200"):
        AC.make_synthetic_campaign(tmp_path / host, seed=1, host=host, a100=False, side_study=False, **QUIET)
    for path in (tmp_path / "B200").rglob("environment.json"):
        env = json.loads(path.read_text())
        env.pop("inventory")
        path.write_text(json.dumps(env))
    meta = json.loads((tmp_path / "B200" / "campaign.json").read_text())
    meta["provenance"]["host"].update(cpu_model=None)
    (tmp_path / "B200" / "campaign.json").write_text(json.dumps(meta))
    cross = AC.compare_hosts({h: AC.analyze(tmp_path / h, energy=False) for h in ("H100", "B200")})
    note = " ".join(cross["caveats_by_host"]["B200"])
    assert "Host identity not recorded for" in note and "cpu_model" in note


def test_mixed_real_and_synthetic_hosts_are_flagged(quiet, tmp_path):
    hosts, _ = quiet
    stats = {h: json.loads(json.dumps(v[2])) for h, v in hosts.items() if h in ("H100", "B200")}
    stats["B200"]["synthetic"] = False
    stats["B200"]["host"]["synthetic"] = False
    cross = AC.compare_hosts(stats)
    assert cross["synthetic"] is True and any("synthetic" in n for n in cross["caveats_by_host"]["B200"])


def test_speedups_are_each_hosts_own_campaign_and_are_not_combined(quiet):
    hosts, cross = quiet
    for host in cross["hosts"]:
        own = hosts[host][2]["c1"]["baseline"]["sf300"]
        copied = cross["speedup"]["c1"][host]["baseline"]["sf300"]
        assert copied["estimate"] == pytest.approx(own["estimate"]) and copied["n_rounds"] == own["n_rounds"] and copied["df"] == own["df"]
    assert set(cross["speedup"]["c1"]) == set(cross["hosts"])           # the reference is listed too
    # GPU speedup over the CPU engine grows with the GPU generation
    sf300 = {h: cross["speedup"]["c1"][h]["baseline"]["sf300"]["estimate"] for h in cross["hosts"]}
    assert sf300["A100"] < sf300["H100"] < sf300["GH200"] < sf300["B200"]


# ------------------------------------------------------------------ command line and outputs

def host_args(hosts, names=HOSTS):
    out = []
    for name in names:
        out += ["--host", f"{name}={hosts[name][0]}"]
    return out


def test_cross_host_cli_writes_everything_and_analyzes_each_root(quiet, tmp_path):
    hosts, _ = quiet
    out = tmp_path / "x"
    assert AC.main([*host_args(hosts), "--output", str(out), "--no-energy"]) == 0
    for name in ("cross_host.json", "cross_host_flat.json", "CROSS_HOST_REPORT.md", "STATS_SCHEMA.md", "SYNTHETIC_NOT_RESULTS.txt",
                 "xhost_A100.json", "xhost_B200.json", "xhost_GH200.json"):
        assert (out / name).is_file(), name
    assert not (out / "xhost_H100.json").exists()
    for host in HOSTS:
        stats = json.loads((out / "hosts" / host / "stats.json").read_text())
        assert stats["host"]["label"] == host and stats["campaign"]["rounds_included"] == [1, 2, 3, 4]
    cross = json.loads((out / "cross_host.json").read_text())
    assert cross["synthetic"] is True and cross["watermark"] == AC.WATERMARK and cross["reference"] == "H100"
    tables = sorted((out / "tables").glob("x*.csv"))
    assert [t.stem for t in tables] == ["x01_hosts", "x02_suite_ratios", "x03_query_ratios", "x04_speedup_by_host", "x05_coverage_differences", "x06_output_agreement", "x07_plan_variability", "x08_gpu_telemetry"]
    for csv_path in tables:
        rows = list(csv.DictReader(csv_path.open()))
        assert all(r["data_status"] == AC.WATERMARK for r in rows), csv_path.name
        tex = csv_path.with_suffix(".tex").read_text()
        assert AC.WATERMARK_ASCII in tex and r"\toprule" in tex, csv_path.name
    report = (out / "CROSS_HOST_REPORT.md").read_text()
    assert AC.WATERMARK in report and "aarch64" in report and "Welch" in report and "A100 / H100" in report


def test_cross_host_cli_accepts_stats_json_files_and_matches_the_root_analysis(quiet, tmp_path):
    hosts, cross_from_roots = quiet
    per_host = tmp_path / "per-host"
    for name in ("H100", "B200"):
        assert AC.main(["--root", str(hosts[name][0]), "--output", str(per_host / name), "--no-energy"]) == 0
    out = tmp_path / "x"
    assert AC.main(["--host", f"H100={per_host / 'H100' / 'stats.json'}", "--host", f"B200={per_host / 'B200'}", "--output", str(out)]) == 0
    cross = json.loads((out / "cross_host.json").read_text())
    assert cross["hosts"] == ["H100", "B200"]
    for engine in AC.ENGINE_ORDER:
        a = cross["ratio"]["B200"][engine]["baseline"]["sf30"]["estimate"]
        assert a == pytest.approx(cross_from_roots["ratio"]["B200"][engine]["baseline"]["sf30"]["estimate"])
    assert not (out / "hosts").exists()          # stats.json inputs were not re-analyzed, so there is nothing to write per host


def test_cli_label_is_recorded_and_reference_option_works(quiet, tmp_path):
    hosts, _ = quiet
    assert AC.main(["--root", str(hosts["B200"][0]), "--output", str(tmp_path / "one"), "--label", "Blackwell-box", "--no-energy"]) == 0
    assert json.loads((tmp_path / "one" / "stats.json").read_text())["host"]["label"] == "Blackwell-box"
    assert AC.main([*host_args(hosts, ("H100", "B200")), "--reference", "B200", "--output", str(tmp_path / "ref"), "--no-energy"]) == 0
    cross = json.loads((tmp_path / "ref" / "cross_host.json").read_text())
    assert cross["reference"] == "B200" and list(cross["ratio"]) == ["H100"]


def test_cli_rejects_bad_host_arguments_and_combinations(quiet, tmp_path, capsys):
    hosts, _ = quiet
    with pytest.raises(SystemExit):
        AC.main(["--host", "H100", "--output", str(tmp_path / "a")])                       # no LABEL=PATH
    with pytest.raises(SystemExit):
        AC.main(["--host", f"H100={hosts['H100'][0]}", "--host", f"H100={hosts['B200'][0]}", "--output", str(tmp_path / "b")])
    with pytest.raises(SystemExit):
        AC.main(["--host", f"H100={hosts['H100'][0]}", "--root", str(hosts["H100"][0]), "--output", str(tmp_path / "c")])
    with pytest.raises(SystemExit):
        AC.main(["--synthetic", "--synthetic-hosts", "B200,A100", "--output", str(tmp_path / "d")])   # H100 is required
    assert AC.parse_host_args(["H100=/a", "B200=/b"]) == [("H100", "/a"), ("B200", "/b")]


def test_host_without_accepted_rounds_is_reported_and_left_out(quiet, tmp_path, capsys):
    hosts, _ = quiet
    bad = tmp_path / "bad"
    AC.make_synthetic_campaign(bad, seed=1, host="GH200", a100=False, side_study=False, failed_gate_rounds=(1, 2, 3, 4), **QUIET)
    out = tmp_path / "x"
    code = AC.main([*host_args(hosts, ("H100", "B200")), "--host", f"GH200={bad}", "--output", str(out), "--no-energy"])
    assert code == 1                                                                         # a host was dropped: not a clean run
    cross = json.loads((out / "cross_host.json").read_text())
    assert cross["hosts"] == ["H100", "B200"]
    assert "GH200: no round passed its gates" in capsys.readouterr().err
    # a reference without data cannot be compared at all
    code = AC.main(["--host", f"H100={bad}", *host_args(hosts, ("B200",)), "--output", str(tmp_path / "y"), "--no-energy"])
    assert code == 1 and not (tmp_path / "y" / "cross_host.json").exists()


def test_synthetic_cli_emits_four_hosts_and_the_cross_host_outputs(tmp_path):
    out = tmp_path / "s"
    assert AC.main(["--synthetic", "--seed", "7", "--output", str(out)]) == 0
    assert (out / "stats.json").is_file() and (out / "cross_host" / "cross_host.json").is_file()
    cross = json.loads((out / "cross_host" / "cross_host.json").read_text())
    assert cross["hosts"] == ["A100", "H100", "GH200", "B200"] and cross["synthetic"] is True
    for host in HOSTS:
        assert (out / "cross_host" / "hosts" / host / "stats.json").is_file()
    primary = json.loads((out / "stats.json").read_text())
    assert primary["host"]["label"] == "H100"
    # the command-line showcase adds a partial cell and an unclassified failure so that every coverage class appears
    cells = primary["coverage"]["matrix"]
    assert cells["baseline"]["q10"]["sf30"]["spark_gpu"]["status"] == "partial"
    assert cells["complex"]["cx_distinct"]["sf300"]["spark_gpu"]["reason_class"] == "error"
    assert cells["complex"]["cx_self_join"]["sf300"]["polars_gpu"]["reason_class"] == "out_of_memory"
    assert cells["complex"]["cx_q13"]["sf30"]["polars_gpu"]["reason_class"] == "unsupported_operation"
    # H100 alone: no cross-host directory
    AC.main(["--synthetic", "--synthetic-hosts", "H100", "--output", str(tmp_path / "t")])
    assert not (tmp_path / "t" / "cross_host").exists()


def test_real_hosts_are_not_watermarked(tmp_path):
    roots = {}
    for host in ("H100", "B200"):
        root = tmp_path / host
        AC.make_synthetic_campaign(root, seed=2, host=host, a100=False, side_study=False, **QUIET)
        meta = json.loads((root / "campaign.json").read_text())
        del meta["synthetic"]
        (root / "campaign.json").write_text(json.dumps(meta))
        roots[host] = root
    out = tmp_path / "x"
    assert AC.main([*host_args({h: (r,) for h, r in roots.items()}, ("H100", "B200")), "--output", str(out), "--no-energy"]) == 0
    cross = json.loads((out / "cross_host.json").read_text())
    assert cross["synthetic"] is False and cross["watermark"] is None
    assert not (out / "SYNTHETIC_NOT_RESULTS.txt").exists()
    # the generated CPU model names still say SYNTHETIC; the watermark itself must be gone
    assert "NOT RESULTS" not in (out / "CROSS_HOST_REPORT.md").read_text()
    rows = list(csv.DictReader((out / "tables" / "x02_suite_ratios.csv").open()))
    assert {r["data_status"] for r in rows} == {"measured"}
    assert "NOT RESULTS" not in (out / "tables" / "x02_suite_ratios.tex").read_text()


def test_xhost_compat_file_has_the_layout_the_paper_tooling_expects(quiet, tmp_path):
    hosts, cross = quiet
    AC.write_cross_outputs(cross, tmp_path)
    compat = json.loads((tmp_path / "xhost_B200.json").read_text())
    assert compat["_meta"]["host"] == "B200" and compat["_meta"]["reference"] == "H100"
    flat = {k: v for k, v in AC.flatten_stats({k: v for k, v in compat.items() if not k.startswith("_")}, (), ()).items()}
    record = compat["polars_gpu"]["baseline"]["sf300"]
    assert record["estimate"] == pytest.approx(cross["ratio"]["B200"]["polars_gpu"]["baseline"]["sf300"]["estimate"])
    assert "polars_gpu.baseline.sf300.ci_low" in flat and "spark_cpu.complex.sf30.estimate" in flat
    for key, value in flat.items():
        if key.endswith(".estimate"):
            prefix = key[: -len(".estimate")]
            assert flat[prefix + ".ci_low"] <= value <= flat[prefix + ".ci_high"]


# ------------------------------------------------------------------ schema contract

def test_every_cross_host_key_is_documented_and_every_documented_key_exists(noisy):
    _, cross = noisy
    flat = AC.flatten_stats(cross, (), AC.CROSS_OPAQUE_PATTERNS)
    undocumented = [k for k, v in flat.items() if not AC.schema_matches(k, AC.CROSS_SCHEMA)
                    and not (v == {} and any(p.startswith(k + ".") for p, _, _ in AC.CROSS_SCHEMA))]
    assert undocumented == []
    seen = {p for k in flat for p in AC.schema_matches(k, AC.CROSS_SCHEMA)}
    assert [p for p, _, optional in AC.CROSS_SCHEMA if not optional and p not in seen] == []


def test_per_host_stats_keys_for_host_identity_are_documented(noisy):
    hosts, _ = noisy
    flat = AC.flatten_stats(hosts["GH200"][2])
    undocumented = [k for k, v in flat.items() if not AC.schema_matches(k) and not (v == {} and any(p.startswith(k + ".") for p, _, _ in AC.SCHEMA))]
    assert undocumented == []
    assert {"host.label", "host.arch", "host.vcpus", "host.driver_version", "host.cpu_model", "host.gpu_memory_gib"} <= set(flat)
    assert AC.lookup(hosts["GH200"][2], "host.arch") == "aarch64"


def test_schema_markdown_documents_the_cross_host_file():
    text = AC.schema_markdown()
    assert "cross_host.json" in text and "`ratio.{host}.{engine}.{suite}.{scale}.ci_low`" in text and "Welch" in text
    assert "`host.arch`" in text and "xhost_<HOST>.json" in text


def test_cross_host_outputs_are_strict_json_and_deterministic(tmp_path):
    def reject(constant):
        raise ValueError(f"non-finite constant {constant}")

    AC.main(["--synthetic", "--seed", "4", "--output", str(tmp_path / "a")])
    text = (tmp_path / "a" / "cross_host" / "cross_host.json").read_text()
    json.loads(text, parse_constant=reject)
    AC.main(["--synthetic", "--seed", "4", "--output", str(tmp_path / "b")])
    first = json.loads(text)
    second = json.loads((tmp_path / "b" / "cross_host" / "cross_host.json").read_text())
    for blob in (first, second):
        blob.pop("generated_at_utc")
        for info in blob["host_info"].values():
            info.pop("root")
    assert first == second


# ------------------------------------------------------------------ integrity counts (no timings)

def test_integrity_counts_sessions_queries_trials_and_failure_classes(tmp_path):
    root = tmp_path / "tree"
    AC.make_synthetic_campaign(root, seed=2, a100=False, side_study=False, **QUIET)
    extras = {}
    stats = AC.analyze(root, energy=False, extras=extras)
    integrity = json.loads(json.dumps(extras["integrity"]))
    assert [r["sessions_found"] for r in integrity["rounds"]] == [32] * 4 and all(r["sessions_expected"] == 32 for r in integrity["rounds"])
    for r in integrity["rounds"]:
        assert (r["cells"], r["ok"], r["sessions_missing"], r["sessions_nonzero_returncode"]) == (192, 185, [], 0)
        assert r["failed"] == {"unsupported_operation": 6, "out_of_memory": 1}          # cx_q13 / cx_window x 3 scales; cx_self_join SF300
    assert integrity["trials_per_ok_cell"] == [3] and integrity["cleanup_not_passed"] == 0
    assert integrity["by_engine"]["polars_gpu"]["failed"] == {"unsupported_operation": 24, "out_of_memory": 4}
    polars = integrity["by_engine"]["polars"]
    assert (polars["sessions"], polars["queries"], polars["ok"], polars["failed"]) == (4 * 8, 4 * 8 * 6, 4 * 8 * 6, {})
    assert stats["campaign"]["trials_per_cell"] == [3]
    # counts only: nothing in the integrity block is a time
    text = json.dumps(integrity)
    assert "seconds" not in text and "median" not in text and "estimate" not in text


def test_integrity_lists_missing_sessions_and_excluded_rounds(tmp_path):
    root = tmp_path / "tree"
    AC.make_synthetic_campaign(root, seed=2, a100=False, side_study=False, failed_gate_rounds=(3,), **QUIET)
    (root / "round-2" / "baseline" / "sf10" / "spark_cpu" / "results.json").unlink()
    extras = {}
    AC.analyze(root, energy=False, extras=extras)
    by_label = {r["label"]: r for r in extras["integrity"]["rounds"]}
    assert by_label["round-3"]["included"] is False and by_label["round-3"]["sessions_found"] == 0 and "fail" in by_label["round-3"]["exclusion"]
    assert by_label["round-2"]["sessions_missing"] == []   # an unreadable session is still found (its queries are session-level failures)
    assert by_label["round-2"]["sessions_readable"] == 31 and by_label["round-2"]["session_failed"] == 6
    import shutil
    shutil.rmtree(root / "round-1" / "complex" / "sf300" / "spark_gpu")
    extras = {}
    AC.analyze(root, energy=False, extras=extras)
    first = next(r for r in extras["integrity"]["rounds"] if r["label"] == "round-1")
    assert first["sessions_found"] == 32 and first["sessions_readable"] == 31      # still scheduled: counted as a session without results
    schedule_path = root / "round-1" / "complex" / "schedule.json"
    schedule_path.write_text(json.dumps([i for i in json.loads(schedule_path.read_text()) if not (i["scale"] == "300" and i["engine"] == "spark_gpu")]))
    extras = {}
    AC.analyze(root, energy=False, extras=extras)
    first = next(r for r in extras["integrity"]["rounds"] if r["label"] == "round-1")
    assert first["sessions_found"] == 31 and first["sessions_missing"] == ["complex/sf300/spark_gpu"]


def test_cli_writes_integrity_files_for_the_run_and_for_each_host(quiet, tmp_path):
    hosts, _ = quiet
    out = tmp_path / "one"
    assert AC.main(["--root", str(hosts["A100"][0]), "--output", str(out), "--no-energy"]) == 0
    assert (out / "integrity.json").is_file()
    text = (out / "INTEGRITY.md").read_text()
    assert AC.WATERMARK in text and "| round-1 | yes | pass | 32 / 32 |" in text and "Measured trials per OK cell: [3]" in text
    cross = tmp_path / "cross"
    assert AC.main([*host_args(hosts, ("H100", "B200")), "--output", str(cross), "--no-energy"]) == 0
    assert (cross / "hosts" / "B200" / "INTEGRITY.md").is_file()
    # a host whose rounds all fail their gates still gets an integrity file explaining why
    bad = tmp_path / "bad"
    AC.make_synthetic_campaign(bad, seed=1, host="GH200", a100=False, side_study=False, failed_gate_rounds=(1, 2, 3, 4), **QUIET)
    assert AC.main([*host_args(hosts, ("H100",)), "--host", f"GH200={bad}", "--output", str(tmp_path / "c2"), "--no-energy"]) == 1
    refused = json.loads((tmp_path / "c2" / "hosts" / "GH200" / "integrity.json").read_text())
    assert all(not r["included"] for r in refused["rounds"])


def test_unrecorded_gpu_name_is_none_not_an_empty_string(tmp_path):
    root = tmp_path / "tree"
    AC.make_synthetic_campaign(root, seed=2, a100=False, side_study=False, **QUIET)
    for path in root.rglob("environment.json"):
        env = json.loads(path.read_text())
        env.pop("inventory")
        env["resource_allocation"].pop("gpu")
        path.write_text(json.dumps(env))
    meta = json.loads((root / "campaign.json").read_text())
    meta["provenance"]["host"]["gpu"] = None
    (root / "campaign.json").write_text(json.dumps(meta))
    host = AC.analyze(root, energy=False)["host"]
    assert host["gpu"] is not None   # the sessions' gpu_inventory still names the device
    for session in root.rglob("results.json"):
        data = json.loads(session.read_text())
        data.pop("gpu_inventory", None)
        session.write_text(json.dumps(data))
    host = AC.analyze(root, energy=False)["host"]
    assert host["gpu"] is None and host["gpu_short"] is None and host["label"] == "gpu_1x_h100_sxm5"


# ------------------------------------------------------------------ RAPIDS jar in the host identity (aarch64 classifier)

def test_the_rapids_jar_is_part_of_the_host_identity_and_the_comparison_names_the_platform_build(quiet):
    hosts, cross = quiet
    assert hosts["H100"][2]["host"]["rapids_jar"] == "rapids-4-spark_2.12-26.08.1.jar"
    assert hosts["GH200"][2]["host"]["rapids_jar"] == "rapids-4-spark_2.12-26.08.1-cuda12-arm64.jar"
    assert hosts["GH200"][2]["host"]["rapids_jar_sha256"] != hosts["H100"][2]["host"]["rapids_jar_sha256"]
    assert cross["identity_differences"]["GH200"]["rapids_jar"] == ["rapids-4-spark_2.12-26.08.1-cuda12-arm64.jar", "rapids-4-spark_2.12-26.08.1.jar"]
    assert any("RAPIDS Accelerator jar differs" in note for note in cross["caveats_by_host"]["GH200"])
    assert not any("RAPIDS Accelerator jar differs" in note for note in cross["caveats_by_host"]["B200"])


# ------------------------------------------------------------------ output agreement per engine x scale, host versus reference

def build(tmp_path, name, host="H100", seed=3):
    root = tmp_path / name
    AC.make_synthetic_campaign(root, seed=seed, host=host, a100=False, side_study=False, **QUIET)
    return root


def analyze(root):
    return json.loads(json.dumps(AC.analyze(root, energy=False)))


def perturb(root, host_round, suite, scale, engine, query, factor):
    """Scale the float outputs of one query in one saved session, as a platform-dependent reduction order would."""
    path = root / f"round-{host_round}" / suite / f"sf{scale}" / engine / "results.json"
    session = json.loads(path.read_text())
    for record in session["records"]:
        if record["query"] == query:
            record["rows"] = [[row[0], row[1] * factor, *row[2:]] for row in record["rows"]]
    path.write_text(json.dumps(session))


def test_output_agreement_is_reported_per_suite_scale_and_engine_with_the_worst_query(tmp_path):
    root = build(tmp_path, "tree")
    perturb(root, 2, "baseline", "100", "spark_gpu", "q10", 1 + 4e-9)
    stats = analyze(root)
    section = stats["output_agreement"]
    cell = section["baseline"]["sf100"]["spark_gpu"]
    assert cell["rounds"] == [1, 2, 3, 4] and cell["failed"] == 0 and cell["compared"] == 6 * 4 * 4
    assert cell["max_rel_diff"] == pytest.approx(4e-9, rel=0.2) and cell["max_rel_diff_query"] == "q10"
    assert cell["max_rel_diff_per_round"][1] == pytest.approx(4e-9, rel=0.2) and cell["max_rel_diff_per_round"][0] < 1e-9
    assert section["baseline"]["sf100"]["spark_gpu"]["queries"]["q10"] == cell["max_rel_diff"]
    untouched = section["baseline"]["sf30"]["spark_gpu"]
    assert untouched["max_rel_diff"] == pytest.approx(3e-10, rel=0.05) and section["baseline"]["sf30"]["polars_gpu"]["max_rel_diff"] == pytest.approx(2e-10, rel=0.05)
    assert "polars" not in section["baseline"]["sf100"]                              # the reference engine is not compared with itself
    assert stats["meta"]["max_rel_diff"] == pytest.approx(cell["max_rel_diff"])      # the campaign-wide value is the maximum of the cells


def test_a_host_whose_difference_is_larger_than_the_reference_is_flagged_even_inside_the_tolerance(tmp_path):
    roots = {}
    for host in ("H100", "B200"):
        roots[host] = build(tmp_path, host, host=host)
    perturb(roots["B200"], 1, "baseline", "300", "spark_gpu", "q03", 1 + 5e-9)          # inside rel 1e-8
    perturb(roots["B200"], 1, "complex", "100", "polars_gpu", "cx_q09", 1 + 5e-8)       # outside rel 1e-8
    stats = {h: analyze(r) for h, r in roots.items()}
    cross = json.loads(json.dumps(AC.compare_hosts(stats)))
    a = cross["agreement"]["B200"]["baseline"]["sf300"]["spark_gpu"]
    assert a["larger_than_reference"] is True and a["host_within_gate"] is True and a["host_failed"] == 0
    assert a["queries_larger"] == ["q03"] and a["host_max_rel_diff_query"] == "q03" and a["host_max_rel_diff"] > 4e-9 > a["ref_max_rel_diff"]
    assert a["matched_rounds"]["1"][0] > a["matched_rounds"]["1"][1] and a["matched_rounds"]["2"][0] == pytest.approx(a["matched_rounds"]["2"][1])
    outside = cross["agreement"]["B200"]["complex"]["sf100"]["polars_gpu"]
    assert outside["larger_than_reference"] is True and outside["host_within_gate"] is False and outside["host_failed"] > 0
    same = cross["agreement"]["B200"]["baseline"]["sf30"]["spark_gpu"]
    assert same["larger_than_reference"] is False and same["queries_larger"] == [] and same["host_max_rel_diff"] == pytest.approx(same["ref_max_rel_diff"])
    flagged = [(s, sc, e) for s, by_scale in cross["agreement"]["B200"].items() for sc, by_engine in by_scale.items() for e, v in by_engine.items() if v["larger_than_reference"]]
    assert sorted(flagged) == [("baseline", "sf300", "spark_gpu"), ("complex", "sf100", "polars_gpu")]
    out = tmp_path / "x"
    AC.write_cross_outputs(cross, out)
    rows = list(csv.DictReader((out / "tables" / "x06_output_agreement.csv").open()))
    assert {(r["suite"], r["scale"], r["engine"]) for r in rows if r["larger_than_reference"] == "True"} == set(flagged)
    assert "Output agreement with Polars (integrity evidence)" in (out / "CROSS_HOST_REPORT.md").read_text()
    # the integrity path agrees: the out-of-gate perturbation is an integrity failure of an accepted round on the host itself
    assert stats["B200"]["meta"]["outputs_failed"] > 0 and stats["B200"]["meta"]["integrity_failures_in_accepted_rounds"] >= 1


def test_cuda_runtime_version_is_decoded_and_part_of_the_identity(quiet):
    hosts, cross = quiet
    assert AC.cuda_runtime_text(12090) == "12.9.0" and AC.cuda_runtime_text(13000) == "13.0.0" and AC.cuda_runtime_text(None) is None
    assert AC.cuda_runtime_text("12090") is None and AC.cuda_runtime_text(True) is None
    assert all(hosts[h][2]["host"]["cuda_runtime_version"] == "12.9.0" for h in hosts)
    assert "cuda_runtime_version" not in cross["identity_differences"]["B200"]          # identical stacks on every host


def test_the_agreement_comparison_gives_the_size_of_an_excess(tmp_path):
    roots = {h: build(tmp_path, h, host=h) for h in ("H100", "B200")}
    perturb(roots["B200"], 1, "baseline", "300", "spark_gpu", "q03", 1 + 5e-9)
    cross = json.loads(json.dumps(AC.compare_hosts({h: analyze(r) for h, r in roots.items()})))
    larger = cross["agreement"]["B200"]["baseline"]["sf300"]["spark_gpu"]
    assert larger["host_over_reference"] == pytest.approx(larger["host_max_rel_diff"] / larger["ref_max_rel_diff"]) and larger["host_over_reference"] > 10
    same = cross["agreement"]["B200"]["baseline"]["sf30"]["spark_gpu"]
    assert same["host_over_reference"] == pytest.approx(1.0)


# ------------------------------------------------------------------ partial ladder

def test_a_comparison_without_every_ladder_host_is_labelled_partial(quiet):
    hosts, full = quiet
    assert full["partial"] is False and full["hosts_not_included"] == []
    assert "PARTIAL" not in AC.cross_report_text(full, AC.build_cross_tables(full))
    two = json.loads(json.dumps(AC.compare_hosts({h: hosts[h][2] for h in ("H100", "GH200")})))
    assert two["hosts"] == ["H100", "GH200"] and two["partial"] is True and two["hosts_not_included"] == ["A100", "B200"]
    report = AC.cross_report_text(two, AC.build_cross_tables(two))
    assert "**PARTIAL comparison.**" in report and "A100, B200 are not included" in report
    one_missing = json.loads(json.dumps(AC.compare_hosts({h: hosts[h][2] for h in ("A100", "H100", "GH200")})))
    assert one_missing["hosts_not_included"] == ["B200"]
    assert "B200 is not included" in AC.cross_report_text(one_missing, AC.build_cross_tables(one_missing))


def test_the_partial_keys_are_documented(quiet):
    _, cross = quiet
    for key in ("hosts_not_included", "partial"):
        assert AC.schema_matches(key, AC.CROSS_SCHEMA), key


# ------------------------------------------------------------------ CPU topology (SMT, physical cores, NUMA)

def lscpu_text(vcpus, threads, cores, sockets, numa, with_cpus=1, hypervisor="KVM"):
    lines = ["Architecture:                            x86_64", f"CPU(s):                                  {vcpus}",
             f"Thread(s) per core:                      {threads}", f"Core(s) per socket:                      {cores}",
             f"Socket(s):                               {sockets}"] + ([f"Hypervisor vendor:                       {hypervisor}"] if hypervisor else [])
    lines.append(f"NUMA node(s):                            {numa}")
    lines += [f"NUMA node{k} CPU(s):                    " + (f"0-{vcpus - 1}" if k < with_cpus else "") for k in range(numa)]
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("lscpu, vcpus, cpus_arg, expected", [
    # H100 / B200 (KVM guest): 26 vCPUs are 13 cores with two hardware threads; 26 threads fill all 13 cores
    (lscpu_text(26, 2, 13, 1, 1), 26, 26, dict(smt_threads_per_core=2, physical_cores=13, numa_nodes=1, numa_nodes_with_cpus=1, topology_consistent=True,
                                              cpus_arg_cores_min=13, cpus_arg_cores_max=13, cpus_arg_share_of_vcpus=1.0, hypervisor="KVM")),
    # GH200: 64 single-thread cores, nine NUMA nodes of which one holds CPUs; 26 threads occupy 26 cores
    (lscpu_text(64, 1, 64, 1, 9, hypervisor=None), 64, 26, dict(smt_threads_per_core=1, physical_cores=64, numa_nodes=9, numa_nodes_with_cpus=1,
                                                                topology_consistent=True, cpus_arg_cores_min=26, cpus_arg_cores_max=26,
                                                                cpus_arg_share_of_vcpus=0.406, hypervisor=None)),
    # A100 (KVM guest): a flat topology of 30 one-core sockets
    (lscpu_text(30, 1, 1, 30, 1), 30, 26, dict(smt_threads_per_core=1, cores_per_socket=1, sockets=30, physical_cores=30, topology_consistent=True,
                                               cpus_arg_cores_min=26, cpus_arg_cores_max=26, cpus_arg_share_of_vcpus=0.867)),
    # more threads than vCPUs: capped at the vCPU count
    (lscpu_text(26, 2, 13, 1, 1), 26, 40, dict(cpus_arg_cores_min=13, cpus_arg_cores_max=13)),
    # fewer threads than cores: packing onto siblings and spreading give different answers
    (lscpu_text(26, 2, 13, 1, 1), 26, 8, dict(cpus_arg_cores_min=4, cpus_arg_cores_max=8)),
    # a topology that does not add up is flagged, not silently trusted
    (lscpu_text(26, 2, 12, 1, 1), 26, 26, dict(physical_cores=12, topology_consistent=False)),
])
def test_cpu_topology_from_lscpu(lscpu, vcpus, cpus_arg, expected):
    topology = AC.cpu_topology(lscpu, vcpus, cpus_arg)
    assert {k: topology[k] for k in expected} == expected


def test_cpu_topology_leaves_unrecorded_fields_none():
    empty = AC.cpu_topology("", None, None)
    assert set(empty) >= {"smt_threads_per_core", "physical_cores", "numa_nodes", "cpus_arg_cores_min", "cpus_arg_cores_max", "topology_consistent"}
    assert all(v is None for v in empty.values())
    no_smt_line = AC.cpu_topology("CPU(s): 26\nCore(s) per socket: 13\nSocket(s): 1\n", 26, 26)
    assert no_smt_line["smt_threads_per_core"] is None and no_smt_line["cpus_arg_cores_min"] is None and no_smt_line["cpus_arg_cores_max"] == 13
    assert no_smt_line["topology_consistent"] is None


def test_synthetic_hosts_report_the_topology_of_their_profile(quiet):
    hosts, _ = quiet
    for host, (threads, cores, sockets, numa, hypervisor) in ((h, AC.SYNTH_HOSTS[h]["topology"]) for h in HOSTS):
        info = hosts[host][2]["host"]
        assert (info["smt_threads_per_core"], info["cores_per_socket"], info["sockets"], info["numa_nodes"], info["hypervisor"]) == (threads, cores, sockets, numa, hypervisor)
        assert info["physical_cores"] == cores * sockets and info["topology_consistent"] is True
        assert info["cpus_arg_cores_min"] == -(-26 // threads) and info["cpus_arg_cores_max"] == min(26, cores * sockets)


def test_the_smt_difference_is_a_caveat_with_the_cores_the_threads_occupy(quiet):
    _, cross = quiet
    gh200 = " ".join(cross["caveats_by_host"]["GH200"])
    assert "SMT differs (GH200: 1 thread per core, H100: 2)" in gh200
    assert "26 threads occupy 26 physical core(s) on GH200 (64 in total) and 13 on H100 (13 in total)" in gh200
    assert "not per-core ratios" in gh200 and "hypervisor" in gh200
    assert "NUMA node count differs (9 vs 1; nodes that hold CPUs: 1 vs 1)" in gh200
    assert "Hypervisor line in lscpu differs (none reported vs KVM)" in gh200 and "bare metal" in gh200
    a100 = " ".join(cross["caveats_by_host"]["A100"])
    assert "SMT differs (A100: 1 thread per core, H100: 2)" in a100 and "26 threads occupy 26 physical core(s) on A100 (30 in total) and 13 on H100" in a100
    b200 = " ".join(cross["caveats_by_host"]["B200"])
    assert "SMT differs" not in b200 and "NUMA node count differs" not in b200 and "Physical core count differs" not in b200
    assert set(cross["identity_differences"]["GH200"]) >= {"smt_threads_per_core", "physical_cores", "numa_nodes", "hypervisor"}
    assert cross["identity_differences"]["A100"]["smt_threads_per_core"] == [1, 2] and "numa_nodes" not in cross["identity_differences"]["A100"]


def test_a_core_count_difference_without_an_smt_difference_has_its_own_caveat():
    notes = AC.host_caveats("X", "H100", {"smt_threads_per_core": 2, "physical_cores": 16, "cpus_arg": 26, "cpus_arg_cores_min": 13, "cpus_arg_cores_max": 16, "n_rounds": 4},
                            {"smt_threads_per_core": 2, "physical_cores": 13, "cpus_arg": 26, "cpus_arg_cores_min": 13, "cpus_arg_cores_max": 13, "n_rounds": 4},
                            {"physical_cores": [16, 13]})
    text = " ".join(notes)
    assert "Physical core count differs (16 vs 13)" in text and "13 to 16 physical core(s) on X and 13 on H100" in text and "SMT differs" not in text


def test_the_host_table_carries_the_topology_columns(quiet):
    _, cross = quiet
    table = {t.name: t for t in AC.build_cross_tables(cross)}["x01_hosts"]
    headers = [h for h, _ in table.text_columns]
    assert {"Threads / core (SMT)", "Physical cores", "NUMA nodes", "Cores occupied by --cpus threads"} <= set(headers)
    cells = {row[0].split(" ")[0]: dict(zip(headers, row)) for row in table.text_rows()}
    assert cells["GH200"]["Cores occupied by --cpus threads"] == "26" and cells["H100"]["Cores occupied by --cpus threads"] == "13"
    assert cells["GH200"]["NUMA nodes"] == "9" and cells["A100"]["Threads / core (SMT)"] == "1"


REAL = {"H100": "h100", "GH200": "gh200", "A100": "a100", "B200": "b200"}


@pytest.mark.parametrize("label, expected", [
    ("H100", dict(smt_threads_per_core=2, physical_cores=13, numa_nodes=1, cpus_arg_cores_min=13, cpus_arg_cores_max=13)),
    ("B200", dict(smt_threads_per_core=2, physical_cores=13, numa_nodes=1, cpus_arg_cores_min=13, cpus_arg_cores_max=13)),
    ("GH200", dict(smt_threads_per_core=1, physical_cores=64, numa_nodes=9, numa_nodes_with_cpus=1, cpus_arg_cores_min=26, cpus_arg_cores_max=26, hypervisor=None)),
    ("A100", dict(smt_threads_per_core=1, physical_cores=30, sockets=30, numa_nodes=1, cpus_arg_cores_min=26, cpus_arg_cores_max=26)),
])
def test_real_hosts_topology_from_the_copied_environment_json(label, expected):
    root = Path(__file__).resolve().parent.parent / "rerun" / "hosts" / REAL[label] / f"campaign-{REAL[label]}"
    if not (root / "round-1" / "gates.json").is_file():
        pytest.skip(f"the copied {label} campaign is not in this checkout")
    host = AC.analyze(root, energy=False, label=label)["host"]
    assert {k: host[k] for k in expected} == expected and host["topology_consistent"] is True
