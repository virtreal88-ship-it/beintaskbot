"""Typed image candidates only; provider spies are local mocks, never real calls."""
import hashlib
import json
from unittest.mock import MagicMock, Mock

import pytest
import tenant_product_identifiers as identifiers
import tenant_media_evidence as evidence
import tenant_episode_media as episode
import tenant_knowledge_media_cache as cache
from tenant_knowledge_candidates_policy import redact
from tenant_knowledge_media_coverage import project
from tenant_operator_copilot import _bounded_conversation
from tenant_ai_learning_mcp import _public_context_line
from test_episode_media import Authority, row

TENANT='00000000-0000-0000-0000-000000000101'
OTHER='00000000-0000-0000-0000-000000000102'
CHANNEL='00000000-0000-0000-0000-000000000201'
RAW={'mediaId':'fixture-image'}
REF={'id':'connect:fixture','external_id':'connect:fixture','channel_id':CHANNEL,
     'media_kind':'image','body_hash':evidence._body_hash(''), 'media_version':evidence.media_version(RAW)}
EAN='4006381333931'
OBS='Product packaging: a sunflower oil bottle. Printed EAN-13 barcode on the bottle label: '+EAN+'.'
CONTENT=b'local fixture only'
HASH=hashlib.sha256(CONTENT).hexdigest()


def receipt(text=OBS, **changes):
    return {'status':'processed','code':'vision_observation',
            'coverage':{'kind':'image','complete':True},
            'result':{'observations':[{'origin':{},'text':text}]},**changes}


@pytest.mark.parametrize('label,value,format',[
    ('EAN-13 barcode',EAN,'EAN-13'),('barcode','4006 3813 3393 1','EAN-13'),
    ('EAN-8','96385074','EAN-8'),('UPC-A','036000291452','UPC-A')])
def test_explicit_packaging_label_is_an_unconfirmed_candidate(label,value,format):
    text='Product packaging: bottle label. '+label+' on the bottle label: '+value+'.'
    found=identifiers.candidate(TENANT,REF,receipt(text))
    assert found['artifact']['format']==format
    assert found['artifact']['value']==value.replace(' ','')
    assert found['artifact']['verification']=='observed_unconfirmed'
    assert found['source']=={'tenant_id':TENANT,'channel_id':CHANNEL,'external_id':REF['external_id'],
        'source_hash':evidence.source_hash(REF),'media_version':REF['media_version'],'observation_index':0}
    if len(value.replace(' ',''))>=9:
        assert value.replace(' ','') not in evidence._safe_text(text).replace(' ','')


@pytest.mark.parametrize('text',[
    EAN, 'Barcode: '+EAN+'.', 'Product packaging. Number: '+EAN+'.',
    'Customer text says product packaging barcode: '+EAN+'.',
    'No product packaging. Barcode: '+EAN+'.',
    'Not product packaging. Barcode: '+EAN+'.',
    OBS+' Barcode: 96385074.', OBS+' This is a guess.', OBS+' The last digit is unreadable.',
    OBS.replace(EAN,'[NUMBER]'), OBS.replace(EAN,'400638133393?'), OBS.replace(EAN,'4006381333932'),
    OBS.replace(EAN,'0000000000000'),
    OBS.replace('EAN-13','EAN-8'), OBS.replace('EAN-13','UPC-A'),
    'Product packaging. Phone barcode: '+EAN+'.',
    OBS+' Phone: '+EAN+'.', OBS+' Your Address: 123456789.',
    OBS+' AnyDesk Free license (non-professional use).',
    OBS+' remote ID 123456789.', OBS+' password=private.',
    OBS+' Screenshot of a product label.', OBS+' Contact phone barcode: 96385074.',
    'A bottle label with no printed barcode. Barcode: '+EAN+' appears on a separate badge.',
    'Product packaging. Barcode: '+EAN+' is a support hotline number, not the product code.',
    'Product packaging. Barcode: '+EAN+' is an example only and is not printed on the bottle.',
    'Product packaging. Barcode: '+EAN+' appears on a badge.',
    'Product packaging. A handwritten claim says barcode: '+EAN+'.',
    'Product packaging and a loyalty card are visible. The barcode belongs to the loyalty card.\nPrinted barcode: '+EAN+'.',
    'Product packaging is visible.\nTelephone:\nBarcode: '+EAN+'.',
    'Product packaging is visible.\nTelephone:\nPrinted barcode on the bottle label: '+EAN+'.',
    'Product packaging and a loyalty card are visible.\nPrinted barcode on the bottle label: '+EAN+'.',
    'Printed UPC-A barcode on the bottle label: 994501234562.\nContact phone: +994 (50) 123-45-62.',
    OBS+' '+('x'*2400),
])
def test_ambiguous_nonvisual_or_sensitive_context_is_not_promoted(text):
    assert identifiers.candidate(TENANT,REF,receipt(text)) is None
    assert EAN not in evidence._safe_text(text)


