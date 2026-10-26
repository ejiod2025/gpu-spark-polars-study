#!/usr/bin/env python3
"""Export an allowlisted, portable benchmark evidence package (stdlib only).

Example from the benchmark directory:
  python publication/export_bundle.py --results results/production-20261025 \
      results/large-sf100-20261025 results/large-sf300-20261025 \
      --output publication/dist/reproducibility
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import ipaddress
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import statistics
import tempfile
import zipfile


BENCHMARK = Path(__file__).resolve().parent.parent
CODE = (
    "workloads.py", "complex_workloads.py", "complex_joins.py", "complex_analytics.py",
    "complex_oracle.py", "generate_data.py", "generate_large_data.py", "worker.py",
    "run_experiment.py", "analyze_results.py", "analyze_complex.py", "analyze_crossover.py",
    "query_cleanup.py", "prove_query_cleanup.py",
    "inspect_spark_metrics.py", "plot_results.py", "plot_crossover.py", "plot_complex.py",
)
EXTRA_DOCUMENTS = ("publication/ARTICLE_DRAFT.md", "publication/COMPLEX_PLAN_NOTES.md")
EXTRA_EVIDENCE = (
    "artifacts/complex/complex-oracle-sf1.json",
    "artifacts/complex/query-cleanup-proof.json",
    "artifacts/complex/postrun-input-integrity.json",
)
EXTRA_SOURCES = {
    "artifacts/complex/verify_input_integrity.py": "validation/verify_input_integrity.py",
}
SPARK_CONF = {
    "spark.master", "spark.submit.deployMode", "spark.driver.memory",
    "spark.driver.maxResultSize", "spark.executor.memory", "spark.executor.cores",
    "spark.executor.instances", "spark.task.cpus", "spark.default.parallelism",
    "spark.sql.shuffle.partitions", "spark.sql.adaptive.enabled",
    "spark.sql.files.maxPartitionBytes", "spark.sql.session.timeZone",
    "spark.plugins", "spark.sql.extensions", "spark.serializer",
    "spark.serializer.objectStreamReset", "spark.rdd.compress", "spark.ui.enabled",
    "spark.eventLog.enabled", "spark.rapids.sql.enabled", "spark.rapids.sql.explain",
    "spark.rapids.sql.concurrentGpuTasks", "spark.rapids.memory.pinnedPool.size",
    "spark.rapids.sql.variableFloatAgg.enabled",
    "spark.rapids.sql.multiThreadedRead.memoryLimit.size",
}
SESSION_FIELDS = {
    "engine", "suite", "suite_version", "query_descriptions", "started_at_utc",
    "completed_at_utc", "failed_at_utc", "failure", "scale_factor", "manifest_sha256",
    "runs", "queries", "cache_policy", "timing_scope", "polars_execution_engine",
    "version", "cpu_threads", "session_and_table_setup_seconds",
    "launch_to_engine_ready_seconds", "query_execution_status", "query_errors",
    "query_cleanup_policy", "cleanup_records", "cleanup_failure",
    "failed_query_cleanup_records", "cleanup_failures", "query_failures",
}
CLEANUP_FIELDS = {
    "query", "phase", "trial", "policy", "status", "seconds", "engine",
    "started_at_utc", "ended_at_utc", "owned_blockmgr_dirs", "spark_cleanup_applicable",
    "gc_requests", "python_gc_runs", "python_gc_collected",
}
SNAPSHOT_FIELDS = {"shuffle_files", "shuffle_bytes", "root_free_bytes", "active_job_ids"}
RECORD_FIELDS = {
    "query", "phase", "trial", "seconds", "rows", "started_at_utc", "ended_at_utc",
    "peak_process_tree_rss_bytes", "plan", "gpu_plan_observed", "gpu_operators",
    "cpu_core_operators", "gpu_execution_class",
}
ARGUMENT_FIELDS = {
    "scales", "engines", "runs", "cpus", "driver_memory", "queries", "suite",
    "keep_going", "engine_timeout_seconds",
    "query_cleanup", "cleanup_timeout_seconds",
}
SCHEDULE_FIELDS = {
    "scale", "engine", "preheat", "start_utc", "process_wall_seconds", "returncode",
    "status", "failure_reason",
}
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def read_json(path: Path):
    return json.loads(path.read_text())


def select(value: dict, fields: set) -> dict:
    return {key: value[key] for key in sorted(fields) if key in value}


def relative_file(parent: Path, relative: str) -> Path:
    name = PurePosixPath(relative)
    if name.is_absolute() or ".." in name.parts or "\\" in relative:
        raise ValueError("Evidence contains an unsafe relative file reference")
    path = parent.joinpath(*name.parts)
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(parent.resolve()):
        raise ValueError("Evidence file is missing, linked, or outside its result directory")
    return path


class Exporter:
    def __init__(self, staging: Path):
        self.output = staging
        self.files = []
        self.trials = []
        self.runs = []
        self.incidents = []
        self.replacements = {str(BENCHMARK): "<BENCHMARK_ROOT>"}

    def normalize(self, value):
        if isinstance(value, dict):
            return {key: self.normalize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.normalize(item) for item in value]
        if not isinstance(value, str):
            return value
        for original, token in sorted(self.replacements.items(), key=lambda pair: -len(pair[0])):
            if original:
                value = value.replace(original, token)
        value = re.sub(r"GPU-[0-9a-fA-F-]{8,}", "<GPU_UUID>", value)
        value = re.sub(r"\bip-\d+(?:-\d+){3}\b", "<HOSTNAME>", value)
        def replace_ip(match):
            # This recorded Java release has IPv4-shaped version syntax. Keep
            # it only in an explicit version context; a bare address is redacted.
            prefix = value[max(0, match.start() - 48):match.start()]
            if (match.group() == "17.0.20.1"
                    and re.search(r"\b(?:OpenJDK|Java|JDK)(?:\s+version)?\s*[\"'`]?\s*$", prefix, re.I)):
                return match.group()
            try:
                address = ipaddress.ip_address(match.group())
                return match.group() if address.is_loopback or int(address) >> 24 == 0 else "<HOST_IP>"
            except ValueError:
                return match.group()
        value = IPV4.sub(replace_ip, value)
        # Known roots above retain meaningful dataset/plan suffixes. Any remaining
        # machine-local path is replaced in full; web URLs are unaffected.
        return re.sub(
            r"(?:(?:file|local)://)?/(?:Users|home|tmp|var|mnt|opt|usr)/[^\s\"'<>\]),;]*",
            "<ABSOLUTE_PATH>", value,
        )

    def write(self, relative: str, data: bytes, source: Path | None = None,
              source_reference: str | None = None, normalizations=()):
        path = self.output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if source is not None:
            self.files.append({
                "source": source_reference, "original_sha256": sha(source.read_bytes()),
                "public_path": relative, "public_sha256": sha(data),
                "normalizations": list(normalizations),
            })

    def write_json(self, relative, value, source=None, source_reference=None, normalizations=()):
        self.write(relative, json_bytes(self.normalize(value)), source, source_reference, normalizations)

    def cleanup(self, record):
        """Retain barrier observations and replace only owned-directory identities."""
        if record is None:
            return None
        public = select(record, CLEANUP_FIELDS)
        for directory in record.get("owned_blockmgr_dirs", []):
            if directory not in self.replacements:
                count = sum(token.startswith("<OWNED_BLOCKMGR_") for token in self.replacements.values())
                self.replacements[directory] = f"<OWNED_BLOCKMGR_{count + 1}>"
        for phase in ["before", "after"]:
            if phase in record:
                public[phase] = select(record[phase], SNAPSHOT_FIELDS)
        for phase in ["scratch_before", "scratch_after"]:
            if phase in record:
                public[phase] = select(record[phase], {"files", "bytes"})
        return public

    def cleanup_failure(self, failure):
        if failure is None:
            return None
        public = select(failure, {"query", "phase", "trial", "policy", "type", "message"})
        if "record" in failure:
            public["record"] = self.cleanup(failure["record"])
        return public

    def extras(self):
        """Copy only named draft narratives and bounded proof artifacts."""
        documents, evidence, sources, missing = [], [], [], []

        def blockmgr_tokens(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"owned_blockmgr_dirs", "before_blockmgr_dirs"} and isinstance(item, list):
                        for directory in item:
                            if isinstance(directory, str) and directory not in self.replacements:
                                count = sum(token.startswith("<BLOCKMGR_DIR_") for token in self.replacements.values())
                                self.replacements[directory] = f"<BLOCKMGR_DIR_{count + 1}>"
                    blockmgr_tokens(item)
            elif isinstance(value, list):
                for item in value:
                    blockmgr_tokens(item)

        for relative in EXTRA_DOCUMENTS:
            source = BENCHMARK / relative
            if not source.is_file() or source.is_symlink():
                missing.append(relative)
                continue
            self.write(relative, self.normalize(source.read_text()).encode(), source, relative,
                       ("portable path/host tokens only; narrative retained as a draft repository-context snapshot",))
            documents.append({"path": relative, "publication_status": "draft_not_cleared",
                              "context": "Draft narrative snapshot; may precede later experiment completion.",
                              "link_resolution_status": "Repository-context relative links are retained; targets are not all bundled or certified resolved."})
        for relative in EXTRA_EVIDENCE:
            source = BENCHMARK / relative
            if not source.is_file() or source.is_symlink():
                missing.append(relative)
                continue
            value = read_json(source)
            blockmgr_tokens(value)
            self.write_json(relative, value, source, relative,
                            ("canonical JSON serialization", "portable path/host and block-manager directory tokens",
                             "numeric values, oracle rows and source/manifest/query SHA-256 values retained"))
            evidence.append(relative)
        for relative, target in EXTRA_SOURCES.items():
            source = BENCHMARK / relative
            if not source.is_file() or source.is_symlink():
                missing.append(relative)
                continue
            self.write(target, source.read_bytes(), source, relative,
                       ("none; exact validation source bytes",))
            sources.append(target)
        self.write_json("DOCUMENT_CONTEXT.json", {"publication_status": "draft_not_cleared",
                                                  "not_a_clearance_assertion": True,
                                                  "draft_documents": documents,
                                                  "included_extra_evidence": evidence,
                                                  "included_validation_sources": sources,
                                                  "missing_optional_curated_files": missing})
        return {"draft_documents": [item["path"] for item in documents],
                "included_extra_evidence": evidence, "included_validation_sources": sources,
                "missing_optional_curated_files": missing}

    def sources(self, root: Path, label: str, environment: dict):
        recorded = environment.get("source_sha256", {})
        if not recorded:
            raise ValueError(f"Run {label} has no recorded source hashes")
        verified = {}
        snapshots = BENCHMARK / "artifacts" / "source-snapshots"
        for name, digest in sorted(recorded.items()):
            if name not in CODE:
                continue
            # Earlier draft bundles can preserve a source revision predating a
            # later analysis-only edit. The run's recorded hash remains mandatory.
            previous_bundles = BENCHMARK / "publication" / "dist"
            candidates = [BENCHMARK / name, *sorted(snapshots.glob("*/" + name)),
                          *sorted(previous_bundles.glob("*/" + name)),
                          *sorted(previous_bundles.glob("*/source-snapshots/*/" + name))]
            source = next((p for p in candidates if p.is_file() and not p.is_symlink()
                           and sha(p.read_bytes()) == digest), None)
            if source is None:
                raise ValueError(f"No byte-identical archived source for {label}/{name}")
            relative = f"source-snapshots/{label}/{name}"
            self.write(relative, source.read_bytes(), source,
                       source.relative_to(BENCHMARK).as_posix(), ("none; exact recorded source bytes",))
            verified[name] = digest
        if not verified:
            raise ValueError(f"Run {label} has no allowlisted benchmark sources")
        self.write_json(f"source-snapshots/{label}/VERIFIED_SOURCES.json", {
            "run": label, "environment_original_sha256": sha((root / "environment.json").read_bytes()),
            "verification": "Every archived source SHA-256 equals this run's recorded source_sha256.",
            "verified_sha256": verified,
            "excluded_nonallowlisted_source_count": len(set(recorded) - set(CODE)),
        })
        # Retain reconstruction disclosures when an earlier verification record
        # can be tied to this exact run, rather than silently replacing history.
        for path in sorted(snapshots.glob("*/snapshot.json")):
            previous = read_json(path)
            reference = previous.get("environment_reference", previous.get("source_environment", ""))
            referenced_environment = Path(reference)
            if not referenced_environment.is_absolute():
                referenced_environment = BENCHMARK / referenced_environment
            tied_environment = (
                referenced_environment.is_file()
                and referenced_environment.resolve().is_relative_to(BENCHMARK)
                and sha(referenced_environment.read_bytes()) == sha((root / "environment.json").read_bytes())
            )
            if (label in reference or tied_environment) and previous.get("source_sha256", previous.get("verified_sha256")) == recorded:
                self.write_json(f"source-snapshots/{label}/original-verification.json", previous,
                                path, f"artifacts/source-snapshots/{path.parent.name}/snapshot.json",
                                ("canonical JSON serialization", "portable path/host tokens"))
                break

    def run(self, root: Path):
        label = root.name
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", label):
            raise ValueError("Result directory names must be simple portable labels")
        environment_path = root / "environment.json"
        environment = read_json(environment_path)
        arguments = environment.get("arguments", {})
        for field, token in [("data_root", "<DATA_ROOT>"), ("output", "<RESULT_ROOT>")]:
            if arguments.get(field):
                self.replacements[str(arguments[field])] = token
        if arguments.get("jar"):
            self.replacements[str(Path(arguments["jar"]).parent)] = "<DEPENDENCY_ROOT>"
            self.replacements[str(Path(arguments["jar"]).parent.parent)] = "<BENCHMARK_ROOT>"
        public_environment = {
            "started_at_utc": environment.get("started_at_utc"),
            "python": environment.get("python"), "platform": environment.get("platform"),
            "arguments": select(arguments, ARGUMENT_FIELDS),
            "resource_allocation": {"gpu": "NVIDIA A100-SXM4-40GB", "gpu_count": 1,
                                    "vcpus": 30, "host_ram_gib_approx": 216,
                                    "basis": "recorded study allocation; not a hard OS-thread or equal-memory limit"},
            "dependency_artifact": Path(arguments.get("jar", "")).name or None,
            "source_sha256": select(environment.get("source_sha256", {}), set(CODE)),
            "inventory_policy": "Raw command output, host identifiers, and hardware serials are excluded.",
        }
        self.write_json(f"results/{label}/environment.json", public_environment, environment_path,
                        f"results/{label}/environment.json",
                        ("allowlisted environment fields; raw inventory omitted", "canonical JSON serialization",
                         "portable path/host tokens"))
        self.sources(root, label, environment)
        scales = sorted(path for path in root.glob("sf*")
                        if path.is_dir() and (path / "manifest.json").is_file())
        selected_scales = {path.name for path in scales}
        declared_scales = {"sf" + format(float(value), "g").replace(".", "p")
                           for value in arguments.get("scales", [])}
        pending_scales = sorted(declared_scales - selected_scales)
        pending_schedule = []
        schedule = root / "schedule.json"
        incidents = []
        if schedule.is_file():
            raw_schedule = read_json(schedule)
            self.write_json(f"results/{label}/schedule.json",
                            [select(item, SCHEDULE_FIELDS) for item in raw_schedule], schedule,
                            f"results/{label}/schedule.json",
                            ("allowlisted schedule fields; launch command omitted", "canonical JSON serialization"))
            for item in raw_schedule:
                item_scale = "sf" + format(float(item["scale"]), "g").replace(".", "p")
                if (item.get("returncode") is None and item.get("status") != "failed"
                        and item_scale not in selected_scales):
                    pending_schedule.append(select(item, SCHEDULE_FIELDS))
                    continue
                if item.get("returncode") != 0 or item.get("status") in {"failed", "incomplete"}:
                    incidents.append({"kind": "failed_or_incomplete_schedule_entry",
                                      **select(item, {"scale", "engine", "returncode", "status", "failure_reason"})})
        sessions = []
        for scale in scales:
            manifest_path = scale / "manifest.json"
            original_manifest_hash = sha(manifest_path.read_bytes())
            # Keep safe manifests byte-identical so the historical analyzers can
            # validate their identity. Normalize paths only when actually needed.
            manifest_data = self.normalize(manifest_path.read_text()).encode()
            manifest_normalizations = (
                ("none; exact manifest bytes",) if manifest_data == manifest_path.read_bytes()
                else ("portable path/host tokens; original JSON formatting retained",)
            )
            for relative in [f"INPUT_MANIFESTS/{label}/{scale.name}.json",
                             f"results/{label}/{scale.name}/manifest.json"]:
                self.write(relative, manifest_data, manifest_path,
                           f"results/{label}/{scale.name}/manifest.json",
                           manifest_normalizations)
            for engine in ["polars", "spark_cpu", "spark_gpu"]:
                session_path = scale / engine / "results.json"
                if not session_path.is_file():
                    incidents.append({"kind": "missing_engine_session", "scale": scale.name, "engine": engine})
                    continue
                raw = read_json(session_path)
                for key in ["spark.driver.host", "spark.executor.host"]:
                    host = raw.get("spark_conf", {}).get(key)
                    if host and host not in {"localhost", "127.0.0.1", "::1"}:
                        self.replacements[host] = "<HOSTNAME>"
                if raw.get("manifest_sha256") != original_manifest_hash:
                    raise ValueError(f"Manifest hash mismatch for {label}/{scale.name}/{engine}")
                public = select(raw, SESSION_FIELDS)
                public.setdefault("suite", arguments.get("suite", "baseline"))
                for field in ["cleanup_records", "failed_query_cleanup_records"]:
                    if field in raw:
                        public[field] = [self.cleanup(item) for item in raw[field]]
                if "cleanup_failure" in raw:
                    public["cleanup_failure"] = self.cleanup_failure(raw["cleanup_failure"])
                if "cleanup_failures" in raw:
                    public["cleanup_failures"] = [self.cleanup_failure(item) for item in raw["cleanup_failures"]]
                query_errors = raw.get("query_errors", []) + raw.get("query_failures", [])
                session_failed = bool(query_errors or raw.get("failure") or raw.get("failed_at_utc")
                                      or raw.get("cleanup_failure") or raw.get("cleanup_failures")
                                      or raw.get("query_execution_status") == "partial"
                                      or not raw.get("completed_at_utc"))
                if session_failed:
                    disk_full = any("No space left on device" in item.get("message", "") for item in query_errors)
                    incidents.append({
                        "kind": "recorded_disk_space_failure" if disk_full else "failed_or_incomplete_session",
                        "scale": scale.name, "engine": engine,
                        "query_error_count": len(query_errors), "saved_result_records": len(raw.get("records", [])),
                        "query_errors": query_errors,
                        "failure": raw.get("failure"), "cleanup_failure": public.get("cleanup_failure"),
                        "query_execution_status": raw.get("query_execution_status"),
                    })
                public["manifest_original_sha256"] = original_manifest_hash
                public["manifest_sha256"] = sha(manifest_data)
                public["manifest_public_sha256"] = sha(manifest_data)
                public["spark_conf"] = select(raw.get("spark_conf", {}), SPARK_CONF)
                public["records"] = []
                for item in raw.get("records", []):
                    record = select(item, RECORD_FIELDS)
                    if not isinstance(record.get("rows"), list):
                        raise ValueError("Every successful record must retain its materialized rows")
                    plan = relative_file(session_path.parent, record["plan"])
                    target = f"results/{label}/{scale.name}/{engine}/{record['plan']}"
                    self.write(target, self.normalize(plan.read_text()).encode(), plan,
                               f"results/{label}/{scale.name}/{engine}/{record['plan']}",
                               ("portable path/host tokens; query plan operators retained",))
                    public["records"].append(record)
                    self.trials.append({
                        "run": label, "suite": public["suite"], "scale_factor": raw["scale_factor"],
                        "query_cleanup_policy": raw.get("query_cleanup_policy", "none"),
                        "engine": engine, "query": record["query"], "phase": record["phase"],
                        "trial": record["trial"], "seconds": record["seconds"],
                        "result_rows": len(record["rows"]),
                    })
                target = f"results/{label}/{scale.name}/{engine}/results.json"
                self.write_json(target, public, session_path, target,
                                ("allowlisted session/record/config fields; process IDs and host config omitted",
                                 "all recorded rows and elapsed times retained", "canonical JSON serialization",
                                 "cleanup numeric before/after observations retained; owned block-manager paths tokenized",
                                 "manifest_sha256 references public bytes; original retained as manifest_original_sha256",
                                 "portable path/host tokens"))
                sessions.append({"scale_factor": raw["scale_factor"], "engine": engine,
                                 "suite": public["suite"], "version": raw.get("version"),
                                 "cpu_threads": raw.get("cpu_threads"), "records": len(public["records"]),
                                 "query_cleanup_policy": raw.get("query_cleanup_policy", "none"),
                                 "evidence_status": "failed_or_partial" if session_failed else "completed_session",
                                 "queries": raw.get("queries", []), "results": target})
        if not sessions:
            raise ValueError(f"No engine results found in {label}")
        if incidents:
            incident = {"run": label, "evidence_kind": "nonperformance_resource_or_incomplete_run_incident",
                        "eligible_for_complete_performance_summary": False,
                        "interpretation": "Recorded failures or missing evidence are not performance bounds or completed-suite results. All raw saved timings/outputs remain in this run's namespace; the run is excluded from SUMMARY.csv.",
                        "observations": incidents}
            self.incidents.append(incident)
            self.write_json(f"results/{label}/INCIDENT.json", incident)
        progress = bool(pending_scales or pending_schedule)
        self.runs.append({"run": label, "sessions": sessions,
                          "selection_scope": "locally available manifest/session evidence at export",
                          "declared_scale_directories": sorted(declared_scales),
                          "selected_scale_directories": sorted(selected_scales),
                          "pending_declared_scale_directories": pending_scales,
                          "pending_unselected_schedule_entries": pending_schedule,
                          "evidence_status": ("incident_support_only" if incidents else
                                              "progress_selected_scope" if progress else "completed_sessions"),
                          "eligible_for_selected_session_summary": not bool(incidents),
                          "eligible_for_complete_performance_summary": not bool(incidents or progress)})

    def tables(self):
        def write_csv(name, rows):
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
            self.write(name, buffer.getvalue().encode())
        excluded = {item["run"] for item in self.incidents}
        progress = {item["run"] for item in self.runs if item["evidence_status"] == "progress_selected_scope"}
        for row in self.trials:
            row["evidence_scope"] = ("incident_support_only" if row["run"] in excluded else
                                     "completed_selected_sessions_progress_run" if row["run"] in progress else
                                     "recorded_completed_run")
        if self.trials:
            write_csv("TRIALS.csv", self.trials)
        grouped = {}
        for row in self.trials:
            if row["phase"] == "measured" and row["run"] not in excluded:
                key = tuple(row[k] for k in ["run", "suite", "query_cleanup_policy", "scale_factor", "engine", "query"])
                grouped.setdefault(key, []).append(row["seconds"])
        summary = []
        for key, times in sorted(grouped.items()):
            summary.append(dict(zip(["run", "suite", "query_cleanup_policy", "scale_factor", "engine", "query"], key)) | {
                "measurements": len(times), "median_seconds": statistics.median(times),
                "min_seconds": min(times), "max_seconds": max(times),
                "evidence_scope": ("completed_selected_sessions_progress_run" if key[0] in progress
                                   else "recorded_completed_run"),
            })
        if summary:
            write_csv("SUMMARY.csv", summary)


def readme(runs: list) -> str:
    labels = "\n".join(f"- `{run['run']}`: {len(run['sessions'])} engine sessions; {run['evidence_status'].replace('_', ' ')}." for run in runs)
    return f"""# Portable Spark / Polars reproducibility evidence (draft)

