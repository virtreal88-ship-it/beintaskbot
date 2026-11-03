"""CRM01 company/deal lists: genuine cookies, PostgreSQL and existing services.

All data is synthetic. Reads use production handlers and current membership;
only the external provider boundary is a forbidden transport spy.
"""
import ast
import asyncio
import contextvars
import importlib
import json
import os
import socket
import subprocess
import threading
import time
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import psycopg
import pytest

from test_company_employees_navigation_postgres import (
    A, B, COOKIE_NAME, ORIGIN, ROOT, LOGGER, database, db, staff, _apis, _snapshot,
)
import tenant_crm_native_companies as companies
import tenant_crm_native_contacts as contacts
import tenant_crm_native_deals as native_deals
import tenant_crm_native_company_api as company_api
import tenant_crm_native_deal_api as native_deal_api
import tenant_crm_store as crm
import tenant_deal_api as legacy_deal_api
import tenant_chat_api as chat_api
import tenant_chat_history_api as history_api
import tenant_crm_sync_store as sync_store
import tenant_member_store as members
from tenant_platform_core import TenantPlatformError
from tenant_policy import TenantPolicy


def ident(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'isolated-company-deal-states-' + name))


COMPANIES = [ident('company-' + str(index)) for index in range(4)]
DEALS = [ident('deal-' + str(index)) for index in range(5)]
CONTACTS = [ident('contact-' + str(index)) for index in range(2)]
BIG_LEAD_ID = '9007199254740993'


class ForbiddenProvider:
    def __init__(self): self.calls = []
    async def __call__(self, tenant, method, path, **kwargs):
        self.calls.append((str(tenant), method, path))
        raise AssertionError('List/card read reached external provider: ' + repr((method, path)))


def _production_entries(provider):
    """Use exact thin bot functions without importing startup or credentials."""
    names = {'_sync_tenant_crm', '_sync_tenant_chat', '_tenant_talk_channel',
             '_tenant_normalize_talk_message', '_tenant_kommo_timestamp'}
    tree = ast.parse((ROOT / 'bot.py').read_text(encoding='utf-8-sig'))
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert {node.name for node in nodes} == names
    namespace = {'asyncio': asyncio, 'json': json, 'datetime': datetime, 'timezone': timezone,
        'TenantPolicy': TenantPolicy, 'TenantPlatformError': TenantPlatformError,
        'enqueue_tenant_crm_sync': sync_store.enqueue, '_tenant_kommo_request': provider,
        'tenant_member': members.member, 'get_tenant_crm_deal': crm.get_crm_deal,
        'list_tenant_crm_messages': crm.list_crm_messages, 'upsert_tenant_crm_messages': crm.upsert_crm_messages,
        'upsert_tenant_crm_deals': crm.upsert_crm_deals}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / 'bot.py'), 'exec'), namespace)
    return namespace


@pytest.fixture
def company_deal_lists(staff, monkeypatch):
    f = staff
    for name in ('tenant_crm_native_companies', 'tenant_crm_native_contacts',
                 'tenant_crm_native_deals', 'tenant_crm_native_tasks', 'tenant_crm_store',
                 'tenant_crm_sync_store', 'tenant_chat_history_store', 'tenant_read_session', 'tenant_crm_list_states',
                 'tenant_task_flow', 'tenant_tasks', 'tenant_task_commands', 'tenant_task_completion', 'tenant_task_approvals',
                 'tenant_crm_sync_policy', 'tenant_crm_contact_listing', 'tenant_deal_completion_store'):
        module = importlib.import_module(name)
        if hasattr(module, '_connect'): monkeypatch.setattr(module, '_connect', f['factory'])
    with f['factory']() as conn:
        for tenant in (A, B):
            conn.execute("UPDATE saas_tenants SET modules='{\"customers\":true,\"deals\":true,\"tasks\":false}' WHERE id=%s::uuid", (tenant,))
            for pipeline, stage, owner in (('10', '101', 182 if tenant == A else 22), ('20', '201', 11 if tenant == A else 22), ('p-historical', 's-new', 11 if tenant == A else 22)):
                conn.execute('INSERT INTO saas_tenant_pipelines(tenant_id,pipeline_id,name,owner_telegram_id) VALUES(%s::uuid,%s,%s,%s)',
                             (tenant, pipeline, 'Pipeline ' + pipeline, owner))
                conn.execute('INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name) VALUES(%s::uuid,%s,%s,%s)',
                             (tenant, pipeline, stage, 'Stage ' + stage))
            conn.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,account_domain,connected_at) VALUES(%s::uuid,'kommo','connected','synthetic.invalid',now())", (tenant,))
        conn.execute("INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name,settings) VALUES(%s::uuid,'10','102','Hidden stage','{\"visible\":false}')", (A,))
        for tenant, actor, role, grants in (
            (A,181,'admin',['customers','deals']), (A,182,'manager',['customers','deals']),
            (A,183,'operator',['customers','deals']), (A,184,'admin',['customers:view','deals:view']),
            (A,185,'admin',['customers']), (A,186,'admin',['deals']), (A,187,'admin',[]),
            (A,188,'admin',['customers','deals']), (B,188,'admin',['customers','deals']),
            (A,189,'admin',['customers','deals']),
        ):
            conn.execute('INSERT INTO saas_tenant_members(tenant_id,telegram_id,display_name,role,permissions) VALUES(%s::uuid,%s,%s,%s,%s::jsonb)',
                         (tenant, actor, 'Synthetic list actor ' + str(actor), role, json.dumps(grants)))
    companies.create_company(A, 'A company Alpha', '+994500001001', 'alpha@synthetic.invalid', 'A private company', 182, company_id=COMPANIES[0])
    companies.create_company(A, 'Literal %_\\ company', '+994500001002', 'literal@synthetic.invalid', '', 11, company_id=COMPANIES[1])
    companies.create_company(A, 'Archived company', company_id=COMPANIES[2])
    companies.archive_company(A, COMPANIES[2])
    companies.create_company(B, 'B PRIVATE company Alpha', '+994500001001', 'alpha@synthetic.invalid', 'B private company', 22, company_id=COMPANIES[3])
    for tenant, index in ((A,0),(B,1)):
        contacts.create_contact(tenant, 'Synthetic client ' + str(index), '+994500002001', contact_id=CONTACTS[index])
        companies.link_entity(tenant, COMPANIES[0 if tenant == A else 3], 'contact', CONTACTS[index])
    for index, tenant, pipeline, stage, owner, title in (
        (0,A,'10','101',182,'A native visible deal'),
        (1,A,'20','201',11,'A native other-pipeline deal'),
        (2,A,'10','102',182,'A native hidden-stage deal'),
        (3,A,'p-historical','s-new',11,'Literal %_\\ native deal'),
        (4,B,'10','101',22,'B PRIVATE native deal'),
    ):
        native_deals.create_deal(tenant, title, pipeline, stage, responsible_id=owner,
            contact_id=CONTACTS[0 if tenant == A else 1], deal_id=DEALS[index], description='Preserve original native relations')
        companies.link_entity(tenant, COMPANIES[0 if tenant == A else 3], 'deal', DEALS[index])
    for tenant in (A, B):
        crm.upsert_crm_deals(tenant_id=tenant, deals=[{
            'kommo_lead_id': 101, 'pipeline_id': 10, 'status_id': 101,
            'name': 'A saved external deal' if tenant == A else 'B PRIVATE saved external deal',
            'contact_name': 'Saved client', 'phone': '+994500002001', 'channel': 'kommo',
            'raw': {'id': 101, 'account_id': 100}, 'source_updated_at': '2099-01-01T00:00:00Z',
        }])
        crm.upsert_crm_messages(tenant_id=tenant, kommo_lead_id=101, messages=[{
            'external_id': 'saved-list-message', 'direction': 'incoming', 'channel': 'kommo',
            'body': 'A saved client message' if tenant == A else 'B PRIVATE saved client message',
            'message_type': 'text', 'happened_at': '2099-01-01T00:00:00Z',
        }])
    crm.upsert_crm_deals(tenant_id=A, deals=[
        {'kommo_lead_id':102,'pipeline_id':10,'status_id':102,'name':'HIDDEN saved stage deal','channel':'kommo'},
        {'kommo_lead_id':201,'pipeline_id':20,'status_id':201,'name':'Other saved pipeline deal','channel':'kommo'},
        {'kommo_lead_id':202,'pipeline_id':10,'status_id':101,'name':'Saved Connect literal %_\\ deal','channel':'connect:synthetic','raw':{}},
        {'kommo_lead_id':int(BIG_LEAD_ID),'pipeline_id':10,'status_id':101,'name':'Lossless saved deal','channel':'kommo'},
        {'kommo_lead_id':301,'pipeline_id':10,'status_id':101,'name':'Deleted saved deal','channel':'kommo'},
    ])
    with f['factory']() as conn:
        conn.execute('UPDATE saas_crm_deals SET deleted_at=now() WHERE tenant_id=%s::uuid AND kommo_lead_id=301', (A,))
    # Explicit real feature bootstrap precedes read-only acceptance. The
    # existing global progress consumer also reads this optional queue schema.
    import tenant_crm_sync_schema as sync_schema
    monkeypatch.setattr(sync_schema, '_ready', False)
    with f['factory']() as conn:
        sync_schema.ensure_sync_schema(conn)
    # The existing owner approval widget reads its own journal at app startup.
    # Initialize that existing feature too, without commands or provider work.
    import tenant_deal_completion_store as deal_commands
    with f['factory']() as conn:
        deal_commands.ensure_schema(conn)
    f['provider'] = ForbiddenProvider()
    f['production'] = _production_entries(f['provider'])
    f['sync'] = f['production']['_sync_tenant_crm']
    yield f
    assert f['provider'].calls == []
    assert f['blocked'] == []


