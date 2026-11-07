"""Exact source-scope retention checks against isolated PostgreSQL/WASM (PGlite)."""
import json
from pathlib import Path
import re
import subprocess
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from tenant_proactive_risk_policy import linear_scope, crm_scope
import tenant_linear_observer_cleanup as cleanup_module

ROOT = Path(__file__).resolve().parents[1]
PGLITE = ROOT / '.support-test-deps/node_modules/@electric-sql/pglite'


class PGliteDatabase:
    def __init__(self):
        self.process = subprocess.Popen(
            ['node', str(ROOT / 'tests/support_pglite.cjs')], cwd=ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')

    def query(self, sql, params=()):
        counter = iter(range(1, 1000))
        sql = re.sub(r'%s', lambda _match: '$' + str(next(counter)), sql)
        self.process.stdin.write(json.dumps({'sql': sql, 'params': params or []},
            default=lambda value: value.isoformat() if hasattr(value, 'isoformat') else str(value)) + '\n')
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(self.process.stderr.read())
        result = json.loads(line)
        if 'error' in result:
            raise RuntimeError(result['error'])
        return result['rows']

    def execute(self, sql, params=None):
        return self.query(sql, params)

    def cursor(self):
        return PGliteCursor(self)

    def commit(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)


class PGliteCursor:
    def __init__(self, database):
        self.database = database

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, params=None):
        self.database.query(sql, params)


class ProactiveRiskRetentionPGlite(unittest.TestCase):
    def test_cleanup_keeps_only_exact_current_linear_and_crm_scopes(self):
        if not PGLITE.exists():
            self.skipTest('Install @electric-sql/pglite in .support-test-deps to run isolated PostgreSQL checks')
        database = PGliteDatabase()
        try:
            database.execute('''CREATE TABLE saas_tenant_integrations (
              tenant_id TEXT, provider TEXT, status TEXT, metadata JSONB,
              updated_at TIMESTAMPTZ, account_domain TEXT, connected_at TIMESTAMPTZ)''')
            database.execute('CREATE TABLE saas_linear_news (tenant_id TEXT,id TEXT,published_at TIMESTAMPTZ,created_at TIMESTAMPTZ)')
            database.execute('CREATE TABLE saas_linear_notice_events (tenant_id TEXT,id TEXT,created_at TIMESTAMPTZ)')
            database.execute('CREATE TABLE saas_linear_snapshots (tenant_id TEXT,issue_id TEXT,source_version TIMESTAMPTZ,snapshot JSONB)')
            database.execute('''CREATE TABLE saas_proactive_risk_findings (
              tenant_id TEXT,source_kind TEXT,source_id TEXT,rule_id TEXT,source_scope TEXT,checked_at TIMESTAMPTZ)''')

            tenant = 'tenant-1'
            updated_at = datetime(2026, 11, 1, 12, 0, 0, 123456, tzinfo=timezone.utc)
            connected_at = datetime(2026, 10, 5, 8, 30, 0, 654321, tzinfo=timezone.utc)
            metadata = {'settings': {'team_id': 'team-current', 'workflow': {'enabled': True, 'sync_enabled': True}}}
            database.execute('''INSERT INTO saas_tenant_integrations
              VALUES($1,'linear','connected',$2::jsonb,$3,NULL,NULL)''', (tenant,json.dumps(metadata),updated_at))
            database.execute('''INSERT INTO saas_tenant_integrations
              VALUES($1,'kommo','connected','{}'::jsonb,NULL,'crm-current',$2)''', (tenant,connected_at))

            old = datetime.now(timezone.utc) - timedelta(days=100)
            current_linear = linear_scope(updated_at, 'team-current')
            old_linear_config = linear_scope(updated_at - timedelta(days=1), 'team-current')
            old_linear_team = linear_scope(updated_at, 'team-old')
            current_crm = crm_scope('crm-current', connected_at)
            old_crm_connection = crm_scope('crm-current', connected_at - timedelta(days=1))
            scopes = [
                ('linear-current', 'linear', current_linear),
                ('linear-old-config', 'linear', old_linear_config),
                ('linear-old-team', 'linear', old_linear_team),
                ('crm-current', 'crm_task', current_crm),
                ('crm-old-connection', 'crm_task', old_crm_connection),
            ]
            for source_id, kind, scope in scopes:
                database.execute('INSERT INTO saas_proactive_risk_findings VALUES($1,$2,$3,$4,$5,$6)',
                                 (tenant,kind,source_id,'risk',scope,old))

            with patch.object(cleanup_module, '_connect', return_value=database), \
                 patch.object(cleanup_module, 'ensure_observer_schema'), \
                 patch.object(cleanup_module, 'ensure_risk_schema'):
                cleanup_module.cleanup()

            retained = {row['source_id'] for row in database.query(
                'SELECT source_id FROM saas_proactive_risk_findings')}
            self.assertEqual(retained, {'linear-current', 'crm-current'})
        finally:
            database.close()


if __name__ == '__main__':
    unittest.main()
