"""Campaign figures (plot_campaign.py): F4 failure classes, F11 ladder, engine colours, type sizes, tick helpers.

The figure review rules that can be checked mechanically are checked here: the three failure classes of F4 differ in
colour under colour-vision-deficiency simulation, in lightness (greyscale) and in glyph; engine colours are defined once;
no text is set below 5.5 pt; F11 is drawn only from cross_host.json.
"""
from __future__ import annotations

import copy
import itertools
import json
import math
from pathlib import Path
import re
import sys

import matplotlib
import numpy as np
import pytest

matplotlib.use("Agg")

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import analyze_campaign as AC  # noqa: E402
import plot_campaign as PC  # noqa: E402

SOURCE = (REPO / "plot_campaign.py").read_text()


# ------------------------------------------------------------------ colour science helpers (no extra dependencies)

def srgb_to_linear(rgb):
    rgb = np.asarray(rgb, float)
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def hex_rgb(value):
    return np.array([int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)])


# Machado, Oliveira and Fernandes (2009), severity 1.0, applied to linear RGB
CVD = {"protan": [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
       "deutan": [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]],
       "tritan": [[1.255528, -0.076749, -0.178779], [-0.078411, 0.930809, 0.147602], [0.004733, 0.691367, 0.303900]]}
XYZ = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])


def lab(value, deficiency=None):
    linear = srgb_to_linear(hex_rgb(value))
    if deficiency:
        linear = np.clip(np.array(CVD[deficiency]) @ linear, 0.0, 1.0)
    xyz = XYZ @ linear / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.array([116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])])


FAILURES = ("unsupported_operation", "out_of_memory", "error")


def fill(key):
    return PC.COVERAGE_STYLE[key]["fill"]


# ------------------------------------------------------------------ F4 failure classes

@pytest.mark.parametrize("deficiency", [None, "protan", "deutan", "tritan"])
def test_failure_class_fills_are_far_apart_for_every_kind_of_colour_vision(deficiency):
    for a, b in itertools.combinations(FAILURES, 2):
        delta = float(np.linalg.norm(lab(fill(a), deficiency) - lab(fill(b), deficiency)))
        assert delta >= 40.0, (a, b, deficiency, delta)


def test_failure_class_fills_differ_in_lightness_so_they_survive_greyscale():
    lightness = sorted(float(lab(fill(k))[0]) for k in FAILURES)
    assert lightness[1] - lightness[0] >= 20.0 and lightness[2] - lightness[1] >= 20.0
    # and none of them is mistaken for the pale supported / not-run cells
    for k in FAILURES:
        assert float(lab(fill("supported"))[0]) - float(lab(fill(k))[0]) >= 15.0
        assert float(lab(fill("not_run"))[0]) - float(lab(fill(k))[0]) >= 15.0


def test_each_coverage_class_has_its_own_glyph_and_only_partial_is_hatched():
    glyphs = [spec["glyph"] for spec in PC.COVERAGE_STYLE.values()]
    assert len(glyphs) == len(set(glyphs)) == 6
    assert {k for k, spec in PC.COVERAGE_STYLE.items() if spec.get("hatch")} == {"partial"}
    assert [PC.COVERAGE_STYLE[k]["glyph"] for k in FAILURES] == ["✗", "OOM", "!"]
    # the failure fills are three different hue families, not three reds
    hues = [math.degrees(math.atan2(lab(fill(k))[2], lab(fill(k))[1])) for k in FAILURES]
    gaps = [abs((a - b + 180) % 360 - 180) for a, b in itertools.combinations(hues, 2)]
    assert min(gaps) >= 35.0 and max(gaps) >= 90.0


@pytest.mark.parametrize("cell, expected", [
    (None, "not_run"), ({"status": "not_run", "reason_class": None}, "not_run"),
    ({"status": "supported", "reason_class": None}, "supported"), ({"status": "partial", "reason_class": "timeout"}, "partial"),
    ({"status": "failed", "reason_class": "unsupported_operation"}, "unsupported_operation"),
    ({"status": "failed", "reason_class": "out_of_memory"}, "out_of_memory"),
    ({"status": "failed", "reason_class": "error"}, "error"), ({"status": "failed", "reason_class": "timeout"}, "error"),
    ({"status": "failed", "reason_class": "session_failed"}, "error"), ({"status": "failed", "reason_class": None}, "error")])
def test_coverage_key_maps_every_outcome_to_one_of_the_six_classes(cell, expected):
    assert PC.coverage_key(cell) == expected


def render(tmp_path, name, seed=7, **kwargs):
    out = tmp_path / name
    assert AC.main(["--synthetic", "--seed", str(seed), "--output", str(out)]) == 0
    stats, base = PC.load_stats(out)
    cross = PC.find_cross(out)
    ctx = PC.build_figures(stats, out / "figures", formats=("png",), cross=cross, **kwargs)
    return out, stats, cross, ctx


