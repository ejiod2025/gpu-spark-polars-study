#!/usr/bin/env python3
"""Synthetic publication-gate tests; fixtures are not benchmark observations."""
from __future__ import annotations

import copy
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("preview_builder", Path(__file__).with_name("build_preview.py"))
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def fixture():
    rows, included, comparisons, plans, barriers = [], [], [], [], []
    for query in builder.COMPLEX_IDS:
        row = {"scale": "sf30", "query": query, "description": "Synthetic fixture",
            "category": "Synthetic fixture", "measured_trials": 3,
            "gpu_execution_class": "hybrid", "query_cleanup_policy": "verified_gc",
            "polars_over_spark_gpu": 0.5, "spark_cpu_over_spark_gpu": 2.0}
        for engine, median in zip(builder.ENGINES, (1.0, 4.0, 2.0)):
            row.update({engine + "_min_seconds": median * 0.9,
                engine + "_median_seconds": median, engine + "_max_seconds": median * 1.1,
                engine + "_cleanup_count": 4, engine + "_cleanup_seconds": 0.04})
            for phase, trial in [("warmup", 0), ("measured", 1), ("measured", 2), ("measured", 3)]:
                slot = {"scale": "sf30", "query": query, "engine": engine, "phase": phase, "trial": trial}
                comparisons.append({**slot, "passed": True})
                barriers.append({**slot, "policy": "verified_gc", "status": "passed", "seconds": 0.01,
                    "scratch_before": {"files": 2, "bytes": 1024}, "scratch_after": {"files": 0, "bytes": 0}})
                if engine == "spark_gpu":
                    plans.append({**slot, "gpu_execution_class": "hybrid"})
        rows.append(row)
        included.append({"scale": "sf30", "query": query, "gpu_execution_class": "hybrid",
            "query_cleanup_policy": "verified_gc"})
    checks = {"suite": "complex", "suite_version": builder.COMPLEX_VERSION,
        "correctness_verified": True, "gpu_plan_coverage_verified": True,
        "verified_gc_cleanup_validated": True, "all_selected_cases_validated": True,
        "included_cases": included, "excluded_cases": [], "not_selected_cases": [],
        "comparisons": comparisons, "gpu_plans": plans, "cleanup_checks": barriers,
        "relative_tolerance": 1e-8, "absolute_tolerance": 1e-6,
        "sessions": [{"scale": "sf30", "engine": engine, "query_cleanup_policy": "verified_gc",
            "explicit_cleanup_count": 24, "explicit_cleanup_sum_seconds": 0.24,
            "process_wall_seconds": 30.0} for engine in builder.ENGINES],
        "datasets": [{"scale": "sf30", "scale_factor": 30, "lineitem_rows": 180000000,
            "total_parquet_bytes": 1000}],
        "full_suite_totals": [{"scale": "sf30", "measured_trials": 3,
            "query_cleanup_policy": "verified_gc", "polars_sum_medians_seconds": 6.0,
            "spark_cpu_sum_medians_seconds": 24.0, "spark_gpu_sum_medians_seconds": 12.0}]}
    return rows, checks


class PreviewProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report = self.root / "report"
        self.report.mkdir()
        self.output = self.root / "output"
        self.output.mkdir()
        self.rows, self.checks = fixture()

    def build(self, pending_scales=()):
        with (self.report / "summary.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self.rows[0]))
            writer.writeheader(); writer.writerows(self.rows)
        (self.report / "correctness.json").write_text(json.dumps(self.checks))
        return builder.complex_suite(self.report, [], self.output, pending_scales)

    def test_uniform_formal_costs_remain_separate(self):
        result = self.build()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["cleanup"]["cases"]), 18)
        self.assertEqual(result["totals"][0]["times"]["polars"]["sum_medians_seconds"], 6.0)
        self.assertEqual(result["cleanup"]["sessions"][0]["cleanup_seconds"], 0.24)
        self.assertIn("next query's heap state", result["protocol"]["timing_note"])
        self.assertTrue((self.output / "evidence/complex-cleanup-sessions.csv").is_file())

    def test_partial_cases_have_no_full_score_and_keep_session_cost_scope(self):
        removed = self.rows.pop()["query"]
        self.checks["included_cases"] = [x for x in self.checks["included_cases"] if x["query"] != removed]
        self.checks["excluded_cases"] = [{"scale": "sf30", "query": removed}]
        self.checks["all_selected_cases_validated"] = False
        result = self.build()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["totals"], [])
        self.assertEqual(result["cleanup"]["sessions"][0]["barrier_count"], 24)
        self.assertIn("excluded", result["cleanup"]["scope"])

    def test_planned_pending_scale_keeps_validated_progress_partial(self):
        result = self.build([300])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["pending_scales"], ["sf300"])
        self.assertIn("SF300 remains pending", result["status_text"])
        self.assertEqual(len(result["totals"]), 1)
        with self.assertRaisesRegex(ValueError, "already has supplied formal cases"):
            self.build([30])

    def test_per_scale_chart_fallback_is_scoped_and_linked(self):
        (self.report / "complex-sf30.png").write_bytes(b"synthetic chart fixture")
        result = self.build([300])
        self.assertEqual(result["chart"]["url"], "assets/complex-sf30.png")
        self.assertIn("SF30 only", result["chart"]["scope"])
        self.assertTrue(any(item["url"] == "assets/complex-sf30.png" for item in result["evidence"]))

    def test_legacy_or_mixed_formal_protocol_is_rejected(self):
        for policies in (("none",) * 6, ("none",) + ("verified_gc",) * 5):
            with self.subTest(policies=policies):
                self.rows, self.checks = fixture()
                for row, included, policy in zip(self.rows, self.checks["included_cases"], policies):
                    row["query_cleanup_policy"] = included["query_cleanup_policy"] = policy
                with self.assertRaisesRegex(ValueError, "uniform verified_gc"):
                    self.build()

    def test_missing_duplicate_or_failed_barriers_are_rejected(self):
        for kind in ("missing", "duplicate", "failed", "leftover", "invalid_count", "negative_duration"):
            with self.subTest(kind=kind):
                self.rows, self.checks = fixture()
                barriers = self.checks["cleanup_checks"]
                if kind == "missing":
                    barriers.pop()
                elif kind == "duplicate":
                    barriers.append(copy.deepcopy(barriers[-1]))
                elif kind == "failed":
                    barriers[-1]["status"] = "failed"
                elif kind == "leftover":
                    barriers[-1]["scratch_after"]["files"] = 1
                elif kind == "invalid_count":
                    barriers[-1]["scratch_after"]["files"] = False
                else:
                    barriers[-1]["seconds"] = -1
                with self.assertRaises(ValueError):
                    self.build()

    def test_summary_and_session_cost_tampering_is_rejected(self):
        for kind in ("case_sum", "case_count", "session_policy", "session_count", "session_sum", "session_wall", "session_failure", "validation_flag"):
            with self.subTest(kind=kind):
                self.rows, self.checks = fixture()
                if kind == "case_sum":
                    self.rows[0]["polars_cleanup_seconds"] = 100
                elif kind == "case_count":
                    self.rows[0]["polars_cleanup_count"] = 3
                elif kind == "validation_flag":
                    self.checks["verified_gc_cleanup_validated"] = False
                else:
                    field, value = {"session_policy": ("query_cleanup_policy", "none"),
                        "session_count": ("explicit_cleanup_count", 1), "session_sum": ("explicit_cleanup_sum_seconds", 0.0),
                        "session_wall": ("process_wall_seconds", 0.1), "session_failure": ("cleanup_failure", {"status": "failed"})}[kind]
                    self.checks["sessions"][0][field] = value
                with self.assertRaises(ValueError):
                    self.build()

    def test_incident_summary_contains_no_performance_trials(self):
        index = {"evidence_kind": "nonperformance_incident_index", "incidents": [{
            "run": "complex-formal-20261025", "evidence_kind": "nonperformance_resource_or_incomplete_run_incident",
            "eligible_for_complete_performance_summary": False, "observations": [{
                "kind": "recorded_disk_space_failure", "engine": "spark_gpu", "scale": "sf300",
                "query_error_count": 6, "saved_result_records": 13, "query_errors": ["private raw stack"]}]}]}
        result = builder.incident_summary(index, "a" * 64, self.output)
        self.assertEqual(result["status"], "documented")
        payload = (self.output / "evidence/prior-attempt-incident.json").read_text()
        self.assertNotIn("private raw stack", payload)
        self.assertIn("not establish a GPU win", payload)
        index["incidents"][0]["eligible_for_complete_performance_summary"] = True
        with self.assertRaises(ValueError):
            builder.incident_summary(index, "a" * 64, self.output)

    def test_correlated_input_is_not_labeled_observed_anti_join(self):
        _, queries = builder.query_definitions()
        query = next(item for item in queries if item["id"] == "cx_semi_anti")
        self.assertEqual(query["description"], "Correlated EXISTS/NOT EXISTS input design")
        self.assertIsNone(query["join_count"])
        self.assertIn("NOT EXISTS", query["sql"].upper())
        self.assertIn("not evidence of observed GPU anti-join", query["plan_note"])


if __name__ == "__main__":
    unittest.main()
