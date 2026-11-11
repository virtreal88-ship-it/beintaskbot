"""Independent report-read scopes over isolated SQL, never providers or production."""
import json
import uuid
from unittest.mock import patch

import pytest
from test_tenant_support_inbox import database, db, A, B, CA, CB, AGENT, PROFILE, seed_job
from test_tenant_night_report import night_db, ticket, message, event, route, audit, NOW, CC
from tenant_linear_policy import TenantLinearError
from tenant_member_access import access_snapshot
from tenant_scoped_access_store import read_access_profile
import tenant_night_report_store as store

D1,D2,C3,C4 = [str(uuid.UUID(int=n)) for n in (1501,1502,1603,1604)]
PERSON={'tenant_id':A,'telegram_id':31,'role':'owner','department_id':D2,
        'permissions':['night_report','dialogs','support','ai_agents']}


def grants(db,scope='self',*,legacy=False):
    scopes={'dialogs':{'view':'none','update':'none'},
            'support':{'view':'none','create':'none','update':'none'},
            'ai_agents':{'view':'none','create':'none','update':'none'}}
    if scope is not None:scopes['night_report']={'view':scope}
    db.execute('''INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value)
      VALUES(%s::uuid,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value''',
      (A,json.dumps({'31':{'access_scopes':scopes}})))
    db.execute('UPDATE saas_tenant_members SET permissions=%s::jsonb WHERE tenant_id=%s::uuid AND telegram_id=31',
      (json.dumps(['night_report'] if legacy else []),A))
    db.commit()


def read(**kwargs):
    return store.report(PERSON,'2026-10-15','UTC','19:00','09:00',now=NOW,**kwargs)


@pytest.fixture
def scoped_night(night_db,monkeypatch):
    connection=night_db
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    for dept in (D1,D2):
        connection.execute('INSERT INTO saas_tenant_departments(tenant_id,id,name) VALUES(%s::uuid,%s::uuid,%s)',(A,dept,dept))
    for actor,department in ((31,D1),(32,D1),(33,D2)):
        connection.execute('''INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,department_id,display_name,permissions)
          VALUES(%s::uuid,%s,'operator',%s::uuid,%s,'[]')''',(A,actor,department,'Employee '+str(actor)))
    for channel,lead in ((CC,43),(C3,44),(C4,45)):route(connection,channel=channel,lead=lead)
    for channel,actor in ((CA,31),(CC,32),(C3,33)):
        connection.execute('INSERT INTO saas_dialog_source_members(tenant_id,source_id,employee_id,updated_by) VALUES(%s::uuid,%s::uuid,%s,11)',(A,channel,actor))
    seed_job(connection)
    keys=[]
    # Own ticket is deliberately on somebody else's source; other's ticket on ours.
    for n,(channel,lead,responsible) in enumerate(((C3,44,31),(CC,43,32),(CA,41,33),(C4,45,None))):
        message(connection,'report-'+str(n),lead,channel,body='Customer '+str(n))
        key=ticket(connection,channel=channel)
        connection.execute('UPDATE saas_support_tickets SET access_responsible_id=%s,assigned_to=11 WHERE tenant_id=%s::uuid AND id=%s::uuid',(responsible,A,key))
        audit(connection,key,{'status':'in_progress','assigned_to':11})
        event(connection,'ai-'+str(n),channel,state='failed',error='chat_ai_failed')
        connection.execute('''INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state,created_at,updated_at)
          VALUES(%s::uuid,%s::uuid,%s::uuid,%s,1,'test','failed','2026-10-15T21:00Z','2026-10-15T21:01Z')''',(A,str(uuid.uuid4()),AGENT,'ai-'+str(n)))
        event(connection,'send-'+str(n),channel,state='failed',error='connect_send_failed')
        event(connection,'hand-'+str(n),channel)
        connection.execute('INSERT INTO saas_support_events(tenant_id,event_key,ticket_id) VALUES(%s::uuid,%s,%s::uuid)',(A,'handoff:hand-'+str(n),key))
        connection.execute('''INSERT INTO saas_support_ticket_messages(tenant_id,ticket_id,message_id,event_key)
          SELECT %s::uuid,%s::uuid,id,'case-material' FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s''',(A,key,A,'report-'+str(n)))
        keys.append(key)
    message(connection,'foreign-secret',42,CB,tenant=B,body='SECRET other tenant')
    connection.commit();grants(connection)
    return connection,keys


