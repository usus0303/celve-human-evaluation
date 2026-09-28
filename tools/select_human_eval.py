#!/usr/bin/env python3
"""Freeze 180 unique COMPLETE images balanced jointly by country/split/category."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from selection_manifest import (SPLITS, DEFAULT_REGIONS, INTEGRITY_POLICY, InfeasibleSelection,
    canonical_key, check_integrity, digest, file_hash, seal_manifest, select_balanced,
    source_labels, text_hash, validate_regions)
from tools.export_human_eval_sample import (PROJECT, BACKUP, read_json, require_unique,
    resolve_run, rows_at, stable_id, validate_description, pack)

INPUT_FIELDS = ('description_file', 'verification_file', 'tf_file', 'batch_meta', 'selection_file')
PATH_FIELDS = ('project_root', 'backup_root', 'patch_root', 'image_resize_root')


def path_value(value, base):
    path = Path(value).expanduser()
    return (base / path if not path.is_absolute() else path).resolve()


def choose(config_path):
    config_path = Path(config_path).resolve()
    config = read_json(config_path)
    if not isinstance(config, dict) or not isinstance(config.get('model_key'), str) or not config['model_key'].strip():
        raise ValueError('Config needs one explicit model_key.')
    raw_runs = config.get('runs', [])
    if (not isinstance(raw_runs, list) or len(raw_runs) != 5
            or sorted(r.get('split', '') for r in raw_runs) != list(SPLITS)):
        raise ValueError('Config must specify exactly one description run for each of split_01..split_05.')
    regions = config.get('regions', DEFAULT_REGIONS)
    validate_regions(regions)
    seed, max_cell = config.get('seed', 42), config.get('max_per_cell', 4)
    candidates, run_records, audits = [], [], []
    for raw in sorted(raw_runs, key=lambda r: r['split']):
        if not raw.get('description_file'):
            raise ValueError('Specify an exact description_file for ' + raw['split'] + '; automatic run discovery is disabled.')
        values = {'model_key': config['model_key'], 'split': raw['split']}
        for name in (*PATH_FIELDS, *INPUT_FIELDS):
            value = raw.get(name, config.get(name))
            values[name] = path_value(value, config_path.parent) if value else None
        values['project_root'] = values['project_root'] or PROJECT
        values['backup_root'] = values['backup_root'] or BACKUP
        resolved = resolve_run(SimpleNamespace(**values))
        inputs = {k: {'path': str(resolved[k]), 'sha256': file_hash(resolved[k])} for k in INPUT_FIELDS}
        descriptions = require_unique(rows_at(resolved['description_file']), resolved['description_file'])
        verification = require_unique(rows_at(resolved['verification_file']), resolved['verification_file'])
        external, external_policy = {}, None
        if raw.get('integrity_file'):
            audit_path = path_value(raw['integrity_file'], config_path.parent)
            inputs['integrity_file'] = {'path': str(audit_path), 'sha256': file_hash(audit_path)}
            audit = read_json(audit_path)
            if (audit.get('schema_version') != 'celve_integrity_v1'
                    or audit.get('description_file_sha256') != inputs['description_file']['sha256']
                    or not isinstance(audit.get('policy_id'), str) or not audit['policy_id'].strip()):
                raise ValueError('Invalid or stale integrity report: ' + str(audit_path))
            external = require_unique(audit['items'], audit_path)
            external_policy = audit['policy_id']
        run = {'split': raw['split'], 'model_key': config['model_key'], 'inputs': inputs,
               'paths': {k: str(resolved[k]) for k in PATH_FIELDS},
               'quantile': resolved['quantile'], 'threshold': resolved['threshold'],
               'integrity_policy': external_policy or INTEGRITY_POLICY}
        run['run_id'] = raw['split'] + '_' + digest(run)[:20]
        run_records.append(run)
        rejected, accepted, rejection_details = Counter(), 0, []
        for key, row in sorted(descriptions.items()):
            key = canonical_key(key)
            external_row = external.get(key)
            if external_row and external_row.get('description_sha256') != text_hash(row.get('description') or ''):
                raise ValueError('Integrity report description hash mismatch: ' + key)
            integrity = check_integrity(row, external_row, external_policy)
            if integrity['classification'] != 'COMPLETE':
                rejected.update(integrity['reasons'])
                rejection_details.append({'source_key': key, 'reasons': integrity['reasons']})
                continue
            if key not in verification:
                raise ValueError('Description has no matching verification row: ' + key)
            verify = verification[key]
            validate_description(row, verify, key, config['model_key'], raw['split'], run['quantile'], run['threshold'])
            country, category = source_labels(key, row, verify)
            candidates.append({'image_id': stable_id('img_', f'{config["model_key"]}\0{raw["split"]}\0{key}'),
                'source_key': key, 'source_country': country, 'category': category, 'split': raw['split'],
                'run_id': run['run_id'], 'description_sha256': text_hash(row['description']),
                'description_row_sha256': digest(row), 'verification_row_sha256': digest(verify), 'integrity': integrity})
            accepted += 1
        audits.append({'split': raw['split'], 'run_id': run['run_id'], 'description_rows': len(descriptions),
                       'complete_candidates': accepted, 'excluded_rows': len(rejection_details),
                       'exclusion_reason_counts': dict(rejected), 'excluded': rejection_details})
        # Detect input files being rewritten while the selector was reading them.
        for item in inputs.values():
            if file_hash(item['path']) != item['sha256']:
                raise ValueError('Input changed during selection: ' + item['path'])
    try:
        selected, counts = select_balanced(candidates, seed, max_cell)
    except InfeasibleSelection as exc:
        exc.report = {'status': 'infeasible', 'runs': audits, 'countries': exc.report}
        raise
    # Validate the selected image/ALL-patch/TF joins now and freeze their bytes.
    # A missing source file aborts selection instead of silently choosing another image.
    for run in run_records:
        rows = [i for i in selected if i['run_id'] == run['run_id']]
        args = SimpleNamespace(**{k: Path(v) for k, v in run['paths'].items()},
            **{k: Path(v['path']) for k, v in run['inputs'].items() if k != 'integrity_file'},
            model_key=config['model_key'], split=run['split'], limit=0, seed=seed, max_mib=4096,
            selected_source_keys=[r['source_key'] for r in rows], selection_items={r['source_key']: r for r in rows},
            return_payload=True)
        payload, asset_paths = pack(args)
        tasks = {r['image_id']: r for r in payload['study.json']['items']}
        for row in rows:
            task = tasks[row['image_id']]
            row['assets'] = {'original_sha256': file_hash(asset_paths[task['original_image']]),
                'patches': {p['patch_id']: {'sha256': file_hash(asset_paths[p['image']]), 'box_xyxy': p.get('box_xyxy')}
                            for p in task['patches']}}
        for item in run['inputs'].values():
            if file_hash(item['path']) != item['sha256']:
                raise ValueError('Input changed during asset validation: ' + item['path'])
    manifest = seal_manifest({'schema_version': 'celve_selection_v1',
        'name': config.get('name', 'CELVE Human Evaluation'), 'model_key': config['model_key'],
        'seed': seed, 'quotas': {'per_country': 20, 'per_split': 4, 'per_category': 4, 'max_per_cell': max_cell},
        'algorithm': 'integer_max_flow_seeded_edge_order_v1', 'regions': regions, 'runs': run_records, 'items': selected})
    return manifest, {'status': 'selected', 'selection_id': manifest['selection_id'], 'runs': audits, 'countries': counts}


def write_new(path, value):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as out:
        json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
        out.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('selection_manifest.json'))
    parser.add_argument('--report', type=Path, default=Path('selection_report.json'))
    args = parser.parse_args()
    try:
        if args.output.resolve() == args.report.resolve() or args.output.exists() or args.report.exists():
            raise ValueError('Choose two different, unused output/report paths; existing files are never overwritten.')
        manifest, report = choose(args.config)
        write_new(args.report, report)
        write_new(args.output, manifest)
        print('Frozen selection:', args.output.resolve())
        print(manifest['selection_id'])
        print('180 unique sources; each country: 20 images, 4 per split, 4 per category.')
        print('No images copied, no models loaded, no existing experiment files changed.')
        return 0
    except InfeasibleSelection as exc:
        write_new(args.report, exc.report)
        print('ERROR:', exc, file=sys.stderr)
        print('Diagnostic report:', args.report.resolve(), file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('ERROR:', exc, file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
