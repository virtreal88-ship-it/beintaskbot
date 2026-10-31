'''Release regression tests against isolated PostgreSQL/WASM; all remote APIs mocked.'''
import asyncio
import json
import uuid
from unittest.mock import Mock, patch, AsyncMock
import pytest
from test_tenant_support_inbox import (
    database, db, A, B, CA, CB, AGENT, PROFILE, seed_job, config_company,
    manual, store, summary, agents, actions, connect_ai,
)
import tenant_ai_agent_run_store as runs
import tenant_ai_agent_worker as worker
import tenant_connect_voice as voice
import tenant_connect_transport as transport
import tenant_episode_media as episode_media
import tenant_media_evidence as evidence
import tenant_connect_images as images
import tenant_knowledge_media as media_core
import tenant_chat_ai_provider as provider
import tenant_ai_spend_budget as spend_budget

@pytest.fixture
def flow(db, monkeypatch):
    from cryptography.fernet import Fernet
    cipher=Fernet(Fernet.generate_key())
    for module in (runs,voice,transport,episode_media,evidence,images,spend_budget):
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
    monkeypatch.setattr(voice,'validate_audio',lambda *a,**kw:a)
    # The common media executor uses the same real isolated SQL admission,
    # while every external media/provider boundary stays mocked.
    monkeypatch.setattr(provider,'transcribe',speech)
    observation=Mock(return_value=('Visible test observation',{'total_tokens':3}))
    monkeypatch.setattr(provider,'generate',observation)
    async def media_bytes(item,**kwargs):
        kind=item['row']['message_type']
        if kind in {'audio','voice'}:return b'mock-media','audio/ogg'
        if kind=='document':return b'Synthetic document','text/plain'
        if kind=='image':
            import base64
            return base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6cN8AAAAASUVORK5CYII='),'image/png'
        return b'mock-video','video/mp4'
    monkeypatch.setattr(media_core,'download_media',AsyncMock(side_effect=media_bytes))
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
    return dict(db=db,inbound=inbound,process=process,speech=speech,generate=generation,send=send,summary=summary_call,observation=observation)

def state(db):
    result=db.execute("SELECT state FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect'",(A,))[0]['state']
    db.commit()
    return result


def _signed_inbound_event(channel=CA, conversation='client'):
    message_id=str(uuid.uuid4())
    payload={'id':str(uuid.uuid4()),'type':'message.inbound.received',
        'occurred_at':'2026-10-30T12:00:00Z',
        'data':{'id':message_id,'channelId':channel,'conversationId':conversation,
            'type':'text','text':'Check the invoice amount','fromMe':False,
            'contactName':'Pilot test client'}}
    return message_id,payload


def test_startup_prepares_pilot_case_schema_repeatably_before_signed_ingress(flow,monkeypatch):
    from tenant_ai_agent_startup import prepare_database
    import tenant_platform_core
    f=flow;db=f['db']
    db.execute('DROP TABLE IF EXISTS saas_ai_pilot_followups');db.commit()
    assert not db.execute("SELECT to_regclass('saas_ai_pilot_followups') IS NOT NULL AS ready")[0]['ready']
    monkeypatch.setattr(transport,'_verify_signature',lambda *_:True)
    monkeypatch.setattr(tenant_platform_core,'_connect',lambda:db)
    monkeypatch.setattr(tenant_platform_core,'_fernet',transport._fernet)
    secret=transport._fernet().encrypt(json.dumps({'webhook_secret':'test-secret'}).encode())
    db.execute("UPDATE saas_tenant_integrations SET secrets=%s WHERE tenant_id=%s::uuid AND provider='connect'",(secret,A));db.commit()
    prepare_database();db.commit()
    prepare_database();db.commit()
    assert db.execute("SELECT to_regclass('saas_ai_pilot_followups') IS NOT NULL AS ready")[0]['ready']
    db.commit()

    monkeypatch.setenv('AI_AGENTS_PILOT_SCOPE_ENABLED','false')
    monkeypatch.setenv('AI_AGENTS_SCOPED_LIVE_ENABLED','false')
    monkeypatch.setenv('AI_AGENTS_ENABLED','true')
    monkeypatch.setenv('AI_SIMULATION_ENABLED','false')
    message_id,payload=_signed_inbound_event()
    assert transport.ingest(json.dumps(payload).encode(),'signed-test',payload)
    fingerprint=__import__('hashlib').sha256(('connect:'+message_id).encode()).hexdigest()
    assert db.execute("SELECT direction FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s",
        (A,'connect:'+message_id))[0]['direction']=='incoming'
    assert db.execute("SELECT state FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND event_fingerprint=%s",
        (A,fingerprint))[0]['state']=='queued'
    f['send'].assert_not_called()
    f['generate'].assert_not_called()
    db.commit()