def _handlers(f, origin=ORIGIN):
    principal = f['session'].profile
    legacy = legacy_deal_api.handlers(legacy_deal_api.DealApiContext(
        member=principal, policy=TenantPolicy, sync_crm=f['production']['_sync_tenant_crm'],
        sync_status=sync_store.sync_status, list_deals=crm.list_crm_deals, get_deal=crm.get_crm_deal,
        platform_error=TenantPlatformError, logger=LOGGER))
    chat = chat_api.handlers(chat_api.ChatApiContext(member=principal, policy=TenantPolicy,
        get_deal=crm.get_crm_deal, list_messages=crm.list_crm_messages,
        sync_chat=f['production']['_sync_tenant_chat'], platform_error=TenantPlatformError, logger=LOGGER))
    return legacy, company_api.handler(principal,origin,LOGGER), native_deal_api.handler(principal,origin,LOGGER), chat


def _app(f, origin=ORIGIN):
    @web.middleware
    async def no_mutations(request, handler):
        f['requests'].append((request.method, request.path_qs))
        if request.method not in {'GET','HEAD'}:
            f['blocked'].append((request.method,request.path))
            return web.json_response({'success':False,'error':'Read-only isolated list acceptance'},status=405)
        return await handler(request)
    app=web.Application(middlewares=[no_mutations])
    auth,_employee=_apis(f,origin)
    legacy,company,deal,chat=_handlers(f,origin)
    app.router.add_get('/api/platform/me',auth['me'])
    app.router.add_get('/api/platform/crm/deals',legacy['deals'])
    app.router.add_get('/api/platform/crm/deal',legacy['deal'])
    app.router.add_get('/api/platform/crm/sync',legacy['sync_status'])
    app.router.add_get('/api/platform/crm/chat',chat['chat'])
    app.router.add_get('/api/platform/crm/chat/history',history_api.handler(f['session'].profile,LOGGER))
    for suffix in ('','/{id}'):
        app.router.add_get('/api/platform/crm/native/companies'+suffix,company)
        app.router.add_get('/api/platform/crm/native/deals'+suffix,deal)
    return app


def _state(f):
    result=_snapshot(f)
    with f['factory']() as conn:
        tables=conn.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename LIKE 'saas_%' ORDER BY tablename").fetchall()
        for row in tables:
            values=conn.execute(psycopg.sql.SQL('SELECT to_jsonb(t) AS value FROM {} t').format(psycopg.sql.Identifier(row['tablename']))).fetchall()
            result[row['tablename']]=sorted(json.dumps(item['value'],sort_keys=True,default=str) for item in values)
    return result


def _identity(tenant=A,actor=11):
    return {'expected_tenant_id':tenant,'expected_user_id':str(actor)}


async def _get(http,cookie,path,query=None):
    response=await http.get(path,params=query or {},headers={'Cookie':COOKIE_NAME+'='+cookie} if cookie else {})
    return response.status,await response.json()


def test_existing_read_routes_keep_original_objects_and_never_sync(company_deal_lists):
    f=company_deal_lists
    cookie=f['session'].issue(A,11)
    before=_state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            for _ in range(2):
                for path,field in (('/api/platform/crm/native/companies','companies'),('/api/platform/crm/native/deals','deals'),('/api/platform/crm/deals','deals')):
                    status,page=await _get(http,cookie,path)
                    assert status==200 and page[field],page
                    assert 'B PRIVATE' not in json.dumps(page)
                status,company=await _get(http,cookie,'/api/platform/crm/native/companies/'+COMPANIES[0],_identity())
                assert status==200 and str(company['company']['id'])==COMPANIES[0]
                assert {str(row['id']) for row in company['company']['deals']}==set(DEALS[:4])
                status,deal=await _get(http,cookie,'/api/platform/crm/native/deals/'+DEALS[3],_identity())
                assert status==200 and deal['deal']['pipeline_id']=='p-historical'
                status,chat=await _get(http,cookie,'/api/platform/crm/chat',{'lead_id':'101'})
                assert status==200 and chat['messages'][0]['body']=='A saved client message',chat
    asyncio.run(scenario())
    assert _state(f)==before and f['provider'].calls==[]


