"""Ownership migration and scopes against a disposable synthetic local DB only."""
import os
import uuid

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
import pytest

from tenant_platform_schema_identity import migrate_identity_schema
from tenant_scoped_access_schema import RESOURCE_TABLES, migrate_scoped_access_schema
from tenant_scoped_access_store import read_access_profile, resource_scope_sql


def tid(number):
    return str(uuid.UUID(int=number))


@pytest.fixture
def db(monkeypatch):
    dsn = os.environ.get('BEINTASKBOT_TEST_POSTGRES_DSN')
    if not dsn:
        pytest.skip('Disposable localhost PostgreSQL required')
    if conninfo_to_dict(dsn).get('host') not in {'127.0.0.1', 'localhost', '::1'}:
        raise RuntimeError('Synthetic localhost database only')
    name = 'test_scoped_foundation_' + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL('CREATE DATABASE {}').format(psycopg.sql.Identifier(name)))
    factory = lambda: psycopg.connect(make_conninfo(dsn, dbname=name), row_factory=dict_row)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    with factory() as conn:
        with conn.cursor() as cur:
            migrate_identity_schema(cur)
            for table in RESOURCE_TABLES:
                # Minimal old schemas deliberately lack the new ownership
                # column; all old data is installed before first migration.
                cur.execute(f'''CREATE TABLE {table}(id UUID PRIMARY KEY,
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id),
                    responsible_id BIGINT, created_by BIGINT)''')
            for number, owners in [(1, [(11, True)]), (2, [(21, True), (22, True)]),
                                   (3, []), (4, [(41, False)]), (5, [(52, True)])]:
                tenant = tid(number)
                cur.execute('''INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id)
                    VALUES(%s,%s,'Synthetic','active',%s)''', (tenant, tenant, number * 10 + 1))
                for actor, active in owners:
                    cur.execute('''INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,active)
                        VALUES(%s,%s,'owner',%s)''', (tenant, actor, active))
                for actor in [number * 10 + 3, number * 10 + 4, number * 10 + 5]:
                    cur.execute('''INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,permissions)
                        VALUES(%s,%s,'manager','["tasks"]')''', (tenant, actor))
                for table in RESOURCE_TABLES:
                    cur.execute(f'''INSERT INTO {table}(id,tenant_id,responsible_id,created_by)
                        VALUES(%s,%s,%s,%s)''', (tid(100 + number), tenant, number * 10 + 3, number * 10 + 4))
    yield factory
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL('DROP DATABASE {} WITH (FORCE)').format(psycopg.sql.Identifier(name)))


def migrate(factory):
    with factory() as conn:
        with conn.cursor() as cur:
            migrate_scoped_access_schema(cur)


def test_backfill_requires_unique_active_matching_owner_and_preserves_assignee(db):
    migrate(db)
    with db() as conn:
        for table in RESOURCE_TABLES:
            rows = conn.execute(f'SELECT * FROM {table} ORDER BY tenant_id').fetchall()
            assert [row['access_responsible_id'] for row in rows] == [11, None, None, None, None]
            assert [row['responsible_id'] for row in rows] == [13, 23, 33, 43, 53]
            assert [row['created_by'] for row in rows] == [14, 24, 34, 44, 54]
        issues = conn.execute('SELECT * FROM saas_access_migration_issues').fetchall()
        assert len(issues) == len(RESOURCE_TABLES) * 4
        assert {row['reason'] for row in issues} == {'sole_active_company_owner_required'}
        assert len(conn.execute('SELECT * FROM saas_access_schema_migrations').fetchall()) == 4


def test_rerun_never_fills_new_system_null_documents_or_reinterprets_old_assignee(db):
    migrate(db)
    with db() as conn:
        for table in RESOURCE_TABLES:
            conn.execute(f'''INSERT INTO {table}(id,tenant_id,responsible_id,created_by)
                VALUES(%s,%s,13,NULL)''', (tid(200), tid(1)))
    migrate(db)
    with db() as conn:
        for table in RESOURCE_TABLES:
            row = conn.execute(f'SELECT * FROM {table} WHERE id=%s', (tid(200),)).fetchone()
            assert row['access_responsible_id'] is None
            assert row['created_by'] is None
            assert row['responsible_id'] == 13
        assert len(conn.execute('SELECT * FROM saas_access_migration_issues').fetchall()) == 16