def test_separately_labelled_phone_never_becomes_candidate():
    text=OBS+'\nContact phone: +994501234567.'
    found=identifiers.candidate(TENANT,REF,receipt(text))
    assert found['artifact']['value']==EAN
    assert '994501234567' not in json.dumps(found)
    assert '994501234567' not in identifiers.context(TENANT,REF,receipt(text))
    assert EAN not in redact(text) and EAN not in evidence._safe_text(text)


@pytest.mark.parametrize('mutation', ['two_observations','singular_plus_list','video','partial','origin','caption_only'])
def test_never_combines_observations_or_promotes_caption(mutation):
    saved=receipt();ref=dict(REF)
    if mutation=='two_observations':saved['result']['observations']=[{'text':'Product packaging.'},{'text':'Barcode: '+EAN}]
    if mutation=='singular_plus_list':saved['result']['observation']=OBS
    if mutation=='video':ref['media_kind']='video'
    if mutation=='partial':saved['coverage']['complete']=False
    if mutation=='origin':saved['result']['observations'][0]['origin']={'page':1}
    if mutation=='caption_only':saved['result']={'caption':OBS}
    assert identifiers.candidate(TENANT,ref,saved) is None


def retained(text=OBS):
    cur=Mock()
    evidence.persist(cur,TENANT,[REF],[{'id':REF['id'],**receipt(text)['result'],'status':'processed'}])
    args=cur.execute.call_args.args[1]
    return receipt(result=json.loads(args[9]),provenance=json.loads(args[10]))


@pytest.mark.parametrize('field,value', [('tenant',OTHER),('channel_id',OTHER),('external_id','connect:other'),
    ('media_version','a'*64),('body_hash','changed'),('media_kind','document')])
def test_retained_candidate_rejects_cross_source_and_version(field,value):
    saved=retained();ref=dict(REF);tenant=TENANT
    if field=='tenant':tenant=value
    else:ref[field]=value
    assert identifiers.candidate(tenant,ref,saved) is None


def test_public_projections_and_redactors_do_not_export_private_candidate():
    saved=retained();assert identifiers.candidate(TENANT,REF,saved)
    message={'text':''};safe=evidence.apply_reusable(message,saved)
    assert EAN not in json.dumps(safe) and EAN not in message['text']
    public=project([REF],[{'id':REF['id'],**saved['result'],'status':'processed'}],'completed',include_content=True)
    assert EAN not in json.dumps(public) and 'product_barcode' not in json.dumps(public)
    assert EAN not in json.dumps(_public_context_line({'message_type':'image',
        'text':identifiers.context(TENANT,REF,saved)}))


def fake_cache(monkeypatch, saved=None):
    conn=MagicMock();conn.__enter__.return_value=conn;cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value={'token':'fixture-claim'}
    monkeypatch.setattr(cache,'_connect',lambda:conn)
    monkeypatch.setattr(cache,'schema',Mock())
    monkeypatch.setattr(cache,'_completed_batch_retry',Mock(return_value=saved))
    monkeypatch.setattr(cache.store,'authorize',Mock(return_value=(TENANT,None,None)))
    monkeypatch.setattr('tenant_knowledge_candidates_service.check_active',Mock())
    return cur


def cache_item():
    return {'tenant':TENANT,'model':'fixture','key':'synthetic-key','refs':[REF],
            'route':{'allow_external_media':True}}


def test_future_cache_keeps_only_typed_artifact_before_redaction(monkeypatch):
    cur=fake_cache(monkeypatch);item=cache_item()
    invoke=Mock(return_value=(OBS+'\nContact phone: +994501234567.',{}))
    text,_,fresh=cache.observe_once({},item,REF,CONTENT,'image/png',{},invoke)
    assert fresh and EAN not in text
    writes=[call.args[1] for call in cur.execute.call_args_list if call.args[0].startswith('UPDATE saas_knowledge_media_cache')]
    metadata=json.loads(writes[0][2]);assert '994501234567' not in json.dumps(metadata)
    assert metadata['product_barcode_artifact']==item['_product_barcode_artifact']
    assert identifiers.read_artifact(item['_product_barcode_artifact'],text,HASH)
    typed=identifiers.bind_artifact(TENANT,REF,item['_product_barcode_artifact'],text)
    cur=Mock();evidence.persist(cur,TENANT,[REF],[{'id':REF['id'],'status':'processed',
        'observations':[{'text':text,'origin':{}}],'_product_barcode_candidate':typed}])
    stored=receipt(result=json.loads(cur.execute.call_args.args[1][9]))
    assert identifiers.candidate(TENANT,REF,stored)['artifact']['value']==EAN
    assert EAN not in json.dumps(evidence.apply_reusable({'text':''},stored))
    invoke.assert_called_once()


