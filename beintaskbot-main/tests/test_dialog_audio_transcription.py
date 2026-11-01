"""Explicit single-audio action; isolated SQL and synthetic provider only."""
import hashlib
import io
import json
import uuid
import wave
from unittest.mock import Mock
import pytest
from cryptography.fernet import Fernet
from test_tenant_support_inbox import database,db,A,B,CA,CB,AGENT,PROFILE,seed_job
import tenant_dialog_audio_transcription as flow
import tenant_dialog_audio_store as store
import tenant_ai_spend_budget as budget
import tenant_ai_asr_model as asr

REAL_DOWNLOAD=flow._download


def wav():
    out=io.BytesIO()
    with wave.open(out,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0\0'*1600)
    return out.getvalue()

@pytest.fixture
def ready(db,monkeypatch):
    seed_job(db)
    db.execute("UPDATE saas_ai_agents SET model='gpt-4.1' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT))
    cipher=Fernet(Fernet.generate_key())
    monkeypatch.setattr(flow,'_fernet',lambda:cipher)
    for module in (flow,budget):monkeypatch.setattr(module,'_connect',lambda:db)
    monkeypatch.setattr(asr,'SHARED_ASR_TENANT_DIGEST',hashlib.sha256(('shared-dialog-asr-rollout:v1:'+A).encode()).hexdigest())
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD','{}')
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:3000000,B:3000000}))
    config={'enabled':True,'consent':True,'copilot_enabled':True,'audio_enabled':True,'transcription_model':'whisper-1','daily_limit':50,'copilot_daily_limit':50}
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,secrets,metadata) VALUES(%s::uuid,'chat_ai',%s,%s::jsonb)",
        (A,cipher.encrypt(json.dumps({'tenant_id':A,'provider':'chat_ai','api_key':'synthetic-key'}).encode()),json.dumps(config)))
    external='connect:'+str(uuid.uuid4())
    db.execute("UPDATE saas_crm_messages SET external_id=%s,message_type='audio',body='',raw=%s::jsonb WHERE tenant_id=%s::uuid",(external,json.dumps({'mediaId':'synthetic-file'}),A));db.commit()
    row=db.execute('SELECT id FROM saas_crm_messages WHERE tenant_id=%s::uuid',(A,))[0]
    data={'channel_id':CA,'conversation_id':'client','message_id':external,'message_row_id':str(row['id'])}
    download=Mock(return_value=(wav(),'audio/wav'));monkeypatch.setattr(flow,'_download',download)
    sent=[]
    def call(key,model,*args,**kw):
        if kw.get('before_dispatch'):kw['before_dispatch']()
        sent.append(model)
        return 'synthetic speech',{'seconds':.1,'total_tokens':0}
    provider=Mock(side_effect=call);monkeypatch.setattr('tenant_chat_ai_provider.transcribe',provider)
    generate=Mock(side_effect=AssertionError('ASR-only must never call text LLM'));monkeypatch.setattr('tenant_chat_ai_provider.generate',generate)
    store.schema(db)
    return db,data,provider,download,sent


def command(data,preview,action=None,key=None):
    return {**data,'source_version':preview['source']['source_version'],'action':action or preview['allowed_actions'][0],
        'cost_token':preview['cost_token'],'idempotency_key':key or str(uuid.uuid4())}


def test_preview_is_readonly_then_single_asr_no_llm_or_send(ready):
    db,data,provider,download,sent=ready
    counts={t:db.execute('SELECT count(*) AS n FROM '+t)[0]['n'] for t in ('saas_chat_ai_runs','saas_dialog_audio_attempts','saas_ai_spend_reservations')};db.commit()
    preview=flow.preview(PROFILE,data)
    assert preview['effective_model']=='gpt-transcribe' and preview['price']['reserve_microusd']==30000
    assert preview['price']['max_duration_seconds']==300 and preview['allowed_actions']==['transcribe']
    provider.assert_not_called();download.assert_not_called()
    assert all(db.execute('SELECT count(*) AS n FROM '+t)[0]['n']==n for t,n in counts.items());db.commit()
    request=command(data,preview)
    result=flow.execute(PROFILE,request)
    assert result['requested_attempt']['state']=='completed'
    assert result['latest_success']['text']=='synthetic speech'
    assert sent==['gpt-transcribe']
    assert db.execute('SELECT reserved_calls FROM saas_chat_ai_runs')==[{'reserved_calls':1}]
    assert db.execute('SELECT model,reserved_microusd,actual_microusd,state FROM saas_ai_spend_reservations')==[{'model':'gpt-transcribe','reserved_microusd':30000,'actual_microusd':30000,'state':'settled'}]
    assert flow.execute(PROFILE,request)['requested_attempt']['id']==result['requested_attempt']['id']
    assert len(sent)==1 and download.call_count==1