@pytest.fixture(scope="module")
def drawn(tmp_path_factory):
    return render(tmp_path_factory.mktemp("figs"), "syn")


def count_pixels(path, hex_value, tolerance=6):
    from PIL import Image
    target = (hex_rgb(hex_value) * 255).round().astype(int)
    pixels = np.asarray(Image.open(path).convert("RGB")).astype(int)
    return int((np.abs(pixels - target).max(axis=2) <= tolerance).sum())


def test_f4_shows_all_three_failure_classes_in_their_own_colours(drawn):
    out, stats, _, ctx = drawn
    matrix = stats["coverage"]["matrix"]
    classes = {PC.coverage_key(cell) for by_query in matrix.values() for by_scale in by_query.values() for by_engine in by_scale.values()
               for cell in by_engine.values() if cell["status"] in ("failed", "partial")}
    assert classes == {"unsupported_operation", "out_of_memory", "error", "partial"}     # the synthetic showcase has every class
    for name in ("f04_gpu_coverage", "f04_gpu_coverage_1col"):
        path = out / "figures" / f"{name}.png"
        for key in FAILURES:
            assert count_pixels(path, fill(key)) > 800, (name, key)


def test_f4_has_a_double_and_a_single_column_version_within_the_column_widths(drawn):
    _, _, _, ctx = drawn
    sizes = {m["id"]: m["size_inches"] for m in ctx.manifest}
    assert sizes["f04_gpu_coverage"][0] == pytest.approx(PC.DOUBLE) and sizes["f04_gpu_coverage_1col"][0] == pytest.approx(PC.SINGLE)
    caption = next(m for m in ctx.manifest if m["id"] == "f04_gpu_coverage")["caption_draft"]
    assert "violet" in caption and "amber" in caption and "dark red" in caption


# ------------------------------------------------------------------ F11 ladder

def test_f11_is_drawn_from_cross_host_json_in_two_widths(drawn):
    out, _, cross, ctx = drawn
    entries = {m["id"]: m for m in ctx.manifest}
    assert {"f11_gpu_ladder", "f11_gpu_ladder_1col"} <= set(entries)
    assert entries["f11_gpu_ladder"]["size_inches"][0] == pytest.approx(PC.DOUBLE)
    assert entries["f11_gpu_ladder_1col"]["size_inches"][0] == pytest.approx(PC.SINGLE)
    assert entries["f11_gpu_ladder"]["synthetic"] is True and "SYNTHETIC" in entries["f11_gpu_ladder"]["caption_draft"]
    assert (out / "figures" / "f11_gpu_ladder.png").is_file()
    assert cross["hosts"] == ["A100", "H100", "GH200", "B200"]


def test_f11_is_skipped_with_a_reason_when_there_is_no_cross_host_file(tmp_path):
    out = tmp_path / "s"
    AC.main(["--synthetic", "--synthetic-hosts", "H100", "--output", str(out)])
    stats, _ = PC.load_stats(out)
    ctx = PC.build_figures(stats, out / "figs", only=["f11"], formats=("png",), cross=None)
    assert not ctx.manifest
    manifest = json.loads((out / "figs" / "figures.json").read_text())
    assert manifest["skipped"] and all("cross-host" in s["reason"] for s in manifest["skipped"])


def test_f11_alone_can_be_drawn_without_per_host_stats(drawn, tmp_path):
    out, _, cross, _ = drawn
    code = PC.main(["--cross-host", str(out / "cross_host"), "--output", str(tmp_path / "only"), "--formats", "png", "--width", "double"])
    assert code == 0
    manifest = json.loads((tmp_path / "only" / "figures.json").read_text())
    assert [m["id"] for m in manifest["figures"]] == ["f11_gpu_ladder", "f12_gpu_idle_fraction"] and manifest["generated_from"] == ["cross_host.json"]
    assert manifest["synthetic"] is True


def test_plot_cli_finds_the_cross_host_file_next_to_stats_json(drawn, tmp_path):
    out, _, _, _ = drawn
    assert PC.main(["--stats", str(out / "stats.json"), "--output", str(tmp_path / "f"), "--formats", "png", "--only", "f11", "--width", "single"]) == 0
    assert (tmp_path / "f" / "f11_gpu_ladder_1col.png").is_file()


