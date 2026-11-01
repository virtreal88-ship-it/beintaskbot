"""Assigned-agent private drafts using isolated PostgreSQL/WASM; no paid calls."""
import json
import uuid
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import tenant_operator_copilot as copilot
import tenant_ai_agents_store as agents
from tenant_linear_policy import TenantLinearError
from test_tenant_support_inbox import database, db, A, B, CA, AGENT, PROFILE, seed_job


@pytest.fixture
def ready(db,monkeypatch):
    seed_job(db)
    other = str(uuid.uuid4())
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,instructions,model) VALUES(%s::uuid,%s::uuid,'Other support',11,'OTHER_AGENT_RULES','other-agent-model')",(A,other))
    db.execute("UPDATE saas_ai_agents SET instructions='ASSIGNED_AGENT_RULES',model='agent-model' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT))
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,secrets,metadata) VALUES(%s::uuid,'chat_ai',%s,%s::jsonb)",
        (A,b'synthetic-cipher',json.dumps({'enabled':True,'consent':True,'copilot_enabled':True,'copilot_model':'configured-copilot-model','daily_limit':100})))
    from tenant_chat_send_schema import ensure_schema as send_schema
    send_schema(db);db.commit()
    monkeypatch.setattr(copilot,'_connect',lambda:db)
    monkeypatch.setattr('tenant_episode_media._connect',lambda:db)
    monkeypatch.setattr(copilot,'_fernet',lambda:SimpleNamespace(decrypt=lambda _:json.dumps({'tenant_id':A,'provider':'chat_ai','api_key':'synthetic-test-key'}).encode()))
    generated = Mock(return_value=('Müştəri üçün hazır cavab.',{'total_tokens':10}))
    monkeypatch.setattr(copilot,'generate',generated)
    data = {'provider':'connect','mode':'reply','prompt':'','request_id':str(uuid.uuid4()),
        'channel_id':CA,'conversation_id':'client','lead_id':41}
    return db,other,generated,data


def test_reply_uses_assigned_instructions_scoped_knowledge_latest_context_and_cached_manual_draft(ready):
    db,other,generated,data = ready
    for agent,title,content in ((AGENT,'Assigned','Internet ASSIGNED_KNOWLEDGE'),
            (other,'Other','Internet FOREIGN_AGENT_KNOWLEDGE'),
            (None,'Company','Internet SHARED_COMPANY_KNOWLEDGE')):
        agents.save_knowledge(PROFILE,{'agent_id':agent,'title':title,'kind':'text','content':content,'active':True})
    for index,(direction,body) in enumerate((('outgoing','We are checking the connection.'),('incoming','Internet is still down?'))):
        db.execute("INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,%s,%s,now()+(%s * interval '1 second'))",
            (A,str(uuid.uuid4()),'latest-'+str(index),'connect:'+CA,direction,body,index+1))
    db.commit()
    result = copilot.run(PROFILE,data)
    assert copilot.run(PROFILE,data) == result
    generated.assert_called_once()
    args,kwargs = generated.call_args
    assert args[1] == 'agent-model'
    assert args[3][-1]['text'] == 'Internet is still down?'
    context = kwargs['extra_context']
    assert context['assigned_agent_id'] == AGENT
    assert context['assigned_agent_instructions'] == 'ASSIGNED_AGENT_RULES'
    knowledge = json.dumps(context['verified_knowledge'])
    assert 'ASSIGNED_KNOWLEDGE' in knowledge and 'SHARED_COMPANY_KNOWLEDGE' in knowledge
    assert 'FOREIGN_AGENT_KNOWLEDGE' not in knowledge
    assert db.execute('SELECT count(*) AS n FROM saas_chat_send_receipts')[0]['n'] == 0
    assert 'learning' not in result


@pytest.mark.parametrize('prompt',['Попроси AnyDesk','Проверили после подключения: причина в интернете; подготовь ответ клиенту.'])
def test_private_employee_command_returns_text_without_sending_or_learning(ready,prompt):
    db,other,generated,data = ready
    data.update(mode='chat',prompt=prompt)
    generated.return_value = ('Zəhmət olmasa AnyDesk kodunu göndərin.',{'total_tokens':10})
    result = copilot.run(PROFILE,data)
    assert result['text'] == 'Zəhmət olmasa AnyDesk kodunu göndərin.'
    assert generated.call_args.kwargs['extra_context']['assigned_agent_instructions'] == 'ASSIGNED_AGENT_RULES'
    assert db.execute('SELECT count(*) AS n FROM saas_chat_send_receipts')[0]['n'] == 0
    assert db.execute('SELECT count(*) AS n FROM saas_ai_knowledge_sources')[0]['n'] == 0
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_candidates')[0]['n'] == 0


