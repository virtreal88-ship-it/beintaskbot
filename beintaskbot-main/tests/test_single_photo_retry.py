"""Real SQL and synthetic bytes; no network, production source or paid calls."""
import hashlib
import json
import subprocess
import uuid
from unittest.mock import Mock, AsyncMock

import pytest
pytest_plugins=('test_tenant_support_inbox',)
from test_tenant_support_inbox import db,A,B,CA,CB,AGENT,PROFILE,seed_job
from test_knowledge_extraction_preflight import _cfg,_source,_pricing
from test_connect_images import PNG
import tenant_ai_single_message_extraction as consent
import tenant_ai_single_photo_retry as retry
import tenant_ai_knowledge_extraction_preflight as preflight
import tenant_ai_learning_mcp as mcp
import tenant_knowledge_candidates_store as store
import tenant_knowledge_candidates_service as service
import tenant_knowledge_campaigns as campaigns
import tenant_knowledge_campaign_worker as worker
import tenant_knowledge_media_cache as cache
import tenant_knowledge_media as media
import tenant_media_evidence as evidence
import tenant_ai_spend_budget as spend
import tenant_chat_ai_provider as provider
import tenant_ai_administrator_read as admin
from tenant_linear_policy import TenantLinearError


def complete(database,cfg,prepared,**kw):
    meta=prepared['_component_meta']['owner_single_extraction']
    result=consent.complete(database.cursor(),cfg,consent_id=meta['consent_ids']['approve'],
      capability=meta['capabilities']['approve'],action='approve',**kw)
    database.commit();return result


@pytest.fixture
def prior(db,monkeypatch):
    seed_job(db);_pricing(monkeypatch);cfg=_cfg();mcp.ensure_mcp_schema(db)
    monkeypatch.setenv('AI_OWNER_COMPONENT_APPROVAL_ENABLED','true')
    monkeypatch.setenv('AI_AGENTS_VISION_MODELS','fixture-model')
    for module in (store,service,campaigns,worker,cache,evidence,spend):monkeypatch.setattr(module,'_connect',lambda:db)
    monkeypatch.setattr(service,'choose_api_credential',lambda *a:('synthetic','platform'))
    monkeypatch.setattr(service,'platform_token_limit',lambda *a:0)
    db.execute('''INSERT INTO saas_ai_learning_mcp_tokens
      (id,tenant_id,token_hash,agent_id,channel_id,client_id,scope,oauth_connection_id,created_by,expires_at)
      VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s::uuid,'test','ai_learning.read ai_learning.drafts',%s::uuid,11,now()+interval '1 hour')''',
      (cfg['oauth_token_id'],A,cfg['oauth_token_id'],AGENT,CA,cfg['oauth_connection_id']))
    message=_source(db)
    db.execute("UPDATE saas_crm_messages SET message_type='image',body='Sayımda fərq görünür',external_id=%s WHERE id=%s::uuid",('connect:'+str(uuid.uuid4()),message));db.commit()
    args={'message_id':message,'channel_id':CA,'lead_id':41}
    source=preflight._source(db.cursor(),cfg,args)
    first=complete(db,cfg,consent.prepare(db.cursor(),cfg,args))
    fragment=first['fragment_id'];event='synthetic-parent-'+fragment
    route=db.execute('SELECT route FROM saas_knowledge_campaign_fragments WHERE id=%s::uuid',(fragment,))[0]['route']
    ref={'id':message,'channel_id':CA,'lead_id':41,'external_id':source['external_id'],
      'media_kind':'image','media_version':source['version'],'body_hash':store.digest('Sayımda fərq görünür'),'role':'customer','text_limit':180}
    digest=hashlib.sha256(PNG).hexdigest()
    result={'id':message,'channel_id':CA,'kind':'image','status':'unknown','code':'media_result_unknown','failure_stage':'visual_analysis','sha256':digest}
    meta={'kind':'vision','version':'observations-v1','model':'fixture-model','detail':'low','channel_id':CA,'mime':'image/png','origin':{},'sha256':digest}
    cache.schema(db.cursor());fp=store.digest(meta);token=str(uuid.uuid4())
    db.execute("INSERT INTO saas_knowledge_media_cache(tenant_id,fingerprint,state,token,metadata) VALUES(%s::uuid,%s,'unknown',%s::uuid,%s::jsonb)",(A,fp,token,json.dumps(meta)))
    db.execute("INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider) VALUES(%s::uuid,%s,'connect')",(A,event))
    db.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state) VALUES(%s::uuid,%s::uuid,%s::uuid,%s,1,'fixture-model','unknown')",(A,fragment,AGENT,event))
    db.execute('''INSERT INTO saas_knowledge_extractions
      (tenant_id,id,created_by,route,source_refs,source_digest,fingerprint,request_key,status,media_results,deadline,completed_at)
      VALUES(%s::uuid,%s::uuid,11,%s::jsonb,%s::jsonb,'old',%s,%s,'unknown',%s::jsonb,now()-interval '1 minute',now())''',
      (A,fragment,json.dumps(route),json.dumps([ref]),fragment,event,json.dumps([result])))
    db.execute("UPDATE saas_knowledge_campaign_fragments SET status='unknown',extraction_id=id WHERE id=%s::uuid",(fragment,))
    db.execute("UPDATE saas_knowledge_campaigns SET status='stopped' WHERE id=%s::uuid",(first['campaign_id'],))
    for kind,amount in (('text',300),('vision',400)):
        db.execute("INSERT INTO saas_ai_spend_reservations(tenant_id,id,call_key,model,kind,reserved_microusd,state) VALUES(%s::uuid,%s::uuid,%s,'fixture-model',%s,%s,'unknown')",
          (A,str(uuid.uuid4()),'parent:'+kind,kind,amount))
    db.commit()
    args.update(retry_of=fragment,source_version=source['version'],request_id=str(uuid.uuid4()))
    return db,cfg,args,fp,ref


