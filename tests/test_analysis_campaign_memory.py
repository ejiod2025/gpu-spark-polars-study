"""GPU memory (F7, secondary.gpu_memory): what each engine's number measures.

Spark GPU's nvidia-smi sample is the RAPIDS pool reserved at plugin start (flat at ~94 GiB from SF1 to SF300 on the GH200), so the per-action
memory comes from the task accumulator gpuMaxDeviceMemoryBytes in the Spark event logs; the sample is only a labelled reference. Polars GPU's
sample is the RMM async pool's high-water mark (it never falls within a session). Without event logs Spark GPU is left out of the memory figure.
"""
from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path
import shutil
import sys

import matplotlib
import pytest

matplotlib.use("Agg")

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import analyze_campaign as AC  # noqa: E402
import plot_campaign as PC  # noqa: E402

QUIET = dict(round_sigma=0.0, query_sigma=0.0, trial_sigma=0.0, position_effect=0.0, gpu_interval=60.0)
GIB = 1024 ** 3


def build(tmp_path, name="tree", **kwargs):
    root = tmp_path / name
    AC.make_synthetic_campaign(root, seed=3, a100=False, side_study=False, **{**QUIET, **kwargs})
    return root


def analyze(root):
    return json.loads(json.dumps(AC.analyze(root, energy=False)))


# ------------------------------------------------------------------ event-log reader

def task_line(launch_ms, accumulables):
    return json.dumps({"Event": "SparkListenerTaskEnd", "Task Info": {"Task ID": 1, "Launch Time": launch_ms, "Finish Time": launch_ms + 5, "Accumulables": [
        {"ID": i, "Name": name, "Update": value, "Value": value} for i, (name, value) in enumerate(accumulables)]}})


@pytest.mark.parametrize("value, expected", [("53.31MB (55903584 bytes)", 55903584), ("1048576", 1048576), (2048, 2048), (3.0, 3), ("12 ms", None), (None, None), (True, None)])
def test_accumulator_byte_counts_are_parsed_from_every_format_spark_writes(value, expected):
    assert AC.parse_byte_count(value) == expected


def test_only_task_end_lines_with_the_device_memory_accumulator_are_read_and_compressed_logs_are_noted(tmp_path):
    events = tmp_path / "spark_gpu-events"
    events.mkdir()
    (events / "local-1").write_text("\n".join([
        json.dumps({"Event": "SparkListenerLogStart"}),
        task_line(1000, [("gpuTime", "5 ms")]),                                              # no device-memory accumulator
        task_line(2000, [("gpuMaxDeviceMemoryBytes", "1.00MB (1048576 bytes)")]),
        task_line(3000, [("gpuMaxDeviceMemoryBytes", "garbage")]),                           # unreadable value: skipped, not zero
        json.dumps({"Event": "SparkListenerTaskStart", "note": "gpuMaxDeviceMemoryBytes"}),   # right text, wrong event
        "not json at all gpuMaxDeviceMemoryBytes SparkListenerTaskEnd"]) + "\n")
    with gzip.open(events / "local-2.gz", "wt") as handle:
        handle.write(task_line(4000, [("gpuMaxDeviceMemoryBytes", "4194304")]) + "\n")
    (events / "local-3.zstd").write_bytes(b"\x28\xb5\x2f\xfd")
    tasks, notes = AC.read_spark_task_memory(events)
    assert tasks == [(2000.0, 1048576), (4000.0, 4194304)]
    assert notes == ["local-3.zstd: compressed event log (.zstd) not read"]
    assert AC.read_spark_task_memory(tmp_path / "missing") == ([], [])


def test_each_action_gets_the_largest_value_among_the_tasks_launched_inside_its_window():
    def iso(ms):
        return AC.datetime.fromtimestamp(ms / 1000, tz=AC.timezone.utc).isoformat()

    records = [{"query": "q01", "phase": "measured", "trial": 1, "started_at_utc": iso(10_000), "ended_at_utc": iso(20_000)},
               {"query": "q01", "phase": "measured", "trial": 2, "started_at_utc": iso(30_000), "ended_at_utc": iso(40_000)},
               {"query": "q03", "phase": "measured", "trial": 1, "started_at_utc": iso(50_000), "ended_at_utc": iso(60_000)},
               {"query": "bad", "phase": "measured", "trial": 1}]
    tasks = [(9_000.0, 999), (10_000.0, 5), (15_000.0, 70), (20_000.0, 60), (20_001.0, 888), (35_000.0, 7)]
    mapped = AC.action_task_memory(records, tasks)
    assert mapped == {("q01", "measured", 1): 70, ("q01", "measured", 2): 7, ("q03", "measured", 1): None}      # inclusive window, outside tasks ignored, no tasks -> None


