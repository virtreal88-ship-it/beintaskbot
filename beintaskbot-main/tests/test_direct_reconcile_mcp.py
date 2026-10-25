"""Issue 260: physical tools over authenticated MCP, fictional fixtures only."""
import asyncio
import json
import uuid
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A, B, CA, CB, PROFILE
from test_connect_dependent_reconcile import seed, snapshot, JID, LID
from test_ai_learning_mcp import _setup
import tenant_ai_learning_mcp as mcp
import tenant_ai_direct_reconcile_mcp as facade
from tenant_linear_policy import TenantLinearError


def cfg():
    return {**mcp._require_cfg(),'actor_id':11,'granted_scopes':[mcp.TOOL_SCOPE,facade.WRITE_SCOPE]}


def call(db,name,args,config=None):
    with db.cursor() as cur:return mcp._tools(cur,config or cfg(),facade.PREFIX+name,args)


def pair(db):
    return next(r for r in call(db,'preview',{})['items'] if r['kind']=='pair')


def payload(item):
    return {k:item[k] for k in ('pair_id','precondition')} | {'request_id':str(uuid.uuid4()),'confirm':True}


def test_native_mcp_e2e_merge_verify_old_links_preflight_rollback(db,monkeypatch):
    before=seed(db,monkeypatch)
    token=_setup(monkeypatch,db,mcp.TOOL_SCOPE+' '+facade.WRITE_SCOPE)
    async def run():
        app=web.Application();app.router.add_post('/mcp',mcp.mcp)
        async with TestClient(TestServer(app)) as client:
            async def request(name,args={}):
                response=await client.post('/mcp',headers={'Authorization':'Bearer '+token},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':facade.PREFIX+name,'arguments':args}})
                body=await response.json()
                assert response.status==200,body
                return json.loads(body['result']['content'][0]['text'])
            listing=await client.post('/mcp',json={'jsonrpc':'2.0','id':2,'method':'tools/list'})
            tools=(await listing.json())['result']['tools']
            assert {facade.PREFIX+x for x in ('preview','apply','rollback_preflight','rollback')} <= {t['name'] for t in tools}
            item=next(x for x in (await request('preview'))['items'] if x['kind']=='pair')
            assert item['state']=='verified_eligible'
            args=payload(item)
            assert (await request('apply',args))['state']=='merged'
            assert (await request('apply',args))['idempotent_replay']
            merged=next(x for x in (await request('preview',{'pair_id':item['pair_id']}))['items'] if x['kind']=='pair')
            assert merged['state']=='already_merged' and not merged['eligible'] and not merged['conflicts']
            assert merged['verification']=={'messages':85,'media':14,'routes':1,'states':['human'],'jobs':1,'drafts':3,'read_markers':1}
            assert (await request('rollback_preflight',{'pair_id':item['pair_id']}))['eligible']
            from tenant_operator_access import access,assert_route
            with db.cursor() as cur:
                tenant,_,policy=access(cur,PROFILE,'dialogs')
                assert assert_route(cur,tenant,policy,'connect',LID,CA,8102,allow_merged=True)['lead_id']==8101
            db.commit()
            assert (await request('rollback',payload(merged)))['state']=='rolled_back'
    asyncio.run(run())
    assert snapshot(db)==before


@pytest.mark.parametrize('case',['read_token','logical_token','worker','other_tenant','other_channel','confirm','stale'])
def test_physical_write_denies_scope_rbac_context_and_staleness(db,monkeypatch,case):
    before=seed(db,monkeypatch);_setup(monkeypatch,db)
    item=pair(db);args=payload(item);config=cfg()
    if case=='read_token':config['granted_scopes']=[mcp.TOOL_SCOPE]
    if case=='logical_token':config['granted_scopes']=[mcp.TOOL_SCOPE,mcp.DIALOGUE_MERGE_SCOPE]
    if case=='worker':
        db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,33,'worker','Synthetic')",(A,));db.commit();config['actor_id']=33
    if case=='other_tenant':config.update(AI_LEARNING_MCP_TENANT_ID=B,actor_id=22,AI_LEARNING_MCP_CHANNEL_ID=CB)
    if case=='other_channel':
        db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Other test')",(A,CB));db.commit();config['AI_LEARNING_MCP_CHANNEL_ID']=CB
    if case=='confirm':args['confirm']=False
    if case=='stale':args['precondition']='0'*64
    with pytest.raises(TenantLinearError):call(db,'apply',args,config)
    db.rollback();assert snapshot(db)==before


