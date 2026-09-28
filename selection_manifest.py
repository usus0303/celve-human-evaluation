"""Frozen, globally unique human-evaluation selections (standard library only)."""
import hashlib
import json
import random
import re
import unicodedata
from collections import Counter, defaultdict, deque
from pathlib import Path

from dataset_loader import COUNTRIES, country_key

SPLITS = tuple(f'split_{i:02d}' for i in range(1, 6))
CATEGORIES = ('architecture', 'clothes', 'cuisine', 'game', 'tool_instrument')
COUNTRY_CODES = tuple(c for c, _ in COUNTRIES)
DEFAULT_REGIONS = [
    {'id': 'east_asia', 'name': '동아시아', 'countries': ['CN', 'KR', 'JP'], 'evaluator': '01'},
    {'id': 'south_asia', 'name': '남아시아', 'countries': ['BD', 'PK', 'IN'], 'evaluator': '02'},
    {'id': 'southeast_asia', 'name': '동남아시아', 'countries': ['ID', 'TH', 'SG'], 'evaluator': '03'},
]
INTEGRITY_POLICY = 'celve_complete_v1'


def canonical_key(value):
    if not isinstance(value, str):
        raise ValueError('source_key must be a string.')
    key = unicodedata.normalize('NFC', value.replace('\\', '/').strip()).casefold().lstrip('/')
    if not key or any(p in ('', '.', '..') for p in key.split('/')):
        raise ValueError('Invalid source_key: ' + repr(value))
    return key


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def text_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def source_labels(key, description, verification):
    parts = canonical_key(key).split('/')
    if len(parts) < 3:
        raise ValueError('Expected country/category/filename source_key: ' + key)
    country = country_key(verification.get('gt') or description.get('gt') or parts[0])
    category = {'tool': 'tool_instrument'}.get(parts[1], parts[1])
    if country not in COUNTRY_CODES or category not in CATEGORIES:
        raise ValueError('Unknown source country/category: ' + key)
    for label in (parts[0], description.get('gt'), verification.get('gt')):
        if label and country_key(label) != country:
            raise ValueError('Conflicting source-country labels: ' + key)
    for row in (description, verification):
        if row.get('category') and {'tool': 'tool_instrument'}.get(row['category'], row['category']) != category:
            raise ValueError('Conflicting category labels: ' + key)
    return country, category


def check_integrity(row, external=None, external_policy=None):
    """Explicit technical-completeness checks; never infer missing finish metadata."""
    reasons = []
    text = row.get('description')
    if row.get('status') != 'ok':
        reasons.append('status_not_ok')
    if not isinstance(text, str) or not text.strip():
        reasons.append('empty_description')
    calls = row.get('generation_calls')
    finishes = []
    if isinstance(calls, list) and calls:
        good_calls = [c for c in calls if isinstance(c, dict) and c.get('status') == 'ok']
        if not good_calls:
            reasons.append('missing_successful_generation_calls')
        finishes.extend(c.get('finish_reason') for c in good_calls)
        chunks = row.get('description_chunks')
        if isinstance(chunks, list):
            covered = {c.get('chunk_index') for c in good_calls}
            if any(not isinstance(c, dict) or c.get('chunk_index') not in covered for c in chunks):
                reasons.append('chunk_without_successful_call')
    if 'finish_reason' in row:
        finishes.append(row['finish_reason'])
    if not finishes:
        reasons.append('missing_finish_reason')
    if 'length' in finishes:
        reasons.append('length')
    if any(f not in ('eos', 'stop', 'eos_token', 'eot', 'end_turn', 'stop_sequence') for f in finishes):
        reasons.append('unknown_finish_reason')
    # Unknown means unknown generation termination or tokenizer placeholders,
    # not the ordinary English word "unknown" inside a valid description.
    if isinstance(text, str) and re.search(r'<(?:unk|\|unk\|)>', text, re.I):
        reasons.append('unknown_token')
    if external_policy is not None:
        if not isinstance(external, dict):
            reasons.append('missing_integrity_row')
        elif (external.get('classification') != 'COMPLETE' or external.get('unknown') is not False
              or external.get('bad_ending') is not False):
            reasons.append('external_not_COMPLETE')
    elif isinstance(text, str) and text.strip():
        ending_texts = [text]
        if isinstance(row.get('description_chunks'), list):
            ending_texts.extend(c.get('description', '') for c in row['description_chunks'] if isinstance(c, dict))
        for ending_text in ending_texts:
            ending = ending_text.rstrip().rstrip('"\'”’)]}').rstrip() if isinstance(ending_text, str) else ''
            if not ending.endswith(('.', '!', '?')) or ending.endswith('...') or re.search(
                    r'\b(?:and|or|but|because|with|such as|including|of|to|the|a|an)[.!?]$', ending, re.I):
                reasons.append('bad_ending')
    return {'classification': 'COMPLETE' if not reasons else 'EXCLUDED',
            'policy': external_policy or INTEGRITY_POLICY, 'reasons': sorted(set(reasons)),
            'finish_reasons': finishes}