@pytest.mark.parametrize('actor,company_status,native_status,legacy_status',[
    (11,200,200,200),(181,200,200,200),(182,200,200,200),(183,403,403,403),
    (184,200,200,403),(185,200,403,200),(186,403,200,200),(187,403,403,403),
])
def test_existing_company_native_deal_and_cached_scopes_remain_distinct(company_deal_lists,actor,company_status,native_status,legacy_status):
    f=company_deal_lists
    cookie=f['session'].issue(A,actor)
    before=_state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            for path,expected in (('/api/platform/crm/native/companies',company_status),('/api/platform/crm/native/deals',native_status),('/api/platform/crm/deals',legacy_status)):
                status,page=await _get(http,cookie,path)
                assert status==expected,page
                assert 'B PRIVATE' not in json.dumps(page)
                if status==200 and path.endswith('/native/deals'):
                    # Native view authority is tenant-wide, including manager
                    # and historical alpha pipeline IDs. It is not Task scope.
                    assert {row['id'] for row in page['deals']}==set(DEALS[:4])
                if status==200 and path=='/api/platform/crm/deals':
                    ids={str(row['kommo_lead_id']) for row in page['deals']}
                    if actor==182: assert ids=={'101','202',BIG_LEAD_ID}
                    assert '102' not in ids and '301' not in ids
    asyncio.run(scenario())
    assert _state(f)==before


COLLECTIONS = [
    ('company','native','/api/platform/crm/native/companies','companies',2,50),
    ('deal','native','/api/platform/crm/native/deals','deals',4,50),
    ('deal','saved','/api/platform/crm/deals','deals',4,100),
]


async def _listing(http,cookie,path,*,actor=11,tenant=A,extra=None):
    query=[('listing','1'),*_identity(tenant,actor).items()]
    if extra: query.extend(extra.items() if isinstance(extra,dict) else extra)
    return await _get(http,cookie,path,query)


@pytest.mark.parametrize('entity,source,path,field,total,cap',COLLECTIONS)
def test_listing_reports_one_exact_current_selection_and_literal_search(company_deal_lists,entity,source,path,field,total,cap):
    f=company_deal_lists
    cookie=f['session'].issue(A,11)
    before=_state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            for _ in range(2):
                status,page=await _listing(http,cookie,path)
                assert status==200,page
                assert (page['entity'],page['source'],page['total'],page['shown'],page['limit'],page['offset'],page['has_more'],page['state'])==(entity,source,total,total,cap,0,False,'ready')
                assert page['actions']['view'] is True
                assert len(page[field])==total and len({row['id'] for row in page[field]})==total
                assert all(isinstance(row['id'],str) and row['source']==source for row in page[field])
                assert 'B PRIVATE' not in json.dumps(page)
                status,filtered=await _listing(http,cookie,path,extra={'search':'%_\\'})
                assert status==200 and filtered['total']==filtered['shown']==len(filtered[field])==1,filtered
                assert '%_\\' in (filtered[field][0].get('name') or filtered[field][0].get('title'))
                status,missing=await _listing(http,cookie,path,extra={'search':'NO MATCH COMPANY DEAL 192837'})
                assert status==200 and missing['state']=='no_matches' and missing['total']==missing['shown']==0 and missing[field]==[],missing
    asyncio.run(scenario())
    assert _state(f)==before and f['provider'].calls==[]


@pytest.mark.parametrize('actor,company_status,native_status,saved_status',[
    (11,200,200,200),(181,200,200,200),(182,200,200,200),(183,403,403,403),
    (184,200,200,403),(185,200,403,200),(186,403,200,200),(187,403,403,403),
])
def test_fresh_listing_preserves_distinct_native_and_cached_authority(company_deal_lists,actor,company_status,native_status,saved_status):
    f=company_deal_lists
    cookie=f['session'].issue(A,actor)
    before=_state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            for (_entity,source,path,field,_total,_cap),expected in zip(COLLECTIONS,(company_status,native_status,saved_status)):
                status,page=await _listing(http,cookie,path,actor=actor)
                assert status==expected,page
                if status==403:
                    assert page['error']=='crm_forbidden' and field not in page and 'total' not in page,page
                else:
                    assert page['source']==source and page['actions']['view'] is True
                    if source=='native' and field=='deals': assert {row['id'] for row in page[field]}==set(DEALS[:4])
                    if source=='saved' and actor==182: assert {row['id'] for row in page[field]}=={'101','202',BIG_LEAD_ID}
                    if actor==184:
                        assert page['actions']['create'] is page['actions']['update'] is page['actions']['archive'] is False
                assert 'B PRIVATE' not in json.dumps(page)
    asyncio.run(scenario())
    assert _state(f)==before


@pytest.mark.parametrize('entity,source,path,field,total,cap',COLLECTIONS)
def test_real_empty_source_has_no_rows_and_no_fabricated_error_count(company_deal_lists,entity,source,path,field,total,cap):
    f=company_deal_lists
    with f['factory']() as conn:
        table='saas_crm_deals' if source=='saved' else 'saas_crm_native_companies' if entity=='company' else 'saas_crm_native_deals'
        column='deleted_at' if source=='saved' else 'archived_at'
        conn.execute(psycopg.sql.SQL('UPDATE {} SET {}=now() WHERE tenant_id=%s::uuid').format(psycopg.sql.Identifier(table),psycopg.sql.Identifier(column)),(A,))
    cookie=f['session'].issue(A,11)
    before=_state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status,page=await _listing(http,cookie,path)
            assert status==200 and page['state']=='empty' and page['total']==page['shown']==0 and not page['has_more'] and page[field]==[],page
    asyncio.run(scenario())
    assert _state(f)==before and f['provider'].calls==[]


@pytest.mark.parametrize('entity,source,path,field,total,cap',COLLECTIONS)
def test_window_is_bounded_but_exact_old_object_is_not_lost(company_deal_lists,entity,source,path,field,total,cap):
    f=company_deal_lists
    with f['factory']() as conn:
        for index in range(cap+3):
            if entity=='company':
                conn.execute("INSERT INTO saas_crm_native_companies(id,tenant_id,name,updated_at) VALUES(%s::uuid,%s::uuid,%s,'2100-01-01')",(ident('window-company-'+str(index)),A,'Window company '+str(index)))
            elif source=='native':
                conn.execute("INSERT INTO saas_crm_native_deals(id,tenant_id,title,pipeline_id,stage_id,updated_at) VALUES(%s::uuid,%s::uuid,%s,'10','101','2100-01-01')",(ident('window-deal-'+str(index)),A,'Window native deal '+str(index)))
            else:
                conn.execute("INSERT INTO saas_crm_deals(tenant_id,kommo_lead_id,pipeline_id,status_id,name,source_updated_at) VALUES(%s::uuid,%s,10,101,%s,'2100-01-01')",(A,10000+index,'Window saved deal '+str(index)))
    cookie=f['session'].issue(A,11)
    before=_state(f)
    old_id=COMPANIES[0] if entity=='company' else DEALS[0] if source=='native' else '101'
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status,page=await _listing(http,cookie,path)
            assert status==200 and page['total']==total+cap+3 and page['shown']==len(page[field])==cap and page['has_more'] is True,page
            assert old_id not in {row['id'] for row in page[field]}
            status,repeated=await _listing(http,cookie,path)
            assert status==200 and repeated[field]==page[field], 'Timestamp ties must have deterministic ID order'
            exact_path=path+'/'+old_id.upper() if source=='native' else '/api/platform/crm/deal'
            query={'read':'1',**_identity(),**({'lead_id':old_id} if source=='saved' else {})}
            status,exact=await _get(http,cookie,exact_path,query)
            assert status==200 and exact[entity]['id']==old_id and exact['source']==source,exact
            if source=='saved':
                status,big=await _get(http,cookie,'/api/platform/crm/deal',{'read':'1','lead_id':BIG_LEAD_ID,**_identity()})
                assert status==200 and big['deal']['id']==BIG_LEAD_ID and big['deal']['kommo_lead_id']==BIG_LEAD_ID,big
                status,last=await _listing(http,cookie,path,extra={'offset':total+cap+10})
                assert status==200 and last['total']==total+cap+3 and last['shown']==0 and last[field]==[] and not last['has_more'],last
    asyncio.run(scenario())
    assert _state(f)==before


