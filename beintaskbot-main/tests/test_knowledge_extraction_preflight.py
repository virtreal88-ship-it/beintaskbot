"""Scoped owner consent tests: all provider paths remain mocked/uninvoked."""
import json
import subprocess
import uuid
import pytest

pytest_plugins=('test_tenant_support_inbox',)
from test_tenant_support_inbox import db, A, B, CA, CB, AGENT, seed_job
from tenant_linear_policy import TenantLinearError
import tenant_ai_knowledge_extraction_preflight as preflight
import tenant_ai_single_message_extraction as consent
import tenant_ai_owner_review_component as component
import tenant_ai_learning_mcp as mcp
import tenant_knowledge_campaign_worker as worker
import tenant_knowledge_campaigns as campaigns
import tenant_knowledge_candidates_service as extraction_service
import tenant_ai_spend_budget as spend_budget
import tenant_knowledge_media as knowledge_media


def _cfg():
    return {'AI_LEARNING_MCP_TENANT_ID':A,'AI_LEARNING_MCP_AGENT_ID':AGENT,
      'AI_LEARNING_MCP_CHANNEL_ID':CA,'actor_id':11,'granted_scopes':['ai_learning.read','ai_learning.drafts'],
      'oauth_connection_id':str(uuid.uuid4()),'oauth_token_id':str(uuid.uuid4())}


def _source(database, *, tenant=A, channel=CA, lead=41):
    message=str(uuid.uuid4())
    database.execute("""INSERT INTO saas_crm_messages
      (tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,raw)
      VALUES(%s::uuid,%s::uuid,%s,%s,'connect:'||%s,'incoming','redacted','video',%s::jsonb)""",
      (tenant,message,lead,'synthetic-video:'+message,channel,json.dumps({'mediaId':'synthetic','fileLength':100,'timestamp':1})))
    database.commit();return message


def _pricing(monkeypatch):
    monkeypatch.setenv('AI_AGENTS_PLATFORM_MODELS',json.dumps(['fixture-model']))
    monkeypatch.setenv('AI_AGENTS_PLATFORM_DEFAULT_MODEL','fixture-model')
    monkeypatch.setenv('AI_AGENTS_PLATFORM_ENABLED','true')
    monkeypatch.setenv('OPENAI_API_KEY','test-only-not-a-real-key')
    monkeypatch.setenv('AI_AGENTS_TRANSCRIPTION_MODEL','fixture-asr')
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({'fixture-model':{'input':1,'output':1}}))
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({'fixture-model':{'vision_low':10},'fixture-asr':{'asr':10}}))
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:5_000_000}))


def _bridge(monkeypatch, cfg):
    monkeypatch.setenv('AI_OWNER_COMPONENT_APPROVAL_ENABLED','true')
    monkeypatch.setattr(consent,'_connection_profile',lambda cur,cfg,required_scopes: (A,11,cfg['oauth_connection_id']))


def test_preflight_is_read_only_and_requires_exact_owner_consent(db,monkeypatch):
    seed_job(db);_pricing(monkeypatch);message=_source(db);cfg=_cfg()
    value=preflight.preflight(db.cursor(),cfg,{'message_id':message,'channel_id':CA,'lead_id':41})
    assert value['ready'] is False and value['reason_code']=='owner_consent_required'
    assert value['message_id']==message and value['provider_domain']=='api.openai.com'
    assert 'conversation_id' not in value and value['credential_source']=='platform'
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_extractions')[0]['n']==0
    assert db.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations')[0]['n']==0


