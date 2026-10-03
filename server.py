#!/usr/bin/env python3
"""CELVE on your own server: Flask + Waitress, local images and SQLite."""
import copy
import argparse
import csv
import hmac
import io
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, request, jsonify, g, send_file, Response
from werkzeug.exceptions import HTTPException
from storage import initialize, connect, digest
from assignments import validate_assignments

BASE = Path(__file__).resolve().parent


class Problem(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def validate(raw, item, allowed, lock, draft=False):
    if not isinstance(raw, dict):
        raise Problem('Missing answers.')
    mode, countries = raw.get('countryMode'), raw.get('countries')
    if mode not in ('selected', 'none', 'unsure') or not isinstance(countries, list):
        raise Problem('Choose countries or a no-selection option.')
    if any(not isinstance(c, str) or c not in allowed for c in countries) or len(set(countries)) != len(countries):
        raise Problem('Invalid country selection.')
    if (mode != 'selected' and countries) or (not draft and mode == 'selected' and not countries):
        raise Problem('Choose at least one country, or none / unsure.')
    evidence = {}
    if countries:
        raw_evidence = raw.get('evidence', {})
        if not isinstance(raw_evidence, dict):
            raise Problem('Answer every evidence question.')
        valid_patches = {p['patch_id'] for p in item['patches']}
        for c in countries:
            e = raw_evidence.get(c)
            if e is None and not lock:
                continue
            if not isinstance(e, dict) or e.get('mode') not in ('patches', 'none', 'unsure', 'outside'):
                raise Problem('Answer the evidence question for every selected country.')
            patches = e.get('patches')
            if not isinstance(patches, list) or any(not isinstance(p, str) or p not in valid_patches for p in patches):
                raise Problem('Invalid patch selection.')
            if len(set(patches)) != len(patches) or (e['mode'] != 'patches' and patches) or (lock and e['mode'] == 'patches' and not patches):
                raise Problem('Select patches or one alternative answer.')
            evidence[c] = {'mode': e['mode'], 'patches': patches}
    return {'countries': countries, 'countryMode': mode, 'evidence': evidence,
            'language': 'ko' if raw.get('language') == 'ko' else 'en'}


def comparison(answers, reference):
    result = []
    for country in answers['countries']:
        e = answers['evidence'][country]
        tf = reference['country_comparison'][country]
        human = e['patches'] if e['mode'] == 'patches' else [] if e['mode'] == 'none' else None
        model = tf['retained_patch_ids'] if tf['tf_evaluated'] else None
        comparable = human is not None and model is not None
        overlap = len(set(human) & set(model)) if comparable else None
        union = len(set(human) | set(model)) if comparable else None
        result.append({'country': country, 'evidence_status': e['mode'],
                       'tf_evaluated': tf['tf_evaluated'], 'human_patch_ids': human,
                       'model_patch_ids': model, 'comparable': comparable,
                       'precision': overlap / len(model) if comparable and model else None,
                       'recall': overlap / len(human) if comparable and human else None,
                       'jaccard': overlap / union if comparable and union else None,
                       'both_empty': comparable and not human and not model})
    return result


def create_app(state=BASE / 'state', secure_cookies=False):
    state = Path(state).resolve()
    initialize(state)
    active = json.loads((state / 'active_study.json').read_text())['study_id']
    if not active.startswith('study_') or not active[6:].isalnum():
        raise ValueError('Invalid active study ID.')
    manifest = json.loads((state / 'studies' / (active + '.json')).read_text())
    if manifest.get('schema_version') != 'celve_server_v1' or manifest['study_id'] != active:
        raise ValueError('Invalid study snapshot. Run manage.py prepare.')
    study = manifest['study']
    items = {i['image_id']: i for i in study['items']}
    assignments = validate_assignments(manifest)
    assigned_ids = {e: set(a['image_ids']) for e, a in assignments.items()}
    assigned_assets = {e: {url.split('/', 1)[1] for image_id in a['image_ids']
                          for url in [items[image_id]['original_image']] + [p['image'] for p in items[image_id]['patches']]}
                       for e, a in assignments.items()}
    allowed = {c['id'] for c in study['countries']}
    app = Flask(__name__, static_folder=str(BASE / 'static'), static_url_path='/static')
    app.config.update(MAX_CONTENT_LENGTH=65536, JSON_SORT_KEYS=False)

    def db():
        if 'db' not in g:
            g.db = connect(state)
        return g.db

    @app.teardown_appcontext
    def close_db(exc):
        connection = g.pop('db', None)
        if connection is not None:
            connection.close()

    def authenticated(fn):
        @wraps(fn)
        def inner(*args, **kwargs):
            token = request.cookies.get('celve_session', '')
            if not token or len(token) > 200:
                raise Problem('Please sign in.', 401)
            row = db().execute('SELECT s.account,s.csrf,a.role FROM sessions s JOIN accounts a ON a.id=s.account WHERE s.token_hash=? AND s.expires>?',
                               (digest(token), time.time())).fetchone()
            if row is None:
                raise Problem('Your session expired. Sign in again to resume.', 401)
            g.account, g.role, g.csrf = row['account'], row['role'], row['csrf']
            return fn(*args, **kwargs)
        return inner

    def evaluator_only():
        if g.role != 'evaluator':
            raise Problem('Use an evaluator access code to complete an evaluation.', 403)
        value = request.args.get('evaluator')
        if value is not None and value != g.account:
            raise Problem('This evaluator belongs to a different access code.', 403)

    @app.before_request
    def origin_check():
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('Origin')
            if origin and urlsplit(origin).netloc != request.host:
                raise Problem('Invalid request origin.', 403)
        if request.method == 'OPTIONS':
            raise Problem('Cross-origin API requests are not enabled.', 403)

    def study_check():
        if request.headers.get('X-Study-ID') != active:
            raise Problem('The study has changed or was not loaded. Reload the page before continuing.', 409)

    def csrf_check():
        if not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), g.csrf):
            raise Problem('Session check failed. Reload the page and try again.', 403)

    @app.after_request
    def response_headers(r):
        if request.path.startswith('/api/'):
            r.headers['Cache-Control'] = 'no-store'
        r.headers['X-Content-Type-Options'] = 'nosniff'
        r.headers['Referrer-Policy'] = 'same-origin'
        r.headers['X-Frame-Options'] = 'SAMEORIGIN'
        return r

    @app.errorhandler(Problem)
    def expected_error(e):
        return jsonify(error=e.message), e.status

    @app.errorhandler(Exception)
    def unexpected_error(e):
        if isinstance(e, HTTPException):
            return jsonify(error=e.description), e.code
        app.logger.exception('Request failed')
        return jsonify(error='The server could not complete the request. Your saved responses are preserved.'), 500

    @app.get('/')
    def index():
        return send_file(BASE / 'static' / 'index.html', max_age=0)

    @app.get('/favicon.svg')
    def favicon():
        return send_file(BASE / 'static' / 'favicon.svg')
        
    @app.get('/review')
    def researcher_review_page():
        return send_file(BASE / 'static' / 'review.html', max_age=0)
    
    @app.get('/healthz')
    def health():
        return jsonify(status='ok')

    @app.post('/api/login')
    def login():
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            raise Problem('Invalid access code.', 401)
        code = payload.get('code', '')
        if not isinstance(code, str) or len(code) > 200:
            raise Problem('Invalid access code.', 401)
        now = time.time()
        ip = request.remote_addr or ''
        db().execute('DELETE FROM login_failures WHERE time<?', (now - 600,))
        if db().execute('SELECT count(*) FROM login_failures WHERE ip=? AND time>?', (ip, now - 600)).fetchone()[0] >= 30:
            raise Problem('Too many incorrect attempts. Try again in 10 minutes.', 429)
        row = db().execute('SELECT id,role FROM accounts WHERE code_hash=?', (digest(code.strip()),)).fetchone()
        if row is None:
            db().execute('INSERT INTO login_failures VALUES (?,?)', (ip, now))
            raise Problem('Invalid access code.', 401)
        old = request.cookies.get('celve_session')
        if old:
            db().execute('DELETE FROM sessions WHERE token_hash=?', (digest(old),))
        db().execute('DELETE FROM sessions WHERE expires<?', (now,))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        db().execute('INSERT INTO sessions VALUES (?,?,?,?)', (digest(token), row['id'], csrf, now + 30 * 86400))
        r = jsonify(account=row['id'], role=row['role'], csrf=csrf)
        r.set_cookie('celve_session', token, httponly=True, samesite='Lax', secure=secure_cookies, max_age=30 * 86400, path='/')
        return r

    @app.get('/api/session')
    @authenticated
    def session():
        return jsonify(account=g.account, role=g.role, csrf=g.csrf)

    @app.post('/api/logout')
    @authenticated
    def logout():
        csrf_check()
        db().execute('DELETE FROM sessions WHERE token_hash=?', (digest(request.cookies['celve_session']),))
        r = jsonify(ok=True)
        r.delete_cookie('celve_session', path='/')
        return r

    @app.get('/api/study')
    @authenticated
    def public_study():
        public = copy.deepcopy(study)
        if g.role == 'evaluator':
            public['items'] = [copy.deepcopy(items[i]) for i in assignments[g.account]['image_ids']]
        for item in public['items']:
            item['original_image'] += '?study_id=' + active
            for patch in item['patches']:
                patch['image'] += '?study_id=' + active
        return jsonify(**public, studyId=active, name=manifest['name'])

    @app.get('/media/<asset_id>')
    @authenticated
    def media(asset_id):
        if request.args.get('study_id') != active:
            raise Problem('The study changed. Reload the page to load the current images.', 409)
        asset = manifest['assets'].get(asset_id)
        if not asset or (g.role == 'evaluator' and asset_id not in assigned_assets[g.account]):
            raise Problem('Unknown image.', 404)
        path = Path(asset['path'])
        if not path.is_absolute():
            path = state / path
        try:
            stat = path.stat()
        except OSError:
            raise Problem('The image is unavailable on the server. Please notify the researcher.', 404)
        if stat.st_size != asset['size'] or stat.st_mtime_ns != asset['mtime_ns']:
            raise Problem('This image changed after the study was prepared. Please notify the researcher.', 409)
        if not asset['mime'].startswith('image/') or path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff'):
            raise Problem('Unsupported image format.', 415)
        r = send_file(path, mimetype=asset['mime'], conditional=True, download_name=asset_id + path.suffix.lower(), max_age=0)
        r.headers['Cache-Control'] = 'private, no-cache'
        return r

    def saved_rows(evaluator):
        rows = db().execute('SELECT image_id,stage,answers,updated_at FROM responses WHERE study_id=? AND evaluator=?',
                            (active, evaluator)).fetchall()
        return [r for r in rows if r['image_id'] in assigned_ids[evaluator]]

    @app.get('/api/evaluation')
    @authenticated
    def evaluation_get():
        evaluator_only()
        study_check()
        rows = [dict(r) for r in saved_rows(g.account)]
        descriptions = {}
        for row in rows:
            row['answers'] = json.loads(row['answers'])
            if row['stage'] in ('locked', 'complete'):
                descriptions[row['image_id']] = manifest['descriptions'][row['image_id']]['description']
        return jsonify(saved=rows, descriptions=descriptions, studyId=active)

    @app.post('/api/evaluation')
    @authenticated
    def evaluation_post():
        evaluator_only()
        study_check()
        csrf_check()
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise Problem('Invalid request.')
        if body.get('evaluator') != g.account:
            raise Problem('This evaluator belongs to a different access code.', 403)
        image_id, action = body.get('imageId'), body.get('action')
        if not isinstance(image_id, str) or image_id not in items:
            raise Problem('Unknown image.')
        if image_id not in assigned_ids[g.account]:
            raise Problem('This image is not assigned to your access code.', 403)
        if action not in ('countries', 'lock', 'rate', 'draft'):
            raise Problem('Unknown action.')
        connection = db()
        connection.execute('BEGIN IMMEDIATE')
        try:
            row = connection.execute('SELECT stage,answers FROM responses WHERE study_id=? AND evaluator=? AND image_id=?',
                                     (active, g.account, image_id)).fetchone()
            now = utcnow()
            if action == 'rate' or (action == 'draft' and body.get('step') == 3):
                if row is None or row['stage'] not in ('locked', 'complete'):
                    raise Problem('Confirm country and evidence answers before rating.', 409)
                unsure = body.get('ratingUnsure') is True
                rating = None if unsure else body.get('rating')
                if not unsure and (type(rating) is not int or not 1 <= rating <= 5):
                    raise Problem('Select a rating from 1 to 5, or unable to judge.')
                answers = json.loads(row['answers'])
                if row['stage'] == 'complete':
                    if action == 'draft' or answers.get('rating') != rating or answers.get('ratingUnsure') != unsure:
                        raise Problem('This rating was already submitted.', 409)
                else:
                    answers.update(rating=rating, ratingUnsure=unsure)
                    if action == 'rate':
                        answers['submittedAt'] = now
                    connection.execute("UPDATE responses SET stage=?,answers=?,updated_at=? WHERE study_id=? AND evaluator=? AND image_id=?",
                                       ('complete' if action == 'rate' else 'locked', json.dumps(answers), now, active, g.account, image_id))
                result = dict(stage='complete' if action == 'rate' else 'locked', answers=answers)
            else:
                lock = action == 'lock'
                if action == 'draft' and (type(body.get('step')) is not int or body['step'] not in (1, 2)):
                    raise Problem('Invalid evaluation step.')
                answers = validate(body.get('answers'), items[image_id], allowed, lock, action == 'draft')
                if row is not None and row['stage'] in ('locked', 'complete'):
                    existing = json.loads(row['answers'])
                    if not lock or any(existing.get(k) != answers[k] for k in ('countries','countryMode','evidence','language')):
                        raise Problem('Confirmed answers cannot be edited after viewing the description.', 409)
                    result = dict(stage=row['stage'], answers=existing, description=manifest['descriptions'][image_id]['description'])
                else:
                    stage = 'locked' if lock else 'draft' if action == 'draft' else 'evidence'
                    if lock:
                        answers['lockedAt'] = now
                    else:
                        answers['draftStep'] = body['step'] if action == 'draft' else 2
                    connection.execute('INSERT INTO responses VALUES (?,?,?,?,?,?) ON CONFLICT(study_id,evaluator,image_id) DO UPDATE SET stage=excluded.stage,answers=excluded.answers,updated_at=excluded.updated_at',
                                       (active, g.account, image_id, stage, json.dumps(answers), now))
                    result = dict(stage=stage, answers=answers)
                    if lock:
                        result['description'] = manifest['descriptions'][image_id]['description']
            connection.commit()
            return jsonify(result)
        except BaseException:
            connection.rollback()
            raise

    @app.get('/api/admin')
    @authenticated
    def admin():
        if g.role != 'researcher':
            raise Problem('Researcher access is required.', 403)
        progress = []
        for evaluator in ('01', '02', '03'):
            rows = saved_rows(evaluator)
            progress.append({'evaluator': evaluator, 'complete': sum(r['stage']=='complete' for r in rows),
                             'in_progress': sum(r['stage']!='complete' for r in rows),
                             'total': len(assigned_ids[evaluator]), 'region_id': assignments[evaluator]['region_id'],
                             'region_name': assignments[evaluator]['region_name']})
        return jsonify(name=manifest['name'], studyId=active, images=len(items),
                       patches=sum(len(i['patches']) for i in study['items']), progress=progress,
                       provenance=manifest['provenance'],
                       selectionId=manifest.get('selection_manifest', {}).get('selection_id'),
                       samplingQuotas=manifest.get('selection_manifest', {}).get('quotas'))
    @app.get('/api/admin/review')
    @authenticated
    def admin_review():
        """Researcher-only payload for browsing all study images and patches."""

        if g.role != 'researcher':
            raise Problem('Researcher access is required.', 403)

        study_check()

    # 각 이미지가 어떤 evaluator / region에 배정됐는지 기록
    image_assignment = {}

        for evaluator, entry in assignments.items():
            for image_id in entry['image_ids']:
                image_assignment[image_id] = {
                    'evaluator': evaluator,
                    'region_id': entry.get('region_id'),
                    'region_name': entry.get('region_name'),
                    }
    @app.get('/api/export')
    @authenticated
    def export():
        study_check()
        if g.role == 'researcher':
            evaluator = request.args.get('evaluator', 'all')
            if evaluator not in ('all','01','02','03'):
                raise Problem('Invalid evaluator.')
        else:
            evaluator_only()
            evaluator = g.account
            done = sum(r['stage']=='complete' for r in saved_rows(evaluator))
            if done != len(assigned_ids[evaluator]):
                raise Problem('Complete all images before downloading comparison results.', 409)
        parameters = [active]
        sql = "SELECT * FROM responses WHERE study_id=? AND stage='complete'"
        if evaluator != 'all':
            sql += ' AND evaluator=?'
            parameters.append(evaluator)
        sql += ' ORDER BY evaluator,image_id'
        records = []
        for row in db().execute(sql, parameters):
            if row['image_id'] not in assigned_ids[row['evaluator']]:
                continue
            answers = json.loads(row['answers'])
            ref = manifest['analysis'][row['image_id']]
            records.append({'evaluator': row['evaluator'], 'image_id': row['image_id'], 'source_key': ref['source_key'],
                            'region_id': assignments[row['evaluator']]['region_id'],
                            'region_name': assignments[row['evaluator']]['region_name'],
                            'image_number': assignments[row['evaluator']]['image_ids'].index(row['image_id']) + 1,
                            **{k: ref.get(k) for k in ('source_country', 'split', 'category', 'run_id', 'selection_id', 'description_sha256')},
                            'answers': answers, 'comparisons': comparison(answers, ref)})
        provenance = manifest['provenance'] if g.role == 'researcher' else [p for p in manifest['provenance'] if p.get('evaluator', evaluator) == evaluator]
        result = {'study_id': active, 'exported_at': utcnow(), 'provenance': provenance,
                  'metric_policy': 'Human-selected countries only. Non-CP, unsure, outside-patch and unassessed cases excluded. Undefined denominators including both-empty Jaccard are null; deliberate no-patch is an empty human set.',
                  'items': records}
        filename = 'celve-' + active + '-' + evaluator
        if request.args.get('format') == 'csv':
            out = io.StringIO(newline='')
            writer = csv.writer(out)
            writer.writerow(['study_id','evaluator','region_id','region_name','image_number','image_id','source_key','source_country','split','category','run_id','selection_id','description_sha256','country_mode','country','evidence_status','tf_evaluated','human_patch_ids','model_patch_ids','comparable','precision','recall','jaccard','both_empty','description_rating','rating_unsure','locked_at','submitted_at'])
            def cell(value):
                if value is None:
                    return ''
                value = str(value)
                return "'" + value if value.startswith(('=','+','-','@','\t','\r')) else value
            for record in records:
                a = record['answers']
                for c in record['comparisons'] or [{}]:
                    writer.writerow([cell(v) for v in [active,record['evaluator'],record['region_id'],record['region_name'],record['image_number'],record['image_id'],record['source_key'],record['source_country'],record['split'],record['category'],record['run_id'],record['selection_id'],record['description_sha256'],a['countryMode'],c.get('country'),c.get('evidence_status','unassessed'),c.get('tf_evaluated'),
                                     '|'.join(c['human_patch_ids']) if c.get('human_patch_ids') is not None else None,
                                     '|'.join(c['model_patch_ids']) if c.get('model_patch_ids') is not None else None,
                                     c.get('comparable',False),c.get('precision'),c.get('recall'),c.get('jaccard'),c.get('both_empty'),a.get('rating'),a.get('ratingUnsure'),a.get('lockedAt'),a.get('submittedAt')]])
            return Response('\ufeff'+out.getvalue(), content_type='text/csv; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="{filename}.csv"'})
        return Response(json.dumps(result, ensure_ascii=False, indent=2), content_type='application/json',
                        headers={'Content-Disposition': f'attachment; filename="{filename}.json"'})

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=Path(os.environ.get('CELVE_STATE_DIR', BASE / 'state')))
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--threads', type=int, default=8)
    parser.add_argument('--secure-cookies', action='store_true', help='Enable when accessed over HTTPS')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.threads < 1:
        parser.error('Invalid port or thread count.')
    try:
        app = create_app(args.state, args.secure_cookies)
    except (OSError, ValueError, KeyError) as exc:
        print('Could not load study:', exc)
        print('First run manage.py import-zip --zip /path/to/data.zip --limit 60, or manage.py prepare for existing files.')
        return 1
    from waitress import serve
    logging.basicConfig(level=logging.INFO)
    print(f'CELVE listening on {args.host}:{args.port}', flush=True)
    print(f'Access codes: {args.state.resolve() / "access_codes.txt"}', flush=True)
    print('Use the reachable server IP/domain in your browser; 0.0.0.0 is a bind address.', flush=True)
    serve(app, host=args.host, port=args.port, threads=args.threads, max_request_body_size=65536,
          expose_tracebacks=False, clear_untrusted_proxy_headers=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