def test_no_assignment_blocks_generation_and_reservation(ready):
    db,other,generated,data = ready
    db.execute("UPDATE saas_ai_conversation_state SET agent_id=NULL WHERE tenant_id=%s::uuid",(A,));db.commit()
    with pytest.raises(TenantLinearError):
        copilot.run(PROFILE,data)
    generated.assert_not_called()
    assert db.execute('SELECT count(*) AS n FROM saas_chat_ai_runs')[0]['n'] == 0


@pytest.mark.parametrize('change',['assignment','instructions'])
def test_inflight_assignment_or_instruction_changes_hide_stale_draft(ready,change):
    db,other,generated,data = ready
    def respond(*args,**kwargs):
        if change == 'assignment':
            db.execute("UPDATE saas_ai_conversation_state SET agent_id=%s::uuid,revision=revision+1 WHERE tenant_id=%s::uuid",(other,A))
        else:
            db.execute("UPDATE saas_ai_agents SET instructions='CHANGED',version=version+1 WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT))
        db.commit()
        return 'stale draft',{'total_tokens':10}
    generated.side_effect = respond
    with pytest.raises(TenantLinearError) as caught:
        copilot.run(PROFILE,data)
    assert caught.value.status == 409
    generated.assert_called_once()
    assert db.execute('SELECT count(*) AS n FROM saas_chat_send_receipts')[0]['n'] == 0


def test_reassignment_excludes_old_agent_private_turns_and_cached_result(ready):
    db,other,generated,data = ready
    data.update(mode='chat',prompt='Explain this case privately')
    copilot.run(PROFILE,data)
    db.execute("UPDATE saas_ai_conversation_state SET agent_id=%s::uuid,revision=revision+1 WHERE tenant_id=%s::uuid",(other,A));db.commit()
    with pytest.raises(TenantLinearError):
        copilot.run(PROFILE,data)
    assert generated.call_count == 1
    data['request_id'] = str(uuid.uuid4())
    copilot.run(PROFILE,data)
    context = generated.call_args.kwargs['extra_context']
    assert context['assigned_agent_id'] == other
    assert context['assigned_agent_instructions'] == 'OTHER_AGENT_RULES'
    assert context['prior_internal_copilot_turns'] == []


def test_other_tenant_cannot_use_dialog_context(ready):
    db,other,generated,data = ready
    with pytest.raises(TenantLinearError):
        copilot.run({'tenant_id':B,'telegram_id':22},data)
    generated.assert_not_called()


@pytest.mark.parametrize('interrupt',[False,True,'media_version','body','answered'])
@pytest.mark.parametrize('kind',['voice','audio','image','document','video'])
def test_manual_reply_processes_current_media_then_auto_reuses_same_exact_receipt_without_send(ready,monkeypatch,kind,interrupt,burst=False):
    from unittest.mock import AsyncMock
    import tenant_episode_media as media
    import tenant_knowledge_media as core
    import tenant_chat_ai_provider as provider
    from test_connect_images import PNG
    db,other,generated,data=ready
    config={'enabled':True,'consent':True,'copilot_enabled':True,'copilot_model':'gpt-4o-mini',
            'daily_limit':100,'audio_enabled':True,'transcription_model':'whisper-1',
            'video_enabled':True,'video_model':'gpt-4o-mini'}
    db.execute("UPDATE saas_tenant_integrations SET metadata=%s::jsonb WHERE tenant_id=%s::uuid AND provider='chat_ai'",(json.dumps(config),A))
    db.execute("UPDATE saas_ai_agents SET model='gpt-4o-mini' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT))
    source='connect:'+str(uuid.uuid4())
    db.execute('''INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,raw,happened_at)
      VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','',%s,'{"mediaId":"version1"}',now()+interval '2 seconds')''',
      (A,str(uuid.uuid4()),source,'connect:'+CA,kind));db.commit()
    sources=[source]
    if burst:
        for external,direction,message_kind,seconds in [('resolved-old','incoming','voice',-2),('resolved-answer','outgoing','text',-1),('between-voices','incoming','text',3),('voice-two','incoming','voice',4),('between-more','incoming','text',5),('voice-three','incoming','voice',6)]:
            external='connect:'+external
            db.execute("INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,raw,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,%s,'Interleaved context',%s,'{\"mediaId\":\"version1\"}',now()+(%s * interval '1 second'))",(A,str(uuid.uuid4()),external,'connect:'+CA,direction,message_kind,seconds))
            if message_kind=='voice' and seconds>0:sources.append(external)
        db.commit()
    monkeypatch.setattr(media,'_connect',lambda:db)
    monkeypatch.setattr('tenant_media_evidence._connect',lambda:db)
    monkeypatch.setattr('tenant_ai_spend_budget._connect',lambda:db)
    def loaded(session,lead,external,**scope):
        assert session['tenant_id']==A and lead==41 and external in sources
        assert scope=={'channel_id':CA,'conversation_id':'client'}
        current=db.execute('SELECT external_id,body,raw,direction,message_type,channel FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s',(A,external))[0]
        db.commit()
        return {'provider':'connect','route':{'channel_id':CA,'conversation_id':'client'},'row':current}
    monkeypatch.setattr('tenant_chat_attachment_store.load',loaded)
    mime={'voice':'audio/wav','audio':'audio/wav','image':'image/png','document':'text/plain','video':'video/mp4'}[kind]
    download=AsyncMock(return_value=(PNG,mime));monkeypatch.setattr(core,'download_media',download)
    monkeypatch.setattr('tenant_connect_voice.validate_audio',lambda data,*a,**kw:(data,'wav','audio/wav'))
    monkeypatch.setattr(core,'extract',lambda *a:('attachment','text','Sənəd mənbəyi'))
    monkeypatch.setattr(core,'video_frames',lambda *a:[(PNG,'image/png',{'second':0})])
    monkeypatch.setattr(core,'video_audio',lambda *a:(b'audio','wav','audio/wav'))
    monkeypatch.setattr('tenant_ai_video_model.require_video_tariff',Mock())
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:1000000}))
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({'gpt-4o-mini':{'vision_low':2},'whisper-1':{'asr':2}}))
    visual=Mock(return_value=('Visible E17',{}));speech=Mock(return_value=('Səs mənbəyi',{}))
    video_speech=Mock(return_value=('Video mənbəyi',{}, {'speech_state':'confident','language':'az'}))
    monkeypatch.setattr(provider,'generate',visual);monkeypatch.setattr(provider,'transcribe',speech)
    monkeypatch.setattr(provider,'transcribe_video',video_speech)
    from tenant_ai_agent_run_store import knowledge_context as actual_knowledge
    retrieve=Mock(side_effect=actual_knowledge)
    monkeypatch.setattr('tenant_ai_agent_run_store.knowledge_context',retrieve)
    if interrupt=='answered':
        db.execute('''INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,happened_at)
          VALUES(%s::uuid,%s::uuid,41,'employee-after-media',%s,'outgoing','Earlier employee answer',now()+(%s * interval '1 second'))''',
          (A,str(uuid.uuid4()),'connect:'+CA,7 if burst else 3));db.commit()
    if interrupt and interrupt!='answered' and kind!='document':
        fired=[]
        def newer(response):
            def invoke(*args,**kwargs):
                if not fired:
                    fired.append(True)
                    if interrupt=='media_version':
                        db.execute("UPDATE saas_crm_messages SET raw='{\"mediaId\":\"replacement\"}' WHERE tenant_id=%s::uuid AND external_id=%s",(A,source))
                    elif interrupt=='body':
                        db.execute("UPDATE saas_crm_messages SET body='edited caption' WHERE tenant_id=%s::uuid AND external_id=%s",(A,source))
                    else:
                        db.execute('''INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,happened_at)
                          VALUES(%s::uuid,%s::uuid,41,'after-paid',%s,'incoming','New detail',now()+interval '3 seconds')''',
                          (A,str(uuid.uuid4()),'connect:'+CA))
                    db.commit()
                return response
            return invoke
        for provider_mock in (visual,speech,video_speech):provider_mock.side_effect=newer(provider_mock.return_value)
        with pytest.raises(TenantLinearError):copilot.run(PROFILE,data)
        generated.assert_not_called()
        assert db.execute("SELECT state FROM saas_episode_media_flights WHERE tenant_id=%s::uuid AND external_id=%s",(A,source))[0]['state']!='running'
        assert db.execute("SELECT count(*) AS n FROM saas_media_evidence_receipts WHERE tenant_id=%s::uuid AND external_id=%s",(A,source))[0]['n']==1
        # An invalidated request cannot dispatch again. Restore only the
        # synthetic source to prove the old exact-version receipt remains free.
        before=(visual.call_count,speech.call_count,video_speech.call_count)
        with pytest.raises(TenantLinearError):copilot.run(PROFILE,data)
        assert (visual.call_count,speech.call_count,video_speech.call_count)==before
        db.execute("UPDATE saas_crm_messages SET raw='{\"mediaId\":\"version1\"}',body='' WHERE tenant_id=%s::uuid AND external_id=%s",(A,source))
        db.execute("DELETE FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id='after-paid'",(A,));db.commit()
        before=(visual.call_count,speech.call_count,video_speech.call_count)
        data['request_id']=str(uuid.uuid4())
        copilot.run(PROFILE,data)
        assert (visual.call_count,speech.call_count,video_speech.call_count)==before
        assert db.execute('SELECT count(*) AS n FROM saas_chat_send_receipts')[0]['n']==0
        return
    first=copilot.run(PROFILE,data)
    if interrupt=='answered':
        assert copilot.run(PROFILE,data)==first
        generated.assert_called_once()
    query=retrieve.call_args.args[1]
    expected={'voice':'Səs mənbəyi','audio':'Səs mənbəyi','image':'Visible E17','document':'Sənəd mənbəyi','video':'Video mənbəyi'}[kind]
    assert expected in query
    assert retrieve.call_args.kwargs['agent_id']==AGENT
    assert first['context_usage']['evidence'][0]['source_id']==source
    assert first['context_usage']['evidence'][0]['status']==('partial' if kind=='video' else 'processed')
    assert generated.call_args.kwargs['extra_context']['assigned_agent_id']==AGENT
    if burst:
        assert speech.call_count==3
        assert {item['source_id'] for item in first['context_usage']['evidence']}==set(sources)
        assert set(sources)<={item['source_id'] for item in generated.call_args.args[3]}
    before=(visual.call_count,speech.call_count,video_speech.call_count)
    db.execute("UPDATE saas_crm_messages SET body='edited after completed draft' WHERE tenant_id=%s::uuid AND external_id=%s",(A,source));db.commit()
    with pytest.raises(TenantLinearError):copilot.run(PROFILE,data)
    assert (visual.call_count,speech.call_count,video_speech.call_count)==before
    db.execute("UPDATE saas_crm_messages SET body='' WHERE tenant_id=%s::uuid AND external_id=%s",(A,source));db.commit()
    data['request_id']=str(uuid.uuid4())
    second=copilot.run(PROFILE,data)
    assert second['text']==first['text']
    assert (visual.call_count,speech.call_count,video_speech.call_count)==before
    auto=media.AutoExecution({'tenant_id':A,'channel_id':CA,'conversation_id':'client','lead_id':41},
        {'model':'gpt-4o-mini','api_key':'mock-key','config':config,'agent_language':'az','video_model':'gpt-4o-mini','video_api_key':'mock-key'})
    # Real lease rejection is covered separately; this is an admitted worker's
    # read-only use of evidence produced by the actual manual request above.
    auto.check=Mock()
    auto.load=lambda profile,lead,external:loaded(PROFILE,lead,external,channel_id=CA,conversation_id='client')
    current=db.execute('SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s',(A,source));db.commit()
    media.prepare(auto,current)
    assert current[0]['_episode_receipt']['reused'] is True
    assert (visual.call_count,speech.call_count,video_speech.call_count)==before
    assert download.await_count==(3 if burst else 1)
    assert db.execute('SELECT count(*) AS n FROM saas_chat_send_receipts')[0]['n']==0
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_candidates')[0]['n']==0
    db.commit()


