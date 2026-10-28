"""Release gates: fabricated/partial artifacts must never create a public page."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1] / "build.py"
SPEC = importlib.util.spec_from_file_location("h100_web_builder", FILE)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class PublicationGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def replay_manifest(self, **changes):
        replay = self.root / "replay"
        replay.mkdir(exist_ok=True)
        value = {"schema_version": "research-artifact-replay-v1", "status": "PASS", "phase": "complete",
                 "synthetic": False, "campaign_id": builder.CAMPAIGN_ID}
        value.update(changes)
        (replay / "REPLAY_MANIFEST.json").write_text(json.dumps(value))
        return replay

    def test_partial_replay_never_creates_output(self):
        replay = self.replay_manifest(status="RUNNING", phase="frozen-analysis")
        output = self.root / "public"
        with self.assertRaisesRegex(ValueError, "complete replay PASS"):
            builder.build(replay, self.root / "absent.pdf", self.root / "absent.json", output)
        self.assertFalse(output.exists())

    def test_forged_pass_still_needs_frozen_identity(self):
        replay = self.replay_manifest()
        with self.assertRaisesRegex(ValueError, "52-file C84"):
            builder.validate_replay(replay)

    def test_synthetic_and_previous_cohort_are_rejected(self):
        for changes in ({"synthetic": True}, {"campaign_id": "SYNTHETIC-TEST"},
                        {"campaign_id": "h100-confirmatory-v1"},
                        {"campaign_id": "h100-confirmatory-replacement-20261027"}, {"synthetic": None}):
            replay = self.replay_manifest(**changes)
            with self.assertRaisesRegex(ValueError, "genuine recovery"):
                builder.validate_replay(replay)

    def test_changed_source_map_is_rejected_before_registry_lookup(self):
        replay = self.replay_manifest(source_commit=builder.SOURCE_COMMIT, source_tree_sha256=builder.SOURCE_TREE,
                                      source_files_sha256={str(number): "0" * 64 for number in range(52)})
        with self.assertRaisesRegex(ValueError, "source map checksum"):
            builder.validate_replay(replay)

    def test_missing_final_pdf_never_creates_output(self):
        output = self.root / "public"
        with patch.object(builder, "validate_replay", return_value=({"campaign_sha256": "0" * 64},
                         {"execution_profile": {"sha256": "a" * 64}}, {}, None)), \
             patch.object(builder, "validate_contention_replay", return_value=({}, {"status": "COMPLETE"})):
            replay = self.replay_manifest()
            with self.assertRaisesRegex(ValueError, "final paper PDF"):
                builder.build(replay, self.root / "absent.pdf", self.root / "absent.json", output, self.root / "contention")
        self.assertFalse(output.exists())

    def test_draft_paper_is_rejected(self):
        paper = self.root / "draft.pdf"
        paper.write_bytes(b"%PDF-1.7\nDRAFT TEST ONLY")
        manifest = self.root / "BUILD_MANIFEST.json"
        manifest.write_text(json.dumps({"schema_version": "h100-paper-build-v1", "status": "PASS_DRAFT", "final": False}))
        with self.assertRaisesRegex(ValueError, "Final paper manifest"):
            builder.validate_paper(paper, manifest, "0" * 64, "1" * 64)

    def test_registered_aborted_v1_hash_cannot_be_relabelled_as_recovery(self):
        source = json.loads((builder.ROOT / "evidence-research/20261026/h100-confirmatory-v1/campaign.json").read_text())["source"]
        replay = self.replay_manifest(source_commit=builder.SOURCE_COMMIT, source_tree_sha256=builder.SOURCE_TREE,
                                      source_files_sha256=source["files_sha256"],
                                      campaign_sha256="0dabe0d40c5adda0a087508e3f95ceb0636815f5133a3b401071612e3f24c145",
                                      prestart_sha256="a758ceea7d16a6c726221db5db2cb99502ac4f640131341b456e9b63ed459b2a")
        with self.assertRaisesRegex(ValueError, "registered recovery campaign/profile"):
            builder.validate_replay(replay)

    def test_profile_proof_requires_exact_registered_hash_and_all_driver_gates(self):
        identity = {"execution_profile_sha256": "a" * 64}
        proof = {"path": "EXECUTION_PROFILE.json", "sha256": "a" * 64,
                 "spark_driver_logs": 160, "profile_gates": 24, "status": "PASS"}
        builder.validate_recovery_profile({"execution_profile": proof}, identity)
        for change in ({"sha256": "b" * 64}, {"spark_driver_logs": 159}, {"profile_gates": 23},
                       {"status": "FAIL"}, {"path": "other.json"}):
            with self.assertRaisesRegex(ValueError, "160 Spark drivers and 24 profile gates"):
                builder.validate_recovery_profile({"execution_profile": {**proof, **change}}, identity)
        with self.assertRaises(ValueError):
            builder.validate_recovery_profile({}, identity)

    def test_preview_has_no_result_payload_or_release_manifest(self):
        output = self.root / "preview"
        result = builder.preview(output)
        self.assertEqual(result["status"], "UNPUBLISHED")
        self.assertFalse(result["final"])
        self.assertFalse((output / "data.json").exists())
        self.assertFalse((output / "PUBLICATION_MANIFEST.json").exists())
        self.assertIn("window.H100_PREVIEW = true", (output / "index.html").read_text())
        self.assertNotIn("__PREVIEW_MODE__", (output / "index.html").read_text())
        self.assertIn('content="noindex,nofollow"', (output / "index.html").read_text())
        self.assertIn('id="contention-pending"', (output / "index.html").read_text())
        self.assertIn("four common measured query pairs", (output / "index.html").read_text())
        self.assertFalse((output / "downloads").exists())

    def test_contention_argument_is_mandatory_before_any_output(self):
        output = self.root / "public"
        with patch.object(builder, "validate_replay", return_value=({}, {}, {}, None)):
            replay = self.replay_manifest()
            with self.assertRaisesRegex(ValueError, "contention-replay.*mandatory"):
                builder.build(replay, self.root / "absent.pdf", self.root / "absent.json", output)
        self.assertFalse(output.exists())

    def test_real_current_partial_contention_cannot_release(self):
        # Read-only genuine boundary: full waves are insufficient while original
        # worker payload and action separation remain pending. Never relabel it.
        actual = builder.ROOT / "rerun/contention-20261028/offline-full-audit-v3"
        if not actual.exists():
            self.skipTest("Private audit is not present in this checkout")
        report = builder.read_json(actual / "CONTENTION_REPLAY.json")
        if report["status"] != "PARTIAL":
            self.skipTest("This historical path is no longer a partial boundary")
        with self.assertRaisesRegex(ValueError, "terminal publication-ready"):
            builder.validate_contention_replay(actual)

    def test_forged_terminal_labels_fail_fresh_reproduction(self):
        actual = builder.ROOT / "rerun/contention-20261028/offline-full-audit-v3"
        if not actual.exists():
            self.skipTest("Private audit is not present in this checkout")
        report = builder.read_json(actual / "CONTENTION_REPLAY.json")
        if report.get("audit_source_sha256") != builder.sha(builder.REPORTING / "contention_replay.py"):
            self.skipTest("Historical auditor source has changed")
        # Deliberately forged receipt, isolated under the test's temporary tree.
        # Keep actual raw evidence and every genuine report byte untouched.
        forged = self.root / "forged-contention"
        forged.mkdir()
        for name in report["output_files_sha256"]:
            shutil.copyfile(actual / name, forged / name)
        report.update(status="COMPLETE", publication_ready=True)
        report["handoff"].update(status="PASS", original_payload_verified=True, no_action_overlap=True)
        (forged / "CONTENTION_REPLAY.json").write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "freshly reproduced"):
            builder.validate_contention_replay(forged)
        self.assertFalse((self.root / "public").exists())

    def contention_boundary(self):
        # Deliberately fabricated lifecycle boundary only. This object cannot
        # satisfy the source/canonical/read-input/fresh-reproduction release gate.
        return {"schema_version": "h100-contention-replay-v1", "synthetic": False,
                "experiment_id": builder.CONTENTION_ID, "plan_sha256": builder.CONTENTION_PLAN,
                "prestart_sha256": builder.CONTENTION_PRESTART, "reference_campaign_sha256": builder.CONTENTION_REFERENCE,
                "reference_execution_profile_sha256": builder.CONTENTION_PROFILE, "status": "COMPLETE",
                "integrity_status": "PASS", "publication_ready": True, "findings": [],
                "handoff": {"status": "PASS", "same_scheduler_resumed": True,
                            "original_payload_verified": True, "no_action_overlap": True},
                "terminal_driver": {"terminal": True}}

    def test_contention_lifecycle_identity_and_separation_rejections(self):
        report = self.contention_boundary()
        for change in ({"synthetic": True}, {"plan_sha256": "0" * 64}, {"prestart_sha256": "0" * 64},
                       {"status": "PARTIAL"}, {"status": "FAIL"}, {"publication_ready": False},
                       {"integrity_status": "FAIL"}, {"findings": [{"message": "fixture failure"}]}):
            with self.assertRaises(ValueError):
                builder.contention_disposition_gate({**report, **change})
        for key in ("same_scheduler_resumed", "original_payload_verified", "no_action_overlap"):
            with self.assertRaisesRegex(ValueError, "actual original payload"):
                builder.contention_disposition_gate({**report, "handoff": {**report["handoff"], key: False}})
        with self.assertRaisesRegex(ValueError, "actual original payload"):
            builder.contention_disposition_gate({**report, "terminal_driver": {"terminal": False}})

    def test_draft_contention_figures_cannot_release(self):
        figures = self.root / "figures"
        figures.mkdir()
        (figures / "FIGURE_MANIFEST.json").write_text(json.dumps({"schema_version": "h100-contention-figures-v1",
                                                               "draft": True, "final": False, "publication_ready": False}))
        with self.assertRaisesRegex(ValueError, "final publication-ready"):
            builder.validate_contention_figures(figures, {"report": self.contention_boundary()}, {"disposition": "COMPLETE"})

    def test_pilot_stop_never_requires_unattempted_expansion_figures(self):
        pilot = builder.contention_figure_stems({"waves": [{"wave_id": "pilot-01-fixture"}]})
        self.assertEqual(len(pilot), 3)
        self.assertTrue(all(stem.startswith("pilot_") for stem in pilot))
        self.assertEqual(builder.contention_figure_stems({"waves": []}), ())
        self.assertEqual(len(builder.contention_figure_stems({"waves": [{"wave_id": "pilot-fixture"},
                                                                      {"wave_id": "main-fixture"}]})), 6)

    def test_typed_contention_public_export_keeps_individual_repetitions(self):
        wave = {"wave_id": "main-01-spark_cpu__polars_gpu-complex-sf30-r1", "pair": "spark_cpu__polars_gpu",
                "participants": ["spark_cpu", "polars_gpu"], "suite": "complex", "scale": "30", "repetition": 1}
        action = {**{key: str(wave[key]) for key in ("wave_id", "pair", "suite", "scale", "repetition")},
                  "config_id": "spark_cpu", "query": "cx_q13", "phase": "measured", "trial": "1",
                  "seconds": "2.5", "host_result_seconds": "2.6", "paired_condition": "False", "oracle_passed": "True",
                  "action_started_at_utc": "2026-10-28T00:00:00+00:00", "action_ended_at_utc": "2026-10-28T00:00:02.500000+00:00",
                  "host_result_ended_at_utc": "2026-10-28T00:00:02.600000+00:00", "private_host_path": "/home/fixture/private"}
        report = {"counts": {key: 0 for key in ("attempted_waves", "successful_outputs", "oracle_failed_outputs",
                    "expected_strict_rejections_observed", "unexpected_query_errors_observed", "common_measured_pairs", "unpaired_cpu_measured_diagnostics")},
                  "disposition": {phase: {"status": "PASS", "unattempted_waves": [], "planned_waves": 0,
                    "attempted_waves": 0, "passed_waves": 0, "failed_waves": 0} for phase in ("pilot", "main")}}
        data = {"report": report, "plan": {"pilot": [], "main": [wave]}, "actions": [action], "overlaps": []}
        result = builder.public_contention(data, {"disposition": "COMPLETE"})
        self.assertEqual(result["actions"][0]["seconds"], 2.5)
        self.assertFalse(result["actions"][0]["paired_condition"])
        self.assertEqual(result["actions"][0]["repetition"], 1)
        self.assertNotIn("private_host_path", result["actions"][0])
        builder.scan_public_text(json.dumps(result), "typed contention fixture")
        for key, value in (("seconds", "nan"), ("paired_condition", "yes"), ("query", "/home/private"),
                           ("config_id", "https://private.example"), ("repetition", "2")):
            with self.assertRaises(ValueError):
                builder.public_contention({**data, "actions": [{**action, key: value}]}, {})

    def test_paper_requires_exact_terminal_contention_review(self):
        paper = self.root / "fixture.pdf"
        paper.write_bytes(b"%PDF-1.7\nFABRICATED GATE INPUT ONLY")
        source = builder.ROOT / "publication/h100-confirmatory"
        metadata = {"schema_version": "h100-paper-build-v1", "status": "PASS", "final": True, "synthetic": False,
                    "campaign_id": builder.CAMPAIGN_ID, "author": "Daniel Ejiofor", "paper_sha256": builder.sha(paper),
                    "campaign_sha256": "a" * 64, "execution_profile_sha256": "b" * 64,
                    "replay_manifest_sha256": "c" * 64, "public_sanitization": {"status": "PASS"},
                    "builder_sha256": builder.sha(source / "paper/build_paper.py"),
                    "manuscript_sha256": builder.sha(source / "MANUSCRIPT.md"),
                    "references_sha256": builder.sha(source / "references.md"),
                    "output_files_sha256": {paper.name: builder.sha(paper)}}
        review = {"status": "COMPLETE", "experiment_id": builder.CONTENTION_ID, "plan_sha256": builder.CONTENTION_PLAN,
                  "prestart_sha256": builder.CONTENTION_PRESTART, "replay_sha256": "d" * 64, "disposition": "COMPLETE"}
        for value in (None, {**review, "replay_sha256": "e" * 64}, {**review, "disposition": "STOPPED_WITH_FAILURE"}):
            (self.root / "paper.json").write_text(json.dumps({**metadata, "contention_review": value}))
            with self.assertRaisesRegex(ValueError, "exact terminal contention_review"):
                builder.validate_paper(paper, self.root / "paper.json", "c" * 64, "a" * 64, "b" * 64, review)

    def test_preview_cannot_enter_site_checkout(self):
        with self.assertRaisesRegex(ValueError, "Site checkout"):
            builder.preview(builder.ROOT / ".sites-checkout-h100-publication" / "dist" / "h100")

    def test_existing_output_is_never_overwritten(self):
        output = self.root / "public"
        output.mkdir()
        (output / "sentinel.txt").write_text("existing study")
        with self.assertRaisesRegex(ValueError, "overwrite"):
            builder.build(self.root / "missing", self.root / "missing", self.root / "missing", output)
        self.assertEqual((output / "sentinel.txt").read_text(), "existing study")

    def test_unsafe_paths_symlinks_and_changed_checksums_are_rejected(self):
        (self.root / "file.txt").write_text("evidence")
        (self.root / "linked.txt").symlink_to(self.root / "file.txt")
        for name in ("../escape", "/absolute", "linked.txt", "missing.txt"):
            with self.assertRaises(ValueError):
                builder.safe_file(self.root, name)
        with self.assertRaisesRegex(ValueError, "checksum differs"):
            builder.check_inventory(self.root, {"file.txt": "0" * 64})

    def test_ambiguous_or_nonfinite_json_is_rejected(self):
        path = self.root / "input.json"
        for text in ('{"status":"PASS","status":"FAIL"}', '{"ratio":NaN}', '{"ratio":Infinity}'):
            path.write_text(text)
            with self.assertRaises(ValueError):
                builder.read_json(path)

    def test_private_text_rejected_from_allowlisted_exports(self):
        for text in ("203.0.113.2", "/home/research/.env", "-----BEGIN PRIVATE KEY-----", "sk-proj-" + "x" * 30,
                     "api_key=secretvalue123", "ssh-ed25519 AAAA"):
            with self.assertRaisesRegex(ValueError, "Private/credential"):
                builder.scan_public_text(text, "test")
        builder.scan_public_text("Polars 1.44.2, cuDF 26.10.2, ten blocks, pointwise 95%", "safe")
        builder.scan_public_text("The recovery retains Java 17.0.20.1.", "explicit software version")
        with self.assertRaises(ValueError):
            builder.scan_public_text("Address 17.0.20.1", "bare address")


if __name__ == "__main__":
    unittest.main()
