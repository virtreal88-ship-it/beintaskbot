'''Release regression tests against isolated PostgreSQL/WASM; all remote APIs mocked.'''
import asyncio
import json
import uuid
from unittest.mock import Mock, patch
import pytest
from test_tenant_support_inbox import (
    database, db, A, B, CA, CB, AGENT, PROFILE, seed_job, config_company,
    manual, store, summary, agents, actions, connect_ai,
)
import tenant_ai_agent_run_store as runs
import tenant_ai_agent_worker as worker
import tenant_connect_voice as voice
import tenant_connect_transport as transport

@pytest.fixture
def flow(db, monkeypatch):
    from cryptography.fernet import Fernet
    cipher=Fernet(Fernet.generate_key())
    for module in (runs,voice,transport):
        monkeypatch.setattr(module,'_connect',lambda:db)
    monkeypatch.setattr(transport,'_fernet',lambda:cipher)
    for name in ('AI_AGENTS_ENABLED','AI_AGENTS_AUTO_SEND_ENABLED'):
        monkeypatch.setenv(name,'true')
    monkeypatch.setenv('STAGING_MODE','false')
    monkeypatch.delenv('AI_AGENTS_PLATFORM_KEY_ACTIVATED_AT',raising=False)
    seed_job(db)
    db.execute("UPDATE saas_ai_agent_jobs SET state='skipped'")
    db.execute("UPDATE saas_ai_agent_settings SET auto_send_enabled=TRUE")
    db.execute("UPDATE saas_ai_agents SET mode='automatic',delay_seconds=0,model='test-model',scopes=%s::jsonb",
               (json.dumps([{'provider':'connect','channel_id':CA}]),))
    secret=cipher.encrypt(json.dumps({'api_key':'mock-connect-key'}).encode())
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets) VALUES(%s::uuid,'connect','connected',%s)",(A,secret))
    db.commit()
    for name in ('platform_key_ready','platform_service_ready','platform_model_allowed'):
        monkeypatch.setattr(runs,name,lambda *a:True)
    monkeypatch.setattr(runs,'choose_api_credential',lambda *a:('mock-platform-key','platform'))
    monkeypatch.setattr(runs,'platform_token_limit',lambda *a:0)
    monkeypatch.setattr(voice,'platform_token_limit',lambda *a:0)
    monkeypatch.setattr(summary,'choose_api_credential',lambda *a:('mock-platform-key','platform'))
    monkeypatch.setattr(summary,'platform_model_allowed',lambda *a:True)
    monkeypatch.setattr(summary,'platform_token_limit',lambda *a:0)
    summary_call=Mock(return_value=(json.dumps({k:'Summary' for k in ('description','summary','suggestions','next_steps')}),{'total_tokens':3}))
    monkeypatch.setattr(summary,'generate',summary_call)
    generation=Mock(return_value=('Cavab hazirdir',{'input_tokens':4,'output_tokens':2,'total_tokens':6}))
    speech=Mock(return_value=('Salam, qiymet nedir?',{'input_tokens':10,'output_tokens':3,'total_tokens':13}))
    send=Mock(side_effect=lambda *a:str(uuid.uuid4()))
    monkeypatch.setattr(worker,'generate',generation)
    monkeypatch.setattr(voice,'transcribe',speech)
    monkeypatch.setattr(voice,'download_audio',lambda *a:(b'mock-media','ogg','audio/ogg'))
    monkeypatch.setattr(voice,'validate_audio',lambda *a:a)
    monkeypatch.setattr(transport,'send_ai_reply',send)
    def inbound(kind='text',text='Salam'):
        mid=str(uuid.uuid4())
        db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,message_type,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming',%s,%s,clock_timestamp())",
                   (str(uuid.uuid4()),A,'connect:'+mid,'connect:'+CA,text,kind))
        db.commit()
        count=connect_ai.enqueue(A,channel_id=CA,conversation_id='client',lead_id=41,message_id=mid,event_id=mid,text=text,message_type=kind)
        return mid,count
    def process():
        job=runs.claim_next()
        if job: asyncio.run(worker.process_connect_one(job,Mock()))
        return job
    return dict(db=db,inbound=inbound,process=process,speech=speech,generate=generation,send=send,summary=summary_call)

def state(db):
    result=db.execute("SELECT state FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect'",(A,))[0]['state']
    db.commit()
    return result

