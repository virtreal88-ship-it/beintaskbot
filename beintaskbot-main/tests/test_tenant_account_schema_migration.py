"""PGlite regression for the additive employee-account identity migration."""
import json
import re
import subprocess
import uuid
from pathlib import Path

import pytest

pytest.importorskip('psycopg.errors')
import tenant_platform_schema_identity as schema

ROOT = Path(__file__).resolve().parents[1]


class Database:
    def __init__(self):
        helper = ROOT / 'tests' / 'support_pglite.cjs'
        self.process = subprocess.Popen(
            ['node', str(helper)], cwd=ROOT, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
        )

    def query(self, sql, params=()):
        counter = iter(range(1, 1000))
        sql = re.sub(r'%s', lambda _: '$' + str(next(counter)), sql)
        self.process.stdin.write(json.dumps({'sql': sql, 'params': params or []}) + '\n')
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(self.process.stderr.read())
        result = json.loads(line)
        if 'error' in result:
            raise RuntimeError(result['error'])
        return result['rows']

    def execute(self, sql, params=()):
        return self.query(sql, params)

    def cursor(self):
        return Cursor(self)

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)


class Cursor:
    def __init__(self, db):
        self.db = db
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def execute(self, sql, params=None):
        self.rows = self.db.execute(sql, params)

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


def test_identity_migration_is_additive_repeatable_and_preserves_legacy_members():
    db = Database()
    tenant_id = str(uuid.uuid4())
    actor_id = 12345
    account_id = str(uuid.uuid4())
    try:
        db.execute('CREATE TABLE saas_tenants(id UUID PRIMARY KEY,slug TEXT UNIQUE,name TEXT,status TEXT,owner_telegram_id BIGINT)')
        db.execute('''CREATE TABLE saas_tenant_members(
            tenant_id UUID NOT NULL REFERENCES saas_tenants(id), telegram_id BIGINT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '', role TEXT NOT NULL CHECK(role IN ('owner','manager','worker','master')),
            permissions JSONB NOT NULL DEFAULT '[]'::jsonb, active BOOLEAN NOT NULL DEFAULT TRUE,
            invited_by BIGINT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,telegram_id))''')
        db.execute('''CREATE TABLE saas_tenant_invites(
            id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id), token_hash TEXT UNIQUE,
            display_name TEXT NOT NULL DEFAULT '', role TEXT NOT NULL CHECK(role IN ('manager','worker','master')),
            permissions JSONB NOT NULL DEFAULT '[]'::jsonb, created_by BIGINT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL, accepted_by BIGINT NULL, accepted_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        db.execute('''CREATE TABLE saas_auth_users(
            id UUID PRIMARY KEY, actor_id BIGINT UNIQUE NOT NULL, email TEXT UNIQUE NOT NULL,
            phone TEXT NOT NULL DEFAULT '', display_name TEXT NOT NULL DEFAULT '', company_name TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL DEFAULT '', tenant_id UUID NULL, verified_at TIMESTAMPTZ NULL,
            disabled_at TIMESTAMPTZ NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        db.execute("INSERT INTO saas_tenants VALUES(%s,'legacy','Legacy','active',%s)", (tenant_id, actor_id))
        db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,display_name,role) VALUES(%s,%s,'Legacy operator','worker')", (tenant_id, actor_id))
        db.execute("INSERT INTO saas_auth_users(id,actor_id,email,display_name) VALUES(%s,%s,'legacy@example.test','Legacy operator')", (account_id, actor_id))

        for _ in range(2):
            with db.cursor() as cur:
                schema.migrate_identity_schema(cur)

        member = db.execute('SELECT display_name,role,active FROM saas_tenant_members WHERE tenant_id=%s AND telegram_id=%s', (tenant_id, actor_id))[0]
        account = db.execute('SELECT actor_id,email,display_name,status FROM saas_auth_users WHERE id=%s', (account_id,))[0]
        assert member == {'display_name': 'Legacy operator', 'role': 'worker', 'active': True}
        assert account['actor_id'] == actor_id and account['email'] == 'legacy@example.test'
        assert account['display_name'] == 'Legacy operator' and account['status'] == 'active'
        db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,display_name,role) VALUES(%s,99,'New operator','operator')", (tenant_id,))
        assert db.execute('SELECT telegram_id FROM saas_tenant_members WHERE tenant_id=%s AND role=%s', (tenant_id, 'operator')) == [{'telegram_id': 99}]
        assert db.execute("SELECT column_name FROM information_schema.columns WHERE table_name='saas_tenant_members' AND column_name='last_login_at'")
        assert db.execute("SELECT table_name FROM information_schema.tables WHERE table_name='saas_tenant_browser_sessions'")
    finally:
        db.close()
