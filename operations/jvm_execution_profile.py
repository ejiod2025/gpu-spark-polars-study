#!/usr/bin/env python3
"""Untimed execution-profile capture/gates for the single GCLocker100 recovery.

The experimental source, campaign format, and journal remain frozen. This
operational evidence binds Java, launcher, inherited environment and original
physical data to an independently successful SF100 diagnostic.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

CAMPAIGN_ID = "h100-confirmatory-gclocker100-20261027"
SOURCE_COMMIT = "c84ef6a07341f350dae6636ab709c1b65fd894e0"
SOURCE_TREE = "9514f0f7ce384763d0a675a9075666707e0edabcc003c729a3e0735a52ea25d8"
SPARK_SUBMIT_OPTS = "-XX:+UnlockDiagnosticVMOptions -XX:GCLockerRetryAllocationCount=100 -XX:+PrintFlagsFinal"
HEAP_BYTES = 103079215104
BANNED_ENV = ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS", "SPARK_LAUNCHER_OPTS")
ENV_NAMES = ("JAVA_HOME", "SPARK_HOME", "SPARK_CONF_DIR", "SPARK_LOCAL_DIRS", "SPARK_SUBMIT_OPTS", "PYTHON_GPU_ENV",
             "DATA_ROOT", "RAPIDS_JAR", "PYSPARK_PYTHON", "PYSPARK_DRIVER_PYTHON", "PYSPARK_SUBMIT_ARGS",
             "SPARK_DRIVER_MEMORY", "SPARK_MEM", "POLARS_MAX_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
             "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS", "CUDA_VISIBLE_DEVICES", *BANNED_ENV)
GATE_PHASES = ("readiness-before", "readiness-after", *[f"block-{n:02d}-{edge}" for n in range(1, 11)
                                                    for edge in ("before", "after")], "final-before", "final-after")
HEX = re.compile(r"[0-9a-f]{64}\Z")


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def require_hashes(value, label):
    if not isinstance(value, dict) or not value or any(not isinstance(k, str) or (not isinstance(v, str) or not HEX.fullmatch(v))
                                                     for k, v in value.items()):
        raise ValueError(f"Missing/invalid {label} SHA256 identities")


def parse_driver_flags(log_text, heap_bytes=HEAP_BYTES):
    """Require one actual PrintFlagsFinal table; reject duplicate/overridden values."""
    expected = {"GCLockerRetryAllocationCount": 100, "MaxHeapSize": heap_bytes, "UseG1GC": True,
                "UnlockDiagnosticVMOptions": True, "PrintFlagsFinal": True}
    observed = {}
    for name, wanted in expected.items():
        matches = re.findall(r"^\s*\w+\s+" + name + r"\s*(?::=|=)\s*(\S+).*$", log_text, re.MULTILINE)
        if len(matches) != 1:
            raise ValueError(f"Expected one actual child JVM flag {name}; found {len(matches)}")
        raw = matches[0]
        value = raw == "true" if isinstance(wanted, bool) else int(raw) if raw.isdigit() else raw
        if value != wanted or (isinstance(wanted, bool) and raw not in ("true", "false")):
            raise ValueError(f"Actual child JVM flag {name} differs: {raw}")
        observed[name] = value
    return observed


def validate_profile(value):
    """Validate the public structural contract without accessing VM-only paths."""
    if (value.get("schema_version") != "h100-jvm-execution-profile-v1" or value.get("campaign_id") != CAMPAIGN_ID
            or value.get("source_commit") != SOURCE_COMMIT or value.get("source_tree_sha256") != SOURCE_TREE
            or value.get("spark_submit_opts") != SPARK_SUBMIT_OPTS or not value.get("created_at_utc")):
        raise ValueError("Execution profile is not the authorized GCLocker100 recovery")
    java = value.get("java", {})
    if java.get("version") != "17.0.20.1" or not java.get("version_output") or not java.get("java_home"):
        raise ValueError("Execution profile must retain Java 17.0.20.1")
    require_hashes(java.get("files_sha256"), "Java")
    if set(java["files_sha256"]) != {str(Path(java["java_home"]) / p)
                                    for p in ("bin/java", "lib/server/libjvm.so", "lib/modules", "release")}:
        raise ValueError("Profile must bind Java binary, libjvm, modules and release")
    if java.get("binary") != str(Path(java["java_home"]) / "bin/java"):
        raise ValueError("Java executable does not belong to recorded Java home")
    require_hashes(value.get("environment_sources"), "environment source")
    require_hashes(value.get("launcher_files_sha256"), "Spark launcher")
    environment = value.get("environment", {})
    if set(environment) != set(ENV_NAMES) or environment["SPARK_SUBMIT_OPTS"] != SPARK_SUBMIT_OPTS:
        raise ValueError("Execution profile environment is incomplete or has different Spark flags")
    if any(environment.get(name) is not None for name in BANNED_ENV):
        raise ValueError("JVM options must be supplied only through SPARK_SUBMIT_OPTS")
    original = value.get("original_inputs", {})
    require_hashes(original.get("files_sha256"), "original input/oracle")
    require_hashes(original.get("dataset_files_sha256"), "physical data")
    if len(original["dataset_files_sha256"]) != 1654 or not original.get("evidence_root"):
        raise ValueError("Recovery must bind the same 1654 physical data files")
    required = {"campaign.json", "INPUT_DOMAIN.json", *[f"manifests/sf{s}.json" for s in (1, 10, 30, 100, 300)]}
    required |= {f"oracles/{suite}-sf{s}.{suffix}" for suite, scales in (("baseline", (1, 10, 30, 100, 300)),
                  ("complex", (30, 100, 300))) for s in scales for suffix in ("json", "log")}
    if not required <= set(original["files_sha256"]):
        raise ValueError("Original oracle JSON/log/manifests and plan/domain identities are incomplete")
    if any(Path(p).is_absolute() or ".." in Path(p).parts for p in original["files_sha256"]):
        raise ValueError("Unsafe original evidence relative path")
    require_hashes(value.get("source_files_sha256"), "frozen source")
    if (len(value["source_files_sha256"]) != 52 or
            hashlib.sha256(canonical(value["source_files_sha256"])).hexdigest() != SOURCE_TREE):
        raise ValueError("Profile must retain the exact frozen 52-file C84 source")
    configs = value.get("python_configs", [])
    expected_configs = {"polars_cpu_144": "polars", "polars_cpu_200": "polars", "polars_gpu": "polars_gpu",
                        "spark_cpu": "spark_cpu", "spark_gpu": "spark_gpu"}
    if (len(configs) != 5 or {entry.get("id"): entry.get("engine") for entry in configs} != expected_configs
            or any(not entry.get("python") or not entry.get("runtime", {}).get("distributions") for entry in configs)):
        raise ValueError("Execution profile must retain all five original CPython runtime identities")
    settings = value.get("settings", {})
    if (settings.get("driver_memory") != "96g" or settings.get("cpus") != 26 or settings.get("runs") != 3
            or settings.get("spark_gpu_profile") != "a100-parity"
            or settings.get("query_cleanup_by_suite") != {"baseline": "none", "complex": "verified_gc"}
            or value.get("spark_sql_settings") != {"shuffle_partitions": 120, "adaptive_enabled": True}):
        raise ValueError("Recovery settings differ from the frozen experiment")
    diagnostic = value.get("diagnostic", {})
    if (diagnostic.get("status") != "PASS" or not diagnostic.get("completed_at_utc")
            or not diagnostic.get("driver_log_path") or not diagnostic.get("validation_path")
            or not HEX.fullmatch(diagnostic.get("driver_log_sha256", ""))
            or not HEX.fullmatch(diagnostic.get("validation_sha256", ""))):
        raise ValueError("A successful separate SF100 diagnostic is a prerequisite")
    return value


def environment():
    return {name: os.environ.get(name) for name in ENV_NAMES}


def java_identity():
    home = os.environ.get("JAVA_HOME")
    binary = (Path(home) / "bin/java") if home else Path(shutil.which("java") or "/missing-java")
    binary = binary.resolve(strict=True)
    home = binary.parent.parent
    output = subprocess.check_output([str(binary), "-version"], stderr=subprocess.STDOUT, text=True, timeout=30)
    match = re.search(r'version "([^"]+)"', output)
    return {"binary": str(binary), "java_home": str(home), "version": match.group(1) if match else None,
            "version_output": output, "files_sha256": {str(home / p): sha(home / p)
            for p in ("bin/java", "lib/server/libjvm.so", "lib/modules", "release")}}


def frozen_module(source):
    sys.path.insert(0, str(source))
    spec = importlib.util.spec_from_file_location("profile_frozen_campaign", source / "research_campaign.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_local(value, source, *, original=False, data=False):
    validate_profile(value)
    if java_identity() != value["java"]:
        raise ValueError("Java binary/build drift since diagnostic/profile capture")
    if environment() != value["environment"]:
        raise ValueError("Inherited execution environment differs from fixed profile")
    for mapping in (value["environment_sources"], value["launcher_files_sha256"]):
        for name, expected in mapping.items():
            if sha(name) != expected:
                raise ValueError(f"Execution profile file changed: {name}")
    module = frozen_module(source)
    identity = module.source_identity()
    if identity["files_sha256"] != value["source_files_sha256"]:
        raise ValueError("Frozen 52-file experimental source changed")
    module.check_frozen_runtimes({"configs": value["python_configs"]})
    for field in ("driver_log", "validation"):
        if sha(value["diagnostic"][field + "_path"]) != value["diagnostic"][field + "_sha256"]:
            raise ValueError("SF100 diagnostic evidence changed")
    parse_driver_flags(Path(value["diagnostic"]["driver_log_path"]).read_text())
    if original:
        root = Path(value["original_inputs"]["evidence_root"])
        for name, expected in value["original_inputs"]["files_sha256"].items():
            if sha(root / name) != expected:
                raise ValueError(f"Original input/oracle evidence changed: {name}")
    if data:
        expected = value["original_inputs"]["dataset_files_sha256"]
        actual = {str(p) for scale in (1, 10, 30, 100, 300)
                  for p in (Path(value["environment"]["DATA_ROOT"]) / f"sf{scale}").glob("*/*.parquet")}
        if actual != set(expected):
            raise ValueError("Physical dataset file set differs")
        for name, digest in expected.items():
            if Path(name).is_symlink() or sha(name) != digest:
                raise ValueError(f"Original physical dataset bytes changed: {name}")
    idle = module.observe_exclusive_host()
    if not idle["eligible"]:
        raise ValueError(f"Execution-profile gate requires idle engines: {idle}")
    return idle


def capture(args):
    if any(name in os.environ for name in BANNED_ENV):
        raise ValueError("Clear global and launcher JVM option variables before capture")
    original = json.loads((args.original_evidence / "campaign.json").read_bytes())
    module = frozen_module(args.source)
    source = module.source_identity()
    if (original["source"]["git_head"] != SOURCE_COMMIT or
            source["files_sha256"] != original["source"]["files_sha256"]):
        raise ValueError("Original campaign and current source must be frozen C84")
    probe = "import pathlib,pyspark; print(pathlib.Path(pyspark.__file__).parent); print(pyspark.__version__)"
    lines = subprocess.check_output([args.python, "-c", probe], text=True, timeout=30).splitlines()
    spark = Path(lines[0])
    if lines[1:] != ["3.5.9"]:
        raise ValueError("Recovery requires unchanged PySpark 3.5.9")
    launcher = spark / "bin/spark-class"
    text = launcher.read_text()
    if ('"$RUNNER" -Xmx128m $SPARK_LAUNCHER_OPTS' not in text or 'exec "${CMD[@]}"' not in text
            or "$SPARK_SUBMIT_OPTS" in text):
        raise ValueError("Actual Spark launcher differs from verified 3.5.9 child-option boundary")
    jars = list((spark / "jars").glob("spark-launcher_*-3.5.9.jar"))
    if len(jars) != 1:
        raise ValueError("Expected one Spark 3.5.9 launcher jar")
    original_names = ["campaign.json", "INPUT_DOMAIN.json", *[f"manifests/sf{s}.json" for s in (1, 10, 30, 100, 300)]]
    original_names += [f"oracles/{suite}-sf{s}.{suffix}" for suite, scales in
                      (("baseline", (1, 10, 30, 100, 300)), ("complex", (30, 100, 300)))
                      for s in scales for suffix in ("json", "log")]
    data = {}
    for scale, dataset in original["datasets"].items():
        root = Path(dataset["path"])
        manifest = json.loads((root / "manifest.json").read_bytes())
        if sha(root / "manifest.json") != dataset["manifest_sha256"]:
            raise ValueError("Actual data manifest differs from v1")
        for table in manifest["tables"].values():
            for file in table["files"]:
                data[str(root / file["path"])] = file["sha256"]
    # The launcher sources spark-env.sh and reads spark-defaults.conf; retain
    # their bytes as well whenever this unchanged installation supplies them.
    launcher_files = [launcher, *jars, *[spark / "bin" / name for name in
                      ("spark-submit", "find-spark-home", "load-spark-env.sh")]]
    conf = Path(os.environ.get("SPARK_CONF_DIR") or spark / "conf")
    launcher_files += [conf / name for name in ("spark-env.sh", "spark-defaults.conf") if (conf / name).is_file()]
    import zipfile
    with zipfile.ZipFile(jars[0]) as jar:
        builder = jar.read("org/apache/spark/launcher/SparkSubmitCommandBuilder.class")
        if b"SPARK_SUBMIT_OPTS" not in builder:
            raise ValueError("Actual 3.5.9 command builder does not recognize child SPARK_SUBMIT_OPTS")
    diagnostic = json.loads(args.diagnostic.read_bytes())
    value = {"schema_version": "h100-jvm-execution-profile-v1", "campaign_id": CAMPAIGN_ID,
             "created_at_utc": utcnow(), "source_commit": SOURCE_COMMIT, "source_tree_sha256": SOURCE_TREE,
             "source_files_sha256": source["files_sha256"], "spark_submit_opts": SPARK_SUBMIT_OPTS,
             "java": java_identity(), "environment": environment(),
             "environment_sources": {str(p.resolve()): sha(p) for p in args.env_file},
             "launcher_files_sha256": {str(p): sha(p) for p in launcher_files},
             "python_configs": original["configs"], "settings": original["settings"], "spark_sql_settings": {"shuffle_partitions": 120, "adaptive_enabled": True},
             "diagnostic": diagnostic, "original_inputs": {"evidence_root": str(args.original_evidence),
                 "files_sha256": {name: sha(args.original_evidence / name) for name in original_names},
                 "dataset_files_sha256": data}}
    check_local(value, args.source, original=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as handle:
        handle.write(canonical(value))
    print(json.dumps({"status": "CAPTURED", "profile_sha256": sha(args.output)}))


def gate(args):
    value = json.loads(args.profile.read_bytes())
    idle = check_local(value, args.source, original=args.verify_original, data=args.verify_data)
    record = {"schema_version": "h100-jvm-execution-profile-gate-v1", "phase": args.phase,
              "status": "PASS", "profile_sha256": sha(args.profile), "completed_at_utc": utcnow(),
              "java_files_sha256": value["java"]["files_sha256"], "java_version": value["java"]["version_output"],
              "environment_sources": value["environment_sources"], "environment": environment(), "exclusive_host": idle}
    existing = [json.loads(line) for line in args.gates.read_text().splitlines()] if args.gates.exists() else []
    if len(existing) >= len(GATE_PHASES) or args.phase != GATE_PHASES[len(existing)]:
        raise ValueError("Execution-profile phases must append exactly once in fixed recovery order")
    for index, prior in enumerate(existing):
        if prior.get("phase") != GATE_PHASES[index] or prior.get("profile_sha256") != record["profile_sha256"]:
            raise ValueError("Earlier execution-profile gate changed")
    with args.gates.open("ab") as handle:
        handle.write((json.dumps(record, sort_keys=True, allow_nan=False) + "\n").encode())
        handle.flush()
        os.fsync(handle.fileno())
    print(json.dumps(record, sort_keys=True))


def copy_original(args):
    value = validate_profile(json.loads(args.profile.read_bytes()))
    check_local(value, args.source, original=True, data=True)
    root = Path(value["original_inputs"]["evidence_root"])
    for name, expected in value["original_inputs"]["files_sha256"].items():
        if name in ("campaign.json", "INPUT_DOMAIN.json"):
            continue
        target = args.evidence / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as handle:
            handle.write((root / name).read_bytes())
        if sha(target) != expected:
            raise ValueError(f"Original evidence copy differs: {name}")
    # A new input-domain record explicitly binds its reuse to v1 and this profile.
    domain = json.loads((root / "INPUT_DOMAIN.json").read_bytes())
    domain["reused_original_domain"] = {"path": str(root / "INPUT_DOMAIN.json"),
        "sha256": value["original_inputs"]["files_sha256"]["INPUT_DOMAIN.json"], "profile_sha256": sha(args.profile),
        "same_physical_files_verified": 1654, "verified_at_utc": utcnow()}
    with (args.evidence / "INPUT_DOMAIN.json").open("xb") as handle:
        handle.write(canonical(domain))
    # Preserve diagnostic inputs inside the prestart archive as root files.
    for field in ("driver_log", "validation"):
        with (args.evidence / ("diagnostic-sf100-" + field + Path(value["diagnostic"][field + "_path"]).suffix)).open("xb") as handle:
            handle.write(Path(value["diagnostic"][field + "_path"]).read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("capture")
    sub.add_argument("--source", type=Path, required=True)
    sub.add_argument("--original-evidence", type=Path, required=True)
    sub.add_argument("--env-file", type=Path, action="append", required=True)
    sub.add_argument("--python", required=True)
    sub.add_argument("--diagnostic", type=Path, required=True)
    sub.add_argument("--output", type=Path, required=True)
    sub.set_defaults(function=capture)
    for name, function in (("gate", gate), ("copy-original", copy_original)):
        sub = commands.add_parser(name)
        sub.add_argument("--profile", type=Path, required=True)
        sub.add_argument("--source", type=Path, required=True)
        if name == "gate":
            sub.add_argument("--gates", type=Path, required=True)
            sub.add_argument("--phase", choices=GATE_PHASES, required=True)
            sub.add_argument("--verify-original", action="store_true")
            sub.add_argument("--verify-data", action="store_true")
        else:
            sub.add_argument("--evidence", type=Path, required=True)
        sub.set_defaults(function=function)
    args = parser.parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
