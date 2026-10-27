"""Negative evidence gates and non-scientific figure-layout tests."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).with_name("build_paper.py")
SPEC = importlib.util.spec_from_file_location("h100_paper_builder", MODULE)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class PaperGates(unittest.TestCase):
    def test_incomplete_synthetic_and_lost_cohorts_cannot_make_final_paper(self):
        base = {"schema_version": "research-artifact-replay-v1", "status": "PASS", "phase": "complete",
                "synthetic": False, "source_commit": builder.C84,
                "campaign_sha256": "a" * 64, "campaign_id": builder.CAMPAIGN_ID}
        cases = [{**base, "status": "RUNNING"}, {**base, "synthetic": True},
                 {**base, "campaign_sha256": builder.OLD_CAMPAIGN},
                 {**base, "source_commit": "drifted"},
                 {**base, "campaign_id": "h100-confirmatory-replacement-20261027"},
                 {**base, "campaign_id": "arbitrary-replacement-cohort"}, {**base, "output_files_sha256": {}}]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for payload in cases:
                (root / "REPLAY_MANIFEST.json").write_text(json.dumps(payload))
                with self.assertRaises(ValueError):
                    builder.verify_replay(root)

    def test_registered_aborted_v1_hash_cannot_be_relabelled_as_recovery(self):
        archive = builder.ROOT / "evidence-research/20261026/h100-confirmatory-v1/campaign.json"
        source = json.loads(archive.read_text())["source"]
        payload = {"schema_version": "research-artifact-replay-v1", "status": "PASS", "phase": "complete",
                   "synthetic": False, "source_commit": builder.C84, "source_tree_sha256": builder.SOURCE_TREE,
                   "source_files_sha256": source["files_sha256"], "campaign_id": builder.CAMPAIGN_ID,
                   "campaign_sha256": "0dabe0d40c5adda0a087508e3f95ceb0636815f5133a3b401071612e3f24c145",
                   "prestart_sha256": "a758ceea7d16a6c726221db5db2cb99502ac4f640131341b456e9b63ed459b2a"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "REPLAY_MANIFEST.json").write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "registered recovery campaign/profile"):
                builder.verify_replay(root)

    def test_profile_proof_requires_exact_registered_hash_and_all_driver_gates(self):
        identity = {"execution_profile_sha256": "a" * 64}
        proof = {"path": "EXECUTION_PROFILE.json", "sha256": "a" * 64,
                 "spark_driver_logs": 160, "profile_gates": 24, "status": "PASS"}
        builder.verify_recovery_profile({"execution_profile": proof}, identity)
        for change in ({"sha256": "b" * 64}, {"spark_driver_logs": 159}, {"profile_gates": 23},
                       {"status": "FAIL"}, {"path": "other.json"}):
            with self.assertRaisesRegex(ValueError, "160 Spark drivers and 24 profile gates"):
                builder.verify_recovery_profile({"execution_profile": {**proof, **change}}, identity)
        with self.assertRaises(ValueError):
            builder.verify_recovery_profile({}, identity)

    def test_missing_or_modified_source_map_fails_before_registry_lookup(self):
        payload = {"schema_version": "research-artifact-replay-v1", "status": "PASS", "phase": "complete",
                   "synthetic": False, "source_commit": builder.C84, "campaign_id": builder.CAMPAIGN_ID,
                   "campaign_sha256": "a" * 64, "source_tree_sha256": builder.SOURCE_TREE,
                   "source_files_sha256": {str(number): "0" * 64 for number in range(52)}}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "REPLAY_MANIFEST.json").write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "52-file C84"):
                builder.verify_replay(root)

    def test_completed_review_cannot_clear_pending_source(self):
        replay = {"campaign_sha256": "a" * 64}
        payload = {"status": "complete", "author": builder.AUTHOR,
                   "campaign_sha256": replay["campaign_sha256"],
                   "manuscript_sha256": builder.sha256(builder.SOURCE),
                   "references_sha256": builder.sha256(builder.REFERENCES),
                   **dict.fromkeys(builder.REVIEW_FIELDS, True)}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "SYNTHETIC-review.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "pending/draft"):
                builder.verify_review(path, replay, "## Results — pending\n")

    def test_sanitization_preserves_public_bibliography_and_detects_private_patterns(self):
        self.assertEqual(builder.sanitize("NIST section 5.3.3.2; Polars 1.44.2")["status"], "PASS")
        for text in ("192.0.2.1", "/Users/example/private", "OPENAI_API_KEY=example",
                     "-----BEGIN PRIVATE KEY-----", "sk-" + "x" * 25):
            self.assertEqual(builder.sanitize(text)["status"], "FAIL")

    def test_verified_layout_image_gets_landscape_page_and_returns_to_portrait(self):
        from PIL import Image, ImageDraw
        from pypdf import PdfReader
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replay, output = root / "SYNTHETIC-replay", root / "SYNTHETIC-output"
            (replay / "figures").mkdir(parents=True)
            output.mkdir()
            image = Image.new("RGB", (1000, 620), "white")
            ImageDraw.Draw(image).text((20, 20), "SYNTHETIC LAYOUT TEST ONLY", fill="black")
            path = replay / "figures/layout.png"
            image.save(path)
            ast = {"blocks": [
                {"t": "Para", "c": [{"t": "Str", "c": "Before figure."}]},
                {"t": "Para", "c": [{"t": "Image", "c": [["", [], []],
                    [{"t": "Str", "c": "SYNTHETIC layout test; no performance data."}],
                    ["replay/figures/layout.png", ""]]}]},
                {"t": "Para", "c": [{"t": "Str", "c": "After figure."}]}]}
            manifest = {"output_files_sha256": {"figures/layout.png": builder.sha256(path)}}
            builder.render_pdf(ast, output / "SYNTHETIC-layout.pdf", "SYNTHETIC LAYOUT TEST ONLY",
                               False, output, replay, manifest)
            pages = PdfReader(output / "SYNTHETIC-layout.pdf").pages
            self.assertEqual(len(pages), 3)
            self.assertLess(pages[0].mediabox.width, pages[0].mediabox.height)
            self.assertGreater(pages[1].mediabox.width, pages[1].mediabox.height)
            self.assertLess(pages[2].mediabox.width, pages[2].mediabox.height)
            self.assertIn("After figure.", pages[2].extract_text())
            self.assertTrue(all("DRAFT" in page.extract_text() for page in pages))


if __name__ == "__main__":
    unittest.main()
