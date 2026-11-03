"""Synthetic local HTTP + PostgreSQL fixture; requires the loopback test guard."""
import json
import logging
import os
from pathlib import Path
from aiohttp import web
from test_customer_memory_postgres import CustomerMemoryPostgresTests
from tenant_customer_memory_api import handler
from tenant_operator_api import handler as operator_handler

if not os.environ.get('CUSTOMER_MEMORY_TEST_DSN'):
    raise RuntimeError('Explicit isolated PostgreSQL fixture required')
CustomerMemoryPostgresTests.setUpClass()
fixture=CustomerMemoryPostgresTests(methodName='runTest');fixture.setUp()
link=fixture.link();fixture.fact(link)
origin='http://127.0.0.1:18765'
app=web.Application()
app.router.add_route('*','/api/platform/customer-memory/{contact_id}',handler(lambda _:fixture.profile,origin,logging.getLogger('fixture')))
app.router.add_get('/api/platform/ai/dialogs',operator_handler(lambda _:fixture.profile,origin,logging.getLogger('fixture'),'dialogs'))
app.router.add_get('/api/platform/ai/dialogs/history',operator_handler(lambda _:fixture.profile,origin,logging.getLogger('fixture'),'history'))
async def page(request):
    html='''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><div id="memory" hidden></div><script src="/memory.js"></script><script>
    const scope=SCOPE; const host=document.getElementById('memory');
    async function api(path,options={}) { const u=new URL(path,location.origin);u.searchParams.set('expected_tenant_id',scope.tenant);u.searchParams.set('expected_user_id',scope.user);const r=await fetch(u,{credentials:'same-origin',...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});const d=await r.json();if(!r.ok){const e=Error('failed');e.status=r.status;throw e}return d; }
    window.tenantI18n={language:'ru'};window.tenantCustomerMemory.mount(host,{contact:CONTACT,api,current:()=>true,scope,owner:true,canEdit:true});
    </script>'''.replace('SCOPE',json.dumps({'tenant':fixture.tenant,'user':'71'})).replace('CONTACT',json.dumps(fixture.contact))
    return web.Response(text=html,content_type='text/html')
async def js(request):return web.FileResponse(Path(__file__).resolve().parent.parent/'docs/tenant-customer-memory.js')
app.router.add_get('/',page);app.router.add_get('/memory.js',js)
web.run_app(app,host='127.0.0.1',port=18765,print=None)