def prepare_and_approve(prior):
    db,cfg,args,_,_=prior
    prepared=consent.prepare(db.cursor(),cfg,args);db.commit()
    return prepared,complete(db,cfg,prepared,acknowledge_prior_unknown=True)


def test_owner_retry_one_use_actual_bytes_to_observation_and_receipt(prior,monkeypatch):
    db,cfg,args,fp,ref=prior
    old=db.execute('SELECT * FROM saas_knowledge_extractions WHERE id=%s::uuid',(args['retry_of'],))[0]
    old_cache=db.execute('SELECT * FROM saas_knowledge_media_cache WHERE fingerprint=%s',(fp,))[0]
    prepared,result=prepare_and_approve(prior)
    assert prepared['exact_preview']['maximum_additional_microusd']==22
    assert prepared['exact_preview']['prior_charge_unknown'] is True
    again=complete(db,cfg,prepared,acknowledge_prior_unknown=True)
    assert again['replayed'] and again['fragment_id']==result['fragment_id']
    claim=campaigns.claim(A,result['campaign_id']);assert claim
    assert campaigns.claim(A,result['campaign_id']) is None
    current={'provider':'connect','route':{'channel_id':CA,'conversation_id':'client'},'row':{'message_type':'image'}}
    monkeypatch.setattr(media,'load',lambda *a:current)
    download=AsyncMock(return_value=(PNG,'image/png'));monkeypatch.setattr(media,'download_media',download)
    vision=Mock(return_value=('Visible synthetic inventory quantity: 7',{'input_tokens':5,'output_tokens':5,'total_tokens':10}))
    main=Mock(return_value=('{"cards":[]}',{'input_tokens':10,'output_tokens':5,'total_tokens':15}))
    monkeypatch.setattr(provider,'generate',vision);monkeypatch.setattr(service,'generate',main)
    response=service.analyze(PROFILE,{**claim['route'],'request_id':str(claim['id'])})
    assert response['request']['status']=='completed'
    visible=admin.read(db.cursor(),{**cfg,'granted_scopes':[admin.READ_SCOPE,admin.CONTENT_SCOPE]},
      'case_evidence_read',{'id':str(claim['id']),'include_content':True})['items'][0]
    assert visible['media_results'][0]['result']['observations'][0]['text']=='Visible synthetic inventory quantity: 7'
    assert download.await_count==vision.call_count==main.call_count==1
    assert 'Visible synthetic inventory quantity: 7' in str(main.call_args)
    assert campaigns.complete(claim,response)
    replay=service.analyze(PROFILE,{**claim['route'],'request_id':str(claim['id'])})
    assert replay['request']['id']==response['request']['id'] and vision.call_count==1
    assert db.execute('SELECT * FROM saas_knowledge_extractions WHERE id=%s::uuid',(args['retry_of'],))[0]==old
    assert db.execute('SELECT * FROM saas_knowledge_media_cache WHERE fingerprint=%s',(fp,))[0]==old_cache
    held=db.execute("SELECT state,reserved_microusd,actual_microusd FROM saas_ai_spend_reservations WHERE call_key LIKE 'parent:%%'")
    assert len(held)==2 and sum(x['reserved_microusd'] for x in held)==700
    assert all(x['state']=='unknown' and x['actual_microusd'] is None for x in held)
    child=db.execute("SELECT state,reserved_microusd FROM saas_ai_spend_reservations WHERE call_key LIKE %s",('extraction:'+str(claim['id'])+'%',))
    assert len(child)==2 and all(x['state']=='settled' for x in child)
    assert sum(x['reserved_microusd'] for x in child)==prepared['exact_preview']['maximum_additional_microusd']
    assert evidence.reusable(PROFILE,ref)['result']['observations'][0]['text']=='Visible synthetic inventory quantity: 7'
    assert download.await_count==vision.call_count==1


