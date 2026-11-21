"""Disposable PostgreSQL coverage with actual fresh operator authorization."""
from datetime import datetime, timedelta, timezone
import os
import uuid

import psycopg
from psycopg.rows import dict_row
import pytest

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
    db.execute("UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s::uuid AND telegram_id=11", (A,))
    db.commit()
    with pytest.raises(TenantLinearError) as inactive:
        store.execute(PROFILE)
    assert inactive.value.status == 403