**Publication status: draft_not_cleared.** This technical evidence package has not
been cleared for external publication or redistribution. Sanitization and valid
checksums do not establish legal clearance. Read `NOTICES.md` and
`publication/NOTICE_RESEARCH.md` before using or distributing it.

This package contains benchmark Python sources, exact verified source snapshots,
pinned Python requirements, dataset manifests, all selected recorded trial timings
and materialized result rows, and normalized executed plans. It contains no
Parquet data, dependency JARs, raw machine inventory, event logs, or SSH material.
No blanket source license is asserted here; dependency and query-source rights
must be considered separately before redistribution.

## Included runs

{labels}

This export is a snapshot of locally selected evidence. `BUNDLE.json` records
each run's declared, selected and pending scale directories. A
`progress_selected_scope` run can contain completed selected sessions while
larger declared scales remain pending; it is not a globally completed run.
Its summary rows are explicitly labeled `completed_selected_sessions_progress_run`.

`DOCUMENT_CONTEXT.json` identifies any included draft article/plan notes and
curated oracle, cleanup-proof or post-run-integrity artifacts. Draft narratives
retain repository-context links whose targets may not be bundled; all links are
not claimed to resolve here. Their text is a dated draft snapshot, not a release
notice or a claim of completed evidence. Proof numeric values and SHA-256 values
are retained; sensitive directory identities are normalized separately.

