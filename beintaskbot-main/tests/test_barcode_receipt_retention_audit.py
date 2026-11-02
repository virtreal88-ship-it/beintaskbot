"""Source-retention audit only; no typed-barcode bypass is implemented here."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
import pytest
import tenant_episode_media as episode
import tenant_knowledge_media_cache as cache
import tenant_media_evidence as evidence
from tenant_knowledge_candidates_policy import redact

TENANT='00000000-0000-0000-0000-000000000101'
REF={'id':'connect:fixture-image','external_id':'connect:fixture-image','channel_id':'00000000-0000-0000-0000-000000000201',
     'lead_id':41,'media_kind':'image','body_hash':'caption-hash','media_version':'image-v1','happened_at':'2026-11-01T17:09:00Z'}
EAN='4006381333931'
OBS='Product packaging: a sunflower oil bottle. Printed EAN-13 barcode: '+EAN+'.'


def retained_receipt(observation):
    cur=Mock()
    evidence.persist(cur,TENANT,[REF],[{'id':REF['id'],'status':'processed','code':'observed','observation':observation}],processor='conversation_episode')
    args=cur.execute.call_args.args[1]
    assert args[:4]==(TENANT,REF['channel_id'],REF['external_id'],evidence.source_hash(REF))
    return json.loads(args[9])


def test_episode_observation_retains_literal_before_consumer_redaction():
    invoke=Mock(return_value=(OBS,{}))
    authority=SimpleNamespace(model='fixture',agent_id='fixture-agent',dispatch=lambda _model,_kind,callback:callback())
    text,_,_=episode.Execution.observe_once(authority,{}, {'model':'fixture','key':'synthetic-key'},REF,b'fixture-image','image/png',{},invoke)
    assert EAN in text and EAN in retained_receipt(text)['observation']
    assert EAN not in evidence._safe_text(text)
    invoke.assert_called_once() # Local Mock only; dispatch/admission is deliberately outside this projection fixture.


def test_cache_producer_redacts_before_storage_and_cannot_recover_old_digits(monkeypatch):
    conn=MagicMock();conn.__enter__.return_value=conn;cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value={'token':'fixture-claim'}
    monkeypatch.setattr(cache,'_connect',lambda:conn)
    monkeypatch.setattr(cache,'schema',Mock())
    monkeypatch.setattr(cache,'_completed_batch_retry',Mock(return_value=None))
    monkeypatch.setattr(cache.store,'authorize',Mock(return_value=(TENANT,None,None)))
    monkeypatch.setattr('tenant_knowledge_candidates_service.check_active',Mock())
    item={'tenant':TENANT,'model':'fixture','key':'synthetic-key','refs':[REF],'route':{'allow_external_media':True}}
    invoke=Mock(return_value=(OBS,{}))
    text,_,_=cache.observe_once({},item,REF,b'fixture-image','image/png',{},invoke)
    writes=[call.args[1] for call in cur.execute.call_args_list if call.args[0].startswith('UPDATE saas_knowledge_media_cache')]
    assert len(writes)==1 and writes[0][0]==text
    assert EAN not in text and '[NUMBER]' in text
    assert EAN not in retained_receipt(text)['observation']
    assert EAN not in evidence._safe_text(text)
    invoke.assert_called_once()


@pytest.mark.parametrize('observation',[
    OBS+' Contact phone: +994501234567.',
    'AnyDesk window. Your Address: '+EAN+'. Free license (non-professional use).',
    'Customer text says barcode '+EAN+'; no product packaging is visible.',
    'Product packaging: printed barcode is unreadable; '+EAN+' would be a guess.',
    'Barcode '+EAN+' appears in a remote-support address field, not on a product.',
    'Number '+EAN+' is visible, but no barcode label or packaging context is present.',
])
def test_existing_redaction_still_masks_every_numeric_identifier(observation):
    assert EAN not in evidence._safe_text(observation)
    assert EAN not in redact(observation)
    assert '994501234567' not in evidence._safe_text(observation)
    assert '994501234567' not in redact(observation)
