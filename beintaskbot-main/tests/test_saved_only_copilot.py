"""One scoped saved-only draft; all provider I/O is intercepted locally."""
import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from cryptography.fernet import Fernet
import tenant_saved_only_copilot as gate
import tenant_operator_copilot as copilot
import tenant_chat_ai_provider as provider
import tenant_ai_spend_budget as budget
from tenant_linear_policy import TenantLinearError
from test_assigned_operator_copilot import ready
from test_tenant_support_inbox import database,db,A,CA,PROFILE
import tenant_episode_media as _episode
ORIGINAL_PREPARE=_episode.prepare
ORIGINAL_COPILOT_PREPARE=copilot._prepare_copilot_media


@pytest.fixture
def saved(ready,monkeypatch):
    db,other,generated,data=ready
    fernet=Fernet(Fernet.generate_key())
    secret=fernet.encrypt(json.dumps({'tenant_id':A,'provider':'chat_ai','api_key':'synthetic-no-live-key'}).encode())
    db.execute("UPDATE saas_ai_agents SET model='gpt-4.1' WHERE tenant_id=%s::uuid",(A,))
    db.execute("UPDATE saas_tenant_integrations SET secrets=%s WHERE tenant_id=%s::uuid AND provider='chat_ai'",(secret,A));db.commit()
    for module in (gate,budget):monkeypatch.setattr(module,'_connect',lambda:db)
    monkeypatch.setattr(gate,'_fernet',lambda:fernet);monkeypatch.setattr(copilot,'_fernet',lambda:fernet)
    monkeypatch.setattr('tenant_media_evidence._connect',lambda:db)
    data={key:value for key,value in data.items() if key!='request_id'};data['saved_only']=True
    monkeypatch.setattr(gate,'APPROVED_SCOPE_DIGEST',gate.scope_digest(A,data,11))
    monkeypatch.delenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',raising=False)
    monkeypatch.delenv('AI_TENANT_SPEND_LIMITS_MICROUSD',raising=False)
    forbidden=Mock(side_effect=AssertionError('Forbidden saved-only media path'))
    monkeypatch.setattr(copilot,'_prepare_copilot_media',forbidden)
    monkeypatch.setattr('tenant_episode_media.prepare',forbidden)
    monkeypatch.setattr('tenant_connect_media.download_media',forbidden)
    monkeypatch.setattr(provider,'transcribe',forbidden)
    monkeypatch.setattr(provider,'transcribe_video',forbidden)
    response=SimpleNamespace(status_code=200,json=lambda:{'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'Synthetic saved draft.'}]}],'usage':{'input_tokens':10,'output_tokens':2,'total_tokens':12}})
    http=Mock(return_value=response);monkeypatch.setattr(provider.requests,'post',http)
    return db,data,http,forbidden


def test_readonly_preflight_one_dispatch_durable_reuse_and_no_media(saved,monkeypatch):
    db,data,http,forbidden=saved;sql=[];query=db.query
    def recorded(statement,params=()):sql.append(statement);return query(statement,params)
    monkeypatch.setattr(db,'query',recorded)
    preview=gate.preflight(PROFILE,data)
    assert preview['state']=='ready' and 0<preview['reserve_microusd']<=940000
    assert preview['max_output_tokens']==900
    assert not any(statement.lstrip().upper().startswith(('CREATE ','ALTER ','INSERT ','UPDATE ','DELETE ')) for statement in sql)
    http.assert_not_called();forbidden.assert_not_called()
    payload={**data,'request_id':preview['request_id'],'cost_token':preview['cost_token']}
    result=gate.execute(PROFILE,payload)
    assert result['text']=='Synthetic saved draft.'
    assert gate.execute(PROFILE,payload)==result
    assert gate.preflight(PROFILE,data)['result']['text']==result['text']
    http.assert_called_once();forbidden.assert_not_called()
    wire=http.call_args.kwargs['json'];assert wire['model']=='gpt-4.1' and wire['max_output_tokens']==900 and wire['service_tier']=='default'
    assert isinstance(wire['input'],str) and 'input_image' not in wire['input']
    financial=db.execute('SELECT state,reserved_microusd FROM saas_ai_spend_reservations')[0]
    assert financial['state']=='settled' and financial['reserved_microusd']==preview['reserve_microusd']
    assert db.execute('SELECT count(*) AS n FROM saas_chat_send_receipts')[0]['n']==0


def test_provider_unknown_never_replays_with_same_or_new_uuid(saved):
    import requests
    db,data,http,forbidden=saved;preview=gate.preflight(PROFILE,data)
    payload={**data,'request_id':preview['request_id'],'cost_token':preview['cost_token']}
    http.side_effect=requests.Timeout('Synthetic timeout')
    with pytest.raises(TenantLinearError):gate.execute(PROFILE,payload)
    with pytest.raises(TenantLinearError):gate.execute(PROFILE,payload)
    with pytest.raises(TenantLinearError):gate.execute(PROFILE,{**payload,'request_id':'00000000-0000-4000-8000-000000000001'})
    assert http.call_count==1
    assert db.execute('SELECT state FROM saas_ai_spend_reservations')[0]['state']=='unknown'
    assert gate.preflight(PROFILE,data)['state']=='unknown'
    forbidden.assert_not_called()


def test_disabled_scope_and_invalid_model_or_tariff_fail_closed(saved,monkeypatch):
    db,data,http,forbidden=saved
    monkeypatch.setattr(gate,'APPROVED_SCOPE_DIGEST',None)
    with pytest.raises(TenantLinearError):gate.preflight(PROFILE,data)
    monkeypatch.setattr(gate,'APPROVED_SCOPE_DIGEST',gate.scope_digest(A,data,11))
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({'gpt-4.1':{'input':1,'output':1}}))
    with pytest.raises(TenantLinearError):gate.preflight(PROFILE,data)
    http.assert_not_called();forbidden.assert_not_called()


def test_exact_final_payload_unicode_escaping_instructions_and_cap_boundary(monkeypatch):
    monkeypatch.delenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',raising=False)
    lines=[{'speaker':'CLIENT','text':'Unicode ş ə \"quote\" \\ path\nsecond line'}]
    extra={'verified_knowledge':[{'title':'Synthetic KB','content':'Knowledge ə \\'}],'prior_internal_copilot_turns':[{'text':'Old \"draft\"'}]}
    instructions='System authority\nCompany ə\nAssigned agent \\ rule'
    wire=provider.request_payload('gpt-4.1','reply',lines,'Draft only',instructions=instructions,extra_context=extra,max_output_tokens=900,service_tier='default')
    quote=gate.quote_payload(wire)
    assert quote['input_upper']==len(json.dumps(wire,ensure_ascii=True,allow_nan=False).encode())+8192
    assert quote['total_upper']==quote['input_upper']+900
    response=SimpleNamespace(status_code=200,json=lambda:{'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'Synthetic'}]}]})
    http=Mock(return_value=response);monkeypatch.setattr(provider.requests,'post',http)
    provider.generate('synthetic-no-live-key','gpt-4.1','reply',lines,'Draft only',instructions=instructions,extra_context=extra,max_output_tokens=900,service_tier='default')
    assert http.call_args.kwargs['json']==wire
    empty={'model':'gpt-4.1','store':False,'max_output_tokens':900,'instructions':'','input':'','service_tier':'default'}
    overhead=len(json.dumps(empty,ensure_ascii=True,allow_nan=False).encode())
    exact={**empty,'input':'x'*(940000//8-8192-900-overhead)}
    assert gate.quote_payload(exact)['reserve']==940000
    with pytest.raises(TenantLinearError):gate.quote_payload({**exact,'input':exact['input']+'x'})
    http.assert_called_once()


def seed_saved_media(db):
    import uuid
    from tenant_episode_media import reference
    from tenant_media_evidence import create_schema,persist
    create_schema(db);db.commit()
    rows=[]
    for index,(kind,body,result) in enumerate([
        ('image','Historical caption',{'observation':'Historical partial image'}),
        ('text','Staff response',{}),('voice','',{'transcript':'Internet is down. Full saved speech.'}),
        ('image','Photo caption remains',{'observations':[{'text':'Visible E17'}]}),
        ('document','Document caption remains',{'observation':'Saved document Internet details'}),
        ('text','Internet follow-up',{})]):
        external='connect:'+str(uuid.uuid4())
        db.execute('''INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,raw,happened_at)
          VALUES(%s::uuid,%s::uuid,41,%s,%s,%s,%s,%s,'{}',now()+(%s * interval '1 second'))''',
          (A,uuid.uuid4(),external,'connect:'+CA,'outgoing' if index==1 else 'incoming',body,kind,index+1))
        row=db.execute('SELECT external_id,body,raw,message_type,channel,COALESCE(happened_at,created_at) AS happened_at FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s',(A,external))[0]
        if kind!='text':
            ref=reference(row,CA,41)
            with db.cursor() as cur:persist(cur,A,[ref],[{'id':external,'status':'partial' if index==0 else 'processed','code':'saved_fixture',**result}],processor='conversation_episode')
        rows.append(row)
    db.commit();return rows


def test_exact_context_parity_with_normal_saved_reuse_including_captions_history_and_kb(saved,monkeypatch):
    import copy
    import tenant_episode_media as episode
    db,data,http,forbidden=saved;seed_saved_media(db)
    state=gate._read(PROFILE,data)
    # The fixture captured the original normal path before forbidding it.
    monkeypatch.setattr(episode,'prepare',ORIGINAL_PREPARE)
    monkeypatch.setattr(copilot,'_prepare_copilot_media',ORIGINAL_COPILOT_PREPARE)
    monkeypatch.setattr(episode.ManualExecution,'validate',lambda *_:None)
    with db.cursor() as cur:
        agent=copilot._assigned_context(cur,A,state['command'])
        rows,turns=copilot._read_history(cur,A,11,state['command'])
    db.commit()
    normal=copilot._draft_context(A,11,copy.deepcopy(state['command']),state['config'],state['connection'],{'api_key':'synthetic-no-live-key'},state['model'],agent,rows,turns)
    wire=provider.request_payload(state['model'],'reply',normal['lines'],normal['request'],instructions=normal['instructions'],extra_context=normal['extra_context'],max_output_tokens=900)
    assert {**wire,'service_tier':'default'}==state['wire']
    assert normal['execution'].source_refs==state['command']['_source_versions']
    assert 'Photo caption remains' in wire['input'] and 'Document caption remains' in wire['input']
    assert 'Historical partial image' in wire['input']
    http.assert_not_called()


@pytest.mark.parametrize('mutation',['source','agent','kb_version'])
def test_final_dispatch_guard_stops_context_races_without_http_or_new_uuid(saved,monkeypatch,mutation):
    import uuid
    from test_tenant_support_inbox import AGENT
    db,data,http,forbidden=saved
    source=str(uuid.uuid4())
    db.execute("INSERT INTO saas_ai_knowledge_sources(tenant_id,id,agent_id,title,kind,content,content_hash,created_by) VALUES(%s::uuid,%s::uuid,%s::uuid,'Internet','text','Internet saved facts','original-hash',11)",(A,source,AGENT))
    db.execute("INSERT INTO saas_ai_knowledge_chunks(tenant_id,source_id,chunk_no,content) VALUES(%s::uuid,%s::uuid,0,'Internet saved facts')",(A,source));db.commit()
    preview=gate.preflight(PROFILE,data);payload={**data,'request_id':preview['request_id'],'cost_token':preview['cost_token']}
    actual=provider.generate;changed=[]
    def mutate_then_generate(*args,**kwargs):
        if mutation=='source':db.execute("UPDATE saas_crm_messages SET body='Changed source' WHERE tenant_id=%s::uuid",(A,))
        elif mutation=='agent':db.execute("UPDATE saas_ai_agents SET instructions='Changed instructions',version=version+1 WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT))
        else:db.execute("UPDATE saas_ai_knowledge_sources SET content_hash='new-version',provenance='{\"revision\":2}'::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,source))
        db.commit();changed.append(True);return actual(*args,**kwargs)
    monkeypatch.setattr(provider,'generate',mutate_then_generate)
    with pytest.raises(TenantLinearError):gate.execute(PROFILE,payload)
    http.assert_not_called();forbidden.assert_not_called();assert changed==[True]
    assert db.execute('SELECT state FROM saas_ai_spend_reservations')[0]['state']=='released'
    assert db.execute('SELECT status FROM saas_chat_ai_runs')[0]['status']=='unknown'


def test_known_text_survives_settlement_failure_and_is_reused_without_second_http(saved,monkeypatch):
    db,data,http,forbidden=saved;preview=gate.preflight(PROFILE,data)
    monkeypatch.setattr(budget,'settle',Mock(side_effect=RuntimeError('Synthetic settlement failure')))
    payload={**data,'request_id':preview['request_id'],'cost_token':preview['cost_token']}
    first=gate.execute(PROFILE,payload)
    assert gate.execute(PROFILE,payload)==first
    assert gate.preflight(PROFILE,data)['result']['text']=='Synthetic saved draft.'
    assert db.execute('SELECT state FROM saas_ai_spend_reservations')[0]['state']=='reserved'
    http.assert_called_once()


@pytest.mark.parametrize('extra',['tools','service_tier','reasoning','previous_response_id','background','images'])
def test_quote_rejects_unbounded_future_provider_features(extra,monkeypatch):
    http=Mock();monkeypatch.setattr(provider.requests,'post',http)
    wire=provider.request_payload('gpt-4.1','reply',[],'',max_output_tokens=900,service_tier='default')
    with pytest.raises(TenantLinearError):gate.quote_payload({**wire,extra:True})
    http.assert_not_called()


@pytest.mark.parametrize('state',['reserved','unknown','settled'])
def test_orphan_financial_record_blocks_preview_and_never_replays(saved,state):
    import uuid
    db,data,http,forbidden=saved;old=gate.preflight(PROFILE,data)
    db.execute("INSERT INTO saas_ai_spend_reservations(tenant_id,id,call_key,model,kind,reserved_microusd,state) VALUES(%s::uuid,%s::uuid,%s,'gpt-4.1','saved_only_copilot',100,%s)",(A,uuid.uuid4(),'saved-only:'+old['request_id'],state));db.commit()
    current=gate.preflight(PROFILE,data)
    assert current['state']=='blocked' and current['cost_token'] is None
    with pytest.raises(TenantLinearError):gate.execute(PROFILE,{**data,'request_id':old['request_id'],'cost_token':old['cost_token']})
    http.assert_not_called();forbidden.assert_not_called()
    assert db.execute('SELECT state FROM saas_ai_spend_reservations')[0]['state']==state
    assert db.execute('SELECT count(*) AS n FROM saas_chat_ai_runs')[0]['n']==0


def test_positive_tenant_cap_and_daily_limit_preserve_existing_limits(saved,monkeypatch):
    db,data,http,forbidden=saved;quote=gate.preflight(PROFILE,data);payload={**data,'request_id':quote['request_id'],'cost_token':quote['cost_token']}
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:1}))
    with pytest.raises(TenantLinearError):gate.execute(PROFILE,payload)
    monkeypatch.delenv('AI_TENANT_SPEND_LIMITS_MICROUSD')
    db.execute("UPDATE saas_tenant_integrations SET metadata=jsonb_set(metadata,'{daily_limit}','1'::jsonb) WHERE tenant_id=%s::uuid",(A,));db.commit()
    quote=gate.preflight(PROFILE,data)
    db.execute("INSERT INTO saas_chat_ai_runs(tenant_id,request_id,requested_by,lead_id,input_hash,config_version,status,reserved_calls) VALUES(%s::uuid,'00000000-0000-4000-8000-000000000002',11,41,'other','v','unknown',1)",(A,));db.commit()
    with pytest.raises(TenantLinearError):gate.execute(PROFILE,{**data,'request_id':quote['request_id'],'cost_token':quote['cost_token']})
    http.assert_not_called();forbidden.assert_not_called()


def test_owner_ui_audit_is_metadata_only_and_matches_durable_result(saved):
    db,data,http,forbidden=saved;seed_saved_media(db)
    preview=gate.preflight(PROFILE,data)
    assert preview['audit']['sources'] and preview['audit']['fingerprint']
    assert all(set(item)=={'source_id','version','kind','scope'} for item in preview['audit']['sources'])
    serialized=json.dumps(preview)
    assert 'Full saved speech' not in serialized and 'Photo caption remains' not in serialized and 'synthetic-no-live-key' not in serialized
    result=gate.execute(PROFILE,{**data,'request_id':preview['request_id'],'cost_token':preview['cost_token']})
    assert result['audit']==preview['audit']
    assert gate.preflight(PROFILE,data)['result']['audit']==preview['audit']


def test_one_case_uuid_never_changes_with_owner_route_config_or_model(saved):
    db,data,http,forbidden=saved
    original=gate.request_id(A,data)
    assert gate.request_id(A,{**data,'conversation_id':'different-route','lead_id':999,'model':'different'})==original
    assert gate.scope_digest(A,data,11)!=gate.scope_digest(A,data,22)
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,22,'owner','Synthetic second owner')",(A,));db.commit()
    with pytest.raises(TenantLinearError):gate.preflight({'tenant_id':A,'telegram_id':22},data)
    http.assert_not_called()


@pytest.mark.parametrize('tier',['auto','priority','flex','fast',None])
def test_quote_never_uses_project_default_or_premium_tier(tier):
    wire=provider.request_payload('gpt-4.1','reply',[],'',max_output_tokens=900,service_tier=tier)
    with pytest.raises(TenantLinearError):gate.quote_payload(wire)


def test_registered_router_reaches_readonly_saved_preflight_and_preserves_normal_post(saved,monkeypatch):
    import asyncio
    import logging
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    import tenant_web_routes as routes
    import tenant_operator_api as api
    db,data,http,forbidden=saved
    sql=[];query=db.query
    def recorded(statement,params=()):sql.append(statement);return query(statement,params)
    monkeypatch.setattr(db,'query',recorded)
    ordinary=Mock(return_value={'text':'Existing ordinary draft'})
    monkeypatch.setattr(api,'run_copilot',ordinary)
    async def scenario():
        context=routes.TenantWebContext(member=lambda _:PROFILE,origin='https://crm.test',logger=logging.getLogger(__name__),crm_request=Mock(),wait_slot=Mock(),bot=lambda:None)
        app=web.Application();routes.register_feature_routes(app,context)
        path='/api/platform/ai/dialogs/copilot'
        params={**data,'saved_only':'1','expected_tenant_id':A,'expected_user_id':'11'}
        async with TestClient(TestServer(app)) as client:
            response=await client.get(path,params={key:str(value) for key,value in params.items()})
            assert response.status==200,await response.text()
            body=await response.json()
            assert body['success'] is True and body['result']['state']=='ready'
            assert not any(statement.lstrip().upper().startswith(('CREATE ','ALTER ','INSERT ','UPDATE ','DELETE ')) for statement in sql)
            http.assert_not_called();forbidden.assert_not_called();ordinary.assert_not_called()
            response=await client.get(path)
            assert response.status==405
            response=await client.get(path,params={**{key:str(value) for key,value in params.items()},'expected_user_id':'22'})
            assert response.status==409
            http.assert_not_called();forbidden.assert_not_called()
            response=await client.post(path,json={'expected_tenant_id':A,'expected_user_id':11},headers={'Origin':'https://crm.test'})
            assert response.status==200
            ordinary.assert_called_once()
            monkeypatch.setattr(api,'run_copilot',copilot.run)
            execute=Mock(return_value={'state':'completed','text':'Synthetic gated result'})
            monkeypatch.setattr(gate,'execute',execute)
            response=await client.post(path,json={**data,'expected_tenant_id':A,'expected_user_id':11},headers={'Origin':'https://crm.test'})
            assert response.status==200 and (await response.json())['result']['state']=='completed'
            execute.assert_called_once()
            http.assert_not_called();forbidden.assert_not_called()
    asyncio.run(scenario())
