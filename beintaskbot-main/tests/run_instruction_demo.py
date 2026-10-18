"""Local isolated PostgreSQL/WASM demo. Never connects to production.
Usage: python tests/run_instruction_demo.py --sample PATH_TO_PILOT_DIRECTORY
"""
import sys,json,uuid,argparse,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).parent))
from test_tenant_support_inbox import Database
from aiohttp import web
import tenant_instruction_library as lib
from tenant_instruction_library_api import handler
from tenant_web_assets import register_assets
import logging
p=argparse.ArgumentParser();p.add_argument('--sample',type=Path,required=True);args=p.parse_args()
db=Database();lib.schema(db);tenant=str(uuid.uuid4());agent=str(uuid.uuid4());profile={'tenant_id':tenant,'telegram_id':11}
db.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s::uuid,%s,'Library test','active',11)",(tenant,tenant))
db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,11,'owner','Test editor')",(tenant,))
db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by) VALUES(%s::uuid,%s::uuid,'Test agent',11)",(tenant,agent));db.commit()
lock=threading.RLock()
class Connection:
 def __enter__(self):lock.acquire();return db
 def __exit__(self,*exc):
  try:return db.__exit__(*exc)
  finally:lock.release()
lib._connect=lambda:Connection()
v=json.loads((args.sample/'warehouse-transfer.lesson.json').read_text(encoding='utf-8'));v['subtitles']=json.loads((args.sample/'warehouse-transfer.subtitles.json').read_text(encoding='utf-8'))
for slide in v['slides']:
 im=slide['image'];body=(args.sample/'visual-az'/im['asset_id']).read_bytes();asset=str(uuid.uuid4());db.execute('INSERT INTO saas_instruction_assets VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s)',(tenant,asset,body,im['width'],im['height'],im['sha256']));im['asset_id']=asset+'.jpg'
db.commit();lib.operation(profile,'save',{'id':str(uuid.uuid4()),'expected_version':0,'doc':v})
app=web.Application();register_assets(app);origin='http://127.0.0.1:8890'
async def me(_):return web.json_response({'member':profile})
async def agents(_):return web.json_response({'success':True,'result':[{'id':agent,'name':'Test agent'}]})
app.router.add_get('/api/platform/me',me);app.router.add_get('/api/platform/ai/agents',agents)
for path,res in [('/api/platform/instructions','collection'),('/api/platform/instructions/{lesson_id}','collection'),('/api/platform/instructions/{lesson_id}/assets/{asset_id}','collection'),('/api/instructions/shared/{token}','public'),('/api/instructions/shared/{token}/assets/{asset_id}','public')]:
 app.router.add_route('*',path,handler(lambda _:profile,origin,logging.getLogger('demo'),res))
web.run_app(app,host='127.0.0.1',port=8890,access_log=None)
