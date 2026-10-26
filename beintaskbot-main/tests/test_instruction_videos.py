"""Isolated PostgreSQL lifecycle, generated media, no network or providers."""
import hashlib,subprocess,uuid
import pytest
from test_instruction_library import ready,save,mutate,database,db
from test_tenant_support_inbox import A,B,AGENT,PROFILE
import tenant_instruction_library as lib
import tenant_instruction_videos as videos
from tenant_linear_policy import TenantLinearError

@pytest.fixture(autouse=True)
def no_delivery(monkeypatch):
    import tenant_connect_transport,tenant_chat_send_provider
    def forbidden(*a,**k):raise AssertionError('Simulation attempted external delivery')
    monkeypatch.setattr(tenant_connect_transport,'send',forbidden)
    monkeypatch.setattr(tenant_chat_send_provider,'send',forbidden)

def uid():return str(uuid.uuid4())
@pytest.fixture(scope='module')
def clip(tmp_path_factory):
    path=tmp_path_factory.mktemp('video')/'synthetic.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=black:s=32x32:d=1','-an','-c:v','mpeg4',str(path)],check=True)
    return path.read_bytes()
@pytest.fixture
def setup(ready,clip):
    conn,_=ready;row=save(ready);row=mutate(mutate(row,'submit'),'approve',review_confirmed=True)
    return conn,mutate(row,'publish',agent_ids=[AGENT]),clip

def op(row,action,**data):return lib.operation(PROFILE,action,{'expected_version':row['version'],'product':'Akul',**data},row['id'])
def add(setup,task='inventory save',version='v1',salt=''):
    _,row,clip=setup
    body=clip if (task,version,salt)==('inventory save','v1','') else clip+(task+version+salt).encode()
    return op(row,'video_upload',source_kind='owner_upload',_video_body=body,metadata={'task':task,'product':'Akul','product_version':version,'when_offer':'After explaining '+task})
def approve(row,asset):return op(row,'video_approve',asset_id=asset['id'],asset_version=asset['version'],sha256=asset['sha256'],client_safe_reviewed=True)
def args(row,asset):return dict(agent_id=AGENT,dialog_id=uid(),offer_id=uid(),asset_id=asset['id'],asset_version=asset['version'],query='How to inventory save',product_version='v1',text_answer='Save the draft to continue tomorrow.')

def test_durable_card_offer_confirmation_receipt(setup):
    conn,row,clip=setup;asset=add(setup)
    assert not asset['reusable_approved'] and lib.private_video(PROFILE,row['id'],asset['id'])==clip
    assert op(row,'video_find',agent_id=AGENT,query='inventory save',product_version='v1')['items']==[]
    asset=approve(row,asset);found=op(row,'video_find',agent_id=AGENT,query='inventory save',product_version='v1')
    assert found['status']=='selected' and found['items'][0]['match_reason'] and not found['production_send_enabled']
    data=args(row,asset);offer=op(row,'video_offer',**data)
    assert offer['status']=='offered' and not offer['customer_sent']
    assert op(row,'video_offer',**data)['offer_id']==offer['offer_id']
    result=op(row,'video_confirm',**data,simulated_client_yes=True)
    assert result['status']=='virtual_sent' and result['sha256']==hashlib.sha256(clip).hexdigest()
    assert op(row,'video_confirm',**data,simulated_client_yes=True)==result
    assert conn.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE action='instruction_video_virtual_send'")[0]['n']==1

def test_tasks_versions_ambiguity_negative(setup):
    _,row,_=setup;assets=[approve(row,add(setup,t,v)) for t,v in [('inventory save','v1'),('inventory save','v2'),('inventory differences','v1')]]
    def find(q,v=None):return op(row,'video_find',agent_id=AGENT,query=q,product_version=v)
    assert find('inventory save')['status']=='clarify'
    assert find('inventory save','v2')['items'][0]['id']==assets[1]['id']
    assert find('inventory differences','v1')['items'][0]['id']==assets[2]['id']
    assert find('different payment methods','v1')['status']=='not_found'
    assert find('inventory','v1')['status']=='not_found'
    assert op(row,'video_find',agent_id=AGENT,query='inventory save',product='Other',product_version='v1')['status']=='not_found'
    approve(row,add(setup,salt='different clip'));assert find('inventory save','v1')['status']=='clarify'

