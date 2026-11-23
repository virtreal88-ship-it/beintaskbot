"""Availability routing through the existing queue on disposable PostgreSQL."""
from datetime import datetime, timedelta, timezone
import uuid

import pytest

import tenant_dialog_lifecycle as lifecycle
import tenant_dialog_presence as presence
from tenant_linear_policy import TenantLinearError
from test_connect_channel_multiprocess import database
from test_dialog_lifecycle_pglite import command, episodes, payload, state
from test_tenant_support_inbox import A, B, CA, db
from tenant_policy import TenantPolicy


def test_four_state_routing_keeps_an_open_dialogue_until_the_next_inbound(episodes, monkeypatch):
    db = episodes
    monkeypatch.setenv(presence.STATUS_ENABLED_ENV, 'true')
    monkeypatch.setenv(presence.ROUTING_SOURCES_ENV, CA)
    personal=str(uuid.uuid4())
    with db.cursor() as cur:
        presence.provision_status_schema(cur)
    db.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions)
        VALUES(%s::uuid,34,'operator','Available operator','["dialogs"]'::jsonb)""", (A,))
    db.execute("""INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name)
        VALUES(%s::uuid,%s::uuid,'Personal')""", (A,personal))
    db.execute("""INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id)
        VALUES(%s::uuid,%s::uuid,'client',89)""", (A,personal))
    db.commit()
    with db.cursor() as cur:
        assert presence.is_online(cur,A,34,source_channel=CA)  # No saved choice defaults to Online.
        assert not presence.is_online(cur,A,11)  # Owner is not an operator.
        assert presence.is_online(cur,A,11,source_channel=personal)
        assert not presence.routing_enabled(cur,A,personal)
        allowed = lifecycle.visibility_sql(cur,TenantPolicy({'tenant_id':A,'telegram_id':11,'role':'owner'}),
                                           "'connect'","c.connect_channel_id::text","c.conversation_id")
        cur.execute(f"""SELECT connect_channel_id::text AS source FROM saas_connect_conversations c
            WHERE tenant_id=%s::uuid AND conversation_id='client' AND {allowed}""", (A,))
        assert {row['source'] for row in cur.fetchall()} == {personal,CA}
        lifecycle.require_access(cur,A,11,'connect',personal,'client',owner=True)
        lifecycle.require_access(cur,A,11,'connect',CA,'client',owner=True)
        info={'provider':'connect','lead_id':89,'channel_id':personal,'conversation_id':'client','can_send':True}
        assert lifecycle.decorate(cur,A,11,info)['lifecycle']['can_accept']
    db.commit()
    personal_route={'channel_id':personal,'lead_id':89}
    owner_status={'tenant_id':A,'telegram_id':11}
    presence.set_status(owner_status,{'status':'away'})
    assert not presence.get_status(owner_status)['routing_enforced']
    personal_episode=lifecycle.mutate(owner_status,{**payload(),**personal_route})['lifecycle']
    accepted = command(actor=33)['lifecycle']
    presence.set_status({'tenant_id':A,'telegram_id':34},{'status':'lunch'})
    with pytest.raises(TenantLinearError) as unavailable:
        command('transfer',accepted['revision'],actor=33,target_employee_id=34)
    assert unavailable.value.status == 403
    presence.set_status({'tenant_id':A,'telegram_id':34},{'status':'online'})
    presence.set_status({'tenant_id':A,'telegram_id':33},{'status':'offline'})
    assert state(db)['operator_id'] == 33
    with db.cursor() as cur:
        lifecycle.require_access(cur,A,33,'connect',CA,'client')
        lifecycle.note_inbound(cur,A,CA,'client',str(uuid.uuid4()),datetime.now(timezone.utc)-timedelta(hours=1))
    db.commit()
    assert state(db)['operator_id'] == 33  # Delayed event is not a fresh handoff.
    with db.cursor() as cur:
        fresh=datetime.now(timezone.utc)+timedelta(seconds=3)
        lifecycle.note_inbound(cur,A,CA,'client',str(uuid.uuid4()),fresh)
        lifecycle.note_inbound(cur,A,personal,'client',str(uuid.uuid4()),fresh)
    db.commit()
    personal_state=state(db,personal)
    assert personal_state['state']=='accepted' and personal_state['operator_id']==11
    assert str(personal_state['id'])==personal_episode['id']
    presence.set_status({'tenant_id':A,'telegram_id':34},{'status':'away'})
    transfer={**payload('transfer',personal_state['revision'],target_employee_id=34),**personal_route}
    assert lifecycle.mutate(owner_status,transfer)['lifecycle']['operator_id']==34
    presence.set_status({'tenant_id':A,'telegram_id':34},{'status':'online'})
    waiting = state(db)
    assert waiting['state'] == 'waiting' and waiting['operator_id'] is None
    with db.cursor() as cur, pytest.raises(TenantLinearError) as invisible:
        lifecycle.require_access(cur,A,33,'connect',CA,'client')
    assert invisible.value.status == 404
    db.commit()
    with pytest.raises(TenantLinearError) as offline:
        command(actor=33,revision=waiting['revision'])
    assert offline.value.status == 403
    assert command(actor=34,revision=waiting['revision'])['lifecycle']['operator_id'] == 34
    old = db.execute("SELECT closed_at FROM saas_dialog_episodes WHERE tenant_id=%s::uuid AND id=%s::uuid",
                     (A, accepted['id']))
    assert old[0]['closed_at'] is not None


def test_four_state_routing_requires_exact_workspace_and_valid_source_ids(monkeypatch):
    monkeypatch.setenv('BEINSYSTEMS_OPERATOR_TENANT_ID', A)
    monkeypatch.setenv(presence.ROUTING_ENV, 'true')
    monkeypatch.setenv(presence.STATUS_ENABLED_ENV, 'true')
    unknown=str(uuid.uuid4())
    for value in ('',f'{CA},invalid'):
        monkeypatch.setenv(presence.ROUTING_SOURCES_ENV,value)
        assert not presence.routing_enabled(None,A,CA)
    monkeypatch.setenv(presence.ROUTING_SOURCES_ENV,f' {CA} , {unknown}')
    assert presence.work_status_routing_sources(A)=={CA,unknown}
    assert not presence.routing_enabled(None,B,CA)
    monkeypatch.delenv(presence.STATUS_ENABLED_ENV)
    assert not presence.routing_enabled(None,A,CA)
    assert not presence.routing_enabled(None,A,unknown)


def test_sidebar_transfer_records_real_accepted_episode_on_unrouted_source(episodes, monkeypatch):
    monkeypatch.delenv(presence.ROUTING_ENV,raising=False)
    accepted=command()['lifecycle']
    with pytest.raises(TenantLinearError) as override:
        command('transfer',accepted['revision'],target_employee_id=33,online_only=True,episode_id=str(uuid.uuid4()))
    assert override.value.status==400
    result=command('transfer',accepted['revision'],target_employee_id=33,online_only=True)['lifecycle']
    assert result['operator_id']==33 and result['id']==accepted['id']
    episode=episodes.execute('SELECT accepted_by,operator_id FROM saas_dialog_episodes WHERE tenant_id=%s::uuid AND id=%s::uuid',
                            (A,accepted['id']))[0]
    assert episode=={'accepted_by':11,'operator_id':33}
    audit=episodes.execute("SELECT entity_id FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid AND action='dialog_transfer' AND entity_id=%s",
                           (A,accepted['id']))
    assert len(audit)==1
