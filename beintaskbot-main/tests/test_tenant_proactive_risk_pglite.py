"""Exact source-scope retention checks against isolated PostgreSQL/WASM (PGlite)."""
import json
from pathlib import Path
import re
import subprocess
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from tenant_proactive_risk_policy import linear_scope, crm_scope
import tenant_linear_observer as linear_observer
from tenant_proactive_risk_store import replace_source_findings
import tenant_linear_observer_cleanup as cleanup_module
import tenant_crm_notice_store as crm_notice_store

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
    def __init__(self, database, connection=None):
        self.database = database
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, params=None):
        if self.connection and not self.connection.in_transaction:
            self.database.execute('BEGIN')
            self.connection.in_transaction = True
        self.rows = self.database.query(sql, params)
        for row in self.rows:
            for key in ('source_version', 'observed_at', 'risk_checked_at', 'due_at', 'updated_at', 'since_at', 'until_at'):
                value = row.get(key)
                if isinstance(value, str):
                    try: row[key] = datetime.fromisoformat(value.replace('Z', '+00:00'))
                    except ValueError: pass

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class PGliteConnection:
    """Small transaction-aware DB-API adapter for full observer SQL paths."""
    def __init__(self, database):
        self.database = database
        self.in_transaction = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_args):
        self.rollback() if exc_type else self.commit()

    def cursor(self):
        return PGliteCursor(self.database, self)

    def commit(self):
        if self.in_transaction:
            self.database.execute('COMMIT')
            self.in_transaction = False

    def rollback(self):
        if self.in_transaction:
            self.database.execute('ROLLBACK')
            self.in_transaction = False


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
            database.execute('''CREATE TABLE saas_linear_snapshots (
              tenant_id TEXT,issue_id TEXT,source_version TIMESTAMPTZ,snapshot JSONB,state_id TEXT,
              source_scope TEXT,risk_snapshot JSONB)''')
            database.execute('''CREATE TABLE saas_proactive_risk_findings (
              tenant_id TEXT,source_kind TEXT,source_id TEXT,rule_id TEXT,source_scope TEXT,checked_at TIMESTAMPTZ)''')

            tenant = 'tenant-1'
            updated_at = datetime(2026, 11, 1, 12, 0, 0, 123456, tzinfo=timezone.utc)
            connected_at = datetime(2026, 10, 5, 8, 30, 0, 654321, tzinfo=timezone.utc)
            metadata = {'settings': {'team_id': 'team-current', 'done_state_ids': ['done'],
                'workflow': {'enabled': True, 'sync_enabled': True}}}
            database.execute('''INSERT INTO saas_tenant_integrations
              VALUES($1,'linear','connected',$2::jsonb,$3,NULL,NULL)''', (tenant,json.dumps(metadata),updated_at))
            database.execute('''INSERT INTO saas_tenant_integrations
              VALUES($1,'kommo','connected','{}'::jsonb,NULL,'crm-current',$2)''', (tenant,connected_at))

            other_tenant = 'tenant-2'
            other_updated_at = updated_at - timedelta(hours=2)
            other_metadata = {'settings': {'team_id': 'team-other', 'done_state_ids': ['done'],
                'workflow': {'enabled': True, 'sync_enabled': True}}}
            database.execute('''INSERT INTO saas_tenant_integrations
              VALUES($1,'linear','connected',$2::jsonb,$3,NULL,NULL)''',
              (other_tenant,json.dumps(other_metadata),other_updated_at))
            safe_tenant = 'tenant-3'
            safe_updated_at = updated_at - timedelta(hours=4)
            safe_metadata = {'settings': {'team_id': 'team-safe', 'done_state_ids': ['done'],
                'workflow': {'enabled': True, 'sync_enabled': True}}}
            database.execute('''INSERT INTO saas_tenant_integrations
              VALUES($1,'linear','connected',$2::jsonb,$3,NULL,NULL)''',
              (safe_tenant,json.dumps(safe_metadata),safe_updated_at))

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

            stale_open = datetime.now(timezone.utc) - timedelta(days=100)
            old_risk_snapshot = {'title': 'Stale task', 'dueDate': '2026-11-10'}
            legacy_full_snapshot = {**old_risk_snapshot,'id':'external-id','team':{'id':'team-current'},
                'state':{'id':'open'},'labels':{'nodes':[{'name':'private-label'}]}}
            database.execute('''INSERT INTO saas_linear_snapshots
              VALUES($1,'open-current',$2,$4::jsonb,'open',$3,$5::jsonb)''',
              (tenant,stale_open,current_linear,json.dumps(old_risk_snapshot),json.dumps(old_risk_snapshot)))
            database.execute('''INSERT INTO saas_linear_snapshots
              VALUES($1,'open-old-config',$2,$4::jsonb,'open',$3,$5::jsonb)''',
              (tenant,stale_open,old_linear_config,json.dumps(old_risk_snapshot),json.dumps(old_risk_snapshot)))
            database.execute('''INSERT INTO saas_linear_snapshots
              VALUES($1,'open-legacy-risk-snapshot',$2,$4::jsonb,'open',$3,'{}'::jsonb)''',
              (tenant,stale_open,current_linear,json.dumps(legacy_full_snapshot)))
            database.execute('''INSERT INTO saas_linear_snapshots
              VALUES($1,'done-current',$2,$4::jsonb,'done',$3,$5::jsonb)''',
              (tenant,stale_open,current_linear,json.dumps(old_risk_snapshot),json.dumps(old_risk_snapshot)))

            with database.cursor() as cursor:
                crm_notice_store.cleanup_expired_risk_findings(cursor)
            retained_crm = {row['source_id'] for row in database.query(
                "SELECT source_id FROM saas_proactive_risk_findings WHERE source_kind='crm_task'")}
            self.assertEqual(retained_crm, {'crm-current'})

            with patch.object(cleanup_module, '_connect', return_value=database), \
                 patch.object(cleanup_module, 'ensure_observer_schema'), \
                 patch.object(cleanup_module, 'ensure_risk_schema'):
                cleanup_module.cleanup()

            retained = {row['source_id'] for row in database.query(
                'SELECT source_id FROM saas_proactive_risk_findings')}
            self.assertEqual(retained, {'linear-current', 'crm-current'})
            retained_risk_snapshots = {row['issue_id']: row['risk_snapshot'] for row in database.query(
                'SELECT issue_id,risk_snapshot FROM saas_linear_snapshots')}
            self.assertEqual(retained_risk_snapshots['open-current'], old_risk_snapshot)
            self.assertEqual(retained_risk_snapshots['open-old-config'], {})
            self.assertEqual(retained_risk_snapshots['open-legacy-risk-snapshot'], old_risk_snapshot)
            self.assertEqual(retained_risk_snapshots['done-current'], {})
            eligible = {row['issue_id'] for row in database.query('''SELECT issue_id FROM saas_linear_snapshots
              WHERE source_scope=$1 AND risk_snapshot<>'{}'::jsonb
                AND state_id<>'done' ''', (current_linear,))}
            self.assertEqual(eligible, {'open-current', 'open-legacy-risk-snapshot'})

            other_scope = linear_scope(other_updated_at, 'team-other')
            safe_scope = linear_scope(safe_updated_at, 'team-safe')
            database.execute('''INSERT INTO saas_linear_snapshots VALUES
              ($1,'disconnect-after-first-pass',$2,$3::jsonb,'open',$4,$5::jsonb),
              ($6,'config-change-after-first-pass',$2,$3::jsonb,'open',$7,$5::jsonb),
              ($8,'other-tenant-control',$2,$3::jsonb,'open',$9,$5::jsonb)''',
              (tenant,stale_open,json.dumps(old_risk_snapshot),current_linear,json.dumps(old_risk_snapshot),
               other_tenant,other_scope,safe_tenant,safe_scope))
            with patch.object(cleanup_module, '_connect', return_value=database), \
                 patch.object(cleanup_module, 'ensure_observer_schema'), \
                 patch.object(cleanup_module, 'ensure_risk_schema'):
                cleanup_module.cleanup()
            database.execute("UPDATE saas_tenant_integrations SET status='disconnected' WHERE tenant_id=$1 AND provider='linear'", (tenant,))
            changed_metadata = {'settings': {'team_id': 'team-new', 'done_state_ids': ['done'],
                'workflow': {'enabled': True, 'sync_enabled': True}}}
            database.execute("UPDATE saas_tenant_integrations SET metadata=$1::jsonb WHERE tenant_id=$2 AND provider='linear'",
                (json.dumps(changed_metadata),other_tenant))
            with patch.object(cleanup_module, '_connect', return_value=database), \
                 patch.object(cleanup_module, 'ensure_observer_schema'), \
                 patch.object(cleanup_module, 'ensure_risk_schema'):
                cleanup_module.cleanup()
            second_pass = {row['issue_id']: row['risk_snapshot'] for row in database.query(
                "SELECT issue_id,risk_snapshot FROM saas_linear_snapshots WHERE issue_id IN "
                "('disconnect-after-first-pass','config-change-after-first-pass','other-tenant-control')")}
            self.assertEqual(second_pass['disconnect-after-first-pass'], {})
            self.assertEqual(second_pass['config-change-after-first-pass'], {})
            self.assertEqual(second_pass['other-tenant-control'], old_risk_snapshot)
        finally:
            database.close()

    def test_sync_runs_real_recheck_sql_and_rolls_back_failed_risk_write(self):
        if not PGLITE.exists():
            self.skipTest('Install @electric-sql/pglite in .support-test-deps to run isolated PostgreSQL checks')
        database = PGliteDatabase()
        try:
            tenant='11111111-1111-4111-8111-111111111111'
            team='22222222-2222-4222-8222-222222222222'
            issue='33333333-3333-4333-8333-333333333333'
            config={'team_id':team,'done_state_ids':['done'],'workflow':{'sync_enabled':True},
                    'members':{},'news':{'enabled':False,'projects':[]}}
            scope=linear_scope('v1',team)
            database.execute('''CREATE TABLE saas_linear_sync (
              tenant_id UUID PRIMARY KEY,config_version TEXT,cursor TEXT,since_at TIMESTAMPTZ,until_at TIMESTAMPTZ,
              due_at TIMESTAMPTZ,updated_at TIMESTAMPTZ,risk_scan_version TEXT)''')
            database.execute('''CREATE TABLE saas_tenants (
              id UUID PRIMARY KEY,status TEXT,owner_telegram_id BIGINT,onboarding JSONB)''')
            database.execute('''CREATE TABLE saas_tenant_integrations (
              tenant_id UUID,provider TEXT,status TEXT,metadata JSONB,updated_at TIMESTAMPTZ)''')
            database.execute('''CREATE TABLE saas_linear_snapshots (
              tenant_id UUID,issue_id UUID,source_version TIMESTAMPTZ,state_id TEXT,snapshot JSONB,
              source_scope TEXT,observed_at TIMESTAMPTZ,risk_checked_at TIMESTAMPTZ,risk_snapshot JSONB,
              PRIMARY KEY(tenant_id,issue_id))''')
            database.execute('''CREATE TABLE saas_proactive_risk_findings (
              tenant_id UUID,source_kind TEXT,source_id TEXT)''')
            database.execute('''CREATE TABLE saas_proactive_risk_scope_checks (
              tenant_id UUID,source_kind TEXT,source_scope TEXT,checked_at TIMESTAMPTZ,
              PRIMARY KEY(tenant_id,source_kind,source_scope))''')
            database.execute("INSERT INTO saas_tenants VALUES($1,'active',7,'{\"timezone\":\"UTC\"}'::jsonb)",(tenant,))
            metadata={'settings':{'team_id':team,'done_state_ids':['done'],
                'workflow':{'enabled':True,'sync_enabled':True}}}
            database.execute("INSERT INTO saas_tenant_integrations VALUES($1,'linear','connected',$2::jsonb,now())",
                (tenant,json.dumps(metadata)))
            database.execute("INSERT INTO saas_linear_sync VALUES($1,'v1',NULL,now()-interval '1 day',NULL,now(),now(),'v3')",
                (tenant,))
            due_issue={'title':'Past due item','dueDate':'2026-11-06','updatedAt':'2026-10-20T10:00:00Z',
                       'assignee':{'id':'worker'},'priority':2,'url':'https://linear.app/acme/issue/X-1'}
            database.execute('''INSERT INTO saas_linear_snapshots VALUES
              ($1,$2,'2026-10-20T10:00:00Z','open','{}'::jsonb,$3,'2026-10-20T10:00:00Z',NULL,$4::jsonb)''',
              (tenant,issue,scope,json.dumps(due_issue)))

            connection=PGliteConnection(database)
            with patch.object(linear_observer,'_connect',return_value=connection), \
                 patch.object(linear_observer,'ensure_observer_schema'), \
                 patch.object(linear_observer,'ensure_risk_schema'), \
                 patch.object(linear_observer,'context',return_value=({},config,'fake-key','v1')), \
                 patch.object(linear_observer,'query',return_value={'issues':{'nodes':[],
                    'pageInfo':{'hasNextPage':False,'endCursor':None}}}):
                with self.assertRaisesRegex(RuntimeError,'rule_id'):
                    linear_observer.sync_one()

            snapshot=database.query('SELECT risk_checked_at FROM saas_linear_snapshots WHERE tenant_id=$1',(tenant,))[0]
            self.assertIsNone(snapshot['risk_checked_at'])
            finding_count=database.query('SELECT count(*) AS n FROM saas_proactive_risk_findings')[0]['n']
            self.assertEqual(finding_count,0)
            due=database.query('SELECT due_at FROM saas_linear_sync WHERE tenant_id=$1',(tenant,))[0]['due_at']
            self.assertGreater(datetime.fromisoformat(due.replace('Z','+00:00')),datetime.now(timezone.utc))
            self.assertFalse(connection.in_transaction)
        finally:
            database.close()


if __name__ == '__main__':
    unittest.main()
