"""Existing OAuth grants: bounded company reads, no privilege or secret leaks."""
import asyncio
import hashlib
import json
import uuid

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer

from test_tenant_support_inbox import A,B,CA,AGENT,PROFILE,database,db,seed_simulation
from test_ai_learning_mcp import _setup
import tenant_ai_learning_mcp as mcp
import tenant_ai_administrator_read as admin
import tenant_ai_administrator_drafts as drafts
import tenant_ai_administrator_approvals as approvals
import tenant_ai_administrator_observations as observations
import tenant_ai_administrator_apply as apply
import tenant_ai_administrator_api as administrator_api


def test_existing_connection_company_reads_and_immediate_owner_revocation(db,monkeypatch):
    token=_setup(monkeypatch,db)
    async def check():
        app=web.Application();app.router.add_post('/mcp',mcp.mcp)
        async with TestClient(TestServer(app)) as client:
            async def call(name,args={}):
                return await client.post('/mcp',headers={'Authorization':'Bearer '+token},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}})
            result=await call('channels_read')
            assert result.status==200
            payload=json.loads((await result.json())['result']['content'][0]['text'])
            assert len(payload['items'])==1 and payload['items'][0]['connect_channel_id']==CA
            denied=await call('channels_read',{'tenant_id':B});assert denied.status==400
            db.execute("UPDATE saas_tenant_members SET role='operator' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,));db.commit()
            assert (await call('channels_read')).status==403
    asyncio.run(check())


def test_all_read_projections_are_valid_bounded_and_tenant_scoped(db,monkeypatch):
    _setup(monkeypatch,db)
    cfg=mcp._require_cfg();cfg['granted_scopes']=[admin.READ_SCOPE,admin.CONTENT_SCOPE]
    for name in admin.SPECS:
        with db.cursor() as cur:
            if name=='system_status_read':
                assert admin.read(cur,cfg,name,{})['writes_enabled'] is False
                continue
            if name=='simulation_queue_diagnostic_read':
                continue
            result=admin.read(cur,cfg,name,{'limit':1,'include_content':True})
            assert len(result['items'])<=1
            assert B not in json.dumps(result)
    assert db.execute('SELECT count(*) AS n FROM saas_crm_messages')[0]['n']==2


def test_message_content_redacts_secrets_and_preserves_ids(db,monkeypatch):
    _setup(monkeypatch,db)
    db.execute("UPDATE saas_crm_messages SET body='password=private sk-abcdefghijklmnopqrst https://private.test/media?token=secret user@example.com +994501234567',raw='{\"api_key\":\"raw-secret\"}'::jsonb WHERE tenant_id=%s::uuid",(A,));db.commit()
    cfg=mcp._require_cfg();cfg['granted_scopes']=[admin.READ_SCOPE,admin.CONTENT_SCOPE]
    with db.cursor() as cur:
        public=admin.read(cur,cfg,'messages_read',{})
        detailed=admin.read(cur,cfg,'messages_read',{'include_content':True})
    assert 'body' not in public['items'][0]
    body=detailed['items'][0]['body']
    assert all(value not in body for value in ['private','sk-','https://','example.com','994501234567'])
    assert str(uuid.UUID(detailed['items'][0]['id']))==detailed['items'][0]['id']
    assert 'raw' not in detailed['items'][0]


def test_cursor_is_bound_to_tenant_tool_and_content_policy(db,monkeypatch):
    _setup(monkeypatch,db)
    cfg=mcp._require_cfg();cfg['granted_scopes']=[admin.READ_SCOPE,admin.CONTENT_SCOPE]
    marker=admin._page_key(cfg,'messages_read',{},1)
    with db.cursor() as cur:
        for name,args in [('channels_read',{'cursor':marker}),('messages_read',{'cursor':marker,'include_content':True}),('messages_read',{'cursor':marker+'bad'})]:
            with pytest.raises(Exception):admin.read(cur,cfg,name,args)
        with pytest.raises(Exception):admin.read(cur,{**cfg,'AI_LEARNING_MCP_TENANT_ID':B},'messages_read',{'cursor':marker})
    for args in [{'limit':True},{'limit':1000},{'sql':'SELECT 1'},{'include_content':'true'},{'id':1}]:
        with pytest.raises(Exception):admin.validate(args)


def test_read_tool_registry_has_strict_schema_no_write_tools():
    for tool in admin.definitions():
        assert tool['inputSchema']['additionalProperties'] is False
        assert tool['securitySchemes'][0]['scopes']==['ai_learning.read']
        assert tool['annotations']=={'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False}


def test_simulation_queue_diagnostic_is_tenant_bound_read_only(db,monkeypatch):
    source = seed_simulation(db)
    queue_id = str(uuid.uuid4())
    db.execute('''INSERT INTO saas_ai_simulation_queue(id,tenant_id,channel_id,conversation_id,lead_id,source_message_id,
        source_event_id,event_fingerprint,state,attempts) VALUES(%s::uuid,%s::uuid,%s::uuid,'client',41,%s::uuid,
        'issue-152','issue-152-fingerprint','queued',0)''', (queue_id,A,CA,source))
    db.execute("UPDATE saas_ai_agent_settings SET operating_mode='simulation' WHERE tenant_id=%s::uuid", (A,))
    db.commit()
    monkeypatch.setenv('AI_SIMULATION_ENABLED','true')
    monkeypatch.setenv('AI_AGENTS_ENABLED','true')
    _setup(monkeypatch,db)
    cfg=mcp._require_cfg();cfg['granted_scopes']=[admin.READ_SCOPE,admin.CONTENT_SCOPE]
    with db.cursor() as cur:
        item=admin.read(cur,cfg,'simulation_queue_diagnostic_read',{'id':queue_id})
    assert item['id']==queue_id and item['attempts']==0 and item['read_only'] is True
    assert item['admission_eligible_now'] is True and item['blockers']==[]
    db.execute("UPDATE saas_ai_simulation_configs SET enabled=FALSE WHERE tenant_id=%s::uuid",(A,));db.commit()
    with db.cursor() as cur:
        stopped=admin.read(cur,cfg,'simulation_queue_diagnostic_read',{'id':queue_id})
    assert stopped['admission_eligible_now'] is False and 'simulation_disabled' in stopped['blockers']
    with db.cursor() as cur:
        with pytest.raises(Exception): admin.read(cur,{**cfg,'AI_LEARNING_MCP_TENANT_ID':B},'simulation_queue_diagnostic_read',{'id':queue_id})


def test_administrator_drafts_are_flagged_scoped_versioned_and_never_applied(db,monkeypatch):
    _setup(monkeypatch,db)
    cfg=mcp._require_cfg(); cfg['actor_id']=11; cfg['granted_scopes']=[drafts.DRAFT_SCOPE]
    with db.cursor() as cur:
        with pytest.raises(Exception): drafts.create(cur,cfg,{'kind':'knowledge','title':'Blocked','body':{}})
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true')
    with db.cursor() as cur:
        value=drafts.create(cur,cfg,{'kind':'knowledge','title':'Draft knowledge','body':{'claim':'needs review'},'target':{}})
    db.commit()
    assert value['state']=='draft' and value['version']==1
    with db.cursor() as cur:
        changed=drafts.update(cur,cfg,{'id':value['id'],'expected_version':1,'body':{'claim':'edited'}})
        assert drafts.listing(cur,cfg,{})['items'][0]['id']==value['id']
        with pytest.raises(Exception): drafts.update(cur,cfg,{'id':value['id'],'expected_version':1,'body':{}})
    db.commit()
    assert changed['version']==2


def test_approval_binds_exact_draft_version_and_is_single_use(db,monkeypatch):
    _setup(monkeypatch,db); monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true')
    cfg=mcp._require_cfg(); cfg['actor_id']=11; cfg['granted_scopes']=[drafts.DRAFT_SCOPE]
    with db.cursor() as cur:
        value=drafts.create(cur,cfg,{'kind':'agent_improvement','title':'Draft','body':{}})
        receipt=approvals.request(cur,{'tenant_id':A,'telegram_id':11},value['id'],1)
        assert approvals.approve(cur,{'tenant_id':A,'telegram_id':11},receipt['id'],'approve')['state']=='approved'
        approvals.consume(cur,A,receipt['id'],value['id'],1,'test')
        with pytest.raises(Exception): approvals.consume(cur,A,receipt['id'],value['id'],1,'test')
    db.commit()


def test_owner_batch_approval_is_atomic_and_tenant_scoped(db,monkeypatch):
    _setup(monkeypatch,db); monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true')
    cfg=mcp._require_cfg(); cfg['actor_id']=11; cfg['granted_scopes']=[drafts.DRAFT_SCOPE]
    with db.cursor() as cur:
        first=drafts.create(cur,cfg,{'kind':'knowledge','title':'One','body':{}}); second=drafts.create(cur,cfg,{'kind':'support_case','title':'Two','body':{}})
        a=approvals.request(cur,PROFILE,first['id'],first['version']); b=approvals.request(cur,PROFILE,second['id'],second['version'])
        assert approvals.approve_many(cur,PROFILE,[a['id'],b['id']])['count']==2
        with pytest.raises(Exception): approvals.approve_many(cur,PROFILE,[a['id'],b['id']])
    db.commit()


def test_mcp_can_request_and_read_but_never_decide_or_apply_an_approval(db,monkeypatch):
    _setup(monkeypatch,db); monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true')
    cfg=mcp._require_cfg(); cfg['actor_id']=11; cfg['granted_scopes']=[drafts.DRAFT_SCOPE]
    with db.cursor() as cur:
        draft=drafts.create(cur,cfg,{'kind':'knowledge','title':'Draft','body':{'claim':'review'},'target':{}})
        receipt=mcp._tools(cur,cfg,'administrator_approval_request',{'draft_id':draft['id'],'version':draft['version']})
        listed=mcp._tools(cur,cfg,'administrator_approval_list',{})
        assert listed['items'][0]['id']==receipt['id'] and listed['items'][0]['state']=='pending'
        with pytest.raises(Exception): mcp._tools(cur,cfg,'administrator_approval_approve',{'approval_id':receipt['id']})
    db.commit()


def test_administrator_approval_and_live_apply_are_owner_only(db,monkeypatch):
    seed_simulation(db); _setup(monkeypatch,db)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true'); monkeypatch.setenv('AI_AUTONOMOUS_APPLY_ENABLED','true')
    cfg=mcp._require_cfg(); cfg['actor_id']=11; cfg['granted_scopes']=[drafts.DRAFT_SCOPE]
    with db.cursor() as cur:
        cur.execute('SELECT version FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT)); version=cur.fetchone()['version']
        draft=drafts.create(cur,cfg,{'kind':'agent_improvement','title':'Prompt','body':{'agent_id':AGENT,'expected_version':version,'instructions':'Use verified evidence only.'}})
        cur.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,))
        with pytest.raises(Exception): approvals.request(cur,PROFILE,draft['id'],draft['version'])
        cur.execute("UPDATE saas_tenant_members SET role='owner' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,))
        receipt=approvals.request(cur,PROFILE,draft['id'],draft['version']); approvals.approve(cur,PROFILE,receipt['id'],'approve')
        cur.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,))
        with pytest.raises(Exception): apply.apply_agent_prompt(cur,PROFILE,receipt['id'],draft['id'])
        cur.execute("UPDATE saas_tenant_members SET role='owner' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,))
        assert apply.apply_agent_prompt(cur,PROFILE,receipt['id'],draft['id'])['rollback_available'] is True
    db.commit()


def test_owner_approval_http_surface_requires_same_origin_and_strict_actions(monkeypatch):
    seen=[]
    monkeypatch.setattr(administrator_api,'_with_connection',lambda fn,*args,**kwargs: seen.append((fn,args,kwargs)) or {'items':[]})
    profile={'tenant_id':A,'telegram_id':11,'role':'owner'}
    async def check():
        app=web.Application(); app.router.add_get('/approvals',administrator_api.handler(lambda _:profile,'https://crm.pro.az',__import__('logging').getLogger(__name__)))
        app.router.add_post('/approvals',administrator_api.handler(lambda _:profile,'https://crm.pro.az',__import__('logging').getLogger(__name__)))
        async with TestClient(TestServer(app)) as client:
            assert (await client.get('/approvals')).status==200
            assert (await client.post('/approvals',json={'action':'approve','approval_id':str(uuid.uuid4())})).status==403
            response=await client.post('/approvals',headers={'Origin':'https://crm.pro.az'},json={'action':'approve','approval_id':str(uuid.uuid4()),'extra':True})
            assert response.status==400
    asyncio.run(check())
    assert seen and seen[0][0] is approvals.listing


def test_observations_are_deduplicated_and_disabled_by_default(db,monkeypatch):
    _setup(monkeypatch,db)
    assert observations.enabled() is False
    with db.cursor() as cur:
        first=observations.enqueue(cur,A,'simulation',{'run_id':'safe'})
        second=observations.enqueue(cur,A,'simulation',{'run_id':'safe'})
        cur.execute('SELECT count(*) AS n FROM saas_ai_administrator_observations WHERE tenant_id=%s::uuid',(A,))
        assert first==second and cur.fetchone()['n']==1
    db.commit()


def test_owner_approved_prompt_application_has_versioned_rollback(db,monkeypatch):
    seed_simulation(db); _setup(monkeypatch,db)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true'); monkeypatch.setenv('AI_AUTONOMOUS_APPLY_ENABLED','true')
    cfg=mcp._require_cfg(); cfg['actor_id']=11; cfg['granted_scopes']=[drafts.DRAFT_SCOPE]
    with db.cursor() as cur:
        cur.execute('SELECT version FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT)); version=cur.fetchone()['version']
        draft=drafts.create(cur,cfg,{'kind':'agent_improvement','title':'Prompt','body':{'agent_id':AGENT,'expected_version':version,'instructions':'Use verified evidence only.'}})
        receipt=approvals.request(cur,PROFILE,draft['id'],draft['version']); approvals.approve(cur,PROFILE,receipt['id'],'approve')
        result=apply.apply_agent_prompt(cur,PROFILE,receipt['id'],draft['id'])
        assert result['rollback_available'] is True
        restored=apply.rollback_agent_prompt(cur,PROFILE,receipt['id'])
        assert restored['rolled_back'] is True
    db.commit()
