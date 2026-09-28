import csv
import io
import json
import tempfile
import unittest
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from manage import activate, restore, snapshot
from railway_start import build_app
from region_loader import load_regions
from server import create_app
from test_bundle import bundle
from tools.export_human_eval_sample import select_sources


class RegionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.args = SimpleNamespace(state=self.root/'state', config=self.root/'regions.json', seed=42, max_mib=10)
        self.config = {'name': 'Regional test', 'regions': []}
        for n, countries in enumerate([['CN', 'KR', 'JP'], ['BD', 'PK', 'IN'], ['ID', 'TH', 'SG']], 1):
            bundle(self.root/f'region{n}.zip', count=63, prefix=str(n), countries=countries, two_patches=True)
            self.config['regions'].append({'id': f'region_{n}', 'name': f'Hidden region {n}',
                                          'evaluator': f'{n:02}', 'countries': countries, 'zip': f'region{n}.zip'})
        self.write_config()

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self):
        self.args.config.write_text(json.dumps(self.config))

    def prepare(self):
        manifest = load_regions(self.args)
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

    def test_quota_isolation_joins_sixty_completion_and_restore(self):
        manifest = self.prepare()
        self.assertEqual(len(manifest['study']['items']), 180)
        self.assertEqual(manifest['study_id'], self.prepare()['study_id'])
        app = create_app(self.args.state)
        clients = [self.login(app, f'{n:02}') for n in range(1, 4)]
        all_ids = set()
        for n, (client, study) in enumerate(clients, 1):
            self.assertEqual(len(study['items']), 60)
            self.assertNotIn('Hidden region', json.dumps(study))
            self.assertNotIn('source_country', json.dumps(study))
            ids = {i['image_id'] for i in study['items']}
            self.assertFalse(ids & all_ids)
            all_ids.update(ids)
            counts = Counter(manifest['analysis'][i]['source_country'] for i in ids)
            self.assertEqual(sorted(counts.values()), [20, 20, 20])
            self.assertEqual(len(study['countries']), 9)
            for item in study['items']:
                self.assertEqual(len(item['patches']), 2)
            self.assertEqual(client.get('/api/evaluation').json['descriptions'], {})
        client, study = clients[0]
        foreign = clients[1][1]['items'][0]
        self.assertEqual(client.get('/'+foreign['original_image']).status_code, 404)
        body = {'evaluator': '01', 'imageId': foreign['image_id'], 'action': 'lock',
                'answers': {'countries': [], 'countryMode': 'none', 'evidence': {}}}
        self.assertEqual(client.post('/api/evaluation', json=body).status_code, 403)
        self.assertEqual(client.get('/api/export').status_code, 409)
        # Start at number 60 and verify each description is joined by identity.
        for item in reversed(study['items']):
            body['imageId'] = item['image_id']
            locked = client.post('/api/evaluation', json=body)
            self.assertEqual(locked.status_code, 200)
            self.assertEqual(locked.json['description'], manifest['descriptions'][item['image_id']]['description'])
            self.assertEqual(client.post('/api/evaluation', json={**body, 'action': 'rate', 'rating': 4}).status_code, 200)
        export = client.get('/api/export')
        self.assertEqual(export.status_code, 200)
        self.assertEqual(len(export.json['items']), 60)
        self.assertEqual({r['region_id'] for r in export.json['items']}, {'region_1'})
        self.assertEqual({r['image_number'] for r in export.json['items']}, set(range(1, 61)))
        self.assertEqual(len(export.json['provenance']), 1)
        admin, _ = self.login(app, 'researcher')
        progress = admin.get('/api/admin').json
        self.assertEqual(progress['images'], 180)
        self.assertEqual([p['total'] for p in progress['progress']], [60, 60, 60])
        self.assertEqual([p['complete'] for p in progress['progress']], [60, 0, 0])
        rows = list(csv.DictReader(io.StringIO(admin.get('/api/export?format=csv').text.lstrip('\ufeff'))))
        self.assertEqual(len(rows), 60)
        self.assertEqual(rows[0]['region_id'], 'region_1')
        backup = self.root/'backup.tar.gz'
        with redirect_stdout(io.StringIO()):
            snapshot(self.args.state, backup)
            self.args.state = self.root/'restored'
            restore(self.args.state, backup)
        restored, restored_study = self.login(create_app(self.args.state), '01')
        self.assertEqual(restored_study['studyId'], study['studyId'])
        self.assertEqual(len(restored.get('/api/evaluation').json['saved']), 60)
        media = restored.get('/'+restored_study['items'][0]['original_image'])
        self.assertEqual(media.status_code, 200)
        media.close()

    def test_insufficient_or_repeated_country_preserves_active(self):
        self.prepare()
        before = (self.args.state/'active_study.json').read_bytes()
        # 60 overall is insufficient if the individual-country quota is not met.
        bundle(self.root/'region1.zip', count=60, prefix='1', countries=['CN', 'CN', 'KR'])
        with self.assertRaisesRegex(ValueError, 'JP: need 20'):
            self.prepare()
        self.assertEqual(before, (self.args.state/'active_study.json').read_bytes())
        bundle(self.root/'region1.zip', count=63, prefix='1', countries=['CN', 'KR', 'JP'])
        self.config['regions'][1]['countries'] = ['CN', 'PK', 'IN']
        self.write_config()
        with self.assertRaisesRegex(ValueError, 'more than one region'):
            self.prepare()
        self.assertEqual(before, (self.args.state/'active_study.json').read_bytes())

    def test_first_start_then_code_update_preserves_study(self):
        setup = build_app(self.args.state, self.root/'missing.json').test_client()
        self.assertEqual(setup.get('/healthz').json['status'], 'awaiting_data')
        self.assertEqual(setup.get('/').status_code, 503)
        with redirect_stdout(io.StringIO()):
            app = build_app(self.args.state, self.args.config)
        self.assertEqual(app.test_client().get('/healthz').json['status'], 'ok')
        before = (self.args.state/'active_study.json').read_bytes()
        self.args.config.write_text('invalid replacement configuration')
        # A redeployment loads the existing active study without reimporting data.
        self.assertEqual(build_app(self.args.state, self.args.config).test_client().get('/healthz').status_code, 200)
        self.assertEqual(before, (self.args.state/'active_study.json').read_bytes())

    def test_exporter_uses_ground_truth_country_with_exact_quota(self):
        rows = {f'wrong_source/{c}/{i}': {'gt': c, 'original_pred_set': ['JP']}
                for c in ['CN', 'KR', 'JP'] for i in range(21)}
        chosen, meta = select_sources(sorted(rows), rows, ['CN', 'KR', 'JP'], 20, 2, 42)
        self.assertEqual(len(chosen), 60)
        self.assertEqual(meta['selected_source_country_counts'], {'CN': 20, 'JP': 20, 'KR': 20})
        del rows['wrong_source/CN/0']; del rows['wrong_source/CN/1']
        with self.assertRaisesRegex(ValueError, 'CN: need 20'):
            select_sources(sorted(rows), rows, ['CN', 'KR', 'JP'], 20, 2, 42)


if __name__ == '__main__':
    unittest.main()