def test_a_series_that_never_falls_is_a_high_water_mark_and_one_that_does_is_not():
    assert AC.nondecreasing([1, 1, 2 * GIB, 2 * GIB, 3 * GIB]) and AC.nondecreasing([5 * GIB, 5 * GIB + 10_000_000, 5 * GIB])      # jitter tolerated
    assert not AC.nondecreasing([5 * GIB, 2 * GIB, 6 * GIB]) and AC.nondecreasing([])


# ------------------------------------------------------------------ the analysis

def event_truth(root, round_id, suite, scale):
    """Independent recomputation from the files: {(query, trial): largest accumulator value in the record's window}."""
    session = json.loads(next((root / f"round-{round_id}" / suite / f"sf{scale}" / "spark_gpu").glob("results.json")).read_text())
    tasks = []
    for path in (root / f"round-{round_id}" / suite / f"sf{scale}" / "spark_gpu-events").iterdir():
        for line in path.read_text().splitlines():
            event = json.loads(line)
            if event["Event"] == "SparkListenerTaskEnd":
                for acc in event["Task Info"]["Accumulables"]:
                    if acc["Name"] == "gpuMaxDeviceMemoryBytes":
                        tasks.append((event["Task Info"]["Launch Time"], int(acc["Update"].split("(")[1].split(" ")[0])))
    out = {}
    for record in session["records"]:
        if record["phase"] != "measured":
            continue
        start = AC.parse_utc(record["started_at_utc"]).timestamp() * 1000
        end = AC.parse_utc(record["ended_at_utc"]).timestamp() * 1000
        out[(record["query"], record["trial"])] = max(v for t, v in tasks if start <= t <= end)
    return out


def test_spark_gpu_memory_is_the_task_reported_maximum_per_action_not_the_nvidia_smi_pool(tmp_path):
    root = build(tmp_path)
    stats = analyze(root)
    memory = stats["secondary"]["gpu_memory"]
    assert memory["measure"] == {"spark_gpu": "task_reported_max_device_memory", "polars_gpu": "process_high_water_mark"}
    assert memory["task_reported_cells"]["spark_gpu"] == {"with_value": 48, "cells": 48}
    for suite, scale, query in (("baseline", "300", "q03"), ("complex", "30", "cx_q09"), ("baseline", "1", "q06")):
        per_round = []
        for r in (1, 2, 3, 4):
            per_round.append(max(v for (q, trial), v in event_truth(root, r, suite, scale).items() if q == query))
        cell = memory["cells"][suite][f"sf{scale}"][query]["spark_gpu"]
        assert cell["task_reported"]["per_round_max_bytes"] == per_round and cell["task_reported"]["max_bytes"] == max(per_round)
        assert cell["max_bytes"] > 30 * GIB                                                    # the nvidia-smi sample is the pool: far above any task
    low, high = memory["summary"]["baseline"]["sf1"]["spark_gpu"], memory["summary"]["baseline"]["sf300"]["spark_gpu"]
    assert low["task_reported"]["max_bytes"] < high["task_reported"]["max_bytes"]                 # it follows the data size; the pool does not
    assert low["task_reported"]["max_bytes"] < 0.05 * low["max_bytes"]
    assert "not a simultaneous total" in memory["measure_note"]["spark_gpu"]


