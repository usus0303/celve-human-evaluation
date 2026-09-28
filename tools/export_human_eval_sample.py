#!/usr/bin/env python3
"""Package existing CELVE data for human-evaluation website integration (no GPU).

Run this file on the machine containing /home/young's experiment directories:
    python3 export_human_eval_sample.py --split split_01 --limit 2

Defaults follow generate_description_llama_final_backup(3).py. Only Python's
standard library is required. No generation script is imported or executed.
All batch_meta box patches for each chosen source are included, not merely
the retained supporting patches. Existing experiment files are never modified.

This is a data handoff sample, NOT a representative final evaluation sample.
Only successful, nonempty descriptions in one explicitly resolved run are
eligible. The generating script omitted images with empty refined sets.
Use --description-file if multiple generation runs exist; this script refuses
to silently choose a run or combine runs. --limit 0 includes all eligible rows.
"""

import argparse
import hashlib
import json
import math
import random
import sys
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path


PROJECT = Path('/home/young/Vlm-interpretability')
BACKUP = Path('/home/young/CONAN_FINAL_BACKUP_20260907')
COUNTRIES = [
    ('CN', 'China'), ('KR', 'South Korea'), ('JP', 'Japan'),
    ('BD', 'Bangladesh'), ('ID', 'Indonesia'), ('PK', 'Pakistan'),
    ('IN', 'India'), ('TH', 'Thailand'), ('SG', 'Singapore'),
]


def read_json(path):
    with path.open(encoding='utf-8') as stream:
        return json.load(stream)


def rows_at(path):
    rows = read_json(path)
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError(f'Expected an array of objects: {path}')
    return rows


def source_key(value):
    return unicodedata.normalize('NFC', str(value or '').replace('\\', '/').strip()).casefold().lstrip('/')


def basename(value):
    return str(value or '').replace('\\', '/').rsplit('/', 1)[-1].lower()


def country_key(value):
    value = str(value).strip().lower()
    aliases = {name.lower(): code for code, name in COUNTRIES}
    aliases.update({code.lower(): code for code, _ in COUNTRIES})
    aliases.update({'korea': 'KR', 'republic of korea': 'KR', 'korea, republic of': 'KR'})
    return aliases.get(value, value)


def country_list(value, context):
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ValueError(f'Invalid country list: {context}')
    keys = [country_key(v) for v in value]
    if len(keys) != len(set(keys)):
        raise ValueError(f'Duplicate normalized country: {context}')
    return keys


def stable_id(prefix, value):
    return prefix + hashlib.sha256(value.encode('utf-8')).hexdigest()[:20]


def select_sources(eligible, verification, countries, per_country, limit, seed):
    """Stratify by source-country ground truth, never by model predictions."""
    countries = sorted({country_key(c) for c in countries}) if countries else None
    if countries and not set(countries).issubset({c for c, _ in COUNTRIES}):
        raise ValueError('Unknown country code in --countries')
    if per_country is not None and (not countries or per_country < 1):
        raise ValueError('--per-country requires --countries and a positive count')
    source_countries = {key: country_key(verification[key].get('gt') or key.split('/')[0]) for key in eligible}
    eligible = [key for key in eligible if not countries or source_countries[key] in countries]
    if not eligible:
        raise ValueError('No successful descriptions for the requested source countries')
    rng = random.Random(seed)
    if per_country is not None:
        chosen = []
        for country in countries:
            candidates = [key for key in eligible if source_countries[key] == country]
            if len(candidates) < per_country:
                raise ValueError(f'{country}: need {per_country} eligible images, found {len(candidates)}. No reduced sample was exported.')
            chosen.extend(rng.sample(candidates, per_country))
        chosen.sort()
    else:
        chosen = sorted(rng.sample(eligible, min(limit, len(eligible)))) if limit else eligible
    return chosen, {'source_countries': countries, 'per_country': per_country,
                    'eligible_after_country_filter': len(eligible),
                    'selected_source_country_counts': {c: sum(source_countries[k] == c for k in chosen)
                                                       for c in sorted({source_countries[k] for k in chosen})}}


