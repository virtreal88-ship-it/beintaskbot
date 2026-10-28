"""Channel isolation across the actual SQL queue/worker; no external messages."""
import asyncio
import hashlib
import json
import uuid
from unittest.mock import Mock
import pytest
from test_tenant_support_inbox import database, db, A, B, CA, CB, AGENT, PROFILE
from test_integrated_connect_release import flow
import tenant_ai_agent_connect as connect
import tenant_ai_agent_run_store as runs
import tenant_ai_agents_store as agents
import tenant_ai_agent_actions as actions
import tenant_ai_agent_worker as worker
import tenant_connect_transport as transport
import tenant_support_inbox_store as inbox
import tenant_operator_dialogs as dialogs
import tenant_connect_avatars as avatars
import tenant_operator_legacy as legacy
from tenant_linear_policy import TenantLinearError

AGENT2=str(uuid.UUID(int=302))

@pytest.fixture
def channels(flow, monkeypatch):
    f=flow; db=f['db']
    monkeypatch.setattr(dialogs,'_connect',lambda:db)
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Second')",(A,CB))
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,'client',43)",(A,CB))
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,enabled,mode,model,delay_seconds,scopes) VALUES(%s::uuid,%s::uuid,'Second agent',11,TRUE,'automatic','test-model',0,%s::jsonb)",(A,AGENT2,json.dumps([{'provider':'connect','channel_id':CB}])))
    db.commit()
    def incoming(channel, text='hello', kind='text', mid=None):
        mid=mid or str(uuid.uuid4());lead=41 if channel==CA else 43
        db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,message_type,happened_at) VALUES(%s::uuid,%s::uuid,%s,%s,%s,'incoming',%s,%s,clock_timestamp()) ON CONFLICT(tenant_id,external_id) DO NOTHING",(str(uuid.uuid4()),A,lead,'connect:'+mid,'connect:'+channel,text,kind));db.commit()
        n=connect.enqueue(A,channel_id=channel,conversation_id='client',lead_id=lead,message_id=mid,event_id=mid,text=text,message_type=kind)
        return mid,n
    f['incoming']=incoming
    return f

def rows(f,table):
    result=f['db'].execute('SELECT * FROM '+table+" WHERE tenant_id=%s::uuid",(A,));f['db'].commit();return result

def states(f):
    return {r['channel_id']:r['state'] for r in rows(f,'saas_ai_conversation_state')}

def test_two_agents_send_with_separate_history_and_duplicate_receipts(channels):
    f=channels
    a,_=f['incoming'](CA,'first-channel-only');b,_=f['incoming'](CB,'second-channel-only')
    assert {r['channel_id']:r['agent_id'] for r in rows(f,'saas_ai_agent_jobs')}=={CA:AGENT,CB:AGENT2}
    for channel,mid in [(CA,a),(CB,b)]: assert f['incoming'](channel,mid=mid)[1]==0
    f['process']();f['process']();assert f['process']() is None
    assert f['send'].call_count==2
    assert {c.args[0]['channel_id'] for c in f['send'].call_args_list}=={CA,CB}
    assert 'second-channel-only' not in str(connect.history(A,41,CA))
    assert 'first-channel-only' not in str(connect.history(A,43,CB))
    assert states(f)=={CA:'active',CB:'active'}

def test_avatar_attempt_status_is_returned_from_local_projection_without_remote_lookup(channels):
    f=channels;f['incoming'](CA)
    first=dialogs.listing(PROFILE,channel='connect:'+CA)['dialogs'][0]
    assert first['avatar_status']==''
    f['db'].execute("UPDATE saas_connect_conversations SET avatar_status='available' WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id='client'",(A,CA));f['db'].commit()
    refreshed=dialogs.listing(PROFILE,channel='connect:'+CA)['dialogs'][0]
    assert refreshed['avatar_status']=='available'