def test_f11_says_so_in_the_title_the_footnote_and_the_caption_when_the_ladder_is_partial(drawn, tmp_path, monkeypatch):
    out, _, cross, ctx = drawn
    assert cross["partial"] is False
    assert not any("Partial ladder" in m["caption_draft"] for m in ctx.manifest if m["id"].startswith("f11"))
    two = dict(cross, hosts=["H100", "GH200"], hosts_not_included=["A100", "B200"], partial=True,
               ratio={"GH200": cross["ratio"]["GH200"]})
    titles = []
    original = PC.figure_title
    monkeypatch.setattr(PC, "figure_title", lambda fig, title, *a, **k: (titles.append(title), original(fig, title, *a, **k))[1])
    ctx2 = PC.build_figures(None, tmp_path / "p", only=["f11"], formats=("png",), cross=two)
    assert titles and all("PARTIAL: A100, B200 not included" in t for t in titles)
    captions = [m["caption_draft"] for m in ctx2.manifest]
    assert len(captions) == 2 and all("Partial ladder: A100, B200 are not included." in c for c in captions)


def test_f11_shares_one_y_range_and_marks_the_reference_at_one(drawn):
    """The H100 column sits exactly on the parity line: ratio 1 for every scale, no interval."""
    _, _, cross, _ = drawn
    for host in cross["ratio"]:
        for engine, by_suite in cross["ratio"][host].items():
            for suite, by_scale in by_suite.items():
                assert all(r["estimate"] is not None for r in by_scale.values())
    assert cross["reference"] == "H100" and "H100" not in cross["ratio"]


# ------------------------------------------------------------------ whole set

def test_every_figure_has_caption_alt_text_sources_and_fits_the_page(drawn):
    _, _, _, ctx = drawn
    ids = {m["id"] for m in ctx.manifest}
    for expected in ("f01_forest_sf300", "f02_suite_speedup_vs_scale", "f03_heatmap_baseline", "f03_heatmap_complex", "f04_gpu_coverage",
                     "f05_variability", "f06_order_effects", "f07_gpu_memory", "f08_polars_version_bridge", "f09_a100_vs_h100",
                     "f10_headline_times", "f11_gpu_ladder"):
        assert expected in ids, expected
    for m in ctx.manifest:
        width, height = m["size_inches"]
        assert width <= PC.DOUBLE + 1e-6 and height <= 11.0, m["id"]
        assert m["caption_draft"].startswith("[" + AC.WATERMARK + "]") and m["alt_text"] and m["data_source"]
        if m["id"].endswith("_1col"):
            assert width == pytest.approx(PC.SINGLE), m["id"]


def test_host_name_in_titles_follows_the_host_that_ran_the_campaign(tmp_path):
    out = tmp_path / "s"
    AC.main(["--synthetic", "--synthetic-hosts", "H100", "--output", str(out)])
    stats, _ = PC.load_stats(out)
    stats["host"].update(gpu_short="B200", gpu="NVIDIA B200")
    ctx = PC.build_figures(stats, out / "figs", only=["f08", "f09"], formats=("png",), widths=("double",))
    captions = " ".join(m["caption_draft"] + m["title"] + m["alt_text"] for m in ctx.manifest)
    assert "B200" in captions and "H100" not in captions


def test_engine_colours_are_defined_once_and_used_everywhere_through_engine_style():
    colours = [spec["color"] for spec in PC.ENGINE_STYLE.values()]
    assert len(set(colours)) == 4 and len({spec["marker"] for spec in PC.ENGINE_STYLE.values()}) == 4
    code = SOURCE.split("\n")
    for colour in colours:
        lines = [i for i, line in enumerate(code) if colour in line and not line.lstrip().startswith("#")]
        assert len(lines) == 1, f"engine colour {colour} is hard-coded outside ENGINE_STYLE: lines {[i + 1 for i in lines]}"
    # figures draw the engine colour from ENGINE_STYLE, so a changed palette changes every figure at once
    assert SOURCE.count("ENGINE_STYLE[") >= 15


def test_no_text_is_set_below_the_journal_minimum_size():
    sizes = [float(m) for m in re.findall(r"font(?:size)?=([0-9]+(?:\.[0-9]+)?)", SOURCE)]
    sizes += [float(m) for m in re.findall(r"\bsize=([0-9]+(?:\.[0-9]+)?)", SOURCE)]
    assert sizes and min(sizes) >= 5.5, sorted(set(s for s in sizes if s < 5.5))
    PC.configure_matplotlib()
    for key in ("font.size", "axes.titlesize", "axes.labelsize", "xtick.labelsize", "ytick.labelsize", "legend.fontsize"):
        assert matplotlib.rcParams[key] >= 5.5, key


@pytest.mark.parametrize("lo, hi", [(0.76, 1.3), (0.8, 2.2), (0.9, 1.15), (0.2, 6.0), (0.03, 50.0), (0.45, 1.9)])
def test_ratio_ticks_always_include_parity_and_are_readable(lo, hi):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    ax.set_yscale("log")
    ax.set_ylim(lo, hi)
    PC.ratio_ticks(ax, "y", lo, hi)
    ticks = [t for t in ax.get_yticks() if lo * 0.999 <= t <= hi * 1.001]
    plt.close(fig)
    assert 1 in ticks and 3 <= len(ticks) <= 10
    gaps = np.diff(np.log(ticks))
    assert gaps.max() / gaps.min() <= 4.0          # roughly even on the log axis


