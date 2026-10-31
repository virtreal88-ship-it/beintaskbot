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
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,instructions) VALUES(%s::uuid,%s::uuid,'Other support',11,'OTHER_AGENT_RULES')",(A,other))
    db.execute("UPDATE saas_ai_agents SET instructions='ASSIGNED_AGENT_RULES',model='agent-model' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT))
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,secrets,metadata) VALUES(%s::uuid,'chat_ai',%s,%s::jsonb)",
        (A,b'synthetic-cipher',json.dumps({'enabled':True,'consent':True,'copilot_enabled':True,'copilot_model':'configured-copilot-model','daily_limit':100})))
    from tenant_chat_send_schema import ensure_schema as send_schema
    send_schema(db);db.commit()
    monkeypatch.setattr(copilot,'_connect',lambda:db)
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
    assert args[1] == 'configured-copilot-model'
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
def test_manual_reply_processes_current_media_then_auto_reuses_same_exact_receipt_without_send(ready,monkeypatch,kind,interrupt):
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
    source='connect:'+str(uuid.uuid4())
    db.execute('''INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,raw,happened_at)
      VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','',%s,'{"mediaId":"version1"}',now()+interval '2 seconds')''',
      (A,str(uuid.uuid4()),source,'connect:'+CA,kind));db.commit()
    monkeypatch.setattr(media,'_connect',lambda:db)
    monkeypatch.setattr('tenant_media_evidence._connect',lambda:db)
    monkeypatch.setattr('tenant_ai_spend_budget._connect',lambda:db)
    def loaded(session,lead,external,**scope):
        assert session['tenant_id']==A and lead==41 and external==source
        assert scope=={'channel_id':CA,'conversation_id':'client'}
        current=db.execute('SELECT external_id,body,raw,direction,message_type,channel FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s',(A,source))[0]
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
          VALUES(%s::uuid,%s::uuid,41,'employee-after-media',%s,'outgoing','Earlier employee answer',now()+interval '3 seconds')''',
          (A,str(uuid.uuid4()),'connect:'+CA));db.commit()
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
    assert download.await_count==1
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
