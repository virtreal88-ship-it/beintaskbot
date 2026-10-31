"""Read saved audio evidence on isolated PGlite, without ASR or media calls."""
import json
import uuid
from test_tenant_support_inbox import database,db,A,B,CA,CB
from tenant_media_evidence import create_schema,media_version,source_hash,_body_hash
from tenant_operator_dialogs import _attach_saved_audio_transcripts,_message


def test_only_exact_message_version_and_channel_receipt_is_shown(db):
    create_schema(db)
    row={'id':str(uuid.uuid4()),'external_id':'connect:audio-test','channel':'connect:'+CA,
         'direction':'incoming','message_type':'audio','body':'','raw':{'mediaId':'version-one'}}
    ref={'external_id':row['external_id'],'body_hash':_body_hash(row['body']),
         'media_kind':'audio','media_version':media_version(row['raw'])}
    db.execute("""INSERT INTO saas_media_evidence_receipts(tenant_id,channel_id,external_id,source_hash,media_kind,direction,status,code,result)
      VALUES(%s::uuid,%s::uuid,%s,%s,'audio','incoming','processed','transcribed',%s::jsonb)""",
      (A,CA,row['external_id'],source_hash(ref),json.dumps({'transcript':'Salam, как дела?', 'observation':'internal-secret'})))
    db.commit()
    with db.cursor() as cur:
        _attach_saved_audio_transcripts(cur,A,[row])
        result=_message(row)
        assert result['audio_transcript']=='Salam, как дела?'
        assert result['has_transcription'] is True
        assert 'internal-secret' not in json.dumps(result)
        changed={**row,'raw':{'mediaId':'version-two'}};changed.pop('_saved_audio_transcript',None)
        _attach_saved_audio_transcripts(cur,A,[changed]);assert '_saved_audio_transcript' not in changed
        other={**row,'channel':'connect:'+CB};other.pop('_saved_audio_transcript',None)
        _attach_saved_audio_transcripts(cur,A,[other]);assert '_saved_audio_transcript' not in other
        foreign={**row};foreign.pop('_saved_audio_transcript',None)
        _attach_saved_audio_transcripts(cur,B,[foreign]);assert '_saved_audio_transcript' not in foreign


def test_voice_body_only_when_confirmed_and_empty_is_not_transcript():
    row={'channel':'connect:'+CA,'message_type':'voice','direction':'incoming','body':'Original speech','raw':{'voice_transcription':{'status':'completed'}}}
    assert _message(row)['audio_transcript']=='Original speech'
    row['raw']['voice_transcription']['status']='failed'
    assert 'audio_transcript' not in _message(row)
    row['body']='';row['raw']['voice_transcription']['status']='completed'
    assert 'audio_transcript' not in _message(row)


def test_authorized_dialog_history_returns_matching_saved_transcript(db,monkeypatch):
    import tenant_operator_dialogs as dialogs
    from tenant_linear_policy import TenantLinearError
    import pytest
    create_schema(db);monkeypatch.setattr(dialogs,'_connect',lambda:db)
    db.execute("UPDATE saas_crm_messages SET message_type='voice',body='',raw=%s::jsonb WHERE tenant_id=%s::uuid AND kommo_lead_id=41",(json.dumps({'mediaId':'voice-one'}),A))
    row=db.execute("SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=41",(A,))[0]
    ref={'external_id':row['external_id'],'body_hash':_body_hash(row['body']),'media_kind':'voice','media_version':media_version(row['raw'])}
    db.execute("""INSERT INTO saas_media_evidence_receipts(tenant_id,channel_id,external_id,source_hash,media_kind,direction,status,code,result)
      VALUES(%s::uuid,%s::uuid,%s,%s,'voice','incoming','processed','transcribed',%s::jsonb)""",(A,CA,row['external_id'],source_hash(ref),json.dumps({'transcript':'Səs qeydi'})));db.commit()
    history=dialogs.history({'tenant_id':A,'telegram_id':11},'connect',41,CA,'client')
    assert history['messages'][0]['audio_transcript']=='Səs qeydi'
    with pytest.raises(TenantLinearError):dialogs.history({'tenant_id':B,'telegram_id':22},'connect',41,CA,'client')


def test_saved_failure_projection_is_exact_version_and_contains_no_private_payload(db):
    create_schema(db)
    row={'id':str(uuid.uuid4()),'external_id':'connect:failure','channel':'connect:'+CA,
        'direction':'incoming','message_type':'audio','body':'','raw':{'mediaId':'current'}}
    ref={'external_id':row['external_id'],'body_hash':_body_hash(''),'media_kind':'audio','media_version':media_version(row['raw'])}
    db.execute('''INSERT INTO saas_media_evidence_receipts(tenant_id,channel_id,external_id,source_hash,media_kind,direction,status,code,result)
      VALUES(%s::uuid,%s::uuid,%s,%s,'audio','incoming','unsupported','media_unavailable',%s::jsonb)''',
      (A,CA,row['external_id'],source_hash(ref),json.dumps({'failure_stage':'media_download','failure_code':'media_processing_failed','observation':'SECRET_PRIVATE_TEXT','raw_error':'SECRET_URL'})));db.commit()
    with db.cursor() as cur:_attach_saved_audio_transcripts(cur,A,[row])
    result=_message(row)
    assert result['analysis_diagnostic']['code']=='media_unavailable'
    assert result['analysis_diagnostic']['stage']=='media_download'
    assert result['analysis_state']=='unsupported' and 'has_transcription' not in result
    assert 'SECRET' not in json.dumps(result)
    changed={**row,'raw':{'mediaId':'replacement'}};changed.pop('_saved_audio_diagnostic',None)
    with db.cursor() as cur:_attach_saved_audio_transcripts(cur,A,[changed])
    assert '_saved_audio_diagnostic' not in changed
    assert '_saved_audio_receipt_diagnostic' not in changed
    foreign={**row};foreign.pop('_saved_audio_diagnostic',None)
    with db.cursor() as cur:_attach_saved_audio_transcripts(cur,B,[foreign])
    assert '_saved_audio_diagnostic' not in foreign
    assert '_saved_audio_receipt_diagnostic' not in foreign


