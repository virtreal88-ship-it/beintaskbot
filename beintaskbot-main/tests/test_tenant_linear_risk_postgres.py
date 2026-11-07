"""Real PostgreSQL transaction check for Linear risk re-evaluation and sync rollback."""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

import tenant_linear_observer


class LinearRiskPostgresIntegration(unittest.TestCase):
    def test_sync_recheck_sql_executes_and_failed_write_rolls_back(self):
        dsn = os.environ.get('TEST_POSTGRES_DSN')
        if not dsn:
            self.skipTest('Set TEST_POSTGRES_DSN to an isolated PostgreSQL database')
        schema = 'risk_test_' + uuid4().hex
        conn = psycopg.connect(dsn)
        try:
            conn.execute(f'CREATE SCHEMA {schema}')
            conn.execute(f'SET search_path TO {schema}')
            tenant, team, issue = [str(uuid4()) for _ in range(3)]
            source_scope = f'v1:{team}'
            config = {'team_id': team, 'done_state_ids': ['done'], 'workflow': {'sync_enabled': True},
                      'members': {}, 'news': {'enabled': False, 'projects': []}}
            conn.execute('''CREATE TABLE saas_linear_sync (
              tenant_id UUID PRIMARY KEY,config_version TEXT,cursor TEXT,since_at TIMESTAMPTZ,until_at TIMESTAMPTZ,
              due_at TIMESTAMPTZ,updated_at TIMESTAMPTZ,risk_scan_version TEXT)''')
            conn.execute('''CREATE TABLE saas_tenants (
              id UUID PRIMARY KEY,status TEXT,owner_telegram_id BIGINT,onboarding JSONB)''')
            conn.execute('''CREATE TABLE saas_tenant_integrations (
              tenant_id UUID,provider TEXT,status TEXT,metadata JSONB,updated_at TIMESTAMPTZ)''')
            conn.execute('''CREATE TABLE saas_linear_snapshots (
              tenant_id UUID,issue_id UUID,source_version TIMESTAMPTZ,state_id TEXT,snapshot JSONB,
              source_scope TEXT,observed_at TIMESTAMPTZ,risk_checked_at TIMESTAMPTZ,risk_snapshot JSONB,
              PRIMARY KEY(tenant_id,issue_id))''')
            # Deliberately incomplete: the actual finding INSERT must fail after the
            # re-evaluation SELECT, proving the sync transaction rolls back.
            conn.execute('CREATE TABLE saas_proactive_risk_findings (tenant_id UUID,source_kind TEXT,source_id TEXT)')
            conn.execute('''CREATE TABLE saas_proactive_risk_scope_checks (
              tenant_id UUID,source_kind TEXT,source_scope TEXT,checked_at TIMESTAMPTZ,
              PRIMARY KEY(tenant_id,source_kind,source_scope))''')
            conn.execute("INSERT INTO saas_tenants VALUES(%s,'active',7,%s::jsonb)",
                         (tenant, json.dumps({'timezone': 'UTC'})))
            metadata = {'settings': {'team_id': team, 'done_state_ids': ['done'],
                'workflow': {'enabled': True, 'sync_enabled': True}}}
            conn.execute("INSERT INTO saas_tenant_integrations VALUES(%s,'linear','connected',%s::jsonb,now())",
                         (tenant, json.dumps(metadata)))
            conn.execute("INSERT INTO saas_linear_sync VALUES(%s,'v1',NULL,now()-interval '1 day',NULL,now(),now(),'v3')",
                         (tenant,))
            due_date = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
            due_issue = {'title': 'Past due item', 'dueDate': due_date, 'updatedAt': '2026-10-20T10:00:00Z',
                         'assignee': {'id': 'worker'}, 'priority': 2,
                         'url': 'https://linear.app/acme/issue/X-1'}
            conn.execute('''INSERT INTO saas_linear_snapshots VALUES
              (%s,%s,'2026-10-20T10:00:00Z','open','{}'::jsonb,%s,'2026-10-20T10:00:00Z',NULL,%s::jsonb)''',
              (tenant, issue, source_scope, json.dumps(due_issue)))
            conn.commit()

            def connect_sync():
                return psycopg.connect(dsn, options=f'-c search_path={schema}', row_factory=dict_row)

            with patch.object(tenant_linear_observer, '_connect', side_effect=connect_sync), \
                 patch.object(tenant_linear_observer, 'ensure_observer_schema'), \
                 patch.object(tenant_linear_observer, 'ensure_risk_schema'), \
                 patch.object(tenant_linear_observer, 'context', return_value=({}, config, 'fake-key', 'v1')), \
                 patch.object(tenant_linear_observer, 'query', return_value={'issues': {'nodes': [],
                     'pageInfo': {'hasNextPage': False, 'endCursor': None}}}):
                with self.assertRaises(psycopg.errors.UndefinedColumn) as error:
                    tenant_linear_observer.sync_one()
            self.assertIn('rule_id', str(error.exception))

            verify = psycopg.connect(dsn, options=f'-c search_path={schema}')
            row = verify.execute('SELECT risk_checked_at FROM saas_linear_snapshots WHERE tenant_id=%s', (tenant,)).fetchone()
            self.assertIsNone(row[0])
            self.assertEqual(verify.execute('SELECT count(*) FROM saas_proactive_risk_findings').fetchone()[0], 0)
            due_at = verify.execute('SELECT due_at FROM saas_linear_sync WHERE tenant_id=%s', (tenant,)).fetchone()[0]
            self.assertGreater(due_at, datetime.now(timezone.utc))
            verify.commit()
            verify.close()
        finally:
            try:
                conn.execute('ROLLBACK')
                conn.execute('SET search_path TO public')
                conn.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
                conn.commit()
            except Exception:
                conn.rollback()
            conn.close()


if __name__ == '__main__':
    unittest.main()