def test_polars_gpu_sample_is_labelled_as_a_session_high_water_mark_and_the_check_counts_sessions(tmp_path):
    memory = analyze(build(tmp_path))["secondary"]["gpu_memory"]
    check = memory["high_water_check"]["polars_gpu"]
    assert (check["sessions"], check["nondecreasing"], check["falling_sessions"]) == (32, 32, []) and check["largest_fall_bytes"] < 0.02 * memory["capacity_bytes"]
    assert "keeps freed blocks" in memory["measure_note"]["polars_gpu"] and "upper bound" in memory["measure_note"]["polars_gpu"]
    assert memory["high_water_check"]["spark_gpu"]["sessions"] == 32


def test_without_event_logs_spark_gpu_is_pool_reservation_only(tmp_path):
    root = build(tmp_path)
    for path in root.glob("round-*/*/sf*/spark_gpu-events"):
        shutil.rmtree(path)
    memory = analyze(root)["secondary"]["gpu_memory"]
    assert memory["measure"]["spark_gpu"] == "pool_reservation_only" and memory["task_reported_cells"]["spark_gpu"]["with_value"] == 0
    assert "left out of the memory figure" in memory["measure_note"]["spark_gpu"]
    assert memory["summary"]["baseline"]["sf300"]["spark_gpu"]["task_reported"] is None
    # a log that has no accumulator at all is the same case
    root2 = build(tmp_path, "plain")
    for path in root2.glob("round-*/*/sf*/spark_gpu-events/*"):
        path.write_text(json.dumps({"Event": "SparkListenerLogStart"}) + "\n")
    assert analyze(root2)["secondary"]["gpu_memory"]["measure"]["spark_gpu"] == "pool_reservation_only"


def test_a_partly_missing_accumulator_still_counts_the_actions_that_have_it(tmp_path):
    root = build(tmp_path)
    path = next((root / "round-1" / "baseline" / "sf10" / "spark_gpu-events").iterdir())
    keep = [line for line in path.read_text().splitlines() if "SparkListenerTaskEnd" not in line or "gpuMaxDeviceMemoryBytes" not in line or '"Launch Time": ' not in line][:3]
    path.write_text("\n".join(keep) + "\n")                     # round 1, SF10: no task value left
    memory = analyze(root)["secondary"]["gpu_memory"]
    cell = memory["cells"]["baseline"]["sf10"]["q01"]["spark_gpu"]["task_reported"]
    assert cell["per_round_max_bytes"][0] is None and all(v is not None for v in cell["per_round_max_bytes"][1:]) and cell["recorded"]
    assert memory["measure"]["spark_gpu"] == "task_reported_max_device_memory"


def test_the_memory_table_names_the_measure_of_every_row(tmp_path):
    out = tmp_path / "out"
    assert AC.main(["--root", str(build(tmp_path)), "--output", str(out), "--no-energy"]) == 0
    rows = list(csv.DictReader((out / "tables" / "t13_gpu_memory.csv").open()))
    measures = {(r["engine"], r["measure"]) for r in rows}
    assert measures == {("polars_gpu", "nvidia-smi, process high-water mark (RMM pool)"), ("spark_gpu", "nvidia-smi, pool reservation"),
                        ("spark_gpu", "task-reported max (not a total)")}
    text = (out / "tables" / "t13_gpu_memory.md").read_text()
    assert "Measure" in text and "RMM async pool" in text and "gpuMaxDeviceMemoryBytes" in text


def test_schema_documents_the_memory_keys(tmp_path):
    stats = analyze(build(tmp_path))
    flat = AC.flatten_stats(stats)
    undocumented = [k for k, v in flat.items() if not AC.schema_matches(k) and not k.startswith("secondary.energy.")
                    and not (v == {} and any(p.startswith(k + ".") for p, _, _ in AC.SCHEMA))]
    assert undocumented == []
    assert {"secondary.gpu_memory.measure.spark_gpu", "secondary.gpu_memory.high_water_check.polars_gpu.sessions",
            "secondary.gpu_memory.summary.baseline.sf30.spark_gpu.task_reported.max_bytes"} <= set(flat)


# ------------------------------------------------------------------ F7

