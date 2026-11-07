"""Real PostgreSQL migrations, tenant isolation, locking and transaction checks."""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

import tenant_linear_observer
import tenant_linear_observer_schema as observer_schema
import tenant_proactive_risk_schema as risk_schema
from tenant_linear_observer import reevaluate_saved_snapshots
from tenant_proactive_risk_policy import linear_scope


class LinearRiskPostgresIntegration(unittest.TestCase):
    def test_migrations_sync_commit_rollback_tenant_isolation_and_skip_locked(self):
        dsn = os.environ.get('TEST_POSTGRES_DSN')
        if not dsn:
            self.skipTest('Set TEST_POSTGRES_DSN to an isolated PostgreSQL database')
        schema = 'risk_test_' + uuid4().hex
        conn = psycopg.connect(dsn)
        verify = blocker = None
        try:
            conn.execute(f'CREATE SCHEMA {schema}')
            conn.execute(f'SET search_path TO {schema}')
            tenant1, tenant2, team1, team2, issue1, issue2 = [str(uuid4()) for _ in range(6)]
            config1 = {'team_id': team1, 'done_state_ids': ['done'], 'workflow': {'sync_enabled': True},
                       'members': {}, 'news': {'enabled': False, 'projects': []}}
            config2 = {**config1, 'team_id': team2}
            scope1, scope2 = linear_scope('v1', team1), linear_scope('v1', team2)

            # The observer migration must upgrade this legacy snapshot table itself.
            conn.execute('CREATE TABLE saas_tenants (id UUID PRIMARY KEY)')
            conn.execute('''CREATE TABLE saas_tenant_members (
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id),telegram_id BIGINT NOT NULL,
              PRIMARY KEY(tenant_id,telegram_id))''')
            conn.execute('''CREATE TABLE saas_linear_snapshots (
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id),issue_id UUID NOT NULL,
              source_version TIMESTAMPTZ NOT NULL,state_id TEXT NOT NULL,snapshot JSONB NOT NULL,
              PRIMARY KEY(tenant_id,issue_id))''')
            # The application uses one tenant table for both relationships and runtime fields.
            conn.execute('ALTER TABLE saas_tenants ADD COLUMN status TEXT')
            conn.execute('ALTER TABLE saas_tenants ADD COLUMN owner_telegram_id BIGINT')
            conn.execute('ALTER TABLE saas_tenants ADD COLUMN onboarding JSONB')
            conn.execute('''CREATE TABLE saas_tenant_integrations (
              tenant_id UUID,provider TEXT,status TEXT,metadata JSONB,updated_at TIMESTAMPTZ)''')
            conn.execute('INSERT INTO saas_tenants VALUES(%s,\'active\',7,%s::jsonb)',
                         (tenant1, json.dumps({'timezone': 'UTC'})))
            conn.execute('INSERT INTO saas_tenants VALUES(%s,\'active\',8,%s::jsonb)',
                         (tenant2, json.dumps({'timezone': 'UTC'})))
            conn.execute('INSERT INTO saas_tenant_members VALUES(%s,7),(%s,8)', (tenant1, tenant2))

            # Run the repository's real additive migrations; only unrelated global
            # platform bootstrap is replaced with the minimal parent schema above.
            with patch.object(observer_schema, '_ensure_schema', lambda _conn: None), \
                 patch.object(risk_schema, '_ensure_schema', lambda _conn: None), \
                 patch.object(observer_schema, '_ready', False), patch.object(risk_schema, '_ready', False):
                observer_schema.ensure_observer_schema(conn)
                risk_schema.ensure_schema(conn)
                cols = {row[0] for row in conn.execute('''SELECT column_name FROM information_schema.columns
                  WHERE table_schema=%s AND table_name='saas_linear_snapshots' ''', (schema,)).fetchall()}
                self.assertTrue({'source_scope','observed_at','risk_checked_at','risk_snapshot'} <= cols)
                self.assertTrue(conn.execute("SELECT to_regclass(%s)", (f'{schema}.saas_proactive_risk_findings',)).fetchone()[0])
            conn.commit()

            for tenant, team in ((tenant1, team1), (tenant2, team2)):
                metadata = {'settings': {'team_id': team, 'done_state_ids': ['done'],
                    'workflow': {'enabled': True, 'sync_enabled': True}}}
                conn.execute("INSERT INTO saas_tenant_integrations VALUES(%s,'linear','connected',%s::jsonb,now())",
                             (tenant, json.dumps(metadata)))
            conn.execute("INSERT INTO saas_linear_sync VALUES(%s,'v1',NULL,now()-interval '1 day',NULL,now(),now(),'v3')",
                         (tenant1,))
            due_date = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
            issue = {'title': 'Past due item', 'dueDate': due_date, 'updatedAt': '2026-10-20T10:00:00Z',
                     'assignee': {'id': 'worker'}, 'priority': 2,
                     'url': 'https://linear.app/acme/issue/X-1'}
            source_version = datetime.now(timezone.utc) - timedelta(days=18)
            for tenant, issue_id, scope in ((tenant1, issue1, scope1), (tenant2, issue2, scope2)):
                conn.execute('''INSERT INTO saas_linear_snapshots
                  (tenant_id,issue_id,source_version,state_id,snapshot,source_scope,observed_at,risk_snapshot)
                  VALUES(%s,%s,%s,'open','{}'::jsonb,%s,%s,%s::jsonb)''',
                  (tenant, issue_id, source_version, scope, source_version, json.dumps(issue)))
            conn.execute('''INSERT INTO saas_proactive_risk_findings
              (tenant_id,source_kind,source_id,rule_id,source_scope,source_version,severity,confidence,
               source_title,source_url,facts,assessment,recommended_step,fingerprint,source_checked_at)
              VALUES(%s,'linear',%s,'prior_rule',%s,%s,'P2','low','Existing finding','',
                '[]'::jsonb,'Previously stored','Verify','prior-fingerprint',%s)''',
              (tenant1, issue1, scope1, source_version.isoformat(), source_version))
            conn.commit()

            def connect_sync():
                return psycopg.connect(dsn, options=f'-c search_path={schema}', row_factory=dict_row)

            with patch.object(tenant_linear_observer, '_connect', side_effect=connect_sync), \
                 patch.object(observer_schema, '_ensure_schema', lambda _conn: None), \
                 patch.object(risk_schema, '_ensure_schema', lambda _conn: None), \
                 patch.object(tenant_linear_observer, 'context', return_value=({}, config1, 'fake-key', 'v1')), \
                 patch.object(tenant_linear_observer, 'query', return_value={'issues': {'nodes': [],
                     'pageInfo': {'hasNextPage': False, 'endCursor': None}}}):
                result = tenant_linear_observer.sync_one()
            self.assertEqual(result['tenant_id'], tenant1)
            self.assertEqual(result['reevaluated'], 1)
            verify = psycopg.connect(dsn, options=f'-c search_path={schema}')
            committed = verify.execute('''SELECT risk_checked_at FROM saas_linear_snapshots
              WHERE tenant_id=%s AND issue_id=%s''', (tenant1, issue1)).fetchone()[0]
            self.assertIsNotNone(committed)
            findings = verify.execute('''SELECT rule_id,source_scope FROM saas_proactive_risk_findings
              WHERE tenant_id=%s AND source_kind='linear' AND source_id=%s''', (tenant1, issue1)).fetchall()
            self.assertEqual(findings, [('overdue_task', scope1)])
            self.assertEqual(verify.execute('''SELECT count(*) FROM saas_proactive_risk_findings
              WHERE tenant_id=%s AND source_id=%s''', (tenant2, issue2)).fetchone()[0], 0)
            checkpoint = verify.execute('''SELECT checked_at FROM saas_proactive_risk_scope_checks
              WHERE tenant_id=%s AND source_kind='linear' AND source_scope=%s''', (tenant1, scope1)).fetchone()
            self.assertIsNotNone(checkpoint)

            # Exercise actual SKIP LOCKED behavior: a held snapshot row must not
            # block another worker or allow it to duplicate the finding.
            verify.execute("UPDATE saas_linear_snapshots SET risk_checked_at=now()-interval '20 minutes' WHERE tenant_id=%s AND issue_id=%s",
                           (tenant1, issue1))
            verify.commit()
            blocker = psycopg.connect(dsn, options=f'-c search_path={schema}')
            blocker.execute('BEGIN')
            blocker.execute('SELECT issue_id FROM saas_linear_snapshots WHERE tenant_id=%s AND issue_id=%s FOR UPDATE',
                            (tenant1, issue1))
            concurrent = psycopg.connect(dsn, options=f'-c search_path={schema}', row_factory=dict_row)
            with concurrent.transaction():
                count = reevaluate_saved_snapshots(concurrent.cursor(), tenant1, config1, source_scope=scope1,
                    tenant_timezone='UTC', checked_at=datetime.now(timezone.utc))
            self.assertEqual(count, 0)
            blocker.rollback()
            blocker.close()

            eligible = verify.execute('''SELECT issue_id FROM saas_linear_snapshots
              WHERE tenant_id=%s AND source_scope=%s AND risk_snapshot<>'{}'::jsonb
                AND state_id<>ALL(ARRAY['done']::text[])
                AND (risk_checked_at IS NULL OR risk_checked_at<=now()-interval '15 minutes')''',
                (tenant1, scope1)).fetchall()
            self.assertEqual(str(eligible[0][0]), issue1)

            # An existing finding is deleted and reinserted during replacement.
            # A trigger failure must roll back that delete and preserve the checkpoint.
            verify.execute('''CREATE FUNCTION reject_risk_insert() RETURNS trigger LANGUAGE plpgsql AS $$
              BEGIN RAISE EXCEPTION 'intentional risk write failure'; END $$''')
            verify.execute('''CREATE TRIGGER reject_risk_insert BEFORE INSERT ON saas_proactive_risk_findings
              FOR EACH ROW EXECUTE FUNCTION reject_risk_insert()''')
            verify.execute("UPDATE saas_linear_snapshots SET risk_checked_at=now()-interval '20 minutes' WHERE tenant_id=%s AND issue_id=%s",
                           (tenant1, issue1))
            verify.commit()
            before_rollback = verify.execute('''SELECT checked_at FROM saas_proactive_risk_scope_checks
              WHERE tenant_id=%s AND source_kind='linear' AND source_scope=%s''', (tenant1, scope1)).fetchone()[0]
            rollback_conn = psycopg.connect(dsn, options=f'-c search_path={schema}', row_factory=dict_row)
            with self.assertRaisesRegex(psycopg.errors.RaiseException, 'intentional risk write failure'):
                with rollback_conn.transaction():
                    reevaluate_saved_snapshots(rollback_conn.cursor(), tenant1, config1, source_scope=scope1,
                        tenant_timezone='UTC', checked_at=datetime.now(timezone.utc))
            rollback_conn.close()
            prior = verify.execute('''SELECT rule_id FROM saas_proactive_risk_findings
              WHERE tenant_id=%s AND source_kind='linear' AND source_id=%s''', (tenant1, issue1)).fetchall()
            self.assertEqual(prior, [('overdue_task',)])
            rolled_back_risk_check = verify.execute('SELECT risk_checked_at FROM saas_linear_snapshots WHERE tenant_id=%s AND issue_id=%s',
                (tenant1, issue1)).fetchone()[0]
            self.assertLess(rolled_back_risk_check, datetime.now(timezone.utc)-timedelta(minutes=15))
            after_rollback = verify.execute('''SELECT checked_at FROM saas_proactive_risk_scope_checks
              WHERE tenant_id=%s AND source_kind='linear' AND source_scope=%s''', (tenant1, scope1)).fetchone()[0]
            self.assertEqual(after_rollback, before_rollback)
            verify.commit()
            concurrent.close()
        finally:
            for candidate in (blocker, verify):
                if candidate and not candidate.closed:
                    candidate.close()
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
