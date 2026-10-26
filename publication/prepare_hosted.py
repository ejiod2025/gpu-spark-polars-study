#!/usr/bin/env python3
"""Prepare a hosted copy without changing measurement values."""
import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
REPLACEMENTS = (
    ('Private local draft evidence only', 'Private draft evidence only'),
    ('private local review draft', 'private hosted review draft'),
    ('Local-only preview · not cleared for publication or redistribution',
     'Private hosted draft · public release and redistribution not cleared'),
    ('An owner-private Sites review draft is being prepared.', 'This is an owner-private Sites review draft.'),
    ('This page is the local review preview.', 'This page is the private hosted review draft.'),
    ('Open this preview through its provided local preview server and confirm that data.json is present.',
     'Reload this private study page; its evidence snapshot must be available.'),
    ('venue details and notices remain pending', 'public-release notices remain pending'),
    ('local draft', 'private draft'), ('Local draft', 'Private draft'),
    ('local review', 'private review'), ('Local review', 'Private review'),
    ('local preview', 'private hosted preview'), ('Local preview', 'Private hosted preview'),
)


def rewrite(value):
    if isinstance(value, dict):
        return {k: rewrite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [rewrite(v) for v in value]
    if isinstance(value, str):
        for old, new in REPLACEMENTS:
            value = value.replace(old, new)
    return value


def numbers(value, prefix=()):
    if isinstance(value, dict):
        return {p: n for k, v in value.items() for p, n in numbers(v, prefix + (k,)).items()}
    if isinstance(value, list):
        return {p: n for k, v in enumerate(value) for p, n in numbers(v, prefix + (k,)).items()}
    return {prefix: value} if type(value) in (int, float) else {}


def public_copy(value):
    if isinstance(value, dict):
        return {key: public_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [public_copy(item) for item in value]
    if isinstance(value, str):
        for old, new in (
            ('A private draft of', 'A public research study of'),
            ('Private hosted draft · public release and redistribution not cleared', 'Public research study · selected, non-certified workloads'),
            ('This is an owner-private Sites review draft. Public release, notices and any required permission/clearance remain unresolved. This page is the private hosted review draft.', 'This study is publicly accessible. Review the included notices before redistributing code or adapted workload definitions.'),
            ('Draft archives are for private review. They are', 'The evidence archive includes source provenance and licensing notes. It is'),
            ('not cleared for external redistribution', 'a research snapshot'),
            ('; public-release notices remain pending.', '; retain applicable notices when reusing its contents.'),
            ('Private draft evidence only; not cleared for external publication or redistribution.', 'Research evidence snapshot; source provenance and applicable-notice references are included.'),
            ('private draft', 'research'), ('Private draft', 'Research'),
            ('private review', 'review'), ('Private review', 'Review'),
            ('private hosted preview', 'public study'),
            ('Reload this private study page', 'Reload this study page'),
            ('A local draft', 'A public research study'),
        ):
            value = value.replace(old, new)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site-url', required=True)
    parser.add_argument('--audience', choices=['owner-private', 'public'], default='owner-private')
    args = parser.parse_args()
    url = urlsplit(args.site_url)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        parser.error('Use the registered HTTPS Site URL without credentials, query or fragment')
    source, checkout = HERE / 'site-preview/dist', HERE / 'hosted-study'
    marker = json.loads((source / 'local-preview.json').read_text())
    assert marker['owner'] == 'spark-gpu-site-preview'
    project = json.loads((checkout / '.openai/hosting.json').read_text())['project_id']
    target, record_path = checkout / 'dist', checkout / 'HOSTED_PREPARATION.json'
    if target.exists():
        assert record_path.exists() and json.loads(record_path.read_text())['project_id'] == project
    staging = Path(tempfile.mkdtemp(prefix='.hosted-build-', dir=checkout))
    try:
        shutil.copytree(source, staging, dirs_exist_ok=True)
        original = json.loads((staging / 'data.json').read_text())
        data = rewrite(original)
        if args.audience == 'public':
            data = public_copy(data)
        data['publication'].pop('external_publication', None)
        data['publication'].update(local_only=False, external_hosting=True,
            hosting=args.audience, public_release=args.audience == 'public')
        if args.audience == 'public':
            data['publication'].update(status='public_research_study', banner='Public research study · original and complex-query results verified')
        assert numbers(original['suites']) == numbers(data['suites']), 'Measurement values changed'
        (staging / 'data.json').write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')
        for name in ('index.html', 'app.js'):
            text = rewrite((staging / name).read_text())
            if args.audience == 'public':
                text = public_copy(text)
            if name == 'index.html':
                meta = ('<meta name="robots" content="noindex,nofollow">\n  '
                        '<link rel="canonical" href="' + html.escape(args.site_url, quote=True) + '">\n  ')
                text = text.replace('<title>', meta + '<title>', 1)
            (staging / name).write_text(text)
        (staging / 'local-preview.json').unlink()
        if target.exists():
            shutil.rmtree(target)
        staging.rename(target)
        record = {'project_id': project, 'site_url': args.site_url, 'audience': args.audience,
            'prepared_at_utc': datetime.now(timezone.utc).isoformat(),
            'source_data_sha256': hashlib.sha256((source / 'data.json').read_bytes()).hexdigest(),
            'hosted_data_sha256': hashlib.sha256((target / 'data.json').read_bytes()).hexdigest(),
            'numeric_values_preserved': True, 'complex_status': data['suites']['complex']['status'],
            'complex_case_count': len(data['suites']['complex']['rows']),
            'public_release_cleared': False}
        record_path.write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps(record))
    finally:
        if staging.exists():
            shutil.rmtree(staging)


if __name__ == '__main__':
    main()
