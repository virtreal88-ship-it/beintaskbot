"""Loopback-only real UI/API smoke server with disposable PostgreSQL and mock AI."""
import os,sys,re,logging,asyncio,json,uuid
from pathlib import Path
sys.path[:0]=[str(Path.cwd()),str(Path.cwd()/'tests')]
os.environ['BEINTASKBOT_TEST_POSTGRES_DSN']='host=127.0.0.1 port=55439 user=postgres dbname=postgres'
import pytest,psycopg
from psycopg.rows import dict_row
from aiohttp import web
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db,A,CA,PROFILE
from test_knowledge_candidates import ready
import tenant_knowledge_candidates_api as api
import tenant_ai_agents_api as agents_api
import tenant_operator_api as operator
import tenant_knowledge_campaign_worker as worker
import tenant_knowledge_quality,tenant_knowledge_media_cache
mp=pytest.MonkeyPatch();gen=database.__wrapped__();database_obj=next(gen)
dbgen=db.__wrapped__(database_obj,mp);next(dbgen)
ready.__wrapped__(database_obj,mp)
second=str(uuid.uuid4())
database_obj.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Synthetic support')",(A,second));database_obj.commit()
factory=lambda:psycopg.connect(database_obj.dsn,row_factory=dict_row)
for name,module in list(sys.modules.items()):
    if name.startswith('tenant_') and hasattr(module,'_connect'):mp.setattr(module,'_connect',factory)
profile={**PROFILE,'role':'owner','active':True,'display_name':'Synthetic administrator'}
origin='http://127.0.0.1:8768';logger=logging.getLogger('knowledge-local');app=web.Application()
async def me(request):return web.json_response({'success':True,'member':profile,'capabilities':{'modules':{'ai_agents':True,'dialogs':True,'support':True}}})
app.router.add_get('/api/platform/me',me)
for path,resource,methods in [('', 'collection',('GET','POST')),('/campaigns','campaigns',('GET','POST')),('/campaigns/preview','campaign_preview',('GET',)),('/campaigns/{campaign_id}/start','campaign_start',('POST',)),('/campaigns/{campaign_id}/control','campaign_control',('POST',)),('/{candidate_id}','candidate',('GET','PATCH'))]:
    for method in methods:app.router.add_route(method,'/api/platform/ai/knowledge-candidates'+path,api.handler(lambda _:profile,origin,logger,resource))
app.router.add_get('/api/platform/ai/agents',agents_api.handler(lambda _:profile,origin,logger,'agents'))
app.router.add_get('/api/platform/ai/dialogs',operator.handler(lambda _:profile,origin,logger,'dialogs'))
async def channels(request):return web.json_response({'success':True,'result':{'channels':[{'provider':'connect','channel_id':CA,'name':'Synthetic primary'},{'provider':'connect','channel_id':second,'name':'Synthetic support'}]}})
app.router.add_get('/api/platform/ai/channels',channels)
async def page(request):
    text=(Path.cwd()/'docs/tenant-app.html').read_text(encoding='utf-8')
    keep={'tenant-operator-i18n.js','tenant-knowledge-campaigns.js','tenant-knowledge-candidates.js'}
    text=re.sub(r'<script[^>]*src="([^"]+)"[^>]*>\s*</script>',lambda m:m[0] if m[1].split('/')[-1] in keep else '',text)
    text=text.replace('<title>','<title>LOCAL SYNTHETIC — ')
    text=text.replace('</body>','<script>document.querySelectorAll(".view").forEach(e=>e.classList.remove("active"));document.getElementById("view-support_cases").classList.add("active");document.dispatchEvent(new CustomEvent("tenant-profile",{detail:{member:'+json.dumps(profile)+'}}));</script></body>')
    return web.Response(text=text,content_type='text/html')
app.router.add_get('/app',page);app.router.add_static('/assets/',Path.cwd()/'docs')
async def background(_):app['campaign_task']=asyncio.create_task(worker.run(logger))
async def cleanup(_):
    app['campaign_task'].cancel()
    try:await app['campaign_task']
    except asyncio.CancelledError:pass
    mp.undo();next(dbgen,None);next(gen,None)
app.on_startup.append(background);app.on_cleanup.append(cleanup)
web.run_app(app,host='127.0.0.1',port=8768,access_log=None)