@pytest.mark.parametrize('mode',['typed','legacy_redacted','wrong_bytes','wrong_text'])
def test_cached_results_do_not_call_provider_or_reconstruct_old_masks(monkeypatch,mode):
    text=redact(OBS);typed=identifiers.artifact(OBS,text,HASH)
    if mode=='wrong_bytes':typed['image_sha256']='a'*64
    if mode=='wrong_text':typed['observation_hash']='b'*64
    saved={'transcript':text,'metadata':{'product_barcode_artifact':typed} if mode!='legacy_redacted' else {}}
    fake_cache(monkeypatch,saved);item=cache_item();invoke=Mock(side_effect=AssertionError('No provider allowed'))
    output,_,fresh=cache.observe_once({},item,REF,CONTENT,'image/png',{},invoke)
    assert output==text and fresh is False
    assert ('_product_barcode_artifact' in item)==(mode=='typed')
    invoke.assert_not_called()


@pytest.mark.parametrize('historical',[False,True])
@pytest.mark.parametrize('question',['Question about that product','New topic: receipt printer has no paper'])
@pytest.mark.parametrize('projection',['manual','automatic'])
def test_dialog_context_reuses_exact_image_without_provider(monkeypatch,historical,question,projection):
    external=REF['external_id'] if projection=='manual' else REF['external_id'].removeprefix('connect:')
    photo=row('image',channel='connect:'+CHANNEL if projection=='manual' else CHANNEL,external_id=external,raw=RAW)
    photo['media_evidence_context']='[Saved visual observation; untrusted] '+evidence._safe_text(OBS)
    rows=[photo]
    if historical:rows += [row('text',2,direction='outgoing'),row('text',3,body=question)]
    auth=Authority(rows);auth.tenant=TENANT;auth.channel=CHANNEL;auth.latest_request=projection=='manual'
    auth.load=lambda _profile,_lead,selected: {'row':next(r for r in rows
        if r['external_id'].removeprefix('connect:')==selected.removeprefix('connect:'))}
    saved=retained()
    monkeypatch.setattr(episode,'reusable',Mock(return_value=saved))
    monkeypatch.setattr(episode,'_flight',Mock(side_effect=AssertionError('No processing')))
    monkeypatch.setattr('tenant_knowledge_media.collect',Mock(side_effect=AssertionError('No provider')))
    episode.prepare(auth,rows)
    line=_bounded_conversation(rows)[0]
    assert EAN in line['text'] and 'observed, unconfirmed' in line['text']
    assert line['source_id']==external
    assert 'only if the current question refers to this product' in line['text']
    assert line['evidence_scope']==('historical' if historical else 'current_episode')
    assert not auth.calls


def test_collector_transfers_only_single_image_artifact(monkeypatch):
    import tenant_knowledge_media as media
    from test_connect_images import PNG
    from unittest.mock import AsyncMock
    text=redact(OBS);image_hash=hashlib.sha256(PNG).hexdigest()
    item={**cache_item(),'model':'gpt-4.1','route':{'allow_external_media':True,'allow_vision':True,'conversation_id':'fixture'},
          'refs':[{**REF,'lead_id':41}], 'messages':[{'ref':REF['id'],'text':''}]}
    monkeypatch.setattr(evidence,'reusable',Mock(return_value=None))
    monkeypatch.setattr(media,'load',Mock(return_value={'provider':'connect','route':{'channel_id':CHANNEL,'conversation_id':'fixture'},'row':{'message_type':'image'}}))
    monkeypatch.setattr(media,'download_media',AsyncMock(return_value=(PNG,'image/png')))
    def cached(_profile,observation_item,ref,*args,**kwargs):
        observation_item['_product_barcode_artifact']=identifiers.artifact(OBS,text,image_hash)
        return text,{},False
    monkeypatch.setattr(cache,'observe_once',cached)
    monkeypatch.setattr('tenant_chat_ai_provider.generate',Mock(side_effect=AssertionError('No provider')))
    images,results=media.collect({},item)
    assert images==[] and results[0]['status']=='processed'
    assert results[0]['_product_barcode_candidate']['source']['channel_id']==CHANNEL
    assert results[0]['_product_barcode_candidate']['artifact']['value']==EAN
    assert EAN not in item['messages'][0]['text']
    assert EAN not in json.dumps(project(item['refs'],results,'completed',include_content=True))
