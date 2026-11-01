"""Full application schema, scoped saved-photo history, no provider calls."""
import json
import pytest
from test_tenant_support_inbox import A, B, CA, CB, PROFILE, db, database
import tenant_operator_dialogs as dialogs
import tenant_media_evidence as evidence


@pytest.mark.parametrize('kind,status,complete,state',[
    ('image','processed',True,'visual_completed'),
    ('image','partial',False,'visual_partial'),
    ('video','partial',False,'visual_partial'),
    ('image','error',False,'failed')])
def test_history_reads_exact_visual_receipt_metadata_only(db,monkeypatch,kind,status,complete,state):
    evidence.create_schema(db)
    monkeypatch.setattr(dialogs,'_connect',lambda:db)
    db.execute("UPDATE saas_crm_messages SET message_type=%s,body='User caption',raw=%s::jsonb WHERE tenant_id=%s::uuid AND kommo_lead_id=41",
        (kind,json.dumps({'mediaId':'photo-original'}),A))
    row=db.execute('SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=41',(A,))[0]
    ref={'id':row['id'],'external_id':row['external_id'],'channel_id':CA,
         'body_hash':evidence._body_hash(row['body']),'media_kind':kind,'media_version':evidence.media_version(row['raw'])}
    evidence.persist(db.cursor(),A,[ref],[{'id':row['id'],'status':status,'code':'vision_observation',
        'observations':[{'text':'PRIVATE_OCR_TEXT AnyDesk license free'}]}]);db.commit()
    result=dialogs.history(PROFILE,'connect',41,CA,'client')['messages'][0]
    assert result['analysis_state']==state
    assert result['body']=='User caption'
    assert 'PRIVATE_OCR_TEXT' not in json.dumps(result) and 'media_evidence_context' not in result
    # Fresh read after a source replacement must not reuse the old status.
    db.execute("UPDATE saas_crm_messages SET raw=%s::jsonb WHERE tenant_id=%s::uuid AND kommo_lead_id=41",
        (json.dumps({'mediaId':'replacement'}),A));db.commit()
    assert dialogs.history(PROFILE,'connect',41,CA,'client')['messages'][0]['analysis_state']=='not_requested'
    # Same external ID and payload do not grant another tenant/channel's receipt.
    for tenant,channel in ((B,CA),(A,CB)):
        foreign={**row,'channel':'connect:'+channel}
        with db.cursor() as cur:dialogs._attach_saved_visual_status(cur,tenant,[foreign])
        assert dialogs._message(foreign)['analysis_state']=='not_requested'


def test_empty_success_or_legacy_audio_flag_never_means_image_analyzed():
    row={'message_type':'image','channel':'connect:'+CA,'raw':{'voice_transcription':{'status':'completed'}},
         'media_evidence':{'status':'processed','has_visual_result':False,'coverage':{'complete':True}}}
    assert dialogs._message(row)['analysis_state']=='not_requested'
