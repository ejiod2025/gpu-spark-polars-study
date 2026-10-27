"""Offline proof checks using archived metadata and explicitly synthetic timestamps.

No benchmark, GPU, JVM, source Parquet file, or remote host is accessed.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_reporting"))
import audit_complete as audit

ARCHIVE = ROOT / "evidence-research/20261026/h100-confirmatory-v1"


def json_file(path):
    return json.loads(Path(path).read_text())


@pytest.fixture()
def readiness():
    return json_file(ARCHIVE / "readiness/complex-polars_gpu/sf1/polars_gpu/results.json")


def test_actual_readiness_has_the_two_specific_translation_shapes(readiness):
    errors = {entry["query"]: entry for entry in readiness["query_errors"]}
    assert set(errors) == {"cx_q13", "cx_window"}
    assert all(audit.translation_rejection(error, error) for error in errors.values())


@pytest.mark.parametrize("kind", ["io", "oom", "different_operator", "measured", "boolean_trial"])
def test_unrelated_failure_is_never_a_known_coverage_rejection(readiness, kind):
    reference = next(e for e in readiness["query_errors"] if e["query"] == "cx_window")
    error = copy.deepcopy(reference)
    if kind == "io":
        error.update(type="OSError", message="Operation not supported")
    elif kind == "oom":
        error.update(message="CUDA out of memory; unsupported allocation size")
    elif kind == "different_operator":
        error["message"] = error["message"].replace("UnaryFunction", "DifferentOperation")
    elif kind == "measured":
        error.update(phase="measured", trial=1)
    else:
        error["trial"] = False
    assert not audit.translation_rejection(error, reference)


def chronology():
    start = datetime(2026, 10, 26, 20, tzinfo=timezone.utc)
    attempts = [{"started_at_utc": start.isoformat(), "ended_at_utc": (start + timedelta(seconds=1)).isoformat()},
                {"started_at_utc": (start + timedelta(seconds=2)).isoformat(),
                 "ended_at_utc": (start + timedelta(seconds=3)).isoformat()}]
    inventory = {"1": {"region/data.parquet": {}}, "10": {"region/data.parquet": {}}}
    def gate(seconds):
        return {"completed_at_utc": (start + timedelta(seconds=seconds)).isoformat(),
                "checks": [{"scale": scale, "status": "pass", "files": 1} for scale in inventory]}
    journal = {"completed_at_utc": (start + timedelta(seconds=5)).isoformat(),
               "input_integrity_gates": [gate(-1), gate(4)]}
    return journal, attempts, inventory


def test_byte_proof_requires_real_temporal_bracketing():
    journal, attempts, inventory = chronology()
    checks = audit.Checks()
    audit.check_chronology(journal, attempts, inventory, checks)
    assert not checks.findings
    journal["input_integrity_gates"][1]["completed_at_utc"] = journal["input_integrity_gates"][0]["completed_at_utc"]
    checks = audit.Checks()
    audit.check_chronology(journal, attempts, inventory, checks)
    assert {f["check_id"] for f in checks.findings} == {"post_byte_gate"}


@pytest.mark.parametrize("kind", ["overlap", "late_gate", "duplicate_scale", "wrong_file_count"])
def test_incomplete_or_misordered_byte_proofs_fail(kind):
    journal, attempts, inventory = chronology()
    if kind == "overlap":
        attempts[1]["started_at_utc"] = attempts[0]["started_at_utc"]
    elif kind == "late_gate":
        journal["completed_at_utc"] = attempts[-1]["ended_at_utc"]
    elif kind == "duplicate_scale":
        journal["input_integrity_gates"][1]["checks"][1]["scale"] = "1"
    else:
        journal["input_integrity_gates"][1]["checks"][0]["files"] = 2
    checks = audit.Checks()
    audit.check_chronology(journal, attempts, inventory, checks)
    assert checks.findings


def cache_schedule():
    files = {"region/data.parquet": {"bytes": 8000}, "nation/data.parquet": {"bytes": 4000}}
    # Deliberately low residency must be retained, not turned into an exclusion.
    observation = {"cache": {"files": {name: {"bytes": value["bytes"], "pages": 2, "resident_pages": 0}
                                          for name, value in files.items()},
                             "errors": [], "bytes": 12000, "pages": 4, "resident_pages": 0, "resident_fraction": 0.0},
                   "host": {"synthetic_notice": "SYNTHETIC cache structure test"}}
    return {"cache_observations": {phase: copy.deepcopy(observation) for phase in
             ("before_preheat", "after_preheat", "after_worker")}}, files


def test_zero_residency_is_measured_without_a_new_rejection_threshold():
    schedule, files = cache_schedule()
    checks = audit.Checks()
    audit.check_cache(schedule, files, checks, "SYNTHETIC-session")
    assert not checks.findings


@pytest.mark.parametrize("kind", ["missing_phase", "missing_file", "probe_error", "invented_fraction"])
def test_cache_proof_retains_every_file_and_observation(kind):
    schedule, files = cache_schedule()
    if kind == "missing_phase":
        del schedule["cache_observations"]["after_worker"]
    else:
        cache = schedule["cache_observations"]["after_worker"]["cache"]
        if kind == "missing_file":
            del cache["files"]["region/data.parquet"]
        elif kind == "probe_error":
            cache["errors"] = [{"path": "region/data.parquet", "error": "mincore failed"}]
        else:
            cache["resident_fraction"] = 1.0
    checks = audit.Checks()
    audit.check_cache(schedule, files, checks, "SYNTHETIC-session")
    assert checks.findings


@pytest.mark.parametrize("config", audit.CONFIGS)
def test_archived_readiness_effective_configuration(config, readiness):
    engine = audit.CONFIGS[config]
    value = json_file(ARCHIVE / f"readiness/baseline-{config}/sf1/{engine}/results.json")
    checks = audit.Checks()
    audit.check_config(value, {"config_id": config, "engine": engine, "session_id": "readiness-only"}, readiness,
                       audit.load_frozen_checks(ARCHIVE / "campaign.json.source"), checks)
    assert not checks.findings


@pytest.mark.parametrize("kind", ["threads", "heap", "gpu_plugin", "gpu_tasks", "gpu_executor"])
def test_effective_configuration_drift_is_explicit_failure(kind, readiness):
    config = "polars_gpu" if kind == "gpu_executor" else "spark_gpu"
    engine = audit.CONFIGS[config]
    value = json_file(ARCHIVE / f"readiness/baseline-{config}/sf1/{engine}/results.json")
    if kind == "threads":
        value["cpu_threads"] = 25
    elif kind == "heap":
        value["spark_conf"]["spark.driver.memory"] = "8g"
    elif kind == "gpu_plugin":
        value["spark_conf"]["spark.plugins"] = ""
    elif kind == "gpu_tasks":
        value["spark_conf"]["spark.rapids.sql.concurrentGpuTasks"] = "8"
    else:
        value["polars_gpu"]["executor"] = "in-memory"
    checks = audit.Checks()
    audit.check_config(value, {"config_id": config, "engine": engine, "session_id": "SYNTHETIC-mutated-metadata"}, readiness,
                       audit.load_frozen_checks(ARCHIVE / "campaign.json.source"), checks)
    assert checks.findings


def test_hash_tracking_detects_mutation_and_duplicate_json(tmp_path):
    path = tmp_path / "fixture.json"
    path.write_text('{"synthetic":true}')
    inputs = audit.Inputs()
    first = inputs.sha(path)
    assert inputs.json(path)["synthetic"] is True
    assert inputs.hashes[str(path.resolve())] == first
    path.write_text('{"synthetic":true,"changed":true}')
    with pytest.raises(ValueError, match="changed during audit"):
        inputs.sha(path)
    other = tmp_path / "duplicate.json"
    other.write_text('{"status":"FAIL","status":"PASS"}')
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        inputs.json(other)


def test_direct_no_B_loader_preserves_complete_frozen_snapshot(tmp_path):
    snapshot = tmp_path / "immutable-source"
    shutil.copytree(ARCHIVE / "campaign.json.source", snapshot)
    def tree():
        return {str(path.relative_to(snapshot)): audit.digest(path.read_bytes()) if path.is_file() else None
                for path in snapshot.rglob("*")}
    before = tree()
    code = ("import sys; from pathlib import Path; "
            f"sys.path.insert(0,{str(ROOT / 'research_reporting')!r}); import audit_complete as a; "
            "assert sys.dont_write_bytecode is False; "
            f"m=a.load_frozen_checks(Path({str(snapshot)!r})); "
            "assert callable(m.check_polars_gpu_metadata); assert sys.dont_write_bytecode is False")
    environment = dict(os.environ)
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    environment.pop("PYTHONPYCACHEPREFIX", None)
    # Deliberately no -B: this is the standalone/direct-loader interpreter mode.
    result = subprocess.run([sys.executable, "-c", code], env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert tree() == before
    assert not list(snapshot.rglob("__pycache__"))


@pytest.mark.parametrize("caller_flag", [False, True])
def test_frozen_loader_restores_bytecode_flag_after_error(tmp_path, monkeypatch, caller_flag):
    path = tmp_path / "analyze_results.py"
    path.write_text("import sys\nassert sys.dont_write_bytecode is True\nraise RuntimeError('deliberate loader failure')\n")
    original = path.read_bytes()
    monkeypatch.setattr(sys, "dont_write_bytecode", caller_flag)
    with pytest.raises(RuntimeError, match="deliberate loader failure"):
        audit.load_frozen_checks(tmp_path)
    assert sys.dont_write_bytecode is caller_flag
    assert path.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["analyze_results.py"]


def test_artifact_mapping_never_follows_escape_or_symlink(tmp_path):
    with pytest.raises(ValueError, match="Unsafe artifact"):
        audit.contained(tmp_path, "../escape")
    target = tmp_path / "outside"
    target.write_text("SYNTHETIC")
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "link").symlink_to(target)
    with pytest.raises(ValueError, match="Escaping/symlink"):
        audit.contained(root, "link")


def synthetic_sensitivity():
    primary, secondary = [], []
    pairs = (("polars_cpu_200", "spark_gpu"), ("spark_cpu", "spark_gpu"),
             ("polars_cpu_144", "polars_gpu"), ("polars_cpu_200", "polars_gpu"))
    for numerator, denominator in pairs:
        for suite, scale in audit.CELLS:
            common = {"contrast_id": f"{numerator}_over_{denominator}-{suite}-sf{scale}",
                      "numerator": numerator, "denominator": denominator, "suite": suite, "scale": scale}
            unsupported = denominator == "polars_gpu" and suite == "complex"
            if not unsupported:
                primary.append({**common, "endpoint": "seconds", "geometric_mean_speedup": 2.0, "ci95_pointwise": [1.5, 2.5]})
            secondary.append({**common, "endpoint": "host_result_seconds", "status": "incomplete" if unsupported else "complete",
                "n_blocks": 0 if unsupported else 10, "missing_blocks": ["SYNTHETIC-missing"] if unsupported else [],
                "geometric_mean_speedup": None if unsupported else .5, "ci95_pointwise": None if unsupported else [.4, .6],
                "direction_changed_vs_action_point_estimate": None if unsupported else True})
    return {"synthetic": True, "primary": primary, "secondary_host_result": secondary}


def test_descriptive_sensitivity_keeps_all_pairs_and_no_secondary_tests(tmp_path):
    rows = audit.sensitivity_rows(synthetic_sensitivity())
    assert len(rows) == 32 and sum(row["host_status"] == "incomplete" for row in rows) == 6
    assert all("p_raw" not in row and "p_holm" not in row for row in rows)
    audit.write_sensitivity(tmp_path, rows, synthetic=True)
    text = (tmp_path / "HOST_RESULT_SENSITIVITY.md").read_text()
    assert "SYNTHETIC TEST ONLY" in text and "pointwise" in text and "No new hypothesis tests" in text


def test_sensitivity_refuses_missing_pair_or_invented_direction():
    value = synthetic_sensitivity()
    value["secondary_host_result"][0]["direction_changed_vs_action_point_estimate"] = False
    with pytest.raises(ValueError, match="direction-change"):
        audit.sensitivity_rows(value)
    value["secondary_host_result"].pop()
    with pytest.raises(ValueError, match="all 32"):
        audit.sensitivity_rows(value)


# These temporary profile/log/receipt fixtures are visibly fabricated mechanism
# tests. They reference copied manifest/oracle identities without reading data.
RECOVERY_ARCHIVE = ROOT / 'evidence-research/20261027/h100-confirmatory-replacement-v1'
FLAGS = '\n'.join((
    '     uintx GCLockerRetryAllocationCount = 100 {product} {command line}',
    '    size_t MaxHeapSize = 103079215104 {product} {command line}',
    '      bool UseG1GC = true {product} {ergonomic}',
    '      bool UnlockDiagnosticVMOptions = true {diagnostic} {command line}',
    '      bool PrintFlagsFinal = true {product} {command line}',
)) + '\n'


def recovery_stamp(seconds):
    return (datetime(2026, 10, 27, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat()


@pytest.fixture
def recovery_profile(tmp_path):
    helper = audit.execution_profile_helpers()
    archive = tmp_path / 'FABRICATED-prestart'
    artifacts = tmp_path / 'FABRICATED-harvest/campaign-results'
    artifacts.mkdir(parents=True)
    plan = json_file(RECOVERY_ARCHIVE / 'campaign.json')
    plan['campaign_id'] = audit.RECOVERY_CAMPAIGN_ID
    original_names = ['campaign.json', 'INPUT_DOMAIN.json',
                      *[f'manifests/sf{s}.json' for s in (1, 10, 30, 100, 300)]]
    original_names += [f'oracles/{suite}-sf{s}.{suffix}'
        for suite, scales in (('baseline', (1, 10, 30, 100, 300)), ('complex', (30, 100, 300)))
        for s in scales for suffix in ('json', 'log')]
    original_files = {}
    for name in original_names:
        source = RECOVERY_ARCHIVE / name
        target = archive / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        original_files[name] = audit.digest(target.read_bytes())
    physical = {}
    for scale, dataset in plan['datasets'].items():
        manifest = json_file(archive / f'manifests/sf{scale}.json')
        physical.update({str(Path(dataset['path']) / name): entry['sha256']
                         for name, entry in audit.manifest_files(manifest).items()})
    diagnostic_validation = {
        'schema_version': 'h100-gclocker100-feasibility-validation-v1', 'status': 'PASS',
        'source_commit': audit.SOURCE_COMMIT, 'engine': 'spark_cpu', 'suite': 'complex', 'scale': '100',
        'runs': 1, 'query_seed': 838471203,
        'manifest_sha256': plan['datasets']['100']['manifest_sha256'],
        'oracle_sha256': plan['oracles']['complex-sf100']['sha256'], 'results_sha256': 'e' * 64,
        'successful_outputs_compared': 12, 'queries': list(audit.QUERIES['complex']), 'cleanup_records_checked': 12,
        'records': [{'query': q, 'phase': phase, 'trial': trial, 'passed': True, 'comparison': {'passed': True}}
                    for q in audit.QUERIES['complex'] for phase, trial in (('warmup', 0), ('measured', 1))],
        'driver_flags': helper.parse_driver_flags(FLAGS), 'fabricated_test_only': True}
    diagnostic_bytes = audit.canonical_bytes(diagnostic_validation)
    java_home = '/FABRICATED-TEST/java17'
    profile = {'schema_version': 'h100-jvm-execution-profile-v1',
        'campaign_id': audit.RECOVERY_CAMPAIGN_ID, 'source_commit': audit.SOURCE_COMMIT,
        'source_tree_sha256': audit.SOURCE_TREE, 'created_at_utc': recovery_stamp(10),
        'spark_submit_opts': helper.SPARK_SUBMIT_OPTS,
        'source_files_sha256': plan['source']['files_sha256'], 'settings': plan['settings'], 'python_configs': plan['configs'],
        'spark_sql_settings': {'shuffle_partitions': 120, 'adaptive_enabled': True},
        'java': {'binary': java_home + '/bin/java', 'java_home': java_home, 'version': '17.0.20.1',
                 'version_output': 'FABRICATED Java 17.0.20.1\n',
                 'files_sha256': {java_home + '/' + name: 'a' * 64 for name in
                                  ('bin/java', 'lib/server/libjvm.so', 'lib/modules', 'release')}},
        'environment_sources': {'/FABRICATED-TEST/env': 'b' * 64},
        'launcher_files_sha256': {'/FABRICATED-TEST/spark-class': 'c' * 64},
        'environment': {name: helper.SPARK_SUBMIT_OPTS if name == 'SPARK_SUBMIT_OPTS' else None
                        for name in helper.ENV_NAMES},
        'original_inputs': {'evidence_root': '/FABRICATED-TEST/original',
                            'files_sha256': original_files, 'dataset_files_sha256': physical},
        'diagnostic': {'status': 'PASS', 'completed_at_utc': recovery_stamp(5),
                       'driver_log_path': '/FABRICATED-TEST/driver.log', 'driver_log_sha256': audit.digest(FLAGS.encode()),
                       'validation_path': '/FABRICATED-TEST/validation.json',
                       'validation_sha256': audit.digest(diagnostic_bytes)}}
    (archive / 'diagnostic-sf100-driver_log.log').write_text(FLAGS)
    (archive / 'diagnostic-sf100-validation.json').write_bytes(diagnostic_bytes)
    for suite in ('baseline', 'complex'):
        for engine in ('spark_cpu', 'spark_gpu'):
            path = archive / f'readiness/{suite}-{engine}/sf1/{engine}.log'
            path.parent.mkdir(parents=True)
            path.write_text(FLAGS)
    (archive / audit.PROFILE_NAME).write_bytes(audit.canonical_bytes(profile))
    profile_sha = audit.digest((archive / audit.PROFILE_NAME).read_bytes())
    gates = []
    times = [20, 30, *[value for block in range(10) for value in (99 + 120 * block, 219 + 120 * block)], 1300, 1500]
    for phase, seconds in zip(helper.GATE_PHASES, times):
        gates.append({'schema_version': 'h100-jvm-execution-profile-gate-v1', 'phase': phase,
            'status': 'PASS', 'profile_sha256': profile_sha, 'completed_at_utc': recovery_stamp(seconds),
            'java_files_sha256': profile['java']['files_sha256'], 'java_version': profile['java']['version_output'],
            'environment_sources': profile['environment_sources'], 'environment': profile['environment'],
            'exclusive_host': {'observed_at_utc': recovery_stamp(seconds - .1), 'nvidia_smi_returncode': 0,
                               'gpu_compute_clients': '', 'nvidia_smi_stderr': '', 'benchmark_processes': [], 'eligible': True}})
    def gate_bytes(selected):
        return b''.join((json.dumps(gate, sort_keys=True) + '\n').encode() for gate in selected)
    (archive / audit.PROFILE_GATES_NAME).write_bytes(gate_bytes(gates[:2]))
    (artifacts.parent / audit.PROFILE_GATES_NAME).write_bytes(gate_bytes(gates))
    identity = {'campaign_id': audit.RECOVERY_CAMPAIGN_ID, 'execution_profile_sha256': profile_sha}
    prestart = {'files_sha256': {audit.PROFILE_NAME: profile_sha}}
    attempts = [{'started_at_utc': recovery_stamp(100 + i * 3), 'ended_at_utc': recovery_stamp(101 + i * 3)}
                for i in range(400)]
    return {'archive': archive, 'artifacts': artifacts, 'plan': plan, 'profile': profile, 'gates': gates,
            'identity': identity, 'prestart': prestart, 'attempts': attempts,
            'journal': {'completed_at_utc': recovery_stamp(1400)}, 'gate_bytes': gate_bytes}


def profile_context(fixture):
    return audit.check_execution_profile(fixture['plan'], fixture['identity'], fixture['archive'],
                                         fixture['artifacts'], fixture['prestart'], audit.Inputs())


def test_recovery_profile_proves_24_idle_receipts_and_all_160_actual_spark_jvms(recovery_profile):
    fixture = recovery_profile
    context = profile_context(fixture)
    for i, session in enumerate(fixture['plan']['sessions']):
        run = fixture['artifacts'] / str(i)
        if session['engine'].startswith('spark_'):
            path = run / f"sf{session['scale']}/{session['engine']}.log"
            path.parent.mkdir(parents=True)
            path.write_text(FLAGS)
        audit.check_spark_driver_profile(context, session, run, audit.Inputs())
    assert context['driver_logs'] == 160 and len(context['gates']) == 24
    audit.check_profile_chronology(context, fixture['plan'], fixture['journal'], fixture['attempts'])


@pytest.mark.parametrize('bad', ['missing', 'java_hash', 'java_version', 'env_source', 'global_options', 'busy', 'duplicate', 'nonobject', 'prefix'])
def test_recovery_profile_gate_drift_fails_closed(recovery_profile, bad):
    fixture = recovery_profile
    gates = copy.deepcopy(fixture['gates'])
    if bad == 'missing':
        gates.pop(4)
    elif bad == 'java_hash':
        gates[5]['java_files_sha256']['/FABRICATED-TEST/java17/lib/modules'] = 'd' * 64
    elif bad == 'java_version':
        gates[5]['java_version'] = 'different Java build'
    elif bad == 'env_source':
        gates[5]['environment_sources'] = {'/different': 'd' * 64}
    elif bad == 'global_options':
        gates[5]['environment']['JAVA_TOOL_OPTIONS'] = ''
    elif bad == 'busy':
        gates[5]['exclusive_host']['benchmark_processes'] = [{'pid': 123}]
    elif bad == 'duplicate':
        gates.insert(5, gates[5])
    elif bad == 'nonobject':
        gates[5] = []
    else:
        gates[0]['completed_at_utc'] = recovery_stamp(21)
    (fixture['artifacts'].parent / audit.PROFILE_GATES_NAME).write_bytes(fixture['gate_bytes'](gates))
    with pytest.raises(ValueError):
        profile_context(fixture)


@pytest.mark.parametrize('bad', ['unregistered', 'profile_hash', 'manifest', 'diagnostic', 'readiness', 'duplicate_json'])
def test_recovery_seal_and_original_data_are_required(recovery_profile, bad):
    fixture = recovery_profile
    if bad == 'unregistered':
        fixture['identity'].pop('campaign_id')
    elif bad == 'profile_hash':
        fixture['identity']['execution_profile_sha256'] = 'd' * 64
    elif bad == 'manifest':
        path = fixture['archive'] / 'manifests/sf1.json'
        path.write_text(path.read_text() + '\n')
    elif bad == 'diagnostic':
        (fixture['archive'] / 'diagnostic-sf100-driver_log.log').write_text('missing actual flags\n')
    elif bad == 'readiness':
        (fixture['archive'] / 'readiness/complex-spark_gpu/sf1/spark_gpu.log').write_text('old JVM profile\n')
    else:
        path = fixture['artifacts'].parent / audit.PROFILE_GATES_NAME
        path.write_bytes(path.read_bytes().replace(b'"status": "PASS"', b'"status": "FAIL", "status": "PASS"'))
    with pytest.raises(ValueError):
        profile_context(fixture)


@pytest.mark.parametrize('bad', ['missing_jvm', 'block_before', 'block_after', 'final_after'])
def test_profile_chronology_cannot_replace_per_session_flags(recovery_profile, bad):
    fixture = recovery_profile
    context = profile_context(fixture)
    context['driver_logs'] = 159 if bad == 'missing_jvm' else 160
    if bad == 'block_before':
        context['gates'][2]['completed_at_utc'] = recovery_stamp(101)
    elif bad == 'block_after':
        context['gates'][3]['completed_at_utc'] = recovery_stamp(217)
    elif bad == 'final_after':
        context['gates'][-1]['completed_at_utc'] = recovery_stamp(1399)
    with pytest.raises(ValueError):
        audit.check_profile_chronology(context, fixture['plan'], fixture['journal'], fixture['attempts'])


@pytest.mark.parametrize('bad', ['heap', 'retry', 'collector', 'diagnostic', 'duplicate'])
def test_each_actual_driver_log_must_show_fixed_effective_flags(tmp_path, bad):
    text = FLAGS
    if bad == 'heap':
        text = text.replace('103079215104', '8589934592')
    elif bad == 'retry':
        text = text.replace('Count = 100', 'Count = 2')
    elif bad == 'collector':
        text = text.replace('UseG1GC = true', 'UseG1GC = false')
    elif bad == 'diagnostic':
        text = text.replace('UnlockDiagnosticVMOptions = true', 'UnlockDiagnosticVMOptions = false')
    else:
        text += FLAGS
    path = tmp_path / 'sf100/spark_cpu.log'
    path.parent.mkdir()
    path.write_text(text)
    context = {'helper': audit.execution_profile_helpers(), 'driver_logs': 0}
    with pytest.raises(ValueError):
        audit.check_spark_driver_profile(context, {'engine': 'spark_cpu', 'scale': '100'}, tmp_path, audit.Inputs())
    assert context['driver_logs'] == 0


@pytest.mark.parametrize('bad', ['subset', 'duplicate', 'comparison', 'cleanup', 'oracle', 'manifest', 'seed', 'measured_trial', 'flags'])
def test_diagnostic_receipt_requires_full_sf100_workload_and_all_12_comparisons(recovery_profile, bad):
    fixture = recovery_profile
    validation = json_file(fixture['archive'] / 'diagnostic-sf100-validation.json')
    if bad == 'subset':
        validation['records'].pop()
    elif bad == 'duplicate':
        validation['records'][-1] = copy.deepcopy(validation['records'][0])
    elif bad == 'comparison':
        validation['records'][0]['comparison']['passed'] = False
    elif bad == 'cleanup':
        validation['cleanup_records_checked'] = 11
    elif bad == 'oracle':
        validation['oracle_sha256'] = 'f' * 64
    elif bad == 'manifest':
        validation['manifest_sha256'] = 'f' * 64
    elif bad == 'seed':
        validation['query_seed'] += 1
    elif bad == 'measured_trial':
        validation['records'][1]['trial'] = 3
    else:
        validation['driver_flags']['GCLockerRetryAllocationCount'] = 2
    with pytest.raises(ValueError):
        audit.check_diagnostic_validation(validation, fixture['plan'], audit.execution_profile_helpers().parse_driver_flags(FLAGS))