@pytest.mark.parametrize('mode,actor,width,language',[
    ('ready',11,360,'ru'),('ready',181,390,'az'),('ready',182,768,'en'),('ready',184,1440,'ru'),
    ('company_states',185,390,'en'),('deal_states',181,390,'en'),
    ('tenant_late',188,390,'en'),('workflow_revoke',182,390,'en'),
    ('catalog_profile_first',181,390,'az'),('catalog_workspace_first',181,390,'en'),
])
def test_actual_company_deal_app_uses_current_cookie_postgres(company_deal_lists,monkeypatch,mode,actor,width,language):
    from tenant_web_assets import register_assets
    from test_task_read_save_flow_postgres import _task_apis
    import tenant_crm_native_api as contact_api
    f=company_deal_lists
    assert (ROOT/'.support-test-deps/node_modules/playwright').exists()
    if mode=='ready':
        with f['factory']() as conn:
            for index in range(26):
                conn.execute("INSERT INTO saas_crm_native_companies(id,tenant_id,name,updated_at) VALUES(%s::uuid,%s::uuid,%s,'2020-01-01')",(ident('browser-company-'+str(index)),A,'Browser scroll company %02d'%index))
                conn.execute("INSERT INTO saas_crm_native_deals(id,tenant_id,title,pipeline_id,stage_id,updated_at) VALUES(%s::uuid,%s::uuid,%s,'10','101','2020-01-01')",(ident('browser-deal-'+str(index)),A,'Browser scroll native deal %02d'%index))
    tokens={'initial':f['session'].issue(A,actor),'company_b':f['session'].issue(B,188),'alternate':f['session'].issue(A,181)}
    before=_state(f)
    from test_contacts_directory_postgres import _observe_sql
    ddl_attempts=[]
    sql_counts={'reader':0,'history':0}
    request_trace=contextvars.ContextVar('isolated_company_deal_request',default=None)
    request_sql={}
    for module_name,key in (('tenant_crm_list_states','reader'),('tenant_chat_history_store','history')):
        def observe(sql,_cursor,key=key):
            sql_counts[key]+=1
            trace_id=request_trace.get()
            if trace_id is not None:
                trace=request_sql.setdefault(trace_id,{'statements':0})
                trace.update(statements=trace['statements']+1,last_sql=sql[:240],backend_pid=_cursor.connection.info.backend_pid)
            if re.match(r'\s*(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE)\b',sql,re.I):
                ddl_attempts.append((key,sql))
                raise AssertionError('New list/read/history attempted runtime DDL: '+sql)
        monkeypatch.setattr(importlib.import_module(module_name),'_connect',_observe_sql(f['factory'],before=observe))
    state={'hold':None,'pending':0,'released':0,'held_paths':[],'held_ids':[],'fault':None,'changed':False,'reads':[],
           'request_lifecycle':[],'request_sql':request_sql}
    sock=socket.socket();sock.bind(('127.0.0.1',0))
    origin='http://127.0.0.1:'+str(sock.getsockname()[1])
    loop=asyncio.new_event_loop();ready=threading.Event()
    paths={'company':'/api/platform/crm/native/companies','native':'/api/platform/crm/native/deals','saved':'/api/platform/crm/deals'}
    async def start():
        state['release']=asyncio.Event()
        app=_app(f,origin)
        def trace_response(response,entry):
            if entry is None:return response
            response.headers['X-Isolated-CRM-Trace']=entry['id']
            original=response.write_eof
            async def observed_eof(*args,**kwargs):
                try:result=await original(*args,**kwargs)
                except BaseException as error:
                    entry['eof_error']=type(error).__name__
                    raise
                entry['write_eof']=time.monotonic()
                return result
            response.write_eof=observed_eof
            return response
        @web.middleware
        async def transport_boundary(request,handler):
            entry=None
            if request.path.startswith('/api/platform/crm/'):
                entry={'id':str(len(state['request_lifecycle'])+1),'path':request.path_qs,'started':time.monotonic()}
                state['request_lifecycle'].append(entry)
            token=request_trace.set(entry['id'] if entry else None)
            try:response=await handler(request)
            except BaseException as error:
                if entry is not None:entry['handler_error']=type(error).__name__
                raise
            finally:request_trace.reset(token)
            if entry is not None:entry.update(handler_done=time.monotonic(),status=response.status)
            trace_response(response,entry)
            resource=next((key for key,path in paths.items() if request.path==path),None)
            exact=request.path.startswith(paths['company']+'/') or request.path.startswith(paths['native']+'/') or request.path=='/api/platform/crm/deal'
            if resource or exact or request.path in ('/api/platform/crm/chat','/api/platform/me'):
                state['reads'].append(request.path_qs)
                selected='profile' if request.path=='/api/platform/me' else 'card' if exact else resource
                if state['hold'] is not None and state['hold']==selected:
                    # Hold every profile response in the controlled initial
                    # loading window; all retain the actual handler body.
                    if selected!='profile':state['hold']=None
                    held_id=uuid.uuid4().hex
                    streamed=web.StreamResponse(status=response.status,headers={**{key:value for key,value in response.headers.items() if key.lower()!='content-length'},'X-Isolated-CRM-Held':held_id})
                    trace_response(streamed,entry)
                    await streamed.prepare(request)
                    state['pending']+=1;state['held_paths'].append(request.path_qs);state['held_ids'].append(held_id)
                    await state['release'].wait()
                    try:
                        await streamed.write(response.body);await streamed.write_eof()
                    except ConnectionResetError: state['cancelled']=True
                    state['released']+=1
                    return streamed
                if state['fault']==resource and resource:
                    # Only this transport response fails, after genuine server
                    # membership, policy and PostgreSQL projection executed.
                    return web.json_response({'success':False,'error':'crm_unavailable'},status=503)
            return response
        app.middlewares.insert(0,transport_boundary)
        auth,_employee=_apis(f,origin)
        app.router.add_get('/api/platform/account',auth['account'])
        app.router.add_get('/api/platform/sessions',auth['sessions'])
        tasks,native_task=_task_apis(f,origin)
        app.router.add_get('/api/platform/crm/tasks',tasks['tasks'])
        app.router.add_get('/api/platform/crm/tasks/options',tasks['options'])
        app.router.add_get('/api/platform/crm/task-approvals',tasks['approvals'])
        import tenant_deal_completion_api as deal_completion_api
        app.router.add_get('/api/platform/crm/deal-approvals',deal_completion_api.handler(f['session'].profile,origin,f['provider'],LOGGER,review=True))
        app.router.add_get('/api/platform/crm/native/tasks',native_task)
        import tenant_crm_sync_policy_api as sync_policy_api
        app.router.add_get('/api/platform/crm/external-sync',sync_policy_api.handler(f['session'].profile,origin,LOGGER))
        contact=contact_api.handler(f['session'].profile,origin,LOGGER)
        for suffix in ('','/{id}'):app.router.add_get('/api/platform/crm/native/contacts'+suffix,contact)
        async def index(_request):return web.FileResponse(ROOT/'docs/tenant-app.html')
        app.router.add_get('/app',index);register_assets(app)
        async def control(request):
            command=request.match_info['command']
            if command=='session':
                token=tokens.get(request.query.get('identity','initial'))
                if not token:raise web.HTTPBadRequest()
                response=web.json_response({'success':True})
                response.set_cookie(COOKIE_NAME,token,httponly=True,samesite='Lax',path='/')
                return response
            if command=='hold':state['hold']=request.query['resource'];state['release'].clear()
            elif command=='release':
                if state['hold']=='profile':state['hold']=None
                state['release'].set()
            elif command=='fault':state['fault']=request.query['resource']
            elif command=='restore':state['fault']=None
            elif command=='empty':
                assert mode in ('company_states','deal_states')
                resource=request.query['resource']
                table='saas_crm_native_companies' if resource=='company' else 'saas_crm_native_deals' if resource=='native' else 'saas_crm_deals'
                column='deleted_at' if resource=='saved' else 'archived_at'
                with f['factory']() as conn:
                    conn.execute(psycopg.sql.SQL('UPDATE {} SET {}=now() WHERE tenant_id=%s::uuid').format(psycopg.sql.Identifier(table),psycopg.sql.Identifier(column)),(A,))
                state['changed']=True
            elif command=='workflow-revoke':
                assert mode=='workflow_revoke'
                import tenant_workflow_store as workflows
                workflows.save_tenant_workflow_config(tenant_id=A,owner_id=11,
                    pipelines=[{'pipeline_id':'10','name':'Pipeline 10','owner_telegram_id':11}],
                    stages=[{'pipeline_id':'10','stage_id':'101','name':'Stage 101','settings':{'visible':False}}])
                state['changed']=True
            elif command=='module-revoke':
                assert mode=='workflow_revoke'
                with f['factory']() as conn:
                    conn.execute("UPDATE saas_tenants SET modules='{\"customers\":false,\"deals\":false,\"tasks\":false}' WHERE id=%s::uuid",(A,))
                state['changed']=True
            elif command!='state':raise web.HTTPNotFound()
            return web.json_response({key:value for key,value in state.items() if key not in {'runner','release'}})
        app.router.add_get('/test/{command}',control)
        async def unrelated(request):
            assert not request.path.startswith('/api/platform/crm/'),request.path
            if request.path=='/api/platform/ai/dialogs':
                return web.json_response({'success':True,'result':{'dialogs':[],'channels':[],'agents':[],'next_offset':None,'total_count':0,'unread_count':0}})
            if request.path=='/api/platform/ai/support-tickets':
                return web.json_response({'success':True,'result':{'members':[],'channels':[],'tickets':[],'next_offset':None,'unresolved_count':0}})
            return web.json_response({'success':True,'result':{},'tasks':[],'deals':[],'contacts':[],'companies':[],'pipelines':[],'members':[],'integrations':[],'notifications':[],'unread_count':0,'requests':[]})
        app.router.add_get('/api/{tail:.*}',unrelated)
        runner=web.AppRunner(app);await runner.setup();await web.SockSite(runner,sock).start()
        state['runner']=runner;ready.set()
    def serve():
        asyncio.set_event_loop(loop);loop.run_until_complete(start());loop.run_forever()
    thread=threading.Thread(target=serve,daemon=True);thread.start()
    assert ready.wait(10),'Isolated company/deal HTTP server did not start'
    try:
        env={**os.environ,'CRM_LIST_RUNTIME_ORIGIN':origin,'CRM_LIST_RUNTIME_MODE':mode,
             'CRM_LIST_RUNTIME_ACTOR':str(actor),'CRM_LIST_RUNTIME_WIDTH':str(width),'CRM_LIST_RUNTIME_LANGUAGE':language,
             'CRM_LIST_RUNTIME_TENANT_A':A,'CRM_LIST_RUNTIME_TENANT_B':B,
             'CRM_LIST_RUNTIME_COMPANY_ID':COMPANIES[0],'CRM_LIST_RUNTIME_DEAL_ID':DEALS[0]}
        result=subprocess.run(['node',str(ROOT/'tests/company_deal_list_states_http_browser.cjs')],cwd=ROOT,env=env,capture_output=True,text=True,timeout=85)
        assert result.returncode==0,result.stdout+'\n'+result.stderr
        assert f['provider'].calls==[] and f['blocked']==[]
        assert ddl_attempts==[] and sql_counts['reader']>0,ddl_attempts
        if mode=='ready' and actor!=184 or mode=='tenant_late':assert sql_counts['history']>0,sql_counts
        after=_state(f)
        changed={key for key in after if before[key]!=after[key]}
        allowed=({'saas_crm_native_companies'} if mode=='company_states' else
                 {'saas_crm_native_deals','saas_crm_deals'} if mode=='deal_states' else
                 {'saas_tenant_pipeline_stages','saas_tenant_pipelines','saas_tenants','saas_tenant_audit_events'} if mode=='workflow_revoke' else set())
        assert changed==allowed,changed
        if mode=='workflow_revoke':
            prior=set(before['saas_tenant_audit_events']);current_events=set(after['saas_tenant_audit_events'])
            assert prior<=current_events
            created=[json.loads(row) for row in current_events-prior]
            assert len(created)==1
            event=created[0]
            assert (event['tenant_id'],event['actor_telegram_id'],event['action'],event['entity_type'])==(A,11,'workflow_updated','workflow')
            assert event['payload']=={'pipelines':1,'stages':1,'policy_keys':[]}
    finally:
        loop.call_soon_threadsafe(state['release'].set)
        asyncio.run_coroutine_threadsafe(state['runner'].cleanup(),loop).result(10)
        loop.call_soon_threadsafe(loop.stop);thread.join(10);loop.close()
        assert not thread.is_alive()


