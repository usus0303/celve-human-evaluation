"""Combine regional bundles while preserving each image/patch/description join."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

from assignments import EVALUATORS, validate_assignments
from bundle_loader import load_bundle
from dataset_loader import COUNTRIES, country_key


def load_regions(args):
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8'))
    if not isinstance(config, dict) or not isinstance(config.get('regions'), list) or len(config['regions']) != 3:
        raise ValueError('The region configuration must contain exactly three regions.')
    combined = {'schema_version': 'celve_server_v1', 'name': config.get('name', 'CELVE Human Evaluation'),
                'study': {'countries': [{'id': c, 'name': n} for c, n in COUNTRIES], 'items': []},
                'descriptions': {}, 'analysis': {}, 'assets': {}, 'provenance': [], 'assignments': {}}
    if not isinstance(combined['name'], str) or not combined['name'].strip():
        raise ValueError('Invalid study name.')
    evaluators, region_ids, sources, used_countries = set(), set(), set(), set()
    for region in config['regions']:
        if not isinstance(region, dict):
            raise ValueError('Each region must be an object.')
        evaluator, region_id, name = region.get('evaluator'), region.get('id'), region.get('name')
        if not isinstance(evaluator, str) or evaluator not in EVALUATORS or evaluator in evaluators:
            raise ValueError('Use evaluator codes 01, 02 and 03 exactly once.')
        if not isinstance(region_id, str) or not re.fullmatch(r'[a-z0-9_-]+', region_id) or region_id in region_ids:
            raise ValueError('Each region needs a unique lowercase region ID.')
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Each region needs a name.')
        evaluators.add(evaluator); region_ids.add(region_id)
        if not isinstance(region.get('zip'), str) or not region['zip']:
            raise ValueError('Each region must specify its data ZIP.')
        zip_path = Path(region['zip'])
        if not zip_path.is_absolute():
            zip_path = config_path.parent / zip_path
        allowed_countries = region.get('countries')
        if not isinstance(allowed_countries, list) or len(allowed_countries) != 3 or any(not isinstance(c, str) for c in allowed_countries):
            raise ValueError('Each region must list exactly three country codes.')
        allowed_countries = {country_key(c) for c in allowed_countries}
        if len(allowed_countries) != 3 or not allowed_countries.issubset({c for c, _ in COUNTRIES}):
            raise ValueError('Each region must contain three distinct supported countries: ' + name)
        if used_countries & allowed_countries:
            raise ValueError('A country cannot belong to more than one region.')
        used_countries.update(allowed_countries)
        options = SimpleNamespace(state=args.state, zip=zip_path, limit=60, seed=args.seed,
                                  max_mib=args.max_mib, name=name, countries=allowed_countries, per_country=20)
        result = load_bundle(options)
        for image_id, reference in result['analysis'].items():
            source = reference['source_key'].replace('\\', '/').strip().casefold()
            if image_id in combined['analysis'] or source in sources:
                raise ValueError('Regional image sets must not overlap: ' + reference['source_key'])
            if reference['source_country'] not in allowed_countries:
                raise ValueError('Image source country does not belong to region ' + name + ': ' + reference['source_key'])
            sources.add(source)
            combined['analysis'][image_id] = reference
        for asset_id, asset in result['assets'].items():
            if asset_id in combined['assets'] and combined['assets'][asset_id] != asset:
                raise ValueError('Conflicting regional asset identity.')
            combined['assets'][asset_id] = asset
        combined['study']['items'].extend(result['study']['items'])
        combined['descriptions'].update(result['descriptions'])
        image_ids = [item['image_id'] for item in result['study']['items']]
        combined['assignments'][evaluator] = {'region_id': region_id, 'region_name': name,
                                              'countries': sorted(allowed_countries), 'per_country': 20, 'image_ids': image_ids}
        for provenance in result['provenance']:
            provenance.update(evaluator=evaluator, region_id=region_id, region_name=name,
                              countries=sorted(allowed_countries), per_country=20,
                              import_policy='20 images per source country, three countries per evaluator; seeded mixed order.')
            combined['provenance'].append(provenance)
    validate_assignments(combined)
    return combined