def case_insensitive_file(root, relative):
    parts = str(relative).replace('\\', '/').split('/')
    if any(p in ('.', '..') for p in parts):
        return None
    current = root
    for part in filter(None, parts):
        direct = current / part
        if direct.exists():
            current = direct
        elif current.is_dir():
            matches = [p for p in current.iterdir() if p.name.casefold() == part.casefold()]
            if len(matches) != 1:
                return None
            current = matches[0]
        else:
            return None
    return current.resolve() if current.is_file() else None


def first_existing(values, roots):
    for value in values:
        if not value:
            continue
        path = Path(str(value)).expanduser()
        candidates = [path] if path.is_absolute() else [root / path for root in roots]
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
    return None


def require_unique(rows, path):
    indexed = {}
    for row in rows:
        key = source_key(row.get('source_key'))
        if not key or key in indexed:
            raise ValueError(f'Missing/duplicate source_key {key!r}: {path}')
        indexed[key] = row
    return indexed


def resolve_run(args):
    project, backup = args.project_root.expanduser(), args.backup_root.expanduser()
    patch_root = args.patch_root or project / 'output/new/grounded_sam_patches/eval' / args.model_key
    batch_file = args.batch_meta or patch_root / 'batch_meta_eval.json'
    resize_root = args.image_resize_root or project / 'image_resize'
    tf_file = args.tf_file or backup / '06_patch_tf_scores' / args.model_key / args.split / 'tf_scored_APS.json'
    selection_file = args.selection_file
    if selection_file is None:
        selection_file = next((p for p in [
            backup / '07_verification/best_quantile_selection_cov90_full_eval.json',
            backup / '07_verification/best_quantile_selection_cov90.json',
        ] if p.is_file()), None)
    if selection_file is None:
        raise FileNotFoundError(f'No selection JSON under {backup / "07_verification"}')
    selection = read_json(selection_file)
    try:
        selected = selection['models'][args.model_key]['splits'][args.split]
        quantile, threshold = float(selected['quantile']), float(selected['threshold'])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f'Selection missing/invalid: {args.model_key}/{args.split}; use the exact --model-key') from exc
    if not math.isfinite(quantile) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError('Invalid selected quantile/threshold')
    verification_file = args.verification_file or (
        backup / '07_verification' / args.model_key / args.split /
        f'quantile_{quantile:.2f}' / 'image_level_results.json'
    )
    description_file = args.description_file
    if description_file is None:
        description_dir = backup / '08_description' / args.model_key / 'patch_caption_en_v2' / args.split
        candidates = sorted(description_dir.glob(f'patch_descriptions_Q{quantile:.2f}_test*_en_*.json'))
        if len(candidates) != 1:
            paths = '\n'.join(str(p) for p in candidates) or '(none)'
            raise ValueError(
                f'Expected exactly one description run under {description_dir}; found {len(candidates)}.\n'
                f'Choose the intended result with --description-file /path/to/file.json\n{paths}'
            )
        description_file = candidates[0]

    return {'project_root': project.resolve(), 'backup_root': backup.resolve(),
            'patch_root': patch_root.resolve(), 'batch_meta': batch_file.resolve(),
            'image_resize_root': resize_root.resolve(), 'tf_file': tf_file.resolve(),
            'selection_file': selection_file.resolve(), 'verification_file': verification_file.resolve(),
            'description_file': description_file.resolve(), 'selected_point': selected,
            'quantile': quantile, 'threshold': threshold}


