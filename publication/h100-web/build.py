#!/usr/bin/env python3
"""Build a public H100 study only from a complete, genuine, audited replay.

No synthetic opt-in exists here. `preview` writes a conspicuous methods-only
local preview; `publish` verifies every gate before creating any output file.
Only allowlisted fields and assets cross the private/public boundary.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import importlib.util
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REPORTING = ROOT / "research_reporting"
CAMPAIGN_ID = "h100-confirmatory-gclocker100-20261027"
SOURCE_COMMIT = "c84ef6a07341f350dae6636ab709c1b65fd894e0"
SOURCE_TREE = "9514f0f7ce384763d0a675a9075666707e0edabcc003c729a3e0735a52ea25d8"
CONFIGS = {"polars_cpu_144": "CPU Polars 1.44.2", "polars_cpu_200": "CPU Polars 2.0.0",
           "polars_gpu": "Polars GPU 1.44.2", "spark_cpu": "CPU Spark 3.5.9", "spark_gpu": "GPU Spark 3.5.9"}
EXPECTED_COUNTS = {"sessions": 400, "successful_outputs": 9360, "warmup_outputs": 2340,
                   "measured_outputs": 7020, "translation_rejections": 60,
                   "successful_complex_cleanups": 3360, "failed_complex_cleanups": 60}
FIGURES = ("suite_ratios_baseline", "suite_ratios_complex", "query_ratios_action", "complete_case_scores")
PUBLIC_CSVS = ("primary_contrasts.csv", "complete_case_scores.csv")
CONTENTION_ID = "h100-contention-20261028"
CONTENTION_PLAN = "670f37af6c1a6f2dbea5fc110d213b148a0acb83858786a21add8252acc89031"
CONTENTION_PRESTART = "4088b5302a7d42c8aa87ebaed06e229bed3b0783bd15cf6556a6110981bf9f25"
CONTENTION_REFERENCE = "3142874b918a2df2d66c8dfb32c26f59a09353edcb4cb73e51c4ba46ff387641"
CONTENTION_PROFILE = "a07569ffa3be9cd138ed63084ff59ed668b8bceeef355b5a4f63adef53464c69"
CONTENTION_PAIRS = {"spark_gpu__polars_cpu_200": "A", "spark_cpu__polars_gpu": "B"}
CONTENTION_QUERIES = {"baseline": {"q01", "q03", "q06", "q10", "q12", "q14"},
                      "complex": {"cx_q09", "cx_q13", "cx_semi_anti", "cx_window", "cx_distinct", "cx_self_join"}}
CONTENTION_FIGURES = tuple(f"{phase}_{stem}" for phase in ("pilot", "main") for stem in
                          ("pair_A_common_actions", "pair_B_common_actions", "actual_action_timeline"))
SECRET_PATTERNS = (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"\bsk-(?:proj-|live-)?[A-Za-z0-9_-]{16,}",
                   r"\b(?:api[_-]?key|access[_-]?token|authorization)\s*[:=]\s*[^\s,;]{8,}",
                   r"\b(?:\d{1,3}\.){3}\d{1,3}\b", r"/(?:Users|home|root|tmp)/", r"ssh-(?:rsa|ed25519)\s")


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
            "Only the genuine recovery campaign may be published")
    require(manifest.get("source_commit") == SOURCE_COMMIT and manifest.get("source_tree_sha256") == SOURCE_TREE
            and len(manifest.get("source_files_sha256", {})) == 52, "Frozen 52-file C84 identity differs")
    source_map = (json.dumps(manifest["source_files_sha256"], indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    require(hashlib.sha256(source_map).hexdigest() == SOURCE_TREE, "Frozen source map checksum differs")
    identity_module = module("h100_web_campaign_identity", REPORTING / "campaign_identity.py")
    identity = identity_module.registered_identity(manifest.get("campaign_sha256"), manifest.get("prestart_sha256"))
    require(identity.get("campaign_id") == CAMPAIGN_ID
            and re.fullmatch(r"[0-9a-f]{64}", identity.get("execution_profile_sha256", "")),
            "Only the registered recovery campaign/profile may be published")
    require(manifest.get("registered_campaign_identity") == identity, "Replay does not bind the current registered identity")
    for field, filename in (("replay_code_sha256", "replay.py"), ("auditor_source_sha256", "audit_complete.py"),
                            ("renderer_sha256", "render.py")):
        require(manifest.get(field) == sha(REPORTING / filename), f"Reporting source differs: {filename}")
    check_inventory(replay, manifest.get("output_files_sha256"))
    require([c.get("name") for c in manifest.get("commands", [])] == ["validation", "analysis", "complete_audit", "rendering"]
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
    validate_recovery_profile(audit, identity)
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


def validate_recovery_profile(audit, identity):
    """Require the exact receipt/log proof produced by the current complete auditor."""
    expected = {"path": "EXECUTION_PROFILE.json", "sha256": identity["execution_profile_sha256"],
                "spark_driver_logs": 160, "profile_gates": 24, "status": "PASS"}
    require(audit.get("execution_profile") == expected,
            "Recovery profile audit must verify all 160 Spark drivers and 24 profile gates")


def validate_paper(paper, paper_manifest, replay_manifest, campaign_sha256, execution_profile_sha256=None,
                   contention_review=None):
    paper = Path(paper).resolve()
    require(paper.is_file() and not paper.is_symlink() and paper.read_bytes().startswith(b"%PDF-"), "A final paper PDF is required")
    metadata = read_json(paper_manifest)
    require(metadata.get("schema_version") == "h100-paper-build-v1" and metadata.get("status") == "PASS"
            and metadata.get("final") is True and metadata.get("synthetic") is False
            and metadata.get("campaign_id") == CAMPAIGN_ID and metadata.get("author") == "Daniel Ejiofor"
            and not metadata.get("affiliation") and metadata.get("paper_sha256") == sha(paper)
            and metadata.get("campaign_sha256") == campaign_sha256
            and re.fullmatch(r"[0-9a-f]{64}", execution_profile_sha256 or "")
            and metadata.get("execution_profile_sha256") == execution_profile_sha256
            and metadata.get("replay_manifest_sha256") == replay_manifest
            and metadata.get("public_sanitization", {}).get("status") == "PASS",
            "Final paper manifest must bind the author, sanitized PDF and exact real replay")
    paper_source = ROOT / "publication" / "h100-confirmatory"
    require(metadata.get("builder_sha256") == sha(paper_source / "paper" / "build_paper.py")
            and metadata.get("manuscript_sha256") == sha(paper_source / "MANUSCRIPT.md")
            and metadata.get("references_sha256") == sha(paper_source / "references.md"), "Final paper source identity differs")
    require(metadata.get("output_files_sha256", {}).get(paper.name) == sha(paper), "Final paper checksum inventory differs")
    require(contention_review is not None and metadata.get("contention_review") == contention_review,
            "Final paper must bind the exact terminal contention_review")
    return metadata


def contention_disposition_gate(report):
    require(report.get("schema_version") == "h100-contention-replay-v1" and report.get("synthetic") is False
            and report.get("experiment_id") == CONTENTION_ID and report.get("plan_sha256") == CONTENTION_PLAN
            and report.get("prestart_sha256") == CONTENTION_PRESTART
            and report.get("reference_campaign_sha256") == CONTENTION_REFERENCE
            and report.get("reference_execution_profile_sha256") == CONTENTION_PROFILE,
            "Only the genuine separately registered contention role may be published")
    require(report.get("status") in {"COMPLETE", "STOPPED_WITH_FAILURE"}
            and report.get("integrity_status") == "PASS" and report.get("publication_ready") is True
            and report.get("findings") == [], "Contention requires a terminal publication-ready integrity PASS")
    handoff = report.get("handoff", {})
    require(handoff.get("status") == "PASS" and handoff.get("same_scheduler_resumed") is True
            and handoff.get("original_payload_verified") is True and handoff.get("no_action_overlap") is True
            and report.get("terminal_driver", {}).get("terminal") is True,
            "Contention requires actual original payload, separation and same-scheduler resume proof")


def validate_contention_replay(directory):
    directory = Path(directory).resolve()
    report = read_json(safe_file(directory, "CONTENTION_REPLAY.json"))
    contention_disposition_gate(report)
    # Reuse the current figure loader's fresh offline audit, including its exact
    # source, canonical, read-input, CSV, handoff and terminal-driver comparison.
    # A self-consistent forged receipt cannot replace actual terminal evidence.
    module("contention_replay", REPORTING / "contention_replay.py")
    verifier = module("h100_web_contention_verifier", REPORTING / "contention_figures.py")
    data = verifier.load_audit(directory, draft=False)
    require(data["report"] == report and data["final"] is True, "Contention audit changed while loading")
    review = {"status": "COMPLETE", "experiment_id": CONTENTION_ID, "plan_sha256": CONTENTION_PLAN,
              "prestart_sha256": CONTENTION_PRESTART, "replay_sha256": sha(directory / "CONTENTION_REPLAY.json"),
              "disposition": report["status"]}
    return data, review


def validate_contention_figures(directory, contention, review):
    if directory is None:
        return None
    directory = Path(directory).resolve()
    manifest = read_json(safe_file(directory, "FIGURE_MANIFEST.json"))
    report = contention["report"]
    require(manifest.get("schema_version") == "h100-contention-figures-v1" and manifest.get("draft") is False
            and manifest.get("final") is True and manifest.get("publication_ready") is True
            and manifest.get("synthetic") is False and manifest.get("audit_publication_ready") is True
            and manifest.get("audit_status") == review["disposition"]
            and manifest.get("plan_sha256") == CONTENTION_PLAN and manifest.get("prestart_sha256") == CONTENTION_PRESTART,
            "Contention figures must be genuine final publication-ready exports")
    contention["inputs"].file(REPORTING / "contention_figures.py")
    require(manifest.get("auditor_source_sha256") == report["audit_source_sha256"]
            and manifest.get("renderer_source_sha256") == sha(REPORTING / "contention_figures.py")
            and manifest.get("input_files_sha256") == contention["inputs"].hashes
            and manifest.get("reproduced_audit_input_files_sha256") == report["input_files_sha256"]
            and manifest.get("audit_counts") == report["counts"], "Contention figure source/input bindings differ")
    allowed = {f"{stem}.{ext}" for stem in contention_figure_stems(report) for ext in ("png", "svg", "pdf")} | {"CAPTIONS.md"}
    require(set(manifest.get("output_files_sha256", {})) == allowed, "Contention figure inventory differs")
    check_inventory(directory, manifest["output_files_sha256"])
    return manifest


def contention_figure_stems(report):
    # A terminal pilot failure may leave the expansion genuinely unattempted.
    # Match the renderer's observed phases without inventing missing figures.
    phases = {wave["wave_id"].split("-", 1)[0] for wave in report["waves"]}
    return tuple(stem for stem in CONTENTION_FIGURES if stem.split("_", 1)[0] in phases)


def number(value, label, positive=False):
    require(not isinstance(value, bool), "Invalid numeric " + label)
    result = float(value)
    require(math.isfinite(result) and (result > 0 if positive else result >= 0), "Invalid numeric " + label)
    return result


def csv_boolean(value):
    require(value in ("True", "False"), "Invalid contention boolean")
    return value == "True"


def public_contention(data, review, figures=None):
    """Typed numerical allowlist: never expose raw receipts, errors or host paths."""
    report, plan = data["report"], data["plan"]
    waves = {w["wave_id"]: (phase, w) for phase in ("pilot", "main") for w in plan[phase]}
    def context(row):
        require(row["wave_id"] in waves, "Unknown contention wave")
        phase, wave = waves[row["wave_id"]]
        require(re.fullmatch(r"(?:pilot|main)-\d{2}-(?:spark_gpu__polars_cpu_200|spark_cpu__polars_gpu)-(?:baseline|complex)-sf(?:30|100)-r[12]", row["wave_id"]),
                "Invalid public contention wave label")
        require(row["query"] in CONTENTION_QUERIES[wave["suite"]], "Unknown contention query")
        return {"study_phase": phase, "wave_id": row["wave_id"], "pair": CONTENTION_PAIRS[wave["pair"]],
                "suite": wave["suite"], "scale": int(wave["scale"]), "repetition": wave["repetition"], "query": row["query"]}
    actions = []
    for row in data["actions"]:
        item = context(row)
        wave = waves[row["wave_id"]][1]
        require(row["config_id"] in wave["participants"] and row["phase"] in {"warmup", "measured"}
                and row["trial"] == ("0" if row["phase"] == "warmup" else "1")
                and row["pair"] == wave["pair"] and row["suite"] == wave["suite"]
                and str(row["scale"]) == str(wave["scale"]) and int(row["repetition"]) == wave["repetition"],
                "Contention action labels differ from the genuine plan")
        item.update(config_id=row["config_id"], phase=row["phase"], trial=int(row["trial"]),
                    seconds=number(row["seconds"], "action duration", True),
                    host_result_seconds=number(row["host_result_seconds"], "host-result duration", True),
                    paired_condition=csv_boolean(row["paired_condition"]), oracle_passed=csv_boolean(row["oracle_passed"]))
        for key in ("action_started_at_utc", "action_ended_at_utc", "host_result_ended_at_utc"):
            timestamp = datetime.fromisoformat(row[key])
            require(timestamp.tzinfo is not None, "Contention endpoint needs an explicit timezone")
            item[key] = timestamp.isoformat()
        actions.append(item)
    overlaps = []
    for row in data["overlaps"]:
        item = context(row)
        require(row["phase"] == "measured" and row["trial"] == "1", "Invalid overlap endpoint")
        item.update({key: number(row[key], key) for key in ("overlap_seconds", "utc_overlap_seconds", "actual_start_skew_seconds", "left_seconds", "right_seconds")})
        item["positive_actual_overlap"] = csv_boolean(row["positive_actual_overlap"])
        overlaps.append(item)
    dispositions = []
    for phase in ("pilot", "main"):
        value = report["disposition"][phase]
        require(value["status"] in {"PASS", "FAILED", "FAIL", "NOT_ATTEMPTED_AFTER_STOP"}, "Invalid terminal phase disposition")
        unattempted = value["unattempted_waves"]
        require(all(w in waves and waves[w][0] == phase for w in unattempted), "Unknown unattempted wave")
        dispositions.append({"study_phase": phase, "status": value["status"], "unattempted_waves": unattempted,
                             **{key: value[key] for key in ("planned_waves", "attempted_waves", "passed_waves", "failed_waves")}})
    counts = report["counts"]
    require(all(type(v) is int and v >= 0 for v in counts.values()), "Invalid contention counts")
    return {"review": review, "counts": select(counts, ("attempted_waves", "successful_outputs", "oracle_failed_outputs",
             "expected_strict_rejections_observed", "unexpected_query_errors_observed", "common_measured_pairs", "unpaired_cpu_measured_diagnostics")),
            "disposition": dispositions, "actions": actions, "overlaps": overlaps,
            "figure_stems": list(contention_figure_stems(report)) if figures else []}


def scan_public_text(text, name):
    # This exact documented Java version resembles an IPv4 address. Permit
    # only its explicit software label; bare addresses still fail the scan.
    text = text.replace("Java 17.0.20.1", "Java 17 update 20 build 1")
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
            "execution_profile_sha256": audit["execution_profile"]["sha256"],
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


def build(replay, paper, paper_manifest, output, contention_replay=None, contention_figures=None):
    output = Path(output).absolute()
    require(not output.exists(), "Refusing to overwrite publication output; build into a fresh directory")
    manifest, audit, data, renderer = validate_replay(replay)
    replay_sha = sha(Path(replay) / "REPLAY_MANIFEST.json")
    require(contention_replay is not None, "A final --contention-replay is mandatory")
    contention, contention_review = validate_contention_replay(contention_replay)
    contention_render = validate_contention_figures(contention_figures, contention, contention_review)
    validate_paper(paper, paper_manifest, replay_sha, manifest["campaign_sha256"],
                   audit["execution_profile"]["sha256"], contention_review)
    payload = public_data(manifest, audit, data, renderer)
    payload["contention"] = public_contention(contention, contention_review, contention_render)
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
        write_csv(downloads / "contention_query_actions.csv", payload["contention"]["actions"],
                  ("study_phase", "wave_id", "pair", "suite", "scale", "repetition", "query", "config_id", "phase", "trial",
                   "seconds", "host_result_seconds", "paired_condition", "oracle_passed", "action_started_at_utc", "action_ended_at_utc", "host_result_ended_at_utc"))
        write_csv(downloads / "contention_common_overlap.csv", payload["contention"]["overlaps"],
                  ("study_phase", "wave_id", "pair", "suite", "scale", "repetition", "query", "overlap_seconds", "utc_overlap_seconds",
                   "actual_start_skew_seconds", "left_seconds", "right_seconds", "positive_actual_overlap"))
        if contention_render:
            for stem in payload["contention"]["figure_stems"]:
                for extension in ("png", "svg", "pdf"):
                    name = f"{stem}.{extension}"
                    target = stage / "assets" / name
                    shutil.copyfile(Path(contention_figures) / name, target)
                    require(sha(target) == contention_render["output_files_sha256"][name], "Contention figure changed during copy")
        shutil.copyfile(paper, downloads / "h100-study.pdf")
        # Raw replay/audit JSON contains local paths and environment observations;
        # publish a narrow provenance statement instead of copying those artifacts.
        provenance = {key: payload[key] for key in ("campaign_id", "campaign_sha256", "prestart_sha256", "execution_profile_sha256", "source_commit",
                                                   "source_tree_sha256", "analysis_sha256", "counts")}
        provenance.update(replay_status="PASS", complete_audit_status="PASS", replay_manifest_sha256=replay_sha,
                          contention_review=contention_review,
                          contention_auditor_sha256=contention["report"]["audit_source_sha256"],
                          contention_csv_sha256={name: sha(Path(contention_replay) / name) for name in
                                                 ("QUERY_ACTIONS_DESCRIPTIVE.csv", "COMMON_QUERY_OVERLAP.csv")},
                          paper_sha256=sha(paper), public_scope="Sanitized numerical tables, figures, paper and provenance; private source and raw infrastructure artifacts are excluded.")
        provenance["public_contention_csv_sha256"] = {name: sha(downloads / name) for name in
                                                     ("contention_query_actions.csv", "contention_common_overlap.csv")}
        if contention_render:
            provenance["contention_figure_manifest_sha256"] = sha(Path(contention_figures) / "FIGURE_MANIFEST.json")
        (downloads / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
        for path in stage.rglob("*"):
            if path.is_file() and path.suffix in {".json", ".csv", ".html", ".css", ".js", ".svg"}:
                scan_public_text(path.read_text(), str(path.relative_to(stage)))
        contention["inputs"].recheck()
        public_manifest = {"schema_version": "h100-publication-web-v1", "status": "PASS", "final": True, "synthetic": False,
                           "campaign_id": CAMPAIGN_ID, "campaign_sha256": manifest["campaign_sha256"],
                           "execution_profile_sha256": audit["execution_profile"]["sha256"],
                           "author": "Daniel Ejiofor", "replay_manifest_sha256": replay_sha,
                           "contention_review": contention_review,
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
    for option in ("replay", "contention-replay", "paper", "paper-manifest", "output"):
        final.add_argument("--" + option, type=Path, required=True)
    final.add_argument("--contention-figures", type=Path, help="Optional final hash-bound contention figures")
    draft = commands.add_parser("preview", help="Local methods-only preview with no result data")
    draft.add_argument("--output", type=Path, default=HERE / "preview")
    args = parser.parse_args()
    try:
        result = preview(args.output) if args.command == "preview" else build(args.replay, args.paper, args.paper_manifest,
                    args.output, args.contention_replay, args.contention_figures)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f"H100 webpage rejected: {exc}\n")
    print(json.dumps({key: result[key] for key in ("schema_version", "status", "campaign_id")}))


if __name__ == "__main__":
    main()
