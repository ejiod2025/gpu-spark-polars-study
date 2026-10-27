"""Release gates: fabricated/partial artifacts must never create a public page."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
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
                         {"execution_profile": {"sha256": "a" * 64}}, {}, None)):
            replay = self.replay_manifest()
            with self.assertRaisesRegex(ValueError, "final paper PDF"):
                builder.build(replay, self.root / "absent.pdf", self.root / "absent.json", output)
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


if __name__ == "__main__":
    unittest.main()