@pytest.mark.parametrize('fault',['revoke','metadata','expired','unknown','unpublish','agent_archived','source_inactive'])
def test_fail_closed(setup,fault):
    conn,row,_=setup;asset=approve(row,add(setup));data=args(row,asset);op(row,'video_offer',**data)
    if fault=='revoke':op(row,'video_revoke',asset_id=asset['id'],asset_version=asset['version'])
    if fault=='metadata':op(row,'video_metadata',asset_id=asset['id'],asset_version=asset['version'],metadata={**asset['metadata'],'when_offer':'changed'})
    if fault=='expired':conn.execute("UPDATE saas_instruction_video_offers SET expires_at=now()-interval '1 second'")
    if fault=='unknown':conn.execute("UPDATE saas_instruction_video_offers SET status='unknown'")
    if fault=='unpublish':mutate(row,'unpublish')
    if fault=='agent_archived':conn.execute('UPDATE saas_ai_agents SET archived_at=now()')
    if fault=='source_inactive':conn.execute('UPDATE saas_ai_knowledge_sources SET active=FALSE')
    conn.commit()
    with pytest.raises(TenantLinearError):op(row,'video_confirm',**data,simulated_client_yes=True)

def test_wrong_dialog_no_offer_no_consent_collision(setup):
    _,row,_=setup;asset=approve(row,add(setup));data=args(row,asset)
    with pytest.raises(TenantLinearError):op(row,'video_confirm',**data,simulated_client_yes=True)
    op(row,'video_offer',**data)
    for changes in ({'dialog_id':uid()},{'agent_id':uid()},{'query':'different request'}):
        with pytest.raises(TenantLinearError):op(row,'video_offer',**{**data,**changes})
    with pytest.raises(TenantLinearError):op(row,'video_confirm',**data,simulated_client_yes=False)

def test_tenant_missing_scope_owner_legacy(setup):
    conn,row,_=setup;asset=add(setup)
    with pytest.raises(TenantLinearError):lib.private_video({'tenant_id':B,'telegram_id':22},row['id'],asset['id'])
    with pytest.raises(TenantLinearError):lib.private_video(PROFILE,row['id'],uid())
    assert op(row,'video_find',agent_id=uid(),query='inventory save',product_version='v1')['items']==[]
    conn.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid",(A,));conn.commit()
    with pytest.raises(TenantLinearError):approve(row,asset)
    conn.execute("UPDATE saas_tenant_members SET role='owner' WHERE tenant_id=%s::uuid",(A,));conn.commit()
    assert lib.operation(PROFILE,'read',lesson_id=row['id'])['published_doc']==row['published_doc']

def test_validation_no_crm(setup,clip):
    _,row,_=setup
    assert 0<videos.validate_file(clip)<=120
    with pytest.raises(ValueError):videos.validate_file(b'not video')
    with pytest.raises(ValueError):videos.validate_file(b'')
    with pytest.raises(TenantLinearError):op(row,'video_upload',source_kind='crm',_video_body=clip,metadata={})
    with pytest.raises(ValueError):videos.metadata({'task':'x','product':'x','product_version':'x','when_offer':'https://private'})


def test_http_upload_private_read_and_auth(setup):
    import asyncio,json,logging
    from aiohttp import web,FormData
    from aiohttp.test_utils import TestClient,TestServer
    from tenant_instruction_library_api import handler
    _,row,clip=setup
    async def check():
        app=web.Application()
        handle=handler(lambda _:PROFILE,'https://app.test',logging.getLogger('test'))
        app.router.add_route('*','/instructions/{lesson_id}',handle)
        app.router.add_get('/instructions/{lesson_id}/assets/{asset_id}',handle)
        async with TestClient(TestServer(app)) as client:
            def form(tenant=A):
                data=FormData();data.add_field('metadata',json.dumps({'action':'video_upload','expected_version':row['version'],'expected_tenant_id':tenant,'expected_user_id':'11','source_kind':'owner_upload','metadata':{'task':'inventory save','product':'Akul','product_version':'v1','when_offer':'After text'}}));data.add_field('video',clip,filename='synthetic.mp4',content_type='video/mp4');return data
            url='/instructions/'+row['id']
            response=await client.post(url,data=form(),headers={'Origin':'https://evil.test'});assert response.status==403
            response=await client.post(url,data=form(B),headers={'Origin':'https://app.test'});assert response.status==409
            response=await client.post(url,data=form(),headers={'Origin':'https://app.test'});assert response.status==200
            asset=(await response.json())['result']
            response=await client.get(url+'/assets/'+asset['id']+'.mp4',params={'expected_tenant_id':A,'expected_user_id':'11'})
            assert response.status==200 and response.content_type=='video/mp4' and await response.read()==clip
            response=await client.get(url+'/assets/'+asset['id']+'.mp4');assert response.status==409
    asyncio.run(check())