def test_failure_notes_use_the_same_glyph_vocabulary_as_f3_and_f4():
    cases = {"unsupported_operation": "✗", "out_of_memory": "OOM", "error": "!", "timeout": "!", "session_failed": "!", "invalid": "!"}
    for reason, glyph in cases.items():
        text = PC.failure_note({"status": "unsupported", "blocked_by": [{"engine": "polars_gpu", "reason_class": reason, "reason": ""}]})
        assert text.startswith(glyph + " ")
        if glyph == "!":     # integrity failures are named as such and never described as a GPU limitation
            assert "(integrity)" in text and not text.endswith("on GPU")
        elif glyph == "✗":
            assert text == "✗ not supported on GPU"
        else:
            assert text == "OOM out of memory"               # short enough for one panel
    cpu = PC.failure_note({"status": "unsupported", "blocked_by": [{"engine": "spark_cpu", "reason_class": "out_of_memory", "reason": ""}]})
    assert cpu == "OOM out of memory"
    assert PC.failure_note({"status": "not_run", "blocked_by": []}) == "– not run"
    assert [PC.COVERAGE_STYLE[k]["glyph"] for k in FAILURES] == [PC.FAILURE_NOTE[k][0] for k in FAILURES]


def figure_texts(monkeypatch, stats, out_dir, only):
    """All text objects of the figures drawn with `only`, by figure id (captured just before they are saved)."""
    captured, original = {}, PC.finish

    def spy(fig, ctx, fig_id, *args, **kwargs):
        captured[fig_id] = [t.get_text() for t in fig.findobj(matplotlib.text.Text) if t.get_text()]
        return original(fig, ctx, fig_id, *args, **kwargs)

    monkeypatch.setattr(PC, "finish", spy)
    PC.build_figures(stats, out_dir, only=only, formats=("png",), widths=("double",))
    return captured


def test_f4_states_that_other_error_is_an_integrity_failure_and_warns_when_one_is_drawn(drawn, tmp_path, monkeypatch):
    """A1: unsupported and OOM are coverage outcomes; any other error fails its round (section 7), so '!' in an accepted round is an alarm."""
    _, stats, _, ctx = drawn
    assert PC.COVERAGE_STYLE["error"]["label"] == "Other error (integrity failure)"
    caption = next(m for m in ctx.manifest if m["id"] == "f04_gpu_coverage")["caption_draft"]
    assert "coverage outcomes" in caption and "integrity failure" in caption and "should not occur in accepted rounds" in caption
    texts = " ".join(" ".join(figure_texts(monkeypatch, stats, tmp_path / "f4", ["f04"])["f04_gpu_coverage"]).split())     # the footnote is wrapped
    assert "Other error (!) is an integrity failure" in texts and "so an accepted round should show none" in texts
    assert "pre-declared and undeclared failures are both coverage outcomes" not in texts           # the earlier, wrong wording
    # the showcase has an unclassified error (!) and a timeout in one round (hatched): both are integrity failures under A1
    assert "WARNING: 2 cell(s) in accepted rounds failed for an integrity reason (marked ! or hatched): the integrity gate and this analysis disagree." in texts
    clean = copy.deepcopy(stats)
    clean["coverage"]["matrix"]["complex"]["cx_distinct"]["sf300"]["spark_gpu"].update(status="supported", reason_class=None, rounds_ok=4)
    clean["coverage"]["matrix"]["baseline"]["q10"]["sf30"]["spark_gpu"].update(status="supported", reason_class=None, rounds_ok=4)
    assert "WARNING" not in " ".join(figure_texts(monkeypatch, clean, tmp_path / "f4b", ["f04"])["f04_gpu_coverage"])


def test_f1_and_f3_use_the_same_integrity_wording(drawn, tmp_path, monkeypatch):
    _, stats, _, ctx = drawn
    captions = {m["id"]: m["caption_draft"] for m in ctx.manifest}
    assert "integrity failure" in captions["f01_forest_sf300"] and "coverage outcomes" in captions["f01_forest_sf300"]
    assert "integrity failure" in captions["f03_heatmap_complex"] and "coverage outcomes" in captions["f03_heatmap_complex"]
    f3 = " ".join(" ".join(figure_texts(monkeypatch, stats, tmp_path / "f3", ["f03"])["f03_heatmap_complex"]).split())
    assert "! other error (an integrity failure)" in f3 and "coverage outcomes" in f3
    f1 = " ".join(figure_texts(monkeypatch, stats, tmp_path / "f1", ["f01"])["f01_forest_sf300"])
    assert "! other error (integrity)" in f1 and "✗ not supported on GPU" in f1 and "OOM out of memory" in f1