def test_unproven_476_3357_shape_and_bounded_all_channel_pagination(db,monkeypatch):
    seed(db,monkeypatch);_setup(monkeypatch,db)
    for lead,identity,count in ((476,'994700000476@s.whatsapp.net',21),(3357,'80000000003357@lid',61)):
        db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,conversation_kind) VALUES(%s::uuid,%s::uuid,%s,%s,'Fictional duplicate hint','contact')",(A,CA,identity,lead))
        for i in range(count):
            db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,channel,direction,external_id) VALUES(%s::uuid,%s::uuid,%s,%s,'incoming',%s)",(str(uuid.uuid4()),A,lead,'connect:'+CA,str(uuid.uuid4())))
    db.commit()
    collected=[];args={'channel_id':'all','limit':1}
    while True:
        page=call(db,'preview',args);assert len(page['items'])<=1;collected.extend(page['items'])
        if not page['next_cursor']:break
        args['cursor']=page['next_cursor']
    blocked=next(r for r in collected if r.get('state')=='review_blocked')
    assert blocked['provider_receipts']==0 and 'provider_identity_unproven' in blocked['conflicts']
    assert {blocked['canonical_messages'],blocked['split_messages']}=={21,61}
    assert 'Fictional' not in json.dumps(collected) and JID not in json.dumps(collected)
    assert not any('@lid' in json.dumps(r) or 'raw' in r for r in collected)
    with pytest.raises(TenantLinearError):call(db,'apply',payload(blocked))
    db.rollback()
    with pytest.raises(TenantLinearError):call(db,'preview',{'limit':26})
    db.rollback()


def test_mcp_encoder_failure_rolls_back_physical_transfer(db,monkeypatch):
    before=seed(db,monkeypatch);token=_setup(monkeypatch,db,mcp.TOOL_SCOPE+' '+facade.WRITE_SCOPE)
    item=pair(db);db.commit()
    monkeypatch.setattr(mcp,'_tool_response',lambda *a:(_ for _ in ()).throw(RuntimeError('synthetic serialization failure')))
    async def run():
        app=web.Application();app.router.add_post('/mcp',mcp.mcp)
        async with TestClient(TestServer(app)) as client:
            response=await client.post('/mcp',headers={'Authorization':'Bearer '+token},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':facade.PREFIX+'apply','arguments':payload(item)}})
            assert response.status==503
    asyncio.run(run());assert snapshot(db)==before


def test_physical_scope_is_explicit_in_oauth_consent_and_schema():
    granted=mcp._requested_scopes(mcp.TOOL_SCOPE+' '+facade.WRITE_SCOPE)
    assert facade.WRITE_SCOPE in granted
    assert facade.WRITE_SCOPE not in mcp._requested_scopes(mcp.TOOL_SCOPE+' '+mcp.DIALOGUE_MERGE_SCOPE)
    text,_=mcp._consent_text(granted)
    assert 'PHYSICAL' in text and 'CRM messages' in text
    tools=facade.definitions()
    assert len(tools)==4
    for tool in tools:
        assert tool['inputSchema']['additionalProperties'] is False
        if tool['name'].endswith(('apply','rollback')):
            assert tool['securitySchemes'][0]['scopes']==[facade.WRITE_SCOPE]
            assert tool['inputSchema']['properties']['confirm']['const'] is True


def test_page_cursor_tampering_and_cross_channel_are_rejected(db,monkeypatch):
    seed(db,monkeypatch);_setup(monkeypatch,db)
    page=call(db,'preview',{'limit':1})
    assert page['next_cursor']
    with pytest.raises(TenantLinearError):call(db,'preview',{'limit':1,'cursor':page['next_cursor']+'0'})
    db.rollback()
    with pytest.raises(TenantLinearError):call(db,'preview',{'channel_id':CB,'cursor':page['next_cursor']})
    db.rollback()


def test_existing_logical_preview_marks_physical_merge_as_merged(db,monkeypatch):
    seed(db,monkeypatch);_setup(monkeypatch,db)
    item=pair(db);call(db,'apply',payload(item));db.commit()
    with db.cursor() as cur:
        result=mcp._tools(cur,cfg(),'whatsapp_dialogue_duplicate_candidates',{})
    assert result['items'][0]['state']=='merged' and not result['items'][0]['eligible']
    assert result['items'][0]['conflicts']==[]


@pytest.mark.parametrize('physical',[False,True])
def test_refresh_preserves_physical_grant_and_never_upgrades_read_token(db,monkeypatch,physical):
    scopes=mcp.TOOL_SCOPE+' '+mcp.OFFLINE_SCOPE+(' '+facade.WRITE_SCOPE if physical else '')
    token=_setup(monkeypatch,db,scopes)
    refresh='synthetic-refresh-260'
    db.execute("UPDATE saas_ai_learning_mcp_tokens SET refresh_token_hash=%s,refresh_expires_at=now()+interval '1 day' WHERE token_hash=%s",(mcp._hash(refresh),mcp._hash(token)));db.commit()
    async def run():
        app=web.Application();app.router.add_post('/oauth/token',mcp.token)
        async with TestClient(TestServer(app)) as client:
            response=await client.post('/oauth/token',data={'grant_type':'refresh_token','refresh_token':refresh,'client_id':mcp.CHATGPT_CLIENT_ID,'scope':mcp.TOOL_SCOPE+' '+facade.WRITE_SCOPE})
            data=await response.json()
            assert response.status==200,data
            assert (facade.WRITE_SCOPE in data['scope'].split()) is physical
            assert data['scope']==scopes
    asyncio.run(run())