def test_component_consent_queues_only_exact_fragment_and_is_idempotent(db,monkeypatch):
    seed_job(db);_pricing(monkeypatch);cfg=_cfg();_bridge(monkeypatch,cfg);message=_source(db)
    consent.ensure_schema(db);prepared=consent.prepare(db.cursor(),cfg,{'message_id':message,'channel_id':CA,'lead_id':41})
    assert prepared['state']=='pending' and 'capability' not in prepared
    meta=prepared['_component_meta']['owner_single_extraction'];db.commit()
    wire=json.loads(mcp._tool_response(prepared,1).body.decode())['result']
    assert meta['capabilities']['approve'] not in json.dumps(wire['content'])
    assert meta['capabilities']['approve'] not in json.dumps(wire['structuredContent'])
    result=consent.complete(db.cursor(),cfg,consent_id=meta['consent_ids']['approve'],capability=meta['capabilities']['approve'],action='approve');db.commit()
    assert result['state']=='approved' and result['execution']=='queued_existing_guarded_campaign_worker'
    row=db.execute('''SELECT c.sources,c.status,f.status AS fragment_status,f.route FROM saas_knowledge_campaigns c
      JOIN saas_knowledge_campaign_fragments f ON f.tenant_id=c.tenant_id AND f.campaign_id=c.id
      WHERE c.tenant_id=%s::uuid''',(A,))[0]
    assert row['sources']==[CA] and row['status']=='running' and row['fragment_status']=='pending'
    assert row['route']['message_ids']==[message] and row['route']['channel_id']==CA and row['route']['source_version']
    assert db.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations')[0]['n']==0
    # No second consent can create another campaign or spend reservation.
    again=consent.prepare(db.cursor(),cfg,{'message_id':message,'channel_id':CA,'lead_id':41})
    assert again['state']=='approved' and again['consent_id']==result['consent_id']


def test_consent_reject_stale_foreign_and_media_change_fail_closed(db,monkeypatch):
    seed_job(db);_pricing(monkeypatch);cfg=_cfg();_bridge(monkeypatch,cfg);message=_source(db);consent.ensure_schema(db)
    with pytest.raises(TenantLinearError,match='OAuth channel'):
        consent.prepare(db.cursor(),cfg,{'message_id':message,'channel_id':CB,'lead_id':41})
    prepared=consent.prepare(db.cursor(),cfg,{'message_id':message,'channel_id':CA,'lead_id':41});meta=prepared['_component_meta']['owner_single_extraction'];db.commit()
    rejected=consent.complete(db.cursor(),cfg,consent_id=meta['consent_ids']['reject'],capability=meta['capabilities']['reject'],action='reject');db.commit()
    assert rejected['state']=='rejected'
    with pytest.raises(TenantLinearError,match='unavailable'):
        consent.complete(db.cursor(),cfg,consent_id=meta['consent_ids']['approve'],capability=meta['capabilities']['approve'],action='approve')