def test_dialog_unread_counts_are_persistent_route_scoped_and_inbound_only(channels):
    f=channels;db=f['db'];f['incoming'](CA,'baseline message')
    first=dialogs.listing(PROFILE,channel='connect:'+CA)['dialogs'][0]
    assert first['unread_count']==0
    incoming_id,_=f['incoming'](CA,'new incoming')
    unread=dialogs.listing(PROFILE,channel='connect:'+CA)
    assert unread['dialogs'][0]['unread_count']==1 and unread['unread_count']==1
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,message_type,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,'outgoing','reply','text',clock_timestamp())",
               (str(uuid.uuid4()),A,'connect:reply-'+str(uuid.uuid4()),'connect:'+CA));db.commit()
    assert dialogs.listing(PROFILE,channel='connect:'+CA)['unread_count']==1
    f['incoming'](CA,'duplicate',mid=incoming_id)
    assert dialogs.listing(PROFILE,channel='connect:'+CA)['unread_count']==1
    latest=db.execute("SELECT id::text AS id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=41 AND channel='connect:'||%s ORDER BY COALESCE(happened_at,created_at) DESC,id DESC LIMIT 1",(A,CA))[0]['id']
    assert dialogs.mark_read(PROFILE,{'provider':'connect','lead_id':41,'channel_id':CA,'conversation_id':'client','message_id':latest})['updated']
    assert dialogs.listing(PROFILE,channel='connect:'+CA)['unread_count']==0
    with pytest.raises(TenantLinearError):
        dialogs.mark_read({'tenant_id':B,'telegram_id':22},{'provider':'connect','lead_id':41,'channel_id':CA,'conversation_id':'client','message_id':latest})


def test_read_snapshot_uses_insertion_order_and_keeps_later_arrivals_unread(channels):
    f=channels;db=f['db']
    f['incoming'](CA,'baseline')
    dialogs.listing(PROFILE)
    f['incoming'](CA,'newer provider time')
    f['incoming'](CA,'late import')
    db.execute("UPDATE saas_crm_messages SET happened_at=clock_timestamp()-interval '1 day' WHERE tenant_id=%s::uuid AND body='late import'",(A,))
    db.commit()
    page=dialogs.history(PROFILE,'connect',41,CA,'client')
    late=db.execute("SELECT id::text AS id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND body='late import'",(A,))[0]['id']
    assert page['read_through_message_id']==late
    assert page['messages'][-1]['id']!=late
    f['incoming'](CA,'arrived after snapshot')
    dialogs.mark_read(PROFILE,{'provider':'connect','lead_id':41,'channel_id':CA,'conversation_id':'client','message_id':page['read_through_message_id']})
    assert dialogs.listing(PROFILE,channel='connect:'+CA)['unread_count']==1
    # Delayed older read responses cannot rewind a cursor.
    dialogs.mark_read(PROFILE,{'provider':'connect','lead_id':41,'channel_id':CA,'conversation_id':'client','message_id':page['messages'][-1]['id']})
    assert dialogs.listing(PROFILE,channel='connect:'+CA)['unread_count']==1


def test_unread_isolated_by_channel_and_filter_does_not_change_sidebar_total(channels):
    f=channels;f['incoming'](CA,'first');f['incoming'](CB,'second')
    dialogs.listing(PROFILE)
    f['incoming'](CA,'new first');f['incoming'](CB,'new second')
    all_result=dialogs.listing(PROFILE)
    first=dialogs.listing(PROFILE,channel='connect:'+CA)
    second=dialogs.listing(PROFILE,channel='connect:'+CB)
    assert all_result['unread_count']==2
    assert first['unread_count']==2 and first['dialogs'][0]['unread_count']==1
    assert second['unread_count']==2 and second['dialogs'][0]['unread_count']==1


