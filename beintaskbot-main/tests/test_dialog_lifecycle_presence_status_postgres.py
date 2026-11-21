"""Disposable status-only enrollment and routing isolation."""
import json
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
    monkeypatch.delenv(presence.STATUS_ENABLED_ENV, raising=False)
    for tenant in (A,B):
        db.execute("""INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id)
            VALUES(%s::uuid,%s,'Synthetic only','active',11)""", (tenant, tenant))
        db.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name)
            VALUES(%s::uuid,11,'owner','Owner'),(%s::uuid,22,'owner','Other synthetic member'),
                  (%s::uuid,33,'worker','Unprivileged synthetic member'),
                  (%s::uuid,44,'operator','Support operator')""",(tenant,tenant,tenant,tenant))
    db.commit()
    assert presence.get_status(identity(A,11))['available'] is False
    with db.cursor() as cur:
        assert not presence.is_effectively_online(cur,A,11,lock=True)
    db.commit()
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
        'available':True,'can_update':True,'status':'online','configured':False,'routing_enforced':False}
    # Reading default availability creates neither an individual row nor an audit event.
    with db.cursor() as cur:
        assert not presence.is_effectively_online(cur,A,11,lock=True), 'legacy table or status flag off is not work readiness'
        assert not presence.is_recorded_online(cur,A,11,lock=True)
    db.commit()
    assert db.execute('SELECT count(*) AS n FROM saas_dialog_employee_presence')[0]['n']==0
    assert db.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE action='dialog_presence_changed'")[0]['n']==0
    with pytest.raises(TenantLinearError) as target_splice:
        presence.set_status(identity(A,11), {'status':'online','employee_id':22})
    assert target_splice.value.status==400
    for status in ('online','lunch','away','away'):
        receipt=presence.set_status(identity(A,11), {'status':status})
        assert receipt['status']==status and receipt['routing_enforced'] is False
    assert presence.get_status(identity(A,11))['status']=='away'
    assert presence.get_status(identity(A,22))['status']=='online'
    # A manually chosen status does not age out; writes are explicit only.
    db.execute("UPDATE saas_dialog_employee_presence SET updated_at='2020-01-01' WHERE tenant_id=%s::uuid AND employee_id=11", (A,))
    db.commit()
    assert presence.get_status(identity(A,11))['status']=='away'
    monkeypatch.setenv(presence.STATUS_ENABLED_ENV, 'true')
    assert not presence.get_status(identity(B,11))['available'], 'four states must be ready before global status'
    with db.cursor() as cur:
        assert not presence.provision_status_schema(cur), 'verified legacy table expands in place'
        assert presence.status_schema_ready(cur,require_offline=True)
    db.commit()
    assert presence.get_status(identity(B,11))['available']
    assert presence.get_status(identity(B,11))['status']=='online'
    with db.cursor() as cur:
        assert not presence.is_recorded_online(cur,A,11)
        assert not presence.is_effectively_online(cur,A,11)
        assert not presence.is_recorded_online(cur,B,11)
        assert presence.is_effectively_online(cur,B,11)
        assert not presence.is_effectively_online(cur,B,33)
    db.commit()
    assert presence.set_status(identity(B,11), {'status':'online'})['status']=='online'
    assert presence.get_status(identity(A,11))['status']=='away'
    with db.cursor() as cur:
        assert not presence.is_recorded_online(cur,A,22,lock=True)
        assert presence.is_effectively_online(cur,A,22,lock=True)
        assert presence.is_recorded_online(cur,B,11,lock=True)
        assert presence.is_effectively_online(cur,B,11,lock=True)
        assert not presence.is_recorded_online(cur,B,33,lock=True)
        assert not presence.routing_enabled(cur,B)
    db.commit()
    monkeypatch.delenv(presence.STATUS_ENABLED_ENV)
    assert not presence.get_status(identity(B,11))['available']
    with db.cursor() as cur:
        assert not presence.is_recorded_online(cur,B,11)
        assert not presence.is_effectively_online(cur,B,11)
    db.commit()
    with db.cursor() as cur:
        assert not presence.routing_enabled(cur,A)
        for actor in (11,22):
            assert presence.is_online(cur,A,actor,lock=True), 'legacy eligibility ignores pilot statuses'
    assert db.execute('SELECT tenant_id::text AS tenant_id,employee_id,status FROM saas_dialog_employee_presence ORDER BY tenant_id,employee_id')==[
        {'tenant_id':A,'employee_id':11,'status':'away'},
        {'tenant_id':B,'employee_id':11,'status':'online'}]
    assert db.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE action='dialog_presence_changed'")[0]['n']==4
    db.execute("UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s::uuid AND telegram_id=11",(A,))
    db.commit()
    with pytest.raises(TenantLinearError) as inactive:
        presence.set_status(identity(A,11), {'status':'online'})
    assert inactive.value.status==403

    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    monkeypatch.setenv(presence.STATUS_ENABLED_ENV, 'true')
    settings={'44':{'access_scopes':{'dialogs':{'view':'self','update':'self'}}}}
    db.execute("""INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value)
        VALUES(%s::uuid,'members',%s::jsonb)""", (B,json.dumps(settings)))
    db.commit()
    assert presence.set_status(identity(B,44),{'status':'online'})['status']=='online'
    with db.cursor() as cur:
        assert presence.is_recorded_online(cur,B,44,lock=True)
    db.commit()
    settings['44']['access_scopes']['dialogs']['update']='none'
    db.execute("""UPDATE saas_tenant_workflow_policies SET value=%s::jsonb
        WHERE tenant_id=%s::uuid AND policy_key='members'""", (json.dumps(settings),B))
    db.commit()
    assert not presence.get_status(identity(B,44))['can_update']
    with db.cursor() as cur:
        assert not presence.is_recorded_online(cur,B,44,lock=True)
        assert presence.is_effectively_online(cur,B,44,lock=True), 'view-only employee retains availability'
    db.commit()
    with pytest.raises(TenantLinearError) as revoked:
        presence.set_status(identity(B,44),{'status':'online'})
    assert revoked.value.status==403
    settings['44']['access_scopes']['dialogs']['view']='none'
    db.execute("""UPDATE saas_tenant_workflow_policies SET value=%s::jsonb
        WHERE tenant_id=%s::uuid AND policy_key='members'""", (json.dumps(settings),B))
    db.commit()
    with db.cursor() as cur:
        assert not presence.is_effectively_online(cur,B,44,lock=True)
    db.commit()

    # An explicit persisted Online is not enough after current membership/ACL
    # is revoked, even though routing policy retains its historical fallback.
    monkeypatch.setenv(presence.STATUS_ENABLED_ENV, 'true')
    db.execute("UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s::uuid AND telegram_id=11",(B,))
    db.commit()
    with db.cursor() as cur:
        assert not presence.is_recorded_online(cur,B,11,lock=True)
        assert not presence.is_effectively_online(cur,B,11,lock=True)
        assert presence.is_online(cur,B,11,lock=True)
    db.commit()

    with db.cursor() as cur:
        cur.execute("ALTER TABLE saas_dialog_employee_presence ADD COLUMN unexpected text")
    db.commit()
    assert not presence.get_status(identity(A,22))['available']
    with db.cursor() as cur:
        assert not presence.is_effectively_online(cur,A,22,lock=True)
    db.commit()
    with pytest.raises(TenantLinearError) as mismatch:
        presence.set_status(identity(A,22),{'status':'online'})
    assert mismatch.value.status==409
