"""Record actual v4 viewing and v5 changed-page inspection, never render or publish."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import re

from PIL import Image
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "rerun/gclocker100-20261027"
V4, V5 = BASE / "paper-final-v4", BASE / "paper-final-v5"
QA4, QA5 = BASE / "paper-final-v4-qa", BASE / "paper-final-v5-qa"
PROOF4 = BASE / "paper-final-v4-independent-qa"
FACTUAL = BASE / "independent-numeric-claim-review-20261028T235145Z"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref(path):
    return {"path": str(path.resolve()), "sha256": sha(path), "bytes": path.stat().st_size}


def read(path):
    return json.loads(path.read_text())


def write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        handle.write(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return ref(path)


def image_ref(path):
    with Image.open(path) as raster:
        return {**ref(path), "dimensions_px": list(raster.size)}


def check_hash(path, expected):
    assert sha(path) == expected, str(path)


now = datetime.now(timezone.utc).isoformat()
expected = {
    V4 / "h100-paper.pdf": "76468f04faf10a8e08c66bb876343c57c168b610ab546eac7ca6dbd82eade53b",
    V4 / "BUILD_MANIFEST.json": "a67e252d542ed42a2a8aa9478ead731f384bb92f0abd8f0993320ee70c129c58",
    V5 / "h100-paper.pdf": "9eb08c534265ee2951522b1e4849df08d08558012eea74eeaedfad751325490b",
    V5 / "BUILD_MANIFEST.json": "3e92a61b5aa3c65f1de90bfcb3111147e52f0971b19fd0e0b29b91d00253fb23",
    V5 / "paper-source.md": "c0fa1713e826412ad4537987bef23a0162b6c1979ea7f37153a31f1d6ec73bec",
    ROOT / "publication/h100-confirmatory/MANUSCRIPT.md": "2fefe2e12f35f323b3cc00c4eafc7505332d4e9080e177915624542eac45d245",
    ROOT / "publication/h100-confirmatory/references.md": "457f465b53266d5d1db71969edea3d9d7d42f13e0c4a920928d024297148860e",
    BASE / "FINAL_PAPER_REVIEW.json": "4752dfd5513dddb3a7a3132eb1d260ab8dca9a4382c5c8b186ba04d9fe8db9d4",
    BASE / "replay-v1/REPLAY_MANIFEST.json": "b59450d0731180b5b9cc642c5174f872ab460ab585445b87230175a07c48655c",
    BASE / "replay-v1/complete-audit/AUDIT_COMPLETE.json": "cb03c639c0b428f50f07a142d1201e8a3c39ad82ea21a2192db95d4ebfc5585d",
    PROOF4 / "PDF_VISUAL_REVIEW_PAGES20_37.json": "fe46c4b9f37285952ea4ac5a9d657aed71378ebcd57f7dfc370804423677a387",
    FACTUAL / "CURRENT_SOURCE_FACTUAL_REVIEW.json": "9b594f8f9a3003f8dd79abd8acd796a9f419d52a16e1f46e43942ae54c5fb434",
    FACTUAL / "NUMERIC_RECOMPUTATION_REVIEW_WITH_QUERY_ESTIMATES.json": "c7055e21c8b803b3a3db459248b77f2f91d1d685741e181dfeb7ff656b0ccb8f",
    FACTUAL / "MANUSCRIPT_NUMERIC_CLAIM_REVIEW_CLEAN_SOURCE.json": "ee461bbac23442af62f04ae5d5a158d70fa6c76c4659a1b5f405b60f924621b3",
    ROOT / "rerun/contention-20261028/offline-terminal-audit-v1/CONTENTION_REPLAY.json": "0cad03b7ab2edc7e0d62dbcc7b84f8fa0ea51325aba41eb354b5baae0c51a2d3",
}
for path, digest in expected.items():
    check_hash(path, digest)
build4, build5 = (read(v / "BUILD_MANIFEST.json") for v in (V4, V5))
review = read(BASE / "FINAL_PAPER_REVIEW.json")
for manifest, folder in [(build4, V4), (build5, V5)]:
    assert manifest["status"] == "PASS" and manifest["final"] is True and manifest["synthetic"] is False
    for name, digest in manifest["output_files_sha256"].items():
        check_hash(folder / name, digest)
assert build5["builder_sha256"] == sha(ROOT / "publication/h100-confirmatory/paper/build_paper.py")
assert build5["public_sanitization"]["status"] == "PASS"
assert build5["embedded_figures_sha256"] == build4["embedded_figures_sha256"]
assert len(build5["embedded_figures_sha256"]) == 10
assert build5["contention_review"] == build4["contention_review"] == review["contention_review"]
assert build5["editorial_review_sha256"] == sha(BASE / "FINAL_PAPER_REVIEW.json")
assert sha(V5 / "paper-source.md") == sha(V4 / "paper-source.md")
assert sha(V5 / "paper-source.json") == sha(V4 / "paper-source.json")
fields = ("all_26_primary_rows", "all_40_coverage_cells", "all_32_secondary_pairs",
          "deviations_and_limits", "results_and_captions", "contention_results_and_limits")
assert review["status"] == "complete" and all(review[field] is True for field in fields)
assert review["manuscript_sha256"] == build5["manuscript_sha256"]
assert review["references_sha256"] == build5["references_sha256"]
readers = [PdfReader(folder / "h100-paper.pdf") for folder in (V4, V5)]
assert all(len(reader.pages) == 37 for reader in readers)


def body(reader):
    # The fixed template emits header, author, footer and page number before
    # body content in the extraction stream. Only pagination changes here.
    return re.sub(r"\s+", " ", " ".join("\n".join(page.extract_text().splitlines()[4:])
                                         for page in reader.pages)).strip()


assert body(readers[0]) == body(readers[1])
assert all(len("\n".join(page.extract_text().splitlines()[4:]).strip()) > 10 or len(page.images) > 0
           for page in readers[1].pages)
comparison = read(QA5 / "RASTER_COMPARISON.json")
assert comparison["changed"] == [14, 15]
assert comparison["page_counts"] == [37, 37]
for row in comparison["raster_hashes"]:
    number = row["page"]
    check_hash(QA4 / f"page-{number:02}.png", row["v4_sha256"])
    check_hash(QA5 / f"page-{number:02}.png", row["v5_sha256"])
assert "Table 2c." not in readers[1].pages[13].extract_text()
assert "Table 2c." in readers[1].pages[14].extract_text()

notes = [
    "Title, author, abstract and introduction are legible and clean.",
    "Related work text and references are legible and clean.",
    "Host/software table and code flags are legible and clean.",
    "Data manifest and five-scale table are legible and clean.",
    "Cohort identities and readiness hashes are legible and clean.",
    "Design and timing methods are legible and clean.",
    "Power-gap, correctness and coverage methods are legible and clean.",
    "Formulas and primary-family allocation table are legible and clean.",
    "Contention pairs table and methods are legible and clean.",
    "Interlude text and results heading are legible and clean.",
    "Cohort disposition and completion counts are legible and clean.",
    "Role table and all forty score cells are legible and clean.",
    "Landscape complete-case score/coverage figure is readable.",
    "Primary Tables 2a/2b are clean; Table 2c caption is orphaned at the bottom, fixed in v5.",
    "Primary Tables 2c/2d and body are legible; v4 Table 2c caption is on prior page, fixed in v5.",
    "Landscape baseline paired-ratio figure is readable.",
    "Landscape complex paired-ratio figure and explicit N/A coverage are readable.",
    "All twelve SF300 query rows and narrative are legible and clean.",
    "Landscape query heatmap, colorbar labels and N/A cells are readable.",
]
range01 = {
    "schema_version": "h100-independent-pdf-range-visual-review-v1",
    "status": "REVIEWED_WITH_LAYOUT_FINDING_SUPERSEDED_BY_V5",
    "completed_at_utc": now,
    "inspection_method": "Individual original-size view_image inspection completed before the authorized v5 repair; this records that actual prior viewing without backdating.",
    "pdf": ref(V4 / "h100-paper.pdf"), "paper_source": ref(V4 / "paper-source.md"),
    "build_manifest": ref(V4 / "BUILD_MANIFEST.json"), "document_page_count": 37,
    "reviewed_page_range": [1, 19], "all_document_pages_reviewed_by_this_reviewer": False,
    "reviewed_pages": [{"page": n, "raster": image_ref(QA4 / f"page-{n:02}.png"), "observed": notes[n-1]}
                       for n in range(1, 20)],
    "findings": [{"page": 14, "description": "Table 2c title/caption separated from table on page 15.",
                  "disposition": "Root required repair; v4 preserved and superseded by v5."}],
    "scope": "Actual individual page viewing only; does not approve v4 as final or attest public deployment, retrieval or retirement.",
}
range01_ref = write_new(PROOF4 / "PDF_VISUAL_REVIEW_PAGES01_19.json", range01)
range20 = read(PROOF4 / "PDF_VISUAL_REVIEW_PAGES20_37.json")
old_notes = {row["page"]: row["observations"]
             for row in range20["reviewed_pages"]}
page_reviews = []
for n in range(1, 38):
    changed = n in (14, 15)
    observation = ("Tables 2a/2b remain intact and legible, with no orphan Table 2c label at the foot of the page."
                   if n == 14 else "Table 2c title, header and all rows now share page 15; Table 2d and narrative remain clean and legible."
                   if n == 15 else notes[n-1] if n <= 19 else old_notes[n])
    page_reviews.append({"page": n, "raster": image_ref(QA5 / f"page-{n:02}.png"),
                         "method": "Individual actual v5 view_image inspection" if changed else "Inherited actual v4 viewing: byte-identical page raster",
                         "v4_raster_sha256": sha(QA4 / f"page-{n:02}.png"), "observed": observation,
                         "prior_receipt": range01_ref if n <= 19 else ref(PROOF4 / "PDF_VISUAL_REVIEW_PAGES20_37.json")})
allqa = {
    "schema_version": "h100-independent-pdf-all-pages-visual-review-v1", "status": "PASS",
    "completed_at_utc": now, "pdf": ref(V5 / "h100-paper.pdf"),
    "paper_source": ref(V5 / "paper-source.md"), "build_manifest": ref(V5 / "BUILD_MANIFEST.json"),
    "document_page_count": 37, "all_document_pages_reviewed": True,
    "reviewed_pages": page_reviews, "direct_v5_pages": [14, 15],
    "inherited_byte_identical_pages": comparison["same"], "raster_comparison": ref(QA5 / "RASTER_COMPARISON.json"),
    "prior_range01_19": range01_ref, "prior_range20_37": ref(PROOF4 / "PDF_VISUAL_REVIEW_PAGES20_37.json"),
    "render_command": ref(QA5 / "RENDER_COMMAND.json"), "changed_pages_individually_viewed": True,
    "checks": {"no_new_blank_pages": True, "page_count_unchanged": True,
               "all_normalized_pdf_body_text_unchanged": True, "markdown_and_ast_bytes_unchanged": True,
               "all_10_embedded_figure_bytes_unchanged": True, "all_build_inventory_hashes_match": True,
               "table2c_caption_and_table_share_page15": True, "public_sanitization": "PASS"},
    "repair": {"builder_commit": "d64f6dc", "builder_sha256": build5["builder_sha256"],
               "description": "Keep bold table labels with next table through its spacing; no manuscript, numerical, caption or image edits.",
               "paper_tests": {"run": 22, "status": "PASS"},
               "development_observation": "First new regression run failed because a nested ReportLab KeepTogether ended the keep chain before the table; corrected before final build and all 22 tests passed."},
    "qa_checker_observation": "Initial local receipt checker required more than three extracted body lines per page, which incorrectly rejected image pages with short extracted captions. Replaced with nonempty body-text-or-image check; no evidence changed.",
    "findings": [], "resolved_prior_findings": [{"description": "V4 Table 2c orphan caption", "resolution": "Visually verified fixed on v5 page 15"}],
    "scope": "All 37 real v5 pages covered by explicit viewing of changed pages and exact raster inheritance from this reviewer's real v4 viewing; no claim of website deployment, private retrieval or retirement readiness.",
}
qa_ref = write_new(QA5 / "PDF_VISUAL_REVIEW_ALL37.json", allqa)
publication = {
    "schema_version": "h100-final-publication-v1", "status": "PASS", "full_paper": True,
    "final_claim_audit": "PASS", "author": "Daniel Ejiofor", "completed_at_utc": now,
    "campaign_sha256": build5["campaign_sha256"], "paper_pdf_sha256": sha(V5 / "h100-paper.pdf"),
    "paper_source_sha256": sha(V5 / "paper-source.md"), "paper_pdf": ref(V5 / "h100-paper.pdf"),
    "paper_source": ref(V5 / "paper-source.md"), "build_manifest": ref(V5 / "BUILD_MANIFEST.json"),
    "page_count": 37, "all_page_visual_qa": qa_ref, "visual_qa": "PASS",
    "editorial_review": ref(BASE / "FINAL_PAPER_REVIEW.json"),
    "manuscript_sha256": build5["manuscript_sha256"], "references_sha256": build5["references_sha256"],
    "substantive_review_fields": {field: review[field] for field in fields},
    "independent_factual_review": ref(FACTUAL / "CURRENT_SOURCE_FACTUAL_REVIEW.json"),
    "independent_numeric_review": ref(FACTUAL / "NUMERIC_RECOMPUTATION_REVIEW_WITH_QUERY_ESTIMATES.json"),
    "independent_manuscript_claim_review": ref(FACTUAL / "MANUSCRIPT_NUMERIC_CLAIM_REVIEW_CLEAN_SOURCE.json"),
    "replay_manifest": ref(BASE / "replay-v1/REPLAY_MANIFEST.json"),
    "complete_audit": ref(BASE / "replay-v1/complete-audit/AUDIT_COMPLETE.json"),
    "contention_review": build5["contention_review"],
    "public_sanitization": build5["public_sanitization"], "findings": [],
    "scope": "Final complete paper, source, substantive claims and all-page PDF visual QA approved. This receipt is not an attestation that website deployment, private Git push, release retrieval or VM retirement has occurred.",
}
publication_ref = write_new(BASE / "final-delivery-v1/PUBLICATION_RECEIPT.json", publication)
print(json.dumps({"qa": qa_ref, "publication_receipt": publication_ref}, indent=2))
