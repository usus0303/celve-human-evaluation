#!/usr/bin/env python3
"""Import evaluation ZIPs or existing files; preserve studies and responses across deployments."""
from contextlib import closing
import argparse
import hashlib
import json
import os
import sqlite3
import tarfile
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from dataset_loader import collect_split, PROJECT, BACKUP, COUNTRIES
from storage import initialize, connect
from assignments import validate_assignments

BASE = Path(__file__).resolve().parent


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as out:
        json.dump(data, out, ensure_ascii=False, indent=2, allow_nan=False)
        out.write('\n')
        out.flush()
        os.fsync(out.fileno())
    tmp.replace(path)
    os.chmod(path, 0o600)


def activate(combined, state):
    validate_assignments(combined)
    payload = json.dumps(combined, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    study_id = 'study_' + hashlib.sha256(payload).hexdigest()[:24]
    combined['study_id'] = study_id
    state = state.resolve()
    initialize(state)
    target = state / 'studies' / (study_id + '.json')
    if not target.exists():
        write_json(target, combined)
    write_json(state / 'active_study.json', {'study_id': study_id})
    print(f'Prepared study: {study_id}')
    print(f'Images: {len(combined["study"]["items"])}; ALL patches: {sum(len(i["patches"]) for i in combined["study"]["items"])}')
    for evaluator, assignment in combined.get('assignments', {}).items():
        print(f'  Evaluator {evaluator}: {assignment["region_name"]}, {len(assignment["image_ids"])} images')
    print(f'Access codes: {state / "access_codes.txt"}')
    print('Start/restart the app to load this study. Existing responses remain in SQLite.')


def snapshot(state, output):
    """Back up SQLite consistently plus study manifests, imported assets and codes."""
    state, output = state.resolve(), output.resolve()
    if not (state / 'responses.sqlite3').exists():
        raise ValueError('No response database exists yet.')
    if output.is_relative_to(state):
        raise ValueError('Write the snapshot outside the state directory.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / 'responses.sqlite3'
        with closing(connect(state)) as source, closing(sqlite3.connect(database)) as dest:
            source.backup(dest)
        with output.open('xb') as raw:
            os.chmod(output, 0o600)
            with tarfile.open(fileobj=raw, mode='w:gz') as archive:
                archive.add(database, arcname='state/responses.sqlite3')
                for name in ('access_codes.txt', 'active_study.json', 'studies', 'imports'):
                    path = state / name
                    if path.exists():
                        archive.add(path, arcname='state/' + name)
    print('Saved complete state snapshot:', output)
    print('Includes imported ZIP assets. External files registered with prepare must be backed up separately.')


def restore(state, archive_path):
    """Restore our snapshot into an empty state directory, never overwrite a study."""
    from bundle_loader import safe_member
    state = state.resolve()
    if state.exists() and any(state.iterdir()):
        raise ValueError('Restore requires an empty state directory. Existing data was not changed.')
    state.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.restore-', dir=state.parent) as tmp:
        with tarfile.open(archive_path, 'r:gz') as archive:
            seen = set()
            for member in archive.getmembers():
                safe_member(member.name)
                if not member.name.startswith('state/') or member.name in seen or not (member.isfile() or member.isdir()):
                    raise ValueError('Invalid snapshot member: ' + member.name)
                seen.add(member.name)
            archive.extractall(tmp, filter='data')
        source = Path(tmp) / 'state'
        for name in ('responses.sqlite3', 'active_study.json', 'access_codes.txt'):
            if not (source / name).is_file():
                raise ValueError('Incomplete snapshot: missing ' + name)
        active = json.loads((source / 'active_study.json').read_text())['study_id']
        if not isinstance(active, str) or not active.startswith('study_') or not active[6:].isalnum():
            raise ValueError('Invalid snapshot study ID.')
        if not (source / 'studies' / (active + '.json')).is_file():
            raise ValueError('Snapshot is missing the active study.')
        with closing(sqlite3.connect(source / 'responses.sqlite3')) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Snapshot database failed its integrity check.')
        if state.exists():
            state.rmdir()
        source.replace(state)
        os.chmod(state, 0o700)
    print('Restored study and responses:', state)
    print('Start the app and sign in with your previous access codes.')


def prepare(args):
    combined = {'schema_version': 'celve_server_v1', 'name': args.name,
                'study': {'countries': [{'id': c, 'name': n} for c, n in COUNTRIES], 'items': []},
                'descriptions': {}, 'analysis': {}, 'assets': {}, 'provenance': []}
    seen_splits, seen_sources = set(), set()
    for file in args.description_file:
        rows = json.loads(file.read_text(encoding='utf-8'))
        if not isinstance(rows, list) or not rows:
            raise ValueError(f'Empty or invalid description run: {file}')
        splits = {r.get('split') for r in rows}
        if len(splits) != 1 or next(iter(splits)) not in [f'split_{i:02d}' for i in range(1, 6)]:
            raise ValueError(f'Description run must have one valid split: {file}')
        split = next(iter(splits))
        if split in seen_splits:
            raise ValueError(f'Two description runs were supplied for {split}. Choose one run per split.')
        seen_splits.add(split)
        options = SimpleNamespace(**vars(args))
        options.split = split
        options.description_file = file.resolve()
        options.output = None
        options.max_mib = 0
        options.tf_file = None
        options.verification_file = None
        result = collect_split(options)
        for ref in result['analysis']:
            if ref['source_key'] in seen_sources and not args.allow_repeated_images:
                raise ValueError('The same source image occurs across splits: ' + ref['source_key'] +
                                 '. Use a non-overlapping study or explicitly set --allow-repeated-images.')
            seen_sources.add(ref['source_key'])
            combined['analysis'][ref['image_id']] = ref
        for key, value in result['assets'].items():
            if key in combined['assets'] and combined['assets'][key] != value:
                raise ValueError('Conflicting asset identity: ' + key)
            combined['assets'][key] = value
        combined['study']['items'].extend(result['study']['items'])
        combined['descriptions'].update(result['descriptions'])
        combined['provenance'].append(result['provenance'])
    if not combined['study']['items']:
        raise ValueError('No eligible images. No active study was changed.')
    activate(combined, args.state)
    for p in combined['provenance']:
        print(f'  {p["split"]}: eligible descriptions={p["eligible_count"]}, included={p["sample_count"]}, '
              f'verification images={p["verification_image_count"]}, empty refined sets={p["empty_refined_count"]}')
        print('  Missing eligible descriptions:', p['verification_without_eligible_description'])
        print('  Description status counts:', p['description_status_counts'])
        print('  Description run:', p['description_file'])
    print('Original images and patches were NOT copied or modified.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=Path(os.environ.get('CELVE_STATE_DIR', BASE / 'state')))
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare', help='Register full eligible data without copying image files')
    p.add_argument('--description-file', type=Path, action='append', required=True,
                   help='Exact chosen run; repeat for a different split. Do not use a wildcard.')
    p.add_argument('--project-root', type=Path, default=PROJECT)
    p.add_argument('--backup-root', type=Path, default=BACKUP)
    p.add_argument('--model-key', default='llama3_2_11b')
    p.add_argument('--limit', type=int, default=0, help='Eligible images per split; 0 = all')
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--name', default='CELVE Human Evaluation')
    p.add_argument('--allow-repeated-images', action='store_true')
    for name in ('selection-file','patch-root','batch-meta','image-resize-root'):
        p.add_argument('--'+name, type=Path)
    sub.add_parser('init', help='Initialize access codes and response database if absent')
    b = sub.add_parser('backup', help='Make a consistent SQLite backup while the server is running')
    b.add_argument('--output', type=Path, required=True)
    z = sub.add_parser('import-zip', help='Import the existing human-evaluation ZIP on any server')
    z.add_argument('--zip', type=Path, required=True)
    z.add_argument('--limit', type=int, default=60, help='Shared image count for each evaluator; 0 = all bundled images')
    z.add_argument('--seed', type=int, default=42)
    z.add_argument('--max-mib', type=float, default=4096, help='Maximum uncompressed ZIP size')
    z.add_argument('--name', default='CELVE Human Evaluation')
    z = sub.add_parser('import-selection', help='Import all 180 frozen split/category-balanced images without resampling')
    z.add_argument('--zip', type=Path, required=True)
    z.add_argument('--max-mib', type=float, default=4096)
    z.set_defaults(limit=0, seed=42, name='CELVE Human Evaluation', require_selection=True)
    r = sub.add_parser('import-regions', help='Assign three regional ZIPs: 60 distinct images per evaluator, 180 total')
    r.add_argument('--config', type=Path, required=True)
    r.add_argument('--seed', type=int, default=42)
    r.add_argument('--max-mib', type=float, default=4096, help='Maximum uncompressed size of each regional ZIP')
    b = sub.add_parser('snapshot', help='Back up responses, study files, imported assets and access codes')
    b.add_argument('--output', type=Path, required=True)
    b = sub.add_parser('restore', help='Restore a snapshot into an empty state directory')
    b.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            if args.limit < 0:
                parser.error('--limit must be >= 0')
            prepare(args)
        elif args.command == 'import-regions':
            from region_loader import load_regions
            activate(load_regions(args), args.state)
        elif args.command in ('import-zip', 'import-selection'):
            from bundle_loader import load_bundle
            activate(load_bundle(args), args.state)
        elif args.command == 'snapshot':
            snapshot(args.state, args.output)
        elif args.command == 'restore':
            restore(args.state, args.archive)
        elif args.command == 'init':
            initialize(args.state)
            print('Access codes:', args.state.resolve() / 'access_codes.txt')
        elif args.command == 'backup':
            if args.output.exists():
                raise FileExistsError('Backup target already exists; choose a new filename.')
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with closing(connect(args.state)) as source, closing(sqlite3.connect(args.output)) as dest:
                source.backup(dest)
            os.chmod(args.output, 0o600)
            print('Saved database backup:', args.output.resolve())
        return 0
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile, tarfile.TarError) as exc:
        print('ERROR:', exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
