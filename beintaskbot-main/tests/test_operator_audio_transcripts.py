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
