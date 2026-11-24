"""Exact-history authorization for shared reminder creation, with no extra recipients."""
from datetime import datetime, timedelta, timezone
import json
import uuid

from psycopg.sql import Composable
import pytest

import tenant_dialog_presence as presence
import tenant_operator_dialogs as dialogs
import tenant_shared_dialog_reminders as reminders
from tenant_linear_policy import TenantLinearError
from tenant_policy import TenantPolicy
from test_dialog_lifecycle_pglite import command, episodes
from test_tenant_support_inbox import A, B, CA, CB, database, db


def profile(actor, tenant=A):
    return {'tenant_id':tenant,'telegram_id':actor}


def exact(**fields):
    return {'action':'schedule','id':str(uuid.uuid4()),'provider':'connect',
            'channel_id':CA,'conversation_id':'client','lead_id':41,'note':'Synthetic note',
            'due_at':(datetime.now(timezone.utc).replace(microsecond=0)+timedelta(hours=1)).isoformat(), **fields}


def error(status, function, *args, **kwargs):
    with pytest.raises(TenantLinearError) as result:
        function(*args, **kwargs)
    assert result.value.status==status


@pytest.fixture
def ready(episodes,monkeypatch):
    monkeypatch.setenv(presence.STATUS_ENABLED_ENV,'true')
    monkeypatch.setenv(presence.ROUTING_SOURCES_ENV,CA)
    monkeypatch.setenv(reminders.ENABLE,'1')
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    monkeypatch.setattr(dialogs,'_connect',lambda:episodes)
    from test_tenant_support_inbox import Cursor
    fetchone=Cursor.fetchone
    def native_due(cursor):
        row=fetchone(cursor)
        if row and isinstance(row.get("due_at"),str):
            row["due_at"]=reminders._time(row["due_at"])
        return row
    monkeypatch.setattr(Cursor,"fetchone",native_due)
    with episodes.cursor() as cur:
        execute=cur.execute
        cur.execute=lambda query,params=None:execute(query.as_string() if isinstance(query,Composable) else query,params)
        presence.provision_status_schema(cur)
        reminders.provision_schema(cur)
        cur.execute('TRUNCATE saas_shared_dialog_reminders')
    for actor,role in [(34,'manager'),(35,'worker'),(36,'worker'),(37,'worker'),(38,'operator')]:
        episodes.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions,active)
            VALUES(%s::uuid,%s,%s,'Synthetic member','["dialogs"]'::jsonb,%s)""",(A,actor,role,actor!=37))
    scopes={str(actor):{'access_scopes':{'dialogs':{'view':view,'update':'none'}}}
            for actor,view in [(33,'all'),(34,'all'),(35,'all'),(36,'self'),(37,'all'),(38,'all')]}
    # The assigned operator retains update only for lifecycle fixture actions.
    scopes['33']['access_scopes']['dialogs']['update']='all'
    episodes.execute("""INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value)
        VALUES(%s::uuid,'members',%s::jsonb) ON CONFLICT (tenant_id,policy_key)
        DO UPDATE SET value=EXCLUDED.value""",(A,json.dumps(scopes)))
    episodes.commit()
    return episodes


def read(actor, **kwargs):
    return dialogs.history(profile(actor),'connect',41,CA,'client',**kwargs)['dialog']


def test_creation_capability_is_not_a_receive_capability(monkeypatch):
    monkeypatch.setattr(reminders,'enabled',lambda tenant:tenant==A)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    base={**profile(34),'tenant_status':'active','active':True,'permissions':['dialogs']}
    for role in ('owner','manager','worker','operator'):
        policy=TenantPolicy({**base,'role':role})
        public=policy.public_capabilities()
        assert public['shared_dialog_reminder_creation'] is True
        assert ('shared_dialog_reminders' in public)==(role=='operator')
    assert not reminders.creation_capability(TenantPolicy({**base,'role':'worker','permissions':[]}))
    assert not reminders.creation_capability(TenantPolicy({**base,'role':'owner','active':False}))
    assert not reminders.creation_capability(TenantPolicy({**base,'role':'owner','tenant_id':B}))


def test_owner_closed_archive_can_create_without_bell_or_ack(ready):
    accepted=command(actor=33)['lifecycle']
    command('close',accepted['revision'],actor=33)
    before=ready.execute('SELECT id,state,operator_id,revision FROM saas_dialog_workflows')
    ready.commit()
    for actor in (11,34,35,38):
        page=read(actor)
        assert page['archive_read_only'] and not page['can_send']
        assert page['shared_reminder']=={'can_schedule':True}
    payload=exact()
    error(403,reminders.execute,profile(37),payload)  # inactive
    error(403,reminders.execute,profile(36),payload)  # view does not include this source
    error(404,reminders.execute,profile(22,B),payload)
    error(404,reminders.execute,profile(11),{**payload,'lead_id':42})
    assert reminders.execute(profile(11),payload)=={'id':payload['id']}
    assert reminders.execute(profile(11),payload)=={'id':payload['id']}
    error(409,reminders.execute,profile(34),{**payload,'id':str(uuid.uuid4())})
    assert ready.execute('SELECT id,state,operator_id,revision FROM saas_dialog_workflows')==before
    ready.execute("UPDATE saas_shared_dialog_reminders SET due_at=now()-interval '1 minute' WHERE id=%s::uuid",(payload['id'],))
    ready.commit()
    for actor in (11,34,35):
        error(404,reminders.execute,profile(actor))
        error(404,reminders.execute,profile(actor),{'action':'open','id':payload['id']})
        assert read(actor)['shared_reminder']=={'can_schedule':True}
    assert [item['id'] for item in reminders.execute(profile(38))['reminders']]==[payload['id']]
    assert reminders.execute(profile(38),{'action':'open','id':payload['id']})['note']==payload['note']
    assert reminders.execute(profile(33))['reminders']==[]


def test_passive_accepted_scope_matches_history_not_a_generic_update(ready):
    command(actor=33)
    payload=exact()
    error(404,reminders.execute,profile(11),payload)
    error(404,reminders.execute,profile(34),payload)
    error(404,reminders.execute,profile(34),{**payload,'history_view':'owner'})
    error(404,reminders.execute,profile(34),{**payload,'history_view':'search','search':'not present'})
    error(404,reminders.execute,profile(34),{**payload,'lead_id':42,'history_view':'search','search':'Client'})
    owner=read(11,owner_overview=True)
    manager=read(34,search_overview=True,search='Client')
    assert owner['owner_overview_read_only'] and not owner['can_send']
    assert manager['search_overview_read_only'] and not manager['can_send']
    assert owner['shared_reminder']=={'can_schedule':True,'schedule_view':'owner'}
    assert manager['shared_reminder']=={'can_schedule':True,'schedule_view':'search'}
    assert reminders.execute(profile(34),{**payload,'history_view':'search','search':'Client'})=={'id':payload['id']}
    assert [item['id'] for item in reminders.execute(profile(33))['reminders']]==[payload['id']]
    assert reminders.execute(profile(38))['reminders']==[]
    assert 'dialog' in reminders.execute(profile(33))['reminders'][0]
    ready.execute("""UPDATE saas_tenant_workflow_policies
       SET value=jsonb_set(value,'{34,access_scopes,dialogs,view}','"self"'::jsonb)
       WHERE tenant_id=%s::uuid AND policy_key='members'""",(A,))
    ready.commit()
    # A client can keep the earlier search form visible, but current scope is
    # rechecked before idempotency lookup or reservation against that route.
    error(403,reminders.execute,profile(34),{**payload,'history_view':'search','search':'Client'})


def test_unstarted_and_unconfigured_routes_do_not_offer_creation(ready):
    assert 'shared_reminder' not in read(11)
    error(404,reminders.execute,profile(11),exact())
    assert 'shared_reminder' not in dialogs.history(profile(11),'connect',41,CA,'client',support_case_only=True)['dialog']
    error(404,reminders.execute,profile(11),exact(channel_id=CB))
    command(actor=33)
    page=dialogs.history(profile(33),'connect',41)
    assert page['dialog'].get('shared_reminder',{}).get('can_schedule') is not True  # no explicit source/conversation
    ready.execute("UPDATE saas_connect_channels SET active=FALSE WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid",(A,CA))
    ready.commit()
    assert read(33).get('shared_reminder',{}).get('can_schedule') is not True
    error(404,reminders.execute,profile(33),exact())