def test_media_preparation_failure_finishes_manual_run_without_main_call_or_send(ready,monkeypatch):
    db,other,generated,data=ready
    monkeypatch.setattr(copilot,'_prepare_copilot_media',Mock(side_effect=TenantLinearError('still running',409)))
    with pytest.raises(TenantLinearError):copilot.run(PROFILE,data)
    generated.assert_not_called()
    assert db.execute('SELECT status FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(A,data['request_id']))[0]['status']=='unknown'
    assert db.execute('SELECT count(*) AS n FROM saas_chat_send_receipts')[0]['n']==0


def test_new_inbound_during_generation_hides_stale_draft(ready):
    db,other,generated,data=ready
    def arrived(*args,**kwargs):
        db.execute('''INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,happened_at)
          VALUES(%s::uuid,%s::uuid,41,'latest-new-inbound',%s,'incoming','New detail',now()+interval '2 seconds')''',
          (A,str(uuid.uuid4()),'connect:'+CA));db.commit()
        return 'stale answer',{}
    generated.side_effect=arrived
    with pytest.raises(TenantLinearError):copilot.run(PROFILE,data)
    generated.assert_called_once()
    assert db.execute('SELECT count(*) AS n FROM saas_chat_send_receipts')[0]['n']==0


def test_reply_cache_without_source_versions_fails_closed_without_model_retry(ready):
    db,other,generated,data=ready
    copilot.run(PROFILE,data)
    db.execute("UPDATE saas_chat_ai_runs SET result=result-'source_versions' WHERE tenant_id=%s::uuid AND request_id=%s::uuid",(A,data['request_id']));db.commit()
    with pytest.raises(TenantLinearError) as error:copilot.run(PROFILE,data)
    assert error.value.keep_request is True
    generated.assert_called_once()


def test_cached_reply_is_hidden_after_new_inbound_without_repeating_model(ready):
    db,other,generated,data=ready
    copilot.run(PROFILE,data)
    db.execute('''INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,happened_at)
      VALUES(%s::uuid,%s::uuid,41,'after-cached',%s,'incoming','New request',now()+interval '2 seconds')''',
      (A,str(uuid.uuid4()),'connect:'+CA));db.commit()
    with pytest.raises(TenantLinearError):copilot.run(PROFILE,data)
    generated.assert_called_once()


def test_manual_kb_uses_customer_evidence_after_incoming_employee_reply(ready,monkeypatch):
    db,other,generated,data=ready
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,raw,happened_at) VALUES(%s::uuid,%s::uuid,41,'staff-latest',%s,'incoming','STAFF_BODY','{\"verified_employee_id\":11}',now()+interval '1 second')",(str(uuid.uuid4()),A,'connect:'+CA))
    db.commit()
    def prepared(tenant,actor,command,rows,model,**kwargs):
        customer=next(row for row in rows if row['external_id']=='m1')
        customer['_manual_request_source']=True
        customer['_copilot_attachment_text']='CUSTOMER_TRANSCRIPT'
        return {'images':[],'voice_transcriptions':set(),'documents':set(),'skipped':[]}
    monkeypatch.setattr(copilot,'_prepare_copilot_media',prepared)
    lookup=Mock(return_value=[])
    monkeypatch.setattr('tenant_ai_agent_run_store.knowledge_context',lookup)
    copilot.run(PROFILE,data)
    assert 'CUSTOMER_TRANSCRIPT' in lookup.call_args.args[1]
    assert 'STAFF_BODY' not in lookup.call_args.args[1]