@pytest.mark.parametrize('change',['source','evidence_hash','tenant','token','expiry','price','budget','parent','cache','ack'])
def test_owner_retry_fails_closed_before_enqueue(prior,change,monkeypatch):
    db,cfg,args,fp,_=prior
    prepared=consent.prepare(db.cursor(),cfg,args);db.commit()
    if change=='source':db.execute("UPDATE saas_crm_messages SET raw='{}'::jsonb WHERE id=%s::uuid",(args['message_id'],))
    if change=='evidence_hash':db.execute("UPDATE saas_knowledge_extractions SET source_refs=jsonb_set(source_refs,'{0,body_hash}',to_jsonb('changed'::text)) WHERE id=%s::uuid",(args['retry_of'],))
    if change=='tenant':cfg={**cfg,'AI_LEARNING_MCP_TENANT_ID':B,'actor_id':22,'AI_LEARNING_MCP_CHANNEL_ID':CB}
    if change=='token':db.execute('UPDATE saas_ai_learning_mcp_tokens SET revoked_at=now() WHERE id=%s::uuid',(cfg['oauth_token_id'],))
    if change=='expiry':db.execute("UPDATE saas_ai_single_message_extraction_consents SET expires_at=now()-interval '1 second' WHERE id=%s::uuid",(prepared['consent_id'],))
    if change=='price':monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({'fixture-model':{'vision_low':11}}))
    if change=='budget':monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:721}))
    if change=='parent':db.execute("UPDATE saas_knowledge_extractions SET status='completed' WHERE id=%s::uuid",(args['retry_of'],))
    if change=='cache':db.execute('DELETE FROM saas_knowledge_media_cache WHERE fingerprint=%s',(fp,))
    db.commit()
    with pytest.raises(TenantLinearError):complete(db,cfg,prepared,acknowledge_prior_unknown=change!='ack')
    db.rollback()
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_campaigns')[0]['n']==1


@pytest.mark.parametrize('change',['revoke','token','source','price'])
def test_revoked_or_changed_retry_between_enqueue_and_provider(prior,change,monkeypatch):
    db,cfg,args,_,_=prior;_,result=prepare_and_approve(prior)
    claim=campaigns.claim(A,result['campaign_id']);assert claim
    if change=='revoke':db.execute("UPDATE saas_ai_single_message_extraction_consents SET state='revoked' WHERE id=%s::uuid",(result['consent_id'],))
    if change=='token':db.execute('UPDATE saas_ai_learning_mcp_tokens SET revoked_at=now() WHERE id=%s::uuid',(cfg['oauth_token_id'],))
    if change=='source':db.execute("UPDATE saas_crm_messages SET external_id='changed' WHERE id=%s::uuid",(args['message_id'],))
    if change=='price':monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({'fixture-model':{'vision_low':11}}))
    db.commit()
    with pytest.raises(TenantLinearError):worker.check_fence(db.cursor(),A,claim['id'])
    assert db.execute("SELECT count(*) AS n FROM saas_ai_spend_reservations WHERE call_key NOT LIKE 'parent:%%'")[0]['n']==0


