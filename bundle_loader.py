"""Import the export_human_eval_sample.py ZIP without needing research-server paths."""
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import random
import re
import shutil
import stat
import tempfile
import warnings
import zipfile

from PIL import Image
from dataset_loader import COUNTRIES, country_key
from selection_manifest import (assignments_from_selection, canonical_key, digest, text_hash,
                                validate_manifest)


def safe_member(name):
    path = PurePosixPath(name)
    if (not name or '\\' in name or '\x00' in name or path.is_absolute()
            or any(p in ('', '.', '..') for p in name.rstrip('/').split('/'))
            or ':' in name):
        raise ValueError('Unsafe ZIP member path: ' + repr(name))
    return name


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def load_bundle(args):
    """Validate a ZIP, stage referenced assets, then return a frozen study manifest.

    Only manage.py activates the returned manifest after this succeeds. An invalid
    bundle cannot replace an active study. Paths inside the manifest are relative
    to state, so the whole state directory can be restored on another server.
    """
    if args.limit < 0 or not math.isfinite(args.max_mib) or args.max_mib <= 0:
        raise ValueError('limit must be >= 0 and max-mib must be finite and > 0.')
    state = args.state.resolve()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    bundle_hash = file_hash(args.zip)
    imports = state / 'imports'
    imports.mkdir(exist_ok=True, mode=0o700)
    final = imports / bundle_hash
    with tempfile.TemporaryDirectory(prefix='.import-', dir=imports) as tmp, zipfile.ZipFile(args.zip) as archive:
        entries = archive.infolist()
        if len(entries) > 100000 or len({i.filename for i in entries}) != len(entries):
            raise ValueError('ZIP has too many entries or duplicate paths.')
        if sum(i.file_size for i in entries) > args.max_mib * 1024**2:
            raise ValueError('ZIP exceeds the uncompressed --max-mib limit.')
        for entry in entries:
            safe_member(entry.filename)
            kind = stat.S_IFMT(entry.external_attr >> 16)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or entry.flag_bits & 1:
                raise ValueError('ZIP links, special files and encrypted entries are not supported.')

        def read_json(name):
            if archive.getinfo(name).file_size > 256 * 1024**2:
                raise ValueError('Metadata JSON is too large: ' + name)
            return json.loads(archive.read(name))

        study = read_json('study.json')
        descriptions = read_json('researcher/descriptions.json')
        analysis = read_json('researcher/analysis.json')
        frozen = None
        if 'researcher/selection_manifest.json' in archive.namelist():
            frozen = validate_manifest(read_json('researcher/selection_manifest.json'))
            if analysis['provenance'].get('selection_id') != frozen['selection_id']:
                raise ValueError('Bundle and selection identity differ.')
            if args.limit != 0 or getattr(args, 'countries', None):
                raise ValueError('Frozen selections cannot be resampled. Use manage.py import-selection --zip FILE.')
        elif analysis['provenance'].get('selection_id') or getattr(args, 'require_selection', False):
            raise ValueError('Missing frozen selection manifest; use the new selection exporter.')
        expected = [{'id': c, 'name': n} for c, n in COUNTRIES]
        if study.get('schema_version') != 'celve_human_eval_handoff_v1' or study.get('countries') != expected:
            raise ValueError('Unsupported study schema or country list.')
        items = study.get('items')
        if not isinstance(items, list) or not items:
            raise ValueError('The bundle has no images.')
        ids = [i['image_id'] for i in items]
        if len(set(ids)) != len(ids) or any(not isinstance(i, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', i) for i in ids):
            raise ValueError('Duplicate or invalid image IDs.')
        if args.limit > len(items):
            raise ValueError(f'Requested {args.limit} images, but ZIP contains {len(items)}. For this bundle use --limit {len(items)} or --limit 0.')
        refs = {i['image_id']: i for i in analysis['items']}
        if len(refs) != len(analysis['items']) or set(refs) != set(ids) or set(descriptions) != set(ids):
            raise ValueError('Image, description and analysis IDs must match exactly.')
        frozen_rows = {r['image_id']: r for r in frozen['items']} if frozen else {}
        if frozen and ids != [r['image_id'] for r in frozen['items']]:
            raise ValueError('ZIP image identities/order differ from the frozen selection.')
        countries = getattr(args, 'countries', None)
        if countries:
            per_country = getattr(args, 'per_country', 20)
            selected = []
            rng = random.Random(args.seed)
            for country in sorted(countries):
                candidates = [i for i, item in enumerate(items)
                              if country_key(refs[item['image_id']].get('verification_row', {}).get('gt')
                                             or refs[item['image_id']]['source_key'].replace('\\', '/').split('/')[0]) == country]
                if len(candidates) < per_country:
                    raise ValueError(f'{country}: need {per_country} images with descriptions, found {len(candidates)}.')
                selected.extend(rng.sample(candidates, per_country))
            # Mix countries so sequence boundaries do not reveal regional country labels.
            rng.shuffle(selected)
        else:
            selected = sorted(random.Random(args.seed).sample(range(len(items)), args.limit)) if args.limit else list(range(len(items)))
        combined = {'schema_version': 'celve_server_v1', 'name': args.name,
                    'study': {'countries': expected, 'items': []}, 'descriptions': {},
                    'analysis': {}, 'assets': {}, 'provenance': []}
        asset_sizes, asset_hashes, staged = {}, {}, Path(tmp)

        def asset(name):
            safe_member(name)
            suffix = PurePosixPath(name).suffix.lower()
            if not name.startswith('assets/') or suffix not in ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.tif', '.tiff'):
                raise ValueError('Unsupported image path: ' + name)
            asset_id = 'a_' + hashlib.sha256((bundle_hash + '\0' + name).encode()).hexdigest()[:24]
            if asset_id in combined['assets']:
                return 'media/' + asset_id, asset_sizes[asset_id]
            target = staged / (asset_id + suffix)
            with archive.open(name) as source, target.open('xb') as dest:
                shutil.copyfileobj(source, dest, length=1024 * 1024)
            os.chmod(target, 0o600)
            # Canonical timestamps keep snapshot IDs stable across imports/restores.
            os.utime(target, ns=(0, 0))
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(target) as im:
                    size = im.size
                    mime = Image.MIME.get(im.format)
                    im.verify()
            if mime is None or not mime.startswith('image/'):
                raise ValueError('Unsupported image content: ' + name)
            destination = final / target.name
            # Re-imports reuse identical immutable files and preserve timestamps.
            if destination.exists():
                if file_hash(destination) != file_hash(target):
                    raise ValueError('An imported asset changed. Restore it from backup before re-importing.')
                info = destination.stat()
            else:
                info = target.stat()
            combined['assets'][asset_id] = {'path': str(destination.relative_to(state)),
                'size': info.st_size, 'mtime_ns': info.st_mtime_ns, 'mime': mime}
            asset_sizes[asset_id] = size
            if frozen:
                asset_hashes[asset_id] = file_hash(target)
            return 'media/' + asset_id, size

        for index in selected:
            item = items[index]
            image_id = item['image_id']
            desc, ref = descriptions[image_id], refs[image_id]
            if not isinstance(desc, dict) or not isinstance(desc.get('description'), str) or not desc['description'].strip():
                raise ValueError('Missing generated description: ' + image_id)
            if frozen:
                row = frozen_rows[image_id]
                if (canonical_key(ref['source_key']) != row['source_key'] or ref.get('selection') != row
                        or text_hash(desc['description']) != row['description_sha256']
                        or digest(ref.get('verification_row')) != row['verification_row_sha256']):
                    raise ValueError('Image/description/verification does not match the frozen selection: ' + image_id)
            original, (width, height) = asset(item['original_image'])
            if frozen and asset_hashes[original.split('/')[1]] != frozen_rows[image_id]['assets']['original_sha256']:
                raise ValueError('Original image bytes differ from frozen selection.')
            patches, patch_ids = [], set()
            for patch in item['patches']:
                patch_id = patch['patch_id']
                if not isinstance(patch_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', patch_id) or patch_id in patch_ids:
                    raise ValueError('Duplicate or invalid patch ID: ' + image_id)
                patch_ids.add(patch_id)
                url, _ = asset(patch['image'])
                box = patch.get('box_xyxy')
                if frozen:
                    saved = frozen_rows[image_id]['assets']['patches'].get(patch_id)
                    if not saved or asset_hashes[url.split('/')[1]] != saved['sha256'] or box != saved.get('box_xyxy'):
                        raise ValueError('Patch bytes/coordinates differ from frozen selection.')
                if box is not None and (not isinstance(box, list) or len(box) != 4 or
                    any(type(v) not in (int, float) or not math.isfinite(v) for v in box) or
                    not 0 <= box[0] < box[2] <= width or not 0 <= box[1] < box[3] <= height):
                    raise ValueError('Invalid patch bounding box: ' + patch_id)
                patches.append({'patch_id': patch_id, 'image': url, 'box_xyxy': box})
            if frozen and patch_ids != set(frozen_rows[image_id]['assets']['patches']):
                raise ValueError('Full patch inventory differs from frozen selection.')
            comparisons = {}
            for country, _ in COUNTRIES:
                tf = ref['country_comparison'][country]
                evaluated, retained = tf['tf_evaluated'], tf['retained_patch_ids']
                if type(evaluated) is not bool or (not evaluated and retained is not None):
                    raise ValueError('Invalid CP candidate status: ' + image_id)
                if evaluated and (not isinstance(retained, list) or any(not isinstance(p, str) or p not in patch_ids for p in retained) or len(set(retained)) != len(retained)):
                    raise ValueError('TF patches must belong to the full patch list: ' + image_id)
                comparisons[country] = {'tf_evaluated': evaluated, 'retained_patch_ids': retained}
            if not isinstance(ref.get('source_key'), str) or not ref['source_key']:
                raise ValueError('Missing source image identity: ' + image_id)
            combined['study']['items'].append({'image_id': image_id, 'original_image': original,
                                               'patches': patches, 'width': width, 'height': height})
            combined['descriptions'][image_id] = {'description': desc['description']}
            combined['analysis'][image_id] = {'image_id': image_id, 'source_key': ref['source_key'],
                                               'country_comparison': comparisons}
            if countries:
                combined['analysis'][image_id]['source_country'] = country_key(
                    ref.get('verification_row', {}).get('gt') or ref['source_key'].replace('\\', '/').split('/')[0])
            if frozen:
                row = frozen_rows[image_id]
                combined['analysis'][image_id].update({k: row[k] for k in
                    ('source_country', 'category', 'split', 'run_id', 'description_sha256', 'integrity')})
                combined['analysis'][image_id]['selection_id'] = frozen['selection_id']
        sources = [r['source_key'] for r in combined['analysis'].values()]
        if len(set(sources)) != len(sources):
            raise ValueError('The selected study contains duplicate source images.')
        provenance = dict(analysis['provenance'])
        provenance.update(bundle_sha256=bundle_hash, bundle_image_count=len(items),
                          sample_count=len(selected), import_seed=args.seed,
                          import_policy='Same fixed selected images for all three evaluators; order follows the bundle.',
                          selected_image_ids=[items[i]['image_id'] for i in selected])
        combined['provenance'].append(provenance)
        if frozen:
            combined['name'] = frozen['name']
            combined['selection_manifest'] = frozen
            combined['assignments'] = assignments_from_selection(frozen)
            run_provenance = {r['run_id']: r for r in analysis['provenance']['runs']}
            if set(run_provenance) != {r['run_id'] for r in frozen['runs']}:
                raise ValueError('Missing selected-run provenance.')
            combined['provenance'] = []
            for region in frozen['regions']:
                for run in frozen['runs']:
                    included = [r['image_id'] for r in frozen['items']
                                if r['run_id'] == run['run_id'] and r['source_country'] in region['countries']]
                    combined['provenance'].append(dict(run_provenance[run['run_id']],
                        evaluator=region['evaluator'], region_id=region['id'], region_name=region['name'],
                        sample_count=len(included), selected_image_ids=included,
                        selection_id=frozen['selection_id'], bundle_sha256=bundle_hash,
                        integrity_policy=run['integrity_policy'], input_files=run['inputs'],
                        import_policy='Frozen global selection; no resampling or reordering.'))
        final.mkdir(exist_ok=True, mode=0o700)
        for target in staged.iterdir():
            destination = final / target.name
            if not destination.exists():
                target.replace(destination)
        return combined