def test_missing_optional_pilot_schema_keeps_signed_crm_receipt_and_fails_ai_closed(flow,monkeypatch):
    f=flow;db=f['db']
    db.execute('DROP TABLE IF EXISTS saas_ai_pilot_followups');db.commit()
    assert not db.execute("SELECT to_regclass('saas_ai_pilot_followups') IS NOT NULL AS ready")[0]['ready']
    def unavailable_schema(_conn):
        raise RuntimeError('simulated additive pilot schema failure')
    monkeypatch.setattr('tenant_ai_agent_startup.ensure_pilot_case_schema',unavailable_schema)
    monkeypatch.setattr(transport,'_verify_signature',lambda *_:True)
    secret=transport._fernet().encrypt(json.dumps({'webhook_secret':'test-secret'}).encode())
    db.execute("UPDATE saas_tenant_integrations SET secrets=%s WHERE tenant_id=%s::uuid AND provider='connect'",(secret,A));db.commit()
    monkeypatch.setenv('AI_AGENTS_ENABLED','true')
    monkeypatch.setenv('AI_SIMULATION_ENABLED','false')
    # Exercise the optional-schema failure branch as if the exact pilot gates
    # had admitted this scoped customer. No provider or model calls are made.
    import tenant_ai_pilot as pilot
    monkeypatch.setattr(pilot,'manual_live_job',lambda *_:True)
    monkeypatch.setattr(pilot,'processing_allowed',lambda *_a,**_k:True)
    monkeypatch.setattr(pilot,'is_pilot_customer_target',lambda *_:True)
    message_id,payload=_signed_inbound_event()
    assert transport.ingest(json.dumps(payload).encode(),'signed-test',payload)
    assert db.execute("SELECT direction FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s",
        (A,'connect:'+message_id))[0]['direction']=='incoming'
    fingerprint=__import__('hashlib').sha256(('connect:'+message_id).encode()).hexdigest()
    jobs=db.execute("SELECT state,provider,agent_id::text AS agent_id,conversation_id,error_code,event_fingerprint FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND event_fingerprint=%s",
        (A,fingerprint))
    assert not jobs, jobs
    assert not db.execute("SELECT 1 FROM saas_tenant_webhook_events WHERE tenant_id=%s::uuid AND fingerprint=%s",
        (A,fingerprint))
    f['send'].assert_not_called()
    f['generate'].assert_not_called()
    db.commit()

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
    assert transcript in str(f['generate'].call_args.args[3])
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


