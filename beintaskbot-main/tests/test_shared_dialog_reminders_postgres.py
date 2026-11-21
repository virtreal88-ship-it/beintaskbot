"""Actual PostgreSQL audience and acknowledgement boundaries for shared reminders."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import uuid

import psycopg
from psycopg.rows import dict_row
import pytest

import tenant_ai_agent_startup as startup
import tenant_dialog_lifecycle as lifecycle
import tenant_dialog_presence as presence
import tenant_operator_dialogs as dialogs
import tenant_platform
import tenant_shared_dialog_reminders as team
from tenant_linear_policy import TenantLinearError
from test_connect_channel_multiprocess import database
from test_dialog_lifecycle_pglite import command, episodes
from test_tenant_support_inbox import A, B, CA, db


def profile(actor, tenant=A):
    return {'tenant_id':tenant,'telegram_id':actor}


def rejected(actor, data=None, status=404):
    with pytest.raises(TenantLinearError) as error:
        team.execute(profile(actor),data)
    assert error.value.status==status


def test_shared_reminder_audience_and_first_open(episodes,monkeypatch):
    db=episodes
    monkeypatch.setenv(presence.STATUS_ENABLED_ENV,'true')
    monkeypatch.setenv(presence.ROUTING_SOURCES_ENV,CA)
    monkeypatch.setenv(team.ENABLE,'1')
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    monkeypatch.setattr(dialogs,'_connect',lambda:db)
    with db.cursor() as cur:
        presence.provision_status_schema(cur)
    db.commit()
    with psycopg.connect(db.dsn,row_factory=dict_row) as conn:
        startup._provision_shared_reminders(conn)
        startup._provision_shared_reminders(conn)
        assert team.schema_ready(conn.cursor())
    for actor in (34,35,36,37):
        db.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions)
            VALUES(%s::uuid,%s,'operator','Synthetic operator','["dialogs"]'::jsonb)""",(A,actor))
    scopes={'36':{'access_scopes':{'dialogs':{'view':'self','update':'self'}}},
            '37':{'access_scopes':{'dialogs':{'view':'all','update':'none'}}}}
    db.execute("""INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value)
        VALUES(%s::uuid,'members',%s::jsonb) ON CONFLICT (tenant_id,policy_key)
        DO UPDATE SET value=EXCLUDED.value""",(A,json.dumps(scopes)))
    personal=str(uuid.uuid4())
    db.execute("""INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name)
        VALUES(%s::uuid,%s::uuid,'Personal source')""",(A,personal))
    db.commit()
    accepted=command(actor=33)['lifecycle']
    presence.set_status(profile(35),{'status':'offline'})
    payload={'action':'schedule','id':str(uuid.uuid4()),
             'provider':'connect','channel_id':CA,'conversation_id':'client','lead_id':41,
             'note':'Only opened reminder contains this note',
             'due_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}
    opened={'action':'open','id':payload['id']}
    rejected(33,{**payload,'channel_id':personal})
    rejected(34,payload)
    assert team.execute(profile(33),payload)=={'id':payload['id']}
    assert team.execute(profile(33),payload)=={'id':payload['id']}
    rejected(33,{**payload,'id':str(uuid.uuid4())},409)
    assert team.execute(profile(33))['reminders'][0]['due'] is False
    rejected(33,opened)

    db.execute("""UPDATE saas_shared_dialog_reminders SET due_at=now()-interval '1 minute'
        WHERE tenant_id=%s::uuid AND id=%s::uuid""",(A,payload['id']))
    db.commit()
    presence.set_status(profile(33),{'status':'away'})
    accepted_view=team.execute(profile(33))['reminders']
    assert accepted_view[0]['dialog']['channel_id']==CA
    assert 'note' not in accepted_view[0] and accepted_view[0]['due']
    assert team.execute(profile(34))['reminders']==[]
    rejected(34,opened)
    page=dialogs.history(profile(33),'connect',41,CA,'client')
    assert page['dialog']['shared_reminder']=={'id':payload['id'],'can_schedule':True}
    assert team.execute(profile(33))['reminders']  # History refresh is read-only.

    command('close',accepted['revision'],actor=33)
    assert team.execute(profile(33))['reminders']==[]
    for actor in (34,37):
        visible=team.execute(profile(actor))['reminders']
        assert [r['id'] for r in visible]==[payload['id']]
        assert 'dialog' not in visible[0] and 'note' not in visible[0]
        with pytest.raises(TenantLinearError) as history_denied:
            dialogs.history(profile(actor),'connect',41,CA,'client')
        assert history_denied.value.status==404
    for actor in (35,36):
        assert team.execute(profile(actor))['reminders']==[]
        rejected(actor,opened)
    with pytest.raises(TenantLinearError) as foreign:
        team.execute(profile(34,B),opened)
    assert foreign.value.status==404

    def claim(actor):
        try:
            return team.execute(profile(actor),opened)
        except TenantLinearError as error:
            return error.status

    monkeypatch.setattr(tenant_platform,'_connect',lambda:psycopg.connect(db.dsn,row_factory=dict_row))
    with ThreadPoolExecutor(max_workers=2) as workers:
        results=list(workers.map(claim,(34,37)))
    assert results.count(404)==1
    result=next(r for r in results if isinstance(r,dict))
    assert result['note']==payload['note'] and 'dialog' not in result
    assert team.execute(profile(34))['reminders']==[]
    assert team.execute(profile(37))['reminders']==[]