def test_fulltext_explicit_precedence_previous_history_and_unknown_hold(ready):
    from tenant_media_evidence import media_version
    db,data,provider,download,sent=ready
    raw={'mediaId':'synthetic-file'};raw['voice_transcription']={'status':'completed','media_version':media_version(raw),'model':'whisper-1'}
    db.execute('UPDATE saas_crm_messages SET body=%s,raw=%s::jsonb WHERE id=%s::uuid',('old synthetic speech',json.dumps(raw),data['message_row_id']));db.commit()
    full='word '*10000+'\npassword=never-expose'
    def success(*args,**kw):
        kw['before_dispatch']();sent.append(args[1]);return full,{'seconds':.1,'total_tokens':0}
    provider.side_effect=success
    result=flow.execute(PROFILE,command(data,flow.preview(PROFILE,data)))
    assert len(result['latest_success']['text'])>4000 and 'never-expose' not in result['latest_success']['text']
    assert result['message']['audio_transcript']==result['latest_success']['text']
    assert any(r['text']=='old synthetic speech' for r in result['history'])
    assert db.execute('SELECT body,raw FROM saas_crm_messages WHERE id=%s::uuid',(data['message_row_id'],))[0]=={'body':'old synthetic speech','raw':raw}
    def fail(*args,**kw):kw['before_dispatch']();sent.append(args[1]);raise TimeoutError('private-provider-error')
    provider.side_effect=fail
    request=command(data,flow.preview(PROFILE,data))
    failed=flow.execute(PROFILE,request)
    assert failed['state']=='unknown' and not failed['allowed_actions']
    assert failed['message']['audio_transcript']==result['message']['audio_transcript']
    assert failed['latest_success']['text']==result['latest_success']['text']
    assert 'private-provider-error' not in json.dumps(failed)
    assert sorted(r['state'] for r in db.execute('SELECT state FROM saas_ai_spend_reservations'))==['settled','unknown']
    assert flow.execute(PROFILE,request)['requested_attempt']['state']=='unknown' and len(sent)==2


def test_idempotency_original_attempt_survives_later_completed_head(ready):
    db,data,provider,download,sent=ready
    first=command(data,flow.preview(PROFILE,data));one=flow.execute(PROFILE,first)
    second=command(data,flow.preview(PROFILE,data));two=flow.execute(PROFILE,second)
    replay=flow.execute(PROFILE,first)
    assert replay['requested_attempt']['id']==one['requested_attempt']['id']
    assert replay['current_attempt']['id']==two['requested_attempt']['id'] and len(sent)==2
    poll=flow.preview(PROFILE,{**data,'idempotency_key':first['idempotency_key']})
    assert poll['requested_attempt']['id']==one['requested_attempt']['id']
    assert download.call_count==2


def test_new_uuid_does_not_replay_consumed_preview_generation(ready):
    db,data,provider,download,sent=ready
    request=command(data,flow.preview(PROFILE,data));flow.execute(PROFILE,request)
    with pytest.raises(flow.AudioError,match='Обновите состояние'):
        flow.execute(PROFILE,{**request,'idempotency_key':str(uuid.uuid4())})
    assert len(sent)==1 and db.execute('SELECT count(*) AS n FROM saas_dialog_audio_attempts')[0]['n']==1

