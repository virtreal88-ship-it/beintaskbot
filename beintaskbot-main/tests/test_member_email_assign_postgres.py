"""Managed-email assignment using disposable localhost PostgreSQL only."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
import pytest

import tenant_member_email_assign as assign
import tenant_email_auth as auth
from tenant_member_store import MemberCardError
from tenant_platform_schema_identity import migrate_identity_schema
from tenant_platform_schema_operations import migrate_operations_schema
from tenant_platform_schema_crm import migrate_crm_schema

A, B = str(uuid.UUID(int=9201)), str(uuid.UUID(int=9202))

@pytest.fixture
def fixture(monkeypatch):
    dsn = os.environ.get('BEINTASKBOT_TEST_POSTGRES_DSN')
    if not dsn:
        pytest.skip('Disposable localhost PostgreSQL required')
    if conninfo_to_dict(dsn).get('host') not in {'127.0.0.1', 'localhost', '::1'}:
        raise RuntimeError('Local synthetic database only')
    name = 'test_owner_email_' + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL('CREATE DATABASE {}').format(psycopg.sql.Identifier(name)))
    factory = lambda: psycopg.connect(make_conninfo(dsn, dbname=name), row_factory=dict_row)
    monkeypatch.setattr(assign, '_connect', factory)
    monkeypatch.setattr(auth, '_send_mail', lambda *a, **kw: pytest.fail('No mail allowed'))
    with factory() as conn:
        with conn.cursor() as cur:
            migrate_identity_schema(cur)
            migrate_operations_schema(cur)
            migrate_crm_schema(cur)
        for tenant, owner, actor in [(A, 11, 41), (B, 22, 42)]:
            conn.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s,%s,'Synthetic','active',%s)", (tenant, tenant, owner))
            for person, role in [(owner, 'owner'), (actor, 'worker')]:
                conn.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,display_name,role) VALUES(%s,%s,'Synthetic',%s)", (tenant, person, role))
            conn.execute("INSERT INTO saas_auth_users(id,actor_id,tenant_id,email,login,password_hash,verified_at) VALUES(%s,%s,%s,%s,%s,'unchanged-synthetic-hash',now())", (uuid.uuid4(), actor, tenant, f'user{actor}@isolated.example', f'employee{actor}'))
            conn.execute("INSERT INTO saas_tenant_audit_events(id,tenant_id,action,entity_type,entity_id) VALUES(%s,%s,'employee.provisioned','employee',%s)", (uuid.uuid4(), tenant, str(actor)))
            for person in [owner, actor]:
                conn.execute("INSERT INTO saas_tenant_browser_sessions(id,tenant_id,actor_id,expires_at) VALUES(%s,%s,%s,now()+interval '1 hour')", (uuid.uuid4(), tenant, person))
        uid = conn.execute('SELECT id FROM saas_auth_users WHERE actor_id=41').fetchone()['id']
        for purpose in ['confirm', 'link_email', 'reset']:
            conn.execute("INSERT INTO saas_auth_tokens(id,user_id,purpose,token_hash,expires_at) VALUES(%s,%s,%s,%s,now()+interval '1 hour')", (uuid.uuid4(), uid, purpose, purpose))
    yield factory
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL('DROP DATABASE {} WITH (FORCE)').format(psycopg.sql.Identifier(name)))


def query(f, sql, params=()):
    with f() as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else []


def command(f, **changes):
    version = query(f, 'SELECT updated_at FROM saas_tenant_members WHERE tenant_id=%s AND telegram_id=41', (A,))[0]['updated_at'].isoformat()
    return dict(tenant_id=A, owner_id=11, telegram_id=41, email=' NEW@isolated.example ',
        expected_version=version, source_session={'tenant_id': A, 'actor_id': 11, 'session_id': ''}, **changes)


def snapshot(f):
    return {table: query(f, f'SELECT to_jsonb(t) AS row FROM {table} t ORDER BY to_jsonb(t)::text')
        for table in ['saas_auth_users', 'saas_tenant_members', 'saas_tenant_browser_sessions', 'saas_auth_tokens', 'saas_tenant_audit_events']}


def test_assignment_revokes_only_target_and_preserves_credentials(fixture):
    f = fixture
    before = query(f, 'SELECT * FROM saas_auth_users WHERE actor_id=41')[0]
    foreign = query(f, 'SELECT * FROM saas_auth_users WHERE actor_id=42')
    cmd = command(f)
    result = assign.assign_member_email(**cmd)
    after = query(f, 'SELECT * FROM saas_auth_users WHERE actor_id=41')[0]
    assert result == {'telegram_id': 41, 'card_version': after_version(f), 'email_changed': True}
    assert result['card_version'] != cmd['expected_version']
    assert after['email'] == 'new@isolated.example'
    for key in ['password_hash', 'verified_at', 'force_password_change', 'login', 'tenant_id']:
        assert after[key] == before[key]
    assert after['legacy_sessions_revoked'] is True
    assert query(f, 'SELECT * FROM saas_auth_users WHERE actor_id=42') == foreign
    sessions = query(f, 'SELECT actor_id,revoked_at FROM saas_tenant_browser_sessions')
    assert all(bool(row['revoked_at']) == (row['actor_id'] == 41) for row in sessions)
    assert all(row['used_at'] for row in query(f, 'SELECT used_at FROM saas_auth_tokens'))
    audit = query(f, "SELECT payload FROM saas_tenant_audit_events WHERE action='employee.email.assigned'")
    assert audit == [{'payload': {}}]


def after_version(f):
    return query(f, 'SELECT updated_at FROM saas_tenant_members WHERE tenant_id=%s AND telegram_id=41', (A,))[0]['updated_at'].isoformat()


@pytest.mark.parametrize('email', ['', 'bad', None, 42, 'x' * 255 + '@example.org'])
def test_invalid_email_no_effect(fixture, email):
    cmd = command(fixture); cmd['email'] = email
    before = snapshot(fixture)
    with pytest.raises(MemberCardError): assign.assign_member_email(**cmd)
    assert snapshot(fixture) == before


@pytest.mark.parametrize('mutation', [
    "DELETE FROM saas_tenant_audit_events WHERE entity_id='41'",
    "UPDATE saas_tenant_members SET active=FALSE WHERE telegram_id=41",
    "UPDATE saas_auth_users SET disabled_at=now() WHERE actor_id=41",
    "UPDATE saas_auth_users SET tenant_id='" + B + "' WHERE actor_id=41",
    "UPDATE saas_tenants SET owner_telegram_id=41 WHERE id='" + B + "'",
    "INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,active) VALUES('" + B + "',41,'worker',FALSE)",
    "UPDATE saas_tenant_members SET role='admin' WHERE telegram_id=11",
    "UPDATE saas_tenants SET status='suspended' WHERE id='" + A + "'",
])
def test_scope_failures_are_atomic(fixture, mutation):
    query(fixture, mutation)
    before = snapshot(fixture)
    with pytest.raises(MemberCardError): assign.assign_member_email(**command(fixture))
    assert snapshot(fixture) == before


def test_duplicate_and_stale_receipt_rejected(fixture):
    f = fixture; cmd = command(f); cmd['email'] = 'USER42@isolated.example'
    before = snapshot(f)
    with pytest.raises(MemberCardError) as error: assign.assign_member_email(**cmd)
    assert error.value.fields == {'email': 'duplicate'}
    assert snapshot(f) == before
    cmd = command(f)
    assign.assign_member_email(**cmd)
    before = snapshot(f)
    with pytest.raises(MemberCardError) as error: assign.assign_member_email(**cmd)
    assert error.value.code == 'member_card_conflict'
    assert snapshot(f) == before


def test_concurrent_same_card_only_one_commit(fixture):
    f = fixture; first = command(f); second = {**first, 'email': 'second@isolated.example'}
    def run(cmd):
        try: return assign.assign_member_email(**cmd)['email_changed']
        except MemberCardError as exc: return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [first, second]))
    assert sorted(map(str, results)) == ['True', 'member_card_conflict']
    assert len(query(f, "SELECT 1 FROM saas_tenant_audit_events WHERE action='employee.email.assigned'")) == 1


def test_login_only_employee_accepts_email(fixture):
    query(fixture, 'UPDATE saas_auth_users SET email=NULL WHERE actor_id=41')
    assert assign.assign_member_email(**command(fixture))['email_changed'] is True


@pytest.mark.parametrize('source', [None, {}, {'tenant_id': B, 'actor_id': 11, 'session_id': ''},
    {'tenant_id': A, 'actor_id': 22, 'session_id': ''}])
def test_source_context_must_match_owner(fixture, source):
    cmd = command(fixture); cmd['source_session'] = source
    before = snapshot(fixture)
    with pytest.raises(MemberCardError): assign.assign_member_email(**cmd)
    assert snapshot(fixture) == before


def test_revoked_owner_session_is_rechecked(fixture):
    f = fixture
    sid = query(f, 'SELECT id FROM saas_tenant_browser_sessions WHERE actor_id=11')[0]['id']
    query(f, 'UPDATE saas_tenant_browser_sessions SET revoked_at=now() WHERE id=%s', (sid,))
    cmd = command(f); cmd['source_session']['session_id'] = str(sid)
    before = snapshot(f)
    with pytest.raises(auth.TenantPlatformError): assign.assign_member_email(**cmd)
    assert snapshot(f) == before
