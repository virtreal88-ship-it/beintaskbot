"""Isolated PostgreSQL lifecycle/isolation tests; no external provider or production."""
import json,uuid
import pytest
from test_tenant_support_inbox import database,db,A,B,AGENT,PROFILE,seed_job
import tenant_instruction_library as lib
from tenant_linear_policy import TenantLinearError

def uid():return str(uuid.uuid4())
def doc(asset):
 return {'schema_version':1,'title':'Test lesson','operation':'inventory.warehouse_transfer','language':'az','review_status':'approved','slides':[{'id':'s1','order':1,'title':'Step','explanation':'Read only','note':'No save','image':{'asset_id':asset+'.jpg','width':100,'height':100,'sha256':'a'*64},'areas':[{'id':'a','x':0,'y':0,'width':10,'height':10,'intent':'focus'}],'playback':{'duration_ms':6000,'transition_ms':650,'focus':[{'at_ms':1400,'area_id':'a','padding':50}],'return_to_overview':True}}],'subtitles':{'slides':[[{'at_ms':0,'text':'Read only'}]]}}
@pytest.fixture
def ready(db,monkeypatch):
 monkeypatch.setattr(lib,'_connect',lambda:db);lib.schema(db);seed_job(db);asset=uid();db.execute('INSERT INTO saas_instruction_assets VALUES(%s::uuid,%s::uuid,%s,100,100,%s)',(A,asset,b'jpeg','a'*64));db.commit();return db,asset

def save(ready):
 db,asset=ready;return lib.operation(PROFILE,'save',{'id':uid(),'expected_version':0,'doc':doc(asset)})
def mutate(row,action,**kw):return lib.operation(PROFILE,action,{'expected_version':row['version'],**kw},row['id'])

def publish_ready(ready):
 row=save(ready)
 row=mutate(row,'submit')
 row=mutate(row,'approve',review_confirmed=True)
 return mutate(row,'publish',agent_ids=[AGENT])