def test_new_read_intent_is_strict_and_never_ignored(company_deal_lists):
    f=company_deal_lists
    cookie=f['session'].issue(A,11)
    before=_state(f)
    invalid=[
        ('/api/platform/crm/native/companies/'+COMPANIES[0],[('listing','1')]),
        ('/api/platform/crm/native/companies',[('read','1')]),
        ('/api/platform/crm/native/deals/'+DEALS[0],[('listing','1')]),
        ('/api/platform/crm/deals',[('read','1')]),
        ('/api/platform/crm/deal',[('listing','1'),('lead_id','101')]),
        ('/api/platform/crm/chat',[('listing','1'),('lead_id','101')]),
        ('/api/platform/crm/chat',[('read','1'),('lead_id','101'),('lead_id','202')]),
        ('/api/platform/crm/deals',[('listing','1'),('listing','1')]),
        ('/api/platform/crm/deals',[('listing','1'),('refresh','0')]),
        ('/api/platform/crm/native/deals',[('listing','1'),('full','0')]),
        ('/api/platform/crm/native/companies',[('listing','1'),('options','1')]),
        ('/api/platform/crm/native/deals',[('listing','1'),('options','1')]),
        ('/api/platform/crm/native/deals/'+DEALS[0],[('read','1'),('options','1')]),
        ('/api/platform/crm/deals',[('listing','1'),('expected_tenant_id',A)]),
        ('/api/platform/crm/deals',[('listing','1'),('expected_tenant_id',A),('expected_user_id','11'),('expected_user_id','11')]),
        ('/api/platform/crm/deals',[('listing','1'),('expected_tenant_id',A),('expected_user_id','-11')]),
        ('/api/platform/crm/deals',[('listing','1'),('lead_id','101')]),
        ('/api/platform/crm/native/companies/'+COMPANIES[0],[('read','1'),('lead_id','202')]),
        ('/api/platform/crm/native/deals',[('listing','1'),('lead_id','101')]),
        ('/api/platform/crm/native/deals',[('listing','1'),('limit','51')]),
        ('/api/platform/crm/native/companies',[('listing','1'),('offset','1')]),
        ('/api/platform/crm/deals',[('listing','1'),('limit','101')]),
        ('/api/platform/crm/deals',[('listing','1'),('offset','-1')]),
    ]
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            for path,query in invalid:
                status,result=await _get(http,cookie,path,query)
                assert status==400 and result['error']=='crm_invalid_query',(path,query,status,result)
                assert not any(key in result for key in ('companies','company','deals','deal','messages','total'))
            for path,query in (
                ('/api/platform/crm/native/companies',{'listing':'1'}),
                ('/api/platform/crm/native/deals',{'listing':'1'}),
                ('/api/platform/crm/deals',{'listing':'1'}),
                ('/api/platform/crm/deal',{'read':'1','lead_id':'101'}),
                ('/api/platform/crm/chat',{'read':'1','lead_id':'101'}),
            ):
                status,result=await _get(http,cookie,path,{**query,**_identity(B,11)})
                assert status==409 and result['error']=='crm_context_changed',result
            status,options=await _get(http,cookie,'/api/platform/crm/native/deals',{'options':'1',**_identity()})
            assert status==200 and {'workflows','members','contacts'}<=set(options),options
            assert any(row['pipeline_id']=='p-historical' and row['stage_id']=='s-new' for row in options['workflows'])
            assert {row['id'] for row in options['contacts']}=={CONTACTS[0]}
            status,changed=await _get(http,cookie,'/api/platform/crm/native/deals',{'options':'1',**_identity(B,11)})
            assert status==409 and not any(key in changed for key in ('workflows','members','contacts')),changed
    asyncio.run(scenario())
    assert _state(f)==before and f['provider'].calls==[]


