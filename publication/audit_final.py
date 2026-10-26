#!/usr/bin/env python3
"""Verify this study's completed private publication against saved artifacts."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import zipfile

B = Path(__file__).resolve().parent.parent


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    report = B / 'reports/complex-formal-v2-20261025'
    hosted = B / 'publication/hosted-study'
    deployment = read(B / 'artifacts/complex/FINAL_DEPLOYMENT.json')
    baseline = read(B / 'reports/crossover-20261025/correctness.json')
    complex_proof = read(report / 'correctness.json')
    integrity = read(B / 'artifacts/complex/postrun-input-integrity.json')
    bundle = B / 'publication/dist/final-evidence-20261025.zip'
    archive = Path(deployment['archive'])
    assert baseline['passed'] and baseline['all_selected_scales_validated']
    assert (len(baseline['included_scales']), len(baseline['comparisons']), len(baseline['gpu_plans'])) == (5, 360, 120)
    assert complex_proof['passed'] and complex_proof['all_full_suites_validated']
    assert not complex_proof['excluded_cases'] and not complex_proof['mixed_cleanup_protocols']
    assert complex_proof['query_cleanup_policies'] == ['verified_gc']
    assert [len(complex_proof[k]) for k in ['included_cases', 'comparisons', 'cleanup_checks', 'gpu_plans', 'sessions']] == [18, 216, 216, 72, 9]
    assert integrity['all_pass'] and len(integrity['datasets']) == 5
    assert sum(x['parquet_files_checked'] for x in integrity['datasets']) == 1773
    assert all(x['all_file_hashes_match'] and x['no_missing_or_extra_parquet_files'] for x in integrity['datasets'])
    assert integrity['integrity_script_sha256'] == sha(B / 'artifacts/complex/verify_input_integrity.py')
    assert integrity['validator_source_sha256'] == sha(B / 'generate_data.py')
    snapshot = B / 'artifacts/source-snapshots/complex-v2'
    recorded = read(snapshot / 'snapshot.json')['verified_sha256']
    assert len(recorded) == 18
    assert recorded == read(B / 'results/complex-formal-v2-20261025/environment.json')['source_sha256']
    for name, wanted in recorded.items():
        assert sha(snapshot / name) == wanted
    with zipfile.ZipFile(bundle) as zipped:
        assert zipped.testzip() is None
        prefix = next(n for n in zipped.namelist() if n.endswith('/BUNDLE.json')).removesuffix('BUNDLE.json')
        meta = json.loads(zipped.read(prefix + 'BUNDLE.json'))
        assert len(meta['runs']) == 7
        final_run = next(x for x in meta['runs'] if x['run'] == 'complex-formal-v2-20261025')
        assert len(final_run['sessions']) == 9 and final_run['eligible_for_complete_performance_summary']
        assert not final_run['pending_declared_scale_directories'] and not final_run['pending_unselected_schedule_entries']
        assert set(final_run['selected_scale_directories']) == {'sf30', 'sf100', 'sf300'}
        rows = list(csv.DictReader(zipped.read(prefix + 'SUMMARY.csv').decode().splitlines()))
        assert rows and 'run' in rows[0] and all(x['run'] != 'complex-formal-20261025' for x in rows)
        assert json.loads(zipped.read(prefix + 'artifacts/complex/postrun-input-integrity.json'))['all_pass']
    with tarfile.open(archive, 'r:gz') as packed:
        members = {x.name.removeprefix('./'): x for x in packed.getmembers() if x.isfile()}
        assert not any(part.startswith('._') for name in members for part in Path(name).parts), 'Unexpected macOS metadata in deployment archive'
        web = packed.extractfile(members['dist/data.json']).read()
        payload = json.loads(web)
        complex_data = payload['suites']['complex']
        assert len(complex_data['rows']) == 18 and len(complex_data['totals']) == 3
        assert complex_data['status'] == 'complete' and not complex_data['pending_scales']
        assert len(payload['suites']['original']['rows']) == 30
        assert payload['publication']['hosting'] == 'owner-private' and not payload['publication']['public_release']
        assert 'Private private' not in web.decode()
        assert packed.extractfile(members['dist/downloads/reproducibility-draft.zip']).read() == bundle.read_bytes()
        assert web == (hosted / 'dist/data.json').read_bytes()
        manifest_key = 'dist/.openai/hosting.json'
        assert json.loads(packed.extractfile(members[manifest_key]).read())['project_id'] == deployment['project_id']
    expected = {(x['scale'], x['query']): x for x in csv.DictReader((report / 'summary.csv').open())}
    assert set(expected) == {(x['scale'], x['query']) for x in complex_data['rows']}
    for row in complex_data['rows']:
        for engine in ('polars', 'spark_cpu', 'spark_gpu'):
            for statistic in ('median', 'min', 'max'):
                assert row['times'][engine][statistic] == float(expected[(row['scale'], row['query'])][f'{engine}_{statistic}_seconds'])
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=hosted, text=True).strip()
    assert head == deployment['source_commit']
    assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=hosted, text=True).strip()
    for name in ('complex-sf30.png', 'complex-sf100.png', 'complex-sf300.png'):
        assert (report / name).read_bytes() == (hosted / 'dist/assets' / name).read_bytes()
    assert deployment['status'] == 'succeeded' and deployment['url'] and deployment['audience'] == 'owner-private'
    result = {'requirements_verified': True,
        'baseline': {'scales': 5, 'outputs': 360, 'gpu_plans': 120},
        'complex': {'scales': 3, 'cases': 18, 'outputs': 216, 'cleanup_barriers': 216, 'gpu_plans': 72, 'sessions': 9, 'exclusions': 0},
        'postrun_inputs': {'scales': 5, 'files': 1773, 'bytes': sum(x['parquet_bytes_checked'] for x in integrity['datasets']), 'passed': True},
        'evidence_roots': 7, 'legacy_incident_excluded': True, 'runtime_sources_verified': 18,
        'hosted_commit': head, 'hosted_data_sha256': hashlib.sha256(web).hexdigest(),
        'evidence_zip_sha256': sha(bundle), 'archive_sha256': sha(archive),
        'native_deployment_status': deployment['status'], 'native_deployment_id': deployment['id'],
        'private_url': deployment['url'], 'audience': 'owner-private', 'public_release_cleared': False,
        'browser_verification': 'Final data/layout checked locally; native successful deployment verifies the exact hosted version. Live owner sign-in remains user-controlled.'}
    (B / 'artifacts/complex/FINAL_COMPLETION.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
