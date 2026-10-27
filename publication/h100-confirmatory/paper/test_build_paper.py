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
                "campaign_sha256": "a" * 64, "campaign_id": "replacement-SYNTHETIC-TEST"}
        cases = [{**base, "status": "RUNNING"}, {**base, "synthetic": True},
                 {**base, "campaign_sha256": builder.OLD_CAMPAIGN},
                 {**base, "source_commit": "drifted"}, {**base, "output_files_sha256": {}}]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for payload in cases:
                (root / "REPLAY_MANIFEST.json").write_text(json.dumps(payload))
                with self.assertRaises(ValueError):
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