def test_cookie_absent_fails_before_even_malformed_new_intent(company_deal_lists):
    f=company_deal_lists
    before=_state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            for path in ('/api/platform/crm/native/companies','/api/platform/crm/native/deals','/api/platform/crm/deals',
                         '/api/platform/crm/native/companies/'+COMPANIES[0],'/api/platform/crm/native/deals/'+DEALS[0],
                         '/api/platform/crm/deal','/api/platform/crm/chat'):
                status,result=await _get(http,None,path,[('listing','bad'),('read','bad'),('expected_tenant_id','bad'),('lead_id','bad')])
                assert status==403 and not any(key in result for key in ('companies','deals','company','deal','messages','total')),result
    asyncio.run(scenario())
    assert _state(f)==before and f['provider'].calls==[]


def test_current_explicit_native_view_survives_coarse_module_off_without_external_grant(company_deal_lists):
    f=company_deal_lists
    with f['factory']() as conn:
        conn.execute("UPDATE saas_tenant_members SET permissions='[\"customers:view\",\"deals:view\"]' WHERE tenant_id=%s::uuid AND telegram_id=183",(A,))
        conn.execute("UPDATE saas_tenants SET modules='{}' WHERE id=%s::uuid",(A,))
    cookie=f['session'].issue(A,183)
    before=_state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            for entity,source,path,field,total,_cap in COLLECTIONS:
                status,page=await _listing(http,cookie,path,actor=183)
                if source=='native':
                    assert status==200 and page['total']==total and page['scope']=='tenant',page
                    assert all(page['actions'][key] is False for key in ('create','update','archive'))
                else:assert status==403 and 'deals' not in page,page
    asyncio.run(scenario())
    assert _state(f)==before


def test_native_uuid_foreign_reads_and_saved_colliding_ids_stay_current_tenant(company_deal_lists):
    f=company_deal_lists
    cookie=f['session'].issue(B,188)
    before=_state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            for path in ('/api/platform/crm/native/companies/'+COMPANIES[0],'/api/platform/crm/native/deals/'+DEALS[0]):
                status,page=await _get(http,cookie,path,{'read':'1',**_identity(B,188)})
                assert status==404 and page['error']=='crm_not_found',page
            for path,field in (('/api/platform/crm/deal','deal'),('/api/platform/crm/chat','messages')):
                status,page=await _get(http,cookie,path,{'read':'1','lead_id':'101',**_identity(B,188)})
                assert status==200 and 'B PRIVATE' in json.dumps(page[field]) and 'A saved' not in json.dumps(page[field]),page
                status,changed=await _get(http,cookie,path,{'read':'1','lead_id':'101',**_identity(A,188)})
                assert status==409 and field not in changed,changed
    asyncio.run(scenario())
    assert _state(f)==before


def test_count_and_rows_are_one_real_snapshot_despite_actual_concurrent_insert(company_deal_lists,monkeypatch):
    import tenant_crm_list_states as reader
    from test_contacts_directory_postgres import _observe_sql
    f=company_deal_lists
    cookie=f['session'].issue(A,11)
    inserted=[]
    def after(sql,_cursor):
        if 'SELECT count(*) AS total' in sql and 'saas_crm_native_companies' in sql and not inserted:
            inserted.append(companies.create_company(A,'Inserted after actual COUNT',company_id=ident('count-race-company')))
    monkeypatch.setattr(reader,'_connect',_observe_sql(f['factory'],after=after))
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status,page=await _listing(http,cookie,COLLECTIONS[0][2])
            assert status==200 and len(inserted)==1,page
            assert page['total']==page['shown']==len(page['companies'])==2,page
            assert 'Inserted after' not in json.dumps(page)
            status,fresh=await _listing(http,cookie,COLLECTIONS[0][2])
            assert status==200 and fresh['total']==fresh['shown']==3,fresh
    asyncio.run(scenario())
    assert f['provider'].calls==[]


