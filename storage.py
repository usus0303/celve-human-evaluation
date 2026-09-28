"""SQLite storage and local access codes. No external service is used."""
from contextlib import closing
import hashlib
import os
import secrets
import sqlite3
from pathlib import Path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, role TEXT NOT NULL, code_hash TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, account TEXT NOT NULL, csrf TEXT NOT NULL, expires REAL NOT NULL);
CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires);
CREATE TABLE IF NOT EXISTS responses (
 study_id TEXT NOT NULL, evaluator TEXT NOT NULL, image_id TEXT NOT NULL,
 stage TEXT NOT NULL, answers TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(study_id,evaluator,image_id));
CREATE TABLE IF NOT EXISTS login_failures (ip TEXT NOT NULL, time REAL NOT NULL);
CREATE INDEX IF NOT EXISTS login_failure_lookup ON login_failures(ip,time);
'''


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def connect(state):
    db = sqlite3.connect(Path(state) / 'responses.sqlite3', timeout=20, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=20000')
    return db


def initialize(state):
    state = Path(state)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with closing(connect(state)) as db:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1):
            raise RuntimeError('Unsupported database version; preserve your existing state directory.')
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript(SCHEMA)
        db.execute('PRAGMA user_version=1')
        if db.execute('SELECT count(*) FROM accounts').fetchone()[0]:
            return
        target = state / 'access_codes.txt'
        codes = [('01', 'evaluator', 'E01-' + secrets.token_urlsafe(24)),
                 ('02', 'evaluator', 'E02-' + secrets.token_urlsafe(24)),
                 ('03', 'evaluator', 'E03-' + secrets.token_urlsafe(24)),
                 ('researcher', 'researcher', 'RESEARCH-' + secrets.token_urlsafe(24))]
        # Exclusive file creation prevents accidental replacement of existing codes.
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            db.execute('BEGIN IMMEDIATE')
            for account, role, code in codes:
                db.execute('INSERT INTO accounts VALUES (?,?,?)', (account, role, digest(code)))
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                out.write('CELVE access codes — give each evaluator only their own code.\n\n')
                out.write('\n'.join(f'{account}: {code}' for account, role, code in codes) + '\n')
                out.flush()
                os.fsync(out.fileno())
            db.commit()
        except BaseException:
            db.rollback()
            target.unlink(missing_ok=True)
            raise
    os.chmod(state / 'responses.sqlite3', 0o600)