def test_prior_manual_reply_exposes_only_matching_metadata_not_private_text(db):
    from tenant_chat_ai_schema import ensure_schema
    ensure_schema(db);create_schema(db)
    row={'id':str(uuid.uuid4()),'external_id':'connect:manual-attempt','channel':'connect:'+CA,
        'direction':'incoming','message_type':'audio','body':'','raw':{'mediaId':'current'}}
    ref={'external_id':row['external_id'],'channel_id':CA,'body_hash':_body_hash(''),
        'media_kind':'audio','media_version':media_version(row['raw'])}
    result={'text':'PRIVATE_COPILOT_RESPONSE','source_versions':[ref],
        'context_usage':{'evidence':[{'source_id':row['external_id'],'kind':'audio','status':'unsupported','code':'media_unavailable'}]}}
    db.execute('''INSERT INTO saas_chat_ai_runs(tenant_id,request_id,requested_by,lead_id,input_hash,config_version,status,reserved_calls,result,provider,channel_id,conversation_id)
      VALUES(%s::uuid,%s::uuid,11,41,'synthetic-hash','1','completed',1,%s::jsonb,'connect',%s,'client')''',
      (A,str(uuid.uuid4()),json.dumps(result),CA));db.commit()
    with db.cursor() as cur:_attach_saved_audio_transcripts(cur,A,[row],conversation_id='client')
    public=_message(row)
    assert public['analysis_diagnostic']['code']=='media_unavailable'
    assert public['analysis_diagnostic']['stage']=='unknown'
    assert 'PRIVATE' not in json.dumps(public)
    for tenant,channel,conversation,raw in [(B,CA,'client',row['raw']),(A,CB,'client',row['raw']),
            (A,CA,'other',row['raw']),(A,CA,'client',{'mediaId':'changed'})]:
        other={**row,'channel':'connect:'+channel,'raw':raw};other.pop('_saved_audio_diagnostic',None)
        with db.cursor() as cur:_attach_saved_audio_transcripts(cur,tenant,[other],conversation_id=conversation)
        assert '_saved_audio_diagnostic' not in other


import pytest

@pytest.mark.parametrize('stage,proven',[('media_download',True),('media_validation',False),('audio_transcription',False)])
def test_later_summary_does_not_hide_original_receipt_proof_or_authorize_unknown_retry(db,stage,proven):
    from tenant_chat_ai_schema import ensure_schema
    ensure_schema(db);create_schema(db)
    row={'id':str(uuid.uuid4()),'external_id':'connect:'+str(uuid.uuid4()),'channel':'connect:'+CA,
        'direction':'incoming','message_type':'audio','body':'','raw':{'mediaId':'current'}}
    ref={'external_id':row['external_id'],'channel_id':CA,'body_hash':_body_hash(''),
        'media_kind':'audio','media_version':media_version(row['raw'])}
    version=source_hash(ref)
    db.execute('''INSERT INTO saas_media_evidence_receipts(tenant_id,channel_id,external_id,source_hash,media_kind,direction,status,code,result,provenance,updated_at)
      VALUES(%s::uuid,%s::uuid,%s,%s,'audio','incoming','error','media_unavailable',%s::jsonb,%s::jsonb,'2026-10-31T20:00:00Z')''',
      (A,CA,row['external_id'],version,json.dumps({'failure_stage':stage,'failure_code':'media_processing_failed','raw_error':'SECRET'}),
       json.dumps({'processor':'knowledge_extraction','source_version':version})))
    result={'text':'PRIVATE_COPILOT_TEXT','source_versions':[ref],
        'context_usage':{'evidence':[{'source_id':row['external_id'],'kind':'audio','status':'error','code':'media_unavailable'}]}}
    db.execute('''INSERT INTO saas_chat_ai_runs(tenant_id,request_id,requested_by,lead_id,input_hash,config_version,status,reserved_calls,result,provider,channel_id,conversation_id,updated_at)
      VALUES(%s::uuid,%s::uuid,11,41,'synthetic','1','completed',1,%s::jsonb,'connect',%s,'client','2026-10-31T22:54:49Z')''',
      (A,str(uuid.uuid4()),json.dumps(result),CA));db.commit()
    with db.cursor() as cur:_attach_saved_audio_transcripts(cur,A,[row],conversation_id='client')
    public=_message(row)
    assert public['analysis_diagnostic']['stage']=='unknown'  # Do not invent the later request's stage.
    original=public['analysis_receipt_diagnostic']
    assert original['stage']==stage and original['pre_dispatch_proven'] is proven
    assert original['at']!=public['analysis_diagnostic']['at']
    assert 'SECRET' not in json.dumps(public) and 'PRIVATE' not in json.dumps(public)
    # Projection is read-only: no receipt/run state or reserved calls changes.
    assert db.execute('SELECT status FROM saas_media_evidence_receipts WHERE tenant_id=%s::uuid AND external_id=%s',(A,row['external_id']))[0]['status']=='error'
    assert db.execute("SELECT reserved_calls FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND result->'context_usage'->'evidence'->0->>'source_id'=%s",(A,row['external_id']))[0]['reserved_calls']==1