EXPECTED_STEMS = sorted(
    [f"f01_forest_{sf}" for sf in ("sf1", "sf10", "sf30", "sf100", "sf300")] + ["f03_heatmap_baseline", "f03_heatmap_complex"]
    + [stem + suffix for stem in ("f02_suite_speedup_vs_scale", "f02x_c1_e1_exploratory", "f04_gpu_coverage", "f05_variability", "f06_order_effects", "f07_gpu_memory",
                                  "f08_polars_version_bridge", "f09_a100_vs_h100", "f10_headline_times", "f11_gpu_ladder", "f12_gpu_idle_fraction")
       for suffix in ("", "_1col")])


def test_figure_stems_are_pinned(drawn):
    """Lead decision: these file names are final (F11 is f11_gpu_ladder, as the paper tooling expects); change them only with approval."""
    out, _, _, ctx = drawn
    assert sorted(m["id"] for m in ctx.manifest) == EXPECTED_STEMS
    assert len(EXPECTED_STEMS) == 29
    for stem in EXPECTED_STEMS:
        assert (out / "figures" / f"{stem}.png").is_file(), stem
    assert PC.FIGURES.keys() == {f"f{n:02d}" for n in range(1, 13)} | {"f02x"}
    assert 'finish(fig, ctx, "f11_gpu_ladder"' in SOURCE and "f11_gpu_generation_ladder" not in SOURCE


def test_every_stem_the_paper_tooling_expects_is_produced_directly_or_through_its_alias():
    tool = REPO / "paper" / "tools" / "sync_figures.py"
    if not tool.is_file():
        pytest.skip("paper/tools/sync_figures.py not present")
    import importlib.util
    spec = importlib.util.spec_from_file_location("paper_sync_figures", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)                                         # defines constants and main(); nothing runs on import
    expected = [stem for stems in {**module.H100_FIGURES, **module.CROSS_HOST_FIGURES}.values() for stem in stems]
    aliases = getattr(module, "SOURCE_ALIASES", {})
    missing = [stem for stem in expected if stem not in EXPECTED_STEMS and not any(a in EXPECTED_STEMS for a in aliases.get(stem, []))]
    assert missing == [], f"paper/tools/sync_figures.py expects figure stems this plotter does not write: {missing}"


def test_unstable_coverage_is_named_as_such_in_f1_f3_and_f4(drawn, tmp_path, monkeypatch):
    """A1: a coverage outcome in some rounds only is excluded from every contrast; the figures say so instead of showing a timing or an error."""
    _, stats, _, ctx = drawn
    cell = stats["coverage"]["matrix"]["baseline"]["q06"]["sf100"]["polars_gpu"]
    assert cell["unstable"] is True and cell["reason_class"] == "out_of_memory"          # the synthetic showcase has one
    assert PC.COVERAGE_STYLE["partial"]["label"].startswith("Unstable")
    note = PC.failure_note({"status": "unstable", "blocked_by": [{"engine": "polars_gpu", "reason_class": "unstable_coverage", "reason": ""}]})
    assert note == "◐ unstable coverage"
    captions = {m["id"]: m["caption_draft"] for m in ctx.manifest}
    for fig_id in ("f01_forest_sf300", "f03_heatmap_baseline", "f04_gpu_coverage"):
        assert "unstable coverage" in captions[fig_id] and "A1" in captions[fig_id], fig_id
    f1 = " ".join(" ".join(figure_texts(monkeypatch, stats, tmp_path / "f1u", ["f01"])["f01_forest_sf100"]).split())
    assert f1.count("◐ unstable coverage") >= 2                                             # C1 and C3 of q06 at SF100
    f3 = " ".join(" ".join(figure_texts(monkeypatch, stats, tmp_path / "f3u", ["f03"])["f03_heatmap_baseline"]).split())
    assert "◐ unstable coverage (excluded, A1)" in f3 and "◐" in f3
    f4 = " ".join(" ".join(figure_texts(monkeypatch, stats, tmp_path / "f4u", ["f04"])["f04_gpu_coverage"]).split())
    assert "hatched: unstable coverage" in f4 and "excluded from every contrast in all rounds" in f4 and "Unstable (some rounds, k/n)" in f4