def test_unread_totals_cover_more_than_one_page_and_cap_only_in_the_ui(channels):
    f=channels;db=f['db']
    db.execute('''INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id)
      SELECT %s::uuid,%s::uuid,'bulk-'||n||'@s.whatsapp.net',1000+n FROM generate_series(1,60) n''',(A,CA))
    db.commit();dialogs.listing(PROFILE,offset=0)
    db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,message_type,happened_at)
      SELECT md5('unread-'||n)::uuid,%s::uuid,41,'connect:unread-'||n,'connect:'||%s,'incoming','message','text',
        clock_timestamp()+n*interval '1 microsecond' FROM generate_series(1,101) n''',(A,CA));db.commit()
    first=dialogs.listing(PROFILE,offset=0)
    second=dialogs.listing(PROFILE,offset=50)
    assert first['total_count']==62 and len(first['dialogs'])==50 and first['next_offset']==50
    assert second['total_count']==62 and len(second['dialogs'])==12 and second['next_offset'] is None
    assert first['unread_count']==101
    assert next(row for row in first['dialogs'] if row['lead_id']==41)['unread_count']==101

def test_avatar_endpoint_is_tenant_scoped_and_claims_only_once(channels,monkeypatch):
    f=channels;db=f['db'];f['incoming'](CA)
    monkeypatch.setattr(avatars,'_connect',lambda:db)
    image=b'\x89PNG\r\n\x1a\n'+b'avatar-bytes'
    fetch=Mock()
    async def get_image(route):
        fetch(route)
        return image,'image/png'
    monkeypatch.setattr(avatars,'_fetch',get_image)
    params={'provider':'connect','lead_id':'41','channel_id':CA,'conversation_id':'client'}
    assert asyncio.run(avatars.get_avatar(PROFILE,params))==(image,'image/png')
    assert db.execute("SELECT avatar_status FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id='client'",(A,CA))[0]['avatar_status']=='available'
    db.commit()
    asyncio.run(avatars.get_avatar(PROFILE,params))
    assert fetch.call_count==2  # app reads the cached Connect copy; Evolution is not called again
    with pytest.raises(TenantLinearError):asyncio.run(avatars.get_avatar({**PROFILE,'tenant_id':B},params))

@pytest.mark.parametrize('phase',['before','during'])
def test_manual_takeover_only_cancels_its_channel_even_during_other_voice(channels,phase):
    f=channels;f['incoming'](CA);f['incoming'](CB,'','audio')
    if phase=='before':
        connect.mark_operator_reply(A,41,CA,'client')
        assert {r['channel_id']:r['state'] for r in rows(f,'saas_ai_agent_jobs')}=={CA:'skipped',CB:'queued'}
    else:
        f['db'].execute("UPDATE saas_ai_agent_jobs SET due_at=now()+interval '1 hour' WHERE channel_id=%s",(CA,));f['db'].commit()
        def takeover(*args,**kwargs):
            connect.mark_operator_reply(A,41,CA,'client')
            return 'Salam',{'total_tokens':3}
        f['speech'].side_effect=takeover
    f['process']()
    assert states(f)=={CA:'human',CB:'active'}
    f['speech'].assert_called_once();f['send'].assert_called_once()
    assert f['send'].call_args.args[0]['channel_id']==CB

def test_pause_resume_only_new_messages_on_selected_channel(channels):
    f=channels;a,_=f['incoming'](CA);f['incoming'](CB)
    agents.set_conversation_state(PROFILE,'client','paused',provider='connect',channel_id=CA)
    f['process']();assert f['send'].call_count==1
    agents.set_conversation_state(PROFILE,'client','active',provider='connect',channel_id=CA)
    assert f['process']() is None
    assert f['incoming'](CA,mid=a)[1]==0
    f['incoming'](CA);f['process']()
    assert f['send'].call_count==2 and states(f)=={CA:'active',CB:'active'}

def test_manual_mode_does_not_queue_inbound_drafts_for_any_channel(channels):
    f=channels;db=f['db'];db.execute("UPDATE saas_ai_agents SET mode='manual'");db.commit()
    jobs_before=rows(f,'saas_ai_agent_jobs')
    mids=[f['incoming'](CA)[0],f['incoming'](CB)[0]];f['process']();f['process']()
    assert rows(f,'saas_ai_agent_drafts')==[]
    events=rows(f,'saas_tenant_webhook_events')
    current=[event for event in events if event.get('external_id') in mids]
    assert len(current)==2 and {event['error_code'] for event in current}=={'manual_mode'}
    assert len(rows(f,'saas_ai_agent_jobs'))==len(jobs_before)
    f['send'].assert_not_called()

def test_handoff_tickets_are_independent_and_idempotent(channels):
    f=channels;f['incoming'](CA);f['incoming'](CB)
    jobs=rows(f,'saas_ai_agent_jobs')
    for j in jobs:
        assert actions.handoff(j,'operator requested')
        assert not actions.handoff(j,'operator requested')
    tickets=rows(f,'saas_support_tickets')
    assert len(tickets)==2 and {t['channel_id'] for t in tickets}=={CA,CB}
    agents.set_conversation_state(PROFILE,'client','active',provider='connect',channel_id=CA)
    assert states(f)=={CA:'active',CB:'human'}
    assert not actions.handoff(next(j for j in jobs if j['channel_id']==CA),'retry')
    assert states(f)[CA]=='active'

def test_restart_recovers_only_expired_channel_and_never_resends(channels):
    f=channels;f['incoming'](CA);f['incoming'](CB)
    first=runs.claim_next();second=runs.claim_next()
    db=f['db'];db.execute("UPDATE saas_ai_agent_jobs SET lease_until=now()-interval '1 second' WHERE tenant_id=%s::uuid AND channel_id=%s",(A,first['channel_id']));db.commit()
    recovered=runs.claim_next()
    assert recovered['channel_id']==first['channel_id'] and recovered['lease_token']!=first['lease_token']
    active=next(j for j in rows(f,'saas_ai_agent_jobs') if j['channel_id']==second['channel_id'])
    assert active['lease_token']==second['lease_token']
    assert runs.skip_job(first,'stale') is False
    asyncio.run(worker.process_connect_one(recovered,Mock()));asyncio.run(worker.process_connect_one(second,Mock()))
    assert f['send'].call_count==2 and runs.claim_next() is None

def test_same_agent_can_have_two_independent_job_keys(channels):
    f=channels;db=f['db']
    db.execute("UPDATE saas_ai_agents SET enabled=FALSE WHERE id=%s::uuid",(AGENT2,))
    db.execute("UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE id=%s::uuid",(json.dumps([{'provider':'connect','channel_id':''}]),AGENT));db.commit()
    f['incoming'](CA);f['incoming'](CB)
    jobs=rows(f,'saas_ai_agent_jobs');assert len(jobs)==2 and all(j['agent_id']==AGENT for j in jobs)
    connect.mark_operator_reply(A,41,CA,'client');f['process']()
    assert f['send'].call_count==1 and f['send'].call_args.args[0]['channel_id']==CB

def test_ai_outbound_echo_does_not_take_over_either_channel(channels,monkeypatch):
    f=channels;f['incoming'](CA);f['incoming'](CB);f['process']();f['process']()
    monkeypatch.setattr(transport,'_verify_signature',lambda *a:True)
    for msg in rows(f,'saas_crm_messages'):
        if msg['author_name']!='AI Agent':continue
        channel=msg['channel'].removeprefix('connect:')
        event={'id':str(uuid.uuid4()),'type':'message.outbound.observed','data':{'id':msg['external_id'].removeprefix('connect:'),'channelId':channel,'conversationId':'client','fromMe':True,'type':'text'}}
        assert transport.ingest(json.dumps(event).encode(),'sig',event)
        assert transport.ingest(json.dumps(event).encode(),'sig',event)
    assert states(f)=={CA:'active',CB:'active'} and f['send'].call_count==2

def test_tenant_channel_and_lead_mismatch_rejected(channels):
    f=channels;f['incoming'](CA);f['incoming'](CB)
    with pytest.raises(ValueError):connect.mark_operator_reply(A,41,CB,'client')
    with pytest.raises(ValueError):connect.mark_operator_reply(B,41,CA,'client')
    with pytest.raises(TenantLinearError):agents.set_conversation_state({'tenant_id':B,'telegram_id':11},'client','paused',provider='connect',channel_id=CB)
    assert states(f)=={CA:'active',CB:'active'}

def test_unresolved_legacy_state_blocks_both_channels_without_losing_history(channels,monkeypatch):
    f=channels;f['incoming'](CA);f['incoming'](CB);db=f['db']
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,state,reason) VALUES(%s::uuid,'connect','client','human','legacy')",(A,));db.commit()
    for channel,lead in [(CA,41),(CB,43)]:
        assert not connect.allowed({'provider':'connect','tenant_id':A,'channel_id':channel,'conversation_id':'client'})
        with pytest.raises(TenantLinearError):agents.set_conversation_state(PROFILE,'client','active',provider='connect',channel_id=channel)
        assert agents.read_conversation_state(PROFILE,'client','connect',channel)['blocked_reason']=='legacy_channel_ambiguous'
        assert agents.read_conversation_for_lead(PROFILE,lead)['state']=='blocked'
        assert dialogs.history(PROFILE,'connect',lead,channel,'client')['dialog']['display_state']=='blocked'
        assert transport.send_ai_reply_if_active(A,lead,channel,'client','no',str(uuid.uuid4()),1) is None
    assert runs.claim_next() is None
    import tenant_ai_reliability as reliability
    monkeypatch.setattr(reliability,'_connect',lambda:db)
    messages=reliability.snapshot(PROFILE)['messages']
    assert messages and all(m['reason_code']=='legacy_channel_ambiguous' and not m['automatic_retry'] for m in messages)
    f['send'].assert_not_called();assert len(rows(f,'saas_crm_messages'))>=2


def test_legacy_channel_review_shows_tenant_routes_but_operator_choice_cannot_resolve_ambiguity(channels,monkeypatch):
    f=channels;db=f['db'];f['incoming'](CA,'channel one proof');f['incoming'](CB,'channel two proof')
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,channel_id,agent_id,state,reason) VALUES(%s::uuid,'connect','client','',%s::uuid,'paused','legacy')",(A,AGENT));db.commit()
    monkeypatch.setattr(legacy,'_connect',lambda:db)
    result=legacy.options(PROFILE,{'provider':'connect','channel_id':CA,'conversation_id':'client','lead_id':41})
    assert result['reason']=='multiple_channels' and result['allowed'] is False
    assert {(item['channel_id'],item['name']) for item in result['channels']}=={(CA,'WhatsApp'),(CB,'Second')}
    assert all(item['message_count']>0 for item in result['channels'])
    assert all(item['job_count']>0 and item['event_count']>0 for item in result['channels'])
    assert result['evidence']['draft_count']==0 and result['evidence']['event_channel_count']==2
    assert result['evidence']['route_count']==2 and result['evidence']['message_count']==sum(item['message_count'] for item in result['channels'])
    # A foreign tenant cannot inspect or select either tenant A route.
    with pytest.raises(TenantLinearError):
        legacy.options({'tenant_id':B,'telegram_id':22},{'provider':'connect','channel_id':CA,'conversation_id':'client','lead_id':41})


def test_legacy_channel_recovery_requires_verified_single_route_and_is_idempotent(channels,monkeypatch):
    f=channels;db=f['db'];f['incoming'](CA,'persisted source message')
    db.execute("DELETE FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id='client'",(A,CB))
    db.execute("UPDATE saas_ai_agent_jobs SET state='skipped' WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s",(A,CA))
    db.execute("UPDATE saas_ai_conversation_state SET channel_id='',state='paused',reason='legacy' WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA));db.commit()
    monkeypatch.setattr(legacy,'_connect',lambda:db)
    data={'provider':'connect','channel_id':CA,'conversation_id':'client','lead_id':41}
    preview=legacy.options(PROFILE,data)
    assert preview['allowed'] and preview['reason']=='unique_verified_route'
    saved=legacy.recover(PROFILE,{**data,'confirmed':True,'expected_token':preview['expected_token']})
    assert saved['recovered'] and saved['resume_required'] and saved['state']=='paused'
    replay=legacy.recover(PROFILE,{**data,'confirmed':True,'expected_token':preview['expected_token']})
    assert replay['replayed'] and replay['state']=='paused'
    assert db.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid AND action='ai_legacy_channel_recovered'",(A,))[0]['n']==1
    assert db.execute("SELECT channel_id,state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id='client'",(A,))[0]=={'channel_id':CA,'state':'paused','reason':'legacy'}


def test_duplicate_message_cannot_change_its_channel(channels):
    f=channels;mid,_=f['incoming'](CA)
    with pytest.raises(ValueError,match='scope mismatch'):f['incoming'](CB,mid=mid)
    assert states(f)=={CA:'active'}
    assert len(rows(f,'saas_ai_agent_jobs'))==1

def test_manual_only_scoped_agent_is_assigned_without_queueing_or_reply(channels):
    f=channels;db=f['db']
    db.execute("UPDATE saas_ai_agents SET mode='manual' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT));db.commit()
    jobs_before=rows(f,'saas_ai_agent_jobs')
    mid,queued=f['incoming'](CA);assert queued==0
    event=db.execute("SELECT state,error_code FROM saas_tenant_webhook_events WHERE tenant_id=%s::uuid AND provider='connect' AND external_id=%s",(A,mid))[0]
    assert event=={'state':'skipped','error_code':'manual_mode'}
    assert len(rows(f,'saas_ai_agent_jobs'))==len(jobs_before)
    assigned=db.execute("SELECT agent_id,state FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA))[0]
    assert assigned=={'agent_id':AGENT,'state':'active'}
    agents.set_conversation_state(PROFILE,'client','paused',provider='connect',channel_id=CA)
    route=dialogs.listing(PROFILE,channel='connect:'+CA)['dialogs'][0]
    assert route['agent_status']=='manual' and not route['handoff_active']
    f['send'].assert_not_called();f['generate'].assert_not_called()


def test_ambiguous_agents_are_not_bound_or_queued(channels):
    f=channels;db=f['db']
    db.execute("UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE tenant_id=%s::uuid AND id IN (%s::uuid,%s::uuid)",
      (json.dumps([{'provider':'connect','channel_id':CB}]),A,AGENT,AGENT2));db.commit()
    _mid,queued=f['incoming'](CB);assert queued==0
    assert db.execute("SELECT 1 FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CB))==[]
    assert db.execute("SELECT error_code FROM saas_tenant_webhook_events WHERE tenant_id=%s::uuid AND provider='connect' ORDER BY received_at DESC LIMIT 1",(A,))[0]['error_code']=='agent_selection_required'
    assert db.execute("SELECT 1 FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND channel_id=%s",(A,CB))==[]
    f['send'].assert_not_called()


def test_disabled_ai_still_reconciles_one_scoped_agent_without_activation(channels,monkeypatch):
    f=channels;db=f['db']
    reconciled=[]
    original=connect.reconcile_unassigned_connect_dialog
    def track(*args,**kwargs):
        result=original(*args,**kwargs);reconciled.append(result);return result
    monkeypatch.setattr(connect,'reconcile_unassigned_connect_dialog',track)
    db.execute("UPDATE saas_ai_agent_settings SET enabled=FALSE,auto_send_enabled=FALSE WHERE tenant_id=%s::uuid",(A,));db.commit()
    db.execute("UPDATE saas_ai_conversation_state SET agent_id=NULL,state='active',reason='' WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA))
    db.execute("DELETE FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id='client' AND channel_id=%s",(A,CA));db.commit()
    assert db.execute("SELECT enabled,auto_send_enabled FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid",(A,))[0]=={'enabled':False,'auto_send_enabled':False}
    assert not connect.allowed({'provider':'connect','tenant_id':A,'channel_id':CA,'conversation_id':'client'})
    assert not connect.allowed({'provider':'connect','tenant_id':A,'channel_id':CA,'conversation_id':'client'},connection=db)
    _mid,queued=f['incoming'](CA,'must not run')
    assert reconciled and reconciled[0][0]['id']==AGENT
    assert queued==0
    state=db.execute("SELECT agent_id,state,reason,agent_assignment_id FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA))[0]
    assert state=={'agent_id':AGENT,'state':'paused','reason':'automatic_assignment_pending','agent_assignment_id':None}
    assert not rows(f,'saas_ai_agent_jobs')
    assert not rows(f,'saas_ai_agent_runs')
    assert db.execute("SELECT enabled,auto_send_enabled FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid",(A,))[0]=={'enabled':False,'auto_send_enabled':False}
    f['send'].assert_not_called();f['generate'].assert_not_called()


def test_gated_assignment_joins_existing_active_runtime_on_later_inbound(channels):
    f=channels;db=f['db']
    db.execute("UPDATE saas_ai_agent_settings SET enabled=FALSE,auto_send_enabled=FALSE WHERE tenant_id=%s::uuid",(A,))
    db.execute("UPDATE saas_ai_conversation_state SET agent_id=NULL,state='active',reason='' WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA))
    db.execute("DELETE FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s",(A,CA));db.commit()
    assert f['incoming'](CA,'while disabled')[1]==0
    pending=db.execute("SELECT agent_id,state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA))[0]
    assert pending=={'agent_id':AGENT,'state':'paused','reason':'automatic_assignment_pending'}
    db.execute("UPDATE saas_ai_agent_settings SET enabled=TRUE,auto_send_enabled=TRUE WHERE tenant_id=%s::uuid",(A,));db.commit()
    assert connect.allowed({'provider':'connect','tenant_id':A,'channel_id':CA,'conversation_id':'client','lead_id':41},connection=db)
    assert f['incoming'](CA,'after active gates')[1]==1
    live=db.execute("SELECT agent_id,state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA))[0]
    assert live=={'agent_id':AGENT,'state':'active','reason':''}
    assert len(rows(f,'saas_ai_agent_jobs'))==1
    f['send'].assert_not_called()


def test_operator_manual_pause_is_not_reassigned_or_resumed(channels):
    f=channels;db=f['db']
    db.execute("UPDATE saas_ai_conversation_state SET agent_id=NULL,state='paused',reason='operator_manual_pause',agent_assignment_id=NULL WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA));db.commit()
    db.execute("DELETE FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s",(A,CA));db.commit()
    assert f['incoming'](CA,'respect manual pause')[1]==0
    state=db.execute("SELECT agent_id,state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA))[0]
    assert state=={'agent_id':None,'state':'paused','reason':'operator_manual_pause'}
    assert not rows(f,'saas_ai_agent_jobs')
    f['send'].assert_not_called();f['generate'].assert_not_called()


def test_identity_conflict_preserves_operator_no_agent_reason(channels):
    f=channels;db=f['db']
    db.execute("UPDATE saas_ai_conversation_state SET agent_id=NULL,state='paused',reason='operator_manual_pause',agent_assignment_id=NULL WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA));db.commit()
    from tenant_connect_direct_identity import record_conflict
    with db.cursor() as cur:record_conflict(cur,A,CA,'client','client',{},'identity-conflict-event')
    db.commit()
    state=db.execute("SELECT agent_id,state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA))[0]
    assert state=={'agent_id':None,'state':'paused','reason':'operator_manual_pause'}


def test_assignment_reconciler_retries_no_candidate_and_preserves_manual_no_agent(channels):
    f=channels;db=f['db']
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,'later',44)",(A,CA))
    db.execute("UPDATE saas_ai_agents SET scopes='[]'::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT));db.commit()
    assert connect.reconcile_unassigned_connect_dialog(db.cursor(),A,CA,'later')[1]=='no_eligible_agent'
    missing=db.execute("SELECT agent_id,state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='later'",(A,CA))[0]
    assert missing=={'agent_id':None,'state':'paused','reason':'automatic_assignment_no_eligible_agent'}
    db.execute("UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(json.dumps([{'provider':'connect','channel_id':CA}]),A,AGENT))
    assert connect.reconcile_unassigned_connect_dialog(db.cursor(),A,CA,'later')[0]['id']==AGENT
    db.execute("UPDATE saas_ai_conversation_state SET agent_id=NULL,agent_assignment_id=%s::uuid,state='paused',reason='operator_agent_unassigned' WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='later'",(str(uuid.uuid4()),A,CA));db.commit()
    assert connect.reconcile_unassigned_connect_dialog(db.cursor(),A,CA,'later')[1]=='explicit_or_existing_state_preserved'
    saved=db.execute("SELECT agent_id,reason FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='later'",(A,CA))[0]
    assert saved=={'agent_id':None,'reason':'operator_agent_unassigned'}


def test_company_settings_save_reconciles_old_direct_routes_without_queueing(channels):
    f=channels;db=f['db']
    db.execute("UPDATE saas_ai_conversation_state SET agent_id=NULL,state='active',reason='' WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA))
    db.execute("DELETE FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s",(A,CA))
    db.execute("UPDATE saas_ai_agent_settings SET enabled=FALSE,auto_send_enabled=FALSE WHERE tenant_id=%s::uuid",(A,));db.commit()
    agents.save_settings(PROFILE,{'max_replies_per_chat':60})
    db.commit()
    state=db.execute("SELECT agent_id,state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA))[0]
    assert state=={'agent_id':AGENT,'state':'paused','reason':'automatic_assignment_pending'}
    assert not rows(f,'saas_ai_agent_jobs')
    assert db.execute("SELECT enabled,auto_send_enabled FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid",(A,))[0]=={'enabled':False,'auto_send_enabled':False}


def test_agent_save_reconciles_each_channel_under_its_own_scope(channels):
    f=channels;db=f['db']
    db.execute("UPDATE saas_ai_agent_settings SET enabled=FALSE,auto_send_enabled=FALSE WHERE tenant_id=%s::uuid",(A,))
    db.execute("UPDATE saas_ai_conversation_state SET agent_id=NULL,state='active',reason='' WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA))
    db.execute("DELETE FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect'",(A,));db.commit()
    version=db.execute("SELECT version FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT))[0]['version'];db.commit()
    agents.save_agent(PROFILE,{'name':'Night support updated','expected_version':version},agent_id=AGENT)
    db.commit()
    assigned={r['channel_id']:r['agent_id'] for r in db.execute("SELECT channel_id,agent_id FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id IN (%s,%s)",(A,CA,CB))}
    assert assigned=={CA:AGENT,CB:AGENT2}
    assert not rows(f,'saas_ai_agent_jobs')
    assert db.execute("SELECT enabled,auto_send_enabled FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid",(A,))[0]=={'enabled':False,'auto_send_enabled':False}


def test_replayed_old_skipped_event_repairs_binding_without_requeue(channels):
    f=channels;db=f['db'];mid='legacy-skipped-event'
    fingerprint=hashlib.sha256(('connect:'+mid).encode()).hexdigest()
    db.execute("UPDATE saas_ai_conversation_state SET agent_id=NULL,state='active',reason='' WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA))
    db.execute("DELETE FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s",(A,CA))
    db.execute("UPDATE saas_ai_agent_settings SET enabled=FALSE,auto_send_enabled=FALSE WHERE tenant_id=%s::uuid",(A,))
    db.execute("INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider,external_id,event_type,payload,state,error_code) VALUES(%s::uuid,%s,'connect',%s,'incoming_message',%s::jsonb,'skipped','company_ai_disabled')",(A,fingerprint,mid,json.dumps({'channel_id':CA,'conversation_id':'client'})))
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type) VALUES(%s::uuid,%s::uuid,41,%s,'incoming',%s,'old','text')",(str(uuid.uuid4()),A,'connect:'+mid,'connect:'+CA));db.commit()
    assert connect.enqueue(A,channel_id=CA,conversation_id='client',lead_id=41,message_id=mid,event_id=mid,text='old')==0
    state=db.execute("SELECT agent_id,state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA))[0]
    assert state=={'agent_id':AGENT,'state':'paused','reason':'automatic_assignment_pending'}
    assert not rows(f,'saas_ai_agent_jobs')


def test_reconcile_ties_do_not_pick_by_order_or_cross_channel(channels):
    f=channels;db=f['db']
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,'tie',45)",(A,CA))
    db.execute("UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(json.dumps([{'provider':'connect','channel_id':CA}]),A,AGENT2));db.commit()
    assert connect.reconcile_unassigned_connect_dialog(db.cursor(),A,CA,'tie')[1]=='agent_selection_required'
    assert db.execute("SELECT agent_id,reason FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='tie'",(A,CA))[0]=={'agent_id':None,'reason':'automatic_assignment_agent_selection_required'}
    assert connect.reconcile_unassigned_connect_dialog(db.cursor(),B,CA,'tie')[1]=='connect_assignment_route_blocked'


def test_read_only_deployment_audit_counts_ambiguity_and_invalid_provenance(channels):
    from scripts.audit_connect_channel_scope import audit
    f=channels;db=f['db'];f['incoming'](CA);f['incoming'](CB)
    result=audit(db);db.commit()
    assert result['safe_to_migrate'] and result['inconsistent_job_routes']==0
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,state) VALUES(%s::uuid,'connect','client','human')",(A,));db.commit()
    result=audit(db);db.commit()
    assert not result['safe_to_migrate'] and result['unresolved_legacy_states']==1
    db.execute("UPDATE saas_ai_agent_jobs SET lead_id=41 WHERE tenant_id=%s::uuid AND channel_id=%s",(A,CB));db.commit()
    result=audit(db);db.commit()
    assert result['inconsistent_job_routes']==1


def test_new_connect_state_does_not_change_same_named_kommo_state(channels):
    f=channels;db=f['db']
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,state,reason) VALUES(%s::uuid,'kommo','client','paused','kommo-only')",(A,));db.commit()
    f['incoming'](CA);f['incoming'](CB);connect.mark_operator_reply(A,41,CA,'client')
    kommo=db.execute("SELECT * FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='kommo'",(A,))[0];db.commit()
    assert kommo['state']=='paused' and kommo['reason']=='kommo-only' and kommo['channel_id']==''


def test_company_send_disabled_does_not_process_real_customer_messages(channels):
    f=channels;db=f['db']
    db.execute("UPDATE saas_ai_agent_settings SET auto_send_enabled=FALSE WHERE tenant_id=%s::uuid",(A,));db.commit()
    jobs_before=rows(f,'saas_ai_agent_jobs')
    assert f['incoming'](CA)[1]==0
    message_id,queued=f['incoming'](CB);assert queued==0
    f['process']();f['send'].assert_not_called()
    assert rows(f,'saas_ai_agent_drafts')==[]
    assert rows(f,'saas_ai_agent_jobs')==jobs_before
    event=db.execute("SELECT state,error_code FROM saas_tenant_webhook_events WHERE tenant_id=%s::uuid AND provider='connect' AND external_id=%s",(A,message_id))[0]
    assert event=={'state':'skipped','error_code':'connect_admission_blocked'}
