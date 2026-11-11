"""Blinded Paper 3 feasibility audit (tools/paper3_feasibility.py) on synthetic campaign trees.

Every fixture carries a distinctive power value ("777.77 W") and query time (2.5 s) so the tests can prove the report never echoes
an outcome. The synthetic host is a miniature of the real layout: one round, one suite, two scales, two engines.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("paper3_feasibility", REPO / "tools" / "paper3_feasibility.py")
P3 = importlib.util.module_from_spec(SPEC)
sys.modules["paper3_feasibility"] = P3
SPEC.loader.exec_module(P3)

T0 = datetime(2026, 10, 30, 3, 57, 3, tzinfo=timezone.utc)
GPU_HEADER = ("timestamp, index, utilization.gpu [%], utilization.memory [%], memory.used [MiB], power.draw [W], "
              "temperature.gpu, clocks.current.sm [MHz], clocks.current.memory [MHz]")
HOST_HEADER = "timestamp_utc,scale,engine,phase,cpu_percent,available_bytes,cached_bytes,swap_used_bytes,disk_free_bytes"
QUERY_SECONDS = 2.5
POWER_TEXT = "777.77 W"
OVERHEAD = 0.02
INTER_RECORD_GAP = 0.003
SETUP = 0.5
TEARDOWN = 0.4
QUERIES = ["q01", "q03"]
SESSIONS = [("1", "polars"), ("1", "polars_gpu"), ("10", "polars"), ("10", "polars_gpu")]


def iso(moment):
    return moment.isoformat()


def local_gpu_stamp(moment, offset_s):
    return (moment + timedelta(seconds=offset_s)).strftime("%Y/%m/%d %H:%M:%S.%f")[:-3]


class Fixture:
    """Builds <root>/hosts/hx/final/campaign-hx/... plus a FINAL manifest and records what it wrote."""

    def __init__(self, root, offset_s=0, gap=None, power=POWER_TEXT, drop_results=None, extra_unexplained=0.0, fail_query=None,
                 unsupported=("cx_q13",), truncate_gpu=False, null_power_rows=(), na_power_rows=(), bad_power_rows=(), integral_host_stamp=False,
                 host_label_shift=0.0, timed_stamps=False):
        self.root = Path(root)
        self.timed_stamps = timed_stamps
        self.host = "hx"
        self.final = self.root / "hosts" / "hx" / "final"
        self.campaign = self.final / "campaign-hx"
        self.suite = self.campaign / "round-1" / "baseline"
        self.manifests = self.root / "manifests"
        self.offset_s, self.gap, self.power = offset_s, gap, power
        self.drop_results, self.extra_unexplained, self.fail_query = drop_results, extra_unexplained, fail_query
        self.unsupported = list(unsupported)
        self.windows = []   # (ident-ish, start, end)
        self.record_windows = []
        self.build(truncate_gpu, set(null_power_rows), set(na_power_rows), set(bad_power_rows), integral_host_stamp, host_label_shift)

    def write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")

    def build(self, truncate_gpu, null_power_rows, na_power_rows, bad_power_rows, integral_host_stamp, host_label_shift):
        design = {"engines": ["polars", "polars_gpu"], "suites": {"baseline": {"scales": ["1", "10"]}},
                  "main_rounds": [{"round": 1, "suite_order": ["baseline"]}], "expect_gpu_unsupported": self.unsupported,
                  "side_study": None}
        self.write_json(self.campaign / "campaign.json", {"design": design})
        for name in ("attempt.json", "gates.json"):
            self.write_json(self.campaign / "round-1" / name, {})
        cursor = T0 + timedelta(seconds=3)
        schedule = []
        for scale, engine in SESSIONS:
            start = cursor
            worker_start = start + timedelta(seconds=0.3)
            first = worker_start + timedelta(seconds=SETUP + 0.004)
            clock = first
            records, errors = [], []
            for query in QUERIES:
                attempts = [("warmup", 0)] + [("measured", t) for t in (1, 2)]
                for phase, trial in attempts:
                    if engine == "polars_gpu" and query == self.fail_query:
                        if phase == "warmup":
                            errors.append({"query": query, "phase": phase, "trial": trial, "type": "X",
                                           "failed_at_utc": iso(clock + timedelta(seconds=0.1))})
                            clock += timedelta(seconds=0.1 + INTER_RECORD_GAP)
                        continue
                    end = clock + timedelta(seconds=QUERY_SECONDS + OVERHEAD)
                    record = {"query": query, "phase": phase, "trial": trial, "seconds": QUERY_SECONDS,
                              "started_at_utc": iso(clock), "ended_at_utc": iso(end)}
                    if self.timed_stamps:   # the newer worker also writes a tight timed interval and CLOCK_MONOTONIC stamps
                        record.update(timed_start_utc=iso(clock + timedelta(seconds=0.001)),
                                      timed_end_utc=iso(clock + timedelta(seconds=0.001 + QUERY_SECONDS)),
                                      timed_start_monotonic_ns=10, timed_end_monotonic_ns=20)
                    records.append(record)
                    self.record_windows.append((clock, end))
                    clock = end + timedelta(seconds=INTER_RECORD_GAP)
            completed = clock + timedelta(seconds=self.extra_unexplained)
            end = completed + timedelta(seconds=TEARDOWN)
            schedule.append({"scale": scale, "engine": engine, "start_utc": iso(start),
                             "process_wall_seconds": (end - start).total_seconds(), "returncode": 0, "status": "complete"})
            self.windows.append((scale, engine, start, end))
            if (scale, engine) != self.drop_results:
                result = {"engine": engine, "queries": QUERIES, "runs": 2, "started_at_utc": iso(worker_start),
                          "completed_at_utc": iso(completed), "session_and_table_setup_seconds": SETUP,
                          "launch_to_engine_ready_seconds": 0.3 + SETUP, "records": records, "query_errors": errors,
                          "query_execution_status": "partial" if errors else "complete"}
                out = self.suite / f"sf{scale}" / engine
                self.write_json(out / "results.json", result)
                (out / "plans").mkdir()
                for record in records:
                    (out / "plans" / f"{record['query']}-{record['phase']}-{record['trial']}.txt").write_text("plan\n")
                (self.suite / f"sf{scale}" / f"{engine}.log").write_text("log\n")
            cursor = end + timedelta(seconds=0.5)
        self.stream_end = cursor - timedelta(seconds=0.4)
        self.write_json(self.suite / "schedule.json", schedule)
        smi = (f"Timestamp                                 : "
               f"{(T0 - timedelta(seconds=0.7) + timedelta(seconds=self.offset_s)).strftime('%a %b %d %H:%M:%S %Y')}\n"
               f"Driver Version                            : 580.1.0\n"
               f"    Product Name                          : NVIDIA TestGPU\n    Product Architecture                  : Hopper\n"
               f"    Average Power Draw        : {self.power}\n"
               f"    Instantaneous Power Draw  : {self.power}\n")
        self.write_json(self.suite / "environment.json", {
            "started_at_utc": iso(T0 - timedelta(seconds=1)), "gpu_telemetry": {"started": True},
            "source_sha256": {"worker.py": "w" * 64, "run_experiment.py": "r" * 64},
            "inventory": [{"command": ["nvidia-smi", "-q"], "returncode": 0, "output": smi}]})
        # gpu.csv: 5 Hz, local wall time, from T0 until just past the last session
        rows, moment, index = [GPU_HEADER], T0, 0
        while moment <= self.stream_end:
            skip = self.gap and self.gap[0] <= (moment - T0).total_seconds() < self.gap[0] + self.gap[1]
            if not skip:
                draw = ("" if index in null_power_rows else "[N/A]" if index in na_power_rows
                        else "12 watts" if index in bad_power_rows else self.power)
                rows.append(f"{local_gpu_stamp(moment, self.offset_s)}, 0, 55 %, 3 %, 4096 MiB, {draw}, 41, 1410 MHz, 1593 MHz")
            moment += timedelta(seconds=0.2)
            index += 1
        text = "\n".join(rows) + "\n"
        if truncate_gpu:
            text = text[:-30]
        (self.suite / "gpu.csv").write_text(text)
        # host.csv: 1 Hz UTC with phase labels
        hrows, moment = [HOST_HEADER], T0 + timedelta(seconds=0.01)
        if integral_host_stamp:
            moment = T0
        while moment <= self.stream_end - timedelta(seconds=0.2):
            label = ("", "", "idle")
            for scale, engine, start, end in self.windows:
                if start + timedelta(seconds=host_label_shift) <= moment <= end:
                    label = (scale, engine, "execute")
            hrows.append(f"{iso(moment)},{label[0]},{label[1]},{label[2]},61.5,123456789,2,3,4")
            moment += timedelta(seconds=1)
        (self.suite / "host.csv").write_text("\n".join(hrows) + "\n")
        self.write_manifest()

    def write_manifest(self):
        self.manifests.mkdir(exist_ok=True)
        lines = []
        for path in sorted(self.final.rglob("*")):
            if path.is_file():
                lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(self.final)}")
        (self.manifests / "FINAL-hx.SHA256SUMS").write_text("\n".join(lines) + "\n")

    def audit(self, verify="all"):
        return P3.audit_host("hx", self.final, self.manifests / "FINAL-hx.SHA256SUMS", verify)

    def run(self, verify="all"):
        return P3.run(self.root / "hosts", self.manifests, ("hx",), verify)


@pytest.fixture
def clean(tmp_path):
    return Fixture(tmp_path)


# ------------------------------------------------------------------------------------------------------------- units
def test_parse_utc_accepts_microsecond_free_isoformat():
    whole = datetime(2026, 10, 30, 3, 57, 4, tzinfo=timezone.utc)
    assert whole.isoformat().endswith("04+00:00")  # isoformat() drops ".000000"
    assert P3.parse_utc(whole.isoformat()) == P3.to_us(whole)
    assert P3.parse_utc("2026-10-30T03:57:03.310588+00:00") == P3.to_us(datetime(2026, 10, 30, 3, 57, 3, 310588, tzinfo=timezone.utc))
    assert P3.parse_utc("2026-10-30T03:57:03") is None   # naive: refused, not guessed
    assert P3.parse_utc(None) is None and P3.parse_utc("garbage") is None


def test_quantile_and_distribution():
    assert P3.quantile([1, 2, 3, 4], 0.5) == 2.5
    assert P3.quantile([], 0.5) is None
    d = P3.distribution([None, 1.0, 3.0])
    assert d["n"] == 2 and d["min"] == 1.0 and d["max"] == 3.0 and d["p50"] == 2.0
    assert P3.distribution([None]) == {"n": 0}


def test_resolve_offset_is_unambiguous_only_for_one_quarter_hour():
    assert P3.resolve_offset(-0.05, 0.8) == ([0], True)
    assert P3.resolve_offset(3599.9, 3600.9) == ([3600], True)
    assert P3.resolve_offset(100, 200) == ([], False)          # no real time zone is 100-200 s from UTC
    assert P3.resolve_offset(-1000, 1000)[1] is False          # two candidates (-900, 0, +900): ambiguous
    assert P3.resolve_offset(1.0, -1.0) == ([], False)         # empty bracket


def test_window_stats_counts_gaps_and_edges():
    stamps = [int(x * 1e6) for x in (0.0, 0.2, 0.4, 0.6, 2.6, 2.8, 3.0)]
    stats = P3.window_stats(stamps, 0, 3_000_000, 1.0)
    assert stats["n"] == 7 and stats["gaps_over_threshold"] == 1
    assert stats["max_gap_s"] == pytest.approx(2.0)
    assert stats["covered_fraction"] == pytest.approx(1 - 2.0 / 3.0)
    edge = P3.window_stats(stamps, -2_000_000, 3_000_000, 1.0)   # 2 s leading edge with no sample is a hole too
    assert edge["gaps_over_threshold"] == 2
    short = P3.window_stats([], 0, 500_000, 1.0)                  # shorter than the period: zero samples, but no gap > threshold
    assert short["n"] == 0 and short["gaps_over_threshold"] == 0 and short["covered_fraction"] is None
    assert P3.window_stats(stamps, 5, 5, 1.0) is None


# ---------------------------------------------------------------------------------------------------------- readers
def test_gpu_reader_counts_null_and_unparseable_draw_without_keeping_values(tmp_path):
    fx = Fixture(tmp_path, null_power_rows={3, 4}, na_power_rows={5}, bad_power_rows={7})
    stream = P3.read_gpu_csv(fx.suite / "gpu.csv")
    assert stream.header_ok and stream.ends_with_newline
    assert (stream.draw_null_rows, stream.draw_na_rows, stream.draw_unparseable_rows) == (2, 1, 1)
    assert stream.draw_ok_rows == stream.rows - 4
    assert stream.bad_timestamp_rows == 0 and stream.indices == ("0",)
    assert not any(hasattr(stream, name) for name in ("draw", "power", "watts"))
    assert 777 not in stream.t_us


def test_gpu_reader_flags_truncated_final_line(tmp_path):
    fx = Fixture(tmp_path, truncate_gpu=True)
    stream = P3.read_gpu_csv(fx.suite / "gpu.csv")
    assert not stream.ends_with_newline
    assert stream.incomplete_rows >= 1


def test_gpu_reader_rejects_a_changed_header(tmp_path):
    fx = Fixture(tmp_path)
    path = fx.suite / "gpu.csv"
    path.write_text(path.read_text().replace("power.draw [W]", "power.draw.average [W]"))
    assert not P3.read_gpu_csv(path).header_ok


def test_host_reader_parses_microsecond_free_stamps(tmp_path):
    fx = Fixture(tmp_path, integral_host_stamp=True)
    stream = P3.read_host_csv(fx.suite / "host.csv")
    assert stream.stamps_without_fraction >= 1
    assert stream.bad_timestamp_rows == 0 and stream.incomplete_rows == 0
    assert stream.span[0] == P3.to_us(T0)


# ------------------------------------------------------------------------------------------------------- whole audit
def test_clean_host_has_no_problems_and_utc_clock(clean):
    report, pooled = clean.audit()
    assert report["time_domain"]["offset_unambiguous"] and report["time_domain"]["offset_applied_s"] == 0
    assert report["time_domain"]["candidate_offsets_s"] == [0] and report["time_domain"]["independent_check_agrees"] is True
    comp = report["completeness"]
    assert comp["sessions_expected"] == comp["sessions_found"] == 4
    assert comp["session_problems"] == [] and comp["stream_problems"] == []
    assert comp["records_total"] == 4 * 6 and comp["queries_without_record_window"] == 0
    assert report["integrity"]["hash_mismatches"] == [] and report["integrity"]["files_hashed"] == report["integrity"]["files_read"]
    gpu = report["coverage"]["gpu_worker_window"]
    assert gpu["sessions"] == 4 and gpu["sessions_with_a_gap_over_threshold"] == 0
    assert gpu["covered_fraction"]["min"] == 1.0
    phase = report["phase_alignment"]
    assert phase["sessions_checked"] == 4 and phase["execute_rows_outside_process_window"] == 0
    assert 0 <= phase["first_execute_row_minus_process_start_s"]["min"] and phase["last_execute_row_minus_process_end_s"]["max"] <= 0
    assert report["nvidia_smi_q"]["driver_versions"] == {"580.1.0": 1}
    assert report["nvidia_smi_q"]["product_names"] == {"NVIDIA TestGPU": 1} and report["nvidia_smi_q"]["architectures"] == {"Hopper": 1}
    assert report["nvidia_smi_q"]["draw_field_average"] == {"numeric": 1}


def test_accounting_recovers_the_injected_timeline(clean):
    _, pooled = clean.audit()
    for account in pooled["accounts"]:
        assert account["negative_overhead"] == 0 and account["overlaps"] == 0
        assert account["teardown_s"] == pytest.approx(TEARDOWN, abs=1e-3)
        assert account["launch_to_worker_s"] == pytest.approx(0.3, abs=1e-3)
        assert account["ready_closure_s"] == pytest.approx(0.0, abs=1e-3)
        # 6 records, each followed by INTER_RECORD_GAP; setup/first-record offset 0.004 is the only other residual
        assert account["unexplained_s"] == pytest.approx(0.004 + 5 * INTER_RECORD_GAP + INTER_RECORD_GAP, abs=2e-3)
        assert account["unexplained_timed_only_s"] == pytest.approx(6 * OVERHEAD + account["unexplained_s"], abs=2e-3)
    windows = pooled["windows"]
    assert len(windows) == 24
    assert all(w["overhead_s"] == pytest.approx(OVERHEAD, abs=1e-3) for w in windows)
    assert all(w["overhead_fraction"] == pytest.approx(OVERHEAD / QUERY_SECONDS, abs=1e-3) for w in windows)


def test_injected_unexplained_time_is_reported(tmp_path):
    fx = Fixture(tmp_path / "x", extra_unexplained=2.0)
    _, pooled = fx.audit()
    assert all(a["unexplained_s"] > 2.0 and a["tail_s"] == pytest.approx(2.0, abs=0.01) for a in pooled["accounts"])
    pooled_report = P3.pooled_report({"hx": pooled})
    assert pooled_report["session_accounting_all_scales"]["unexplained_s_session_total"]["min"] > 2.0


@pytest.mark.parametrize("offset", [3600, -14400, 19800])
def test_local_time_zone_is_detected_and_removed(tmp_path, offset):
    fx = Fixture(tmp_path, offset_s=offset)
    report, pooled = fx.audit()
    td = report["time_domain"]
    assert td["offset_unambiguous"] and td["offset_applied_s"] == offset
    assert report["coverage"]["gpu_process_window"]["covered_fraction"]["min"] == 1.0
    assert report["completeness"]["stream_problems"] == []
    assert all(w["gpu_samples"] >= 12 for w in pooled["windows"])   # 2.52 s at 5 Hz, only if the shift was applied
    assert abs(td["nvsmi_q_minus_env_start_s"]["min"] - offset) < 1.0   # the independent whole-second stamp shows the same zone
    assert td["independent_check_offset_s"] == offset and td["independent_check_agrees"] is True


def test_a_non_quarter_hour_offset_is_reported_as_unresolved(tmp_path):
    fx = Fixture(tmp_path, offset_s=400)
    report, _ = fx.audit()
    assert not report["time_domain"]["offset_unambiguous"] and report["time_domain"]["candidate_offsets_s"] == []


def test_gap_inside_a_session_is_flagged_and_attributed(tmp_path):
    fx = Fixture(tmp_path, gap=(6.0, 3.0))
    report, _ = fx.audit()
    stream = report["streams"][0]
    assert len(stream["gpu_gaps_over_1s"]) == 1 and stream["gpu_gaps_over_1s"][0]["gap_s"] > 2.9
    assert stream["gpu_gaps_over_1s"][0]["during_session"].endswith(("polars", "polars_gpu"))
    cov = report["coverage"]["gpu_worker_window"]
    assert cov["sessions_with_a_gap_over_threshold"] >= 1 and cov["covered_fraction"]["min"] < 1.0


def test_missing_results_and_unexpected_problems_are_reported(tmp_path):
    fx = Fixture(tmp_path, drop_results=("10", "polars_gpu"))
    report, _ = fx.audit()
    comp = report["completeness"]
    assert comp["sessions_found"] == 4 and comp["sessions_with_problems"] == 1
    assert any("results.json missing" in p for item in comp["session_problems"] for p in item["problems"])


def test_expected_gpu_query_errors_are_not_problems_but_unexpected_ones_are(tmp_path):
    expected = Fixture(tmp_path / "a", fail_query="q03", unsupported=("q03",))
    comp = expected.audit()[0]["completeness"]
    assert comp["session_problems"] == [] and comp["query_errors_total"] == 2
    assert comp["query_errors_expected_gpu_unsupported"] == 2
    unexpected = Fixture(tmp_path / "b", fail_query="q03", unsupported=("cx_q13",))
    problems = [p for item in unexpected.audit()[0]["completeness"]["session_problems"] for p in item["problems"]]
    assert any("outside expect_gpu_unsupported" in p for p in problems)


def test_truncated_gpu_stream_is_a_stream_problem(tmp_path):
    fx = Fixture(tmp_path, truncate_gpu=True)
    problems = fx.audit()[0]["completeness"]["stream_problems"]
    assert any("gpu_ends_with_newline" in p["problem"] for p in problems)


def test_mislabelled_host_phase_is_caught(tmp_path):
    fx = Fixture(tmp_path, host_label_shift=-3.0)    # execute label starts 3 s before the process does
    report, _ = fx.audit()
    assert report["phase_alignment"]["execute_rows_outside_process_window"] > 0
    assert report["completeness"]["sessions_with_problems"] >= 1


def test_hash_verification_detects_a_modified_file(tmp_path):
    fx = Fixture(tmp_path)
    target = fx.suite / "sf1" / "polars" / "results.json"
    target.write_text(target.read_text().replace('"q01"', '"q99"', 1))
    integrity = fx.audit()[0]["integrity"]
    assert integrity["hash_mismatches"] == ["campaign-hx/round-1/baseline/sf1/polars/results.json"]


def test_manifest_set_differences_are_counted(tmp_path):
    fx = Fixture(tmp_path)
    (fx.suite / "stray.txt").write_text("x")
    (fx.suite / "sf1" / "polars.log").unlink()
    integrity = fx.audit()[0]["integrity"]
    assert integrity["tree_files_not_in_manifest"] == 1 and integrity["manifest_entries_missing_on_disk"] == 1


def test_verify_none_hashes_nothing(clean):
    assert clean.audit("none")[0]["integrity"]["files_hashed"] == 0


def test_verify_spot_hashes_a_strict_subset_and_catches_a_sampled_change(tmp_path):
    final = tmp_path / "final"
    (final / "bulk").mkdir(parents=True)
    files = []
    for k in range(120):
        path = final / "bulk" / f"f{k:03d}.txt"
        path.write_text(f"payload {k}")
        files.append(path)
    manifest = {str(p.relative_to(final)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    spot = P3.integrity("hx", final, manifest, files, "spot")
    full = P3.integrity("hx", final, manifest, files, "all")
    assert 0 < spot["files_hashed"] < full["files_hashed"] == 120 and spot["hash_mismatches"] == []
    files[0].write_text("tampered")   # index 0 is always in the every-Nth sample
    assert P3.integrity("hx", final, manifest, files, "spot")["hash_mismatches"] == ["bulk/f000.txt"]


# ------------------------------------------------------------------------------------------------------------- blinding
def blind_corpus(tmp_path):
    fx = Fixture(tmp_path)
    report = fx.run()
    return report, json.dumps(report, sort_keys=True), P3.render(report)


def test_report_never_echoes_a_power_or_query_time_value(tmp_path):
    _, blob, text = blind_corpus(tmp_path)
    for needle in ("777.77", "777", "2.5,", "4096", "1410", "1593", "61.5", "123456789"):
        assert needle not in blob.replace('"2.5"', ""), needle
        assert needle not in text, needle


def test_report_keys_never_name_an_outcome(tmp_path):
    report, _, _ = blind_corpus(tmp_path)
    P3.assert_blinded(report)  # does not raise


@pytest.mark.parametrize("key", ["mean_power_w", "energy_joules", "gpu_util", "temperature", "clock_mhz", "memory_used", "cost_usd",
                                 "speedup", "engine_rank"])
def test_assert_blinded_refuses_outcome_keys(key):
    with pytest.raises(P3.BlindingError):
        P3.assert_blinded({"hosts": [{"nested": {key: 1}}]})


def test_report_has_no_per_engine_split_of_unexplained_time(tmp_path):
    report, blob, _ = blind_corpus(tmp_path)
    pooled = json.dumps(report["pooled"])
    for engine in ("polars_gpu", "spark_cpu", "spark_gpu", '"polars"'):
        assert engine not in pooled
    assert set(report["pooled"]["session_accounting_by_scale"]) == {"1", "10"}


def test_audit_is_idempotent(tmp_path):
    fx = Fixture(tmp_path)
    first = json.dumps(fx.run(), sort_keys=True)
    second = json.dumps(fx.run(), sort_keys=True)
    assert first == second
    assert P3.render(json.loads(first)) == P3.render(json.loads(second))


def test_cli_writes_json_and_text(tmp_path, capsys):
    fx = Fixture(tmp_path)
    out = tmp_path / "report.json"
    code = P3.main(["--hosts-root", str(fx.root / "hosts"), "--manifests", str(fx.manifests), "--hosts", "hx", "--json", str(out)])
    assert code == 0
    assert json.loads(out.read_text())["hosts"]["hx"]["completeness"]["sessions_found"] == 4
    printed = capsys.readouterr().out
    assert "BLINDED FEASIBILITY AUDIT" in printed and "777.77" not in printed


def test_missing_host_directory_is_reported_not_raised(tmp_path):
    report = P3.run(tmp_path / "nowhere", tmp_path / "none", ("zz",), "all")
    assert report["hosts"]["zz"]["campaign_dir_present"] is False
    assert "campaign directory" in P3.render(report)


def edit_results(fx, mutate, scale="1", engine="polars"):
    path = fx.suite / f"sf{scale}" / engine / "results.json"
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data))


def problems_of(fx):
    return [p for item in fx.audit("none")[0]["completeness"]["session_problems"] for p in item["problems"]]


def test_overlapping_record_windows_are_flagged(tmp_path):
    fx = Fixture(tmp_path)

    def overlap(data):
        first_end = datetime.fromisoformat(data["records"][0]["ended_at_utc"])
        data["records"][1]["started_at_utc"] = iso(first_end - timedelta(seconds=0.5))

    edit_results(fx, overlap)
    assert any("overlapping explained intervals" in p for p in problems_of(fx))


def test_record_window_shorter_than_timed_seconds_is_flagged(tmp_path):
    fx = Fixture(tmp_path)
    edit_results(fx, lambda data: data["records"][0].update(seconds=QUERY_SECONDS + 1.0))
    assert any("record window shorter than its timed seconds" in p for p in problems_of(fx))


def test_worker_window_outside_process_window_is_flagged(tmp_path):
    fx = Fixture(tmp_path)
    edit_results(fx, lambda data: data.update(completed_at_utc=iso(datetime.fromisoformat(data["completed_at_utc"]) + timedelta(seconds=5))))
    assert any("outside the runner's process window" in p for p in problems_of(fx))


def test_record_without_timestamps_is_flagged(tmp_path):
    fx = Fixture(tmp_path)
    edit_results(fx, lambda data: data["records"][2].pop("ended_at_utc"))
    report = fx.audit("none")[0]
    assert report["completeness"]["queries_without_record_window"] == 1
    assert any("without started_at_utc/ended_at_utc" in p for item in report["completeness"]["session_problems"] for p in item["problems"])


# ------------------------------------------------------------------------------------- per-trial timestamp presence
def test_record_stamp_presence_is_counted_by_host_suite_and_scale(tmp_path):
    fx = Fixture(tmp_path)
    pooled = P3.pooled_report({"hx": fx.audit()[1]})
    presence = pooled["record_stamp_presence"]
    assert presence["records"] == presence["with_started_ended_at_utc"] == 24
    assert presence["with_timed_start_end_utc"] == 0 and presence["with_timed_monotonic_ns"] == 0
    assert presence["by_host_suite_scale"]["hx/baseline/sf1"] == {"records": 12, "with_window": 12, "with_timed_stamps": 0, "with_monotonic": 0}
    assert set(presence["by_host_suite_scale"]) == {"hx/baseline/sf1", "hx/baseline/sf10"}


def test_newer_worker_stamps_are_detected(tmp_path):
    fx = Fixture(tmp_path, timed_stamps=True)
    presence = P3.pooled_report({"hx": fx.audit()[1]})["record_stamp_presence"]
    assert presence["with_timed_start_end_utc"] == presence["with_timed_monotonic_ns"] == 24


def test_a_record_missing_its_window_shows_up_in_the_presence_table(tmp_path):
    fx = Fixture(tmp_path)
    edit_results(fx, lambda data: data["records"][0].pop("started_at_utc"))
    presence = P3.pooled_report({"hx": fx.audit("none")[1]})["record_stamp_presence"]
    assert presence["with_started_ended_at_utc"] == 23
    assert presence["by_host_suite_scale"]["hx/baseline/sf1"]["with_window"] == 11


def test_window_width_is_reported_by_host_and_suite_not_by_engine(tmp_path):
    fx = Fixture(tmp_path)
    pooled = P3.pooled_report({"hx": fx.audit()[1]})
    assert set(pooled["window_width_by_host"]) == {"hx"} and set(pooled["window_width_by_suite"]) == {"baseline"}
    width = pooled["window_width_by_host"]["hx"]["window_minus_timed_seconds_s"]
    assert width["p50"] == pytest.approx(OVERHEAD, abs=1e-3)
    assert "polars" not in json.dumps(pooled)


def test_worker_source_hash_is_reported_per_host(clean):
    provenance = clean.audit()[0]["source_provenance"]
    assert provenance["worker.py"] == {"w" * 64: 1}
