#!/usr/bin/env python3
"""Export exactly the frozen selection, preserving each selected run's evidence."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from selection_manifest import file_hash, validate_manifest
from tools.export_human_eval_sample import COUNTRIES, pack, read_json


def zip_entry(name):
    # Re-exporting a frozen selection must not depend on wall-clock timestamps.
    entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    entry.compress_type = zipfile.ZIP_DEFLATED
    entry.external_attr = 0o100600 << 16
    return entry


def export_selection(selection_path, output, max_mib=4096):
    if not math.isfinite(max_mib) or max_mib <= 0:
        raise ValueError('max-mib must be finite and positive.')
    manifest = validate_manifest(read_json(Path(selection_path)))
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise ValueError('Output already exists; choose a new ZIP filename.')
    tasks, descriptions, references, assets, asset_hashes, provenance = {}, {}, {}, {}, {}, []
    for run in manifest['runs']:
        for name, value in run['inputs'].items():
            if file_hash(value['path']) != value['sha256']:
                raise ValueError('Frozen input file changed: ' + name + ': ' + value['path'])
        items = [i for i in manifest['items'] if i['run_id'] == run['run_id']]
        args = SimpleNamespace(**{k: Path(v) for k, v in run['paths'].items()},
            **{k: Path(v['path']) for k, v in run['inputs'].items() if k != 'integrity_file'},
            model_key=manifest['model_key'], split=run['split'], limit=0, seed=manifest['seed'],
            countries=None, per_country=None, output=None, max_mib=max_mib,
            selected_source_keys=[i['source_key'] for i in items], selection_items={i['source_key']: i for i in items},
            return_payload=True)
        content, run_assets = pack(args)
        research = content['researcher/analysis.json']
        provenance.append(dict(research['provenance'], run_id=run['run_id']))
        for item in content['study.json']['items']:
            if item['image_id'] in tasks:
                raise ValueError('Duplicate exported image identity.')
            tasks[item['image_id']] = item
            frozen = next(i for i in items if i['image_id'] == item['image_id'])
            if file_hash(run_assets[item['original_image']]) != frozen['assets']['original_sha256']:
                raise ValueError('Selected original image changed: ' + frozen['source_key'])
            asset_hashes[item['original_image']] = frozen['assets']['original_sha256']
            if {p['patch_id'] for p in item['patches']} != set(frozen['assets']['patches']):
                raise ValueError('Full patch inventory changed: ' + frozen['source_key'])
            for patch in item['patches']:
                saved = frozen['assets']['patches'][patch['patch_id']]
                asset_hashes[patch['image']] = saved['sha256']
                if file_hash(run_assets[patch['image']]) != saved['sha256'] or patch.get('box_xyxy') != saved.get('box_xyxy'):
                    raise ValueError('Selected patch changed: ' + frozen['source_key'])
        descriptions.update(content['researcher/descriptions.json'])
        references.update({r['image_id']: r for r in research['items']})
        for name, path in run_assets.items():
            if name in assets:
                raise ValueError('Duplicate exported asset path.')
            assets[name] = path
        for value in run['inputs'].values():
            if file_hash(value['path']) != value['sha256']:
                raise ValueError('Input changed during ZIP preparation: ' + value['path'])
    order = [i['image_id'] for i in manifest['items']]
    if set(tasks) != set(order) or set(descriptions) != set(order) or set(references) != set(order):
        raise ValueError('Image/description/evidence set differs from frozen selection.')
    contents = {
        'study.json': {'schema_version': 'celve_human_eval_handoff_v1',
                      'countries': [{'id': c, 'name': n} for c, n in COUNTRIES], 'items': [tasks[i] for i in order]},
        'researcher/descriptions.json': descriptions,
        'researcher/analysis.json': {'provenance': {'model_key': manifest['model_key'],
            'selection_id': manifest['selection_id'], 'sample_count': 180,
            'sampling_policy': 'Frozen 180 unique sources, 20 per country, 4 per split and category per country.',
            'runs': provenance}, 'items': [references[i] for i in order]},
        'researcher/selection_manifest.json': manifest,
    }
    serialized = {name: json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False).encode() for name, value in contents.items()}
    total = sum(p.stat().st_size for p in assets.values()) + sum(len(value) for value in serialized.values())
    if total > max_mib * 1024**2:
        raise ValueError(f'Payload {total / 1024**2:.1f} MiB exceeds max-mib; no patches were discarded.')
    output.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as archive:
            created = True
            for name, value in serialized.items():
                archive.writestr(zip_entry(name), value)
            for name, path in assets.items():
                h = hashlib.sha256()
                with path.open('rb') as source, archive.open(zip_entry(name), 'w', force_zip64=True) as target:
                    for block in iter(lambda: source.read(1024 * 1024), b''):
                        h.update(block); target.write(block)
                if h.hexdigest() != asset_hashes[name]:
                    raise ValueError('Image/patch changed while writing ZIP: ' + str(path))
            archive.writestr(zip_entry('README.txt'), 'Frozen CELVE human-evaluation selection.\n'
                '180 distinct source_key; 20 per country, 4 per split/category per country.\n'
                'Original images, ALL patches and exact selected-run descriptions are included.\n'
                'Import with manage.py import-selection --zip this.zip; never resample.\n'
                'researcher/ files and TF results must not be publicly served.\n')
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise
    print('Created:', output)
    print(f'Images: 180; ALL patches: {sum(len(i["patches"]) for i in tasks.values())}; ZIP: {output.stat().st_size / 1024**2:.2f} MiB')
    print('Selection preserved:', manifest['selection_id'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('human_eval_180.zip'))
    parser.add_argument('--max-mib', type=float, default=4096)
    args = parser.parse_args()
    try:
        export_selection(args.selection, args.output, args.max_mib)
        return 0
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        print('ERROR:', exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
