#!/usr/bin/env python3
"""Build a readable H100 paper from Markdown; final builds require real replay.

This editorial layer never runs a benchmark, changes an estimate, publishes a
page, or modifies the frozen source. ReportLab renders the PDF from Pandoc's
Markdown AST. The same composed Markdown is exported as standalone HTML.
"""
from __future__ import annotations

import argparse
import csv
import contextlib
from datetime import datetime, timezone
import hashlib
import html
import ipaddress
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import textwrap

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "MANUSCRIPT.md"
REFERENCES = HERE.parent / "references.md"
ROOT = HERE.parents[2]
REPORTING = ROOT / "research_reporting"
AUTHOR = "Daniel Ejiofor"
CAMPAIGN_ID = "h100-confirmatory-gclocker100-20261027"
SOURCE_TREE = "9514f0f7ce384763d0a675a9075666707e0edabcc003c729a3e0735a52ea25d8"
EXPECTED_COUNTS = {"sessions": 400, "successful_outputs": 9360, "warmup_outputs": 2340,
                   "measured_outputs": 7020, "translation_rejections": 60,
                   "successful_complex_cleanups": 3360, "failed_complex_cleanups": 60}
C84 = "c84ef6a07341f350dae6636ab709c1b65fd894e0"
OLD_CAMPAIGN = "69669dfbc74ffb2a0c378c319c7b886043ffccb8f301784fb2b73b9ab32581e0"
REVIEW_FIELDS = ("all_26_primary_rows", "all_40_coverage_cells", "all_32_secondary_pairs",
                 "deviations_and_limits", "results_and_captions", "contention_results_and_limits")