def test_no_text_in_any_figure_is_clipped_by_the_figure_edge(drawn, tmp_path, monkeypatch):
    """Every visible text artist of every figure (both widths) lies inside the canvas; the diagonal watermark is exempt."""
    _, stats, cross, _ = drawn
    problems, original = [], PC.finish

    def spy(fig, ctx, fig_id, *args, **kwargs):
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        width, height = fig.canvas.get_width_height()
        not_drawn = set()                                   # tick labels of ticks outside the view interval are never rendered
        for ax in fig.axes:
            for axis in (ax.xaxis, ax.yaxis):
                low, high = sorted(axis.get_view_interval())
                for tick, loc in zip(axis.get_major_ticks(), axis.get_majorticklocs()):
                    if loc < low - 1e-9 * abs(low) or loc > high + 1e-9 * abs(high):
                        not_drawn |= {id(tick.label1), id(tick.label2)}
        for text in fig.findobj(matplotlib.text.Text):
            if not text.get_text().strip() or not text.get_visible() or (text.get_alpha() or 1.0) < 0.5 or id(text) in not_drawn:
                continue
            box = text.get_window_extent(renderer)
            if box.x0 < -1.5 or box.x1 > width + 1.5 or box.y0 < -1.5 or box.y1 > height + 1.5:
                problems.append(f"{fig_id}: {text.get_text()[:60]!r} spans x {box.x0:.0f}..{box.x1:.0f} of {width}, y {box.y0:.0f}..{box.y1:.0f} of {height}")
        return original(fig, ctx, fig_id, *args, **kwargs)

    monkeypatch.setattr(PC, "finish", spy)
    PC.build_figures(stats, tmp_path / "edge", formats=("png",), cross=cross)
    assert problems == []


def legend_and_line_styles(monkeypatch, stats, cross, out_dir, only):
    """{figure id: ([legend handle linestyles in legend order], [(linestyle, marker face colour) of the plotted data lines])}."""
    captured, original = {}, PC.finish

    def spy(fig, ctx, fig_id, *args, **kwargs):
        fig.canvas.draw()
        handles = [h for legend in fig.legends for h in legend.legend_handles]
        captured[fig_id] = ([getattr(h, "get_linestyle", lambda: None)() for h in handles],
                            [(line.get_linestyle(), line.get_markerfacecolor()) for ax in fig.axes for line in ax.get_lines() if len(line.get_xdata()) > 1])
        return original(fig, ctx, fig_id, *args, **kwargs)

    monkeypatch.setattr(PC, "finish", spy)
    PC.build_figures(stats, out_dir, only=only, formats=("png",), cross=cross, widths=("double", "single"))
    return captured


def test_f2_legend_swatches_have_the_line_style_and_marker_the_plotted_suites_have(drawn, tmp_path, monkeypatch):
    """Baseline = solid line + filled marker, complex = dashed line + hollow marker, in the legend and on the data lines."""
    _, stats, cross, _ = drawn
    captured = legend_and_line_styles(monkeypatch, stats, cross, tmp_path / "f2", ["f02"])
    assert PC.SUITE_STYLE["baseline"]["ls"] == "-" and PC.SUITE_STYLE["complex"]["ls"] != "-"
    for fig_id in ("f02_suite_speedup_vs_scale", "f02_suite_speedup_vs_scale_1col"):
        legend, plotted = captured[fig_id]
        assert legend[0] == "-" and legend[1] != "-"                                          # baseline swatch solid, complex swatch dashed
        solid = [p for p in plotted if p[0] == "-"]
        dashed = [p for p in plotted if p[0] != "-"]
        assert solid and dashed                                                               # both suites are drawn, one of each style
    handler = PC.HandlerSuiteKey()
    for suite, filled in (("baseline", True), ("complex", False)):
        line, marker = handler.create_artists(None, PC.SuiteKey(suite, suite), 0, 0, 20, 8, 6.4, matplotlib.transforms.IdentityTransform())
        assert (line.get_linestyle() == "-") == (suite == "baseline")
        assert (marker.get_markerfacecolor() == PC.INK) == filled and marker.get_markerfacecolor() == (PC.INK if filled else PC.SURFACE)
        assert line.get_xdata()[1] - line.get_xdata()[0] == 20                              # the dash pattern spans the whole swatch


def test_no_legend_in_any_figure_promises_a_line_style_the_figure_does_not_use(drawn, tmp_path, monkeypatch):
    """Only F2 and F2x (complex = dashed) and F12 (host = line style) use line styles; every other legend swatch is a solid line or a marker."""
    _, stats, cross, _ = drawn
    captured = legend_and_line_styles(monkeypatch, stats, cross, tmp_path / "all", None)
    non_solid = {fig_id: [s for s in legend if s not in ("-", "solid", "None", None, "")] for fig_id, (legend, _) in captured.items()}
    offenders = {fig_id: styles for fig_id, styles in non_solid.items() if styles and not fig_id.startswith(("f02_", "f02x_", "f12_"))}
    assert offenders == {}
    for fig_id, (legend, plotted) in captured.items():
        if not fig_id.startswith(("f02_", "f02x_", "f12_")):
            drawn_lines = [style for style, _ in plotted if style not in ("None", "none", "", " ", None)]       # marker-only series have no line
            assert all(style == "-" for style in drawn_lines), (fig_id, "plots a dashed data line that no legend entry explains")