def test_employee_published_catalog_is_read_only_and_tenant_scoped(ready):
 db,asset=ready
 published=publish_ready(ready)
 draft=save(ready)
 db.execute("UPDATE saas_tenant_members SET role='worker' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,))
 db.commit()
 employee_a={'tenant_id':A,'telegram_id':11}
 employee_b={'tenant_id':B,'telegram_id':22}

 page=lib.published_list(employee_a,{'q':'warehouse','offset':0})
 assert [item['id'] for item in page['items']]==[published['id']]
 item=page['items'][0]
 assert item['operation']=='inventory.warehouse_transfer'
 assert not {'state','doc','published_doc','public_hash','tenant_id'} & set(item)
 assert lib.published_read(employee_a,published['id'])['doc']['title']=='Test lesson'
 assert lib.published_asset(employee_a,published['id'],asset+'.jpg')==b'jpeg'
 assert lib.published_list(employee_a,{'q':'missing'})['items']==[]

 # The active employee in another company cannot enumerate or fetch this lesson or image.
 assert lib.published_list(employee_b)['items']==[]
 with pytest.raises(TenantLinearError) as error:
  lib.published_read(employee_b,published['id'])
 assert error.value.status==404
 with pytest.raises(TenantLinearError) as error:
  lib.published_asset(employee_b,published['id'],asset+'.jpg')
 assert error.value.status==404

 # The new read path does not grant any of the existing editing operations.
 with pytest.raises(TenantLinearError) as error:
  lib.operation(employee_a,'list')
 assert error.value.status==403
 with pytest.raises(TenantLinearError) as error:
  lib.operation(employee_a,'read',lesson_id=published['id'])
 assert error.value.status==403
 assert draft['state']=='draft'

 db.execute("UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=11",(A,))
 db.commit()
 with pytest.raises(TenantLinearError) as error:
  lib.published_list(employee_a)
 assert error.value.status==403


def test_employee_catalog_hides_unpublished_or_revoked_snapshots(ready):
 db,asset=ready
 published=publish_ready(ready)
 employee={'tenant_id':A,'telegram_id':11}
 assert [item['id'] for item in lib.published_list(employee)['items']]==[published['id']]

 # Unpublishing removes both the item and its screenshot from member read access.
 row=lib.operation(PROFILE,'read',lesson_id=published['id'])
 row=mutate(row,'unpublish')
 assert lib.published_list(employee)['items']==[]
 for read in (lambda:lib.published_read(employee,published['id']),
              lambda:lib.published_asset(employee,published['id'],asset+'.jpg')):
  with pytest.raises(TenantLinearError) as error:
   read()
  assert error.value.status==404

 # Editing a published lesson returns it to draft; its historical snapshot is not listed.
 republished=publish_ready(ready)
 edited=lib.operation(PROFILE,'save',{'expected_version':republished['version'],'doc':republished['doc']},republished['id'])
 assert edited['state']=='draft'
 assert lib.published_list(employee)['items']==[]


def test_published_catalog_http_requires_identity_and_is_get_only(monkeypatch):
 import asyncio,logging
 from aiohttp import web
 from aiohttp.test_utils import TestServer,TestClient
 from tenant_instruction_library_api import handler
 from unittest.mock import Mock
 profile={'tenant_id':A,'telegram_id':11,'role':'worker'}
 service=Mock(return_value={'items':[],'next_offset':None})
 monkeypatch.setattr(lib,'published_list',service)
 async def check():
  app=web.Application()
  app.router.add_get('/catalog',handler(lambda _request:profile,'https://app.test',logging.getLogger('test'),resource='catalog'))
  async with TestClient(TestServer(app)) as client:
   response=await client.get('/catalog')
   assert response.status==409
   service.assert_not_called()
   response=await client.get('/catalog',params={'expected_tenant_id':A,'expected_user_id':'11'})
   assert response.status==200
   assert (await response.json())['result']['items']==[]
   service.assert_called_once()
   response=await client.post('/catalog',json={'expected_tenant_id':A,'expected_user_id':'11'})
   assert response.status==405
 asyncio.run(check())

def test_complete_workflow_knowledge_public_revocation(ready):
 db,asset=ready;r=save(ready)
 assert r['state']=='draft' and r['doc']['review_status']=='pending_review'
 assert lib.operation(PROFILE,'list')['items'][0]['id']==r['id']
 with pytest.raises(TenantLinearError):mutate(r,'publish',agent_ids=[AGENT])
 r=mutate(r,'submit')
 with pytest.raises(TenantLinearError):mutate(r,'approve')
 r=mutate(r,'approve',review_confirmed=True);r=mutate(r,'publish',agent_ids=[AGENT]);assert r['state']=='published'
 sources=db.execute('SELECT agent_id,active,provenance FROM saas_ai_knowledge_sources');assert len(sources)==1 and sources[0]['active'] and str(sources[0]['agent_id'])==AGENT;db.commit()
 token=mutate(r,'share')['token'];assert lib.shared(token)['doc']['title']=='Test lesson';assert lib.shared(token,asset+'.jpg')==b'jpeg'
 with pytest.raises(TenantLinearError):lib.shared(token,uid()+'.jpg')
 r=lib.operation(PROFILE,'read',lesson_id=r['id']);r=mutate(r,'revoke')
 with pytest.raises(TenantLinearError):lib.shared(token)
 assert lib.private_asset(PROFILE,r['id'],asset+'.jpg')==b'jpeg'

def test_tenants_roles_archive_and_asset_binding(ready):
 db,asset=ready;r=save(ready)
 for action in ('read','save','approve'):
  with pytest.raises(TenantLinearError):lib.operation({'tenant_id':B,'telegram_id':22},action,{'expected_version':1,'doc':doc(asset)},r['id'])
 with pytest.raises(TenantLinearError):lib.private_asset({'tenant_id':B,'telegram_id':22},r['id'],asset+'.jpg')
 db.execute("UPDATE saas_tenant_members SET role='worker' WHERE tenant_id=%s::uuid",(A,));db.commit()
 with pytest.raises(TenantLinearError):lib.operation(PROFILE,'list')
 db.execute("UPDATE saas_tenant_members SET role='owner' WHERE tenant_id=%s::uuid",(A,));db.execute("UPDATE saas_tenants SET status='archived' WHERE id=%s::uuid",(A,));db.commit()
 with pytest.raises(TenantLinearError):lib.operation(PROFILE,'read',lesson_id=r['id'])

def test_stale_edit_no_duplicate_publish_and_unpublish_on_edit(ready):
 db,asset=ready;r=save(ready);old=r
 r=lib.operation(PROFILE,'save',{'expected_version':r['version'],'doc':r['doc']},r['id'])
 with pytest.raises(TenantLinearError):lib.operation(PROFILE,'save',{'expected_version':old['version'],'doc':old['doc']},r['id'])
 r=mutate(mutate(r,'submit'),'approve',review_confirmed=True);r=mutate(r,'publish',agent_ids=[AGENT])
 with pytest.raises(TenantLinearError):mutate(r,'publish',agent_ids=[AGENT])
 token=mutate(r,'share')['token'];r=lib.operation(PROFILE,'read',lesson_id=r['id']);r=lib.operation(PROFILE,'save',{'expected_version':r['version'],'doc':r['doc']},r['id']);assert r['state']=='draft'
 assert db.execute('SELECT active FROM saas_ai_knowledge_sources')[0]['active'] is False;db.commit()
 with pytest.raises(TenantLinearError):lib.shared(token)

def test_unpublish_is_distinct_from_public_link_revoke_and_disables_sources(ready):
 db,_=ready;r=save(ready);r=mutate(mutate(r,'submit'),'approve',review_confirmed=True);r=mutate(r,'publish',agent_ids=[AGENT])
 source=db.execute('SELECT id FROM saas_ai_knowledge_sources')[0]['id'];token=mutate(r,'share')['token']
 r=lib.operation(PROFILE,'read',lesson_id=r['id']);r=mutate(r,'unpublish')
 assert r['state']=='approved' and r['published_doc'] is None and r['public_hash'] is None
 assert db.execute('SELECT active FROM saas_ai_knowledge_sources WHERE id=%s::uuid',(source,))[0]['active'] is False;db.commit()
 with pytest.raises(TenantLinearError):lib.shared(token)
 with pytest.raises(TenantLinearError):
  import tenant_ai_agents_store as agents
  agents.save_knowledge(PROFILE,{'content':'bypass edit'},source_id=source)
 with pytest.raises(TenantLinearError):agents.delete_knowledge(PROFILE,source)
 assert db.execute('SELECT count(*) AS n FROM saas_ai_knowledge_sources WHERE id=%s::uuid',(source,))[0]['n']==1;db.commit()

def test_archive_restore_preserves_lesson_and_historical_knowledge(ready):
 db,_=ready;r=save(ready);r=mutate(mutate(r,'submit'),'approve',review_confirmed=True);r=mutate(r,'publish',agent_ids=[AGENT]);source=db.execute('SELECT id FROM saas_ai_knowledge_sources')[0]['id'];db.commit()
 archived=mutate(r,'archive');assert archived['state']=='archived'
 assert lib.operation(PROFILE,'list')['items']==[]
 assert db.execute('SELECT active FROM saas_ai_knowledge_sources WHERE id=%s::uuid',(source,))[0]['active'] is False;db.commit()
 restored=mutate(archived,'restore');assert restored['state']=='published'
 assert db.execute('SELECT active FROM saas_ai_knowledge_sources WHERE id=%s::uuid',(source,))[0]['active'] is True;db.commit()

@pytest.mark.parametrize('fault',['path','bounds','duration','cue','language','order'])
def test_document_bounds(fault):
 v=doc(uid());s=v['slides'][0]
 if fault=='path':s['image']['asset_id']='../secret.jpg'
 if fault=='bounds':s['areas'][0]['width']=200
 if fault=='duration':s['playback']['duration_ms']=999999
 if fault=='cue':v['subtitles']['slides'][0][0]['at_ms']=10000
 if fault=='language':v['language']='xx'
 if fault=='order':s['order']=2
 with pytest.raises((ValueError,TenantLinearError)):lib.document(v)
@pytest.mark.parametrize('language',['ru','az','en'])
def test_languages_and_no_model_approval(language):
 v=doc(uid());v['language']=language;assert lib.document(v)['review_status']=='pending_review'


def test_image_upload_dedup_and_foreign_image(ready):
 import io,base64
 from PIL import Image
 db,asset=ready;out=io.BytesIO();Image.new('RGB',(40,20),'white').save(out,format='PNG')
 data={'image':base64.b64encode(out.getvalue()).decode()};a=lib.operation(PROFILE,'upload',data);b=lib.operation(PROFILE,'upload',data);assert a==b
 row=db.execute('SELECT body FROM saas_instruction_assets WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,a['asset_id'][:-4]))[0];db.commit()
 with Image.open(io.BytesIO(row['body'])) as im:assert im.format=='JPEG' and im.size==(40,20) and not im.getexif()
 with pytest.raises(TenantLinearError):lib.operation({'tenant_id':B,'telegram_id':22},'save',{'id':uid(),'expected_version':0,'doc':doc(asset)})
 with pytest.raises(ValueError):lib.operation(PROFILE,'upload',{'image':'not-base64'})

def test_public_link_disabled_when_company_archived(ready):
 db,_=ready;r=save(ready);r=mutate(mutate(r,'submit'),'approve',review_confirmed=True);r=mutate(r,'publish',agent_ids=[AGENT]);token=mutate(r,'share')['token']
 db.execute("UPDATE saas_tenants SET status='archived' WHERE id=%s::uuid",(A,));db.commit()
 with pytest.raises(TenantLinearError):lib.shared(token)

def test_http_identity_origin_and_content_type(monkeypatch):
 import asyncio,logging
 from aiohttp import web
 from aiohttp.test_utils import TestServer,TestClient
 from tenant_instruction_library_api import handler
 from unittest.mock import Mock
 service=Mock(return_value={'items':[]});monkeypatch.setattr(lib,'operation',service)
 async def check():
  app=web.Application();app.router.add_route('*','/instructions',handler(lambda _:PROFILE,'https://app.test',logging.getLogger('test')))
  async with TestClient(TestServer(app)) as client:
   r=await client.get('/instructions');assert r.status==409;service.assert_not_called()
   r=await client.post('/instructions',json={'expected_tenant_id':A,'expected_user_id':'11'},headers={'Origin':'https://evil.test'});assert r.status==403;service.assert_not_called()
   r=await client.get('/instructions',params={'expected_tenant_id':A,'expected_user_id':'11'});assert r.status==200
 asyncio.run(check())


def test_foreign_agent_rolls_back_entire_publication(ready):
 db,_=ready;r=save(ready);r=mutate(mutate(r,'submit'),'approve',review_confirmed=True)
 with pytest.raises(TenantLinearError):mutate(r,'publish',agent_ids=[AGENT,uid()])
 assert db.execute('SELECT count(*) AS n FROM saas_ai_knowledge_sources')[0]['n']==0;db.commit()
 assert lib.operation(PROFILE,'read',lesson_id=r['id'])['state']=='approved'


def test_additive_schema_is_idempotent_and_preserves_existing_data(ready):
 db,_=ready
 row=save(ready)
 row=mutate(mutate(row,'submit'),'approve',review_confirmed=True)
 row=mutate(row,'publish',agent_ids=[AGENT])
 token=mutate(row,'share')['token']
 tables=['saas_tenants','saas_ai_agents','saas_ai_agent_settings','saas_ai_agent_jobs',
         'saas_ai_conversation_state','saas_ai_knowledge_sources','saas_instruction_lessons','saas_instruction_assets']
 before={t:db.execute('SELECT * FROM '+t) for t in tables};db.commit()
 lib.schema(db);lib.schema(db);db.commit()
 assert {t:db.execute('SELECT * FROM '+t) for t in tables}==before
 indexes=db.execute("SELECT indexdef FROM pg_indexes WHERE indexname='saas_instruction_share_idx'")
 assert len(indexes)==1 and 'UNIQUE' in indexes[0]['indexdef'] and 'WHERE' in indexes[0]['indexdef']
 db.commit()
 assert lib.shared(token)['doc']['title']=='Test lesson'
