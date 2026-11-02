"""Exact typed receipt readback using full-schema disposable native PostgreSQL."""
import json
from unittest.mock import Mock
from pathlib import Path

import pytest
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db
from test_product_barcode_evidence import TENANT, OTHER, CHANNEL, REF, OBS, EAN, receipt, HASH
import tenant_media_evidence as evidence
import tenant_product_identifiers as identifiers
from tenant_knowledge_candidates_policy import redact
import tenant_knowledge_media_cache as cache
from test_tenant_support_inbox import A
from test_product_barcode_evidence import CONTENT, cache_item

WIRE_FIXTURES=json.loads((Path(__file__).parent/'fixtures/product_barcode_observation_responses.json').read_text())


@pytest.fixture
def receipt_db(db,monkeypatch):
    monkeypatch.setattr(evidence,'_connect',lambda:db)
    return db


@pytest.mark.parametrize('producer',['original','pre_redaction_cache','old_redacted'])
def test_native_persist_reload_scope_and_public_projection(receipt_db,producer):
    cur=receipt_db.cursor();text=OBS if producer=='original' else redact(OBS)
    row={'id':REF['id'],'status':'processed','code':'vision_observation','observations':[{'text':text,'origin':{}}]}
    if producer=='pre_redaction_cache':
        row['_product_barcode_candidate']=identifiers.bind_artifact(TENANT,REF,identifiers.artifact(OBS,text,HASH),text)
    evidence.persist(cur,TENANT,[REF],[row],processor='conversation_episode');receipt_db.commit()
    saved=evidence.reusable({'tenant_id':TENANT},REF)
    found=identifiers.candidate(TENANT,REF,saved)
    assert bool(found)==(producer!='old_redacted')
    if found:assert found['artifact']['value']==EAN
    assert EAN not in json.dumps(evidence.apply_reusable({'text':''},saved))
    for tenant,ref in [(OTHER,REF),(TENANT,{**REF,'channel_id':OTHER}),
            (TENANT,{**REF,'external_id':'connect:other'}),(TENANT,{**REF,'media_version':'f'*64}),
            (TENANT,{**REF,'body_hash':'changed-caption'})]:
        assert evidence.reusable({'tenant_id':tenant},ref) is None
    # A reuse consumer cannot overwrite the retained candidate with its public text.
    evidence.persist(cur,TENANT,[REF],[{'id':REF['id'],**evidence.apply_reusable({'text':''},saved)}])
    receipt_db.commit()
    assert identifiers.candidate(TENANT,REF,evidence.reusable({'tenant_id':TENANT},REF))==found


def test_native_multiple_observations_do_not_keep_a_stale_typed_field(receipt_db):
    cur=receipt_db.cursor();saved=receipt()
    typed=identifiers.candidate(TENANT,REF,saved)
    evidence.persist(cur,TENANT,[REF],[{'id':REF['id'],'status':'processed',
        'observations':[{'text':OBS},{'text':'AnyDesk Your Address: 123456789'}],
        '_product_barcode_candidate':typed}]);receipt_db.commit()
    saved=evidence.reusable({'tenant_id':TENANT},REF)
    assert 'product_barcode_candidate' not in saved['result']
    assert identifiers.candidate(TENANT,REF,saved) is None


def test_native_cache_metadata_update_and_reuse_without_second_dispatch(receipt_db,monkeypatch):
    monkeypatch.setattr(cache,'_connect',lambda:receipt_db)
    monkeypatch.setattr(cache.store,'authorize',Mock(return_value=(A,None,None)))
    monkeypatch.setattr('tenant_knowledge_candidates_service.check_active',Mock())
    item={**cache_item(),'tenant':A};invoke=Mock(return_value=(OBS,{}))
    text,_,fresh=cache.observe_once({},item,REF,CONTENT,'image/png',{},invoke)
    assert fresh and EAN not in text
    rows=receipt_db.execute('SELECT transcript,metadata FROM saas_knowledge_media_cache WHERE tenant_id=%s::uuid',(A,))
    assert len(rows)==1 and EAN not in rows[0]['transcript']
    typed=rows[0]['metadata']['product_barcode_artifact']
    assert identifiers.read_artifact(typed,text,HASH)
    # Reuse the actual committed cache through the real SQL claim path.
    repeated={**cache_item(),'tenant':A}
    output,_,fresh=cache.observe_once({},repeated,REF,CONTENT,'image/png',{},invoke)
    assert output==text and not fresh and repeated['_product_barcode_artifact']==typed
    invoke.assert_called_once()