@pytest.mark.parametrize('change',['source','assignment'])
def test_before_dispatch_change_is_safe_skipped_released_not_stuck(ready,change):
    db,data,provider,download,sent=ready
    def stop(*args,**kw):
        if change=='source':db.execute("UPDATE saas_crm_messages SET raw=raw||'{\"mediaId\":\"changed\"}'::jsonb WHERE id=%s::uuid",(data['message_row_id'],))
        else:db.execute('UPDATE saas_ai_conversation_state SET agent_id=NULL WHERE tenant_id=%s::uuid',(A,))
        db.commit();kw['before_dispatch']();raise AssertionError('must stop before HTTP')
    provider.side_effect=stop
    result=flow.execute(PROFILE,command(data,flow.preview(PROFILE,data)))
    assert result['requested_attempt']['state']=='failed_safe' and not result['requested_attempt']['published']
    assert db.execute('SELECT status FROM saas_chat_ai_runs')==[{'status':'skipped'}]
    assert db.execute('SELECT state,actual_microusd FROM saas_ai_spend_reservations')==[{'state':'released','actual_microusd':0}]
    assert db.execute('SELECT active_attempt_id FROM saas_dialog_audio_sources')==[{'active_attempt_id':None}]
    assert not sent


def test_known_paid_success_withheld_then_fresh_transcribe_reuses_no_new_http(ready):
    db,data,provider,download,sent=ready
    def generate_then_change(*args,**kw):
        kw['before_dispatch']();sent.append(args[1])
        db.execute('UPDATE saas_ai_agents SET version=version+1 WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT));db.commit()
        return 'known retained speech',{'seconds':.1,'total_tokens':0}
    provider.side_effect=generate_then_change
    first=flow.execute(PROFILE,command(data,flow.preview(PROFILE,data)))
    assert first['requested_attempt']['state']=='completed' and not first['requested_attempt']['published']
    assert not first['latest_success'] and not first['message'].get('audio_transcript')
    second=flow.execute(PROFILE,command(data,flow.preview(PROFILE,data),action='transcribe'))
    assert second['requested_attempt']['origin']=='reuse' and second['requested_attempt']['reserve_microusd']==0
    assert second['message']['audio_transcript']=='known retained speech' and len(sent)==1
    assert db.execute('SELECT count(*) AS n FROM saas_chat_ai_runs')[0]['n']==1


def test_verified_old_whisper_reuse_keeps_actual_artifact_model(ready):
    from tenant_media_evidence import media_version
    db,data,provider,download,sent=ready
    raw={'mediaId':'synthetic-file'};raw['voice_transcription']={'status':'completed','media_version':media_version(raw),'model':'whisper-1'}
    db.execute('UPDATE saas_crm_messages SET body=%s,raw=%s::jsonb WHERE id=%s::uuid',('old verified speech',json.dumps(raw),data['message_row_id']));db.commit()
    preview=flow.preview(PROFILE,data)
    reused=flow.execute(PROFILE,command(data,preview,action='transcribe'))
    assert reused['effective_model']=='gpt-transcribe' and reused['latest_success']['model']=='whisper-1'
    assert reused['requested_attempt']['reserve_microusd']==0 and not sent
    fresh=flow.execute(PROFILE,command(data,flow.preview(PROFILE,data),action='retranscribe'))
    assert fresh['latest_success']['model']=='gpt-transcribe' and len(sent)==1


def test_blocked_preview_has_stable_assignment_reason_and_safe_message(ready):
    db,data,provider,download,sent=ready
    db.execute('UPDATE saas_ai_conversation_state SET agent_id=NULL WHERE tenant_id=%s::uuid',(A,));db.commit()
    result=flow.preview(PROFILE,data)
    assert result['state']=='blocked' and result['blocked_reason']=='agent_assignment_required'
    assert 'назнач' in result['blocked_message'].lower() and result['cost_token'] is None
    provider.assert_not_called();download.assert_not_called()


def test_matching_cache_unknown_blocks_but_unmatched_hold_is_never_refunded(ready):
    import tenant_knowledge_media_cache as cache
    db,data,provider,download,sent=ready
    cache.schema(db.cursor());budget.ensure_schema(db)
    key=cache.transcription_fingerprint({},'whisper-1',(wav(),'wav','audio/wav'))
    db.execute("INSERT INTO saas_knowledge_media_cache(tenant_id,fingerprint,state,token) VALUES(%s::uuid,%s,'unknown',%s::uuid)",(A,key,str(uuid.uuid4())));db.commit()
    request=command(data,flow.preview(PROFILE,data))
    with pytest.raises(flow.AudioError):flow.execute(PROFILE,request)
    assert not sent
    db.execute("UPDATE saas_knowledge_media_cache SET fingerprint='unmatched-opaque-legacy-key'")
    with db.cursor() as cur:budget.reserve_media(cur,A,'old:unknown','knowledge_asr','whisper-1','asr',1)
    db.commit();budget.settle_media(A,'old:unknown',operation='asr',attempted_units=1,unknown=True)
    flow.execute(PROFILE,command(data,flow.preview(PROFILE,data)))
    assert len(sent)==1 and db.execute("SELECT state,reserved_microusd,actual_microusd FROM saas_ai_spend_reservations WHERE call_key='old:unknown'")==[{'state':'unknown','reserved_microusd':30000,'actual_microusd':None}]
    assert db.execute("SELECT state FROM saas_knowledge_media_cache WHERE fingerprint='unmatched-opaque-legacy-key'")==[{'state':'unknown'}]
    assert sum(r['reserved_microusd'] for r in db.execute('SELECT reserved_microusd FROM saas_ai_spend_reservations'))==60000


def test_stale_source_token_never_downloads_or_reserves(ready):
    db,data,provider,download,sent=ready
    request=command(data,flow.preview(PROFILE,data))
    db.execute("UPDATE saas_crm_messages SET raw=raw||'{\"mediaId\":\"replacement\"}'::jsonb WHERE id=%s::uuid",(data['message_row_id'],));db.commit()
    with pytest.raises(flow.AudioError):flow.execute(PROFILE,request)
    provider.assert_not_called();download.assert_not_called()
    assert db.execute('SELECT count(*) AS n FROM saas_chat_ai_runs')[0]['n']==0


def test_other_source_row_cannot_be_chosen_by_external_id_or_other_tenant(ready):
    db,data,provider,download,sent=ready
    foreign=db.execute('SELECT id,external_id FROM saas_crm_messages WHERE tenant_id=%s::uuid LIMIT 1',(B,))[0]
    with pytest.raises(flow.AudioError):flow.preview(PROFILE,{**data,'message_row_id':str(foreign['id']),'message_id':foreign['external_id']})
    provider.assert_not_called();download.assert_not_called()

@pytest.mark.parametrize('captured',[False,True])
def test_group_representative_requires_current_capture_and_uses_view_agent(ready,captured):
    import tenant_ai_group_coordination as groups
    db,data,provider,download,sent=ready
    group='120363000000000001@g.us';participant='994500000001@s.whatsapp.net'
    groups.create_schema(db)
    db.execute("UPDATE saas_connect_conversations SET conversation_id=%s,conversation_kind='group' WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid",(group,A,CA))
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Other channel')",(A,CB))
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind,lead_id) VALUES(%s::uuid,%s::uuid,%s,'group',99)",(A,CB,group))
    db.execute('UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',(json.dumps([{'provider':'connect','channel_id':CA,'allow_group_messages':True}]),A,AGENT))
    db.execute("INSERT INTO saas_ai_group_coordination(tenant_id,group_jid,assignments) VALUES(%s::uuid,%s,%s::jsonb)",(A,group,json.dumps([{'agent_id':AGENT,'channel_id':CA,'priority':0}])))
    raw_a={'mediaId':'actual-view-file','channelId':CA,'conversationId':group,'externalMessageId':'SHARED_AUDIO' if captured else 'OTHER_AUDIO','participantJid':participant}
    db.execute('UPDATE saas_crm_messages SET raw=%s::jsonb WHERE id=%s::uuid',(json.dumps(raw_a),data['message_row_id']))
    selection=str(uuid.uuid4());external='connect:'+str(uuid.uuid4())
    raw_b={'mediaId':'not-the-requested-file','channelId':CB,'conversationId':group,'externalMessageId':'SHARED_AUDIO','participantJid':participant}
    db.execute("INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,raw) VALUES(%s::uuid,%s::uuid,99,%s,%s,'outgoing','private other-channel caption','audio',%s::jsonb)",(A,selection,external,'connect:'+CB,json.dumps(raw_b)));db.commit()
    request_source={**data,'conversation_id':group,'message_row_id':selection,'message_id':external}
    if not captured:
        with pytest.raises(flow.AudioError) as caught:flow.preview(PROFILE,request_source)
        assert caught.value.status in {403,404} and 'private' not in str(caught.value)
        provider.assert_not_called();download.assert_not_called()
    else:
        def actual_file(execution):
            assert str(execution.auth['row']['id'])==data['message_row_id']
            assert str(execution.auth['context']['agent_id'])==AGENT
            assert execution.auth['view_channel']==CA and execution.auth['source_channel']==CA
            return wav(),'audio/wav'
        download.side_effect=actual_file
        result=flow.execute(PROFILE,command(request_source,flow.preview(PROFILE,request_source)))
        assert result['requested_attempt']['state']=='completed' and result['message']['id']==selection
        assert result['source']['message_row_id']==selection and result['source']['message_id']==external
        assert result['message']['audio_transcript']=='synthetic speech' and len(sent)==1