def _arm_synthetic_initial_pilot(db,monkeypatch):
    import tenant_ai_pilot as pilot
    scope={'tenant_id':A,'channel_id':CA,'conversation_id':'client'}
    monkeypatch.setenv('AI_AGENTS_PILOT_SCOPE_ENABLED','true')
    monkeypatch.setenv('AI_AGENTS_PILOT_TENANT_ID',A)
    monkeypatch.setenv('AI_AGENTS_PILOT_CHANNEL_ID',CA)
    monkeypatch.setenv('AI_AGENTS_PILOT_CONVERSATION_ID','client')
    monkeypatch.setattr(pilot,'scoped_live_enabled',lambda:True)
    db.execute("UPDATE saas_ai_agents SET mode='manual' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT))
    db.commit()
    monkeypatch.setattr(pilot,'manual_live_job',lambda job:
        job.get('provider')=='connect' and job.get('tenant_id')==A
        and job.get('channel_id')==CA and job.get('conversation_id')=='client')
    # Admission itself is separately covered by the pilot approval tests; keep
    # this exercise focused on actual worker execution and the DB send boundary.
    monkeypatch.setattr(pilot,'processing_allowed',lambda *_args,**_kwargs:True)
    with db.cursor() as cur:
        pilot.schema(cur)
        _row,fingerprint=pilot._snapshot(cur,A,scope)
        cur.execute('''INSERT INTO saas_ai_pilot_confirmations
          (tenant_id,channel_id,conversation_id,scope_fingerprint,kwid_confirmed_by,
           kwid_confirmed_at,kwid_expires_at,owner_approved_by,owner_approved_at,owner_expires_at)
          VALUES(%s::uuid,%s::uuid,%s,%s,11,clock_timestamp()-interval '1 minute',
           clock_timestamp()+interval '14 minutes',11,clock_timestamp()-interval '1 minute',
           clock_timestamp()+interval '14 minutes')
          ON CONFLICT(tenant_id) DO UPDATE SET channel_id=EXCLUDED.channel_id,
           conversation_id=EXCLUDED.conversation_id,scope_fingerprint=EXCLUDED.scope_fingerprint,
           kwid_confirmed_by=EXCLUDED.kwid_confirmed_by,kwid_confirmed_at=EXCLUDED.kwid_confirmed_at,
           kwid_expires_at=EXCLUDED.kwid_expires_at,owner_approved_by=EXCLUDED.owner_approved_by,
           owner_approved_at=EXCLUDED.owner_approved_at,owner_expires_at=EXCLUDED.owner_expires_at''',
          (A,CA,'client',fingerprint))
    db.commit()


def test_initial_parser_fallback_is_receipt_backed_and_duplicate_inbound_is_idempotent(flow,monkeypatch):
    f=flow;db=f['db'];_arm_synthetic_initial_pilot(db,monkeypatch)
    f['generate'].return_value=('not json',{'input_tokens':4,'output_tokens':2,'total_tokens':6})
    message_id,count=f['inbound']('text','The app still shows an unexpected value.')
    assert count==1, db.execute("SELECT state,error_code FROM saas_tenant_webhook_events WHERE tenant_id=%s::uuid ORDER BY received_at DESC LIMIT 1",(A,))
    job=f['process']()
    assert job and f['send'].call_count==1, db.execute("SELECT state,error_code FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid",(A,))
    body=db.execute("""SELECT body FROM saas_crm_messages WHERE tenant_id=%s::uuid
      AND channel=%s AND direction='outgoing' AND author_name='AI Agent'""",
      (A,'connect:'+CA))[0]['body']
    assert body not in ('not json','The app still shows an unexpected value.')
    assert 'rounding' not in body.casefold() and 'programmer' not in body.casefold()
    stored=db.execute("""SELECT result->>'send_state' AS send_state,
      result->>'pilot_initial_triage_rejection' AS rejection,error_code
      FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid ORDER BY created_at DESC LIMIT 1""",(A,))[0]
    assert stored=={'send_state':'accepted','rejection':'pilot_initial_invalid_json','error_code':''}
    import tenant_ai_reliability as reliability
    mapped=reliability._message({'message_id':message_id,'provider':'connect','sent':True,
        'job_state':'sent','run_state':'completed','triage_rejection_code':stored['rejection']})
    assert mapped['status']=='sent'
    assert mapped['diagnostic_code']=='pilot_initial_invalid_json'
    assert mapped['diagnostic']=='Ответ модели для начального разбора отклонён: некорректный JSON'
    replay=connect_ai.enqueue(A,channel_id=CA,conversation_id='client',lead_id=41,
        message_id=message_id,event_id=message_id,text='The app still shows an unexpected value.',message_type='text')
    assert replay==0
    assert f['process']() is None
    assert f['send'].call_count==1
    assert db.execute("""SELECT count(*) AS n FROM saas_crm_messages WHERE tenant_id=%s::uuid
      AND channel=%s AND direction='outgoing' AND author_name='AI Agent'""",
      (A,'connect:'+CA))[0]['n']==1
    db.commit()


def test_initial_parser_fallback_is_blocked_by_database_human_takeover_revision(flow,monkeypatch):
    import tenant_ai_agent_connect as connect
    f=flow;db=f['db'];_arm_synthetic_initial_pilot(db,monkeypatch)
    def take_over_during_generation(*_args,**_kwargs):
        connect.mark_operator_reply(A,41,CA,'client')
        return 'not json',{'input_tokens':4,'output_tokens':2,'total_tokens':6}
    f['generate'].side_effect=take_over_during_generation
    f['inbound']('text','The app still shows an unexpected value.')
    before=db.execute("SELECT revision FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND channel_id=%s AND conversation_id='client'",(A,CA))[0]['revision']
    job=f['process']()
    assert job
    assert f['send'].call_count==0
    assert state(db)=='human'
    after=db.execute("SELECT revision FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND channel_id=%s AND conversation_id='client'",(A,CA))[0]['revision']
    assert after>before
    saved=db.execute("SELECT state,error_code FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect'",
        (A,))[0]
    assert saved['state']=='skipped'
    db.commit()

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
    assert 'Мне нужен оператор, интернет не работает' in str(f['summary'].call_args.args[3])
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


def test_newest_message_without_provider_timestamp_uses_created_at_for_current_turn(flow):
    f=flow;db=f['db']
    f['inbound']('text','Earlier timestamped message')
    mid,_=f['inbound']('text','Latest without provider timestamp')
    db.execute('UPDATE saas_crm_messages SET happened_at=NULL WHERE tenant_id=%s::uuid AND external_id=%s',(A,'connect:'+mid));db.commit()
    history=connect_ai.history(A,41,CA)
    assert history[-1]['external_id']==mid and history[-1]['happened_at']
    f['process']()
    f['send'].assert_called_once()
    assert f['generate'].call_args.kwargs['extra_context']['latest_client_message_id']==mid


@pytest.mark.parametrize('phase',['analysis','generation'])
def test_same_id_media_edit_keeps_paid_old_receipt_but_never_sends_stale_reply(flow,phase):
    f=flow;db=f['db']
    db.execute("UPDATE saas_ai_agents SET model='gpt-4o-mini' WHERE tenant_id=%s::uuid",(A,));db.commit()
    mid,_=f['inbound']('image','')
    def changed(response):
        def invoke(*args,**kwargs):
            db.execute("UPDATE saas_crm_messages SET raw='{\"mediaId\":\"replacement\"}' WHERE tenant_id=%s::uuid AND external_id=%s",(A,'connect:'+mid));db.commit()
            return response
        return invoke
    target=f['observation'] if phase=='analysis' else f['generate']
    target.side_effect=changed(target.return_value)
    f['process']()
    f['observation'].assert_called_once();f['send'].assert_not_called()
    if phase=='analysis':f['generate'].assert_not_called()
    else:f['generate'].assert_called_once()
    saved=db.execute('SELECT status,result FROM saas_media_evidence_receipts WHERE tenant_id=%s::uuid AND external_id=%s',(A,'connect:'+mid))
    assert len(saved)==1 and saved[0]['status']=='processed'
    assert 'Visible test observation' in str(saved[0]['result'])
    assert not db.execute("SELECT 1 FROM saas_episode_media_flights WHERE tenant_id=%s::uuid AND external_id=%s AND state='running'",(A,'connect:'+mid))
    db.commit()