def swatch_report(fig):
    """For every legend swatch that has a line: (label, number of white gaps across the line at 300 dpi, handle linestyle, handle colour)."""
    old_dpi = fig.get_dpi()
    fig.set_dpi(300)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    pixels = np.asarray(fig.canvas.buffer_rgba())
    height = pixels.shape[0]
    report = []
    for legend in fig.legends:
        for handle, text in zip(legend.legend_handles, legend.get_texts()):
            if not isinstance(handle, matplotlib.lines.Line2D) or handle.get_linestyle() in ("None", "none", " ", ""):
                continue                                                                  # patches and marker-only swatches have no line
            box = handle.get_window_extent(renderer)
            row = int(round(height - (box.y0 + box.y1) / 2))
            line = pixels[row, int(box.x0) + 2: int(box.x1) - 1, :3]
            white = (line >= 240).all(axis=1)
            runs = sum(1 for i in range(len(white)) if white[i] and (i == 0 or not white[i - 1]) and white[i: i + 2].all())
            report.append((text.get_text(), runs, handle.get_linestyle(), matplotlib.colors.to_hex(handle.get_color())))
    fig.set_dpi(old_dpi)
    return report


def swatch_offences(stats, cross, out_dir, monkeypatch):
    """(number of swatches checked, list of offences) over every figure and both widths."""
    found, original = {}, PC.finish

    def spy(fig, ctx, fig_id, *args, **kwargs):
        plotted = {}
        for ax in fig.axes:
            for line in ax.get_lines():
                if len(line.get_xdata()) > 1 and line.get_linestyle() not in ("None", "none", " ", "") and line.get_linewidth() > 0:
                    plotted.setdefault(matplotlib.colors.to_hex(line.get_color()), set()).add("solid" if line.get_linestyle() == "-" else "dashed")
        found[fig_id] = (swatch_report(fig), plotted)
        return original(fig, ctx, fig_id, *args, **kwargs)

    monkeypatch.setattr(PC, "finish", spy)
    PC.build_figures(stats, out_dir, formats=("png",), cross=cross)
    checked, offences = 0, []
    for fig_id, (report, plotted) in found.items():
        for label, runs, style, color in report:
            dashed_entry = (fig_id.startswith(("f02_", "f02x_")) and "dashed" in label) or (fig_id.startswith("f12_") and style != "-")      # F12: host = line style
            if (runs >= 2) != dashed_entry:
                offences.append(f"{fig_id}: swatch {label!r} has {runs} white gap(s) across its line (style {style})")
            if (style != "-") != dashed_entry:
                offences.append(f"{fig_id}: swatch {label!r} has line style {style}")
            if color in plotted and ("dashed" if style != "-" else "solid") not in plotted[color]:
                offences.append(f"{fig_id}: swatch {label!r} ({style}) is drawn differently from the plotted {plotted[color]} lines")
            checked += 1
    return checked, offences


def test_every_legend_swatch_shows_the_line_style_of_the_data_it_stands_for(drawn, tmp_path, monkeypatch):
    """Solid swatches are unbroken lines (no white ring cutting them into dashes); the one dashed entry (F2 complex) has gaps; and a swatch's
    line style equals the style of the plotted lines of the same colour. Both widths of every figure."""
    _, stats, cross, _ = drawn
    checked, offences = swatch_offences(stats, cross, tmp_path / "swatches", monkeypatch)
    assert checked >= 30 and offences == []


def test_the_swatch_check_detects_the_stock_legend_handle_that_cut_solid_lines_into_dashes(drawn, tmp_path, monkeypatch):
    """Guard for the check itself: with matplotlib's stock handler (marker ring over a short line) it must report the F5/F7 swatches."""
    from matplotlib.legend_handler import HandlerLine2D
    _, stats, cross, _ = drawn
    monkeypatch.setattr(PC, "HandlerSwatchLine", HandlerLine2D)
    checked, offences = swatch_offences(stats, cross, tmp_path / "stock", monkeypatch)
    assert any(o.startswith("f07_") and "white gap" in o for o in offences) and any(o.startswith("f05_") and "white gap" in o for o in offences)


# ------------------------------------------------------------------ F10: queries without a timing sit outside the time axis

def luminance(color):
    r, g, b = matplotlib.colors.to_rgb(color)
    return 0.2126 * r ** 2.2 + 0.7152 * g ** 2.2 + 0.0722 * b ** 2.2