def validate_description(row, verify, key, model_key, split, quantile, threshold):
    for field in ('verification_model', 'description_model'):
        if row.get(field) != model_key:
            raise ValueError(f'Description {field} does not match --model-key: {key}')
    if row.get('split') != split:
        raise ValueError(f'Description split mismatch: {key}')
    for field, expected in [('quantile', quantile), ('threshold', threshold)]:
        if not math.isclose(float(row.get(field, math.nan)), expected, abs_tol=1e-8, rel_tol=0):
            raise ValueError(f'Description {field} differs from selection: {key}')
        if field in verify and not math.isclose(float(verify[field]), expected, abs_tol=1e-8, rel_tol=0):
            raise ValueError(f'Verification {field} differs from selection: {key}')
    if verify.get('split', split) != split:
        raise ValueError(f'Verification split mismatch: {key}')
    for desc_field, verify_field in [('aps_pred_set', 'original_pred_set'), ('refined_pred_set', 'refined_pred_set')]:
        if set(country_list(row.get(desc_field), key)) != set(country_list(verify.get(verify_field), key)):
            raise ValueError(f'Description/verification country sets differ: {key}/{desc_field}')


def pack(args):
    context = resolve_run(args)
    project, backup = context['project_root'], context['backup_root']
    patch_root, batch_file = context['patch_root'], context['batch_meta']
    resize_root, tf_file = context['image_resize_root'], context['tf_file']
    selection_file, verification_file = context['selection_file'], context['verification_file']
    description_file = context['description_file']
    selected, quantile, threshold = context['selected_point'], context['quantile'], context['threshold']
    verification = require_unique(rows_at(verification_file), verification_file)
    description_rows = rows_at(description_file)
    descriptions = require_unique(description_rows, description_file)
    batches, tf_rows = defaultdict(list), defaultdict(list)
    seen_patches = set()
    for row in rows_at(batch_file):
        key, name = source_key(row.get('source_key')), basename(row.get('box_patch'))
        if not key or not name or (key, name) in seen_patches:
            raise ValueError(f'Missing/duplicate batch patch identity: {key!r}/{name!r}')
        seen_patches.add((key, name))
        batches[key].append(row)
    for row in rows_at(tf_file):
        key = source_key(row.get('source_key'))
        if key:
            tf_rows[key].append(row)

    eligible = []
    for key, row in descriptions.items():
        if row.get('status') != 'ok' or not isinstance(row.get('description'), str) or not row['description'].strip():
            continue
        if key not in verification:
            raise ValueError(f'Description has no verification row: {key}')
        validate_description(row, verification[key], key, args.model_key, args.split, quantile, threshold)
        eligible.append(key)
    eligible.sort()
    if not eligible:
        raise ValueError('No successful, nonempty descriptions in the selected run')
    fixed = getattr(args, 'selected_source_keys', None)
    if fixed is not None:
        if len(fixed) != len(set(fixed)) or any(key not in eligible for key in fixed):
            raise ValueError('Frozen selection includes an ineligible or repeated source. No resampling is allowed.')
        chosen, sampling = list(fixed), {'frozen_selection': True}
    else:
        chosen, sampling = select_sources(eligible, verification, getattr(args, 'countries', None),
                                          getattr(args, 'per_country', None), args.limit, args.seed)

    assets, tasks, texts, research = {}, [], {}, []
    for key in chosen:
        desc, verify = descriptions[key], verification[key]
        frozen = getattr(args, 'selection_items', {}).get(key)
        if frozen:
            from selection_manifest import digest, text_hash
            if (digest(desc) != frozen['description_row_sha256'] or digest(verify) != frozen['verification_row_sha256']
                    or text_hash(desc['description']) != frozen['description_sha256']):
                raise ValueError('Selected description or verification row changed: ' + key)
        task_id = stable_id('img_', f'{args.model_key}\0{args.split}\0{key}')
        if frozen and frozen['image_id'] != task_id:
            raise ValueError('Frozen image identity mismatch: ' + key)
        if not batches[key]:
            raise ValueError(f'No full batch patch list: {key}')
        if not tf_rows[key]:
            raise ValueError(f'No TF input rows: {key}')
        values = [v for r in tf_rows[key] for v in [r.get('source_image'), r.get('cp_image')]]
        values += [r.get('source_image') for r in batches[key]]
        values += [desc.get('original_image')]
        original = first_existing(values, [project, resize_root, Path.cwd()]) or case_insensitive_file(resize_root, key)
        if original is None:
            raise FileNotFoundError(f'Original image unresolved: {key}')
        original_asset = f'assets/{task_id}/original{original.suffix.lower()}'
        assets[original_asset] = original
        patches, patch_map, patch_research = [], {}, []
        for row in sorted(batches[key], key=lambda r: basename(r['box_patch'])):
            name = basename(row['box_patch'])
            path = first_existing([row['box_patch']], [patch_root, project, Path.cwd()])
            if path is None:
                raise FileNotFoundError(f'Full patch missing (cannot silently omit): {key}/{row["box_patch"]}')
            pid = stable_id('p_', key + '\0' + name)
            asset = f'assets/{task_id}/{pid}{path.suffix.lower()}'
            assets[asset] = path
            patch_map[name] = pid
            patches.append({'patch_id': pid, 'image': asset, 'box_xyxy': row.get('box_xyxy')})
            patch_research.append({'patch_id': pid, 'original_metadata': row, 'resolved_path': str(path)})

        cp = country_list(verify.get('original_pred_set'), key)
        refined = country_list(verify.get('refined_pred_set'), key)
        if not set(refined).issubset(cp):
            raise ValueError(f'Refined set is not a subset of CP: {key}')
        raw_supports = verify.get('supporting_patches') or {}
        if not isinstance(raw_supports, dict):
            raise ValueError(f'Expected country-keyed supporting_patches: {key}')
        supports = {}
        for label, values in raw_supports.items():
            country = country_key(label)
            if country in supports or not isinstance(values, list):
                raise ValueError(f'Duplicate/invalid support country: {key}/{label}')
            entries = []
            for support in values:
                name = basename(support.get('box_patch'))
                if name not in patch_map:
                    raise ValueError(f'Retained patch absent from full batch metadata: {key}/{name}')
                score = float(support['c'])
                if not math.isfinite(score) or not 0 <= score <= 1 or score + 1e-8 < threshold:
                    raise ValueError(f'Invalid retained TF score: {key}/{label}/{name}')
                entries.append({'patch_id': patch_map[name], 'score': score})
            if entries and country not in refined:
                raise ValueError(f'Supports outside refined set: {key}/{label}')
            if len({e['patch_id'] for e in entries}) != len(entries):
                raise ValueError(f'Duplicate support patch: {key}/{label}')
            supports[country] = entries
        if any(not supports.get(country) for country in refined):
            raise ValueError(f'Refined country missing supports: {key}')

        tasks.append({'image_id': task_id, 'original_image': original_asset, 'patches': patches})
        texts[task_id] = {'description': desc['description'], 'language': 'en'}
        research.append({
            'image_id': task_id, 'source_key': key, 'original_image_path': str(original),
            'cp_countries': cp, 'refined_countries': refined, 'threshold': threshold,
            'country_comparison': {
                code: {'tf_evaluated': code in cp,
                       'retained_patch_ids': [r['patch_id'] for r in supports.get(code, [])] if code in cp else None}
                for code, _ in COUNTRIES
            },
            'retained_patches_by_country': supports, 'all_patch_metadata': patch_research,
            'verification_row': verify, 'tf_rows': tf_rows[key],
            'description_provenance': {k: desc[k] for k in [
                'schema_version', 'prompt_version', 'verification_model', 'description_model',
                'run_config', 'input_fingerprint', 'status', 'omitted_patch_ids',
                'all_support_count', 'used_support_count', 'supporting_patches',
            ] if k in desc},
        })

        if frozen:
            research[-1]['selection'] = frozen
            research[-1].update(source_country=frozen['source_country'], split=frozen['split'],
                                category=frozen['category'], run_id=frozen['run_id'])

    provenance = {
        'model_key': args.model_key, 'split': args.split, 'quantile': quantile,
        'threshold': threshold, 'seed': args.seed,
        'description_file': str(description_file), 'verification_file': str(verification_file),
        'tf_file': str(tf_file), 'batch_meta_file': str(batch_file),
        'selection_file': str(selection_file), 'selected_point': selected,
        'verification_image_count': len(verification),
        'empty_refined_count': sum(not r.get('refined_pred_set') for r in verification.values()),
        'description_row_count': len(descriptions), 'eligible_count': len(eligible),
        'sample_count': len(chosen),
        'sampling_policy': 'Seeded selection of successful nonempty descriptions from one run, stratified by source country when requested.',
        **sampling,
    }
    contents = {
        'study.json': {'schema_version': 'celve_human_eval_handoff_v1',
                       'countries': [{'id': code, 'name': name} for code, name in COUNTRIES], 'items': tasks},
        'researcher/descriptions.json': texts,
        'researcher/analysis.json': {'provenance': provenance, 'items': research},
    }
    if getattr(args, 'return_payload', False):
        return contents, assets
    serialized = {name: json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8') for name, value in contents.items()}
    total = sum(p.stat().st_size for p in assets.values()) + sum(len(v) for v in serialized.values())
    if total > args.max_mib * 1024 * 1024:
        raise ValueError(f'Input payload is {total / 1024**2:.1f} MiB; cap is {args.max_mib:g}. '
                         'Reduce --limit or explicitly raise --max-mib. No patches were discarded.')
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as archive:
            created = True
            for name, data in serialized.items():
                archive.writestr(name, data)
            for name, path in assets.items():
                archive.write(path, name)
            archive.writestr('README.txt', (
                'CELVE human-evaluation integration sample\n\n'
                'study.json includes original images and ALL batch box patches.\n'
                'Asset names are opaque; original filenames/GT/scores are researcher-only.\n'
                'researcher/descriptions.json: exact English descriptions, no translation.\n'
                'researcher/analysis.json: source identities, CP/TF outcomes, provenance.\n'
                'Do not expose researcher/ files through a public static directory.\n'
                'Serve descriptions only after country/evidence answers are committed.\n'
                'Same patch can be selected for multiple countries.\n'
                'Non-CP country: tf_evaluated=false, retained_patch_ids=null (NOT []).\n'
                'CP country with no surviving patch: tf_evaluated=true, retained_patch_ids=[].\n'
                'Raw TF rows are preserved; their score schema is not inferred.\n'
                'Box coordinates are preserved verbatim; check their coordinate frame before overlaying.\n'
                'This is an integration sample, not a final balanced evaluation sample.\n'
                'Eligibility requires a successful nonempty description in one generation run.\n'
                'Empty refined sets/failed generations need a separate final-study policy.\n'
            ))
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise
    print(f'Created: {output}')
    print(f'Model: {args.model_key}; split: {args.split}; threshold: {threshold:g}')
    print(f'Images: {len(tasks)}; ALL patches: {sum(len(t["patches"]) for t in tasks)}; '
          f'ZIP: {output.stat().st_size / 1024**2:.2f} MiB')
    print(f'Eligible successful descriptions: {len(eligible)}; '
          f'empty refined sets in verification: {provenance["empty_refined_count"]}')
    print('Attach this ZIP in the conversation. No model loading or experiment modification occurred.')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--project-root', type=Path, default=PROJECT)
    parser.add_argument('--backup-root', type=Path, default=BACKUP)
    parser.add_argument('--model-key', default='llama3_2_11b')
    parser.add_argument('--split', default='split_01', choices=[f'split_{i:02d}' for i in range(1, 6)])
    parser.add_argument('--limit', type=int, default=2, help='Sample size; 0 = all eligible descriptions')
    parser.add_argument('--countries', nargs='+', help='Source-country codes, e.g. CN KR JP; does not change the nine answer choices')
    parser.add_argument('--per-country', type=int, help='Exact sample count for each requested country; takes precedence over --limit')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--max-mib', type=float, default=24, help='Maximum uncompressed input payload')
    parser.add_argument('--output', type=Path, default=Path('human_eval_sample.zip'))
    for name in ['description-file', 'selection-file', 'verification-file', 'tf-file', 'patch-root', 'batch-meta', 'image-resize-root']:
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args()
    if args.limit < 0 or not math.isfinite(args.max_mib) or args.max_mib <= 0:
        parser.error('--limit must be >= 0 and --max-mib must be finite and > 0')
    try:
        pack(args)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