def test_foreign_chat_uuid_and_orphan_unknown_reservation_never_dispatch(ready):
    db,data,provider,download,sent=ready
    request=command(data,flow.preview(PROFILE,data))
    db.execute("INSERT INTO saas_chat_ai_runs(tenant_id,request_id,requested_by,lead_id,input_hash,config_version,status,reserved_calls,provider,channel_id,conversation_id) VALUES(%s::uuid,%s::uuid,11,41,'other','other','completed',1,'connect',%s,'client')",(A,request['idempotency_key'],CA));db.commit()
    with pytest.raises(flow.AudioError) as caught:flow.execute(PROFILE,request)
    assert caught.value.code=='idempotency_conflict' and not sent
    other=command(data,flow.preview(PROFILE,data));key='dialog-audio:'+other['idempotency_key']
    with db.cursor() as cur:budget.reserve_media(cur,A,key,'dialog_audio_asr','whisper-1','asr',1)
    db.commit();budget.settle_media(A,key,operation='asr',attempted_units=1,unknown=True)
    with pytest.raises(flow.AudioError) as caught:flow.execute(PROFILE,other)
    assert caught.value.code=='attempt_state_unavailable' and not sent
    assert db.execute('SELECT state FROM saas_ai_spend_reservations WHERE call_key=%s',(key,))==[{'state':'unknown'}]