@pytest.mark.parametrize('transcript',['Salam, qiyməti nə qədərdir?','Здравствуйте, какая цена?'])
def test_real_sql_text_voice_text_and_central_key(flow,transcript):
    f=flow
    f['inbound']();f['process']()
    f['speech'].return_value=(transcript,{'input_tokens':10,'output_tokens':3,'total_tokens':13})
    f['inbound']('audio','');f['process']()
    f['inbound']('text','More details');f['process']()
    assert f['send'].call_count==3
    f['speech'].assert_called_once()
    assert f['speech'].call_args.args[0]=='mock-platform-key'
    assert '[Audio transcript] '+transcript in str(f['generate'].call_args.args[3])
    assert state(f['db'])=='active'
    assert f['db'].execute("SELECT sum(reserved_calls) AS calls FROM saas_ai_agent_runs")[0]['calls']==4
    assert f['db'].execute("SELECT sum(total_tokens) AS tokens FROM saas_ai_agent_token_usage")[0]['tokens']==31
    assert not f['db'].execute("SELECT 1 FROM saas_tenant_integrations WHERE provider='kommo'")
    f['db'].commit()

def test_pilot_send_requires_fresh_exact_kwid_and_separate_owner_confirmation(flow,monkeypatch):
    from tenant_ai_pilot import _snapshot
    f=flow;db=f['db']
    monkeypatch.setenv('AI_AGENTS_PILOT_SCOPE_ENABLED','true')
    monkeypatch.setenv('AI_AGENTS_PILOT_TENANT_ID',A)
    monkeypatch.setenv('AI_AGENTS_PILOT_CHANNEL_ID',CA)
    monkeypatch.setenv('AI_AGENTS_PILOT_CONVERSATION_ID','client')
    f['inbound']();f['process']()
    assert f['send'].call_count==0  # absent attestation blocks at final send

    scope={'tenant_id':A,'channel_id':CA,'conversation_id':'client'}
    with db.cursor() as cur:
        _row,fingerprint=_snapshot(cur,A,scope)
        cur.execute('''INSERT INTO saas_ai_pilot_confirmations
            (tenant_id,channel_id,conversation_id,scope_fingerprint,kwid_confirmed_by,kwid_confirmed_at,kwid_expires_at)
            VALUES(%s::uuid,%s::uuid,%s,%s,11,clock_timestamp(),clock_timestamp()+interval '15 minutes')''',
            (A,CA,'client',fingerprint))
    db.commit()
    f['inbound']();f['process']()
    assert f['send'].call_count==0  # KWID confirmation alone cannot run the pilot

    db.execute('''UPDATE saas_ai_pilot_confirmations SET owner_approved_by=11,
        owner_approved_at=clock_timestamp(),owner_expires_at=kwid_expires_at
        WHERE tenant_id=%s::uuid''',(A,));db.commit()
    f['inbound']();f['process']()
    assert f['send'].call_count==1

    # A setting edit changes the live fingerprint; old approval is unusable.
    db.execute("UPDATE saas_ai_agent_settings SET updated_at=clock_timestamp()+interval '1 second' WHERE tenant_id=%s::uuid",(A,));db.commit()
    assert not __import__('tenant_ai_pilot').automatic_send_allowed(
        {'tenant_id':A,'channel_id':CA,'conversation_id':'client'})

def test_resume_only_new_messages_and_replayed_webhook(flow):
    f=flow;db=f['db']
    old,_=f['inbound']('audio','')
    agents.set_conversation_state(PROFILE,'client','paused','pause','connect',CA)
    paused,count=f['inbound']('audio','')
    assert count==0
    agents.set_conversation_state(PROFILE,'client','active','resume','connect',CA)
    assert f['process']() is None
    assert connect_ai.enqueue(A,channel_id=CA,conversation_id='client',lead_id=41,message_id=old,event_id='replay',text='',message_type='audio')==0
    f['inbound']();f['process']()
    f['speech'].assert_not_called()
    f['send'].assert_called_once()
    rows=connect_ai.history(A,41,CA)
    assert all(not r['voice_eligible'] for r in rows if r['external_id'] in {old,paused})
    f['inbound']('audio','');f['process']()
    f['speech'].assert_called_once()

def test_human_reply_during_transcription_cancels_generation_and_send(flow):
    f=flow
    def takeover(*args,**kwargs):
        connect_ai.mark_operator_reply(A,41,CA,'client')
        return 'Salam',{'total_tokens':4}
    f['speech'].side_effect=takeover
    f['inbound']('audio','');f['process']()
    f['generate'].assert_not_called();f['send'].assert_not_called()
    assert state(f['db'])=='human'
    assert not store.listing(PROFILE)['tickets']