@pytest.mark.parametrize('scope,count', [('self',1),('department',2),('all',4)])
def test_scoped_lists_counts_summaries_failures_and_details(scoped_night,scope,count):
    connection,keys=scoped_night;grants(connection,scope)
    result=read()
    assert result['access_scope']==scope
    assert result['metrics']=={'dialogs':count,'ai_dialogs':count,'handed_off':count,
      'unresolved':count,'ai_errors':count,'connect_errors':count,'taken_in_work':count,'attention_count':count}
    assert {r['id'] for r in result['tickets']}==set(keys[:count])
    expected_sources={CA} if count==1 else {CA,CC} if count==2 else {CA,CC,C3,C4}
    assert {r['channel_id'] for r in result['failures']}==expected_sources
    assert len(result['failures'])==2*count
    assert all('event_key' not in row for row in result['failures'])
    assert all(row['code'] in {'ai_failed','connect_failed'} for row in result['failures'])
    assert result['next_offset'] is None
    assert all('access_responsible_id' not in row and 'summary_agent_binding' not in row for row in result['tickets'])
    for n,key in enumerate(keys):
        if n<count:
            detail=store.detail(PERSON,ticket_id=key)
            assert detail['ticket']['id']==key
            assert [m['body'] for m in detail['messages']]==['Customer '+str(n)]
            assert detail['history_scope']=='ticket_messages'
            assert 'ai_enabled' not in detail['dialog']
        else:
            with pytest.raises(TenantLinearError) as denied:store.detail(PERSON,ticket_id=key)
            assert denied.value.status==404
    for channel in (CA,CC,C3,C4):
        if channel in expected_sources:assert store.detail(PERSON,channel_id=channel,conversation_id='client')['ticket'] is None
        else:
            with pytest.raises(TenantLinearError):store.detail(PERSON,channel_id=channel,conversation_id='client')
    assert 'SECRET' not in json.dumps(result)
    with pytest.raises(TenantLinearError):store.detail(PERSON,channel_id=CB,conversation_id='client')
    with pytest.raises(TenantLinearError):store.detail({'tenant_id':B,'telegram_id':31,'role':'owner'},ticket_id=keys[0])


@pytest.mark.parametrize('scope',['none','garbage',True,{}])
def test_none_and_malformed_report_cells_override_legacy(scoped_night,scope):
    connection,keys=scoped_night;grants(connection,scope,legacy=True)
    for operation in (read,lambda:store.detail(PERSON,ticket_id=keys[0]),
                      lambda:store.detail(PERSON,channel_id=CA,conversation_id='client')):
        with pytest.raises(TenantLinearError) as denied:operation()
        assert denied.value.status==403


def test_current_permissions_departments_sources_and_membership(scoped_night):
    connection,keys=scoped_night;grants(connection,'department')
    assert read()['metrics']['attention_count']==2
    connection.execute('UPDATE saas_tenant_members SET department_id=%s::uuid WHERE tenant_id=%s::uuid AND telegram_id=32',(D2,A));connection.commit()
    assert read()['metrics']['attention_count']==1
    assert read()['metrics']['dialogs']==1
    with pytest.raises(TenantLinearError):store.detail(PERSON,ticket_id=keys[1])
    connection.execute('UPDATE saas_tenant_members SET department_id=NULL WHERE tenant_id=%s::uuid AND telegram_id=31',(A,));connection.commit()
    assert not any(read()['metrics'].values())
    grants(connection,'self')
    assert read()['metrics']['attention_count']==1
    connection.execute('DELETE FROM saas_dialog_source_members WHERE tenant_id=%s::uuid AND employee_id=31',(A,));connection.commit()
    assert read()['metrics']['dialogs']==0
    assert read()['metrics']['attention_count']==1
    with pytest.raises(TenantLinearError):store.detail(PERSON,channel_id=CA,conversation_id='client')
    assert store.detail(PERSON,ticket_id=keys[0])['messages']
    grants(connection,'none')
    with pytest.raises(TenantLinearError):read()
    grants(connection,'all')
    connection.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=31',(A,));connection.commit()
    with pytest.raises(TenantLinearError):read()
    with pytest.raises(TenantLinearError):store.detail(PERSON,ticket_id=keys[0])


@pytest.mark.parametrize('status',['suspended','archived','setup'])
def test_current_tenant_status_denies(scoped_night,status):
    connection,keys=scoped_night
    connection.execute('UPDATE saas_tenants SET status=%s WHERE id=%s::uuid',(status,A));connection.commit()
    with pytest.raises(TenantLinearError):read()
    with pytest.raises(TenantLinearError):store.detail(PERSON,ticket_id=keys[0])


