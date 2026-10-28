"""Negative evidence gates and non-scientific figure-layout tests."""
import importlib.util
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

MODULE = Path(__file__).with_name("build_paper.py")
SPEC = importlib.util.spec_from_file_location("h100_paper_builder", MODULE)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def lifecycle_only(status="COMPLETE"):
    """TEST ONLY: lifecycle metadata without observations or performance results."""
    return {"schema_version": "h100-contention-replay-v1", "synthetic": False,
            "scope": "TEST ONLY lifecycle metadata; no scientific observations",
            "experiment_id": builder.CONTENTION_ID, "plan_sha256": builder.CONTENTION_PLAN,
            "prestart_sha256": builder.CONTENTION_PRESTART,
            "reference_campaign_sha256": builder.CONTENTION_REFERENCE,
            "reference_execution_profile_sha256": builder.CONTENTION_PROFILE,
            "status": status, "integrity_status": "PASS", "publication_ready": status != "PARTIAL", "findings": [],
            "handoff": {"status": "PASS", "same_scheduler_resumed": True,
                        "original_payload_verified": True, "no_action_overlap": True},
            "terminal_driver": {"terminal": True}}


def mocked_verification(receipt, report):
    """TEST ONLY subprocess response; never written as a scientific output."""
    return {"schema_version": "h100-paper-contention-verification-v1", "final": True,
            "contention_review": builder.contention_review_object(report, builder.sha256(receipt)),
            "loader_sha256": builder.sha256(builder.REPORTING / "contention_figures.py"),
            "auditor_sha256": builder.sha256(builder.REPORTING / "contention_replay.py"),
            "input_files_sha256": {str(receipt.resolve()): builder.sha256(receipt)},
            "figures": None, "protected_roots": [str(receipt.parent.resolve())]}


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
        contention = builder.contention_review_object(lifecycle_only(), "b" * 64)
        payload = {"status": "complete", "author": builder.AUTHOR,
                   "campaign_sha256": replay["campaign_sha256"],
                   "manuscript_sha256": builder.sha256(builder.SOURCE),
                   "references_sha256": builder.sha256(builder.REFERENCES),
                   "contention_review": contention,
                   **dict.fromkeys(builder.REVIEW_FIELDS, True)}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "SYNTHETIC-review.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "pending/draft"):
                builder.verify_review(path, replay, "## Results — pending\n", contention)

    def test_final_requires_contention_and_selected_analysis_runtime_before_any_render(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(builder, "verify_replay") as isolated:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "requires --contention-replay"):
                builder.build(root / "never-created", root / "not-read", root / "not-read-review")
            with self.assertRaisesRegex(ValueError, "explicit --analysis-python"):
                builder.build(root / "never-created", root / "not-read", root / "not-read-review",
                              contention_root=root / "not-read-contention")
            isolated.assert_not_called()
            self.assertFalse((root / "never-created").exists())

    def test_contention_terminal_markers_cannot_clear_partial_or_missing_separation(self):
        for status in ("COMPLETE", "STOPPED_WITH_FAILURE"):
            report = lifecycle_only(status)
            expected = {"status": "COMPLETE", "experiment_id": builder.CONTENTION_ID,
                        "plan_sha256": builder.CONTENTION_PLAN, "prestart_sha256": builder.CONTENTION_PRESTART,
                        "replay_sha256": "a" * 64, "disposition": status}
            self.assertEqual(builder.contention_review_object(report, "a" * 64), expected)
            for field in ("same_scheduler_resumed", "original_payload_verified", "no_action_overlap"):
                changed = copy.deepcopy(report)
                changed["handoff"][field] = False
                with self.assertRaisesRegex(ValueError, "original payload, separation"):
                    builder.contention_disposition_gate(changed)
            with self.assertRaisesRegex(ValueError, "terminal publication-ready"):
                builder.contention_disposition_gate(report | {"publication_ready": False})
        with self.assertRaisesRegex(ValueError, "PARTIAL is not final"):
            builder.contention_disposition_gate(lifecycle_only("PARTIAL"))
        with self.assertRaisesRegex(ValueError, "exact genuine"):
            builder.contention_disposition_gate(lifecycle_only() | {"plan_sha256": "f" * 64})

    def test_partial_receipt_never_launches_scientific_replay_or_writes_output(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(builder.subprocess, "run") as command:
            root = Path(temporary)
            (root / "CONTENTION_REPLAY.json").write_text(json.dumps(lifecycle_only("PARTIAL")))
            with self.assertRaisesRegex(ValueError, "PARTIAL is not final"):
                builder.verify_contention_replay(root, Path(sys.executable))
            command.assert_not_called()
            self.assertEqual({p.name for p in root.iterdir()}, {"CONTENTION_REPLAY.json"})

    def test_selected_analysis_python_is_not_taken_from_a_receipt_and_venv_path_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = lifecycle_only() | {"analysis_runtime": {"interpreter": "/UNTRUSTED/receipt-interpreter"}}
            receipt = root / "CONTENTION_REPLAY.json"
            receipt.write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, "explicit --analysis-python"):
                builder.verify_contention_replay(root, None)
            invocation = root / "analysis-env/bin/python"
            invocation.parent.mkdir(parents=True)
            invocation.symlink_to(sys.executable)
            response = mocked_verification(receipt, report)
            with mock.patch.object(builder.subprocess, "run", return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps(response), stderr="")) as command:
                actual = builder.verify_contention_replay(root, invocation)
            self.assertEqual(actual["contention_review"], response["contention_review"])
            argv = command.call_args.args[0]
            self.assertEqual(argv[0], str(invocation.absolute()))
            self.assertEqual(argv[1:4], ["-B", "-I", "-c"])
            self.assertIn("scientific_contention_check", argv[4])
            self.assertNotIn("/UNTRUSTED/receipt-interpreter", argv)

    def test_terminal_marker_still_requires_a_successful_fresh_current_source_response(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = lifecycle_only()
            receipt = root / "CONTENTION_REPLAY.json"
            receipt.write_text(json.dumps(report))
            with mock.patch.object(builder.subprocess, "run", return_value=SimpleNamespace(
                    returncode=1, stdout="", stderr="TEST ONLY: receipt differs from freshly reproduced audit")):
                with self.assertRaisesRegex(ValueError, "Fresh contention verification failed"):
                    builder.verify_contention_replay(root, Path(sys.executable))
            response = mocked_verification(receipt, report)
            for change in ({"final": False}, {"loader_sha256": "0" * 64},
                           {"contention_review": {**response["contention_review"], "replay_sha256": "0" * 64}}):
                with mock.patch.object(builder.subprocess, "run", return_value=SimpleNamespace(
                        returncode=0, stdout=json.dumps(response | change), stderr="")):
                    with self.assertRaisesRegex(ValueError, "exact terminal receipt/current verifier"):
                        builder.verify_contention_replay(root, Path(sys.executable))

    def test_editorial_contention_review_is_explicit_and_bound_to_exact_terminal_receipt(self):
        replay = {"campaign_sha256": "a" * 64}
        contention = builder.contention_review_object(lifecycle_only(), "b" * 64)
        payload = {"status": "complete", "author": builder.AUTHOR, "campaign_sha256": replay["campaign_sha256"],
                   "manuscript_sha256": builder.sha256(builder.SOURCE), "references_sha256": builder.sha256(builder.REFERENCES),
                   "contention_review": contention, **dict.fromkeys(builder.REVIEW_FIELDS, True)}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "TEST-ONLY-review.json"
            path.write_text(json.dumps(payload))
            builder.verify_review(path, replay, "TEST ONLY: editorial gate without paper generation.", contention)
            for change in ({"contention_results_and_limits": False},
                           {"contention_review": {**contention, "replay_sha256": "0" * 64}}):
                path.write_text(json.dumps(payload | change))
                with self.assertRaisesRegex(ValueError, "all required source/result checks"):
                    builder.verify_review(path, replay, "TEST ONLY lifecycle gate.", contention)

    def test_common_contention_gate_imports_without_matplotlib_in_a_clean_python(self):
        program = ("import sys; sys.path.insert(0, sys.argv[1]); "
                   "from research_reporting import contention_figures; "
                   "assert not any(k == 'matplotlib' or k.startswith('matplotlib.') for k in sys.modules); "
                   "print('PASS: common contention gate has no eager Matplotlib import')")
        result = subprocess.run([sys.executable, "-B", "-I", "-c", program, str(builder.ROOT)],
                                capture_output=True, text=True, check=True)
        self.assertIn("no eager Matplotlib", result.stdout)

    def test_actual_partial_contention_receipt_is_rejected_without_heavy_replay(self):
        directory = builder.ROOT / "rerun/contention-20261028/offline-full-audit-v3"
        if not (directory / "CONTENTION_REPLAY.json").is_file():
            self.skipTest("Private real PARTIAL audit is unavailable")
        with mock.patch.object(builder.subprocess, "run") as command:
            with self.assertRaisesRegex(ValueError, "PARTIAL is not final"):
                builder.verify_contention_replay(directory, Path(sys.executable))
            command.assert_not_called()

    def test_real_isolated_analysis_entrypoint_refuses_partial_before_heavy_replay(self):
        directory = builder.ROOT / "rerun/contention-20261028/offline-full-audit-v3"
        if not (directory / "CONTENTION_REPLAY.json").is_file():
            self.skipTest("Private real PARTIAL audit is unavailable")
        before = {path.name for path in directory.iterdir()}
        result = subprocess.run([sys.executable, "-B", "-I", "-c", builder.CONTENTION_CHECK_CODE,
                                 str(MODULE), str(directory), ""], capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PARTIAL requires --draft", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual({path.name for path in directory.iterdir()}, before)

    def test_draft_contention_figures_cannot_enter_a_verified_final_binding(self):
        from research_reporting import contention_figures
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "CONTENTION_REPLAY.json").write_text(json.dumps(lifecycle_only()))
            figures = root / "TEST-ONLY-DRAFT-figures"
            figures.mkdir()
            (figures / "FIGURE_MANIFEST.json").write_text(json.dumps({
                "schema_version": "h100-contention-figures-v1", "draft": True, "final": False}))
            inputs = contention_figures.audit.Inputs()
            inputs.file(root / "CONTENTION_REPLAY.json")
            data = {"report": lifecycle_only(), "inputs": inputs, "final": True, "draft": False}
            with mock.patch.object(builder, "load_contention_verifier", return_value=contention_figures), \
                    mock.patch.object(contention_figures, "load_audit", return_value=data) as loader:
                with self.assertRaisesRegex(ValueError, "genuine final exports"):
                    builder.scientific_contention_check(root, figures)
                loader.assert_called_once_with(root.resolve(), draft=False)

    def test_shadowed_verifier_cannot_run_while_proof_cites_current_source_paths(self):
        loader = mock.Mock()
        verifier = SimpleNamespace(__file__="/TEST-ONLY-shadow/contention_figures.py", load_audit=loader,
                                   audit=SimpleNamespace(__file__=str(builder.REPORTING / "contention_replay.py")))
        with mock.patch.object(builder, "load_contention_verifier", return_value=verifier):
            with self.assertRaisesRegex(ValueError, "module origins differ"):
                builder.scientific_contention_check(Path("not-read"))
        loader.assert_not_called()

    def test_sanitization_preserves_public_bibliography_and_detects_private_patterns(self):
        self.assertEqual(builder.sanitize("NIST section 5.3.3.2; Polars 1.44.2")["status"], "PASS")
        for text in ("192.0.2.1", "/Users/example/private", "OPENAI_API_KEY=example",
                     "-----BEGIN PRIVATE KEY-----", "sk-" + "x" * 25):
            self.assertEqual(builder.sanitize(text)["status"], "FAIL")

    def test_contention_embedding_copies_only_hash_bound_final_assets(self):
        from PIL import Image, ImageDraw
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            figures, output = root / "TEST-ONLY-figures", root / "TEST-ONLY-output"
            figures.mkdir()
            output.mkdir()
            source = figures / "layout.png"
            raster = Image.new("RGB", (500, 250), "white")
            ImageDraw.Draw(raster).text((10, 10), "SYNTHETIC LAYOUT ONLY", fill="black")
            raster.save(source)
            reference = "contention/figures/layout.png"
            ast = {"blocks": [{"t": "Para", "c": [{"t": "Image", "c": [["", [], []],
                [{"t": "Str", "c": "SYNTHETIC layout only; no observations."}], [reference, ""]]}]}]}
            manifest = {"final": True, "output_files_sha256": {"layout.png": builder.sha256(source)}}
            binding = builder.render_pdf(ast, output / "SYNTHETIC-layout.pdf", "SYNTHETIC LAYOUT ONLY", False,
                                         output, None, None, figures, manifest)
            self.assertEqual(binding["embedded_figures_sha256"], {reference: builder.sha256(source)})
            self.assertEqual(builder.sha256(output / reference), builder.sha256(source))
            with self.assertRaisesRegex(ValueError, "verified final manifest"):
                builder.render_pdf(ast, output / "SYNTHETIC-rejected.pdf", "SYNTHETIC LAYOUT ONLY", False,
                                   output, None, None, figures, manifest | {"final": False})

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
