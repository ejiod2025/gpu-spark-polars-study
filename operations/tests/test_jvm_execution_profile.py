"""Operational profile proof; no VM, Java, Spark, or data job is launched."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("jvm_profile", Path(__file__).resolve().parents[1] / "jvm_execution_profile.py")
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)
REPO = Path(__file__).resolve().parents[2]


def flag_log(heap=profile.HEAP_BYTES):
    return "\n".join(["[Global flags]", " uintx GCLockerRetryAllocationCount = 100 {product} {command line}",
                      f" size_t MaxHeapSize = {heap} {{product}} {{command line}}",
                      " bool UseG1GC = true {product} {ergonomic}",
                      " bool UnlockDiagnosticVMOptions = true {diagnostic} {command line}",
                      " bool PrintFlagsFinal = true {product} {command line}"])


@pytest.fixture
def value():
    plan = json.loads((REPO / "evidence-research/20261027/h100-confirmatory-replacement-v1/campaign.json").read_bytes())
    home = "/opt/java17"
    names = ["campaign.json", "INPUT_DOMAIN.json", *[f"manifests/sf{s}.json" for s in (1, 10, 30, 100, 300)]]
    names += [f"oracles/{suite}-sf{s}.{suffix}" for suite, scales in
              (("baseline", (1, 10, 30, 100, 300)), ("complex", (30, 100, 300)))
              for s in scales for suffix in ("json", "log")]
    environment = {name: None for name in profile.ENV_NAMES}
    environment["SPARK_SUBMIT_OPTS"] = profile.SPARK_SUBMIT_OPTS
    return {"schema_version": "h100-jvm-execution-profile-v1", "campaign_id": profile.CAMPAIGN_ID,
            "source_commit": profile.SOURCE_COMMIT, "source_tree_sha256": profile.SOURCE_TREE,
            "source_files_sha256": plan["source"]["files_sha256"], "created_at_utc": "2026-10-27T00:00:00Z",
            "spark_submit_opts": profile.SPARK_SUBMIT_OPTS,
            "java": {"binary": home + "/bin/java", "java_home": home, "version": "17.0.20.1",
                     "version_output": 'openjdk version "17.0.20.1"', "files_sha256": {
                         home + "/" + name: "a" * 64 for name in ("bin/java", "lib/server/libjvm.so", "lib/modules", "release")}},
            "environment_sources": {"/original/h100-env.sh": "a" * 64}, "environment": environment,
            "launcher_files_sha256": {"/pyspark/bin/spark-class": "b" * 64},
            "python_configs": plan["configs"], "settings": plan["settings"], "spark_sql_settings": {"shuffle_partitions": 120, "adaptive_enabled": True},
            "diagnostic": {"status": "PASS", "completed_at_utc": "2026-10-27T00:00:00Z", "driver_log_path": "/diag/spark_cpu.log",
                           "driver_log_sha256": "a" * 64, "validation_path": "/diag/validation.json", "validation_sha256": "b" * 64},
            "original_inputs": {"evidence_root": "/original/evidence", "files_sha256": {name: "c" * 64 for name in names},
                                "dataset_files_sha256": {f"/data/sf1/t/{i}.parquet": "d" * 64 for i in range(1654)}}}


def test_actual_driver_flag_table_requires_one_correct_child_table():
    observed = profile.parse_driver_flags(flag_log())
    assert observed["GCLockerRetryAllocationCount"] == 100
    assert observed["MaxHeapSize"] == profile.HEAP_BYTES
    assert observed["UseG1GC"] is True
    for text in (flag_log().replace("= 100", "= 2"), flag_log(heap=1024), flag_log().replace("UseG1GC = true", "UseG1GC = false"),
                 flag_log().replace("PrintFlagsFinal = true", "PrintFlagsFinal = false"), flag_log() + "\n" + flag_log(),
                 "-XX:GCLockerRetryAllocationCount=100 -Xmx96g -XX:+UseG1GC"):
        with pytest.raises(ValueError):
            profile.parse_driver_flags(text)


def test_diagnostic_and_original_1654_byte_identity_are_required(value):
    assert profile.validate_profile(value) is value
    for change in ("old_campaign", "diagnostic_failed", "missing_file", "heap_change", "source_drift", "missing_oracle_log"):
        bad = copy.deepcopy(value)
        if change == "old_campaign":
            bad["campaign_id"] = "replacement"
        elif change == "diagnostic_failed":
            bad["diagnostic"]["status"] = "FAIL"
        elif change == "missing_file":
            bad["original_inputs"]["dataset_files_sha256"].popitem()
        elif change == "heap_change":
            bad["settings"]["driver_memory"] = "128g"
        elif change == "source_drift":
            bad["source_files_sha256"]["worker.py"] = "e" * 64
        else:
            del bad["original_inputs"]["files_sha256"]["oracles/complex-sf100.log"]
        with pytest.raises(ValueError):
            profile.validate_profile(bad)


@pytest.mark.parametrize("name", profile.BANNED_ENV)
@pytest.mark.parametrize("value_in_env", ["", "-XX:+PrintFlagsFinal"])
def test_global_and_launcher_options_must_be_absent_even_when_empty(value, name, value_in_env):
    value["environment"][name] = value_in_env
    with pytest.raises(ValueError, match="only through SPARK_SUBMIT_OPTS"):
        profile.validate_profile(value)


def test_runtime_environment_and_java_drift_fail_before_idle_probe(value, monkeypatch, tmp_path):
    monkeypatch.setattr(profile, "java_identity", lambda: {**value["java"], "version": "17.0.21"})
    with pytest.raises(ValueError, match="Java binary/build drift"):
        profile.check_local(value, tmp_path)
    monkeypatch.setattr(profile, "java_identity", lambda: value["java"])
    monkeypatch.setattr(profile, "environment", lambda: {**value["environment"], "SPARK_SUBMIT_OPTS": ""})
    with pytest.raises(ValueError, match="Inherited execution environment"):
        profile.check_local(value, tmp_path)


def test_gates_append_exactly_once_in_fixed_order_and_bind_profile(tmp_path, value, monkeypatch):
    path = tmp_path / "EXECUTION_PROFILE.json"
    path.write_bytes(profile.canonical(value))
    gates = tmp_path / "EXECUTION_PROFILE_GATES.jsonl"
    monkeypatch.setattr(profile, "check_local", lambda *args, **kwargs: {"eligible": True})
    monkeypatch.setattr(profile, "environment", lambda: value["environment"])
    args = SimpleNamespace(profile=path, source=tmp_path, gates=gates, phase="block-01-before", verify_original=False, verify_data=False)
    with pytest.raises(ValueError, match="fixed recovery order"):
        profile.gate(args)
    args.phase = "readiness-before"
    profile.gate(args)
    assert json.loads(gates.read_text())["profile_sha256"] == profile.sha(path)
    with pytest.raises(ValueError, match="fixed recovery order"):
        profile.gate(args)
    args.phase = "readiness-after"
    profile.gate(args)
    assert len(gates.read_text().splitlines()) == 2
    value["created_at_utc"] = "changed"
    path.write_bytes(profile.canonical(value))
    args.phase = "block-01-before"
    with pytest.raises(ValueError, match="Earlier execution-profile gate changed"):
        profile.gate(args)


def test_preparation_cannot_provision_or_regenerate_and_supervision_has_fixed_profile_gates():
    prepare = (REPO / "operations/prepare_h100_gclocker100.sh").read_text()
    run = (REPO / "operations/run_h100_gclocker100.sh").read_text()
    assert "pip install" not in prepare and "generate_data.py" not in prepare and "research_oracle.py" not in prepare
    assert "copy-original" in prepare and "research_readiness.py" in prepare
    assert "--campaign-id " + profile.CAMPAIGN_ID in prepare and "--seed 20261026" in prepare
    for script in (prepare, run):
        assert "unset JAVA_TOOL_OPTIONS _JAVA_OPTIONS JDK_JAVA_OPTIONS SPARK_LAUNCHER_OPTS" in script
        assert "export SPARK_SUBMIT_OPTS='" + profile.SPARK_SUBMIT_OPTS + "'" in script
    assert run.index('profile_gate "$RESEARCH_BLOCK-before"') < run.index('--max-sessions 40')
    assert run.index('--max-sessions 40') < run.index('profile_gate "$RESEARCH_BLOCK-after"')
    assert run.index('profile_gate "$RESEARCH_BLOCK-after"') < run.index('RESEARCH_PHASE="offhost-backup-$RESEARCH_BLOCK"')
    assert len(profile.GATE_PHASES) == 24


CONTROLLER_SPEC = importlib.util.spec_from_file_location("profile_controller", REPO / "operations/continue_h100_replacement.py")
controller = importlib.util.module_from_spec(CONTROLLER_SPEC)
CONTROLLER_SPEC.loader.exec_module(controller)


def test_recovery_controller_reuses_existing_controller_with_distinct_paths(tmp_path):
    args = SimpleNamespace(repository=REPO, destination=tmp_path / "raw", host="ubuntu@68.209.73.247",
                           evidence=controller.RECOVERY_EVIDENCE, profile="gclocker100")
    obj = controller.Controller(args)
    assert obj.campaign_id == profile.CAMPAIGN_ID
    assert obj.archive == REPO / "evidence-research/20261027/h100-confirmatory-gclocker100-v1"
    assert obj.runner == "run_h100_gclocker100.sh"
    assert "operations/prepare_h100_gclocker100.sh" in obj.tools
    assert "operations/jvm_execution_profile.py" in obj.tools
    assert "operations/run_h100_replacement.sh" not in obj.tools


def test_recovery_registry_binds_profile_and_disallows_a_second_cohort(tmp_path):
    registry = tmp_path / controller.REGISTRY
    registry.parent.mkdir(parents=True)
    registry.write_bytes(controller.json_bytes({"schema_version": "h100-registered-campaign-identities-v1", "campaigns": {}}))
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "PRESTART_MANIFEST.json").write_text("sealed actual bytes")
    entry = controller.register_archive(tmp_path, archive, "a" * 64, "b" * 64)
    assert entry["campaign_id"] == profile.CAMPAIGN_ID
    assert entry["execution_profile_sha256"] == "b" * 64
    controller.register_archive(tmp_path, archive, "a" * 64, "b" * 64)
    with pytest.raises(ValueError, match="single GCLocker100 recovery cohort"):
        controller.register_archive(tmp_path, archive, "c" * 64, "b" * 64)
    with pytest.raises(ValueError, match="existing campaign registry entry differs"):
        controller.register_archive(tmp_path, archive, "a" * 64, "c" * 64)


def test_recovery_controller_requires_repeated_readiness_and_same_physical_inputs(value):
    plan = json.loads((REPO / "evidence-research/20261027/h100-confirmatory-replacement-v1/campaign.json").read_bytes())
    status = {"execution_profile": value, "execution_profile_sha256": "a" * 64, "plan": plan,
              "execution_profile_gates": [{"phase": phase, "status": "PASS", "profile_sha256": "a" * 64}
                                          for phase in ("readiness-before", "readiness-after")],
              "input_domain": json.loads((REPO / "evidence-research/20261027/h100-confirmatory-replacement-v1/INPUT_DOMAIN.json").read_bytes())}
    status["input_domain"]["reused_original_domain"] = {"same_physical_files_verified": 1654, "profile_sha256": "a" * 64,
                "sha256": value["original_inputs"]["files_sha256"]["INPUT_DOMAIN.json"]}
    obj = object.__new__(controller.Controller)
    obj.repo = REPO
    obj.validate_recovery_prepared(status)
    for change in ("unrepeated_readiness", "other_profile_gate", "physical_data_not_verified", "settings"):
        bad = copy.deepcopy(status)
        if change == "unrepeated_readiness":
            bad["execution_profile_gates"] = []
        elif change == "other_profile_gate":
            bad["execution_profile_gates"][0]["profile_sha256"] = "b" * 64
        elif change == "physical_data_not_verified":
            bad["input_domain"]["reused_original_domain"]["same_physical_files_verified"] = 1653
        else:
            bad["plan"]["settings"]["cpus"] = 27
        with pytest.raises(ValueError):
            obj.validate_recovery_prepared(bad)