class InfeasibleSelection(ValueError):
    def __init__(self, report):
        self.report = report
        failed = ', '.join(c for c, r in report.items() if r['selected'] != 20)
        super().__init__('No feasible 180-image selection; constraints were not relaxed. Countries: ' + failed)


def select_balanced(candidates, seed=42, max_per_cell=4, countries=COUNTRY_CODES):
    """Integer max flow: split -> split/category -> unique source -> category.

    Seeded edge order chooses a reproducible feasible solution, not a uniform
    probability sample. Residual edges can reassign a repeated source to a split.
    """
    if type(seed) is not int or type(max_per_cell) is not int or not 1 <= max_per_cell <= 4:
        raise ValueError('seed must be an integer and max_per_cell must be 1..4.')
    identities, pairs = {}, set()
    for c in candidates:
        key = canonical_key(c['source_key'])
        if key != c['source_key'] or c['split'] not in SPLITS or c['category'] not in CATEGORIES or c['source_country'] not in COUNTRY_CODES:
            raise ValueError('Invalid candidate labels or noncanonical source_key.')
        identity = (c['source_country'], c['category'])
        if key in identities and identities[key] != identity:
            raise ValueError('A repeated source changes country/category: ' + key)
        identities[key] = identity
        pair = (c['split'], key)
        if pair in pairs:
            raise ValueError('Multiple candidates for the same split/source: ' + key)
        pairs.add(pair)
    chosen, reports = [], {}
    for country in sorted(countries):
        rows = sorted((r for r in candidates if r['source_country'] == country), key=lambda r: (r['split'], r['source_key']))
        rng = random.Random(f'{seed}:{country}')
        graph = defaultdict(list)

        def edge(a, b, capacity):
            forward = [b, capacity, len(graph[b])]
            reverse = [a, 0, len(graph[a])]
            graph[a].append(forward); graph[b].append(reverse)
            return forward

        source, sink = ('start',), ('end',)
        splits, categories = list(SPLITS), list(CATEGORIES)
        rng.shuffle(splits); rng.shuffle(categories)
        for split in splits:
            edge(source, ('split', split), 4)
            for category in categories:
                edge(('split', split), ('cell', split, category), max_per_cell)
        for key in sorted({r['source_key'] for r in rows}):
            edge(('image', key), ('category', identities[key][1]), 1)
        for category in categories:
            edge(('category', category), sink, 4)
        rng.shuffle(rows)
        candidate_edges = [(r, edge(('cell', r['split'], r['category']), ('image', r['source_key']), 1)) for r in rows]
        flow = 0
        while flow < 20:
            parents = {source: None}; queue = deque([source])
            while queue and sink not in parents:
                node = queue.popleft()
                for index, e in enumerate(graph[node]):
                    if e[1] and e[0] not in parents:
                        parents[e[0]] = (node, index); queue.append(e[0])
            if sink not in parents:
                break
            node = sink
            while node != source:
                previous, index = parents[node]; e = graph[previous][index]
                e[1] -= 1; graph[node][e[2]][1] += 1; node = previous
            flow += 1
        selected = [r for r, e in candidate_edges if e[1] == 0]
        assert len(selected) == flow
        chosen.extend(selected)
        reports[country] = {
            'eligible_unique_sources': len({r['source_key'] for r in rows}), 'selected': flow,
            'available_by_split': {s: sum(r['split'] == s for r in rows) for s in SPLITS},
            'available_unique_by_category': {c: len({r['source_key'] for r in rows if r['category'] == c}) for c in CATEGORIES},
            'available_by_cell': {s: {c: sum(r['split'] == s and r['category'] == c for r in rows) for c in CATEGORIES} for s in SPLITS},
            'selected_by_split': dict(Counter(r['split'] for r in selected)),
            'selected_by_category': dict(Counter(r['category'] for r in selected)),
        }
    if any(r['selected'] != 20 for r in reports.values()):
        raise InfeasibleSelection(reports)
    chosen.sort(key=lambda r: (r['source_country'], r['split'], r['category'], r['source_key']))
    # One mixed order is frozen here; neither ZIP export nor server resamples it.
    random.Random(seed).shuffle(chosen)
    return chosen, reports


