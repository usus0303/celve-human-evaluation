import copy
import csv
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import zipfile
from collections import Counter
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from PIL import Image
from bundle_loader import load_bundle
from manage import activate, snapshot, restore
from railway_start import build_app
from selection_manifest import (CATEGORIES, COUNTRY_CODES, SPLITS, InfeasibleSelection,
    check_integrity, file_hash, seal_manifest, select_balanced, text_hash, validate_manifest)
from server import create_app
from tools.export_human_eval_sample import stable_id
from tools.select_human_eval import choose, main as select_main, write_new
from tools.export_human_eval_selection import export_selection


def fixture(root):
    project, backup = root/'project', root/'backup'
    project.mkdir(); backup.mkdir()
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    png = io.BytesIO(); Image.new('RGB', (2, 2), 'blue').save(png, format='PNG')
    batches, sources = [], []
    for country in COUNTRY_CODES:
        for category in CATEGORIES:
            for index in range(5):
                key = f'{country.lower()}/{category}/image{index}.png'
                original = project/'image_resize'/key
                original.parent.mkdir(parents=True, exist_ok=True); original.write_bytes(png.getvalue())
                patches = []
                for n in range(2):
                    path = project/'patches'/key.replace('.png', f'_p{n}.png')
                    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(png.getvalue()); patches.append(path)
                    batches.append({'source_key': key, 'source_image': str(original), 'box_patch': str(path), 'box_xyxy': [0, 0, 1, 1]})
                sources.append((key, country, original, patches))
    batch = project/'batch.json'; write(batch, batches)
    selection = backup/'thresholds.json'
    write(selection, {'models': {'test_model': {'splits': {s: {'quantile': .75, 'threshold': .8} for s in SPLITS}}}})
    config = {'model_key': 'test_model', 'project_root': str(project), 'backup_root': str(backup),
              'selection_file': str(selection), 'batch_meta': str(batch), 'patch_root': str(project/'patches'),
              'max_per_cell': 1, 'seed': 42, 'runs': []}
    for n, split in enumerate(SPLITS):
        descriptions, verification, tf = [], [], []
        for key, country, original, patches in sources:
            # Same 225 originals appear in all 5 splits; TF evidence differs by split.
            descriptions.append({'source_key': key, 'gt': country, 'status': 'ok', 'split': split,
                'verification_model': 'test_model', 'description_model': 'test_model', 'quantile': .75, 'threshold': .8,
                'aps_pred_set': ['KR', 'JP'], 'refined_pred_set': ['KR'], 'original_image': str(original),
                'description': f'Exact description from {split} for {key}.',
                'generation_calls': [{'chunk_index': 1, 'status': 'ok', 'finish_reason': 'eos'}]})
            verification.append({'source_key': key, 'gt': country, 'original_pred_set': ['KR', 'JP'],
                'refined_pred_set': ['KR'], 'supporting_patches': {'KR': [{'box_patch': str(patches[n % 2]), 'c': .9}]}})
            tf.append({'source_key': key, 'source_image': str(original)})
        files = {name: backup/f'{split}_{name}.json' for name in ('description_file', 'verification_file', 'tf_file')}
        for name, rows in zip(files, (descriptions, verification, tf)):
            write(files[name], rows)
        config['runs'].append({'split': split, **{k: str(v) for k, v in files.items()}})
    config_path = root/'config.json'; write(config_path, config)
    return config_path, config


