"""Validate evaluator assignments and return the image IDs visible to each account."""
EVALUATORS = ('01', '02', '03')


def validate_assignments(manifest):
    assignments = manifest.get('assignments')
    if manifest.get('selection_manifest'):
        from selection_manifest import assignments_from_selection
        expected = assignments_from_selection(manifest['selection_manifest'])
        if assignments != expected:
            raise ValueError('Assignments differ from the frozen selection.')
    all_ids = [i['image_id'] for i in manifest['study']['items']]
    if assignments is None:
        # Existing shared-image studies retain their original behavior.
        return {e: {'region_id': None, 'region_name': None, 'image_ids': list(all_ids)} for e in EVALUATORS}
    if not isinstance(assignments, dict) or set(assignments) != set(EVALUATORS):
        raise ValueError('A regional study must assign evaluator codes 01, 02 and 03.')
    assigned, region_ids = [], set()
    for evaluator in EVALUATORS:
        entry = assignments[evaluator]
        if not isinstance(entry, dict) or not isinstance(entry.get('region_id'), str) or not entry['region_id']:
            raise ValueError('Missing region ID: ' + evaluator)
        if entry['region_id'] in region_ids:
            raise ValueError('Each evaluator must have a different region.')
        region_ids.add(entry['region_id'])
        if not isinstance(entry.get('region_name'), str) or not entry['region_name'].strip():
            raise ValueError('Missing region name: ' + evaluator)
        ids = entry.get('image_ids')
        if not isinstance(ids, list) or len(ids) != 60 or any(not isinstance(i, str) or i not in all_ids for i in ids):
            raise ValueError('Each evaluator must have exactly 60 valid images.')
        assigned.extend(ids)
        if 'countries' in entry:
            countries = entry['countries']
            if not isinstance(countries, list) or len(countries) != 3 or len(set(countries)) != 3 or entry.get('per_country') != 20:
                raise ValueError('Each region requires three countries with 20 images each.')
            counts = {c: 0 for c in countries}
            for image_id in ids:
                country = manifest['analysis'][image_id].get('source_country')
                if country not in counts:
                    raise ValueError('Assigned image does not belong to the configured region.')
                counts[country] += 1
            if any(count != 20 for count in counts.values()):
                raise ValueError('Each country must have exactly 20 assigned images.')
    if len(assigned) != len(set(assigned)) or set(assigned) != set(all_ids) or len(all_ids) != 180:
        raise ValueError('Regional assignments must cover 180 distinct images without overlap.')
    return assignments