def validate_regions(regions):
    if not isinstance(regions, list) or len(regions) != 3:
        raise ValueError('Exactly three regions are required.')
    countries, evaluators, ids = [], [], []
    for r in regions:
        if not isinstance(r, dict) or not isinstance(r.get('id'), str) or not re.fullmatch(r'[a-z0-9_-]+', r['id']):
            raise ValueError('Invalid region ID.')
        if not isinstance(r.get('name'), str) or not r['name'].strip():
            raise ValueError('Region name is required.')
        if not isinstance(r.get('countries'), list) or len(r['countries']) != 3:
            raise ValueError('Each region needs three countries.')
        countries.extend(r['countries']); evaluators.append(r.get('evaluator')); ids.append(r['id'])
    if sorted(countries) != sorted(COUNTRY_CODES) or sorted(evaluators) != ['01', '02', '03'] or len(set(ids)) != 3:
        raise ValueError('Regions must partition nine countries and evaluators 01/02/03 without overlap.')


def seal_manifest(manifest):
    manifest['selection_id'] = 'selection_' + digest({k: v for k, v in manifest.items() if k != 'selection_id'})
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 'celve_selection_v1':
        raise ValueError('Unsupported selection manifest.')
    if not isinstance(manifest.get('name'), str) or not manifest['name'].strip() or not isinstance(manifest.get('model_key'), str):
        raise ValueError('Selection name and model_key are required.')
    expected = 'selection_' + digest({k: v for k, v in manifest.items() if k != 'selection_id'})
    if manifest.get('selection_id') != expected:
        raise ValueError('Selection manifest checksum mismatch; do not edit a frozen selection.')
    validate_regions(manifest['regions'])
    quotas = manifest['quotas']
    if any(quotas.get(k) != v for k, v in {'per_country': 20, 'per_split': 4, 'per_category': 4}.items()):
        raise ValueError('Selection quotas must be 20 / 4 / 4.')
    max_cell = quotas.get('max_per_cell', 4)
    if type(max_cell) is not int or not 1 <= max_cell <= 4:
        raise ValueError('Invalid cell quota.')
    runs = {r['run_id']: r for r in manifest['runs']}
    if len(manifest['runs']) != 5 or len(runs) != 5 or sorted(r['split'] for r in runs.values()) != list(SPLITS):
        raise ValueError('Use exactly one explicitly chosen run for each of the five splits.')
    if any(r['model_key'] != manifest['model_key'] for r in runs.values()):
        raise ValueError('A selection can contain only one model.')
    items = manifest['items']
    if len(items) != 180 or len({r['image_id'] for r in items}) != 180 or len({canonical_key(r['source_key']) for r in items}) != 180:
        raise ValueError('Selection must contain 180 globally unique source_key and image IDs.')
    for row in items:
        if canonical_key(row['source_key']) != row['source_key'] or not re.fullmatch(r'[A-Za-z0-9_-]+', row['image_id']):
            raise ValueError('Invalid selection identity.')
        if row['source_country'] not in COUNTRY_CODES or row['category'] not in CATEGORIES or row['split'] not in SPLITS:
            raise ValueError('Invalid selection stratum.')
        if row['run_id'] not in runs or runs[row['run_id']]['split'] != row['split']:
            raise ValueError('Selected image/run split mismatch.')
        if row['integrity']['classification'] != 'COMPLETE' or row['integrity'].get('reasons'):
            raise ValueError('Selection contains a non-COMPLETE description.')
        for field in ('description_sha256', 'description_row_sha256', 'verification_row_sha256'):
            if not isinstance(row.get(field), str) or not re.fullmatch('[0-9a-f]{64}', row[field]):
                raise ValueError('Missing selected-row content hash: ' + field)
        assets = row.get('assets', {})
        if not isinstance(assets.get('original_sha256'), str) or not re.fullmatch('[0-9a-f]{64}', assets['original_sha256']):
            raise ValueError('Missing original image hash.')
        if not isinstance(assets.get('patches'), dict) or not assets['patches']:
            raise ValueError('Missing full patch inventory.')
        for patch in assets['patches'].values():
            if not isinstance(patch.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', patch['sha256']):
                raise ValueError('Missing patch content hash.')
    for country in COUNTRY_CODES:
        selected = [r for r in items if r['source_country'] == country]
        if len(selected) != 20 or any(sum(r['split'] == s for r in selected) != 4 for s in SPLITS) or any(
                sum(r['category'] == c for r in selected) != 4 for c in CATEGORIES):
            raise ValueError('Unbalanced country/split/category selection: ' + country)
        if any(n > max_cell for n in Counter((r['split'], r['category']) for r in selected).values()):
            raise ValueError('Split/category cell exceeds configured quota: ' + country)
    return manifest


def assignments_from_selection(manifest):
    validate_manifest(manifest)
    return {r['evaluator']: {'region_id': r['id'], 'region_name': r['name'],
            'countries': r['countries'], 'per_country': 20,
            'image_ids': [i['image_id'] for i in manifest['items'] if i['source_country'] in r['countries']]}
            for r in manifest['regions']}