def test_expired_pending_consent_requires_a_fresh_exact_review(db,monkeypatch):
    seed_job(db);_pricing(monkeypatch);cfg=_cfg();_bridge(monkeypatch,cfg);message=_source(db);consent.ensure_schema(db)
    first=consent.prepare(db.cursor(),cfg,{'message_id':message,'channel_id':CA,'lead_id':41});meta=first['_component_meta']['owner_single_extraction'];db.commit()
    db.execute("UPDATE saas_ai_single_message_extraction_consents SET expires_at=now()-interval '1 second' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,meta['consent_ids']['approve']));db.commit()
    with pytest.raises(TenantLinearError,match='unavailable'):
        consent.complete(db.cursor(),cfg,consent_id=meta['consent_ids']['approve'],capability=meta['capabilities']['approve'],action='approve')
    fresh=consent.prepare(db.cursor(),cfg,{'message_id':message,'channel_id':CA,'lead_id':41})
    assert fresh['state']=='pending' and fresh['consent_id']!=first['consent_id']
    # A pre-existing pending capability also cannot survive a media identity change.
    message2=_source(db);prepared=consent.prepare(db.cursor(),cfg,{'message_id':message2,'channel_id':CA,'lead_id':41});meta=prepared['_component_meta']['owner_single_extraction'];db.commit()
    db.execute("UPDATE saas_crm_messages SET raw=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(json.dumps({'mediaId':'replaced','fileLength':101}),A,message2));db.commit()
    with pytest.raises(TenantLinearError,match='Source changed'):
        consent.complete(db.cursor(),cfg,consent_id=meta['consent_ids']['approve'],capability=meta['capabilities']['approve'],action='approve')
    # A revoked stored consent has no completion fallback or replay path.
    message3=_source(db);prepared=consent.prepare(db.cursor(),cfg,{'message_id':message3,'channel_id':CA,'lead_id':41});meta=prepared['_component_meta']['owner_single_extraction'];db.commit()
    db.execute("UPDATE saas_ai_single_message_extraction_consents SET state='revoked' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,meta['consent_ids']['approve']));db.commit()
    with pytest.raises(TenantLinearError,match='unavailable'):
        consent.complete(db.cursor(),cfg,consent_id=meta['consent_ids']['approve'],capability=meta['capabilities']['approve'],action='approve')


def test_preflight_needs_exact_running_lease_and_combined_budget(db,monkeypatch):
    seed_job(db);_pricing(monkeypatch);cfg=_cfg();_bridge(monkeypatch,cfg);message=_source(db);consent.ensure_schema(db)
    prepared=consent.prepare(db.cursor(),cfg,{'message_id':message,'channel_id':CA,'lead_id':41});meta=prepared['_component_meta']['owner_single_extraction'];db.commit()
    result=consent.complete(db.cursor(),cfg,consent_id=meta['consent_ids']['approve'],capability=meta['capabilities']['approve'],action='approve');db.commit()
    # Pending is deliberately not reported as ready: only worker ownership of
    # the exact fragment lease can proceed toward extraction.
    value=preflight.preflight(db.cursor(),cfg,{'message_id':message,'channel_id':CA,'lead_id':41,'consent_id':result['consent_id']})
    assert value['ready'] is False and value['reason_code']=='lease_or_exact_fragment_required'
    db.execute("UPDATE saas_knowledge_campaign_fragments SET status='running',lease_token=%s::uuid,lease_until=now()+interval '5 minutes' WHERE tenant_id=%s::uuid",(str(uuid.uuid4()),A));db.commit()
    value=preflight.preflight(db.cursor(),cfg,{'message_id':message,'channel_id':CA,'lead_id':41,'consent_id':result['consent_id']})
    assert value['ready'] is True
    # An existing reservation consuming the cap makes the same source not ready
    # without changing the fragment or creating a second reservation.
    db.execute("INSERT INTO saas_ai_spend_reservations(tenant_id,id,call_key,model,kind,reserved_microusd,state) VALUES(%s::uuid,%s::uuid,'other','fixture-model','test',5000000,'reserved')",(A,str(uuid.uuid4())));db.commit()
    value=preflight.preflight(db.cursor(),cfg,{'message_id':message,'channel_id':CA,'lead_id':41,'consent_id':result['consent_id']})
    assert value['ready'] is False and value['reason_code']=='tariff_or_budget_unavailable'


def test_single_message_component_uses_only_app_completion_and_exact_state():
    script=component.resource_html().split('<script>',1)[1].split('</script>',1)[0]
    harness=r'''
const vm=require('vm'),fs=require('fs'),source=fs.readFileSync(0,'utf8');
const cap='a'.repeat(32), approve='11111111-1111-1111-1111-111111111111', reject='22222222-2222-2222-2222-222222222222';
const elements={status:{textContent:''},diff:{textContent:''},approve:{disabled:true,hidden:false},reject:{disabled:true,hidden:false},publish:{disabled:true,hidden:true}};const calls=[];
const api={toolResponseMetadata:{owner_single_extraction:{consent_ids:{approve,reject},capabilities:{approve:cap,reject:'b'.repeat(32)},exact_preview:{message_id:'id',attachment_kind:'video'}}},callTool:async(n,a)=>{calls.push([n,a]);return {structuredContent:{state:a.action==='approve'?'approved':'rejected'}};}};
const context={window:{openai:api,parent:{},addEventListener:()=>{}},document:{querySelector:q=>elements[q.slice(1)]},JSON,Error};vm.runInNewContext(source,context);
if(elements.approve.disabled||elements.reject.disabled)process.exit(2);Promise.resolve(elements.approve.onclick()).then(()=>{if(calls.length!==1||calls[0][0]!=='administrator_single_message_extraction_component_complete'||calls[0][1].consent_id!==approve||calls[0][1].capability!==cap||elements.status.textContent!=='Approved and queued.')process.exit(3);}).catch(()=>process.exit(4));
'''
    result=subprocess.run(['node','-e',harness],input=script,text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr


def test_single_message_worker_claims_only_selected_message_and_finishes_actual_extraction(db,monkeypatch):
    seed_job(db);_pricing(monkeypatch);cfg=_cfg();_bridge(monkeypatch,cfg);message=_source(db);consent.ensure_schema(db)
    prepared=consent.prepare(db.cursor(),cfg,{'message_id':message,'channel_id':CA,'lead_id':41});meta=prepared['_component_meta']['owner_single_extraction'];db.commit()
    consent.complete(db.cursor(),cfg,consent_id=meta['consent_ids']['approve'],capability=meta['capabilities']['approve'],action='approve');db.commit()
    monkeypatch.setattr(worker,'_connect',lambda:db);monkeypatch.setattr(campaigns,'_connect',lambda:db)
    monkeypatch.setattr(extraction_service,'_connect',lambda:db);monkeypatch.setattr(extraction_service.store,'_connect',lambda:db);monkeypatch.setattr(spend_budget,'_connect',lambda:db)
    monkeypatch.setattr(worker,'reconcile',lambda profile,campaign_id:None)
    # Actual prepare/check_active/finish run; only outbound model/media transport
    # is mocked.  No attachment bytes or provider request leaves the fixture.
    seen=[]
    def fake_generate(key,model,mode,lines,draft,**kwargs):
        seen.extend(lines);return '{"cards":[]}',{'input_tokens':1,'output_tokens':1,'total_tokens':2}
    monkeypatch.setattr(extraction_service,'generate',fake_generate)
    monkeypatch.setattr(knowledge_media,'collect',lambda profile,item,**kwargs:([],[]))
    assert worker.tick() is True
    assert len(seen)==1 and seen[0]['ref']=='m1'
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_campaign_sources WHERE tenant_id=%s::uuid',(A,))[0]['n']==0
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid',(A,))[0]['n']==1
    fragment=db.execute('SELECT status FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid',(A,))[0]
    assert fragment['status']=='completed'
    assert db.execute('SELECT status FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid',(A,))[0]['status']=='stopped'


def test_worker_rechecks_changed_approved_media_before_provider(db,monkeypatch):
    seed_job(db);_pricing(monkeypatch);cfg=_cfg();_bridge(monkeypatch,cfg);message=_source(db);consent.ensure_schema(db)
    prepared=consent.prepare(db.cursor(),cfg,{'message_id':message,'channel_id':CA,'lead_id':41});meta=prepared['_component_meta']['owner_single_extraction'];db.commit()
    consent.complete(db.cursor(),cfg,consent_id=meta['consent_ids']['approve'],capability=meta['capabilities']['approve'],action='approve');db.commit()
    # The consent is valid but its underlying provider-media identity changed
    # while queued.  Dispatch must block before prepare/reservation/provider.
    db.execute("UPDATE saas_crm_messages SET raw=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(json.dumps({'mediaId':'changed','fileLength':101}),A,message));db.commit()
    monkeypatch.setattr(worker,'_connect',lambda:db);monkeypatch.setattr(campaigns,'_connect',lambda:db)
    monkeypatch.setattr(worker,'reconcile',lambda profile,campaign_id:None)
    called=[];monkeypatch.setattr(worker,'analyze',lambda *args,**kwargs: called.append(True))
    assert worker.tick() is True
    assert not called
    assert db.execute('SELECT status FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid',(A,))[0]['status']=='blocked'
    assert db.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid',(A,))[0]['n']==0


def test_disabled_company_asr_is_explicitly_not_ready(db,monkeypatch):
    seed_job(db);_pricing(monkeypatch);message=_source(db)
    monkeypatch.setenv('AI_AGENTS_PLATFORM_ENABLED','false');monkeypatch.delenv('OPENAI_API_KEY')
    db.execute("UPDATE saas_ai_agents SET model='company-model' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT))
    db.execute("""INSERT INTO saas_tenant_integrations(tenant_id,provider,secrets,metadata)
      VALUES(%s::uuid,'chat_ai',%s,%s::jsonb)""",(A,b'test-only',json.dumps({'enabled':True,'consent':True,'models':['company-model'],'audio_enabled':False})))
    db.commit()
    value=preflight.preflight(db.cursor(),_cfg(),{'message_id':message,'channel_id':CA,'lead_id':41})
    assert value['ready'] is False and value['reason_code']=='asr_model_unavailable' and value['tariffs']['asr'] is False