CONTENTION_ID = "h100-contention-20261028"
CONTENTION_PLAN = "670f37af6c1a6f2dbea5fc110d213b148a0acb83858786a21add8252acc89031"
CONTENTION_PRESTART = "4088b5302a7d42c8aa87ebaed06e229bed3b0783bd15cf6556a6110981bf9f25"
CONTENTION_REFERENCE = "3142874b918a2df2d66c8dfb32c26f59a09353edcb4cb73e51c4ba46ff387641"
CONTENTION_PROFILE = "a07569ffa3be9cd138ed63084ff59ed668b8bceeef355b5a4f63adef53464c69"
CONTENTION_CHECK_CODE = """import importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("h100_paper_scientific_check", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
proof = module.scientific_contention_check(Path(sys.argv[2]), Path(sys.argv[3]) if sys.argv[3] else None)
print(json.dumps(proof, sort_keys=True, allow_nan=False))
"""
PENDING = re.compile(r"\b(pending|placeholder|TODO|TBD)\b|technical draft", re.I)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def decode_json(value, label="JSON"):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key in {label}")
            result[key] = value
        return result
    return json.loads(value, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def read_json(path):
    return decode_json(Path(path).read_text(), Path(path).name)


def contained(root, relative):
    relative = PurePosixPath(str(relative))
    if relative.is_absolute() or ".." in relative.parts or "\\" in str(relative):
        raise ValueError(f"Unsafe artifact: {relative}")
    root = Path(root).resolve()
    path = root.joinpath(*relative.parts)
    if (not path.resolve().is_relative_to(root) or not path.is_file() or
            any(part.is_symlink() for part in (path, *path.parents) if part != root and root in part.parents)):
        raise ValueError(f"Missing/escaping/symlink artifact: {relative}")
    return path


def registered_identity(manifest):
    spec = importlib.util.spec_from_file_location("h100_paper_campaign_identity", REPORTING / "campaign_identity.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    identity = helper.registered_identity(manifest.get("campaign_sha256"), manifest.get("prestart_sha256"))
    if (identity.get("campaign_id") != CAMPAIGN_ID or
            not re.fullmatch(r"[0-9a-f]{64}", identity.get("execution_profile_sha256", "")) or
            manifest.get("registered_campaign_identity") != identity):
        raise ValueError("Final paper requires the exact registered recovery campaign/profile")
    return identity


def verify_recovery_profile(audit, identity):
    expected = {"path": "EXECUTION_PROFILE.json", "sha256": identity["execution_profile_sha256"],
                "spark_driver_logs": 160, "profile_gates": 24, "status": "PASS"}
    if audit.get("execution_profile") != expected:
        raise ValueError("Recovery profile audit must verify all 160 Spark drivers and 24 profile gates")


def verify_replay(root):
    """Verify already-produced evidence without recomputing scientific results."""
    root = root.resolve()
    manifest_path = root / "REPLAY_MANIFEST.json"
    manifest = read_json(manifest_path)
    campaign = manifest.get("campaign_sha256")
    if (manifest.get("schema_version") != "research-artifact-replay-v1" or
            manifest.get("status") != "PASS" or manifest.get("phase") != "complete" or
            manifest.get("synthetic") is not False or manifest.get("source_commit") != C84 or
            manifest.get("campaign_id") != CAMPAIGN_ID or
            not re.fullmatch(r"[0-9a-f]{64}", campaign or "") or campaign == OLD_CAMPAIGN):
        raise ValueError("Final paper requires a completed genuine recovery replay")
    source_map = manifest.get("source_files_sha256", {})
    if (len(source_map) != 52 or manifest.get("source_tree_sha256") != SOURCE_TREE or
            hashlib.sha256((json.dumps(source_map, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()).hexdigest()
            != SOURCE_TREE):
        raise ValueError("Final paper requires the exact frozen 52-file C84 source identity")
    identity = registered_identity(manifest)
    for field, filename in (("replay_code_sha256", "replay.py"), ("auditor_source_sha256", "audit_complete.py"),
                            ("renderer_sha256", "render.py")):
        if manifest.get(field) != sha256(REPORTING / filename):
            raise ValueError(f"Reporting source differs: {filename}")
    commands = manifest.get("commands", [])
    if ([entry.get("name") for entry in commands] != ["validation", "analysis", "complete_audit", "rendering"] or
            any(entry.get("status") != "PASS" or entry.get("returncode") != 0 for entry in commands)):
        raise ValueError("Final paper requires all complete replay commands to succeed")
    inventory = manifest.get("output_files_sha256", {})
    critical = {"validation.json", "analysis/analysis.json", "analysis/block_ratios.csv",
                "complete-audit/AUDIT_COMPLETE.json", "complete-audit/HOST_RESULT_SENSITIVITY.csv",
                "figures/RENDER_MANIFEST.json", "figures/primary_contrasts.csv",
                "figures/complete_case_scores.csv"}
    if not critical <= inventory.keys():
        raise ValueError("Replay is missing required complete paper artifacts")
    for relative, digest in inventory.items():
        if not re.fullmatch(r"[0-9a-f]{64}", digest or "") or sha256(contained(root, relative)) != digest:
            raise ValueError(f"Replay artifact hash differs: {relative}")
    validation = read_json(root / "validation.json")
    audit = read_json(root / "complete-audit/AUDIT_COMPLETE.json")
    analysis = read_json(root / "analysis/analysis.json")
    figures = read_json(root / "figures/RENDER_MANIFEST.json")
    audit_ref, render_ref = manifest.get("complete_audit", {}), manifest.get("render_manifest", {})
    if (audit_ref != {"path": "complete-audit/AUDIT_COMPLETE.json", "status": "PASS",
                      "sha256": sha256(root / "complete-audit/AUDIT_COMPLETE.json")} or
            render_ref.get("path") != "figures/RENDER_MANIFEST.json" or
            render_ref.get("sha256") != sha256(root / "figures/RENDER_MANIFEST.json")):
        raise ValueError("Replay audit/render manifest binding differs")
    if (any(value.get("campaign_sha256") != campaign for value in (validation, audit, analysis, figures)) or
            validation.get("status") != "PASS" or
            validation.get("planned_sessions") != validation.get("audited_sessions") or
            validation.get("audited_sessions") != 400 or validation.get("successful_outputs_compared") != 9360 or
            validation.get("pending_sessions") or validation.get("failed_sessions") or
            validation.get("journal_sha256") != audit.get("journal_sha256") or
            audit.get("schema_version") != "research-complete-audit-v1" or
            audit.get("status") != "PASS" or audit.get("findings") != [] or audit.get("synthetic") is not False or
            audit.get("counts") != EXPECTED_COUNTS or audit.get("registered_campaign_identity") != identity or
            audit.get("source_sha256") != manifest["auditor_source_sha256"] or
            len(audit.get("sessions", [])) != 400 or any(row.get("status") != "PASS" for row in audit["sessions"]) or
            analysis.get("campaign_id") != CAMPAIGN_ID or analysis.get("primary_family_status") != "complete" or
            analysis.get("validation_sha256") != sha256(root / "validation.json") or
            analysis.get("analysis_source_sha256") != source_map["research_analysis.py"] or
            len(analysis.get("sessions", [])) != 400 or len(analysis.get("primary", [])) != 26 or
            figures.get("synthetic") is not False or figures.get("campaign_id") != CAMPAIGN_ID or
            figures.get("renderer_sha256") != manifest["renderer_sha256"] or
            figures.get("primary_test_family_size") != 26 or
            any(row.get("status") != "complete" or row.get("n_blocks") != 10 for row in analysis["primary"])):
        raise ValueError("Replay must retain complete matching validation, audit, analysis and figures")
    verify_recovery_profile(audit, identity)
    for relative, count in (("figures/primary_contrasts.csv", 26),
                            ("figures/complete_case_scores.csv", 40),
                            ("complete-audit/HOST_RESULT_SENSITIVITY.csv", 32)):
        with (root / relative).open(newline="") as handle:
            if len(list(csv.DictReader(handle))) != count:
                raise ValueError(f"Incomplete paper table: {relative}")
    return manifest, sha256(manifest_path)


def contention_disposition_gate(report):
    """Lifecycle predicate only; the shared current-source replay is also mandatory."""
    if (report.get("schema_version") != "h100-contention-replay-v1" or report.get("synthetic") is not False or
            report.get("experiment_id") != CONTENTION_ID or report.get("plan_sha256") != CONTENTION_PLAN or
            report.get("prestart_sha256") != CONTENTION_PRESTART or
            report.get("reference_campaign_sha256") != CONTENTION_REFERENCE or
            report.get("reference_execution_profile_sha256") != CONTENTION_PROFILE):
        raise ValueError("Final paper requires the exact genuine separate contention role")
    if (report.get("status") not in {"COMPLETE", "STOPPED_WITH_FAILURE"} or
            report.get("integrity_status") != "PASS" or report.get("publication_ready") is not True or
            report.get("findings") != []):
        raise ValueError("Final paper requires terminal publication-ready contention provenance; PARTIAL is not final")
    handoff = report.get("handoff", {})
    if (handoff.get("status") != "PASS" or handoff.get("same_scheduler_resumed") is not True or
            handoff.get("original_payload_verified") is not True or handoff.get("no_action_overlap") is not True or
            report.get("terminal_driver", {}).get("terminal") is not True):
        raise ValueError("Final contention provenance requires original payload, separation and same-scheduler resume proof")


def contention_review_object(report, digest):
    """Shape the common paper/web contract; never substitute this for scientific verification."""
    contention_disposition_gate(report)
    if not re.fullmatch(r"[0-9a-f]{64}", digest or ""):
        raise ValueError("An exact terminal contention receipt hash is required")
    return {"status": "COMPLETE", "experiment_id": CONTENTION_ID, "plan_sha256": CONTENTION_PLAN,
            "prestart_sha256": CONTENTION_PRESTART, "replay_sha256": digest, "disposition": report["status"]}


def load_contention_verifier():
    """Load the current helper and its auditor by exact paths, without package shadowing."""
    def module(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ValueError("Cannot load the current contention scientific verifier")
        value = importlib.util.module_from_spec(spec)
        previous = sys.modules.get(name)
        sys.modules[name] = value
        try:
            spec.loader.exec_module(value)
        except BaseException:
            sys.modules.pop(name, None)
            if previous is not None:
                sys.modules[name] = previous
            raise
        return value
    auditor = module("contention_replay", REPORTING / "contention_replay.py")
    verifier = module("h100_paper_contention_verifier", REPORTING / "contention_figures.py")
    if verifier.audit is not auditor:
        raise ValueError("Contention helper imported a different auditor module")
    return verifier


def scientific_contention_check(directory, figures=None):
    """Run only in the explicitly selected analysis Python, with lazy plotting imports.

    The shared loader replays current-source offline validation against actual
    raw bytes. This function never imports or invokes its plotting functions.
    """
    directory = Path(directory).resolve()
    verifier = load_contention_verifier()
    loader = REPORTING / "contention_figures.py"
    auditor = REPORTING / "contention_replay.py"
    if (Path(verifier.__file__).resolve() != loader.resolve() or
            Path(verifier.audit.__file__).resolve() != auditor.resolve()):
        raise ValueError("Scientific contention verifier/auditor module origins differ from current reporting source")
    receipt = contained(directory, "CONTENTION_REPLAY.json")
    with contextlib.redirect_stdout(sys.stderr):
        data = verifier.load_audit(directory, draft=False)
    if data.get("final") is not True or data.get("draft") is not False:
        raise ValueError("Shared contention loader did not verify a genuine terminal audit")
    report, inputs = data["report"], data["inputs"]
    review = contention_review_object(report, sha256(receipt))
    inputs.file(loader)
    figure_binding = None
    if figures is not None:
        figures = Path(figures).resolve()
        figure_path = contained(figures, "FIGURE_MANIFEST.json")
        value = read_json(figure_path)
        if (value.get("schema_version") != "h100-contention-figures-v1" or value.get("draft") is not False or
                value.get("final") is not True or value.get("publication_ready") is not True or
                value.get("synthetic") is not False or value.get("audit_publication_ready") is not True or
                value.get("audit_status") != review["disposition"] or value.get("plan_sha256") != CONTENTION_PLAN or
                value.get("prestart_sha256") != CONTENTION_PRESTART or
                value.get("auditor_source_sha256") != report["audit_source_sha256"] or
                value.get("renderer_source_sha256") != sha256(loader) or
                value.get("input_files_sha256") != inputs.hashes or
                value.get("reproduced_audit_input_files_sha256") != report["input_files_sha256"] or
                value.get("audit_counts") != report["counts"]):
            raise ValueError("Contention figures must be genuine final exports of this exact terminal audit")
        phases = {wave["wave_id"].split("-", 1)[0] for wave in report["waves"]}
        stems = {f"{phase}_{stem}" for phase in phases for stem in
                 ("pair_A_common_actions", "pair_B_common_actions", "actual_action_timeline")}
        expected = {f"{stem}.{extension}" for stem in stems for extension in ("png", "svg", "pdf")} | {"CAPTIONS.md"}
        if (set(value.get("output_files_sha256", {})) != expected or
                {path.name for path in figures.iterdir() if path.is_file()} != expected | {"FIGURE_MANIFEST.json"}):
            raise ValueError("Final contention figure inventory differs from the actually attempted phases")
        for name, digest in value["output_files_sha256"].items():
            if inputs.file(contained(figures, name)) != digest:
                raise ValueError(f"Contention figure hash differs: {name}")
        figure_binding = {"directory": str(figures), "manifest_sha256": inputs.file(figure_path), "manifest": value}
    inputs.recheck()
    protected = [directory, Path(report["canonical_directory"])]
    protected += [Path(row["directory"]) for row in report["snapshots"].values()]
    protected += [Path(row["archive_path"]).parent for row in report["snapshots"].values()]
    handoff = report["handoff"]
    protected += [Path(handoff[key]) for key in ("directory", "original_attempt_directory") if handoff.get(key)]
    if handoff.get("held_journal_path"):
        protected.append(Path(handoff["held_journal_path"]).parent)
    if report.get("terminal_driver", {}).get("path"):
        protected.append(Path(report["terminal_driver"]["path"]).parent)
    if figures:
        protected.append(figures)
    return {"schema_version": "h100-paper-contention-verification-v1", "final": True, "contention_review": review,
            "loader_sha256": sha256(loader), "auditor_sha256": sha256(auditor),
            "analysis_interpreter": sys.executable, "analysis_python_version": sys.version.split()[0],
            "input_files_sha256": inputs.hashes,
            "reproduced_audit_input_files_sha256": report["input_files_sha256"],
            "figures": figure_binding, "protected_roots": sorted({str(path.resolve()) for path in protected})}


def verify_contention_replay(directory, analysis_python, figures=None):
    """Require actual terminal proof from a caller-selected, separate analysis runtime."""
    directory = Path(directory).resolve()
    receipt = contained(directory, "CONTENTION_REPLAY.json")
    report = read_json(receipt)
    digest = sha256(receipt)
    expected = contention_review_object(report, digest)
    if analysis_python is None:
        raise ValueError("Contention verification requires explicit --analysis-python; no receipt-selected interpreter")
    interpreter = Path(analysis_python).expanduser().absolute()  # Preserve a venv symlink's invocation path.
    if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
        raise ValueError("--analysis-python must name an executable Python analysis environment")
    command = [str(interpreter), "-B", "-I", "-c", CONTENTION_CHECK_CODE,
               str(Path(__file__).resolve()), str(directory), str(Path(figures).resolve()) if figures else ""]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=1800, check=False)
    if result.returncode:
        detail = result.stderr.strip()[-4000:]
        raise ValueError(f"Fresh contention verification failed in the selected analysis Python: {detail}")
    proof = decode_json(result.stdout, "contention verification response")
    if (proof.get("schema_version") != "h100-paper-contention-verification-v1" or proof.get("final") is not True or
            proof.get("contention_review") != expected or proof.get("loader_sha256") != sha256(REPORTING / "contention_figures.py") or
            proof.get("auditor_sha256") != sha256(REPORTING / "contention_replay.py") or
            proof.get("input_files_sha256", {}).get(str(receipt)) != digest or
            sha256(receipt) != digest or (figures is not None) != (proof.get("figures") is not None)):
        raise ValueError("Fresh contention proof does not bind the exact terminal receipt/current verifier")
    if figures is not None and proof["figures"].get("directory") != str(Path(figures).resolve()):
        raise ValueError("Fresh contention figure proof identifies a different directory")
    for name, expected_hash in proof["input_files_sha256"].items():
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash or "") or sha256(name) != expected_hash:
            raise ValueError("Contention verification input changed after the fresh audit")
    return proof


def verify_review(path, replay, manuscript, contention_review=None):
    review = read_json(path)
    expected = {"status": "complete", "author": AUTHOR,
                "campaign_sha256": replay["campaign_sha256"],
                "manuscript_sha256": sha256(SOURCE), "references_sha256": sha256(REFERENCES),
                "contention_review": contention_review}
    if contention_review is None:
        raise ValueError("Final editorial review requires the verified terminal contention role")
    if any(review.get(key) != value for key, value in expected.items()) or any(
            review.get(key) is not True for key in REVIEW_FIELDS):
        raise ValueError("Final editorial review does not bind all required source/result checks")
    if PENDING.search(manuscript):
        raise ValueError("Final manuscript still contains a pending/draft marker")
    return review


def plain(inlines):
    return "".join(item.get("c", "") if item["t"] in ("Str",) else
                   " " if item["t"] in ("Space", "SoftBreak", "LineBreak") else
                   item["c"][1] if item["t"] in ("Code", "Math") else
                   plain(item["c"][1]) if item["t"] in ("Link", "Image") else
                   plain(item.get("c", [])) if isinstance(item.get("c"), list) else ""
                   for item in inlines)


def inline(inlines):
    output = []
    for item in inlines:
        kind, content = item["t"], item.get("c")
        if kind == "Str":
            output.append(html.escape(content))
        elif kind in ("Space", "SoftBreak"):
            output.append(" ")
        elif kind == "LineBreak":
            output.append("<br/>")
        elif kind in ("Strong", "Emph", "Strikeout", "Superscript", "Subscript"):
            tag = {"Strong": "b", "Emph": "i", "Strikeout": "strike", "Superscript": "super", "Subscript": "sub"}[kind]
            output.append(f"<{tag}>{inline(content)}</{tag}>")
        elif kind == "Code":
            output.append(f'<font name="PaperMono" size="8.2">{html.escape(content[1])}</font>')
        elif kind == "Math":
            output.append(html.escape(content[1]))
        elif kind == "Link":
            label, target = inline(content[1]), content[2][0]
            if target.startswith(("https://", "http://", "mailto:")):
                output.append(f'<a href="{html.escape(target, quote=True)}" color="#145f86">{label}</a>')
            else:
                output.append(label)
        elif kind == "Quoted":
            output.append("“" + inline(content[1]) + "”")
        elif kind == "Span":
            output.append(inline(content[1]))
        elif kind == "Image":
            raise ValueError("Images must be in their own Markdown paragraph")
        else:
            raise ValueError(f"Unsupported paper inline type: {kind}")
    return "".join(output)


def register_fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    roots = [Path("/usr/share/fonts/truetype/dejavu"), Path("/Library/Fonts")]
    try:
        import reportlab
        roots.append(Path(reportlab.__file__).parent / "fonts")
    except ImportError:
        pass
    choices = (("Paper", ("DejaVuSans.ttf", "Vera.ttf")),
               ("PaperBold", ("DejaVuSans-Bold.ttf", "VeraBd.ttf")),
               ("PaperItalic", ("DejaVuSans-Oblique.ttf", "VeraIt.ttf")),
               ("PaperBoldItalic", ("DejaVuSans-BoldOblique.ttf", "VeraBI.ttf")),
               ("PaperMono", ("DejaVuSansMono.ttf", "VeraMono.ttf", "Vera.ttf")))
    font_files = {}
    for name, candidates in choices:
        font = next((root / filename for root in roots for filename in candidates if (root / filename).is_file()), None)
        if font is None:
            raise ValueError(f"No supported Unicode font for {name}")
        pdfmetrics.registerFont(TTFont(name, str(font)))
        font_files[name] = {"file": font.name, "sha256": sha256(font)}
    pdfmetrics.registerFontFamily("Paper", normal="Paper", bold="PaperBold", italic="PaperItalic", boldItalic="PaperBoldItalic")
    return font_files


def render_pdf(ast, destination, title, final, output, replay_root, replay_manifest=None,
               contention_figures_root=None, contention_figures_manifest=None):
    import reportlab
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (BaseDocTemplate, Frame, HRFlowable, Image, KeepTogether, NextPageTemplate,
                                    PageBreak, PageTemplate, Paragraph, Preformatted, Spacer, Table, TableStyle)
    fonts = register_fonts()
    width, height = letter
    margin, text_width = .72 * inch, width - 1.44 * inch
    normal = ParagraphStyle("PaperBody", fontName="Paper", fontSize=9.5, leading=14,
                            spaceAfter=7, alignment=TA_LEFT, splitLongWords=True,
                            allowWidows=0, allowOrphans=0)
    styles = {"body": normal,
              "title": ParagraphStyle("PaperTitle", parent=normal, fontName="PaperBold", fontSize=21, leading=26, spaceAfter=14),
              "author": ParagraphStyle("PaperAuthor", parent=normal, fontSize=11, leading=16, spaceAfter=16),
              "h1": ParagraphStyle("PaperH1", parent=normal, fontName="PaperBold", fontSize=14, leading=19, spaceBefore=16, spaceAfter=8, keepWithNext=True),
              "h2": ParagraphStyle("PaperH2", parent=normal, fontName="PaperBold", fontSize=11, leading=16, spaceBefore=12, spaceAfter=6, keepWithNext=True),
              "h3": ParagraphStyle("PaperH3", parent=normal, fontName="PaperBold", fontSize=10, leading=14, spaceBefore=10, spaceAfter=5, keepWithNext=True),
              "table": ParagraphStyle("PaperTable", parent=normal, fontSize=8, leading=11, spaceAfter=0),
              "tablehead": ParagraphStyle("PaperTableHead", parent=normal, fontName="PaperBold", fontSize=8, leading=11, spaceAfter=0, textColor=colors.white),
              "caption": ParagraphStyle("PaperCaption", parent=normal, fontSize=8, leading=11, spaceAfter=6),
              "code": ParagraphStyle("PaperCode", parent=normal, fontName="PaperMono", fontSize=7.8, leading=11)}

    def page(canvas, doc):
        canvas.saveState()
        page_width, page_height = canvas._pagesize
        canvas.setTitle(title)
        canvas.setAuthor(AUTHOR)
        canvas.setSubject("H100 recovery study with separate contention extension" if final else "DRAFT: recovery findings pending; not final study results")
        canvas.setFont("Paper", 7.5)
        canvas.setFillColor(colors.HexColor("#586575"))
        canvas.drawString(margin, page_height - .40 * inch, "H100 dataframe execution · Polars and Spark")
        canvas.drawRightString(page_width - margin, page_height - .40 * inch,
                               "Daniel Ejiofor" if final else "DRAFT · RESULTS PENDING")
        canvas.setStrokeColor(colors.HexColor("#d9e1e8"))
        canvas.line(margin, .46 * inch, page_width - margin, .46 * inch)
        canvas.drawString(margin, .30 * inch, "Fixed configurations · one host · paired blocks")
        canvas.drawRightString(page_width - margin, .30 * inch, str(doc.page))
        canvas.restoreState()

    doc = BaseDocTemplate(str(destination), pagesize=letter, leftMargin=margin, rightMargin=margin,
                          topMargin=.72 * inch, bottomMargin=.64 * inch,
                          title=title, author=AUTHOR)
    portrait = Frame(margin, .64 * inch, text_width, height - 1.36 * inch,
                     leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    wide_width, wide_height = landscape(letter)
    figure_width, figure_height = wide_width - 2 * margin, wide_height - 1.36 * inch
    wide = Frame(margin, .64 * inch, figure_width, figure_height,
                 leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate("portrait", [portrait], onPage=page, pagesize=letter),
                          PageTemplate("landscape", [wide], onPage=page, pagesize=landscape(letter))])
    story = [Paragraph(html.escape(title), styles["title"]), Paragraph(AUTHOR, styles["author"])]
    figure_bindings = {}

    def cell_text(blocks):
        return "<br/>".join(inline(block["c"]) for block in blocks if block["t"] in ("Plain", "Para"))

    def add_image(item):
        label, reference = item["c"][1], item["c"][2][0]
        if reference.startswith("replay/figures/"):
            if replay_root is None:
                raise ValueError("Paper references a replay figure but --replay was omitted")
            relative = reference.removeprefix("replay/")
            source = contained(replay_root, relative)
            if not replay_manifest or replay_manifest.get("output_files_sha256", {}).get(relative) != sha256(source):
                raise ValueError("Referenced paper figure is not bound by the completed replay manifest")
        elif reference.startswith("contention/figures/"):
            if contention_figures_root is None or contention_figures_manifest is None:
                raise ValueError("Paper references a contention figure but verified --contention-figures was omitted")
            name = reference.removeprefix("contention/figures/")
            source = contained(contention_figures_root, name)
            if (contention_figures_manifest.get("final") is not True or
                    contention_figures_manifest.get("output_files_sha256", {}).get(name) != sha256(source)):
                raise ValueError("Referenced contention figure is not bound by its verified final manifest")
        else:
            raise ValueError("Paper figures require verified replay/figures/ or contention/figures/ references")
        destination_image = output / reference
        destination_image.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination_image)
        figure_bindings[reference] = sha256(source)
        if source.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            raise ValueError("Use the verified PNG/JPEG export for paper raster embedding")
        caption = Paragraph(inline(label), styles["caption"])
        _, caption_height = caption.wrap(figure_width, figure_height)
        caption_spacing = caption.getSpaceBefore() + caption.getSpaceAfter()
        image_caption_gap, layout_tolerance = 6, 2
        image_height = figure_height - caption_height - caption_spacing - image_caption_gap - layout_tolerance
        if image_height <= 0:
            raise ValueError("Figure caption leaves no room for its image on one landscape page")
        image = Image(str(destination_image))
        scale = min(figure_width / image.imageWidth, image_height / image.imageHeight)
        image.drawWidth, image.drawHeight = image.imageWidth * scale, image.imageHeight * scale
        story.extend([NextPageTemplate("landscape"), PageBreak(),
                      KeepTogether([image, Spacer(1, image_caption_gap), caption]),
                      NextPageTemplate("portrait"), PageBreak()])

    def blocks(items, numbered=None):
        for block in items:
            kind, content = block["t"], block.get("c")
            if kind in ("Para", "Plain"):
                if len(content) == 1 and content[0]["t"] == "Image":
                    add_image(content[0])
                else:
                    story.append(Paragraph(inline(content), styles["body"]))
            elif kind == "Figure":
                figure_body = content[2]
                if (len(figure_body) != 1 or figure_body[0]["t"] not in ("Para", "Plain") or
                        len(figure_body[0]["c"]) != 1 or figure_body[0]["c"][0]["t"] != "Image"):
                    raise ValueError("Paper figures require one standalone verified image")
                item = figure_body[0]["c"][0]
                if cell_text(content[1][1]) != inline(item["c"][1]):
                    raise ValueError("Figure caption differs from its image description")
                add_image(item)
            elif kind == "Header":
                level, _, words = content
                story.append(Paragraph(inline(words), styles[f"h{min(3, max(1, level - 1))}"]))
            elif kind == "Table":
                _, caption, _, head, bodies, foot = content
                rows = head[1] + [row for body in bodies for row in body[2] + body[3]] + foot[1]
                table_data = []
                header_count = len(head[1])
                for row_index, row in enumerate(rows):
                    table_data.append([Paragraph(cell_text(cell[4]), styles["tablehead"] if row_index < header_count else styles["table"])
                                       for cell in row[1]])
                column_count = max(len(row) for row in table_data)
                if any(len(row) != column_count for row in table_data):
                    raise ValueError("Spanning paper table cells require a simpler source table")
                weights = [max(12, min(70, max(len(re.sub("<[^>]*>", "", cell_text(row[1][i][4])))
                                                 for row in rows))) ** .5 for i in range(column_count)]
                widths = [text_width * weight / sum(weights) for weight in weights]
                table = Table(table_data, colWidths=widths, repeatRows=header_count, hAlign="LEFT")
                table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, header_count - 1), colors.HexColor("#20384d")),
                                           ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                           ("LEFTPADDING", (0, 0), (-1, -1), 6),
                                           ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                                           ("TOPPADDING", (0, 0), (-1, -1), 6),
                                           ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                                           ("ROWBACKGROUNDS", (0, header_count), (-1, -1), [colors.HexColor("#f0f4f7"), colors.white]),
                                           ("LINEBELOW", (0, 0), (-1, -1), .35, colors.HexColor("#d7e0e7"))]))
                story.extend([Spacer(1, 5), KeepTogether([table]) if len(rows) <= 8 else table, Spacer(1, 9)])
                if caption[1]:
                    blocks(caption[1])
            elif kind in ("OrderedList", "BulletList"):
                start, values = (content[0][0], content[1]) if kind == "OrderedList" else (1, content)
                for index, value in enumerate(values, start):
                    first = True
                    for item in value:
                        if first and item["t"] in ("Para", "Plain"):
                            prefix = f"{index}. " if kind == "OrderedList" else "• "
                            story.append(Paragraph(prefix + inline(item["c"]), styles["body"]))
                            first = False
                        else:
                            blocks([item])
            elif kind == "CodeBlock":
                lines = [line for raw in content[1].splitlines() for line in
                         (textwrap.wrap(raw, 96, replace_whitespace=False, drop_whitespace=False) or [""])]
                story.append(Preformatted("\n".join(lines), styles["code"]))
            elif kind == "HorizontalRule":
                story.append(HRFlowable(width="100%", thickness=.5, color=colors.HexColor("#d7e0e7")))
            elif kind == "BlockQuote":
                blocks(content)
            elif kind == "Div":
                blocks(content[1])
            else:
                raise ValueError(f"Unsupported paper block type: {kind}")
    blocks(ast["blocks"])
    doc.build(story)
    return {"reportlab": reportlab.Version, "fonts": fonts, "embedded_figures_sha256": figure_bindings}