`results/` holds curated session JSON and relative plan references.
`INPUT_MANIFESTS/` holds the corresponding dataset metadata and file checksums.
`source-snapshots/` preserves source bytes verified against each run's original
environment hash inventory. Top-level Python files are the current reproduction
tools and can differ from those snapshots. `TRIALS.csv` retains warmups and
measurements, with an explicit evidence scope and cleanup policy. `SUMMARY.csv`
groups measured trials by run, suite, cleanup policy, scale, engine and query.
Runs with a recorded failure or incomplete evidence remain in their own raw
namespaces and `TRIALS.csv`, labeled `incident_support_only`, and are excluded
entirely from `SUMMARY.csv`. `INCIDENTS.json` and each affected run's `INCIDENT.json`
preserve those observations as nonperformance evidence. No failed query is
assigned an inferred performance bound. Baseline and complex suites remain
separate, as do legacy `none` and `verified_gc` resource protocols.

## Verify this download

From this package's root directory on Linux, run `sha256sum -c SHA256SUMS`.
The adjacent ZIP checksum and download JSON describe the archive itself.
`PROVENANCE.json` maps every selected private original file's SHA-256 to its
public file SHA-256 and lists normalization operations. Original files remain
private. Public JSON/plans are intentionally transformed copies and do not claim
byte identity with the originals. Source files marked exact are byte-identical.
Safe input manifests retain their original bytes. If a manifest needs identifier
normalization, session `manifest_sha256` references its public bytes and
`manifest_original_sha256` preserves the original input-manifest identity.

## Reproduce from this package's root directory

Use a Linux NVIDIA GPU host with a compatible driver and Java 17. The recorded
allocation is one A100 40 GB, 30 vCPUs and about 216 GiB RAM. Inspect each session's
actual engine version and compute configuration in `results/`; those observations
take precedence over current dependency defaults.

Chart generation has a separate optional dependency set. Install it with
`python -m pip install -r requirements-reporting.txt` before running plot scripts;
it pins matplotlib 3.10.8 and is separate from the timed-engine requirements.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
mkdir -p jars
curl -fL https://repo.maven.apache.org/maven2/com/nvidia/rapids-4-spark_2.12/26.08.1/rapids-4-spark_2.12-26.08.1.jar -o jars/rapids-4-spark_2.12-26.08.1.jar
sha256sum -c DEPENDENCY_SHA256.txt
python generate_data.py --root data --scales 1 10 30
python generate_large_data.py --root data --scales 100 300 --workers 12
python run_experiment.py --data-root data --output rerun/baseline --scales 30 100 300 --runs 3 --cpus 30 --driver-memory 96g --jar jars/rapids-4-spark_2.12-26.08.1.jar --suite baseline
python run_experiment.py --data-root data --output rerun/complex --scales 30 100 300 --runs 3 --cpus 30 --driver-memory 96g --jar jars/rapids-4-spark_2.12-26.08.1.jar --suite complex --keep-going --query-cleanup verified_gc --cleanup-timeout-seconds 60
python analyze_results.py --results rerun/baseline --output rerun/baseline-report
python analyze_complex.py --results rerun/complex --output rerun/complex-report
```

For historical execution, use the matching `source-snapshots/<run>/` sources;
older runners predate `--suite` and failure-handling flags. Their `--help` documents
the supported arguments. New generated files need not have identical compressed
Parquet bytes or layout; compare schemas, row counts and semantic fingerprints.
Saved manifests identify the actual input files shared by all three engines.

For functional checking, `python complex_oracle.py --data data/sf1 --output
rerun/complex-oracle-sf1.json` runs all six SQL definitions in DuckDB outside timed
competitor sessions. Pass its output to `analyze_complex.py --oracle` alongside
the matching SF1 results only. Run labels are preserved under `results/<run>/`;
baseline and complex roots containing the same scale remain separate evidence.

Under `verified_gc`, a barrier follows each returned action outside the query
clock. The barrier's elapsed time remains within process wall time and is saved
separately. Cleanup records retain before/after shuffle file and byte counts,
root free bytes, active job IDs, GC requests, outcome and timestamps. Owned
block-manager directory identities are replaced by portable tokens. A recorded
passed barrier means no remaining observed owned shuffle files or active jobs;
it is not a claim that all memory allocations were released. Legacy `none` runs
have no explicit verified barrier measurement. `prove_query_cleanup.py`, when
included, is a separate small Spark lifecycle proof, not a timed competitor.

`validation/verify_input_integrity.py`, when included, is exact read-only source
for the post-run input check. It must run only after all nine complex engine
sessions finish and the orchestrator stops. It expects the recorded five-scale
layout: `data/sf1`, `data/sf10`, `data/sf30`, `data-large/sf100`, `data-large/sf300`,
and a `data-large/sf30` alias to `data/sf30`. From the package root, use Python
stdin so `generate_data` resolves from that directory. Set
`COMPLETED_BENCHMARK_PID` to the actual finished orchestrator's PID, compute
`INTEGRITY_SCRIPT_SHA256` from the included script bytes, and run:

```sh
python - --run rerun/complex --orchestrator-pid "$COMPLETED_BENCHMARK_PID" --source-sha256 "$INTEGRITY_SCRIPT_SHA256" --output artifacts/complex/postrun-input-integrity.json < validation/verify_input_integrity.py
```

The helper refuses a live benchmark orchestrator and an existing proof output.
Its inclusion does not mean the check has run; only an included successful
`postrun-input-integrity.json` records that outcome.

## Scope and limitations

The original six selected TPC-H-derived queries and the six complex workloads
are separate experiments. Their tables and per-query observations must remain
separate. This is neither the complete nor a certified TPC-H benchmark. The
queries use the same Float64/DOUBLE projections for decimal quantities/money.
Spark SQL is compared with equivalent native Polars lazy expressions. Outputs
are materialized. Per-query timers exclude engine/table setup, warmups and plan
serialization; startup/setup fields are retained separately. Filesystem preheating
is attempted before each engine; cache residency is not guaranteed.

Three measurements within one fresh engine session are not three independently
restarted experiments. A sum of per-query medians is a workload score, not a
measured complete-suite latency. Failed/missing complex queries must be disclosed;
do not treat incomplete engine totals as comparable complete-suite scores. GPU
operators in plans establish operator selection, not a GPU runtime percentage.
This one-host fixed configuration does not establish product-wide performance,
distributed scaling, equal memory budgets, cost efficiency or statistical
significance. Dataset size is not physical bytes read by every query.

Data generation uses DuckDB's [TPC-H extension](https://duckdb.org/docs/1.5/core_extensions/tpch).
Query definitions derive from the [TPC-H specification](https://www.tpc.org/tpch/).
The GPU dependency is NVIDIA's [cuDF/RAPIDS Spark plugin](https://nvidia.github.io/cudf-spark/docs/download.html).
"""


def notices() -> str:
    return """# Draft publication and source notices

Publication status: **draft_not_cleared**. No blanket license is granted or
asserted for this bundle. Technical sanitization and source-hash verification do
not establish permission to publish results, redistribute source, or use
benchmark names. No written TPC permission has been established by this export.

Selected workload definitions derive from the DuckDB 1.5.6 TPC-H query sources.
Consult the complete [tagged TPC-origin software license, EULA version 2.2](https://raw.githubusercontent.com/duckdb/duckdb/v1.5.6/extension/tpch/dbgen/LICENSE)
and the [complete TPC-hosted EULA 2.2.0](https://www.tpc.org/TPC_Documents_Current_Versions/txt/EULA_v2.2.0.txt).
The full terms, including preserved licenses/notices, modified-software
conditions, no-fee distribution, required legend/presentation, and performance
disclosure conditions must be considered; this notice does not replace them.
Covered redistributed software must retain the complete applicable EULA and
upstream notices where required. The linked DuckDB root MIT license is not a
blanket license for separately noticed TPC material or project contributions.

Result presentation and benchmark-name use also require review against the
[current TPC policies](https://www.tpc.org/TPC_Documents_Current_Versions/pdf/Policies_v6.20.pdf),
including applicable permission, disclaimer and attribution requirements.
`publication/NOTICE_RESEARCH.md` records primary sources, specific conditions,
and unresolved questions. It is research for the publication owner, not legal
clearance. Original-contribution licensing and covered-portion provenance remain
unresolved. Do not distribute this draft as a cleared public release.
"""


def scan_public(root: Path):
    forbidden = [
        re.compile(re.escape(str(Path.home()).encode())),
        re.compile(rb"/(?:Users|home)/[A-Za-z0-9_.-]+/"),
        re.compile(re.escape(("Key" + "22").encode())),
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE" + rb" KEY-----"),
        re.compile(rb"GPU-[0-9a-fA-F-]{8,}"),
    ]
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        for pattern in forbidden:
            if pattern.search(data):
                raise ValueError(f"Public leak check failed for {path.relative_to(root)}")
        for match in IPV4.finditer(data.decode("utf-8")):
            try:
                address = ipaddress.ip_address(match.group())
            except ValueError:
                continue
            # Versions such as py4j 0.10.9.9 are not host addresses.
            if int(address) >> 24 != 0 and not address.is_loopback and (address.is_private or address.is_link_local):
                raise ValueError(f"Private address leak check failed for {path.relative_to(root)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    roots = [path.expanduser().resolve() for path in args.results]
    if len({root.name for root in roots}) != len(roots):
        parser.error("Result directory labels must be unique")
    output = args.output.expanduser().resolve()
    if output.exists():
        parser.error("Output already exists; choose a fresh directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".bundle-", dir=output.parent))
    try:
        export = Exporter(staging)
        for name in (*CODE, "requirements.txt", "requirements-reporting.txt"):
            path = BENCHMARK / name
            if path.is_file() and not path.is_symlink():
                export.write(name, path.read_bytes(), path, name,
                             ("none; exact current source bytes",))
        own = Path(__file__).resolve()
        export.write("publication/export_bundle.py", own.read_bytes(), own,
                     "publication/export_bundle.py", ("none; exact exporter source bytes",))
        for path, relative in [
            (BENCHMARK / "artifacts/requirements.lock.txt", "requirements.lock.txt"),
            (BENCHMARK / "artifacts/jar.sha256", "DEPENDENCY_SHA256.txt"),
        ]:
            if path.is_file():
                export.write(relative, path.read_bytes(), path, f"artifacts/{path.name}",
                             ("none; exact dependency record bytes",))
        for root in roots:
            export.run(root)
        for relative in ["generation-equivalence-proof.json", "large-search/large-generation-proof-linux.json"]:
            path = BENCHMARK / "artifacts" / relative
            if path.is_file():
                export.write_json("validation/" + path.name, read_json(path), path,
                                  "artifacts/" + relative,
                                  ("canonical JSON serialization", "portable path/host tokens"))
        notice_research = BENCHMARK / "publication/NOTICE_RESEARCH.md"
        if not notice_research.is_file():
            raise ValueError("Publication notice research is required for the draft package")
        export.write("publication/NOTICE_RESEARCH.md", notice_research.read_bytes(), notice_research,
                     "publication/NOTICE_RESEARCH.md", ("none; exact publication-research bytes",))
        extras = export.extras()
        export.tables()
        export.write_json("INCIDENTS.json", {"evidence_kind": "nonperformance_incident_index",
                                            "incidents": export.incidents})
        export.write("README.md", readme(export.runs).encode())
        export.write("NOTICES.md", notices().encode())
        export.write_json("BUNDLE.json", {"format_version": 1, "publication_status": "draft_not_cleared",
                                         "selection_scope": "locally available evidence snapshot; global run completion is not implied",
                                         "runs": export.runs,
                                         **extras,
                                         "dataset_files_included": False, "dependency_binaries_included": False})
        export.write_json("PROVENANCE.json", {
            "format_version": 1,
            "policy": "Allowlisted evidence only; transformed public bytes have separate hashes from private originals.",
            "portable_tokens": ["<BENCHMARK_ROOT>", "<DATA_ROOT>", "<RESULT_ROOT>",
                                "<DEPENDENCY_ROOT>", "<ABSOLUTE_PATH>", "<HOST_IP>", "<HOSTNAME>", "<GPU_UUID>",
                                "<OWNED_BLOCKMGR_n>", "<BLOCKMGR_DIR_n>"],
            "spark_compute_configuration_allowlist": sorted(SPARK_CONF),
            "files": sorted(export.files, key=lambda item: item["public_path"]),
        })
        scan_public(staging)
        checksums = "".join(f"{sha(path.read_bytes())}  {path.relative_to(staging).as_posix()}\n"
                            for path in sorted(staging.rglob("*")) if path.is_file())
        export.write("SHA256SUMS", checksums.encode())
        staging.rename(output)
        archive = output.with_name(output.name + ".zip")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for path in sorted(output.rglob("*")):
                if path.is_file():
                    info = zipfile.ZipInfo("spark-gpu-reproducibility/" + path.relative_to(output).as_posix(),
                                           date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    bundle.writestr(info, path.read_bytes(), compresslevel=9)
        digest = sha(archive.read_bytes())
        archive.with_name(archive.name + ".sha256").write_text(f"{digest}  {archive.name}\n")
        download = {"filename": archive.name, "sha256": digest, "bytes": archive.stat().st_size,
                    "content_manifest_sha256": sha((output / "SHA256SUMS").read_bytes()),
                    "archive_root": "spark-gpu-reproducibility", "format": "zip"}
        output.with_name(output.name + ".download.json").write_bytes(json_bytes(download))
        print(json.dumps(download, sort_keys=True))
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


if __name__ == "__main__":
    main()
