"""Disposable PostgreSQL coverage with actual fresh operator authorization."""
from datetime import datetime, timedelta, timezone
import os
import uuid

import psycopg
from psycopg.rows import dict_row
import pytest

import tenant_ai_agent_startup as startup
from test_startup_transaction_budget_postgres import (
    assert_session_released, database as startup_database, install_bootstrap,
)
from test_connect_channel_multiprocess import database as native_database
from test_tenant_support_inbox import database as wasm_database
from test_tenant_support_inbox import A, B, CA, CB, PROFILE
import tenant_personal_dialog_reminders as store
import tenant_platform
from tenant_linear_policy import TenantLinearError


def test_personal_isolation_due_and_fresh_source_access(request, monkeypatch):
    if os.environ.get('BEINTASKBOT_TEST_POSTGRES_DSN'):
        db = request.getfixturevalue('native_database')
        factory = lambda: psycopg.connect(db.dsn, row_factory=dict_row)
    else:
        db = request.getfixturevalue('wasm_database')
        from tenant_dialog_rollout import migrate
        with db.cursor() as cur:
            migrate(cur)
        db.commit()
        factory = lambda: db
    monkeypatch.setattr(tenant_platform, '_connect', factory)
    table = 'saas_personal_dialog_reminders'
    monkeypatch.delenv('TENANT_PERSONAL_DIALOG_REMINDERS_ENABLED', raising=False)
    with pytest.raises(TenantLinearError) as disabled:
        store.execute(PROFILE)
    assert disabled.value.status == 404
    assert db.execute("SELECT to_regclass(%s) IS NULL AS missing", (table,))[0]['missing']
    monkeypatch.setenv('TENANT_PERSONAL_DIALOG_REMINDERS_ENABLED', '1')
    for tenant, channel, lead in [(A, CA, 42), (B, CB, 43)]:
        db.execute("""INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id)
            VALUES(%s::uuid,%s,'Synthetic','active',11)""", (tenant, tenant))
        db.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name)
            VALUES(%s::uuid,11,'owner','One'),(%s::uuid,99,'owner','Two')""", (tenant, tenant))
        db.execute("""INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name)
            VALUES(%s::uuid,%s::uuid,'Synthetic')""", (tenant, channel))
        db.execute("""INSERT INTO saas_connect_conversations
            (tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at)
            VALUES(%s::uuid,%s::uuid,'client',%s,'Synthetic',now())""", (tenant, channel, lead))
    db.commit()
    with pytest.raises(TenantLinearError) as missing:
        store.execute(PROFILE)
    assert missing.value.status == 503
    with db.cursor() as cur:
        store.migrate(cur)
        store.migrate(cur)
    db.commit()
    ident = str(uuid.uuid4())
    command = dict(action='create', id=ident, provider='connect', channel_id=CA,
                   conversation_id='client', lead_id=42, note='Обратиться к инструкции',
                   due_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
    with pytest.raises(TenantLinearError):
        store.execute(PROFILE, {**command, 'channel_id':CB})
    with pytest.raises(TenantLinearError):
        store.execute(PROFILE, {**command, 'conversation_id':'spliced'})
    with pytest.raises(ValueError):
        store.execute(PROFILE, {**command, 'provider':'kommo'})
    with pytest.raises(ValueError):
        store.execute(PROFILE, {k:v for k,v in command.items() if k!='channel_id'})
    assert store.execute(PROFILE, command) == {'id':ident}
    assert store.execute(PROFILE, command) == {'id':ident}
    with pytest.raises(TenantLinearError) as conflict:
        store.execute(PROFILE, {**command, 'note':'Changed'})
    assert conflict.value.status == 409
    listing = store.execute(PROFILE)
    assert listing['total'] == 1 and listing['due_count'] == 0
    assert set(listing['reminders'][0]) == {'id','note','due_at','due','can_open'}
    assert listing['reminders'][0]['can_open'] is True
    independent = {k:command[k] for k in ('action','id','note','due_at')}
    independent['id'] = str(uuid.uuid4())
    assert store.execute(PROFILE, independent) == {'id':independent['id']}
    assert store.execute(PROFILE, independent) == {'id':independent['id']}
    assert next(r for r in store.execute(PROFILE)['reminders'] if r['id']==independent['id'])['can_open'] is False
    with pytest.raises(TenantLinearError) as no_dialog:
        store.execute(PROFILE, {'id':independent['id'], 'action':'open'})
    assert no_dialog.value.status == 404
    for other in [{'tenant_id':A,'telegram_id':99}, {'tenant_id':B,'telegram_id':11}]:
        assert store.execute(other)['reminders'] == []
        for action in ('complete','open'):
            with pytest.raises(TenantLinearError) as rejected:
                store.execute(other, {'id':ident, 'action':action})
            assert rejected.value.status == 404
    assert store.execute(PROFILE, {'id':ident,'action':'open'})['dialog']['channel_id'] == CA
    db.execute(f"UPDATE {table} SET due_at=now()-interval '1 hour' WHERE id=%s::uuid", (ident,))
    db.execute("""DELETE FROM saas_connect_conversations
                  WHERE tenant_id=%s::uuid AND conversation_id='client'""", (A,))
    db.commit()
    assert store.execute(PROFILE)['due_count'] == 1
    with pytest.raises(TenantLinearError):
        store.execute(PROFILE, {'id':ident,'action':'open'})
    assert store.execute(PROFILE, {'id':ident,'action':'complete'}) == {'id':ident}
    assert store.execute(PROFILE, {'id':ident,'action':'complete'}) == {'id':ident}
    assert store.execute(PROFILE, {'id':independent['id'],'action':'complete'}) == {'id':independent['id']}
    assert store.execute(PROFILE)['total'] == 0
    monkeypatch.setenv('BEINSYSTEMS_OPERATOR_TENANT_ID', A)
    monkeypatch.setenv(store.WORKSPACE_ENABLE, '1')
    monkeypatch.delenv(store.LEGACY_ENABLE)
    assert store.execute(PROFILE)['total'] == 0
    db.execute("ALTER INDEX saas_personal_dialog_reminders_pending RENAME TO wrong_test_index")
    db.commit()
    with pytest.raises(TenantLinearError) as wrong_index:
        store.execute(PROFILE)
    assert wrong_index.value.status == 503
    monkeypatch.setenv(store.LEGACY_ENABLE, '1')
    assert store.execute(PROFILE)['total'] == 0  # previous global mode unchanged
    db.execute("UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s::uuid AND telegram_id=11", (A,))
    db.commit()
    with pytest.raises(TenantLinearError) as inactive:
        store.execute(PROFILE)
    assert inactive.value.status == 403


@pytest.fixture
def reminder_boot(startup_database, monkeypatch):
    # Import normal application dependencies before the bootstrap helper puts
    # its isolated connection stub in sys.modules.
    import tenant_dialog_company_binding
    tenant=str(uuid.uuid4())
    monkeypatch.setenv('BEINSYSTEMS_OPERATOR_TENANT_ID', tenant)
    for key in (store.LEGACY_ENABLE,store.WORKSPACE_ENABLE,store.PROVISION_ON_STARTUP,
                'DIALOG_REPLY_TEMPLATES_ENABLED','DIALOG_REPLY_TEMPLATES_PROVISION_ON_STARTUP',
                'DIALOG_REPLY_TEMPLATES_OBSERVE_ON_STARTUP',
                'BEINSYSTEMS_DIALOG_COMPANY_BINDINGS_PROVISION_ON_STARTUP'):
        monkeypatch.delenv(key, raising=False)
    def base(conn):
        conn.execute("""CREATE TABLE IF NOT EXISTS saas_tenant_members(
            tenant_id uuid, telegram_id bigint,PRIMARY KEY(tenant_id,telegram_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS saas_tenant_departments(
            tenant_id uuid, id uuid, PRIMARY KEY(tenant_id,id))""")
        conn.execute("CREATE TABLE IF NOT EXISTS saas_tenants(id uuid PRIMARY KEY)")
        conn.execute("INSERT INTO saas_tenant_members VALUES (%s,11) ON CONFLICT DO NOTHING",(tenant,))
        conn.execute("INSERT INTO budget_webhook VALUES(41) ON CONFLICT DO NOTHING")
    install_bootstrap(monkeypatch,startup_database,base)
    return startup_database,tenant


def _objects(conn):
    return conn.execute("""SELECT to_regclass('saas_personal_dialog_reminders') IS NOT NULL,
        to_regclass('saas_personal_dialog_reminders_pending') IS NOT NULL""").fetchone()


def test_stock_startup_never_touches_reminders_even_if_ui_enabled(reminder_boot,monkeypatch):
    dsn,_=reminder_boot
    monkeypatch.setenv(store.WORKSPACE_ENABLE,'1')
    def unexpected(*_args):
        pytest.fail('stock startup must not probe, provision, or verify reminders')
    for name in ('provision_schema','verify_provisioned_schema','migrate','_ready'):
        monkeypatch.setattr(store,name,unexpected)
    startup.prepare_database()
    with psycopg.connect(dsn) as conn:
        assert _objects(conn)==(False,False)
    assert_session_released(dsn)


def test_opt_in_is_bounded_idempotent_and_does_not_enable_ui(reminder_boot,monkeypatch,caplog):
    dsn,tenant=reminder_boot
    monkeypatch.setenv(store.PROVISION_ON_STARTUP,'1')
    actual=store.migrate
    calls=[]
    def check(cur):
        with psycopg.connect(dsn) as observer:
            assert observer.execute('SELECT * FROM budget_webhook').fetchall()==[(41,)]
            assert not observer.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))',
                (startup._STARTUP_SCHEMA_LOCK,)).fetchone()[0]
        cur.execute("SELECT current_setting('transaction_timeout') AS budget,current_setting('lock_timeout') AS lock")
        assert cur.fetchone()=={'budget':'5s','lock':'2s'}
        calls.append(True)
        actual(cur)
    monkeypatch.setattr(store,'migrate',check)
    with caplog.at_level('INFO',logger=startup.__name__):
        startup.prepare_database()
    assert 'Personal reminders schema preflight: ready' in caplog.text
    ident=uuid.uuid4()
    with psycopg.connect(dsn) as conn:
        assert _objects(conn)==(True,True)
        assert conn.execute("SELECT count(*) FROM saas_personal_dialog_reminders").fetchone()==(0,)
        conn.execute("""INSERT INTO saas_personal_dialog_reminders(tenant_id,actor_id,id,note,due_at)
            VALUES (%s,11,%s,'synthetic existing note',now())""",(tenant,ident))
    startup.prepare_database()
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT id,note FROM saas_personal_dialog_reminders").fetchall()==[
            (ident,'synthetic existing note')]
    assert calls==[True], 'repeat startup verifies only and does not re-run DDL'
    assert not store.enabled(tenant)
    assert_session_released(dsn)


def test_incompatible_schema_is_neither_repaired_nor_replaced(reminder_boot,monkeypatch,caplog):
    dsn,tenant=reminder_boot
    with psycopg.connect(dsn) as conn:
        conn.execute("CREATE TABLE saas_personal_dialog_reminders(marker text)")
        conn.execute("INSERT INTO saas_personal_dialog_reminders VALUES ('unrelated synthetic value')")
    monkeypatch.setenv(store.PROVISION_ON_STARTUP,'1')
    startup.prepare_database()
    assert 'Personal reminders schema preflight: unverified' in caplog.text
    assert 'unrelated synthetic value' not in caplog.text
    with psycopg.connect(dsn) as conn:
        assert _objects(conn)==(True,False)
        assert conn.execute("SELECT marker FROM saas_personal_dialog_reminders").fetchall()==[
            ('unrelated synthetic value',)]
        assert conn.execute('SELECT * FROM budget_webhook').fetchall()==[(41,)]
    assert not store.enabled(tenant)
    assert_session_released(dsn)


def test_new_table_is_rolled_back_when_index_name_is_already_taken(reminder_boot,monkeypatch,caplog):
    dsn,_=reminder_boot
    with psycopg.connect(dsn) as conn:
        conn.execute("CREATE TABLE other_objects(id bigint)")
        conn.execute("CREATE INDEX saas_personal_dialog_reminders_pending ON other_objects(id)")
    monkeypatch.setenv(store.PROVISION_ON_STARTUP,'1')
    startup.prepare_database()
    assert 'Personal reminders schema preflight: unverified' in caplog.text
    with psycopg.connect(dsn) as conn:
        assert _objects(conn)==(False,True)
    assert_session_released(dsn)


def test_provision_requires_valid_workspace_before_reminder_ddl(reminder_boot,monkeypatch,caplog):
    dsn,_=reminder_boot
    monkeypatch.setenv(store.PROVISION_ON_STARTUP,'1')
    monkeypatch.setenv('BEINSYSTEMS_OPERATOR_TENANT_ID','invalid-uuid')
    def unexpected(*_args):
        pytest.fail('must not probe or provision the table')
    monkeypatch.setattr(store,'provision_schema',unexpected)
    startup.prepare_database()
    assert 'Personal reminders schema preflight: workspace_not_configured' in caplog.text
    with psycopg.connect(dsn) as conn:
        assert _objects(conn)==(False,False)
    assert_session_released(dsn)


def test_reminder_provision_and_template_observation_are_independent(reminder_boot,monkeypatch,caplog):
    dsn,_=reminder_boot
    monkeypatch.setenv('DIALOG_REPLY_TEMPLATES_OBSERVE_ON_STARTUP','true')
    monkeypatch.setenv(store.PROVISION_ON_STARTUP,'1')
    with caplog.at_level('INFO',logger=startup.__name__):
        startup.prepare_database()
    assert 'Reply template schema observation: absent' in caplog.text
    assert 'Personal reminders schema preflight: ready' in caplog.text
    with psycopg.connect(dsn) as conn:
        assert _objects(conn)==(True,True)
        assert conn.execute("SELECT to_regclass('saas_dialog_reply_templates') IS NULL").fetchone()==(True,)
    assert_session_released(dsn)


@pytest.mark.parametrize('malformed_template',[False,True])
def test_legacy_optional_hooks_and_new_reminders_are_independent(reminder_boot,monkeypatch,caplog,malformed_template):
    dsn,tenant=reminder_boot
    if malformed_template:
        with psycopg.connect(dsn) as conn:
            conn.execute("CREATE TABLE saas_dialog_reply_templates(marker text)")
            conn.execute("INSERT INTO saas_dialog_reply_templates VALUES ('synthetic private marker')")
    for flag,value in ((store.PROVISION_ON_STARTUP,'1'),
                       ('DIALOG_REPLY_TEMPLATES_PROVISION_ON_STARTUP','true'),
                       ('BEINSYSTEMS_DIALOG_COMPANY_BINDINGS_PROVISION_ON_STARTUP','true')):
        monkeypatch.setenv(flag,value)
    with caplog.at_level('INFO',logger=startup.__name__):
        startup.prepare_database()
        startup.prepare_database()
    expected='unverified' if malformed_template else 'ready'
    assert caplog.text.count('Reply template schema preflight: '+expected)==2
    assert caplog.text.count('Optional Kommo dialogue company schema ready')==2
    assert caplog.text.count('Personal reminders schema preflight: ready')==2
    assert 'synthetic private marker' not in caplog.text
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM saas_beinsystems_kommo_dialog_company_bindings").fetchone()==(0,)
        assert conn.execute("SELECT count(*) FROM saas_personal_dialog_reminders").fetchone()==(0,)
        if malformed_template:
            assert conn.execute("SELECT marker FROM saas_dialog_reply_templates").fetchall()==[('synthetic private marker',)]
            assert conn.execute("SELECT to_regclass('saas_dialog_reply_templates_scope_idx') IS NULL").fetchone()==(True,)
        else:
            assert conn.execute("SELECT count(*) FROM saas_dialog_reply_templates").fetchone()==(0,)
    from tenant_dialog_reply_templates import feature_ready
    assert not feature_ready({'tenant_id':tenant})
    assert not store.enabled(tenant)
    assert_session_released(dsn)
