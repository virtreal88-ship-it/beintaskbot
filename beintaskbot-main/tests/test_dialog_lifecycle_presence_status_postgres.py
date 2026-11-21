"""Disposable status-only enrollment and routing isolation."""
from unittest.mock import patch

import psycopg
from psycopg.rows import dict_row
import pytest
import tenant_dialog_presence as presence
import tenant_platform
from tenant_linear_policy import TenantLinearError
from test_connect_channel_multiprocess import database

A = '00000000-0000-0000-0000-000000000101'
B = '00000000-0000-0000-0000-000000000102'


def identity(tenant, actor):
    return {'tenant_id': tenant, 'telegram_id': actor}


def test_disposable_status_only_existing_code(database, monkeypatch):
    db = database
    monkeypatch.setattr(tenant_platform, '_connect', lambda: psycopg.connect(db.dsn, row_factory=dict_row))
    monkeypatch.setenv('BEINSYSTEMS_OPERATOR_TENANT_ID', A)
    monkeypatch.delenv(presence.ROUTING_ENV, raising=False)
    for tenant in (A,B):
        db.execute("""INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id)
            VALUES(%s::uuid,%s,'Synthetic only','active',11)""", (tenant, tenant))
        db.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name)
            VALUES(%s::uuid,11,'owner','Owner'),(%s::uuid,22,'owner','Other synthetic member'),
                  (%s::uuid,33,'worker','Unprivileged synthetic member')""",(tenant,tenant,tenant))
    db.commit()
    assert presence.get_status(identity(A,11))['available'] is False
    assert db.execute("SELECT to_regclass('saas_dialog_employee_presence') IS NULL AS missing")[0]['missing']
    # Production needs this existing explicit helper only if the table is absent.
    with db.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout='2s'")
        cur.execute("SET LOCAL statement_timeout='8s'")
        presence.migrate(cur)
        presence.migrate(cur)
    db.commit()
    assert db.execute('SELECT count(*) AS n FROM saas_dialog_employee_presence')[0]['n']==0
    with patch.object(tenant_platform,'_connect',side_effect=AssertionError('foreign tenant must not open DB')):
        assert presence.get_status(identity(B,11))['available'] is False
        with pytest.raises(TenantLinearError) as denied:
            presence.set_status(identity(B,11),{'status':'online'})
        assert denied.value.status==403
    assert presence.get_status(identity(A,11))=={
        'available':True,'can_update':True,'status':None,'configured':False,'routing_enforced':False}
    with pytest.raises(TenantLinearError) as target_splice:
        presence.set_status(identity(A,11), {'status':'online','employee_id':22})
    assert target_splice.value.status==400
    for status in ('online','lunch','away','away'):
        receipt=presence.set_status(identity(A,11), {'status':status})
        assert receipt['status']==status and receipt['routing_enforced'] is False
    assert presence.get_status(identity(A,11))['status']=='away'
    assert presence.get_status(identity(A,22))['status'] is None
    with db.cursor() as cur:
        assert not presence.routing_enabled(cur,A)
        for actor in (11,22):
            assert presence.is_online(cur,A,actor,lock=True), 'legacy eligibility ignores pilot statuses'
    assert db.execute('SELECT tenant_id::text AS tenant_id,employee_id,status FROM saas_dialog_employee_presence')==[
        {'tenant_id':A,'employee_id':11,'status':'away'}]
    assert db.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE action='dialog_presence_changed'")[0]['n']==3
    db.execute("UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s::uuid AND telegram_id=11",(A,))
    db.commit()
    with pytest.raises(TenantLinearError) as inactive:
        presence.set_status(identity(A,11), {'status':'online'})
    assert inactive.value.status==403