def test_new_ownership_and_department_references_cannot_cross_tenant(db):
    migrate(db)
    with db() as conn:
        conn.execute('INSERT INTO saas_tenant_departments(tenant_id,id,name) VALUES(%s,%s,%s)', (tid(2), tid(600), 'Other tenant'))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db() as conn:
            conn.execute('UPDATE saas_tenant_members SET department_id=%s WHERE tenant_id=%s AND telegram_id=13', (tid(600), tid(1)))
    for table in RESOURCE_TABLES:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with db() as conn:
                conn.execute(f'UPDATE {table} SET access_responsible_id=21 WHERE tenant_id=%s', (tid(1),))


def test_migration_is_atomic_with_enclosing_transaction(db):
    with pytest.raises(RuntimeError):
        with db() as conn:
            with conn.cursor() as cur:
                migrate_scoped_access_schema(cur)
                raise RuntimeError('Synthetic rollback')
    with db() as conn:
        assert conn.execute("SELECT to_regclass('saas_access_schema_migrations') AS relation").fetchone()['relation'] is None
        assert not conn.execute("SELECT 1 FROM information_schema.columns WHERE table_name='saas_crm_native_tasks' AND column_name='access_responsible_id'").fetchone()
    migrate(db)


def setup_scope_data(db):
    migrate(db)
    with db() as conn:
        for n, name in [(600, 'Sales'), (601, 'Support')]:
            conn.execute('INSERT INTO saas_tenant_departments(tenant_id,id,name) VALUES(%s,%s,%s)', (tid(1), tid(n), name))
        conn.execute('UPDATE saas_tenant_members SET department_id=%s WHERE tenant_id=%s AND telegram_id IN (13,14)', (tid(600), tid(1)))
        conn.execute('UPDATE saas_tenant_members SET department_id=%s WHERE tenant_id=%s AND telegram_id=15', (tid(601), tid(1)))
        conn.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'members',%s::jsonb)",
                     (tid(1), '{"13":{"access_scopes":{"tasks":{"view":"department"}}}}'))
        for n, actor in [(201, 13), (202, 14), (203, 15)]:
            conn.execute('INSERT INTO saas_crm_native_tasks(id,tenant_id,access_responsible_id) VALUES(%s,%s,%s)', (tid(n), tid(1), actor))


def test_sql_reads_current_department_and_company_and_inactive_actor_is_denied(db):
    setup_scope_data(db)
    with db() as conn:
        with conn.cursor() as cur:
            profile = read_access_profile(cur, tid(1), 13, lock=True)
            assert profile['department_id'] == uuid.UUID(tid(600))
            sql, args = resource_scope_sql(profile, 'tasks', 'view')
            cur.execute('SELECT r.id FROM saas_crm_native_tasks r WHERE ' + sql + ' ORDER BY r.id', args)
            assert [str(row['id']) for row in cur.fetchall()] == [tid(201), tid(202)]
    with db() as conn:
        conn.execute('UPDATE saas_tenant_members SET department_id=%s WHERE tenant_id=%s AND telegram_id=14', (tid(601), tid(1)))
    with db() as conn:
        with conn.cursor() as cur:
            profile = read_access_profile(cur, tid(1), 13, lock=True)
            sql, args = resource_scope_sql(profile, 'tasks', 'view')
            cur.execute('SELECT r.id FROM saas_crm_native_tasks r WHERE ' + sql, args)
            assert [str(row['id']) for row in cur.fetchall()] == [tid(201)]
            assert read_access_profile(cur, tid(2), 13) is None
    with db() as conn:
        conn.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=13', (tid(1),))
    with db() as conn:
        with conn.cursor() as cur:
            assert read_access_profile(cur, tid(1), 13) is None


