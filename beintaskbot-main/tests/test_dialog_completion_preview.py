"""Synthetic, read-only preparation of existing lifecycle completion actions."""
import asyncio
import json
import uuid
from unittest.mock import Mock

import pytest
from aiohttp.test_utils import make_mocked_request
from test_operator_list_performance import (fixture, operator_db, database, db,
    PROFILE, A, B, CA, CB, add_rows, measured)
from test_dialog_lifecycle_pglite import seed_legacy_dismissed
import tenant_operator_dialogs as dialogs
import tenant_dialog_lifecycle as lifecycle
from tenant_dialog_rollout import transition
from tenant_linear_policy import TenantLinearError


@pytest.fixture
def active(fixture, monkeypatch):
    import tenant_platform
    monkeypatch.setattr(tenant_platform,'_connect',lambda:fixture)
    with fixture.cursor() as cur:
        lifecycle.migrate(cur)
        transition(cur,A,mode='active',admission_enabled=True,expected_generation=0)
    employee=int(PROFILE['telegram_id'])
    fixture.execute("INSERT INTO saas_dialog_employee_presence(tenant_id,employee_id,status,updated_by) VALUES(%s::uuid,%s,'online',%s)",(A,employee,employee))
    fixture.commit()
    return fixture


def message(d, n, *, tenant=A, channel=CA, lead=41, direction='incoming', message_type='text'):
    mid=str(uuid.UUID(int=800000+n))
    d.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,
      channel,direction,body,message_type,created_at) VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,
      'Synthetic',%s,'2099-01-01T00:00:00Z')''',
      (mid,tenant,lead,'preview-'+str(n),'connect:'+channel,direction,message_type))
    d.commit()
    return mid


def test_missed_call_is_unread_and_opening_its_chat_marks_only_missed_call_read(active):
    baseline=message(active,500)
    first=dialogs.listing(PROFILE,lifecycle='all',channel='connect:'+CA)
    row=next(r for r in first['dialogs'] if r['lead_id']==41)
    assert row['unread_count']==0, 'the first visible snapshot establishes an exact message cursor'

    missed=message(active,501,direction='system',message_type='call_missed')
    rejected=message(active,502,direction='system',message_type='call_rejected')
    listing=dialogs.listing(PROFILE,lifecycle='all',channel='connect:'+CA)
    row=next(r for r in listing['dialogs'] if r['lead_id']==41)
    assert row['unread_count']==1 and listing['unread_count']==1

    page=dialogs.history(PROFILE,'connect',41,CA,'client')
    assert {m['id'] for m in page['messages']} >= {missed,rejected,baseline}
    assert page['read_through_message_id']==missed, 'open-chat read-through includes call_missed, but excludes other system events'

    dialogs.mark_read(PROFILE,dict(provider='connect',lead_id=41,channel_id=CA,
        conversation_id='client',message_id=page['read_through_message_id']))
    after=dialogs.listing(PROFILE,lifecycle='all',channel='connect:'+CA)
    assert next(r for r in after['dialogs'] if r['lead_id']==41)['unread_count']==0
    with pytest.raises(TenantLinearError,match='Сообщение не найдено'):
        dialogs.mark_read(PROFILE,dict(provider='connect',lead_id=41,channel_id=CA,
            conversation_id='client',message_id=rejected))

    active.execute('''INSERT INTO saas_connect_conversations
      (tenant_id,connect_channel_id,conversation_id,lead_id,contact_name)
      VALUES(%s::uuid,%s::uuid,'missed-only',44,'Missed call only')''',(A,CA))
    active.commit()
    call_only=message(active,503,lead=44,direction='system',message_type='call_missed')
    first_view=dialogs.listing(PROFILE,lifecycle='all',channel='connect:'+CA)
    only_row=next(r for r in first_view['dialogs'] if r['lead_id']==44)
    assert only_row['unread_count']==1, 'a newly visible call-only dialogue starts with an unread missed call'
    only_page=dialogs.history(PROFILE,'connect',44,CA,'missed-only')
    assert only_page['read_through_message_id']==call_only
    dialogs.mark_read(PROFILE,dict(provider='connect',lead_id=44,channel_id=CA,
        conversation_id='missed-only',message_id=call_only))
    after_first_open=dialogs.listing(PROFILE,lifecycle='all',channel='connect:'+CA)
    assert next(r for r in after_first_open['dialogs'] if r['lead_id']==44)['unread_count']==0


def test_missed_call_before_unrelated_system_event_reads_without_404_and_keeps_no_reply_status(active):
    baseline=message(active,510)
    dialogs.listing(PROFILE,lifecycle='all')
    active.execute('''INSERT INTO saas_dialog_workflows
      (tenant_id,channel_id,conversation_id,id,state,operator_id,revision,closed_at)
      VALUES(%s::uuid,%s::uuid,'client',%s::uuid,'dismissed',11,1,clock_timestamp())''',
      (A,CA,str(uuid.uuid4())))
    active.commit()
    missed=message(active,511,direction='system',message_type='call_missed')
    later_system=message(active,512,direction='system',message_type='unrelated_system_event')

    snapshot=dialogs.listing(PROFILE,lifecycle='completed',channel='connect:'+CA)
    row=next(r for r in snapshot['dialogs'] if r['lead_id']==41)
    assert row['unread_count']==1 and row['lifecycle']['state']=='dismissed'
    page=dialogs.history(PROFILE,'connect',41,CA,'client')
    assert {m['id'] for m in page['messages']} >= {missed,later_system}
    assert page['read_through_message_id']==missed

    # The read cursor targets the missed call. A later unrelated system event
    # must neither produce the #786 "message not found" 404 nor be consumed.
    dialogs.mark_read(PROFILE,dict(provider='connect',lead_id=41,channel_id=CA,
        conversation_id='client',message_id=page['read_through_message_id']))
    refreshed=dialogs.listing(PROFILE,lifecycle='completed',channel='connect:'+CA)
    row=next(r for r in refreshed['dialogs'] if r['lead_id']==41)
    assert row['unread_count']==0 and row['lifecycle']['state']=='dismissed'
    reloaded=dialogs.history(PROFILE,'connect',41,CA,'client')
    assert {m['id'] for m in reloaded['messages']} >= {missed,later_system}
    assert reloaded['dialog']['lifecycle']['state']=='dismissed'



@pytest.fixture
def missed_attention_receipts(active):
    requests=[]
    yield requests
    # Receipts intentionally have no tenant FK and outlive the shared
    # PGlite fixture's tenant reset. Delete only this case's synthetic IDs.
    active.execute("DELETE FROM saas_dialog_lifecycle_receipts WHERE tenant_id=%s::uuid AND request_id::text=ANY(%s::text[])",(A,requests))
    active.commit()


def test_missed_call_attention_is_independent_of_reading_and_follows_exact_accepted_episode(active, missed_attention_receipts):
    remote=str(uuid.UUID(int=331))
    conversation='missed-call-only'
    from test_tenant_operator_workspace import employee
    profile=employee(active,['dialogs'],role='operator',actor=33)
    active.execute("INSERT INTO saas_dialog_employee_presence(tenant_id,employee_id,status,updated_by) VALUES(%s::uuid,33,'online',11)",(A,))
    active.execute("""INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value)
      VALUES(%s::uuid,'members','{"33":{"access_scopes":{"dialogs":{"view":"all","update":"all"}}}}'::jsonb)""",(A,))
    active.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Other source')",(A,remote))
    for source,lead in [(CA,43),(remote,44)]:
        active.execute("""INSERT INTO saas_connect_conversations
          (tenant_id,connect_channel_id,conversation_id,lead_id,contact_name)
          VALUES(%s::uuid,%s::uuid,%s,%s,'Synthetic client')""",(A,source,conversation,lead))
    active.commit()
    calls={source:message(active,n,channel=source,lead=lead,direction='system',message_type='call_missed')
           for n,source,lead in [(520,CA,43),(521,remote,44)]}
    for call in calls.values():
        active.execute("UPDATE saas_crm_messages SET happened_at=clock_timestamp()-interval '1 minute' WHERE id=%s",(call,))
    active.commit()

    route=dict(provider='connect',channel_id=CA,conversation_id=conversation,lead_id=43)

    def cards():
        return {r['channel_id']:r for r in dialogs.listing(profile,lifecycle='all')['dialogs'] if r['conversation_id']==conversation}

    def mutate(action, revision):
        request=str(uuid.uuid4())
        missed_attention_receipts.append(request)
        return lifecycle.mutate(profile,dict(route,action=action,expected_revision=revision,request_id=request))['lifecycle']

    first=cards()
    assert first[CA]['missed_call_pending'] and first[remote]['missed_call_pending']
    assert first[CA]['unread_count']==1 and first[remote]['unread_count']==1
    page=dialogs.history(profile,'connect',43,CA,conversation)
    assert page['read_through_message_id']==calls[CA]
    assert page['dialog']['last_message_type']=='call_missed' and page['dialog']['last_message_direction']=='system'
    assert page['dialog']['lifecycle']['can_accept'] is True
    dialogs.mark_read(profile,dict(route,message_id=page['read_through_message_id']))
    read=cards()
    assert read[CA]['unread_count']==0 and read[CA]['missed_call_pending']
    assert read[remote]['unread_count']==1 and read[remote]['missed_call_pending']
    assert read[CA]['lifecycle']['state']=='waiting'
    assert read[CA]['lifecycle']['can_dismiss'] is False, 'do not treat a call as inbound text for No reply'

    with pytest.raises(TenantLinearError):
        mutate('accept',3)
    assert cards()[CA]['missed_call_pending'], 'rejected acceptance must not clear attention'

    accepted=mutate('accept',0)
    assert accepted['state']=='accepted'
    assert not cards()[CA]['missed_call_pending'] and cards()[remote]['missed_call_pending']
    observer=next(r for r in dialogs.listing(PROFILE,lifecycle='all',owner_overview=True)['dialogs'] if r['lead_id']==43)
    assert observer['owner_overview_read_only'] and 'missed_call_pending' not in observer
    assert 'last_message_type' not in observer
    assert sum(r['unread_count'] for r in cards().values())==1, 'acceptance is not bulk or ordinary mark-read'

    closed=mutate('close',1)
    assert closed['state']=='closed'
    assert not cards()[CA]['missed_call_pending'], 'completing a handled call must not restore the red preview'

    fresh=message(active,522,lead=43,direction='system',message_type='call_missed')
    active.execute("UPDATE saas_crm_messages SET happened_at=clock_timestamp()+interval '1 second' WHERE id=%s",(fresh,))
    active.commit()
    fresh_row=cards()[CA]
    assert fresh_row['missed_call_pending'], 'a fresh missed call after a completed episode needs attention again'
    assert fresh_row['lifecycle']['state']=='closed', 'the projection does not reopen or assign the dialogue'
    assert fresh_row['unread_count']==1
    restarted=mutate('start',2)
    assert restarted['state']=='accepted' and not cards()[CA]['missed_call_pending']
    assert cards()[CA]['unread_count']==1, 'starting does not read the missed call'
    assert fresh in {m['id'] for m in dialogs.history(profile,'connect',43,CA,conversation)['messages']}


def test_preview_exact_inbound_token_and_no_read_or_lifecycle_writes(active, monkeypatch):
    older=message(active,1)
    latest=message(active,2)
    message(active,3,channel=CB)
    message(active,4,tenant=B,channel=CB)
    message(active,5,direction='outgoing')
    tables=['saas_operator_dialog_reads','saas_dialog_workflows','saas_dialog_episodes',
            'saas_dialog_lifecycle_receipts','saas_tenant_audit_events']
    before={table:active.execute('SELECT count(*) AS n FROM '+table)[0]['n'] for table in tables}
    monkeypatch.setattr(dialogs,'_unread_counts',Mock(side_effect=AssertionError('preview must not touch read cursors')))
    result=dialogs.listing(PROFILE,completion_preview=True)
    assert result['completion_preview'] is True and result['unread_count'] is None
    row=next(r for r in result['dialogs'] if r['lead_id']==41)
    assert row['lifecycle']['last_inbound_id']==latest
    assert row['lifecycle']['can_dismiss'] is False
    assert row['lifecycle']['revision']==0
    after={table:active.execute('SELECT count(*) AS n FROM '+table)[0]['n'] for table in tables}
    assert after==before
    detail=dialogs.history(PROFILE,'connect',41,CA,'client')['dialog']
    assert row['lifecycle']=={k:v for k,v in detail['lifecycle'].items() if k!='completed_episodes'} | {'bulk_waiting': True}


def test_preview_has_constant_query_round_trips_and_default_is_unchanged(active):
    message(active,6)
    ordinary=dialogs.listing(PROFILE)
    assert 'completion_preview' not in ordinary
    assert ordinary['dialogs'][0]['lifecycle']['last_inbound_id'] is None
    small,small_sql=measured(active,dialogs.listing,completion_preview=True)
    add_rows(active,80)
    large,large_sql=measured(active,dialogs.listing,completion_preview=True)
    assert len(small_sql)==len(large_sql)
    assert len(large['dialogs'])==50 and large['next_offset']==50
    assert sum('WITH routes(channel_id,conversation_id,lead_id)' in q for q in large_sql)==1
    second=dialogs.listing(PROFILE,completion_preview=True,offset=50)
    assert second['completion_preview'] is True and second['next_offset'] is None


def test_new_inbound_after_preview_is_not_dismissed(active):
    prior=message(active,7)
    from test_dialog_lifecycle_pglite import payload
    lifecycle.mutate(PROFILE,payload('accept'))
    row=next(r for r in dialogs.listing(PROFILE,completion_preview=True)['dialogs'] if r['lead_id']==41)
    message(active,8)
    with pytest.raises(TenantLinearError) as denied:
        lifecycle.mutate(PROFILE,dict(provider='connect',channel_id=CA,conversation_id='client',
            lead_id=41,action='dismiss',individual_no_reply=True,expected_revision=row['lifecycle']['revision'],
            message_id=prior,request_id=str(uuid.uuid4())))
    assert denied.value.status==409
    assert active.execute('SELECT count(*) AS n FROM saas_dialog_lifecycle_receipts')[0]['n']==1


def test_preview_api_is_opt_in_and_identity_bound(monkeypatch):
    import tenant_operator_api as api
    read=Mock(return_value={'completion_preview':True,'dialogs':[]})
    monkeypatch.setattr(api,'listing',read)
    handler=api.handler(lambda request:PROFILE,'https://fixture.test',Mock(),'dialogs')
    response=asyncio.run(handler(make_mocked_request('GET','/api/platform/ai/dialogs?completion_preview=1')))
    assert response.status==200 and read.call_args.kwargs=={'completion_preview':True}
    read.reset_mock()
    response=asyncio.run(handler(make_mocked_request('GET','/api/platform/ai/dialogs')))
    assert response.status==200 and read.call_args.kwargs=={}
    read.reset_mock()
    response=asyncio.run(handler(make_mocked_request('GET','/api/platform/ai/dialogs?completion_preview=1',
        headers={'X-Tenant-Id':B,'X-User-Id':'11'})))
    assert response.status==409
    read.assert_not_called()


def test_preview_respects_disabled_admission(active):
    message(active,9)
    with active.cursor() as cur:
        transition(cur,A,mode='active',admission_enabled=False,expected_generation=1)
    active.commit()
    row=next(r for r in dialogs.listing(PROFILE,completion_preview=True)['dialogs'] if r['lead_id']==41)
    assert row['lifecycle']['last_inbound_id']
    assert row['lifecycle']['can_dismiss'] is False


@pytest.mark.parametrize('offline',['disconnected','inactive'])
def test_dismiss_permission_is_independent_of_transport(active,offline):
    incoming=message(active,31)
    from test_dialog_lifecycle_pglite import payload
    lifecycle.mutate(PROFILE,payload('accept'))
    if offline=='disconnected':
        active.execute("UPDATE saas_tenant_integrations SET status='disconnected' WHERE tenant_id=%s::uuid AND provider='connect'",(A,))
    else:
        active.execute('UPDATE saas_connect_channels SET active=FALSE WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid',(A,CA))
    active.commit()
    preview=next(r for r in dialogs.listing(PROFILE,completion_preview=True)['dialogs'] if r['lead_id']==41)
    detail=dialogs.history(PROFILE,'connect',41,CA,'client')['dialog']
    for row in (preview,detail):
        assert row['lifecycle']['can_dismiss'] is True
        assert row['lifecycle']['has_inbound'] is True
        assert row['lifecycle']['can_accept'] is False
        assert row['can_send'] is False
        assert 'update' not in row and '_action_capabilities' not in row
    result=lifecycle.mutate(PROFILE,dict(provider='connect',channel_id=CA,conversation_id='client',lead_id=41,
        action='dismiss',individual_no_reply=True,expected_revision=1,request_id=str(uuid.uuid4()),message_id=incoming))
    assert result['lifecycle']['state']=='accepted' and result['lifecycle']['no_reply_needed']
    assert any(r['lead_id']==41 for r in dialogs.listing(PROFILE)['dialogs'])


def test_regular_list_distinguishes_no_incoming_without_read_side_effect(active):
    active.execute("DELETE FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=41 AND channel=%s",(A,'connect:'+CA))
    active.commit()
    message(active,32,direction='outgoing')
    assert not any(r['lead_id']==41 for r in dialogs.listing(PROFILE,lifecycle='active')['dialogs'])
    listed=next(r for r in dialogs.listing(PROFILE,lifecycle='all')['dialogs'] if r['lead_id']==41)
    detail=dialogs.history(PROFILE,'connect',41,CA,'client')['dialog']
    assert listed['lifecycle']['has_inbound'] is False
    assert detail['lifecycle']['has_inbound'] is False
    assert listed['lifecycle']['state']=='waiting'  # Projection only, no fabricated workflow.
    assert detail['lifecycle']['can_dismiss'] is False
    assert not active.execute('SELECT 1 FROM saas_dialog_workflows WHERE tenant_id=%s::uuid AND channel_id=%s::uuid',(A,CA))
    active.commit()
    message(active,33)
    listed=next(r for r in dialogs.listing(PROFILE)['dialogs'] if r['lead_id']==41)
    assert listed['lifecycle']['has_inbound'] is True
    assert listed['lifecycle']['last_inbound_id'] is None  # Tokens remain preview/details only.
    assert listed['unread_count']>0


def test_unknown_inbound_and_permission_fail_closed():
    from types import SimpleNamespace
    class NoSQL:
        def execute(self,*_):raise AssertionError('No SQL for supplied list snapshot')
    row=dict(provider='connect',channel_id=CA,conversation_id='client',lead_id=41,can_send=True)
    result=lifecycle.decorate(NoSQL(),A,11,row,details=False,_rollout=SimpleNamespace(enforced=True,admission_enabled=True),_workflow=None,_last_inbound='incoming',_online=False)
    assert result['lifecycle']['has_inbound'] is None
    assert result['lifecycle']['can_dismiss'] is False


def test_regular_inbound_presence_uses_one_unsorted_query(active):
    message(active,34)
    small,small_sql=measured(active,dialogs.listing)
    add_rows(active,61)
    large,large_sql=measured(active,dialogs.listing)
    assert len(small_sql)==len(large_sql)
    presence=[q for q in large_sql if 'WITH routes(channel_id,conversation_id,lead_id)' in q]
    assert len(presence)==1 and 'ORDER BY' not in presence[0]
    _,preview_sql=measured(active,dialogs.listing,completion_preview=True)
    tokens=[q for q in preview_sql if 'WITH routes(channel_id,conversation_id,lead_id)' in q]
    assert len(tokens)==1 and 'ORDER BY m.created_at DESC,m.id DESC' in tokens[0]


@pytest.mark.parametrize('direction,raw',[('incoming',{'verified_employee_id':11,'fromMe':False}),('outgoing',{'fromMe':True,'source':'linked_phone'})])
def test_completed_view_keeps_history_order_and_does_not_reopen(active,direction,raw):
    prior=message(active,80)
    seed_legacy_dismissed(active,prior)
    latest=message(active,81,direction=direction)
    active.execute('UPDATE saas_crm_messages SET raw=%s::jsonb WHERE id=%s::uuid',(json.dumps(raw),latest))
    add_rows(active,3)
    active.execute("UPDATE saas_connect_conversations SET last_message_at='2099-01-01T00:00:00Z' WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id='client'",(A,CA))
    active.commit()
    tables=['saas_dialog_workflows','saas_dialog_episodes','saas_dialog_lifecycle_receipts','saas_tenant_audit_events']
    before={t:active.execute('SELECT count(*) AS n FROM '+t)[0]['n'] for t in tables}
    all_rows=dialogs.listing(PROFILE,lifecycle='all')['dialogs']
    assert all_rows[0]['lead_id']==41
    assert [r['lead_id'] for r in all_rows if r['lead_id']>=1001]==[1003,1002,1001]
    completed=dialogs.listing(PROFILE,lifecycle='completed')['dialogs']
    assert [r['lead_id'] for r in completed]==[41]
    assert completed[0]['lifecycle']['state']=='dismissed'
    assert not completed[0]['can_send'] and not completed[0]['lifecycle']['can_dismiss']
    assert all(r['lead_id']!=41 for r in dialogs.listing(PROFILE,lifecycle='active')['dialogs'])
    assert dialogs.listing(PROFILE,lifecycle='completed',search='client')['dialogs']
    assert not dialogs.listing(PROFILE,lifecycle='completed',search='absent-synthetic')['dialogs']
    assert dialogs.history(PROFILE,'connect',41,CA,'client')['dialog']['lifecycle']['state']=='dismissed'
    assert before=={t:active.execute('SELECT count(*) AS n FROM '+t)[0]['n'] for t in tables}
    assert active.execute('SELECT state,revision FROM saas_dialog_workflows WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s',(A,CA,'client'))[0]=={'state':'dismissed','revision':1}


def test_lifecycle_mode_api_is_explicit_and_validated(active,monkeypatch):
    import tenant_operator_api as api
    read=Mock(return_value={'dialogs':[]})
    monkeypatch.setattr(api,'listing',read)
    handler=api.handler(lambda request:PROFILE,'https://fixture.test',Mock(),'dialogs')
    response=asyncio.run(handler(make_mocked_request('GET','/api/platform/ai/dialogs?lifecycle=all')))
    assert response.status==200 and read.call_args.kwargs=={'lifecycle':'all'}
    with pytest.raises(TenantLinearError): dialogs.listing(PROFILE,lifecycle='unrestricted')


def test_active_filter_keeps_accepted_without_inbound_and_does_not_write(active):
    from tenant_dialog_lifecycle import mutate
    message(active,91)
    mutate(PROFILE,dict(provider='connect',channel_id=CA,conversation_id='client',lead_id=41,
        action='accept',expected_revision=0,request_id=str(uuid.uuid4())))
    active.execute("DELETE FROM saas_crm_messages WHERE tenant_id=%s::uuid AND channel=%s AND kommo_lead_id=41",(A,'connect:'+CA))
    active.commit()
    # Ordinary listing retains its existing read-cursor initialization; the filter
    # must not create/update lifecycle or mark any new message read.
    dialogs.listing(PROFILE,lifecycle='all')
    tables=['saas_dialog_workflows','saas_dialog_episodes','saas_dialog_lifecycle_receipts','saas_tenant_audit_events','saas_operator_dialog_reads']
    before={table:active.execute('SELECT count(*) AS n FROM '+table)[0]['n'] for table in tables}
    result=dialogs.listing(PROFILE,lifecycle='active',channel='connect:'+CA)
    row=next(r for r in result['dialogs'] if r['lead_id']==41)
    assert row['lifecycle']['state']=='accepted' and row['lifecycle']['has_inbound'] is False
    assert result['total_count']==len(result['dialogs'])
    assert before=={table:active.execute('SELECT count(*) AS n FROM '+table)[0]['n'] for table in tables}


def test_active_keeps_confirmed_missed_call_until_operator_accepts(active):
    active.execute("DELETE FROM saas_crm_messages WHERE tenant_id=%s::uuid AND channel=%s AND kommo_lead_id=41", (A,'connect:'+CA))
    active.commit()
    message(active,929,direction='outgoing')
    missed=message(active,930,direction='system',message_type='call_missed')
    add_rows(active,2)
    message(active,931,lead=1001,direction='system',message_type='call_observed')
    message(active,932,lead=1002,direction='system',message_type='call_invalidated')
    listing=dialogs.listing(PROFILE,lifecycle='active',channel='connect:'+CA)
    assert [row['lead_id'] for row in listing['dialogs']]==[41]
    assert listing['total_count']==1
    assert listing['dialogs'][0]['missed_call_pending'] is True

    # Opening and reading the call does not accept the dialogue.
    page=dialogs.history(PROFILE,'connect',41,CA,'client')
    assert page['read_through_message_id']==missed
    dialogs.mark_read(PROFILE,dict(provider='connect',lead_id=41,channel_id=CA,
        conversation_id='client',message_id=missed))
    listing=dialogs.listing(PROFILE,lifecycle='active',channel='connect:'+CA)
    assert listing['dialogs'][0]['missed_call_pending'] is True

    # Unlike the attention queue, bulk completion only works with text
    # inbound tokens; a missed call is not a rhetorical question.
    preview=dialogs.listing(PROFILE,lifecycle='active',completion_preview=True,
        all_rows=True,channel='connect:'+CA)
    assert next(row for row in preview['dialogs'] if row['lead_id']==41)['lifecycle']['bulk_waiting'] is False
    lifecycle.mutate(PROFILE,dict(provider='connect',channel_id=CA,conversation_id='client',lead_id=41,
        action='accept',expected_revision=0,request_id=str(uuid.uuid4())))
    listing=dialogs.listing(PROFILE,lifecycle='active',channel='connect:'+CA)
    assert listing['dialogs'][0]['lifecycle']['state']=='accepted'
    assert listing['dialogs'][0]['missed_call_pending'] is False


def test_active_excludes_empty_routes_before_count_and_pagination(active):
    message(active,92)
    add_rows(active,61)
    result=dialogs.listing(PROFILE,lifecycle='active',channel='connect:'+CA)
    assert [r['lead_id'] for r in result['dialogs']]==[41]
    assert result['total_count']==1 and result['next_offset'] is None
    assert dialogs.listing(PROFILE,lifecycle='active',offset=50)['dialogs']==[]
    all_result=dialogs.listing(PROFILE,lifecycle='all',all_rows=True,channel='connect:'+CA)
    assert all_result['total_count']==62
    assert {r['lead_id'] for r in all_result['dialogs']}=={41,*range(1001,1062)}
    preview=dialogs.listing(PROFILE,lifecycle='active',completion_preview=True,all_rows=True,channel='connect:'+CA)
    assert preview['total_count']==62  # Keep nonactionable sources for audit/skip summaries.


@pytest.mark.parametrize('missing',[False,True])
def test_active_keeps_unknown_inbound_metadata(active,monkeypatch,missing):
    original=lifecycle.decorate
    def unknown(*args,**kwargs):
        row=original(*args,**kwargs)
        if row.get('lifecycle'):
            if missing:row['lifecycle'].pop('has_inbound',None)
            else:row['lifecycle']['has_inbound']=None
        return row
    monkeypatch.setattr(lifecycle,'decorate',unknown)
    result=dialogs.listing(PROFILE,lifecycle='active',channel='connect:'+CA)
    assert any(row['lead_id']==41 for row in result['dialogs']), 'unknown is not confirmed outgoing-only'


def test_mark_all_read_clears_unread_messages_in_closed_dialogs(active):
    dialogs.listing(PROFILE, lifecycle='all')  # establish the employee's initial cursor
    message(active, 94)
    accept=dict(provider='connect', channel_id=CA, conversation_id='client', lead_id=41,
                action='accept', expected_revision=0, request_id=str(uuid.uuid4()))
    lifecycle.mutate(PROFILE, accept)
    close={**accept, 'action':'close', 'expected_revision':1, 'request_id':str(uuid.uuid4())}
    lifecycle.mutate(PROFILE, close)

    before=dialogs.listing(PROFILE, lifecycle='completed')
    assert before['unread_count']==1
    assert before['dialogs'][0]['lifecycle']['state']=='closed'

    result=dialogs.mark_all_read(PROFILE)

    assert result['updated_dialogs']==1
    after=dialogs.listing(PROFILE, lifecycle='completed')
    assert after['unread_count']==0
    assert after['dialogs'][0]['unread_count']==0


def test_old_target_paginates_and_bulk_read_preserves_no_reply_state_and_new_arrival(active,monkeypatch):
    dialogs.listing(PROFILE, lifecycle='all')  # establish the employee's initial cursor
    active.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,
      channel,direction,body,created_at,happened_at)
      SELECT lpad(to_hex(1000000+n),32,'0')::uuid,%s::uuid,41,'page-'||n::text,
        'connect:'||%s::text,'incoming','Synthetic paginated inbound',
        '2099-01-01T00:00:00Z'::timestamptz+n*interval '1 minute',
        '2099-01-01T00:00:00Z'::timestamptz+n*interval '1 minute'
      FROM generate_series(1,150) n''',(A,CA))
    active.commit()
    target=str(uuid.UUID(int=1000075))
    system_event=str(uuid.UUID(int=2000000))
    active.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,
      channel,direction,body,created_at,happened_at)
      VALUES(%s::uuid,%s::uuid,41,'call-after-history','connect:'||%s::text,'system',
        'Synthetic call event','2100-01-01T00:00:00Z','2100-01-01T00:00:00Z')''',
      (system_event,A,CA))
    active.commit()

    from test_dialog_lifecycle_pglite import payload
    lifecycle.mutate(PROFILE,payload('accept'))
    dismissed=lifecycle.mutate(PROFILE,dict(provider='connect',channel_id=CA,
        conversation_id='client',lead_id=41,action='dismiss',individual_no_reply=True,expected_revision=1,
        message_id=str(uuid.UUID(int=1000150)),request_id=str(uuid.uuid4())))
    assert dismissed['lifecycle']['state']=='accepted' and dismissed['lifecycle']['no_reply_needed']
    no_reply_view=dialogs.listing(PROFILE,lifecycle='active')
    assert no_reply_view['unread_count']==150
    assert no_reply_view['dialogs'][0]['lifecycle']['no_reply_needed']
    assert dialogs.listing(PROFILE,lifecycle='completed')['dialogs']==[]

    anchor_page=dialogs.history(PROFILE,'connect',41,CA,'client',anchor=target)
    anchor_ids={str(row['id']) for row in anchor_page['messages']}
    assert target in anchor_ids
    assert anchor_page['anchor_message_id']==target
    assert anchor_page['read_through_message_id']==target
    assert len(anchor_page['messages'])==50
    assert anchor_page['next_before']
    older_page=dialogs.history(PROFILE,'connect',41,CA,'client',before=anchor_page['next_before'])
    older_ids={str(row['id']) for row in older_page['messages']}
    assert older_ids and not older_ids.intersection(anchor_ids)
    assert target not in older_ids

    dialogs.mark_read(PROFILE,dict(provider='connect',lead_id=41,channel_id=CA,
                                   conversation_id='client',message_id=target))
    after_target=dialogs.listing(PROFILE,lifecycle='all')
    assert after_target['unread_count']==75
    assert after_target['dialogs'][0]['lifecycle']['no_reply_needed']

    original_mark_read=dialogs.mark_read
    arrived=[]
    def add_message_after_bulk_snapshot(profile,payload):
        if not arrived:
            arrived.append(True)
            active.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,
              channel,direction,body,created_at,happened_at)
              VALUES(%s::uuid,%s::uuid,41,'arrived-after-bulk-snapshot','connect:'||%s::text,
                'incoming','Synthetic post-snapshot message','2200-01-01T00:00:00Z','2200-01-01T00:00:00Z')''',
              (str(uuid.UUID(int=3000000)),A,CA))
            active.commit()
        return original_mark_read(profile,payload)
    monkeypatch.setattr(dialogs,'mark_read',add_message_after_bulk_snapshot)
    result=dialogs.mark_all_read(PROFILE)
    assert result['updated_dialogs']==1
    after_all=dialogs.listing(PROFILE,lifecycle='all')
    assert after_all['unread_count']==1
    assert after_all['dialogs'][0]['unread_count']==1
    assert after_all['dialogs'][0]['lifecycle']['state']=='accepted'
    assert not after_all['dialogs'][0]['lifecycle']['no_reply_needed']
    # A fresh server listing (the state after reload) retains both lifecycle and read cursor.
    reloaded=dialogs.listing(PROFILE,lifecycle='all')
    assert reloaded['unread_count']==1
    assert reloaded['dialogs'][0]['unread_count']==1
    assert reloaded['dialogs'][0]['lifecycle']['state']=='accepted'
    assert not reloaded['dialogs'][0]['lifecycle']['no_reply_needed']
    repeated=dialogs.mark_all_read(PROFILE)
    assert repeated['updated_dialogs']==1
    after_repeat=dialogs.listing(PROFILE,lifecycle='all')
    assert after_repeat['unread_count']==0
    assert after_repeat['dialogs'][0]['unread_count']==0
    assert after_repeat['dialogs'][0]['lifecycle']['state']=='accepted'
    assert not after_repeat['dialogs'][0]['lifecycle']['no_reply_needed']
