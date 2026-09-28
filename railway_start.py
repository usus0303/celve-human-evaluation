#!/usr/bin/env python3
"""Start with persistent data; serve a setup page until data is supplied."""
import logging
import os
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, jsonify

from manage import activate
from region_loader import load_regions
from server import create_app


def build_app(state, config=None, selection_zip=None):
    state = Path(state).resolve()
    # Code deployments must never select a new study or reset saved answers.
    if not (state / 'active_study.json').exists():
        if selection_zip:
            if Path(selection_zip).is_file():
                from bundle_loader import load_bundle
                activate(load_bundle(SimpleNamespace(state=state, zip=Path(selection_zip), limit=0,
                    seed=42, max_mib=4096, name='CELVE Human Evaluation', require_selection=True)), state)
        elif config and Path(config).is_file():
            activate(load_regions(SimpleNamespace(state=state, config=Path(config), seed=42, max_mib=4096)), state)
    if (state / 'active_study.json').exists():
        return create_app(state, secure_cookies=True)
    app = Flask(__name__, static_folder=None)

    @app.get('/healthz')
    def health():
        return jsonify(status='awaiting_data')

    @app.get('/')
    def index():
        return ('<!doctype html><html lang="ko"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>CELVE</title><body style="font:18px system-ui;padding:10vh 8vw">'
                '<h1>CELVE</h1><p>평가를 준비하고 있습니다. 연구자의 안내를 기다려 주세요.</p>'
                '<p>The evaluation is being prepared. Please wait for the researcher’s invitation.</p>'
                '</body></html>', 503, {'Cache-Control': 'no-store', 'Retry-After': '300'})
    return app


def main():
    state = Path(os.environ.get('CELVE_STATE_DIR', '/data/state')).resolve()
    if os.environ.get('RAILWAY_PROJECT_ID'):
        mount = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH')
        if not mount or not state.is_relative_to(Path(mount).resolve()):
            raise ValueError('Attach a Railway Volume at /data before starting; CELVE_STATE_DIR must be inside it.')
    port = int(os.environ.get('PORT', '8000'))
    if not 1 <= port <= 65535:
        raise ValueError('Invalid PORT.')
    app = build_app(state, os.environ.get('CELVE_REGION_CONFIG', '/data/regions.json'),
                    os.environ.get('CELVE_SELECTION_ZIP'))
    logging.basicConfig(level=logging.INFO)
    print(f'CELVE listening on 0.0.0.0:{port}; state={state}', flush=True)
    from waitress import serve
    serve(app, host='0.0.0.0', port=port, threads=8, max_request_body_size=65536,
          expose_tracebacks=False, clear_untrusted_proxy_headers=True)


if __name__ == '__main__':
    main()
