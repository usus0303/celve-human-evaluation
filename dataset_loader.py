#!/usr/bin/env python3
"""Read and validate one existing CELVE run; register files without copying pixels."""

import argparse
import hashlib
import json
import math
import random
import sys
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
    return str(value or '').replace('\\', '/').strip().lower().lstrip('/')


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


def collect_split(args):
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

    verification = require_unique(rows_at(verification_file), verification_file)
    description_bytes = description_file.read_bytes()
    description_rows = json.loads(description_bytes)
    if not isinstance(description_rows, list) or any(not isinstance(r, dict) for r in description_rows):
        raise ValueError(f'Expected an array of description objects: {description_file}')
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
        for field in ('verification_model', 'description_model'):
            if row.get(field) != args.model_key:
                raise ValueError(f'Description {field} does not match --model-key: {key}')
        if row.get('split') != args.split:
            raise ValueError(f'Description split mismatch: {key}')
        for field, expected in [('quantile', quantile), ('threshold', threshold)]:
            if not math.isclose(float(row.get(field, math.nan)), expected, abs_tol=1e-8, rel_tol=0):
                raise ValueError(f'Description {field} differs from selection: {key}')
        for desc_field, verify_field in [('aps_pred_set', 'original_pred_set'), ('refined_pred_set', 'refined_pred_set')]:
            if set(country_list(row.get(desc_field), key)) != set(country_list(verification[key].get(verify_field), key)):
                raise ValueError(f'Description/verification country sets differ: {key}/{desc_field}')
        eligible.append(key)
    eligible.sort()
    if not eligible:
        raise ValueError('No successful, nonempty descriptions in the selected run')
    chosen = sorted(random.Random(args.seed).sample(eligible, min(args.limit, len(eligible)))) if args.limit else eligible

    assets, tasks, texts, research = {}, [], {}, []
    for key in chosen:
        desc, verify = descriptions[key], verification[key]
        task_id = stable_id('img_', f'{args.model_key}\0{args.split}\0{key}')
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
        'sampling_policy': 'All successful nonempty descriptions in the selected run.' if not args.limit else 'Seeded subset of successful nonempty descriptions in the selected run.',
    }
    contents = {
        'study.json': {'schema_version': 'celve_human_eval_handoff_v1',
                       'countries': [{'id': code, 'name': name} for code, name in COUNTRIES], 'items': tasks},
        'researcher/descriptions.json': texts,
        'researcher/analysis.json': {'provenance': provenance, 'items': research},
    }
    from PIL import Image
    import mimetypes
    registered = {}
    def register(asset):
        path = assets[asset]
        stat = path.stat()
        aid = stable_id('a_', asset)
        registered[aid] = {'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
                           'mime': mimetypes.guess_type(path.name)[0] or 'application/octet-stream'}
        return 'media/' + aid
    for item in tasks:
        with Image.open(assets[item['original_image']]) as image:
            item['width'], item['height'] = image.size
        item['original_image'] = register(item['original_image'])
        for patch in item['patches']:
            patch['image'] = register(patch['image'])
    slim = []
    for row in research:
        slim.append({k: row[k] for k in ['image_id', 'source_key', 'cp_countries', 'refined_countries',
                     'threshold', 'country_comparison', 'retained_patches_by_country']})
    provenance['verification_without_description_row'] = len(set(verification) - set(descriptions))
    provenance['verification_without_eligible_description'] = len(set(verification) - set(eligible))
    provenance['description_status_counts'] = dict(__import__('collections').Counter(str(r.get('status','missing')) for r in description_rows))
    provenance['description_file_sha256'] = hashlib.sha256(description_bytes).hexdigest()
    return {'study': contents['study.json'], 'descriptions': texts, 'analysis': slim,
            'assets': registered, 'provenance': provenance}