def test_three_voice_customer_request_after_staff_answer_reuses_all_receipts(ready,monkeypatch):
    test_manual_reply_processes_current_media_then_auto_reuses_same_exact_receipt_without_send(
        ready,monkeypatch,'voice','answered',burst=True)
def test_async_consultation_uses_current_assigned_agent_and_long_original_evidence(ready):
    import tenant_dialog_consultations as consultations
    db,other,generated,data=ready
    consultations.schema(db)
    consult=str(uuid.uuid4());reply=str(uuid.uuid4())
    db.execute("UPDATE saas_ai_agents SET language='az' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT))
    db.execute("""INSERT INTO saas_internal_consultations(tenant_id,id,case_id,channel_id,conversation_id,request_key,reason,status,created_by)
      VALUES(%s::uuid,%s::uuid,NULL,%s::uuid,'client','manual-consult','Printer','waiting_specialist',11)""",(A,consult,CA))
    db.execute("""INSERT INTO saas_dialog_consultation_replies(tenant_id,id,consultation_id,channel_id,conversation_id,message_id,author,body,happened_at)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,'developers','quote','Developer',%s,now())""",(A,reply,consult,CA,'Long developer evidence '+('x'*5000)))
    db.commit()
    original=db.execute("SELECT id::text,external_id,body,message_type,direction,raw,COALESCE(happened_at,created_at) AS happened_at FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=41 AND channel=%s",(A,'connect:'+CA))
    selected=[row['id'] for row in original]
    db.execute('''INSERT INTO saas_public_case_shares(id,tenant_id,ticket_id,consultation_id,channel_id,conversation_id,group_channel_id,group_conversation_id,
      token_hash,selected_message_ids,public_messages,source_versions,expires_at)
      VALUES(%s::uuid,%s::uuid,NULL,%s::uuid,%s::uuid,'client',%s::uuid,'client',%s,%s::jsonb,'[]'::jsonb,%s::jsonb,now()+interval '1 day')''',
      (uuid.uuid4(),A,consult,CA,CA,str(uuid.uuid4()),json.dumps(selected),json.dumps({row['id']:consultations.version(row) for row in original})))
    db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,happened_at)
      VALUES(%s::uuid,%s::uuid,41,'NEW-INCIDENT',%s,'incoming','Completely different new issue',now()+interval '1 day')''',(uuid.uuid4(),A,'connect:'+CA));db.commit()
    copilot.run(PROFILE,{**data,'mode':'instruction','prompt':'Подготовь ответ клиенту'},consultation_reply_id=reply)
    context=generated.call_args.kwargs['extra_context']
    assert 'Completely different new issue' not in str(generated.call_args.args)
    assert context['untrusted_developer_consultation_evidence']['original_summary']=='Printer'
    assert context['prior_internal_copilot_turns']==[]
    assert context['language_preference']=='az'
    assert context['assigned_agent_id']==AGENT
    assert context['assigned_agent_instructions']=='ASSIGNED_AGENT_RULES'
    assert len(context['untrusted_developer_consultation_evidence']['body'])>5000
    assert db.execute('SELECT count(*) AS n FROM saas_support_tickets')[0]['n']==0
    assert db.execute('SELECT count(*) AS n FROM saas_chat_send_receipts')[0]['n']==0
    with pytest.raises(TenantLinearError):copilot.run(PROFILE,{**data,'request_id':str(uuid.uuid4())},consultation_reply_id=str(uuid.uuid4()))


@pytest.mark.parametrize('status,code,stage',[
    ('error','ai_spend_media_price_unknown','audio_transcription'),
    ('error','ai_spend_budget_exhausted','audio_transcription'),
    ('error','ai_call_limit_exhausted','audio_transcription'),
    ('error','media_authority_changed','audio_transcription'),
    ('error','source_access_denied','audio_transcription'),
    ('unsupported','media_unavailable','media_download'),
    ('error','media_processing_failed','media_validation'),
    ('unknown','media_result_unknown','audio_transcription'),
    ('unavailable','legacy_voice_version_unverified',None),
    ('partial','transcript_truncated',None),
    ('processed','transcribed',None),
])
@pytest.mark.parametrize('kind',['audio','voice'])
def test_latest_unread_audio_never_generates_old_topic_or_queries_stale_body(ready,monkeypatch,status,code,stage,kind):
    db,other,generated,data=ready
    monkeypatch.setattr('tenant_episode_media._connect',lambda:db)
    source='connect:'+str(uuid.uuid4())
    db.execute('''INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,raw,happened_at)
      VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','OLD_TOPIC_UNVERIFIED_BODY','voice','{"mediaId":"latest-not-old"}',now()+interval '2 seconds')''',
      (A,str(uuid.uuid4()),source,'connect:'+CA))
    db.execute('UPDATE saas_crm_messages SET message_type=%s WHERE tenant_id=%s::uuid AND external_id=%s',(kind,A,source));db.commit()
    def failed_media(_tenant,_actor,_command,rows,_model,**kw):
        latest=rows[-1];latest['_manual_request_source']=True;latest['_copilot_context_body']=''
        latest['_episode_receipt']={'status':status,'code':code,**({'failure_stage':stage} if stage else {})}
        from tenant_media_admission import REASONS
        if code in REASONS:latest['_episode_receipt']['admission_status']=429 if code in {'ai_spend_budget_exhausted','ai_call_limit_exhausted'} else 403 if code=='source_access_denied' else 409
        from tenant_episode_media import reference
        kw['execution'].source_refs=[reference(latest,CA,41)]
        # A processed receipt without actual speech is not sufficient evidence.
        if status=='partial':latest['_episode_receipt']['transcript']='only part of the speech'
        return {'images':[],'voice_transcriptions':set(),'documents':set(),'skipped':[{'source_id':source,'kind':'voice','reason':code}]}
    monkeypatch.setattr(copilot,'_prepare_copilot_media',failed_media)
    knowledge=Mock(return_value=[]);monkeypatch.setattr('tenant_ai_agent_run_store.knowledge_context',knowledge)
    with pytest.raises(TenantLinearError) as caught:copilot.run(PROFILE,data)
    assert caught.value.status==(429 if code in {'ai_spend_budget_exhausted','ai_call_limit_exhausted'} else 403 if code=='source_access_denied' else 409)
    from tenant_media_admission import explanation
    if explanation(code,'ru'):assert explanation(code,'ru') in str(caught.value)
    assert 'OLD_TOPIC' not in str(caught.value)
    assert (stage or 'unknown') in str(caught.value)
    generated.assert_not_called();knowledge.assert_not_called()
    assert db.execute('SELECT status FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(A,data['request_id']))[0]['status']=='unknown'
    assert db.execute('SELECT count(*) AS n FROM saas_chat_send_receipts')[0]['n']==0
    # Same request stays quarantined instead of causing an implicit paid replay.
    with pytest.raises(TenantLinearError):copilot.run(PROFILE,data)
    generated.assert_not_called()


def test_newer_text_question_is_not_blocked_by_an_older_failed_voice(ready,monkeypatch):
    db,other,generated,data=ready
    monkeypatch.setattr('tenant_episode_media._connect',lambda:db)
    for index,(kind,body) in enumerate([('audio','OLD_VOICE_TOPIC'),('text','CURRENT_TEXT_QUESTION')],1):
        db.execute('''INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,raw,happened_at)
          VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming',%s,%s,'{}',now()+(%s * interval '1 second'))''',
          (A,str(uuid.uuid4()),'connect:'+str(uuid.uuid4()),'connect:'+CA,body,kind,index))
    db.commit()
    def prepared(_tenant,_actor,_command,rows,_model,**kw):
        from tenant_episode_media import reference
        for row in rows[-2:]:
            row['_manual_request_source']=True
            if row['message_type']=='audio':
                row['_copilot_context_body']='';row['_episode_receipt']={'status':'unsupported','code':'media_unavailable'}
        kw['execution'].source_refs=[reference(rows[-1],CA,41),reference(rows[-2],CA,41)]
        return {'images':[],'voice_transcriptions':set(),'documents':set(),'skipped':[]}
    monkeypatch.setattr(copilot,'_prepare_copilot_media',prepared)
    knowledge=Mock(return_value=[]);monkeypatch.setattr('tenant_ai_agent_run_store.knowledge_context',knowledge)
    result=copilot.run(PROFILE,data)
    generated.assert_called_once()
    assert result['text'] and 'CURRENT_TEXT_QUESTION' in knowledge.call_args.args[1]
    assert 'OLD_VOICE_TOPIC' not in knowledge.call_args.args[1]
    assert 'OLD_VOICE_TOPIC' not in str(generated.call_args.args[3])
    assert db.execute('SELECT count(*) AS n FROM saas_chat_send_receipts')[0]['n']==0

@pytest.mark.parametrize('mode',['reply','chat','instruction','solution'])
def test_every_private_dialog_action_uses_assigned_model_not_company_copilot(ready,mode):
    db,other,generated,data=ready
    # A stale/empty company model must not replace the agent's configured one.
    db.execute("UPDATE saas_tenant_integrations SET metadata=jsonb_set(metadata,'{copilot_model}','\"\"'::jsonb) WHERE tenant_id=%s::uuid AND provider='chat_ai'",(A,));db.commit()
    data.update(mode=mode,prompt='Explain the current request' if mode!='reply' else '')
    copilot.run(PROFILE,data)
    assert generated.call_args.args[1]=='agent-model'
    assert generated.call_args.kwargs['extra_context']['assigned_agent_id']==AGENT


@pytest.mark.parametrize('model',['','bad model with spaces'])
def test_missing_or_invalid_assigned_model_blocks_without_fallback_or_reservation(ready,model):
    db,other,generated,data=ready
    db.execute('UPDATE saas_ai_agents SET model=%s WHERE tenant_id=%s::uuid AND id=%s::uuid',(model,A,AGENT));db.commit()
    with pytest.raises(TenantLinearError) as caught:copilot.run(PROFILE,data)
    assert caught.value.status==409
    generated.assert_not_called()
    assert db.execute('SELECT count(*) AS n FROM saas_chat_ai_runs')[0]['n']==0


def test_model_change_without_version_bump_invalidates_cache_and_private_memory(ready):
    db,other,generated,data=ready
    data.update(mode='chat',prompt='Old model private memory')
    copilot.run(PROFILE,data)
    db.execute("UPDATE saas_ai_agents SET model='new-agent-model' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT));db.commit()
    with pytest.raises(TenantLinearError):copilot.run(PROFILE,data)
    assert generated.call_count==1
    copilot.run(PROFILE,{**data,'request_id':str(uuid.uuid4()),'prompt':'New model request'})
    assert generated.call_args.args[1]=='new-agent-model'
    assert generated.call_args.kwargs['extra_context']['prior_internal_copilot_turns']==[]


def test_model_change_during_generation_hides_stale_result(ready):
    db,other,generated,data=ready
    def respond(*args,**kwargs):
        db.execute("UPDATE saas_ai_agents SET model='new-agent-model' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT));db.commit()
        return 'stale result',{}
    generated.side_effect=respond
    with pytest.raises(TenantLinearError) as caught:copilot.run(PROFILE,data)
    assert caught.value.status==409
    assert db.execute('SELECT count(*) AS n FROM saas_chat_send_receipts')[0]['n']==0


def test_assigned_text_model_preserves_specialized_asr_and_photo_selection(ready,monkeypatch):
    import tenant_connect_voice as voice
    db,other,generated,data=ready
    db.execute("UPDATE saas_tenant_integrations SET metadata=metadata||%s::jsonb WHERE tenant_id=%s::uuid AND provider='chat_ai'",
        (json.dumps({'audio_enabled':True,'transcription_model':'whisper-1','photo_model':'gpt-4.1'}),A));db.commit()
    prepare=Mock(wraps=copilot._prepare_copilot_media)
    monkeypatch.setattr(copilot,'_prepare_copilot_media',prepare)
    copilot.run(PROFILE,data)
    execution=prepare.call_args.kwargs['execution']
    assert execution.model=='agent-model'
    assert execution.command['_agent_id']==AGENT
    assert voice.transcription_model({'config':execution.config,'credential_source':execution.credential_source})=='whisper-1'
    assert execution.item({'media_kind':'image'},{})['route']['campaign_photo_model']=='gpt-4.1'
    assert generated.call_args.args[1]=='agent-model'


def test_queued_connect_work_requires_current_assignment_without_disabling_assignment_discovery(ready,monkeypatch):
    import tenant_ai_agent_connect as connect
    db,other,generated,data=ready
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status) VALUES(%s::uuid,'connect','connected')",(A,))
    db.execute("UPDATE saas_ai_agent_settings SET auto_send_enabled=TRUE,operating_mode='active' WHERE tenant_id=%s::uuid",(A,));db.commit()
    monkeypatch.setattr(connect,'runtime_flag',lambda _:True)
    monkeypatch.setattr('tenant_connect_identity.conflicted',lambda *a:False)
    job={**data,'tenant_id':A,'agent_id':AGENT}
    assert connect.allowed(job,connection=db)
    db.execute("UPDATE saas_ai_conversation_state SET agent_id=NULL WHERE tenant_id=%s::uuid",(A,));db.commit()
    assert not connect.allowed(job,connection=db)
    # Route admission for the separate assignment business process remains usable.
    assert connect.allowed(job,connection=db,require_assignment=False)
    assert db.execute('SELECT count(*) AS n FROM saas_crm_messages')[0]['n']>0
    generated.assert_not_called()


@pytest.mark.parametrize('mode',['reply','chat','instruction','solution'])
def test_unassignment_before_private_provider_call_prevents_dispatch(ready,monkeypatch,mode):
    import tenant_episode_media as media
    db,other,generated,data=ready
    data.update(mode=mode,prompt='Explain' if mode!='reply' else '')
    original=media.ManualExecution.check
    def lose_assignment(self):
        db.execute('UPDATE saas_ai_conversation_state SET agent_id=NULL WHERE tenant_id=%s::uuid',(A,));db.commit()
        return original(self)
    monkeypatch.setattr(media.ManualExecution,'check',lose_assignment)
    with pytest.raises(TenantLinearError):copilot.run(PROFILE,data)
    generated.assert_not_called()
    assert db.execute('SELECT count(*) AS n FROM saas_chat_send_receipts')[0]['n']==0
    assert db.execute('SELECT count(*) AS n FROM saas_crm_messages')[0]['n']>0


def test_no_assignment_cannot_clear_saved_private_agent_history(ready):
    db,other,generated,data=ready
    copilot.ensure_schema(db)
    db.execute("INSERT INTO saas_operator_copilot_threads(tenant_id,requested_by,channel_id,conversation_id,turns) VALUES(%s::uuid,11,%s,'client','[{\"text\":\"saved private history\"}]'::jsonb)",(A,CA))
    db.execute('UPDATE saas_ai_conversation_state SET agent_id=NULL WHERE tenant_id=%s::uuid',(A,));db.commit()
    with pytest.raises(TenantLinearError):copilot.run(PROFILE,{**data,'mode':'clear'})
    generated.assert_not_called()
    assert db.execute('SELECT count(*) AS n FROM saas_operator_copilot_threads')[0]['n']==1


def test_kommo_assignment_uses_normalized_scope_not_transport_origin(ready,monkeypatch):
    import tenant_ai_agent_admission as admission
    db,other,generated,data=ready
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,account_domain) VALUES(%s::uuid,'kommo','connected','synthetic.kommo.com')",(A,))
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,agent_id,state) VALUES(%s::uuid,'kommo','','kommo-chat',%s::uuid,'active')",(A,AGENT));db.commit()
    monkeypatch.setattr(admission,'_connect',lambda:db)
    monkeypatch.setattr('tenant_ai_agent_runtime_flags.enabled',lambda _:True)
    job={'tenant_id':A,'agent_id':AGENT,'provider':'kommo','channel_id':'whatsapp','conversation_id':'kommo-chat'}
    assert admission.allowed_job(job)
    db.execute("UPDATE saas_ai_conversation_state SET agent_id=NULL WHERE tenant_id=%s::uuid AND provider='kommo'",(A,));db.commit()
    assert not admission.allowed_job(job)
    generated.assert_not_called()
