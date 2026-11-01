"""Exact typed receipt readback using full-schema disposable native PostgreSQL."""
import json
from unittest.mock import Mock

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