def test_duration_and_size_limits(setup,clip,monkeypatch):
    import json
    from types import SimpleNamespace
    monkeypatch.setattr(videos,'MAX_BYTES',len(clip)-1)
    with pytest.raises(ValueError):videos.validate_file(clip)
    monkeypatch.setattr(videos,'MAX_BYTES',100*1024*1024)
    monkeypatch.setattr(videos.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout=json.dumps({'format':{'format_name':'mov,mp4','duration':121},'streams':[{'codec_type':'video'}]})))
    with pytest.raises(ValueError):videos.validate_file(clip)


def test_new_offer_supersedes_old_and_interleaved_dialogs_are_isolated(setup):
    _,row,_=setup;asset=approve(row,add(setup));one=args(row,asset)
    op(row,'video_offer',**one)
    other=args(row,asset);op(row,'video_offer',**other)
    two={**one,'offer_id':uid()};op(row,'video_offer',**two)
    with pytest.raises(TenantLinearError):op(row,'video_confirm',**one,simulated_client_yes=True)
    assert op(row,'video_confirm',**two,simulated_client_yes=True)['status']=='virtual_sent'
    assert op(row,'video_confirm',**other,simulated_client_yes=True)['status']=='virtual_sent'


def test_expert_discovery_is_private_and_read_only(setup):
    conn,row,_=setup;asset=approve(row,add(setup))
    with conn.cursor() as c:result=lib.expert_lookup(c,A,AGENT,'inventory save')
    assert not result['customer_send_allowed']
    assert result['video_candidates']['items'][0]['id']==asset['id']
    assert result['video_candidates']['status']=='clarify'
    assert not result['video_candidates']['production_send_enabled']
    assert 'body' not in repr(result) and 'source_ref' not in repr(result)


def test_storage_budget_boundary_dedup_retained_revoke(setup,monkeypatch):
    conn,row,clip=setup;monkeypatch.setattr(videos,'TENANT_STORAGE_BYTES',len(clip))
    first=add(setup);assert first['size_bytes']==len(clip)
    assert add(setup)==first
    op(row,'video_revoke',asset_id=first['id'],asset_version=first['version'])
    with pytest.raises(TenantLinearError) as err:add(setup,salt='new')
    assert err.value.status==413
    assert int(conn.execute('SELECT sum(size_bytes) AS used FROM saas_instruction_videos')[0]['used'])==len(clip)
    assert len(conn.execute('SELECT id FROM saas_instruction_videos'))==1
    with pytest.raises(TenantLinearError) as err:
        op(row,'video_upload',source_kind='owner_upload',_video_body=clip,
          metadata={'task':'changed','product':'Akul','product_version':'v1','when_offer':'Changed'})
    assert err.value.status==409
    assert add(setup)['active'] is False


def test_large_blob_metadata_never_returns_bytes_and_file_is_immutable(setup,monkeypatch):
    import tempfile
    from test_tenant_support_inbox import Cursor
    conn,row,clip=setup
    with tempfile.TemporaryFile() as video:
        video.write(clip);video.truncate(4*1024*1024);video.seek(0)
        asset=op(row,'video_upload',source_kind='owner_upload',_video_body=video,
          metadata={'task':'inventory save','product':'Akul','product_version':'v1','when_offer':'After text'})
    assert asset['size_bytes']==4*1024*1024
    class MetadataCursor(Cursor):
        def execute(self,sql,params=None):
            super().execute(sql,params)
            assert not any(isinstance(v,(bytes,bytearray,memoryview)) for r in self.rows for v in r.values()),'metadata operation fetched a blob'
    with monkeypatch.context() as patch:
        patch.setattr(conn,'cursor',lambda:MetadataCursor(conn))
        asset=approve(row,asset);data=args(row,asset);op(row,'video_list');op(row,'video_offer',**data)
        op(row,'video_confirm',**data,simulated_client_yes=True)
        op(row,'video_revoke',asset_id=asset['id'],asset_version=asset['version'])
    for sql,params in [('UPDATE saas_instruction_videos SET body=%s',(b'changed',)),
                       ("UPDATE saas_instruction_videos SET sha256='forged'",()),
                       ('UPDATE saas_instruction_videos SET size_bytes=1',())]:
        with pytest.raises(Exception):conn.execute(sql,params)
        conn.rollback()
    assert lib.private_video(PROFILE,row['id'],asset['id'],length=16)==clip[:16]