def test_saved_scope_cannot_mix_old_owner_with_new_stage_visibility(company_deal_lists,monkeypatch):
    import tenant_crm_list_states as reader
    import tenant_workflow_store as workflows
    from test_contacts_directory_postgres import _observe_sql
    f=company_deal_lists
    cookie=f['session'].issue(A,182)
    assert TenantPolicy(members.member(A,182)).pipeline_scope()==[{'pipeline_id':10,'status_ids':[101]}]
    changed=[]
    def after(sql,_cursor):
        if 'FROM saas_tenant_pipelines WHERE' in sql and not changed:
            changed.append(True)
            workflows.save_tenant_workflow_config(tenant_id=A,owner_id=11,
                pipelines=[{'pipeline_id':'10','name':'Pipeline 10','owner_telegram_id':11}],
                stages=[{'pipeline_id':'10','stage_id':'102','name':'Now visible','settings':{'visible':True}}])
            assert TenantPolicy(members.member(A,182)).pipeline_scope()==[]
    monkeypatch.setattr(reader,'_connect',_observe_sql(f['factory'],after=after))
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status,page=await _listing(http,cookie,COLLECTIONS[2][2],actor=182)
            assert status==200 and changed==[True],page
            assert {row['id'] for row in page['deals']}=={'101','202',BIG_LEAD_ID}
            assert page['total']==page['shown']==3 and 'HIDDEN' not in json.dumps(page),page
    asyncio.run(scenario())
    assert f['provider'].calls==[]


@pytest.mark.parametrize('revision',['member','tenant'])
def test_actual_share_wait_retries_once_then_obeys_committed_current_denial(company_deal_lists,monkeypatch,revision):
    import tenant_crm_list_states as reader
    f=company_deal_lists
    cookie=f['session'].issue(A,181)
    pids=[]
    def factory():
        conn=f['factory']();pids.append(conn.info.backend_pid);return conn
    monkeypatch.setattr(reader,'_connect',factory)
    holder=f['factory']()
    if revision=='member':holder.execute('SELECT 1 FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=181 FOR UPDATE',(A,))
    else:holder.execute('SELECT 1 FROM saas_tenants WHERE id=%s::uuid FOR UPDATE',(A,))
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            request=asyncio.create_task(_listing(http,cookie,COLLECTIONS[0][2],actor=181))
            try:
                deadline=time.monotonic()+8;blocked=False
                while time.monotonic()<deadline:
                    if pids:
                        with f['factory']() as observer:
                            blocked=holder.info.backend_pid in observer.execute('SELECT pg_blocking_pids(%s) AS blockers',(pids[0],)).fetchone()['blockers']
                    if blocked:break
                    await asyncio.sleep(.02)
                assert blocked,'Actual RR read must wait on the current authority SHARE fence'
                if revision=='member':holder.execute("UPDATE saas_tenant_members SET permissions='[]' WHERE tenant_id=%s::uuid AND telegram_id=181",(A,))
                else:holder.execute("UPDATE saas_tenants SET modules='{}' WHERE id=%s::uuid",(A,))
                holder.commit()
                status,page=await asyncio.wait_for(request,8)
                assert status==403 and page['error']=='crm_forbidden' and 'companies' not in page and 'total' not in page,page
                assert len(pids)==2 and len(set(pids))==2,'One actual SerializationFailure retries the entire read on one fresh connection'
            finally:
                holder.rollback()
                if not request.done():request.cancel()
    try:asyncio.run(scenario())
    finally:holder.close()
    assert f['provider'].calls==[]


def test_missing_new_reader_readiness_fails_closed_without_runtime_repair(company_deal_lists,monkeypatch):
    import tenant_crm_list_states as reader
    from test_contacts_directory_postgres import _observe_sql
    f=company_deal_lists
    cookie=f['session'].issue(A,11)
    before=_state(f);observed=[]
    def observe(sql,_cursor):
        observed.append(sql)
        assert not re.match(r'\s*(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE)\b',sql,re.I),sql
    monkeypatch.setattr(reader,'_connect',_observe_sql(f['factory'],before=observe))
    # Catalog absence is synthetic test setup only. The real request must
    # report unavailability and may never repair tables or perform migrations.
    with f['factory']() as conn:conn.execute('ALTER TABLE saas_crm_messages RENAME TO isolated_missing_crm_messages')
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            for path,query in ((COLLECTIONS[0][2],{'listing':'1'}),('/api/platform/crm/chat',{'read':'1','lead_id':'101'})):
                status,page=await _get(http,cookie,path,{**query,**_identity()})
                assert status==503 and page['error']=='crm_unavailable' and not any(key in page for key in ('companies','messages','total')),page
    try:asyncio.run(scenario())
    finally:
        with f['factory']() as conn:conn.execute('ALTER TABLE isolated_missing_crm_messages RENAME TO saas_crm_messages')
    assert observed and any('to_regclass' in sql for sql in observed)
    assert _state(f)==before and f['provider'].calls==[]


def test_initialized_new_reader_never_bootstraps_on_cold_process_schema_flags(company_deal_lists,monkeypatch):
    import tenant_crm_list_states as reader
    import tenant_platform_schema as platform_schema
    from test_contacts_directory_postgres import _observe_sql
    f=company_deal_lists
    # Resolve the real current membership before simulating a cold process
    # cache; the reader itself still re-reads and locks actual authority rows.
    profile=members.member(A,11)
    before=_state(f);observed=[]
    def observe(sql,_cursor):
        observed.append(sql)
        assert not re.match(r'\s*(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE)\b',sql,re.I),sql
    monkeypatch.setattr(platform_schema,'_schema_ready',False)
    monkeypatch.setattr(reader,'_connect',_observe_sql(f['factory'],before=observe))
    for entity,source,_path,field,total,_cap in COLLECTIONS:
        page=reader.read(profile,entity=entity,source=source)
        assert page['total']==page['shown']==len(page[field])==total
    assert reader.read(profile,entity='company',identifier=COMPANIES[0])['company']['id']==COMPANIES[0]
    assert reader.read(profile,entity='deal',identifier=DEALS[3])['deal']['pipeline_id']=='p-historical'
    chat=reader.read(profile,entity='deal',source='saved',identifier='101',chat=True)
    assert chat['messages'][0]['body']=='A saved client message'
    assert platform_schema._schema_ready is False,'List/read code cannot change global bootstrap state'
    assert observed and _state(f)==before and f['provider'].calls==[]


def _history_query(actor=11,tenant=A,**extra):
    return {'lead_id':'101',**_identity(tenant,actor),**extra}