def test_locked_profile_holds_company_fence_against_department_writer(db):
    setup_scope_data(db)
    with db() as reader, db() as writer:
        with reader.cursor() as cur:
            read_access_profile(cur, tid(1), 13, lock=True)
        writer.execute("SET lock_timeout='100ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            writer.execute('SELECT id FROM saas_tenants WHERE id=%s FOR UPDATE', (tid(1),))


@pytest.fixture
def workflow_db(db, monkeypatch):
    import tenant_workflow_store as workflow
    with db() as conn:
        conn.execute('''CREATE TABLE saas_tenant_audit_events (
            id UUID PRIMARY KEY,tenant_id UUID,actor_telegram_id BIGINT,
            action TEXT,entity_type TEXT,payload JSONB)''')
    monkeypatch.setattr(workflow, '_connect', db)
    monkeypatch.setattr(workflow, '_ensure_schema', lambda conn: None)
    monkeypatch.setattr(workflow, '_member', lambda *args: {'role': 'owner', 'telegram_id': 11})
    monkeypatch.setattr(workflow, 'list_tenant_workflow_config', lambda **kwargs: {})
    return workflow


def test_workflow_cannot_add_change_clear_or_alias_bypass_matrix(db, workflow_db):
    import json
    old = {'13': {'access_scopes': {'tasks': {'view': 'self'}}, 'ai_enabled': True}}
    with db() as conn:
        conn.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'members',%s::jsonb)",
                     (tid(1), json.dumps(old)))
    for raw in ['members', ' members ']:
        for patch in [{'13': {'access_scopes': {'tasks': {'view': 'all'}}}},
                      {'13': {'access_scopes': {}}},
                      {'14': {'access_scopes': {'tasks': {'view': 'self'}}}}]:
            with pytest.raises(workflow_db.TenantPlatformError, match='requires_employee_editor'):
                workflow_db.save_tenant_workflow_config(tenant_id=tid(1), owner_id=11, policies={raw: patch})
            with db() as conn:
                assert conn.execute("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s AND policy_key='members'", (tid(1),)).fetchone()['value'] == old
                assert conn.execute('SELECT count(*) n FROM saas_tenant_audit_events').fetchone()['n'] == 0


def test_old_workflow_save_preserves_even_omitted_inactive_member_scopes(db, workflow_db):
    import json
    old = {'13': {'access_scopes': {'tasks': {'view': 'self'}}, 'ai_enabled': True},
           '14': {'access_scopes': {'tasks': {'delete': 'none'}}}}
    with db() as conn:
        conn.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'members',%s::jsonb)",
                     (tid(1), json.dumps(old)))
        conn.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=14', (tid(1),))
    workflow_db.save_tenant_workflow_config(tenant_id=tid(1), owner_id=11,
        policies={'members': {'13': {'ai_enabled': False, 'notifications': {'task_assigned': {'push': False}}}}})
    with db() as conn:
        saved = conn.execute("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s AND policy_key='members'", (tid(1),)).fetchone()['value']
        assert saved == {'13': {'ai_enabled': False, 'notifications': {'task_assigned': {'push': False}},
                               'access_scopes': old['13']['access_scopes']},
                         '14': {'access_scopes': old['14']['access_scopes']}}


def test_current_profile_preserves_legacy_onboarding_pipeline_fallback(db, monkeypatch):
    from tenant_policy import TenantPolicy
    monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED', raising=False)
    with db() as conn:
        conn.execute('''UPDATE saas_tenants SET onboarding=%s::jsonb,modules='{"deals":true}'
            WHERE id=%s''', ('{"pipeline":{"selected":[{"id":10,"stage_ids":[100,200]}]}}', tid(1)))
        conn.execute("UPDATE saas_tenant_members SET permissions='[\"deals\"]' WHERE tenant_id=%s AND telegram_id=13", (tid(1),))
        conn.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'members','{\"13\":{\"pipeline_ids\":[\"10\"]}}')", (tid(1),))
    with db() as conn:
        with conn.cursor() as cur:
            profile = read_access_profile(cur, tid(1), 13, lock=True)
            policy = TenantPolicy(profile)
            assert profile['onboarding'] == {'pipeline': {'selected': [{'id': 10, 'stage_ids': [100, 200]}]}}
            assert policy.action_scope('deals', 'view') is None
            assert policy.pipeline_scope() == [{'pipeline_id': 10, 'status_ids': [100, 200]}]
            assert policy.can_access_deal({'tenant_id': tid(1), 'pipeline_id': 10, 'status_id': 100})
            assert not policy.can_access_deal({'tenant_id': tid(2), 'pipeline_id': 10, 'status_id': 100})