def test_successful_receipt_replays_after_expiry_revoke(setup):
    conn,row,_=setup;asset=approve(row,add(setup));data=args(row,asset)
    op(row,'video_offer',**data);receipt=op(row,'video_confirm',**data,simulated_client_yes=True)
    op(row,'video_revoke',asset_id=asset['id'],asset_version=asset['version'])
    conn.execute("UPDATE saas_instruction_video_offers SET expires_at=now()-interval '1 day'");conn.commit()
    assert op(row,'video_confirm',**data,simulated_client_yes=True)==receipt
    assert conn.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE action='instruction_video_virtual_send'")[0]['n']==1
    with pytest.raises(TenantLinearError):op(row,'video_confirm',**{**data,'dialog_id':uid()},simulated_client_yes=True)


def test_http_ranges_and_concurrent_admission(setup):
    import asyncio,logging
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    import tenant_instruction_library_api as api
    _,row,clip=setup;asset=add(setup)
    async def check():
        app=web.Application();handle=api.handler(lambda _:PROFILE,'https://app.test',logging.getLogger('test'))
        app.router.add_get('/instructions/{lesson_id}/assets/{asset_id}',handle)
        app.router.add_post('/instructions/{lesson_id}',handle)
        url='/instructions/'+row['id']+'/assets/'+asset['id']+'.mp4'
        auth={'expected_tenant_id':A,'expected_user_id':'11'}
        async with TestClient(TestServer(app)) as client:
            for value,expected in [('bytes=0-15',clip[:16]),('bytes=-10',clip[-10:]),('bytes=16-',clip[16:])]:
                response=await client.get(url,params=auth,headers={'Range':value})
                assert response.status==206 and response.headers['Accept-Ranges']=='bytes'
                assert await response.read()==expected
            response=await client.get(url,params=auth,headers={'Range':'bytes=999999-1000000'})
            assert response.status==416 and response.headers['Content-Range']=='bytes */'+str(len(clip))
            response=await client.get(url,params=auth,headers={'Range':'bytes=0-2,5-8'});assert response.status==416
            response=await client.get(url,params={**auth,'expected_tenant_id':B},headers={'Range':'bytes=0-15'});assert response.status==409
            async with api.upload_slot():
                # A concurrent request is rejected before its file body is read.
                response=await client.post('/instructions/'+row['id'],data=b'x',headers={'Content-Type':'multipart/form-data; boundary=fake','Origin':'https://app.test'})
                assert response.status==429
    asyncio.run(check())


def test_full_advertised_file_boundary_uses_disk_not_a_giant_test_buffer(monkeypatch):
    import json,tempfile
    from types import SimpleNamespace
    assert videos.MAX_BYTES==100*1024*1024 and videos.MAX_SECONDS==120
    monkeypatch.setattr(videos.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout=json.dumps({'format':{'format_name':'mov,mp4','duration':120},'streams':[{'codec_type':'video'}]})))
    with tempfile.TemporaryFile() as source:
        source.truncate(100*1024*1024)
        assert videos.validate_file(source)==120
        source.truncate(100*1024*1024+1)
        with pytest.raises(ValueError):videos.validate_file(source)


def test_video_metadata_pages_are_bounded(setup):
    conn,row,_=setup;asset=add(setup)
    # Synthetic retained rows exercise list size without loading blobs.
    conn.execute('''INSERT INTO saas_instruction_videos(tenant_id,lesson_id,id,body,sha256,duration_seconds,metadata,size_bytes)
      SELECT tenant_id,lesson_id,md5(n::text)::uuid,body,sha256,duration_seconds,metadata,size_bytes
      FROM saas_instruction_videos CROSS JOIN generate_series(1,50) n WHERE id=%s::uuid''',(asset['id'],));conn.commit()
    first=op(row,'video_list');second=op(row,'video_list',offset=first['next_offset'])
    assert len(first['items'])==50 and first['next_offset']==50
    assert len(second['items'])==1 and second['next_offset'] is None