def test_unmatched_unknown_hold_still_exhausts_existing_budget(ready,monkeypatch):
    db,data,provider,download,sent=ready
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:30000}))
    with db.cursor() as cur:budget.reserve_media(cur,A,'unattributed-old','knowledge_asr','whisper-1','asr',1)
    db.commit();budget.settle_media(A,'unattributed-old',operation='asr',attempted_units=1,unknown=True)
    with pytest.raises(Exception):flow.execute(PROFILE,command(data,flow.preview(PROFILE,data)))
    assert not sent and db.execute('SELECT count(*) AS n FROM saas_dialog_audio_attempts')[0]['n']==0
    assert db.execute("SELECT state,reserved_microusd FROM saas_ai_spend_reservations WHERE call_key='unattributed-old'")==[{'state':'unknown','reserved_microusd':30000}]


@pytest.mark.parametrize('kind',['voice_message','ptt'])
def test_native_audio_alias_claims_take_source_lock_and_respect_pending(ready,monkeypatch,kind):
    import tenant_episode_media as episode
    from types import SimpleNamespace
    db,data,provider,download,sent=ready
    original=provider.side_effect
    def pending(*args,**kw):
        # The explicit claim is durably pending before provider dispatch.
        ref={**flow.reference(db.execute('SELECT * FROM saas_crm_messages WHERE id=%s::uuid',(data['message_row_id'],))[0],CA,41),'media_kind':kind}
        # Use the real SQL claim path, not a synthetic source-guard stub.
        monkeypatch.setattr(episode,'_connect',lambda:db)
        key,token,result=episode._flight(SimpleNamespace(tenant=A,check=lambda:None),ref)
        assert token is None and result['code']=='audio_transcription_pending'
        return original(*args,**kw)
    provider.side_effect=pending
    result=flow.execute(PROFILE,command(data,flow.preview(PROFILE,data)))
    assert result['requested_attempt']['state']=='completed' and len(sent)==1