def draw_f7(stats, out_dir, monkeypatch):
    """(manifest entry, legend texts, plotted lines per panel as [(marker, color, ydata)]) of the double-width F7."""
    captured, original = {}, PC.finish

    def spy(fig, ctx, fig_id, *args, **kwargs):
        fig.canvas.draw()
        captured["texts"] = [t.get_text() for t in fig.findobj(matplotlib.text.Text) if t.get_text()]
        captured["lines"] = [[(line.get_marker(), line.get_color(), list(line.get_ydata()), line.get_linewidth()) for line in ax.get_lines()] for ax in fig.axes]
        captured["legend"] = [t.get_text() for leg in fig.legends for t in leg.get_texts()]
        return original(fig, ctx, fig_id, *args, **kwargs)

    monkeypatch.setattr(PC, "finish", spy)
    ctx = PC.build_figures(stats, out_dir, only=["f07"], formats=("png",), widths=("double",))
    return next(m for m in ctx.manifest if m["id"] == "f07_gpu_memory"), captured


def test_f7_plots_the_task_reported_memory_and_draws_the_pool_only_as_a_labelled_reference(tmp_path, monkeypatch):
    stats = analyze(build(tmp_path))
    entry, drawn = draw_f7(stats, tmp_path / "f7", monkeypatch)
    legend = " | ".join(drawn["legend"])
    assert "Spark GPU: largest task-reported device memory (not a total)" in legend and "Spark GPU: RAPIDS pool reservation (nvidia-smi)" in legend
    assert "Polars GPU: nvidia-smi, RMM pool high-water mark" in legend
    summary = stats["secondary"]["gpu_memory"]["summary"]["baseline"]
    summary_by_suite = stats["secondary"]["gpu_memory"]["summary"]
    spark_marker = PC.ENGINE_STYLE["spark_gpu"]["marker"]
    series = [(m, y) for m, c, y, w in drawn["lines"][0] if m == spark_marker]
    expected = [summary[sc]["spark_gpu"]["task_reported"]["median_bytes"] / GIB for sc in ("sf1", "sf10", "sf30", "sf100", "sf300")]
    assert len(series) == 1 and series[0][1] == pytest.approx(expected)                       # the data series IS the task-reported median
    pool = [y for m, c, y, w in drawn["lines"][0] if c == PC.ENGINE_STYLE["spark_gpu"]["color"] and m in (None, "None", "") and w > 1.0]
    assert len(pool) == 1 and pool[0] == pytest.approx([summary[sc]["spark_gpu"]["median_bytes"] / GIB for sc in ("sf1", "sf10", "sf30", "sf100", "sf300")])
    assert min(series[0][1]) < 1.0 < pool[0][0]                                                # reference far above the data series
    texts = " ".join(drawn["texts"])
    free = sum(1 for suite in ("baseline", "complex")
               if max(summary_by_suite[suite][sc]["polars_gpu"]["median_bytes"] for sc in summary_by_suite[suite])
               < 0.72 * summary_by_suite[suite][next(iter(summary_by_suite[suite]))]["spark_gpu"]["median_bytes"])
    assert texts.count("Spark GPU pool reservation (nvidia-smi)") == free             # named in the panel unless the Polars GPU curve would run through the label (the legend names it anyway)
    caption = entry["caption_draft"]
    assert "gpuMaxDeviceMemoryBytes" in caption and "not a simultaneous total" in caption and "high-water mark" in caption and "32 of 32 sessions" in caption
    assert "reserved at plugin start" in caption


def test_f7_drops_spark_gpu_and_says_why_when_only_the_pool_reservation_exists(tmp_path, monkeypatch):
    root = build(tmp_path)
    for path in root.glob("round-*/*/sf*/spark_gpu-events"):
        shutil.rmtree(path)
    stats = analyze(root)
    entry, drawn = draw_f7(stats, tmp_path / "f7", monkeypatch)
    assert not any("Spark GPU" in text for text in drawn["legend"])
    assert not any(m == PC.ENGINE_STYLE["spark_gpu"]["marker"] for lines in drawn["lines"] for m, *_ in lines)
    caption = entry["caption_draft"]
    assert "Spark GPU is not shown" in caption and "pool_reservation_only" in caption and "not memory in use" in caption
    assert any("Spark GPU is not shown" in t for t in drawn["texts"])                          # the figure itself says it, not only the caption


GH200 = REPO / "rerun" / "hosts" / "gh200" / "campaign-gh200"


