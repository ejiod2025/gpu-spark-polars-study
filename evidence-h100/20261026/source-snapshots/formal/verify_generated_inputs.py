#!/usr/bin/env python3
"""Verify newly generated TPC-H-derived inputs against the A100 input manifests.

Run this after generate_data.py / generate_large_data.py and BEFORE any timed run.

Reference manifests live in evidence/INPUT_MANIFESTS/<run namespace>/sf<N>.json. The same
dataset was reused by several namespaces (the manifests are byte-identical), so the namespace is
chosen by scale in this order: production-20261025 (SF1/SF10/SF30, single-process generate_data.py),
large-sf100-20261025 and large-sf300-20261025 (chunked generate_large_data.py), then the complex
namespaces. If several namespaces hold different manifests for one scale the tool reports it.

Gating checks (FAIL, exit 1): DuckDB version, generation SQL / dbgen scale, Parquet settings, table
set, per-table row counts and schemas, the new manifest versus the files actually on disk (every
listed file exists and re-hashes; no extra or missing Parquet), Parquet footers (SNAPPY, row-group
size), and the order-independent content fingerprint of the Parquet on disk versus the best
available reference: the A100 manifest (chunked SF100/SF300 store it), else the A100-host
generation proof in evidence/validation (SF0.01/0.013/1), else an in-memory dbgen reference with
--reference-dbgen.

Byte-level checks are informational by default (--byte-identity report|require|ignore):
generate_data.py uses PER_THREAD_OUTPUT, so which rows land in which file depends on thread
scheduling and even two runs on one host differ; Parquet bytes also differ across platforms
(macOS arm64 vs Linux x86_64). Measured evidence is in docs/POLARS_GPU_COMPAT.md and the Phase 0
report. Use --byte-identity require only for a byte copy of the A100 files.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import duckdb  # noqa: E402

from generate_data import FORMAT_VERSION, TABLES, scale_name, sha256, sql_string  # noqa: E402
from generate_large_data import DUCKDB_VERSION, KEYS, fingerprint  # noqa: E402

EVIDENCE = HERE / "evidence"
MANIFEST_DIR = EVIDENCE / "INPUT_MANIFESTS"
PROOF_FILES = (EVIDENCE / "validation" / "large-generation-proof-linux.json",
               EVIDENCE / "validation" / "generation-equivalence-proof.json")
NAMESPACE_PRIORITY = ("production-20261025", "large-sf100-20261025", "large-sf300-20261025",
                      "complex-formal-v2-20261025", "complex-formal-20261025",
                      "complex-smoke-sf1-20261025", "complex-cleanup-smoke-sf1-20261025")
PARQUET_EXPECTED = {"compression": "snappy", "row_group_size": 122880,
                    "file_size_target": "128MB", "per_thread_output": True}
FINGERPRINT_KEYS = ("rows", "schema", "key_columns", "first_key_min", "first_key_max",
                    "row_hash_sum", "row_hash_xor", "key_hash_sum", "key_hash_xor")
STATUSES = ("PASS", "FAIL", "WARN", "INFO", "SKIP")
UNIQUE_KEY_AUTO_LIMIT_ROWS = 300_000_000


class Dataset:
    """Accumulates check results for one dataset directory."""

    def __init__(self, scale: float, path: Path):
        self.scale, self.path = scale, path
        self.checks: list[dict] = []
        self.reference: dict | None = None

    def add(self, check_id: str, status: str, detail: str, **data) -> None:
        assert status in STATUSES, status
        self.checks.append({"id": check_id, "status": status, "detail": detail, **data})
        print(f"  [{status:4s}] {check_id}: {detail}", flush=True)

    def counts(self) -> dict:
        return {s: sum(1 for c in self.checks if c["status"] == s) for s in STATUSES}

    @property
    def ok(self) -> bool:
        return self.counts()["FAIL"] == 0


def load_json(path: Path):
    return json.loads(path.read_text())


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_reference(scale: float, manifest_dir: Path) -> tuple[Path | None, list[dict]]:
    """Pick the A100 manifest for a scale; return it and every namespace that holds one."""
    name = scale_name(scale) + ".json"
    found = []
    for namespace in sorted(p.name for p in manifest_dir.iterdir() if p.is_dir()):
        path = manifest_dir / namespace / name
        if path.is_file():
            found.append({"namespace": namespace, "path": str(path), "sha256": file_digest(path)})
    if not found:
        return None, []
    rank = {n: i for i, n in enumerate(NAMESPACE_PRIORITY)}
    found.sort(key=lambda item: rank.get(item["namespace"], len(rank)))
    return Path(found[0]["path"]), found


def proof_reference(scale: float, proof_files=PROOF_FILES) -> tuple[dict | None, str | None]:
    """Table fingerprints of `CALL dbgen(sf=...)` from the A100-host generation proofs."""
    for proof in proof_files:
        if not proof.is_file():
            continue
        for check in load_json(proof).get("checks", []):
            if check.get("scale_factor") == scale and check.get("tables"):
                return check["tables"], str(proof)
    return None, None


def is_chunked(manifest: dict) -> bool:
    return "children" in manifest or "chunks" in manifest


def first_sql_part(manifest: dict) -> str:
    return manifest.get("generation_sql", "")


def check_header(ds: Dataset, ref: dict, new: dict) -> None:
    scale = ds.scale
    if new.get("format_version") != FORMAT_VERSION or ref.get("format_version") != FORMAT_VERSION:
        ds.add("format_version", "FAIL", f"new={new.get('format_version')} ref={ref.get('format_version')}")
    else:
        ds.add("format_version", "PASS", str(FORMAT_VERSION))
    if new.get("scale_factor") != scale or ref.get("scale_factor") != scale:
        ds.add("scale_factor", "FAIL", f"requested={scale} new={new.get('scale_factor')} ref={ref.get('scale_factor')}")
    else:
        ds.add("scale_factor", "PASS", str(scale))
    if new.get("duckdb_version") == ref.get("duckdb_version") == DUCKDB_VERSION:
        ds.add("duckdb_version", "PASS", new["duckdb_version"])
    else:
        ds.add("duckdb_version", "FAIL",
               f"new={new.get('duckdb_version')} ref={ref.get('duckdb_version')} pinned={DUCKDB_VERSION}")
    if duckdb.__version__ != DUCKDB_VERSION:
        ds.add("installed_duckdb", "WARN", f"verifier runs duckdb {duckdb.__version__}, pinned {DUCKDB_VERSION}")
    if is_chunked(new) != is_chunked(ref):
        ds.add("generator_kind", "WARN",
               f"new={'chunked' if is_chunked(new) else 'single-process'} "
               f"ref={'chunked' if is_chunked(ref) else 'single-process'}; byte layout is not comparable")
    else:
        ds.add("generator_kind", "PASS", "chunked" if is_chunked(new) else "single-process")
    if first_sql_part(new) == first_sql_part(ref):
        ds.add("generation_sql", "PASS", first_sql_part(new))
    else:
        severity = "WARN" if is_chunked(new) and is_chunked(ref) and new.get("children") != ref.get("children") \
            and str(new.get("scale_factor")) == str(ref.get("scale_factor")) else "FAIL"
        ds.add("generation_sql", severity, f"new={first_sql_part(new)!r} ref={first_sql_part(ref)!r}")
    for key in ("compression", "row_group_size", "file_size_target", "per_thread_output"):
        a, b, expected = new.get("parquet", {}).get(key), ref.get("parquet", {}).get(key), PARQUET_EXPECTED[key]
        if a == b == expected:
            continue
        ds.add(f"parquet_setting.{key}", "FAIL", f"new={a!r} ref={b!r} expected={expected!r}")
        break
    else:
        ds.add("parquet_settings", "PASS", json.dumps(new.get("parquet"), sort_keys=True))
    if is_chunked(new) and is_chunked(ref):
        version = (new.get("tpch_extension") or {}).get("extension_version")
        expected = (ref.get("tpch_extension") or {}).get("extension_version")
        ds.add("tpch_extension", "PASS" if version == expected else "FAIL", f"new={version} ref={expected}")
    for key in ("generation_threads", "generation_memory_limit", "generation_workers",
                "generation_threads_per_worker", "generation_memory_limit_per_worker"):
        if key in ref or key in new:
            a, b = new.get(key), ref.get(key)
            if a != b:
                level = "WARN" if key == "generation_threads" else "INFO"
                note = (" (single-process file layout follows the thread count; use the A100 value)"
                        if key == "generation_threads" else "")
                ds.add(f"param.{key}", level, f"new={a!r} ref={b!r}{note}")
    if set(new.get("tables", {})) != set(TABLES) or set(ref.get("tables", {})) != set(TABLES):
        ds.add("table_set", "FAIL", f"new={sorted(new.get('tables', {}))} ref={sorted(ref.get('tables', {}))}")
    else:
        ds.add("table_set", "PASS", ", ".join(TABLES))


def check_tables(ds: Dataset, ref: dict, new: dict) -> None:
    bad = []
    for table in TABLES:
        a, b = new["tables"].get(table), ref["tables"].get(table)
        if a is None or b is None:
            continue
        if a["rows"] != b["rows"]:
            bad.append(f"{table}: rows new={a['rows']} ref={b['rows']}")
        if a["schema"] != b["schema"]:
            bad.append(f"{table}: schema new={a['schema']} ref={b['schema']}")
    if bad:
        ds.add("rows_and_schema_vs_reference", "FAIL", "; ".join(bad))
    else:
        total = sum(new["tables"][t]["rows"] for t in TABLES)
        ds.add("rows_and_schema_vs_reference", "PASS", f"8 tables, {total:,} rows, schemas identical")
    for key in ("total_rows",):
        if new.get(key) != ref.get(key):
            ds.add(f"manifest.{key}", "FAIL", f"new={new.get(key)} ref={ref.get(key)}")


def check_byte_identity(ds: Dataset, ref: dict, new: dict, policy: str) -> None:
    if policy == "ignore":
        ds.add("byte_identity", "SKIP", "disabled by --byte-identity ignore")
        return
    per_table, path_match, multiset_match, total = {}, 0, 0, 0
    layout_diffs = []
    for table in TABLES:
        fa = {f["path"]: f for f in new["tables"][table]["files"]}
        fb = {f["path"]: f for f in ref["tables"][table]["files"]}
        same_path = sum(1 for p in fa if p in fb and fa[p]["sha256"] == fb[p]["sha256"])
        multiset = sorted(f["sha256"] for f in fa.values()) == sorted(f["sha256"] for f in fb.values())
        if set(fa) != set(fb):
            layout_diffs.append(f"{table}: {len(fa)} files vs {len(fb)}")
        per_table[table] = {"files_new": len(fa), "files_ref": len(fb), "same_path_same_sha256": same_path,
                            "file_hash_multiset_equal": multiset,
                            "table_bytes_equal": new["tables"][table]["parquet_bytes"] == ref["tables"][table]["parquet_bytes"]}
        path_match += same_path
        multiset_match += int(multiset)
        total += len(fb)
    if layout_diffs:
        ds.add("file_layout_vs_reference", "WARN", "file path sets differ: " + "; ".join(layout_diffs))
    else:
        ds.add("file_layout_vs_reference", "PASS", "identical file path sets per table")
    exact = path_match == total and all(v["table_bytes_equal"] for v in per_table.values())
    detail = (f"{path_match}/{total} files byte-identical by path; file-hash multiset equal for "
              f"{multiset_match}/8 tables; table byte totals equal for "
              f"{sum(v['table_bytes_equal'] for v in per_table.values())}/8 tables")
    status = "PASS" if exact else ("FAIL" if policy == "require" else "INFO")
    ds.add("byte_identity", status, detail, per_table=per_table)


def disk_files(dataset: Path, table: str) -> set[str]:
    return {p.relative_to(dataset).as_posix() for p in (dataset / table).glob("*.parquet")}


def check_disk(ds: Dataset, new: dict, rehash: bool, workers: int) -> None:
    dataset = ds.path
    problems, files = [], []
    for table in TABLES:
        listed = {f["path"]: f for f in new["tables"][table]["files"]}
        observed = disk_files(dataset, table)
        for path in sorted(set(listed) - observed):
            problems.append(f"missing {path}")
        for path in sorted(observed - set(listed)):
            problems.append(f"unlisted {path}")
        for path, item in listed.items():
            target = dataset / path
            if target.is_file():
                if target.stat().st_size != item["bytes"]:
                    problems.append(f"size mismatch {path}: disk={target.stat().st_size} manifest={item['bytes']}")
                else:
                    files.append((target, item))
        listed_bytes = sum(f["bytes"] for f in listed.values())
        if listed_bytes != new["tables"][table]["parquet_bytes"]:
            problems.append(f"{table}: parquet_bytes {new['tables'][table]['parquet_bytes']} != sum(files) {listed_bytes}")
    # Match the complete scale name: .sf100/.sf10 state is not SF1 state.
    prefix = "." + dataset.name
    leftovers = sorted(p.name for p in dataset.parent.iterdir()
                       if (p.name == prefix or p.name.startswith(prefix + "."))
                       and p.name != prefix + ".generation.lock")
    leftovers += sorted(str(p.relative_to(dataset)) for p in dataset.rglob("*")
                        if p.name.endswith((".tmp", ".wal", ".duckdb")) or p.name == ".steps" or p.name == ".work")
    if leftovers:
        problems.append("partial-write leftovers: " + ", ".join(leftovers))
    if rehash:
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            digests = list(pool.map(lambda pair: sha256(pair[0]), files))
        bad = [str(pair[0].relative_to(dataset)) for pair, digest in zip(files, digests) if digest != pair[1]["sha256"]]
        problems += [f"sha256 mismatch {path}" for path in bad]
        suffix = f"; re-hashed {len(files)} files in {time.perf_counter() - start:.1f}s"
    else:
        suffix = "; sha256 re-hash skipped (--no-rehash)"
    if problems:
        ds.add("disk_vs_new_manifest", "FAIL", f"{len(problems)} problem(s): " + "; ".join(problems[:8])
               + (" ..." if len(problems) > 8 else ""), problems=problems)
    else:
        ds.add("disk_vs_new_manifest", "PASS" if rehash else "WARN",
               f"{len(files)} files present, sizes match, no extra Parquet, no partial-write leftovers{suffix}")


def connect(threads: int, memory_limit: str, temp_dir: Path):
    connection = duckdb.connect(":memory:")
    connection.execute(f"SET threads = {int(threads)}")
    connection.execute(f"SET memory_limit = {sql_string(memory_limit)}")
    connection.execute(f"SET temp_directory = {sql_string(temp_dir)}")
    return connection


def relation(dataset: Path, table: str) -> str:
    return f"read_parquet({sql_string(dataset / table / '*.parquet')})"


def check_footers(ds: Dataset, connection) -> None:
    problems = []
    for table in TABLES:
        codecs, max_rows = connection.execute(
            f"SELECT list(DISTINCT compression), max(row_group_num_rows) FROM parquet_metadata({sql_string(ds.path / table / '*.parquet')})"
        ).fetchone()
        if sorted(codecs) != ["SNAPPY"]:
            problems.append(f"{table}: codecs {codecs}")
        if max_rows > PARQUET_EXPECTED["row_group_size"]:
            problems.append(f"{table}: row group of {max_rows} rows > {PARQUET_EXPECTED['row_group_size']}")
    if problems:
        ds.add("parquet_footers", "FAIL", "; ".join(problems))
    else:
        ds.add("parquet_footers", "PASS", f"all row groups SNAPPY and <= {PARQUET_EXPECTED['row_group_size']} rows")


def compare_fingerprint(expected: dict, observed: dict) -> list[str]:
    return [f"{key}: disk={observed[key]!r} expected={expected.get(key)!r}"
            for key in FINGERPRINT_KEYS if key in expected and observed[key] != expected[key]]


def check_content(ds: Dataset, ref: dict | None, new: dict, connection, reference_dbgen: bool,
                  proof: dict | None, proof_path: str | None, threads: int) -> dict:
    """Fingerprint the Parquet on disk and compare against every available reference."""
    start = time.perf_counter()
    observed = {table: fingerprint(connection, table, relation(ds.path, table)) for table in TABLES}
    seconds = time.perf_counter() - start
    ds.add("disk_fingerprint", "INFO", f"computed order-independent fingerprint of 8 tables in {seconds:.1f}s")
    sources = []
    if ref is not None and all("row_hash_sum" in ref["tables"][t] for t in TABLES):
        sources.append(("A100 manifest", {t: ref["tables"][t] for t in TABLES}))
    if proof:
        sources.append((f"A100-host generation proof ({Path(proof_path).name})", proof))
    if all("row_hash_sum" in new["tables"][t] for t in TABLES):
        sources.append(("new manifest (self-consistency)", {t: new["tables"][t] for t in TABLES}))
    if reference_dbgen:
        start = time.perf_counter()
        reference = duckdb.connect(":memory:")
        reference.execute(f"SET threads = {int(threads)}")
        reference.execute("INSTALL tpch")
        reference.execute("LOAD tpch")
        reference.execute(f"CALL dbgen(sf = {ds.scale!r})")
        sources.append((f"in-memory CALL dbgen(sf = {ds.scale!r}) with duckdb {duckdb.__version__}",
                        {t: fingerprint(reference, t) for t in TABLES}))
        reference.close()
        ds.add("dbgen_reference", "INFO", f"generated reference in {time.perf_counter() - start:.1f}s")
    external = [s for s in sources if not s[0].startswith("new manifest")]
    for label, expected in sources:
        bad = []
        for table in TABLES:
            bad += [f"{table}.{item}" for item in compare_fingerprint(expected[table], observed[table])]
        check_id = "content_fingerprint[" + label.split(" (")[0].split(" with")[0] + "]"
        if bad:
            ds.add(check_id, "FAIL", f"{label}: {len(bad)} mismatch(es): " + "; ".join(bad[:4]))
        else:
            ds.add(check_id, "PASS", f"{label}: rows, schema, key range, row/key hash sum and xor match for 8 tables")
    if not external:
        ds.add("content_fingerprint[external]", "WARN",
               "no external content reference for this scale (the A100 manifest holds no fingerprint and no proof "
               "covers it); content rests on row counts, schemas and self-consistency. Add --reference-dbgen "
               "for an in-memory dbgen comparison.")
    return observed


def check_unique_keys(ds: Dataset, connection, new: dict, mode: str) -> None:
    total = sum(new["tables"][t]["rows"] for t in TABLES)
    if mode == "off" or (mode == "auto" and total > UNIQUE_KEY_AUTO_LIMIT_ROWS):
        ds.add("unique_primary_keys", "SKIP", f"not run ({'--unique-keys off' if mode == 'off' else f'{total:,} rows > auto limit'})")
        return
    bad = []
    for table in TABLES:
        keys = ", ".join(KEYS[table])
        distinct = connection.execute(
            f"SELECT count(*) FROM (SELECT DISTINCT {keys} FROM {relation(ds.path, table)})").fetchone()[0]
        if distinct != new["tables"][table]["rows"]:
            bad.append(f"{table}: {distinct} distinct keys for {new['tables'][table]['rows']} rows")
    ds.add("unique_primary_keys", "FAIL" if bad else "PASS", "; ".join(bad) if bad else "primary keys unique in all 8 tables")


def verify_dataset(scale: float, root: Path, args) -> Dataset:
    path = root / scale_name(scale)
    ds = Dataset(scale, path)
    print(f"== {scale_name(scale)}: {path}", flush=True)
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        ds.add("new_manifest", "FAIL", f"missing {manifest_path}")
        return ds
    new = load_json(manifest_path)
    ds.add("new_manifest", "INFO", f"sha256 {file_digest(manifest_path)[:16]}...",
           sha256=file_digest(manifest_path))
    if args.reference_manifest:
        reference_path, alternates = args.reference_manifest, []
    else:
        reference_path, alternates = find_reference(scale, args.manifest_dir)
    proof, proof_path = proof_reference(scale)
    if reference_path is None:
        status = "WARN" if args.allow_no_reference else "FAIL"
        ds.add("a100_reference_manifest", status, f"no {scale_name(scale)}.json under {args.manifest_dir}")
        if status == "FAIL" and proof is None:
            return ds
        ref = None  # only manifest-independent checks below
    else:
        ref = load_json(reference_path)
        distinct = {a["sha256"] for a in alternates}
        ds.reference = {"path": str(reference_path), "namespace": reference_path.parent.name,
                        "sha256": file_digest(reference_path), "all_namespaces": alternates}
        note = (f"; {len(alternates)} namespaces hold it, all byte-identical" if len(distinct) == 1 and len(alternates) > 1
                else "")
        if len(distinct) > 1:
            ds.add("a100_reference_namespaces", "WARN",
                   "namespaces disagree: " + ", ".join(f"{a['namespace']}={a['sha256'][:8]}" for a in alternates)
                   + f"; using {reference_path.parent.name}")
        ds.add("a100_reference_manifest", "INFO", f"{reference_path.parent.name}/{reference_path.name}{note}")
        check_header(ds, ref, new)
        check_tables(ds, ref, new)
        check_byte_identity(ds, ref, new, args.byte_identity)
    check_disk(ds, new, not args.no_rehash, args.workers)
    temp_dir = Path(tempfile.mkdtemp(prefix="verify-inputs-", dir=args.temp_dir))
    connection = connect(args.threads, args.memory_limit, temp_dir)
    try:
        for check_id, run_check in (
                ("parquet_footers", lambda: check_footers(ds, connection)),
                ("content_fingerprint", lambda: check_content(ds, ref, new, connection, args.reference_dbgen,
                                                              proof, proof_path, args.threads)),
                ("unique_primary_keys", lambda: check_unique_keys(ds, connection, new, args.unique_keys))):
            try:
                run_check()
            except duckdb.Error as error:  # unreadable/corrupt Parquet must be a finding, not a crash
                ds.add(check_id, "FAIL", f"DuckDB could not read the Parquet files: {str(error).splitlines()[0]}")
    finally:
        connection.close()
        for item in sorted(temp_dir.rglob("*"), reverse=True):
            item.unlink() if item.is_file() else item.rmdir()
        temp_dir.rmdir()
    return ds


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--scales", type=float, nargs="+", default=[1])
    parser.add_argument("--manifest-dir", type=Path, default=MANIFEST_DIR)
    parser.add_argument("--reference-manifest", type=Path,
                        help="override the A100 manifest (only with a single scale)")
    parser.add_argument("--allow-no-reference", action="store_true",
                        help="do not fail when no A100 manifest exists for a scale (smoke scales)")
    parser.add_argument("--byte-identity", choices=("report", "require", "ignore"), default="report")
    parser.add_argument("--no-rehash", action="store_true", help="skip re-hashing files against the new manifest")
    parser.add_argument("--reference-dbgen", action="store_true",
                        help="also fingerprint an in-memory CALL dbgen(sf) (memory heavy above SF30)")
    parser.add_argument("--unique-keys", choices=("auto", "on", "off"), default="auto")
    parser.add_argument("--threads", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 4), help="parallel file hashers")
    parser.add_argument("--memory-limit", default="16GB", help="DuckDB memory limit for fingerprinting")
    parser.add_argument("--temp-dir", type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument("--report", type=Path, help="write the JSON report here (new file only)")
    parser.add_argument("--strict", action="store_true", help="treat WARN as failure")
    args = parser.parse_args(argv)
    if args.reference_manifest and len(args.scales) != 1:
        parser.error("--reference-manifest requires exactly one scale")
    if args.report and args.report.exists():
        parser.error(f"refusing to overwrite {args.report}")
    started = datetime.now(timezone.utc)
    root = args.data_root.expanduser().resolve()
    datasets = [verify_dataset(scale, root, args) for scale in args.scales]
    ok = all(d.ok and not (args.strict and d.counts()["WARN"]) for d in datasets)
    report = {
        "tool": "verify_generated_inputs", "format": 1, "started_at_utc": started.isoformat(),
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": {"platform": platform.platform(), "machine": platform.machine(), "python": platform.python_version(),
                 "duckdb": duckdb.__version__, "cpus": os.cpu_count()},
        "args": {k: str(v) for k, v in vars(args).items()}, "ok": ok,
        "datasets": [{"scale_factor": d.scale, "path": str(d.path), "ok": d.ok, "counts": d.counts(),
                      "reference": d.reference, "checks": d.checks} for d in datasets],
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.report.with_suffix(args.report.suffix + ".tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n")
        temporary.replace(args.report)
        print(f"report: {args.report}")
    print("RESULT:", "OK" if ok else "FAILED", {scale_name(d.scale): d.counts() for d in datasets}, flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