def test_opaque_history_cursor_preserves_tied_imports_and_source_scope(ready):
    db,data,provider,download,sent=ready
    with db.cursor() as cur:
        auth=flow._authority(cur,PROFILE,data)
        for i in range(23):store.import_success(cur,auth,{'origin':'legacy_body','source_version':auth['source_version'],'source_current':True,'model':'whisper-1','text':'previous '+str(i)})
    db.commit()
    first=flow.preview(PROFILE,data)
    assert len(first['history'])==20 and first['history_next_cursor']
    second=flow.preview(PROFILE,{**data,'history_before':first['history_next_cursor']})
    assert len(second['history'])==3 and not second['history_next_cursor']
    ids=[item['id'] for page in (first,second) for item in page['history']]
    assert len(set(ids))==23 and {item['generation'] for item in first['history']}=={0}
    provider.assert_not_called();download.assert_not_called()


def test_paid_success_for_changed_source_is_history_only_never_reapplied(ready):
    db,data,provider,download,sent=ready
    def changed(*args,**kw):
        kw['before_dispatch']();sent.append(args[1])
        db.execute("UPDATE saas_crm_messages SET raw=raw||'{\"mediaId\":\"new-file\"}'::jsonb WHERE id=%s::uuid",(data['message_row_id'],));db.commit()
        return 'old source paid speech',{'seconds':.1}
    provider.side_effect=changed
    first=flow.execute(PROFILE,command(data,flow.preview(PROFILE,data)))
    assert first['requested_attempt']['state']=='completed' and not first['requested_attempt']['source_current']
    assert not first['latest_success'] and not first['message'].get('audio_transcript')
    assert first['history'][0]['text']=='old source paid speech'
    assert db.execute('SELECT state,actual_microusd FROM saas_ai_spend_reservations')==[{'state':'settled','actual_microusd':30000}]


@pytest.mark.parametrize('kind',['voice_message','ptt'])
def test_alias_protected_loader_and_transient_download_keep_exact_source(ready,monkeypatch,kind):
    import tenant_chat_attachment_store as attachments
    import tenant_connect_transport as transport
    import tenant_connect_media as media
    db,data,provider,download,sent=ready
    db.execute('UPDATE saas_crm_messages SET message_type=%s WHERE id=%s::uuid',(kind,data['message_row_id']));db.commit()
    monkeypatch.setattr(attachments,'_connect',lambda:db)
    monkeypatch.setattr(transport,'connect_route',lambda *a,**kw:{'channel_id':CA,'conversation_id':'client','api_key':'synthetic'})
    # Protected attachment reads also support provider aliases. Normalization
    # is transient; the ASR authority and fingerprint retain the original row.
    loaded=attachments.load(PROFILE,41,data['message_id'],channel_id=CA,conversation_id='client')
    assert str(loaded['row']['id'])==data['message_row_id'] and loaded['row']['message_type']=='audio'
    assert db.execute('SELECT message_type FROM saas_crm_messages WHERE id=%s::uuid',(data['message_row_id'],))[0]['message_type']==kind
    async def checked(item,**kw):
        assert str(item['row']['id'])==data['message_row_id'] and item['row']['message_type']=='audio'
        assert kw['max_bytes']==10*1024*1024
        return wav(),'audio/wav'
    monkeypatch.setattr(media,'download_media',checked);monkeypatch.setattr(flow,'_download',REAL_DOWNLOAD)
    result=flow.execute(PROFILE,command(data,flow.preview(PROFILE,data)))
    assert result['requested_attempt']['state']=='completed' and len(sent)==1
    assert db.execute('SELECT message_type FROM saas_crm_messages WHERE id=%s::uuid',(data['message_row_id'],))[0]['message_type']==kind


def test_unverified_old_text_cannot_turn_retranscribe_token_into_paid_transcribe(ready):
    db,data,provider,download,sent=ready
    db.execute("UPDATE saas_crm_messages SET body='old unverified transcript',raw=raw||'{\"voice_transcription\":{\"status\":\"completed\",\"model\":\"whisper-1\"}}'::jsonb WHERE id=%s::uuid",(data['message_row_id'],));db.commit()
    preview=flow.preview(PROFILE,data)
    assert preview['allowed_actions']==['retranscribe'] and not preview['latest_success']['source_current']
    with pytest.raises(flow.AudioError) as caught:flow.execute(PROFILE,command(data,preview,action='transcribe'))
    assert caught.value.code=='preview_changed' and not sent
    assert db.execute('SELECT count(*) AS n FROM saas_chat_ai_runs')[0]['n']==0