@pytest.mark.parametrize('producer',['episode','cache'])
@pytest.mark.parametrize('fixture',WIRE_FIXTURES,ids=lambda value:value['name'])
def test_real_responses_shape_producer_collect_persist_and_private_context(receipt_db,monkeypatch,producer,fixture):
    """HTTP is a fixture: real parsing/plumbing, not a claim about model vision."""
    from unittest.mock import AsyncMock
    from test_connect_images import PNG
    from test_episode_media import Authority,row
    from test_product_barcode_evidence import RAW
    import tenant_episode_media as episode
    import tenant_knowledge_media as media
    import tenant_chat_ai_provider as provider
    from tenant_operator_copilot import _bounded_conversation
    from tenant_ai_photo_model import INPUT_UPPER
    from tenant_knowledge_media_coverage import project

    photo=row('image',channel='connect:'+CHANNEL,external_id=REF['external_id'],raw=RAW)
    authority=Authority([photo]);authority.tenant=A;authority.channel=CHANNEL;authority.model='gpt-4.1'
    ref=episode.reference(photo,CHANNEL,authority.lead)
    item=authority.item(ref,{'text':'','ref':ref['id']})
    http=Mock(return_value=Mock(status_code=200,json=Mock(return_value=fixture['response'])))
    monkeypatch.setattr(provider.requests,'post',http)
    monkeypatch.setattr(media,'download_media',AsyncMock(return_value=(PNG,'image/png')))
    monkeypatch.setattr(media,'load',authority.load)
    monkeypatch.setattr(cache,'_connect',lambda:receipt_db)
    monkeypatch.setattr(cache.store,'authorize',Mock(return_value=(A,None,None)))
    monkeypatch.setattr('tenant_knowledge_candidates_service.check_active',Mock())
    # Clear only this synthetic disposable receipt, never a provider cache/hold.
    receipt_db.execute('DELETE FROM saas_media_evidence_receipts');receipt_db.commit()
    images,results=media.collect({'tenant_id':A},item,execution=authority if producer=='episode' else None)
    assert not images and results[0]['status']=='processed'
    http.assert_called_once()
    request=http.call_args.kwargs['json']
    assert identifiers.PRODUCER_RULE in request['instructions']
    assert 'Barcode on product packaging: <literal digits>.' in request['instructions']
    assert request['max_output_tokens']==600 and request['store'] is False
    assert request['input'][0]['content'][1]['type']=='input_image'
    assert request['input'][0]['content'][1]['detail']=='low'
    assert json.loads(request['input'][0]['content'][0]['text'])['conversation']==[]
    # Existing conservative byte-based bound remains below the quoted ceiling.
    envelope=json.dumps({'mode':'knowledge_visual_observation','conversation':[],'manager_draft':''},ensure_ascii=False)
    assert len(request['instructions'].encode())+len(envelope.encode())+85+256<=INPUT_UPPER
    evidence.persist(receipt_db.cursor(),A,[ref],results,processor='conversation_episode' if producer=='episode' else 'knowledge_extraction')
    receipt_db.commit()
    saved=evidence.reusable({'tenant_id':A},ref)
    assert bool(identifiers.candidate(A,ref,saved))==fixture['eligible']
    assert EAN not in json.dumps(evidence.apply_reusable({'text':''},saved))
    assert EAN not in json.dumps(project([ref],results,'completed',include_content=True))
    # Re-open the exact persisted source for a draft; no second vision request.
    monkeypatch.setattr(episode,'_flight',Mock(side_effect=AssertionError('No reanalysis')))
    http.side_effect=AssertionError('No second provider request')
    episode.prepare(authority,[photo])
    line=_bounded_conversation([photo])[0]
    assert (EAN in line['text'])==fixture['eligible']
    if fixture['eligible']:assert 'observed, unconfirmed' in line['text']
    http.assert_called_once()