def test_unapproved_retry_flag_cannot_bypass_unknown_parent(prior):
    db,cfg,args,_,_=prior
    with pytest.raises(TenantLinearError):
        service.prepare(PROFILE,{'request_id':str(uuid.uuid4()),'retry_of':args['retry_of'],
          'channel_id':CA,'conversation_id':'client','analysis_agent_id':AGENT,'message_ids':[args['message_id']]})


def test_stale_version_cannot_prepare_retry(prior):
    db,cfg,args,_,_=prior
    with pytest.raises(TenantLinearError,match='source version changed'):
        consent.prepare(db.cursor(),cfg,{**args,'source_version':'0'*64})


def test_saved_exact_observation_avoids_retry_download_and_vision(prior,monkeypatch):
    db,cfg,args,fp,ref=prior;_,result=prepare_and_approve(prior)
    evidence.create_schema(db)
    evidence.persist(db.cursor(),A,[ref],[{'id':ref['id'],'status':'processed',
      'code':'vision_observation','observation':'Previously saved synthetic quantity: 7'}]);db.commit()
    claim=campaigns.claim(A,result['campaign_id']);assert claim
    download=AsyncMock(side_effect=AssertionError('must reuse'));vision=Mock(side_effect=AssertionError('must reuse'))
    main=Mock(return_value=('{"cards":[]}',{'input_tokens':10,'output_tokens':5,'total_tokens':15}))
    monkeypatch.setattr(media,'download_media',download);monkeypatch.setattr(provider,'generate',vision)
    monkeypatch.setattr(service,'generate',main)
    response=service.analyze(PROFILE,{**claim['route'],'request_id':str(claim['id'])})
    assert response['request']['status']=='completed'
    assert 'Previously saved synthetic quantity: 7' in str(main.call_args)
    assert download.await_count==vision.call_count==0 and main.call_count==1
    assert db.execute('SELECT state FROM saas_knowledge_media_cache WHERE fingerprint=%s',(fp,))[0]['state']=='unknown'
    assert db.execute("SELECT state FROM saas_ai_spend_reservations WHERE call_key=%s",('extraction:'+str(claim['id'])+':vision',))[0]['state']=='released'


def test_one_retry_cannot_retry_unknown_child_or_change_image_bytes(prior):
    db,cfg,args,fp,ref=prior;_,result=prepare_and_approve(prior)
    claim=campaigns.claim(A,result['campaign_id']);assert claim
    item={'tenant':A,'id':str(claim['id']),'route':claim['route'],'_owner_retry_id':result['consent_id']}
    with pytest.raises(TenantLinearError,match='bytes changed'):
        retry.cache_namespace(db.cursor(),item,fp,{'sha256':'a'*64})
    db.execute("UPDATE saas_knowledge_campaign_fragments SET status='unknown' WHERE id=%s::uuid",(claim['id'],));db.commit()
    with pytest.raises(TenantLinearError):
        consent.prepare(db.cursor(),cfg,{**args,'retry_of':str(claim['id']),'request_id':str(uuid.uuid4())})
    assert campaigns.claim(A,result['campaign_id']) is None