def test_voice_handoff_creates_one_ticket_with_trusted_transcript_and_survives_summary_failure(flow):
    f=flow;db=f['db']
    f['speech'].return_value=('Мне нужен оператор, интернет не работает',{'total_tokens':7})
    f['summary'].side_effect=RuntimeError('mock failure')
    mid,_=f['inbound']('audio','');job=f['process']()
    ticket=store.listing(PROFILE)['tickets'][0]
    assert ticket['summary_state']=='unavailable'
    assert 'интернет' in ticket['description']
    assert '[Audio transcript]' in str(f['summary'].call_args.args[3])
    assert 'Audio unavailable' not in str(f['summary'].call_args.args[3])
    f['generate'].assert_not_called();f['send'].assert_not_called()
    assert state(db)=='human'
    store.update(PROFILE,ticket['id'],{'status':'resolved'})
    agents.set_conversation_state(PROFILE,'client','active','resume','connect',CA)
    assert not actions.handoff(job,'repeated old handoff')
    assert state(db)=='active'
    assert connect_ai.enqueue(A,channel_id=CA,conversation_id='client',lead_id=41,message_id=mid,event_id='retry',text='',message_type='audio')==0
    assert len(store.listing(PROFILE,status='all')['tickets'])==1

def test_reply_handoff_is_committed_before_final_callback_and_does_not_undo_resume(flow,monkeypatch):
    f=flow;db=f['db']
    monkeypatch.setattr(worker,'enabled_triggers',lambda *a:[{'trigger_type':'phrase','condition':{'phrase':'Salam'},'actions':['handoff_after_reply']}])
    callback=connect_ai.finish_sent_after_reply_handoff
    def inspect(job,reservation,message_id):
        assert state(db)=='human'
        assert len(store.listing(PROFILE)['tickets'])==1
        agents.set_conversation_state(PROFILE,'client','active','resume','connect',CA)
        assert callback(job,reservation,message_id)
        assert state(db)=='active'
        assert callback(job,reservation,message_id)
        assert state(db)=='active'
    monkeypatch.setattr(connect_ai,'finish_sent_after_reply_handoff',inspect)
    f['inbound']();f['process']()
    f['send'].assert_called_once()
    assert len(store.listing(PROFILE)['tickets'])==1

@pytest.mark.parametrize('run_state',['completed','superseded','unknown'])
def test_summary_budget_counts_all_reserved_voice_calls(db,run_state):
    seed_job(db);cipher=config_company(db,limit=2)
    db.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state,reserved_calls) VALUES(%s::uuid,%s::uuid,%s::uuid,'evt',1,'test-model',%s,2)",(A,str(uuid.uuid4()),AGENT,run_state));db.commit()
    with patch.object(summary,'_fernet',return_value=cipher),patch.object(summary,'generate') as generate:
        result=manual()
    generate.assert_not_called()
    assert result['ticket']['summary_state']=='unavailable'

def test_manual_handoff_retry_after_resolving_ticket_is_noop(flow):
    agents.set_conversation_state(PROFILE,'client','human','handoff','connect',CA)
    ticket=store.listing(PROFILE)['tickets'][0]['id']
    store.update(PROFILE,ticket,{'status':'resolved'})
    agents.set_conversation_state(PROFILE,'client','human','handoff','connect',CA)
    assert len(store.listing(PROFILE,status='all')['tickets'])==1

def test_same_jid_in_second_channel_fails_closed_and_never_sends(flow):
    f=flow;db=f['db']
    channel=str(uuid.uuid4())
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Second')",(A,channel))
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,'client',43)",(A,channel));db.commit()
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,state) VALUES(%s::uuid,'connect','client','human')",(A,));db.commit()
    assert not connect_ai.allowed({'provider':'connect','tenant_id':A,'channel_id':CA,'conversation_id':'client'})
    assert f['inbound']()[1]==0
    assert f['process']() is None
    f['send'].assert_not_called()
    with pytest.raises(Exception):
        agents.set_conversation_state(PROFILE,'client','human','handoff','connect',CA)

def test_restart_after_paid_voice_claim_does_not_repeat_call(flow):
    f=flow;db=f['db']
    mid,_=f['inbound']('audio','');job=runs.claim_next()
    reservation=runs.reserve_run(job,pipeline_id='',stage_id='',model='test-model',reserved_calls=2)
    source=next(r for r in connect_ai.history(A,41,CA) if r['external_id']==mid)
    assert voice._claim(job,reservation,source,'test-transcriber') is None
    db.execute("UPDATE saas_ai_agent_jobs SET lease_until=now()-interval '1 second'")
    db.execute("UPDATE saas_ai_agent_runs SET created_at=now()-interval '15 minutes'");db.commit()
    assert runs.claim_next() is None
    with pytest.raises(voice.VoiceError,match='voice_already_claimed'):
        voice._claim(job,reservation,source,'test-transcriber')
    f['inbound']();f['process']()
    f['speech'].assert_not_called()
    f['send'].assert_called_once()