def test_cached_history_reads_without_optional_index_and_keeps_cursor_identity(company_deal_lists,monkeypatch):
    import tenant_chat_history_store as history
    from test_contacts_directory_postgres import _observe_sql
    f=company_deal_lists
    crm.upsert_crm_messages(tenant_id=A,kommo_lead_id=101,messages=[
        {'external_id':'history-index-%02d'%index,'direction':'incoming','channel':'kommo','body':'Synthetic history '+str(index),'happened_at':'2099-01-01T00:00:00Z'} for index in range(5)])
    with f['factory']() as conn:conn.execute('DROP INDEX IF EXISTS saas_crm_messages_history_position')
    tokens={'owner':f['session'].issue(A,11),'other':f['session'].issue(A,181),'foreign':f['session'].issue(B,188)}
    before=_state(f);sqls=[]
    def observe(sql,_cursor):
        sqls.append(sql)
        assert not re.match(r'\s*(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE)\b',sql,re.I),sql
    monkeypatch.setattr(history,'_connect',_observe_sql(f['factory'],before=observe))
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            first_cursor=None;cursor='';seen=[]
            while True:
                status,page=await _get(http,tokens['owner'],'/api/platform/crm/chat/history',_history_query(limit='2',cursor=cursor))
                assert status==200 and page['source']=='cache' and len(page['messages'])<=2,page
                assert 'B PRIVATE' not in json.dumps(page)
                seen.extend(row['external_id'] for row in page['messages'])
                if not page['has_more']:
                    assert page['next_cursor'] is None;break
                assert page['next_cursor'] and page['next_cursor']!=cursor
                cursor=page['next_cursor'];first_cursor=first_cursor or cursor
                assert len(seen)<=6
            assert len(seen)==len(set(seen))==6 and first_cursor
            invalid=[
                (tokens['other'],_history_query(actor=181,cursor=first_cursor)),
                (tokens['foreign'],_history_query(actor=188,tenant=B,cursor=first_cursor)),
                (tokens['owner'],_history_query(lead_id='202',cursor=first_cursor)),
                (tokens['owner'],_history_query(cursor='not_a_cursor')),
            ]
            for token,query in invalid:
                status,page=await _get(http,token,'/api/platform/crm/chat/history',query)
                assert status==400 and 'messages' not in page,page
            status,page=await _get(http,tokens['foreign'],'/api/platform/crm/chat/history',_history_query(actor=188))
            assert status==409 and 'messages' not in page,page
            status,page=await _get(http,tokens['owner'],'/api/platform/crm/chat/history',{'lead_id':'101'})
            assert status==409 and 'messages' not in page,page
    asyncio.run(scenario())
    with f['factory']() as conn:assert conn.execute("SELECT to_regclass('saas_crm_messages_history_position') AS index").fetchone()['index'] is None
    assert sqls and all('OFFSET' not in sql for sql in sqls)
    assert _state(f)==before and f['provider'].calls==[]


def test_cached_history_missing_base_table_is_503_without_schema_repair(company_deal_lists,monkeypatch):
    import tenant_chat_history_store as history
    from test_contacts_directory_postgres import _observe_sql
    f=company_deal_lists
    cookie=f['session'].issue(A,11)
    before=_state(f);sqls=[]
    def observe(sql,_cursor):
        sqls.append(sql)
        assert not re.match(r'\s*(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE)\b',sql,re.I),sql
    monkeypatch.setattr(history,'_connect',_observe_sql(f['factory'],before=observe))
    with f['factory']() as conn:conn.execute('ALTER TABLE saas_crm_messages RENAME TO isolated_missing_history_messages')
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status,page=await _get(http,cookie,'/api/platform/crm/chat/history',_history_query())
            assert status==503 and 'messages' not in page and 'total' not in page,page
    try:asyncio.run(scenario())
    finally:
        with f['factory']() as conn:conn.execute('ALTER TABLE isolated_missing_history_messages RENAME TO saas_crm_messages')
    assert sqls and _state(f)==before and f['provider'].calls==[]


@pytest.mark.parametrize('revocation',['member','tenant_status','pipeline'])
def test_cached_history_keeps_existing_current_status_and_scope_guards(company_deal_lists,revocation):
    f=company_deal_lists
    cookie=f['session'].issue(A,182)
    with f['factory']() as conn:
        if revocation=='member':conn.execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s::uuid AND telegram_id=182',(A,))
        elif revocation=='tenant_status':conn.execute("UPDATE saas_tenants SET status='disabled' WHERE id=%s::uuid",(A,))
        else:conn.execute("UPDATE saas_tenant_pipeline_stages SET settings='{\"visible\":false}' WHERE tenant_id=%s::uuid AND pipeline_id='10'",(A,))
    before=_state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status,page=await _get(http,cookie,'/api/platform/crm/chat/history',_history_query(actor=182))
            assert status==403 and 'messages' not in page,page
    asyncio.run(scenario())
    assert _state(f)==before and f['provider'].calls==[]


def test_cached_history_actual_member_wait_retries_fresh_revoke(company_deal_lists,monkeypatch):
    import tenant_chat_history_store as history
    f=company_deal_lists
    cookie=f['session'].issue(A,182)
    pids=[]
    def factory():
        conn=f['factory']();pids.append(conn.info.backend_pid);return conn
    monkeypatch.setattr(history,'_connect',factory)
    holder=f['factory']()
    holder.execute('SELECT 1 FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=182 FOR UPDATE',(A,))
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            request=asyncio.create_task(_get(http,cookie,'/api/platform/crm/chat/history',_history_query(actor=182)))
            try:
                deadline=time.monotonic()+8;blocked=False
                while time.monotonic()<deadline:
                    if pids:
                        with f['factory']() as observer:
                            blocked=holder.info.backend_pid in observer.execute('SELECT pg_blocking_pids(%s) AS blockers',(pids[0],)).fetchone()['blockers']
                    if blocked:break
                    await asyncio.sleep(.02)
                assert blocked,'History must wait at the genuine current membership SHARE fence'
                holder.execute("UPDATE saas_tenant_members SET permissions='[]' WHERE tenant_id=%s::uuid AND telegram_id=182",(A,))
                holder.commit()
                status,page=await asyncio.wait_for(request,8)
                assert status==403 and 'messages' not in page,page
                assert len(pids)==len(set(pids))==2,'Real SerializationFailure retries once on fresh current authority'
            finally:
                holder.rollback()
                if not request.done():request.cancel()
    try:asyncio.run(scenario())
    finally:holder.close()
    assert f['provider'].calls==[]


@pytest.mark.parametrize('raw',[{}, {'provider':'kommo','source':'kommo'}])
def test_saved_connect_provenance_overrides_missing_or_conflicting_raw_without_sync(company_deal_lists,raw):
    f=company_deal_lists
    with f['factory']() as conn:
        conn.execute("UPDATE saas_crm_deals SET channel='connect:synthetic',raw=%s::jsonb WHERE tenant_id=%s::uuid AND kommo_lead_id=202",(json.dumps(raw),A))
    cookie=f['session'].issue(A,11)
    before=_state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status,page=await _get(http,cookie,'/api/platform/crm/deal',{'read':'1','lead_id':'202',**_identity()})
            assert status==200 and page['deal']['id']=='202' and page['deal']['provenance']=='connect',page
            assert not page['deal'].get('conversation_id') and not page['deal'].get('connect_channel_id')
            # This is the existing explicit refresh route with fresh identity;
            # Connect provenance must still read cache without any Kommo call.
            status,chat=await _get(http,cookie,'/api/platform/crm/chat',{'refresh':'1','lead_id':'202',**_identity()})
            assert status==200 and chat['messages']==[] and chat['stale'] is False,chat
    asyncio.run(scenario())
    assert _state(f)==before and f['provider'].calls==[]