class SamplingAlgorithmTests(unittest.TestCase):
    def test_residual_reassignment_unique_sources_and_joint_margins(self):
        candidates = [{'source_country': 'KR', 'source_key': f'kr/{c}/{n}.png', 'category': c, 'split': s}
                      for c in CATEGORIES for n in range(4) for s in SPLITS]
        chosen, _ = select_balanced(candidates, max_per_cell=1, countries=['KR'])
        again, _ = select_balanced(list(reversed(candidates)), max_per_cell=1, countries=['KR'])
        self.assertEqual(chosen, again)
        self.assertEqual(len({r['source_key'] for r in chosen}), 20)
        self.assertEqual(Counter(r['split'] for r in chosen), dict.fromkeys(SPLITS, 4))
        self.assertEqual(Counter(r['category'] for r in chosen), dict.fromkeys(CATEGORIES, 4))
        self.assertEqual(max(Counter((r['split'], r['category']) for r in chosen).values()), 1)

    def test_joint_infeasible_despite_sufficient_marginal_counts(self):
        candidates = []
        for c in CATEGORIES:
            for n in range(4):
                splits = SPLITS[:2] if c == 'architecture' else SPLITS[2:]
                candidates.extend({'source_country': 'KR', 'source_key': f'kr/{c}/{n}.png', 'category': c, 'split': s} for s in splits)
        with self.assertRaises(InfeasibleSelection) as caught:
            select_balanced(candidates, countries=['KR'])
        report = caught.exception.report['KR']
        self.assertEqual(report['eligible_unique_sources'], 20)
        self.assertTrue(all(n >= 4 for n in report['available_by_split'].values()))
        self.assertEqual(report['selected'], 16)

    def test_integrity_missing_unknown_length_bad_endings_and_external(self):
        good = {'status': 'ok', 'description': 'A complete cultural description.', 'finish_reason': 'eos'}
        self.assertEqual(check_integrity(good)['classification'], 'COMPLETE')
        for update, reason in [({'finish_reason': 'length'}, 'length'), ({'finish_reason': 'unknown'}, 'unknown_finish_reason'),
                               ({'description': 'Truncated with'}, 'bad_ending'), ({'description': 'An <unk> object.'}, 'unknown_token'),
                               ({'description': ''}, 'empty_description')]:
            self.assertIn(reason, check_integrity(dict(good, **update))['reasons'])
        self.assertIn('missing_finish_reason', check_integrity({'status': 'ok', 'description': 'Text.'})['reasons'])
        chunks = {'status': 'ok', 'description': 'Earlier fragment. Final sentence.',
                  'description_chunks': [{'chunk_index': 1, 'description': 'Earlier fragment with'}, {'chunk_index': 2, 'description': 'Final sentence.'}],
                  'generation_calls': [{'chunk_index': i, 'status': 'ok', 'finish_reason': 'eos'} for i in (1, 2)]}
        self.assertIn('bad_ending', check_integrity(chunks)['reasons'])
        report = {'classification': 'COMPLETE', 'unknown': False, 'bad_ending': False}
        self.assertEqual(check_integrity(dict(good, description='Externally reviewed caption'), report, 'existing_audit_v3')['classification'], 'COMPLETE')
        self.assertIn('length', check_integrity(dict(good, finish_reason='length'), report, 'existing_audit_v3')['reasons'])


class SelectionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.config_path, self.config = fixture(self.root)
        self.manifest, self.report = choose(self.config_path)
        self.selection = self.root/'selection.json'; write_new(self.selection, self.manifest)
        self.zip = self.root/'human_eval_180.zip'
        self.args = SimpleNamespace(state=self.root/'state', zip=self.zip, limit=0, seed=999,
                                    max_mib=30, name='Ignored for frozen selection', require_selection=True)

    def tearDown(self):
        self.temp.cleanup()

    def export(self):
        with redirect_stdout(io.StringIO()):
            export_selection(self.selection, self.zip, 30)

    def activate(self):
        manifest = load_bundle(self.args)
        with redirect_stdout(io.StringIO()):
            activate(manifest, self.args.state)
        return manifest

    def login(self, app, account):
        codes = dict(line.split(': ', 1) for line in (self.args.state/'access_codes.txt').read_text().splitlines() if ': ' in line)
        client = app.test_client()
        result = client.post('/api/login', json={'code': codes[account]})
        self.assertEqual(result.status_code, 200)
        client.environ_base['HTTP_X_CSRF_TOKEN'] = result.json['csrf']
        study = client.get('/api/study').json
        client.environ_base['HTTP_X_STUDY_ID'] = study['studyId']
        return client, study

    def test_end_to_end_frozen_selection_exact_run_full_patches_and_export(self):
        self.assertEqual(self.manifest, choose(self.config_path)[0])
        self.assertEqual(Counter(i['split'] for i in self.manifest['items']), dict.fromkeys(SPLITS, 36))
        self.export(); server_manifest = self.activate()
        second_zip = self.root/'same_selection_again.zip'
        with redirect_stdout(io.StringIO()):
            export_selection(self.selection, second_zip, 30)
        self.assertEqual(file_hash(self.zip), file_hash(second_zip))
        self.assertEqual(self.manifest, server_manifest['selection_manifest'])
        with redirect_stdout(io.StringIO()):
            first_start = build_app(self.root/'first_start', selection_zip=self.zip)
        self.assertEqual(first_start.test_client().get('/healthz').json['status'], 'ok')
        self.assertEqual(json.loads((self.root/'first_start/active_study.json').read_text())['study_id'], server_manifest['study_id'])
        # Import seed does not change the selected images or their order.
        self.args.seed = 7
        self.assertEqual(server_manifest['study_id'], self.activate()['study_id'])
        app = create_app(self.args.state)
        client, study = self.login(app, '01')
        expected_ids = server_manifest['assignments']['01']['image_ids']
        self.assertEqual([r['image_id'] for r in study['items']], expected_ids)
        self.assertEqual(len(expected_ids), 60)
        for hidden in ('source_key', 'category', 'run_id', 'source_country', 'selection_id', 'split_'):
            self.assertNotIn(hidden, json.dumps(study))
        self.assertEqual(client.get('/api/evaluation').json['descriptions'], {})
        item = study['items'][-1]
        selected = next(r for r in self.manifest['items'] if r['image_id'] == item['image_id'])
        self.assertEqual(len(item['patches']), 2)
        support_index = SPLITS.index(selected['split']) % 2
        expected_patch = stable_id('p_', selected['source_key'] + '\0' + Path(selected['source_key']).name.replace('.png', f'_p{support_index}.png'))
        actual_tf = server_manifest['analysis'][item['image_id']]['country_comparison']['KR']['retained_patch_ids']
        self.assertEqual(actual_tf, [expected_patch])
        # One patch can support two human-selected countries.
        body = {'evaluator': '01', 'imageId': item['image_id'], 'action': 'lock', 'answers': {
            'countryMode': 'selected', 'countries': ['KR', 'JP'],
            'evidence': {c: {'mode': 'patches', 'patches': [expected_patch]} for c in ('KR', 'JP')}}}
        locked = client.post('/api/evaluation', json=body)
        self.assertEqual(locked.status_code, 200)
        self.assertEqual(text_hash(locked.json['description']), selected['description_sha256'])
        self.assertIn(selected['split'], locked.json['description'])
        self.assertEqual(client.post('/api/evaluation', json={**body, 'action': 'rate', 'rating': 5}).status_code, 200)
        admin, _ = self.login(app, 'researcher')
        info = admin.get('/api/admin').json
        self.assertEqual(info['selectionId'], self.manifest['selection_id'])
        self.assertEqual(info['images'], 180)
        self.assertEqual(len(info['provenance']), 15)
        row = admin.get('/api/export').json['items'][0]
        self.assertEqual(row['split'], selected['split']); self.assertEqual(row['category'], selected['category'])
        self.assertEqual(row['comparisons'][0]['jaccard'], 1)
        csv_rows = list(csv.DictReader(io.StringIO(admin.get('/api/export?format=csv').text.lstrip('\ufeff'))))
        self.assertEqual(csv_rows[0]['run_id'], selected['run_id'])
        self.assertEqual(csv_rows[0]['selection_id'], self.manifest['selection_id'])
        # Other region remains inaccessible even when an asset ID is known.
        _, other_study = self.login(app, '02')
        self.assertEqual(client.get('/'+other_study['items'][0]['original_image']).status_code, 404)
        snapshot_path = self.root/'snapshot.tar.gz'
        with redirect_stdout(io.StringIO()):
            snapshot(self.args.state, snapshot_path)
            self.args.state = self.root/'restored'
            restore(self.args.state, snapshot_path)
        restored, restored_study = self.login(build_app(self.args.state, selection_zip=self.root/'missing.zip'), '01')
        self.assertEqual(study['studyId'], restored_study['studyId'])
        self.assertEqual(restored.get('/api/evaluation').json['saved'][0]['answers']['rating'], 5)

    def test_manifest_checksums_margins_and_global_source_uniqueness(self):
        changed = copy.deepcopy(self.manifest); changed['items'][0]['split'] = 'split_05'
        with self.assertRaisesRegex(ValueError, 'checksum'):
            validate_manifest(changed)
        repeated = copy.deepcopy(self.manifest); repeated['items'][1]['source_key'] = repeated['items'][0]['source_key']
        with self.assertRaisesRegex(ValueError, 'globally unique'):
            seal_manifest(repeated)
        changed = copy.deepcopy(self.manifest)
        changed['items'][0]['category'] = next(c for c in CATEGORIES if c != changed['items'][0]['category'])
        with self.assertRaisesRegex(ValueError, 'Unbalanced'):
            seal_manifest(changed)

    def test_changed_inputs_and_pixels_abort_export_without_zip(self):
        path = Path(self.manifest['runs'][0]['inputs']['description_file']['path'])
        before = path.read_bytes(); path.write_bytes(before + b'\n')
        with self.assertRaisesRegex(ValueError, 'Frozen input file changed'):
            self.export()
        self.assertFalse(self.zip.exists()); path.write_bytes(before)
        selected = self.manifest['items'][0]
        original = Path(self.config['project_root'])/'image_resize'/selected['source_key']
        original.write_bytes(b'changed image bytes')
        with self.assertRaisesRegex(ValueError, 'original image changed'):
            self.export()
        self.assertFalse(self.zip.exists())

    def test_external_integrity_report_is_bound_to_exact_description_file(self):
        run = self.config['runs'][0]
        description_path = Path(run['description_file'])
        rows = json.loads(description_path.read_text())
        audit = {'schema_version': 'celve_integrity_v1', 'policy_id': 'existing_integrity_v3',
                 'description_file_sha256': file_hash(description_path), 'items': [
                     {'source_key': r['source_key'], 'description_sha256': text_hash(r['description']),
                      'classification': 'COMPLETE', 'unknown': False, 'bad_ending': False} for r in rows]}
        path = self.root/'audit.json'; path.write_text(json.dumps(audit))
        run['integrity_file'] = str(path); self.config_path.write_text(json.dumps(self.config))
        manifest, _ = choose(self.config_path)
        self.assertEqual(manifest['runs'][0]['integrity_policy'], 'existing_integrity_v3')
        audit['description_file_sha256'] = '0' * 64; path.write_text(json.dumps(audit))
        with self.assertRaisesRegex(ValueError, 'stale integrity'):
            choose(self.config_path)

    def test_import_rejects_resampling_description_changes_and_missing_patch(self):
        self.export(); self.activate()
        active = (self.args.state/'active_study.json').read_bytes()
        self.args.limit = 60
        with self.assertRaisesRegex(ValueError, 'cannot be resampled'):
            load_bundle(self.args)
        self.args.limit = 0
        with zipfile.ZipFile(self.zip) as z:
            originals = {name: z.read(name) for name in z.namelist()}
        for mutation in ('description', 'patch'):
            entries = dict(originals)
            if mutation == 'description':
                d = json.loads(entries['researcher/descriptions.json']); first = next(iter(d)); d[first]['description'] = 'Wrong run description.'
                entries['researcher/descriptions.json'] = json.dumps(d).encode()
            else:
                d = json.loads(entries['study.json']); d['items'][0]['patches'].pop()
                entries['study.json'] = json.dumps(d).encode()
            self.args.zip = self.root/f'bad_{mutation}.zip'
            with zipfile.ZipFile(self.args.zip, 'w') as z:
                for name, value in entries.items(): z.writestr(name, value)
            with self.assertRaises(ValueError): self.activate()
            self.assertEqual(active, (self.args.state/'active_study.json').read_bytes())

    def test_cli_infeasibility_report_excludes_unknown_and_no_manifest(self):
        # Remove all eligible architecture descriptions while other margins remain large.
        for run in self.config['runs']:
            path = Path(run['description_file']); rows = json.loads(path.read_text())
            for row in rows:
                if '/architecture/' in row['source_key']:
                    row['generation_calls'][0]['finish_reason'] = 'unknown'
            path.write_text(json.dumps(rows))
        output, report = self.root/'new_selection.json', self.root/'report.json'
        argv = ['select_human_eval.py', '--config', str(self.config_path), '--output', str(output), '--report', str(report)]
        with patch('sys.argv', argv), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = select_main()
        self.assertEqual(result, 1); self.assertFalse(output.exists())
        audit = json.loads(report.read_text()); self.assertEqual(audit['status'], 'infeasible')
        self.assertGreater(audit['runs'][0]['exclusion_reason_counts']['unknown_finish_reason'], 0)
        self.assertEqual(audit['countries']['KR']['available_unique_by_category']['architecture'], 0)


if __name__ == '__main__':
    unittest.main()