def test_retry_unknown_provider_outcome_is_never_dispatched_again(prior,monkeypatch):
    db,cfg,args,fp,_=prior;prepared,result=prepare_and_approve(prior)
    claim=campaigns.claim(A,result['campaign_id']);assert claim
    monkeypatch.setattr(media,'load',lambda *a:{'provider':'connect','route':{'channel_id':CA,'conversation_id':'client'},'row':{'message_type':'image'}})
    download=AsyncMock(return_value=(PNG,'image/png'));monkeypatch.setattr(media,'download_media',download)
    vision=Mock(side_effect=TimeoutError('synthetic timeout'));main=Mock(side_effect=TimeoutError('synthetic main timeout'))
    monkeypatch.setattr(provider,'generate',vision);monkeypatch.setattr(service,'generate',main)
    data={**claim['route'],'request_id':str(claim['id'])}
    response=service.analyze(PROFILE,data)
    assert response['request']['status']=='unknown'
    assert campaigns.complete(claim,response)
    assert service.analyze(PROFILE,data)['request']['status']=='unknown'
    assert download.await_count==vision.call_count==main.call_count==1
    assert campaigns.claim(A,result['campaign_id']) is None
    assert complete(db,cfg,prepared,acknowledge_prior_unknown=True)['fragment_id']==result['fragment_id']
    with pytest.raises(TenantLinearError):
        consent.prepare(db.cursor(),cfg,{**args,'retry_of':str(claim['id']),'request_id':str(uuid.uuid4())})
    assert db.execute("SELECT count(*) AS n FROM saas_knowledge_media_cache WHERE state='unknown'")[0]['n']==2
    assert int(db.execute("SELECT sum(reserved_microusd) AS n FROM saas_ai_spend_reservations WHERE call_key LIKE 'parent:%%' AND state='unknown'")[0]['n'])==700


def test_atomic_reservation_still_counts_old_holds_after_enqueue(prior,monkeypatch):
    db,_,_,_,_=prior;_,result=prepare_and_approve(prior)
    claim=campaigns.claim(A,result['campaign_id']);assert claim
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:721}))
    with pytest.raises(TenantLinearError,match='ai_spend_budget_exhausted'):
        service.prepare(PROFILE,{**claim['route'],'request_id':str(claim['id'])})
    db.rollback()
    assert db.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations')[0]['n']==2
    assert db.execute("SELECT count(*) AS n FROM saas_knowledge_extractions WHERE status='running'")[0]['n']==0


def test_completion_replay_after_expiry_returns_prior_receipt_without_enqueue(prior):
    db,cfg,_,_,_=prior;prepared,result=prepare_and_approve(prior)
    db.execute("UPDATE saas_ai_single_message_extraction_consents SET expires_at=now()-interval '1 second' WHERE id=%s::uuid",(result['consent_id'],));db.commit()
    assert complete(db,cfg,prepared,acknowledge_prior_unknown=True)['fragment_id']==result['fragment_id']
    assert campaigns.claim(A,result['campaign_id']) is None
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_campaign_fragments')[0]['n']==2


def test_retry_widget_requires_explicit_unknown_charge_acknowledgment():
    script=__import__('tenant_ai_owner_review_component').resource_html().split('<script>',1)[1].split('</script>',1)[0]
    harness=r'''
const vm=require('vm'),fs=require('fs');const e={status:{},diff:{},approve:{},reject:{},publish:{},retryNotice:{},retryAcknowledge:{}};const calls=[];
const api={toolResponseMetadata:{owner_single_extraction:{consent_ids:{approve:'a',reject:'r'},capabilities:{approve:'x'.repeat(32),reject:'y'.repeat(32)},exact_preview:{one_use_retry:true,maximum_additional_microusd:22}}},callTool:async(n,a)=>{calls.push(a);return {structuredContent:{state:'approved'}};}};
vm.runInNewContext(fs.readFileSync(0,'utf8'),{window:{openai:api,parent:{},addEventListener:()=>{}},document:{querySelector:q=>e[q.slice(1)]},JSON,Error});
(async()=>{if(!e.approve.disabled||e.retryNotice.hidden)throw Error('missing ack');await e.approve.onclick();if(calls.length)throw Error('silent ack');e.retryAcknowledge.checked=true;e.retryAcknowledge.onchange();if(e.approve.disabled)throw Error('disabled');await e.approve.onclick();if(calls.length!==1||calls[0].acknowledge_prior_unknown!==true)throw Error('not bound');})().catch(()=>process.exit(1));
'''
    result=subprocess.run(['node','-e',harness],input=script,text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr
