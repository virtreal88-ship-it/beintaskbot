"""Exact-route owner metadata: real isolated SQL, no content/provider or writes."""
import asyncio
import json
import re
import uuid
from unittest.mock import Mock
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from test_tenant_support_inbox import database,db,A,B,CA,CB
from test_ai_learning_mcp import _setup
import tenant_ai_learning_mcp as mcp
import tenant_ai_administrator_read as admin
import tenant_dialog_audio_store as store
from tenant_linear_policy import TenantLinearError

NAME='audio_attempts_diagnostic_read'
ARGS={'channel_id':CA,'lead_id':'41'}

@pytest.fixture
def diagnostic(db,monkeypatch):
    token=_setup(monkeypatch,db,scope=mcp.TOOL_SCOPE+' '+admin.READ_SCOPE)
    cfg={**mcp._require_cfg(),'actor_id':11,'granted_scopes':[admin.READ_SCOPE]}
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,enabled,scopes) VALUES(%s::uuid,%s::uuid,'Synthetic',11,TRUE,%s::jsonb)",(A,cfg['AI_LEARNING_MCP_AGENT_ID'],json.dumps([{'provider':'connect','channel_id':CA,'allow_group_messages':True}])))
    store.schema(db)
    for module,name in [('tenant_chat_ai_provider','transcribe'),('tenant_chat_ai_provider','generate')]:
        monkeypatch.setattr(module+'.'+name,Mock(side_effect=AssertionError('No provider allowed')))
    return db,cfg,token


def source(db,*,tenant=A,channel=CA,lead=41,external=None,at='2026-10-22T10:00:00Z'):
    key=str(uuid.uuid4());external=external or 'connect:'+str(uuid.uuid4())
    db.execute("INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,message_type,body,raw,happened_at) VALUES(%s::uuid,%s::uuid,%s,%s,%s,'incoming','audio','PRIVATE_TRANSCRIPT https://private.invalid','{\"jid\":\"123456789@g.us\",\"api_key\":\"PRIVATE_KEY\"}'::jsonb,%s::timestamptz)",(tenant,key,lead,external,'connect:'+channel,at));db.commit()
    return key,external


def attempt(db,cfg,row,*,code='provider_timeout',model='gpt-transcribe'):
    key=str(uuid.uuid4())
    generation=db.execute('SELECT count(*) AS n FROM saas_dialog_audio_attempts WHERE tenant_id=%s::uuid AND message_row_id=%s::uuid',(A,row[0]))[0]['n']+1
    db.execute("INSERT INTO saas_dialog_audio_attempts(tenant_id,id,message_row_id,selection_row_id,selection_version,channel_id,external_id,source_version,view_channel_id,view_conversation_id,generation,origin,actor_id,model,state,code,transcript,published,dispatched_at,completed_at) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,'v',%s::uuid,%s,'v',%s::uuid,'123456789@g.us',%s,'manual',11,%s,'unknown',%s,'PRIVATE_TRANSCRIPT',FALSE,now(),now())",(A,key,row[0],row[0],CA,row[1],CA,generation,model,code));db.commit()
    return key


def read(db,cfg,args=ARGS):
    with db.cursor() as cur:return admin.read(cur,cfg,NAME,args)


def test_metadata_only_no_attempt_is_not_provider_failure(diagnostic,monkeypatch):
    db,cfg,_=diagnostic;one=source(db);two=source(db,at='2026-10-31T10:00:00Z');key=attempt(db,cfg,one)
    sql=[];execute=db.execute
    def readonly(query,*args):
        sql.append(query)
        assert not re.search(r'\b(INSERT|UPDATE|DELETE|CREATE|ALTER|TRUNCATE|DROP)\b',query,re.I)
        return execute(query,*args)
    monkeypatch.setattr(db,'execute',readonly)
    result=read(db,cfg)
    assert result['read_only'] and result['coverage']=='manual_attempts_only'
    assert len(result['items'])==2
    stored=next(i for i in result['items'] if i['message_row_id']==one[0]);empty=next(i for i in result['items'] if i['message_row_id']==two[0])
    assert stored['attempt']['id']==key and stored['attempt']['code']=='provider_timeout'
    assert stored['attempt']['dispatched_at'] and stored['attempt']['completed_at']
    assert empty['attempt_status']=='no_attempt' and empty['attempt'] is None
    assert all(i['source_current'] is None and i['source_current_reason']=='metadata_only' for i in result['items'])
    wire=json.dumps(result)
    assert all(v not in wire for v in ('PRIVATE','123456789','@g.us','https://','cost_token','transcript','usage'))
    assert all('SELECT * FROM visible' not in q for q in sql)
    source_query=next(q for q in sql if 'm.id AS message_row_id' in q)
    assert 'm.body' not in source_query and 'm.raw' not in source_query


def test_wrong_tenant_channel_lead_and_current_owner_are_denied(diagnostic):
    db,cfg,_=diagnostic;source(db);foreign=source(db,tenant=B,channel=CB,lead=42)
    for changed,args in [({**cfg,'AI_LEARNING_MCP_TENANT_ID':B},ARGS),(cfg,{**ARGS,'channel_id':CB}),(cfg,{**ARGS,'lead_id':'42'}),({**cfg,'actor_id':99},ARGS),({**cfg,'granted_scopes':[]},ARGS)]:
        with pytest.raises(TenantLinearError):read(db,changed,args)
    db.execute("UPDATE saas_tenant_members SET role='operator' WHERE tenant_id=%s::uuid",(A,));db.commit()
    with pytest.raises(TenantLinearError):read(db,cfg)


def test_agent_scope_revocation_blocks_metadata(diagnostic):
    db,cfg,_=diagnostic
    db.execute("UPDATE saas_ai_agents SET scopes='[]'::jsonb WHERE tenant_id=%s::uuid",(A,));db.commit()
    with pytest.raises(TenantLinearError):read(db,cfg)


def test_bounded_cursor_reaches_old_sources_and_attempts_and_binds_actor_route(diagnostic):
    db,cfg,_=diagnostic
    oldest=source(db);attempt_ids={attempt(db,cfg,oldest) for _ in range(23)}
    newer=source(db,at='2026-10-31T10:00:00Z')
    first=read(db,cfg);assert len(first['items'])==20 and first['next_cursor']
    second=read(db,cfg,{**ARGS,'cursor':first['next_cursor']})
    assert len(second['items'])==4 and second['next_cursor'] is None
    items=first['items']+second['items']
    assert {i['attempt']['id'] for i in items if i['attempt']}==attempt_ids
    assert any(i['message_row_id']==newer[0] and i['attempt'] is None for i in items)
    for args in ({**ARGS,'cursor':first['next_cursor']+'x'},{**ARGS,'cursor':first['next_cursor'],'lead_id':'42'},{**ARGS,'limit':21},{**ARGS,'include_content':True}):
        with pytest.raises(TenantLinearError):read(db,cfg,args)
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,12,'owner','Other synthetic owner')",(A,));db.commit()
    with pytest.raises(TenantLinearError):read(db,{**cfg,'actor_id':12},{**ARGS,'cursor':first['next_cursor']})


def test_stored_arbitrary_codes_models_and_external_jid_are_not_returned(diagnostic):
    db,cfg,_=diagnostic
    row=source(db,external='123456789@g.us');attempt(db,cfg,row,code='PRIVATE_KEY https://private.invalid',model='sk-abcdefghijklmnopqrst')
    item=read(db,cfg)['items'][0]
    assert item['message_id'] is None and item['attempt']['code'] is None and item['attempt']['model'] is None
    assert 'PRIVATE' not in json.dumps(item) and 'sk-' not in json.dumps(item)


def test_missing_attempt_table_is_read_only_source_metadata(diagnostic):
    db,cfg,_=diagnostic;source(db)
    db.execute('DROP TABLE saas_dialog_audio_attempts');db.commit()
    result=read(db,cfg)
    assert not result['attempt_records_available'] and result['items'][0]['attempt_status']=='no_attempt'


def test_existing_primary_tool_scope_and_audit_are_preserved(diagnostic):
    db,cfg,token=diagnostic;source(db)
    assert next(t for t in admin.definitions() if t['name']==NAME)['inputSchema']['required']==['channel_id','lead_id']
    async def scenario():
        app=web.Application();app.router.add_post('/mcp',mcp.mcp)
        async with TestClient(TestServer(app)) as client:
            body={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':NAME,'arguments':ARGS}}
            assert (await client.post('/mcp',json=body)).status==401
            response=await client.post('/mcp',headers={'Authorization':'Bearer '+token},json=body)
            assert response.status==200
            result=json.loads((await response.json())['result']['content'][0]['text'])
            assert result['metadata_only'] and len(result['items'])==1
    asyncio.run(scenario())
    assert db.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE action='mcp_administrator_read'")[0]['n']==1
    assert db.execute('SELECT count(*) AS n FROM saas_dialog_audio_attempts')[0]['n']==0


@pytest.mark.parametrize('args',[None,[],{}, {'channel_id':CA,'lead_id':'0'}, {'channel_id':CA,'lead_id':'41','cursor':False}])
def test_malformed_arguments_are_400(diagnostic,args):
    db,cfg,_=diagnostic
    with pytest.raises(TenantLinearError) as error:read(db,cfg,args)
    assert error.value.status==400