def sanitize(text):
    checks = {"private_key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
              "api_token": r"\bsk-[A-Za-z0-9_-]{16,}|\bAKIA[A-Z0-9]{16}\b",
              "credential_assignment": r"(?i)(?:OPENAI_API_KEY|AWS_SECRET_ACCESS_KEY|Authorization)\s*[:=]\s*\S+",
              "local_home_path": r"/Users/[^\s<]+|/home/[^\s<]+|(?:^|\s)~?/\.ssh/"}
    findings = [{"kind": name, "count": len(re.findall(pattern, text))}
                for name, pattern in checks.items() if re.search(pattern, text)]
    ips = set()
    # These dotted values are a public handbook section and the pinned Java
    # version, not host addresses. Only the exact explicitly labeled contexts
    # are excluded; an unlabelled occurrence still receives the address check.
    address_text = re.sub(r"(?i)\bsection\s+5\.3\.3\.2\b", "NIST handbook section", text)
    address_text = re.sub(r"(?i)\b(?:Java|OpenJDK)\s+17\.0\.20\.1\b", "recorded Java version", address_text)
    for value in re.findall(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", address_text):
        try:
            ips.add(str(ipaddress.IPv4Address(value)))
        except ipaddress.AddressValueError:
            pass
    if ips:
        findings.append({"kind": "ipv4_address", "count": len(ips)})
    return {"status": "FAIL" if findings else "PASS", "findings": findings,
            "scope": "PDF extracted text and standalone HTML: IPv4 (excluding labeled NIST section 5.3.3.2 and Java/OpenJDK 17.0.20.1), private-key markers, credential patterns, local home paths; not release authorization"}


CSS = """body{max-width:900px;margin:3rem auto;padding:0 1.5rem;color:#162a3b;font:17px/1.65 Georgia,serif}
h1,h2,h3{font-family:system-ui,sans-serif;line-height:1.25}h1{font-size:2.2rem}h2{margin-top:2.3rem}
a{color:#145f86}table{border-collapse:collapse;width:100%;font:14px/1.4 system-ui,sans-serif;margin:1.5rem 0}
th{background:#20384d;color:white}td,th{padding:.6rem;border:1px solid #d7e0e7;vertical-align:top}
tr:nth-child(even){background:#f0f4f7}img{max-width:100%;height:auto}code{font-size:.86em;overflow-wrap:anywhere}
pre{white-space:pre-wrap}header{border-bottom:1px solid #d7e0e7;margin-bottom:2rem} .author{font:20px system-ui,sans-serif}
@media print{body{max-width:none;margin:0}h2,h3{break-after:avoid}tr,img{break-inside:avoid}}
"""


def build(output, replay_root=None, review_path=None, *, contention_root=None, analysis_python=None,
          contention_figures_root=None):
    output = output.resolve()
    replay_root = replay_root.resolve() if replay_root is not None else None
    contention_root = contention_root.resolve() if contention_root is not None else None
    contention_figures_root = contention_figures_root.resolve() if contention_figures_root is not None else None
    final = review_path is not None
    if output.exists():
        raise ValueError("Refusing to overwrite a paper output directory")
    if final and replay_root is None:
        raise ValueError("A final paper requires --replay")
    if final and contention_root is None:
        raise ValueError("A final paper requires --contention-replay")
    if (final or contention_root is not None) and analysis_python is None:
        raise ValueError("Contention verification requires explicit --analysis-python")
    if contention_figures_root is not None and contention_root is None:
        raise ValueError("--contention-figures requires --contention-replay")
    if not shutil.which("pandoc"):
        raise ValueError("Pandoc is required for Markdown parsing and standalone HTML")
    manuscript, references = SOURCE.read_text(), REFERENCES.read_text()
    title = manuscript.splitlines()[0].removeprefix("# ")
    replay, replay_hash = verify_replay(replay_root) if replay_root is not None else (None, None)
    contention = verify_contention_replay(contention_root, analysis_python, contention_figures_root) if contention_root else None
    if contention and replay and (replay["campaign_sha256"] != CONTENTION_REFERENCE or
            replay["registered_campaign_identity"]["execution_profile_sha256"] != CONTENTION_PROFILE):
        raise ValueError("Paper primary replay differs from the contention role's exact reference campaign/profile")
    if final:
        verify_review(review_path, replay, manuscript, contention["contention_review"])
    protected = ([replay_root] if replay_root else []) + (contention["protected_roots"] if contention else [])
    if replay:
        protected += [replay[key] for key in ("archive", "artifacts_root") if replay.get(key)]
    if any(output.is_relative_to(Path(path).resolve()) for path in protected):
        raise ValueError("Paper output must be separate from immutable replay/contention evidence")
    body = "\n".join(manuscript.splitlines()[1:]).strip()
    body = body.replace(f"**{AUTHOR}**\n", "", 1).lstrip()
    ref_body = "\n".join(references.splitlines()[1:]).strip()
    composed = body + "\n\n## References\n\n" + ref_body + "\n"
    # Local source links are useful in the repository but would be broken in a
    # standalone paper. Preserve their labels; remote references stay linked.
    composed = re.sub(r"(?<!!)\[([^\]]+)\]\((?!https?://|mailto:|#)([^)]+)\)", r"\1", composed)
    output.mkdir(parents=True)
    manifest = {"schema_version": "h100-paper-build-v1", "status": "RUNNING", "final": final,
                "synthetic": False, "author": AUTHOR, "title": title,
                "manuscript_sha256": sha256(SOURCE), "references_sha256": sha256(REFERENCES),
                "builder_sha256": sha256(Path(__file__)), "replay_manifest_sha256": replay_hash,
                "campaign_id": replay.get("campaign_id") if replay else None,
                "campaign_sha256": replay.get("campaign_sha256") if replay else None,
                "execution_profile_sha256": replay["registered_campaign_identity"]["execution_profile_sha256"] if replay else None,
                "contention_review": contention["contention_review"] if contention else None,
                "started_at_utc": datetime.now(timezone.utc).isoformat()}
    try:
        if contention:
            proof_path = output / "CONTENTION_VERIFICATION.json"
            proof_path.write_text(json.dumps(contention, indent=2, sort_keys=True, allow_nan=False) + "\n")
            manifest["contention_verification"] = {"path": proof_path.name, "sha256": sha256(proof_path),
                "analysis_python": str(Path(analysis_python).expanduser().absolute()),
                "analysis_interpreter": contention["analysis_interpreter"],
                "loader_sha256": contention["loader_sha256"], "auditor_sha256": contention["auditor_sha256"]}
        if review_path:
            manifest["editorial_review_sha256"] = sha256(review_path)
        markdown_path = output / "paper-source.md"
        markdown_path.write_text(composed)
        ast = json.loads(subprocess.run(["pandoc", "--from=markdown", "--to=json", str(markdown_path)],
                                        check=True, text=True, capture_output=True).stdout)
        (output / "paper-source.json").write_text(json.dumps(ast, ensure_ascii=False, indent=2) + "\n")
        figure_manifest = contention["figures"]["manifest"] if contention and contention.get("figures") else None
        manifest.update(render_pdf(ast, output / "h100-paper.pdf", title, final, output, replay_root, replay,
                                   contention_figures_root, figure_manifest))
        (output / "paper.css").write_text(CSS)
        html_source = output / "paper-html-source.md"
        html_source.write_text(f"---\ntitle: {json.dumps(title)}\nauthor: {json.dumps(AUTHOR)}\nlang: en\n---\n\n" + composed)
        subprocess.run(["pandoc", "--from=markdown", "--to=html5", "--standalone", "--embed-resources",
                        "--css=paper.css", "--resource-path=.", "--output=h100-paper.html", html_source.name],
                       cwd=output, check=True, text=True, capture_output=True)
        extractor = shutil.which("pdftotext")
        if extractor:
            extracted = subprocess.run([extractor, "-layout", str(output / "h100-paper.pdf"), "-"],
                                       check=True, text=True, capture_output=True).stdout
            manifest["pdf_text_extractor"] = "pdftotext -layout"
        else:
            import pypdf
            reader = pypdf.PdfReader(str(output / "h100-paper.pdf"))
            extracted = "\n\f\n".join(page.extract_text(extraction_mode="layout") for page in reader.pages)
            manifest["pdf_text_extractor"] = f"pypdf {pypdf.__version__} layout"
        (output / "paper-extracted.txt").write_text(extracted)
        if AUTHOR not in extracted or len(extracted) < 1000:
            raise ValueError("Generated PDF is missing expected paper text/author")
        if not final and "DRAFT" not in extracted:
            raise ValueError("Draft PDF is missing its explicit status")
        manifest["public_sanitization"] = sanitize(extracted + "\n" + (output / "h100-paper.html").read_text())
        if manifest["public_sanitization"]["status"] != "PASS":
            raise ValueError("Paper text contains a private-address/credential/path pattern; inspect sanitization categories")
        if sha256(SOURCE) != manifest["manuscript_sha256"] or sha256(REFERENCES) != manifest["references_sha256"]:
            raise ValueError("Paper source changed during the build")
        if replay_root is not None and sha256(replay_root / "REPLAY_MANIFEST.json") != replay_hash:
            raise ValueError("Replay manifest changed during the build")
        if contention:
            for name, digest in contention["input_files_sha256"].items():
                if sha256(name) != digest:
                    raise ValueError("Verified contention input/figure changed during the paper build")
        manifest.update(status="PASS" if final else "PASS_DRAFT", paper_sha256=sha256(output / "h100-paper.pdf"),
                        paper_html_sha256=sha256(output / "h100-paper.html"),
                        pandoc_version=subprocess.run(["pandoc", "--version"], check=True, text=True,
                                                      capture_output=True).stdout.splitlines()[0])
    except BaseException as exc:
        manifest.update(status="FAIL", error={"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        manifest["ended_at_utc"] = datetime.now(timezone.utc).isoformat()
        manifest["output_files_sha256"] = {str(path.relative_to(output)): sha256(path)
                                             for path in sorted(output.rglob("*")) if path.is_file()
                                             and path.name != "BUILD_MANIFEST.json"}
        (output / "BUILD_MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--final-review", type=Path)
    parser.add_argument("--contention-replay", type=Path, help="genuine terminal contention audit directory")
    parser.add_argument("--analysis-python", type=Path, help="explicit Python for fresh offline scientific verification")
    parser.add_argument("--contention-figures", type=Path, help="optional final hash-bound contention figure exports")
    args = parser.parse_args()
    try:
        result = build(args.output, args.replay, args.final_review, contention_root=args.contention_replay,
                       analysis_python=args.analysis_python, contention_figures_root=args.contention_figures)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Paper build refused/failed: {type(exc).__name__}: {exc}\n")
    print(json.dumps({key: result[key] for key in ("status", "final", "author", "campaign_id", "paper_sha256")}))


if __name__ == "__main__":
    main()