@pytest.mark.skipif(not (GH200 / "round-1" / "gates.json").is_file(), reason="the copied GH200 campaign is not in this checkout")
def test_the_real_gh200_event_logs_give_per_action_task_memory_far_below_the_pool_reservation(tmp_path):
    out = tmp_path / "out"
    assert AC.main(["--root", str(GH200), "--output", str(out), "--no-energy"]) == 0
    memory = json.loads((out / "stats.json").read_text())["secondary"]["gpu_memory"]
    assert memory["measure"]["spark_gpu"] == "task_reported_max_device_memory"
    cells = memory["task_reported_cells"]["spark_gpu"]
    assert cells["with_value"] == cells["cells"] > 0                                           # every Spark GPU cell has a task-reported value
    summary = memory["summary"]
    pool = summary["baseline"]["sf1"]["spark_gpu"]
    assert memory["constant_check"]["spark_gpu"]["constant"] == memory["constant_check"]["spark_gpu"]["sessions"] > 0        # on the GH200 the sample is a constant pool
    assert pool["max_bytes"] > 0.9 * memory["capacity_bytes"] * 0.95                           # the nvidia-smi sample is the ~94 GiB pool even at SF1
    assert pool["task_reported"]["max_bytes"] < 0.02 * pool["max_bytes"]                       # while the largest task at SF1 reports well under 2% of it
    assert summary["baseline"]["sf1"]["spark_gpu"]["task_reported"]["max_bytes"] < summary["baseline"]["sf300"]["spark_gpu"]["task_reported"]["max_bytes"]
    check = memory["high_water_check"]["polars_gpu"]
    assert check["sessions"] > 0 and check["nondecreasing"] == check["sessions"]               # Polars GPU's sample is a session high-water mark


def test_a_spark_gpu_sample_that_varies_within_sessions_is_not_drawn_as_a_pool_reservation(tmp_path, monkeypatch):
    """As on the B200: the first actions show the whole pool, later ones a small value, so the sample is not a reservation line."""
    root = build(tmp_path)
    for path in root.glob("round-*/*/sf*/spark_gpu/results.json"):
        session = json.loads(path.read_text())
        ordered = sorted(session["records"], key=lambda r: r["started_at_utc"])
        for record in ordered[3:]:
            record["peak_gpu_memory_bytes"] = int(record["peak_gpu_memory_bytes"] * 0.05)
        path.write_text(json.dumps(session))
    stats = analyze(root)
    memory = stats["secondary"]["gpu_memory"]
    flat = memory["constant_check"]["spark_gpu"]
    assert (flat["sessions"], flat["constant"]) == (32, 0) and flat["later_minimum_median_bytes"] < 0.1 * flat["first_sample_median_bytes"]
    entry, drawn = draw_f7(stats, tmp_path / "f7", monkeypatch)
    assert not any("pool reservation" in text for text in drawn["legend"])
    assert "Spark GPU pool reservation (nvidia-smi)" not in " ".join(drawn["texts"])
    assert "constant within only 0 of 32 sessions" in entry["caption_draft"] and "not drawn" in entry["caption_draft"]
    assert any("Spark GPU: largest task-reported device memory" in text for text in drawn["legend"])        # the task-reported series is still there


def test_the_constant_check_is_true_for_a_reserved_pool_and_false_otherwise():
    assert AC.constant_series([94.0, 94.1, 93.9]) and AC.constant_series([5])
    assert not AC.constant_series([177.7, 2.6, 2.5]) and not AC.constant_series([]) and not AC.constant_series([0, 0])


B200 = REPO / "rerun" / "hosts" / "b200" / "campaign-b200"


@pytest.mark.skipif(not (B200 / "round-1" / "gates.json").is_file(), reason="the copied B200 campaign is not in this checkout")
def test_the_real_b200_spark_gpu_sample_is_not_a_constant_pool(tmp_path):
    out = tmp_path / "out"
    assert AC.main(["--root", str(B200), "--output", str(out), "--no-energy"]) == 0
    memory = json.loads((out / "stats.json").read_text())["secondary"]["gpu_memory"]
    assert memory["measure"]["spark_gpu"] == "task_reported_max_device_memory"
    check = memory["constant_check"]["spark_gpu"]
    assert check["sessions"] > 0 and check["constant"] < check["sessions"]                     # the pool is only seen in the first actions of a session
    assert memory["high_water_check"]["polars_gpu"]["nondecreasing"] == memory["high_water_check"]["polars_gpu"]["sessions"]


