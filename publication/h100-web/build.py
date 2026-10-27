#!/usr/bin/env python3
"""Build a public H100 study only from a complete, genuine, audited replay.

No synthetic opt-in exists here. `preview` writes a conspicuous methods-only
local preview; `publish` verifies every gate before creating any output file.
Only allowlisted fields and assets cross the private/public boundary.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REPORTING = ROOT / "research_reporting"
CAMPAIGN_ID = "h100-confirmatory-replacement-20261027"
SOURCE_COMMIT = "c84ef6a07341f350dae6636ab709c1b65fd894e0"
SOURCE_TREE = "9514f0f7ce384763d0a675a9075666707e0edabcc003c729a3e0735a52ea25d8"
CONFIGS = {"polars_cpu_144": "CPU Polars 1.44.2", "polars_cpu_200": "CPU Polars 2.0.0",
           "polars_gpu": "Polars GPU 1.44.2", "spark_cpu": "CPU Spark 3.5.9", "spark_gpu": "GPU Spark 3.5.9"}
EXPECTED_COUNTS = {"sessions": 400, "successful_outputs": 9360, "warmup_outputs": 2340,
                   "measured_outputs": 7020, "translation_rejections": 60,
                   "successful_complex_cleanups": 3360, "failed_complex_cleanups": 60}
FIGURES = ("suite_ratios_baseline", "suite_ratios_complex", "query_ratios_action", "complete_case_scores")
PUBLIC_CSVS = ("primary_contrasts.csv", "complete_case_scores.csv")
SECRET_PATTERNS = (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"\bsk-(?:proj-|live-)?[A-Za-z0-9_-]{16,}",
                   r"\b(?:api[_-]?key|access[_-]?token|authorization)\s*[:=]\s*[^\s,;]{8,}",
                   r"\b(?:\d{1,3}\.){3}\d{1,3}\b", r"/(?:Users|home|root|tmp)/", r"ssh-(?:rsa|ed25519)\s")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError(f"Duplicate JSON key: {key}")
            value[key] = item
        return value
    def bad(value):
        raise ValueError(f"Nonfinite JSON value: {value}")
    return json.loads(Path(path).read_text(), object_pairs_hook=pairs, parse_constant=bad)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe_file(root, name):
    relative = PurePosixPath(str(name))
    require(not relative.is_absolute() and ".." not in relative.parts and "\\" not in str(relative), "Unsafe artifact path")
    root = Path(root).resolve()
    path = root.joinpath(*relative.parts)
    require(path.is_file() and path.resolve().is_relative_to(root), f"Missing/escaping artifact: {name}")
    require(not any(p.is_symlink() for p in (path, *path.parents) if p != root and root in p.parents), "Symlink artifact rejected")
    return path


def check_inventory(root, expected):
    require(isinstance(expected, dict) and expected, "Missing checksum inventory")
    for name, checksum in expected.items():
        require(isinstance(checksum, str) and re.fullmatch(r"[0-9a-f]{64}", checksum), "Invalid checksum")
        require(sha(safe_file(root, name)) == checksum, f"Artifact checksum differs: {name}")


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def select(entry, fields):
    return {name: entry[name] for name in fields}


def validate_replay(replay):
    replay = Path(replay).resolve()
    manifest = read_json(safe_file(replay, "REPLAY_MANIFEST.json"))
    require(manifest.get("schema_version") == "research-artifact-replay-v1" and manifest.get("status") == "PASS"
            and manifest.get("phase") == "complete", "A complete replay PASS is required")
    require(manifest.get("synthetic") is False and manifest.get("campaign_id") == CAMPAIGN_ID,
            "Only the genuine replacement campaign may be published")
    require(manifest.get("source_commit") == SOURCE_COMMIT and manifest.get("source_tree_sha256") == SOURCE_TREE
            and len(manifest.get("source_files_sha256", {})) == 52, "Frozen 52-file C84 identity differs")
    source_map = (json.dumps(manifest["source_files_sha256"], indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    require(hashlib.sha256(source_map).hexdigest() == SOURCE_TREE, "Frozen source map checksum differs")
    identity_module = module("h100_web_campaign_identity", REPORTING / "campaign_identity.py")
    identity = identity_module.registered_identity(manifest.get("campaign_sha256"), manifest.get("prestart_sha256"))
    require(manifest.get("registered_campaign_identity") == identity, "Replay does not bind the current registered identity")
    for field, filename in (("replay_code_sha256", "replay.py"), ("auditor_source_sha256", "audit_complete.py"),
                            ("renderer_sha256", "render.py")):
        require(manifest.get(field) == sha(REPORTING / filename), f"Reporting source differs: {filename}")
    check_inventory(replay, manifest.get("output_files_sha256"))
    require({c.get("name") for c in manifest.get("commands", [])} >= {"validation", "analysis", "complete_audit", "rendering"}
            and all(c.get("returncode") == 0 and c.get("status") == "PASS" for c in manifest["commands"]),
            "Replay commands did not all succeed")
    audit_ref = manifest.get("complete_audit", {})
    require(audit_ref.get("path") == "complete-audit/AUDIT_COMPLETE.json" and audit_ref.get("status") == "PASS", "Missing complete audit binding")
    audit_path = safe_file(replay, audit_ref["path"])
    audit = read_json(audit_path)
    require(sha(audit_path) == audit_ref.get("sha256") and audit.get("schema_version") == "research-complete-audit-v1"
            and audit.get("status") == "PASS" and audit.get("synthetic") is False
            and audit.get("campaign_sha256") == manifest["campaign_sha256"]
            and audit.get("source_sha256") == manifest["auditor_source_sha256"]
            and audit.get("counts") == EXPECTED_COUNTS and audit.get("findings") == []
            and len(audit.get("sessions", [])) == 400 and all(s.get("status") == "PASS" for s in audit["sessions"]),
            "Complete audit must PASS all 400 sessions and exact output counts")
    require(audit.get("registered_campaign_identity") == identity, "Audit registered identity differs")
    validation_path = safe_file(replay, "validation.json")
    validation = read_json(validation_path)
    require(validation.get("status") == "PASS" and validation.get("campaign_sha256") == manifest["campaign_sha256"]
            and validation.get("journal_sha256") == audit.get("journal_sha256")
            and validation.get("planned_sessions") == validation.get("audited_sessions") == 400
            and validation.get("successful_outputs_compared") == 9360
            and not validation.get("pending_sessions") and not validation.get("failed_sessions"), "Final validation is incomplete")
    renderer = module("h100_web_research_renderer", REPORTING / "render.py")
    data = renderer.load_analysis(replay / "analysis")
    report = data["report"]
    require(not data["synthetic"] and report.get("campaign_id") == CAMPAIGN_ID
            and report.get("campaign_sha256") == manifest["campaign_sha256"]
            and report.get("validation_sha256") == sha(validation_path)
            and report.get("analysis_source_sha256") == manifest["source_files_sha256"]["research_analysis.py"],
            "Analysis identity/validation binding differs")
    render_ref = manifest.get("render_manifest", {})
    require(render_ref.get("path") == "figures/RENDER_MANIFEST.json", "Unexpected render manifest path")
    render_path = safe_file(replay, render_ref["path"])
    rendered = read_json(render_path)
    require(sha(render_path) == render_ref.get("sha256") and rendered.get("schema_version") == "research-reporting-v1"
            and rendered.get("synthetic") is False and rendered.get("campaign_id") == CAMPAIGN_ID
            and rendered.get("campaign_sha256") == manifest["campaign_sha256"]
            and rendered.get("renderer_sha256") == manifest["renderer_sha256"]
            and rendered.get("input_files_sha256") == data["inputs"]
            and rendered.get("primary_test_family_size") == 26, "Rendered provenance differs")
    check_inventory(replay / "figures", rendered.get("output_files_sha256"))
    for stem in FIGURES:
        for extension in ("png", "svg", "pdf"):
            require(f"{stem}.{extension}" in rendered["output_files_sha256"], "All figure formats are required")
    return manifest, audit, data, renderer


def validate_paper(paper, paper_manifest, replay_manifest, campaign_sha256):
    paper = Path(paper).resolve()
    require(paper.is_file() and not paper.is_symlink() and paper.read_bytes().startswith(b"%PDF-"), "A final paper PDF is required")
    metadata = read_json(paper_manifest)
    require(metadata.get("schema_version") == "h100-paper-build-v1" and metadata.get("status") == "PASS"
            and metadata.get("final") is True and metadata.get("synthetic") is False
            and metadata.get("campaign_id") == CAMPAIGN_ID and metadata.get("author") == "Daniel Ejiofor"
            and not metadata.get("affiliation") and metadata.get("paper_sha256") == sha(paper)
            and metadata.get("campaign_sha256") == campaign_sha256
            and metadata.get("replay_manifest_sha256") == replay_manifest
            and metadata.get("public_sanitization", {}).get("status") == "PASS",
            "Final paper manifest must bind the author, sanitized PDF and exact real replay")
    paper_source = ROOT / "publication" / "h100-confirmatory"
    require(metadata.get("builder_sha256") == sha(paper_source / "paper" / "build_paper.py")
            and metadata.get("manuscript_sha256") == sha(paper_source / "MANUSCRIPT.md")
            and metadata.get("references_sha256") == sha(paper_source / "references.md"), "Final paper source identity differs")
    require(metadata.get("output_files_sha256", {}).get(paper.name) == sha(paper), "Final paper checksum inventory differs")
    return metadata


def scan_public_text(text, name):
    for pattern in SECRET_PATTERNS:
        require(not re.search(pattern, text, re.I), f"Private/credential-like text in public asset: {name}")


def public_data(manifest, audit, data, renderer):
    report = data["report"]
    primary_fields = ("contrast_id", "numerator", "denominator", "suite", "scale", "geometric_mean_speedup",
                      "ci95_pointwise", "n_blocks", "p_raw", "p_holm", "holm_reject_two_sided_005")
    query_fields = ("contrast_id", "numerator", "denominator", "suite", "scale", "query", "endpoint", "status",
                    "n_blocks", "geometric_mean_speedup", "ci95_pointwise")
    queries = []
    for suite, scale in renderer.CELLS:
        for denominator in ("spark_gpu", "polars_gpu"):
            for query in renderer.QUERIES[suite]:
                row = data["exploratory"][("polars_cpu_200", denominator, suite, scale, query, "seconds")]
                queries.append({field: row.get(field) for field in query_fields})
    block_ratios = [{"contrast_id": contrast, "block_id": block, "ratio": row["ratio"]}
                    for (contrast, block), row in data["raw"].items()]
    return {"schema_version": "h100-publication-data-v1", "status": "PASS", "final": True, "synthetic": False,
            "author": "Daniel Ejiofor", "campaign_id": CAMPAIGN_ID,
            "campaign_sha256": manifest["campaign_sha256"], "prestart_sha256": manifest["prestart_sha256"],
            "source_commit": SOURCE_COMMIT, "source_tree_sha256": SOURCE_TREE,
            "analysis_sha256": data["inputs"]["analysis.json"], "counts": audit["counts"], "configs": CONFIGS,
            "primary": [select(row, primary_fields) for row in report["primary"]],
            "block_ratios": block_ratios, "queries": queries, "scores": renderer.complete_case_rows(data),
            "completed_at_utc": manifest["ended_at_utc"]}


def write_csv(path, rows, fields=None):
    rows = list(rows)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_templates(output, preview=False):
    for path in (HERE / "templates").iterdir():
        if path.is_file():
            target = output / path.name
            text = path.read_text()
            text = text.replace("__PREVIEW_MODE__", "true" if preview else "false")
            text = text.replace("__ROBOTS__", "noindex,nofollow" if preview else "index,follow")
            scan_public_text(text, path.name)
            target.write_text(text)


def build(replay, paper, paper_manifest, output):
    output = Path(output).absolute()
    require(not output.exists(), "Refusing to overwrite publication output; build into a fresh directory")
    manifest, audit, data, renderer = validate_replay(replay)
    replay_sha = sha(Path(replay) / "REPLAY_MANIFEST.json")
    validate_paper(paper, paper_manifest, replay_sha, manifest["campaign_sha256"])
    payload = public_data(manifest, audit, data, renderer)
    # Create no visible output until every gate and allowlisted public asset passes.
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".h100-web-", dir=output.parent) as temporary:
        stage = Path(temporary)
        write_templates(stage)
        (stage / "assets").mkdir()
        downloads = stage / "downloads"
        downloads.mkdir()
        (stage / "data.json").write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
        for stem in FIGURES:
            for extension in ("png", "svg", "pdf"):
                target = stage / "assets" / f"{stem}.{extension}"
                shutil.copyfile(Path(replay) / "figures" / target.name, target)
        for name in PUBLIC_CSVS:
            shutil.copyfile(Path(replay) / "figures" / name, downloads / name)
        write_csv(downloads / "paired_block_ratios.csv", payload["block_ratios"])
        query_rows = [{**row, "ci95_pointwise": json.dumps(row["ci95_pointwise"])} for row in payload["queries"]]
        write_csv(downloads / "descriptive_query_ratios.csv", query_rows)
        shutil.copyfile(paper, downloads / "h100-study.pdf")
        # Raw replay/audit JSON contains local paths and environment observations;
        # publish a narrow provenance statement instead of copying those artifacts.
        provenance = {key: payload[key] for key in ("campaign_id", "campaign_sha256", "prestart_sha256", "source_commit",
                                                   "source_tree_sha256", "analysis_sha256", "counts")}
        provenance.update(replay_status="PASS", complete_audit_status="PASS", replay_manifest_sha256=replay_sha,
                          paper_sha256=sha(paper), public_scope="Sanitized numerical tables, figures, paper and provenance; private source and raw infrastructure artifacts are excluded.")
        (downloads / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
        for path in stage.rglob("*"):
            if path.is_file() and path.suffix in {".json", ".csv", ".html", ".css", ".js", ".svg"}:
                scan_public_text(path.read_text(), str(path.relative_to(stage)))
        public_manifest = {"schema_version": "h100-publication-web-v1", "status": "PASS", "final": True, "synthetic": False,
                           "campaign_id": CAMPAIGN_ID, "campaign_sha256": manifest["campaign_sha256"],
                           "author": "Daniel Ejiofor", "replay_manifest_sha256": replay_sha,
                           "paper_sha256": sha(paper), "builder_sha256": sha(Path(__file__)),
                           "output_files_sha256": {p.relative_to(stage).as_posix(): sha(p) for p in sorted(stage.rglob("*")) if p.is_file()}}
        (stage / "PUBLICATION_MANIFEST.json").write_text(json.dumps(public_manifest, indent=2, sort_keys=True) + "\n")
        shutil.copytree(stage, output)
    return public_manifest


def preview(output):
    output = Path(output).absolute()
    require(not output.exists(), "Refusing to overwrite preview output")
    require(not output.is_relative_to(ROOT / ".sites-checkout-h100-publication"), "An unpublished preview cannot be written into the Site checkout")
    output.mkdir(parents=True)
    write_templates(output, preview=True)
    result = {"schema_version": "h100-publication-preview-v1", "status": "UNPUBLISHED", "final": False,
              "campaign_id": CAMPAIGN_ID, "reason": "Methods-only preview. No performance observations, estimates or publication approval."}
    (output / "PREVIEW_ONLY.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    final = commands.add_parser("publish", help="Build release-ready files; does not deploy")
    for option in ("replay", "paper", "paper-manifest", "output"):
        final.add_argument("--" + option, type=Path, required=True)
    draft = commands.add_parser("preview", help="Local methods-only preview with no result data")
    draft.add_argument("--output", type=Path, default=HERE / "preview")
    args = parser.parse_args()
    try:
        result = preview(args.output) if args.command == "preview" else build(args.replay, args.paper, args.paper_manifest, args.output)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f"H100 webpage rejected: {exc}\n")
    print(json.dumps({key: result[key] for key in ("schema_version", "status", "campaign_id")}))


if __name__ == "__main__":
    main()