def test_ticket_linked_history_never_falls_back_to_entire_source_or_other_cases(scoped_night):
    connection,keys=scoped_night
    # Even legacy/full-conversation cases have no implicit full-dialog grant.
    connection.execute("UPDATE saas_support_tickets SET history_scope='conversation' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,keys[0]))
    other=ticket(connection,channel=C3,status='resolved')
    message(connection,'unlinked',44,C3,body='SECRET other case')
    message(connection,'corrupt-channel',44,CA,body='SECRET wrong channel')
    for external in ('unlinked','corrupt-channel','foreign-secret'):
        connection.execute('''INSERT INTO saas_support_ticket_messages(tenant_id,ticket_id,message_id,event_key)
          SELECT %s::uuid,%s::uuid,id,'corrupt' FROM saas_crm_messages WHERE external_id=%s''',
          (A,other if external=='unlinked' else keys[0],external))
    for n in range(55):
        message(connection,'linked-'+str(n),44,C3,at='2026-10-15T22:%02d:00Z'%n,body='Linked '+str(n))
        connection.execute('''INSERT INTO saas_support_ticket_messages(tenant_id,ticket_id,message_id,event_key)
          SELECT %s::uuid,%s::uuid,id,'case-material' FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s''',(A,keys[0],A,'linked-'+str(n)))
    cursor=connection.execute("SELECT id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id='unlinked'",(A,))[0]['id']
    connection.commit()
    first=store.detail(PERSON,ticket_id=keys[0]);older=store.detail(PERSON,ticket_id=keys[0],before=first['next_before'])
    assert len(first['messages'])==50 and len(older['messages'])==6
    assert not {m['id'] for m in first['messages']} & {m['id'] for m in older['messages']}
    assert 'SECRET' not in json.dumps(first)+json.dumps(older)
    assert store.detail(PERSON,ticket_id=keys[0],before=str(cursor))['messages']==[]
    with pytest.raises(TenantLinearError):store.detail(PERSON,ticket_id=other)
    with pytest.raises(TenantLinearError):store.detail(PERSON,channel_id=C3,conversation_id='client')
    connection.execute('DELETE FROM saas_support_ticket_messages WHERE tenant_id=%s::uuid AND ticket_id=%s::uuid',(A,keys[0]));connection.commit()
    assert store.detail(PERSON,ticket_id=keys[0])['messages']==[]


def test_scoped_pages_and_catalog_do_not_grant_other_sections(scoped_night):
    connection,_=scoped_night
    with connection.cursor() as cur:current=read_access_profile(cur,A,31)
    connection.commit()
    result=access_snapshot(current)
    assert result['scope_catalog']['night_report']==['view']
    assert result['pages']['night_report'] is True
    assert all(result['pages'][name] is False for name in ('dialogs','support','ai_agents','ai_administrator','ai_dashboard'))
    from tenant_operator_access import access
    from tenant_ai_management_access import require_unrestricted_dialog_read
    for section in ('dialogs','support'):
        with connection.cursor() as cur:
            with pytest.raises(TenantLinearError):access(cur,PERSON,section)
        connection.rollback()
    with connection.cursor() as cur:
        with pytest.raises(TenantLinearError):require_unrestricted_dialog_read(cur,PERSON)
    from tenant_scoped_access import validate_access_scopes
    for action in ('create','update','delete'):
        with pytest.raises(ValueError):validate_access_scopes({'night_report':{action:'all'}})


def test_legacy_guard_and_gate_off_behavior(scoped_night,monkeypatch):
    connection,keys=scoped_night
    grants(connection,None,legacy=True)
    with pytest.raises(TenantLinearError,match='unrestricted_support'):read()
    with connection.cursor() as cur:current=read_access_profile(cur,A,31)
    connection.commit()
    assert access_snapshot(current)['pages']['night_report'] is False
    # The explicit report grant, including all, is independent of support/dialogs.
    grants(connection,'all')
    assert read()['metrics']['dialogs']==4
    monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED')
    with pytest.raises(TenantLinearError):read() # scope alone cannot enable legacy access
    grants(connection,'none',legacy=True)
    assert read()['access_scope'] is None
    assert read()['metrics']['dialogs']==4
    assert store.detail(PERSON,ticket_id=keys[0])['history_scope']=='conversation'


def test_owner_master_and_no_provider_or_settings_change(scoped_night):
    connection,keys=scoped_night
    seed=connection.execute('SELECT * FROM saas_ai_agent_settings');connection.commit()
    with patch('tenant_connect_transport._request',side_effect=AssertionError('No provider')),\
         patch('tenant_support_inbox_summary.summarize',side_effect=AssertionError('No AI generation')):
        assert read()['metrics']['dialogs']==1
        assert store.detail(PERSON,ticket_id=keys[0])['messages']
    assert connection.execute('SELECT * FROM saas_ai_agent_settings')==seed;connection.commit()
    connection.execute("UPDATE saas_tenant_members SET role='master' WHERE tenant_id=%s::uuid AND telegram_id=31",(A,));connection.commit()
    with pytest.raises(TenantLinearError):read()
    assert store.report(PROFILE,'2026-10-15','UTC','19:00','09:00',now=NOW)['metrics']['dialogs']==4


def test_narrow_report_defaults_do_not_disclose_other_agent_schedule(scoped_night):
    connection,_=scoped_night
    connection.execute('UPDATE saas_ai_agents SET schedule=%s::jsonb WHERE tenant_id=%s::uuid',
      (json.dumps({'timezone':'America/New_York','start':'21:00','end':'08:00'}),A));connection.commit()
    defaults=store.report(PERSON,'2026-10-15',now=NOW)['defaults']
    assert defaults=={'timezone':'Asia/Baku','start':'19:00','end':'09:00','timezone_source':'default',
                     'hours_source':'default','multiple_schedules':False}


def test_ticket_pagination_does_not_reveal_other_rows_or_counts(scoped_night):
    connection,keys=scoped_night
    for n in range(53):
        conversation='owned-'+str(n);route(connection,channel=C3,conversation=conversation,lead=300+n)
        key=ticket(connection,channel=C3,conversation=conversation)
        connection.execute('UPDATE saas_support_tickets SET access_responsible_id=31 WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,key))
    connection.commit()
    first=read();second=read(offset=first['next_offset'])
    assert first['metrics']['attention_count']==54 and second['metrics']['attention_count']==54
    assert len(first['tickets'])==50 and len(second['tickets'])==4 and second['next_offset'] is None
    assert not {r['id'] for r in first['tickets']} & {r['id'] for r in second['tickets']}
    assert not set(keys[1:]) & {r['id'] for r in first['tickets']+second['tickets']}


@pytest.mark.parametrize('change',['permission','source','department','ticket_owner'])
def test_opaque_access_snapshot_rejects_stale_pages_and_detail(scoped_night,change):
    connection,keys=scoped_night;grants(connection,'department')
    version=read()['access_version']
    assert len(version)==64
    if change=='permission':grants(connection,'self')
    elif change=='source':
        connection.execute('DELETE FROM saas_dialog_source_members WHERE tenant_id=%s::uuid AND employee_id=32',(A,));connection.commit()
    elif change=='department':
        connection.execute('UPDATE saas_tenant_members SET department_id=%s::uuid WHERE tenant_id=%s::uuid AND telegram_id=32',(D2,A));connection.commit()
    else:
        connection.execute('UPDATE saas_support_tickets SET access_responsible_id=33 WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,keys[1]));connection.commit()
    for operation in (lambda:read(offset=50,expected_access_version=version),
                      lambda:store.detail(PERSON,ticket_id=keys[0],expected_access_version=version)):
        with pytest.raises(TenantLinearError) as stale:operation()
        assert stale.value.status==409
    assert read()['access_version']!=version


def test_only_permitted_rows_influence_access_snapshot(scoped_night):
    connection,keys=scoped_night
    version=read()['access_version']
    connection.execute('DELETE FROM saas_dialog_source_members WHERE tenant_id=%s::uuid AND employee_id=33',(A,))
    connection.execute('UPDATE saas_support_tickets SET access_responsible_id=32 WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,keys[2]));connection.commit()
    assert read()['access_version']==version


def test_exact_message_snapshot_rejects_removed_material_on_earlier_page(scoped_night):
    connection,keys=scoped_night
    first=store.detail(PERSON,ticket_id=keys[0])
    connection.execute('DELETE FROM saas_support_ticket_messages WHERE tenant_id=%s::uuid AND ticket_id=%s::uuid',(A,keys[0]));connection.commit()
    with pytest.raises(TenantLinearError) as stale:
        store.detail(PERSON,ticket_id=keys[0],before=first['messages'][0]['id'],expected_history_version=first['history_version'])
    assert stale.value.status==409
    assert store.detail(PERSON,ticket_id=keys[0])['messages']==[]


@pytest.mark.asyncio
async def test_http_report_routes_reauthorize_and_never_mutate(scoped_night):
    """Actual aiohttp dispatch + SQL, no external server/provider dependencies."""
    from aiohttp import web
    from aiohttp.test_utils import TestServer, TestClient
    from unittest.mock import Mock
    from tenant_night_report_api import handler
    connection,keys=scoped_night
    app=web.Application()
    root='/api/platform/ai/night-report'
    for path,resource in (('', 'report'),('/tickets/{ticket_id}', 'ticket'),('/dialog','dialog')):
        app.router.add_get(root+path,handler(lambda _:PERSON,Mock(),resource))
    async with TestClient(TestServer(app)) as client:
        params={'date':'2026-10-15','timezone':'UTC','start':'19:00','end':'09:00',
                'expected_tenant_id':A,'expected_user_id':'31','tenant_id':B}
        response=await client.get(root,params=params)
        assert response.status==200 and response.headers['Cache-Control']=='no-store'
        result=(await response.json())['result']
        assert [r['id'] for r in result['tickets']]==[keys[0]]
        version=result['access_version']
        response=await client.get(root+'/tickets/'+keys[0],params={'expected_access_version':version})
        assert response.status==200
        assert [m['body'] for m in (await response.json())['result']['messages']]==['Customer 0']
        response=await client.get(root+'/dialog',params={'channel_id':C3,'conversation_id':'client'})
        assert response.status==404
        response=await client.get(root+'/tickets/'+keys[2]);assert response.status==404
        response=await client.get(root,params={**params,'expected_user_id':'33'});assert response.status==409
        for method in ('post','put','patch','delete'):
            response=await getattr(client,method)(root+'/tickets/'+keys[0]);assert response.status==405
        grants(connection,'department')
        response=await client.get(root,params={**params,'offset':'50','expected_access_version':version});assert response.status==409
        grants(connection,'none')
        response=await client.get(root,params=params);assert response.status==403
        assert (await response.json())['success'] is False
        response=await client.get(root+'/tickets/'+keys[0]);assert response.status==403


def legacy_grants(connection):
    """An ordinary report grant with no scoped overrides in any section."""
    grants(connection,None,legacy=True)
    connection.execute("UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,'{31,access_scopes}','{}') WHERE tenant_id=%s::uuid AND policy_key='members'",(A,))
    connection.commit()


@pytest.mark.parametrize('start,end',[('legacy','self'),('self','legacy'),('gate_off','self'),('self','gate_off')])
def test_legacy_scoped_transitions_have_non_null_snapshots(scoped_night,monkeypatch,start,end):
    connection,keys=scoped_night
    def choose(mode):
        monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
        if mode=='legacy':legacy_grants(connection)
        elif mode=='gate_off':
            grants(connection,'self',legacy=True)
            monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED')
        else:grants(connection,'self',legacy=True)
    choose(start)
    before=read()
    version=before['access_version']
    assert isinstance(version,str) and len(version)==64
    first=store.detail(PERSON,ticket_id=keys[0],expected_access_version=version)
    assert isinstance(first['history_version'],str) and first['access_version']==version
    choose(end)
    for operation in (lambda:read(offset=50,expected_access_version=version),
                      lambda:store.detail(PERSON,ticket_id=keys[0],expected_access_version=version),
                      lambda:store.detail(PERSON,channel_id=CA,conversation_id='client',expected_access_version=version)):
        with pytest.raises(TenantLinearError) as stale:operation()
        assert stale.value.status==409
    after=read()
    assert after['access_version']!=version
    assert after['metrics']['attention_count']==(1 if end=='self' else 4)
    detail=store.detail(PERSON,ticket_id=keys[0],expected_access_version=after['access_version'])
    assert detail['access_scope']==('self' if end=='self' else None)


def test_legacy_history_snapshots_also_detect_removed_messages(scoped_night):
    connection,keys=scoped_night;legacy_grants(connection)
    before=store.detail(PERSON,ticket_id=keys[0])
    assert before['access_scope'] is None and before['history_version']
    connection.execute('DELETE FROM saas_crm_messages WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,before['messages'][0]['id']));connection.commit()
    with pytest.raises(TenantLinearError) as stale:
        store.detail(PERSON,ticket_id=keys[0],expected_history_version=before['history_version'])
    assert stale.value.status==409
