"""Channel isolation across the actual SQL queue/worker; no external messages."""
import asyncio
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

def test_manual_drafts_and_reviews_are_channel_scoped(channels):
    f=channels;db=f['db'];db.execute("UPDATE saas_ai_agents SET mode='manual'");db.commit()
    f['incoming'](CA);f['incoming'](CB);f['process']();f['process']()
    drafts=rows(f,'saas_ai_agent_drafts');assert len(drafts)==2
    connect.mark_operator_reply(A,41,CA,'client')
    first=next(d for d in drafts if d['lead_id']==41);second=next(d for d in drafts if d['lead_id']==43)
    with pytest.raises(TenantLinearError): agents.reserve_draft_send(PROFILE,first['id'])
    assert agents.reserve_draft_send(PROFILE,second['id'])['channel_id']==CB
    assert agents.finish_draft_send(A,second['id'],'blocked')=='pending'
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


def test_duplicate_message_cannot_change_its_channel(channels):
    f=channels;mid,_=f['incoming'](CA)
    with pytest.raises(ValueError,match='scope mismatch'):f['incoming'](CB,mid=mid)
    assert states(f)=={CA:'active'}
    assert len(rows(f,'saas_ai_agent_jobs'))==1


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


def test_connect_test_mode_selects_channel_and_agent_and_never_sends(channels):
    f=channels;db=f['db']
    db.execute("UPDATE saas_ai_agent_settings SET operating_mode='test' WHERE tenant_id=%s::uuid",(A,))
    db.execute("INSERT INTO saas_ai_agent_connect_test_mode(tenant_id,enabled,verified,channel_id,conversation_id,agent_id) VALUES(%s::uuid,TRUE,TRUE,%s,'client',%s::uuid)",(A,CB,AGENT2));db.commit()
    assert f['incoming'](CA)[1]==0
    assert f['incoming'](CB)[1]==1
    f['process']();f['send'].assert_not_called()
    drafts=rows(f,'saas_ai_agent_drafts');assert len(drafts)==1
    assert drafts[0]['lead_id']==43 and drafts[0]['agent_id']==AGENT2
    with pytest.raises(TenantLinearError):agents.reserve_draft_send(PROFILE,drafts[0]['id'])