def test_f10_puts_queries_without_a_timing_in_a_strip_not_at_a_y_value(drawn, tmp_path, monkeypatch):
    _, stats, cross, _ = drawn
    seen = {}
    original = PC.finish

    def spy(fig, ctx, fig_id, *args, **kwargs):
        seen[fig_id] = (fig, [(ax, list(ax.get_lines()), [t.get_text() for t in ax.texts]) for ax in fig.axes], args)
        return original(fig, ctx, fig_id, *args, **kwargs)

    monkeypatch.setattr(PC, "finish", spy)
    PC.build_figures(stats, tmp_path / "f10", only=["f10"], formats=("png",), cross=cross)
    missing = {(suite, q, e) for suite, by_q in stats["medians"].items() for q in stats["campaign"]["queries"][suite]
               for e in stats["campaign"]["engines"] for sc in [stats["campaign"]["scales"][suite][-1]]
               if stats["medians"][suite][sc][q][e]["geomean"] is None}
    assert ("complex", "cx_q13", "polars_gpu") in missing
    for fig_id in ("f10_headline_times", "f10_headline_times_1col"):
        fig, axes, args = seen[fig_id]
        main = [a for a in axes if a[0].get_yscale() == "log"]
        strips = [a for a in axes if a[0].get_yscale() != "log"]
        assert len(main) == len(strips) == len(stats["campaign"]["suites"])
        markers = [ln for _, lines, _ in main for ln in lines if ln.get_marker() in ("o", "D", "s", "^")]
        assert markers and not any(ln.get_marker() in ("x", "X") for _, lines, _ in main for ln in lines)       # no cross anywhere on the time axis
        assert len(markers) == sum(len(stats["campaign"]["queries"][s]) * len(stats["campaign"]["engines"]) for s in stats["campaign"]["suites"]) - len(missing)
        lows = [ax.get_ylim()[0] for ax, _, _ in main]
        assert all(ln.get_ydata()[0] >= low for low in lows[:1] for ln in markers)                           # every marker sits at a real time inside the axis
        hollow = [ln for _, lines, _ in strips for ln in lines if ln.get_marker() in ("o", "D", "s", "^")]
        assert len(hollow) == len(missing) and all(ln.get_markerfacecolor() == PC.SURFACE for ln in hollow)
        written = sorted(t for _, _, texts in strips for t in texts if t in ("not supported", "OOM", "unstable", "failed"))
        reason_of = {"unsupported_operation": "not supported", "out_of_memory": "OOM", "unstable_coverage": "unstable"}
        assert written == sorted(reason_of.get(stats["medians"][s][stats["campaign"]["scales"][s][-1]][q][e]["reason_class"], "failed") for s, q, e in missing)   # each reason is written in the strip
        assert all(st.patch.get_hatch() for st, _, _ in strips)                                              # the strip is hatched: visibly not a data region


def test_f10_markers_are_large_outlined_and_the_whiskers_are_visible(drawn, tmp_path, monkeypatch):
    _, stats, cross, _ = drawn
    seen = {}
    original = PC.finish
    monkeypatch.setattr(PC, "finish", lambda fig, ctx, fig_id, *a, **k: (seen.setdefault(fig_id, list(fig.axes)), original(fig, ctx, fig_id, *a, **k))[1])
    ctx = PC.build_figures(stats, tmp_path / "f10b", only=["f10"], formats=("png",), cross=cross)
    for fig_id, axes in seen.items():
        lines = [ln for ax in axes if ax.get_yscale() == "log" for ln in ax.get_lines()]
        data = [ln for ln in lines if ln.get_marker() in ("o", "D", "s", "^")]
        assert min(ln.get_markersize() for ln in data) >= 1.5 * 4.4 - 1e-9                                  # at least 1.5x the earlier 4.4 pt
        triangle = [ln for ln in data if ln.get_marker() == "^"]
        circle = [ln for ln in data if ln.get_marker() == "o"]
        assert triangle[0].get_markersize() > circle[0].get_markersize()                                   # the faintest (Spark GPU) is drawn larger
        assert luminance(triangle[0].get_markeredgecolor()) < 0.5 * luminance(triangle[0].get_markerfacecolor())   # and outlined in a dark shade of its own colour
        whiskers = [ln for ln in lines if ln.get_linestyle() == "-" and len(ln.get_ydata()) == 2 and ln.get_xdata()[0] == ln.get_xdata()[1]
                    and matplotlib.colors.to_hex(ln.get_color()) != matplotlib.colors.to_hex(PC.GRID)]          # not the query separators
        assert whiskers and all(ln.get_linewidth() >= 1.0 for ln in whiskers)
    entry = next(m for m in ctx.manifest if m["id"] == "f10_headline_times")
    assert "hatched strip" in entry["caption_draft"] and "hidden behind the marker" in entry["caption_draft"] and "end caps" in entry["caption_draft"]
    assert "A cross marks" not in entry["caption_draft"]