@pytest.mark.parametrize('mode,company_send',[('manual',True),('automatic',False)])
def test_manual_mode_and_company_send_gate_never_send(flow,mode,company_send):
    f=flow;db=f['db']
    db.execute('UPDATE saas_ai_agents SET mode=%s',(mode,))
    db.execute('UPDATE saas_ai_agent_settings SET auto_send_enabled=%s',(company_send,))
    db.commit()
    f['inbound']('audio','');f['process']()
    if mode=='manual':
        f['speech'].assert_not_called()
        f['send'].assert_not_called()
        assert db.execute('SELECT count(*) AS n FROM saas_ai_agent_drafts')[0]['n']==0
        db.commit()
        return
    f['speech'].assert_not_called()
    f['send'].assert_not_called()
    assert db.execute('SELECT count(*) AS n FROM saas_ai_agent_drafts')[0]['n']==0
    db.commit()


def test_manual_mode_no_longer_generates_inbound_drafts(flow,monkeypatch):
    f=flow;db=f['db']
    db.execute("UPDATE saas_ai_agents SET mode='manual'");db.commit()
    monkeypatch.setattr(worker,'enabled_triggers',lambda *a:[{'trigger_type':'phrase','condition':{'phrase':'Salam'},'actions':['handoff_after_reply']}])
    f['inbound']();f['process']()
    f['speech'].assert_not_called()
    f['send'].assert_not_called()
    assert db.execute('SELECT count(*) AS n FROM saas_ai_agent_drafts')[0]['n']==0
    db.commit()

def test_migrations_are_idempotent_and_existing_voice_receipts_survive(flow):
    import tenant_ai_agents_schema as schema
    f=flow;db=f['db']
    f['inbound']('audio','');f['process']()
    before=db.execute('SELECT * FROM saas_connect_voice_receipts');db.commit()
    for _ in range(2):
        schema._ready=False
        schema.ensure_schema(db)
    assert db.execute('SELECT * FROM saas_connect_voice_receipts')==before
    db.commit()


def test_operator_webhook_and_human_mode_commit_together_and_retry_after_resume_is_noop(flow,monkeypatch):
    f=flow;db=f['db']
    f['inbound']('audio','')
    event={'id':'operator-event','type':'message.outbound.observed','data':{
        'id':str(uuid.uuid4()),'channelId':CA,'conversationId':'client','type':'text','text':'I will help'}}
    raw=json.dumps(event).encode()
    monkeypatch.setattr(transport,'_verify_signature',lambda *args:True)
    stop=connect_ai.mark_operator_reply
    def interrupted(*args,**kwargs):
        stop(*args,**kwargs)
        raise RuntimeError('simulated worker interruption before commit')
    with patch.object(connect_ai,'mark_operator_reply',side_effect=interrupted):
        with pytest.raises(RuntimeError):
            transport.ingest(raw,'mock-signature',event)
    assert state(db)=='active'
    assert not db.execute("SELECT 1 FROM saas_connect_webhook_events WHERE event_id='operator-event'")
    db.commit()
    assert transport.ingest(raw,'mock-signature',event)
    assert state(db)=='human'
    assert f['process']() is None
    agents.set_conversation_state(PROFILE,'client','active','resume','connect',CA)
    assert transport.ingest(raw,'mock-signature',event)
    assert state(db)=='active'
    f['send'].assert_not_called()


def test_connect_lead_control_resolves_provider_channel_and_enforces_tenant(flow):
    f=flow
    route=agents.read_conversation_for_lead(PROFILE,41)
    assert route['provider']=='connect'
    assert route['channel_id']==CA
    assert route['conversation_id']=='client'
    assert agents.read_conversation_for_lead({'tenant_id':B,'telegram_id':22},41) is None


def test_ambiguous_cross_provider_lead_control_fails_closed(flow):
    f=flow;db=f['db']
    db.execute("INSERT INTO saas_ai_agent_jobs(tenant_id,agent_id,provider,channel_id,conversation_id,lead_id,state,revision,event_fingerprint,talk_id,chat_id,due_at) SELECT tenant_id,agent_id,'kommo','other-channel','other-chat',lead_id,state,revision,event_fingerprint,talk_id,chat_id,due_at FROM saas_ai_agent_jobs LIMIT 1")
    db.commit()
    assert agents.read_conversation_for_lead(PROFILE,41) is None

    db.execute("DELETE FROM saas_ai_agent_jobs WHERE provider='connect'")
    db.commit()
    route=agents.read_conversation_for_lead(PROFILE,41)
    assert route['provider']=='kommo'
    assert route['conversation_id']=='other-chat'