def test_a_polars_gpu_sample_that_falls_is_reported_not_called_a_high_water_mark(tmp_path, monkeypatch):
    """On a small card the RMM pool can sit near the device size and the sample can fall (A100 40 GB, complex SF300): the check and the wording follow the data."""
    root = build(tmp_path)
    path = root / "round-2" / "complex" / "sf300" / "polars_gpu" / "results.json"
    session = json.loads(path.read_text())
    peaks = [r for r in sorted(session["records"], key=lambda r: r["started_at_utc"]) if r.get("peak_gpu_memory_bytes")]
    top = max(r["peak_gpu_memory_bytes"] for r in peaks)
    for record in peaks[len(peaks) // 2:]:
        record["peak_gpu_memory_bytes"] = int(top * 0.5)
    path.write_text(json.dumps(session))
    stats = analyze(root)
    memory = stats["secondary"]["gpu_memory"]
    check = memory["high_water_check"]["polars_gpu"]
    assert check["sessions"] == 32 and check["nondecreasing"] == 31 and len(check["falling_sessions"]) == 1
    fall = check["falling_sessions"][0]
    assert (fall["round"], fall["suite"], fall["scale"]) == (2, "complex", "sf300") and fall["largest_fall_bytes"] == check["largest_fall_bytes"] >= 0.4 * top
    note = memory["measure_note"]["polars_gpu"]
    assert "it did not fall in 31 of 32 sessions" in note and "fell in the other 1" in note and "never falls" not in note
    ctx = PC.build_figures(stats, tmp_path / "figs", only=["f07"], formats=("png",))
    caption = next(m for m in ctx.manifest if m["id"] == "f07_gpu_memory")["caption_draft"]
    assert "does not fall within a session in 31 of 32 sessions" in caption and "falls by up to" in caption
    assert "never falls within a session" not in caption


def test_a_spark_gpu_sample_that_is_not_constant_is_not_called_a_pool_reservation(tmp_path):
    """B200: the first action of every Spark GPU session reads ~178 GiB and later actions a few GiB. Note, table label and check follow the data."""
    root = build(tmp_path)
    for path in root.glob("round-*/*/sf*/spark_gpu/results.json"):
        session = json.loads(path.read_text())
        ordered = sorted((r for r in session["records"] if r.get("peak_gpu_memory_bytes")), key=lambda r: r["started_at_utc"])
        for k, record in enumerate(ordered):
            record["peak_gpu_memory_bytes"] = 177 * GIB if k == 0 else (3 + k % 5) * GIB
        path.write_text(json.dumps(session))
    stats = analyze(root)
    memory = stats["secondary"]["gpu_memory"]
    flat = memory["constant_check"]["spark_gpu"]
    assert flat["constant"] == 0 and flat["sessions"] == 32
    assert flat["first_sample_median_bytes"] == 177 * GIB and 3 * GIB <= flat["later_minimum_median_bytes"] <= 4 * GIB
    note = memory["measure_note"]["spark_gpu"]
    assert "constant within a session in only 0 of 32 sessions" in note and "not a fixed pool reservation" in note and "177.0 GiB" in note
    assert "are the RAPIDS pool reservation made at plugin start" not in note and "not a simultaneous total" in note
    out = tmp_path / "out"
    assert AC.main(["--root", str(root), "--output", str(out), "--no-energy"]) == 0
    table = (out / "tables" / "t13_gpu_memory.csv").read_text()
    assert "nvidia-smi sample (not constant within sessions)" in table and "nvidia-smi, pool reservation" not in table


def test_a_constant_spark_gpu_sample_keeps_the_pool_reservation_wording(tmp_path):
    memory = analyze(build(tmp_path))["secondary"]["gpu_memory"]
    assert memory["constant_check"]["spark_gpu"]["constant"] == 32
    assert "RAPIDS pool reservation made at plugin start" in memory["measure_note"]["spark_gpu"]
