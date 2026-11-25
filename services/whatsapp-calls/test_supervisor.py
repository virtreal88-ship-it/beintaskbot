import json
import uuid
from unittest.mock import AsyncMock,Mock
import pytest
from aiohttp.test_utils import TestServer,TestClient
from supervisor import Supervisor,make_app,binding

SOURCE=str(uuid.UUID(int=1));TOKEN='s'*48

@pytest.mark.asyncio
async def test_source_stop_is_durable_scoped_idempotent_and_requires_explicit_resume(tmp_path):
    supervisor,client=await client_for(tmp_path)
    headers={'Authorization':'Bearer '+TOKEN};endpoint='/v1/sources/'+SOURCE
    payload={**BINDING,'messaging_generation':'g1'}
    scope={'tenant':BINDING['tenant_id'],'source':BINDING['channel_id'],'account':BINDING['account_jid'],'generation':'g1'}
    try:
        await client.post(endpoint,json=payload,headers=headers)
        original=json.loads((tmp_path/SOURCE/'source.json').read_text())
        supervisor.engine.return_value={'diagnostics':{'active_registry_calls':0,'provider_start_requested':0,'audio_attached':0},
            'messaging_diagnostics':{'unknown_sends':0,'pending_events':0}}
        assert (await client.post(endpoint+'/disconnect',json={'scope':{**scope,'generation':'other'}},headers=headers)).status==403
        assert (await client.post(endpoint+'/disconnect',json={'scope':scope},headers=headers)).status==200
        saved=json.loads((tmp_path/SOURCE/'source.json').read_text())
        assert saved=={**original,'disabled':True}
        assert (await (await client.get(endpoint,headers=headers)).json())['stopped']
        assert (await client.post(endpoint+'/messages',json={'scope':scope},headers=headers)).status==409
        assert (await client.post(endpoint,json=payload,headers=headers)).status==409
        supervisor.engine.reset_mock()
        assert (await client.post(endpoint+'/disconnect',json={'scope':scope},headers=headers)).status==200
        supervisor.engine.assert_not_called()
        assert (await client.post(endpoint,json={**payload,'resume':True},headers=headers)).status==200
        assert json.loads((tmp_path/SOURCE/'source.json').read_text())=={**original,'disabled':False}
    finally:await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('key',['active_registry_calls','unknown_sends','pending_events'])
async def test_source_stop_refuses_live_or_unknown_work(tmp_path,key):
    supervisor,client=await client_for(tmp_path)
    headers={'Authorization':'Bearer '+TOKEN};endpoint='/v1/sources/'+SOURCE
    scope={'tenant':BINDING['tenant_id'],'source':BINDING['channel_id'],'account':BINDING['account_jid'],'generation':'g1'}
    try:
        await client.post(endpoint,json={**BINDING,'messaging_generation':'g1'},headers=headers)
        status={'diagnostics':dict(active_registry_calls=0,provider_start_requested=0,audio_attached=0),
                'messaging_diagnostics':dict(unknown_sends=0,pending_events=0)}
        status['diagnostics' if key=='active_registry_calls' else 'messaging_diagnostics'][key]=1
        supervisor.engine.return_value=status
        assert (await client.post(endpoint+'/disconnect',json={'scope':scope},headers=headers)).status==409
        assert not supervisor.sources[SOURCE]['data'].get('disabled')
    finally:await client.close()

BINDING={'tenant_id':str(uuid.UUID(int=2)),'channel_id':str(uuid.UUID(int=3)),
         'account_jid':'123456789@s.whatsapp.net','gateway_token':'x'*48}


@pytest.mark.asyncio
async def test_stop_waits_for_process_exit_and_saved_stop_survives_restart(tmp_path):
    import asyncio
    from types import SimpleNamespace
    supervisor,client=await client_for(tmp_path)
    headers={'Authorization':'Bearer '+TOKEN};endpoint='/v1/sources/'+SOURCE
    try:
        await client.post(endpoint,json={**BINDING,'messaging_generation':'g1'},headers=headers)
        entry=supervisor.sources[SOURCE];exited=asyncio.Event()
        def terminate():
            assert json.loads((tmp_path/SOURCE/'source.json').read_text())['disabled']
            exited.set()
        async def process_owner():
            await exited.wait()
            entry['process']=None
        entry['process']=SimpleNamespace(returncode=None,terminate=terminate)
        entry['task']=asyncio.create_task(process_owner())
        supervisor.engine.return_value={'diagnostics':dict(active_registry_calls=0,provider_start_requested=0,audio_attached=0),'messaging_diagnostics':dict(unknown_sends=0,pending_events=0)}
        scope={'tenant':BINDING['tenant_id'],'source':BINDING['channel_id'],'account':BINDING['account_jid'],'generation':'g1'}
        assert (await client.post(endpoint+'/disconnect',json={'scope':scope},headers=headers)).status==200
        assert entry['task'].done() and entry['process'] is None
        saved=json.loads((tmp_path/SOURCE/'source.json').read_text())
        restarted=Supervisor(tmp_path,TOKEN,'https://worker.invalid','never-executed')
        restarted.validate_saved(SOURCE,saved)
        restarted.sources[SOURCE]={'data':saved,'task':None,'process':None}
        restarted.configure=Mock(side_effect=AssertionError('disabled source must not launch'))
        restarted.launch(SOURCE)
        assert restarted.sources[SOURCE]['task'] is None
    finally:await client.close()


async def client_for(tmp_path):
    supervisor=Supervisor(tmp_path,TOKEN,'https://worker.invalid','never-executed')
    supervisor.start=AsyncMock();supervisor.stop=AsyncMock();supervisor.launch=Mock();supervisor.engine=AsyncMock(return_value={'connected':False,'account_jid':'','qr':''})
    client=TestClient(TestServer(make_app(supervisor)));await client.start_server()
    return supervisor,client


@pytest.mark.asyncio
async def test_private_control_rejects_browser_tokens_and_legacy_urls(tmp_path):
    supervisor,client=await client_for(tmp_path)
    try:
        for extra in ({},{'Authorization':'Bearer wrong'},{'Authorization':'Bearer '+TOKEN,'Origin':'https://crm.pro.az'},
                      {'Authorization':'Bearer '+TOKEN,'Cookie':'auth=1'}):
            response=await client.post('/v1/sources/'+SOURCE,json=BINDING,headers=extra)
            assert response.status==403
        assert not supervisor.sources
        response=await client.get('/api/sessions',headers={'Authorization':'Bearer '+TOKEN})
        assert response.status==404
    finally:await client.close()


@pytest.mark.asyncio
async def test_source_provision_is_durable_immutable_and_never_returns_secrets(tmp_path):
    supervisor,client=await client_for(tmp_path)
    headers={'Authorization':'Bearer '+TOKEN}
    try:
        first=await client.post('/v1/sources/'+SOURCE,json=BINDING,headers=headers)
        result=await first.json();assert first.status==200 and set(result)=={'public_key'} and len(result['public_key'])==64
        saved=json.loads((tmp_path/SOURCE/'source.json').read_text());supervisor.validate_saved(SOURCE,saved)
        repeated=await client.post('/v1/sources/'+SOURCE,json=BINDING,headers=headers)
        assert await repeated.json()==result
        for field in ('tenant_id','channel_id','gateway_token','account_jid'):
            changed={**BINDING,field:str(uuid.UUID(int=9)) if field.endswith('_id') else 'z'*48 if field=='gateway_token' else '987654321@s.whatsapp.net'}
            response=await client.post('/v1/sources/'+SOURCE,json=changed,headers=headers)
            assert response.status==409
        assert json.loads((tmp_path/SOURCE/'source.json').read_text())==saved
        other=await client.post('/v1/sources/'+str(uuid.UUID(int=10)),json={**BINDING,'channel_id':str(uuid.UUID(int=10))},headers=headers)
        assert other.status==409
        assert len(supervisor.sources)==1
    finally:await client.close()


def test_only_direct_number_and_uuid_binding_are_accepted():
    from aiohttp import web
    for changed in ({'account_jid':'123456@g.us'},{'account_jid':'123456@lid'},{'tenant_id':'../other'},
                    {'gateway_token':'short'},{'arbitrary_url':'https://external.invalid'}):
        with pytest.raises(web.HTTPBadRequest):binding({**BINDING,**changed})


@pytest.mark.asyncio
async def test_unified_pairing_generation_is_immutable_and_keeps_keys_private(tmp_path):
    supervisor,client=await client_for(tmp_path)
    headers={'Authorization':'Bearer '+TOKEN};endpoint='/v1/sources/'+SOURCE
    try:
        payload={**BINDING,'messaging_generation':'test-g1'}
        response=await client.post(endpoint,json=payload,headers=headers)
        assert response.status==200
        assert set(await response.json())=={'public_key'}
        original=json.loads((tmp_path/SOURCE/'source.json').read_text())
        assert original['messaging']['generation']=='test-g1'
        assert len(original['messaging']['key'])==64
        assert (await client.post(endpoint,json=payload,headers=headers)).status==200
        assert json.loads((tmp_path/SOURCE/'source.json').read_text())==original
        assert (await client.post(endpoint,json={**payload,'messaging_generation':'test-g2'},headers=headers)).status==409
        assert json.loads((tmp_path/SOURCE/'source.json').read_text())==original
    finally:await client.close()


@pytest.mark.asyncio
async def test_call_only_transition_requires_exact_stopped_device_and_is_idempotent(tmp_path):
    import sqlite3
    supervisor,client=await client_for(tmp_path)
    try:
        response=await client.post('/v1/sources/'+SOURCE,json=BINDING,headers={'Authorization':'Bearer '+TOKEN})
        public=(await response.json())['public_key']
        saved=(tmp_path/SOURCE/'source.json').read_bytes()
        db=sqlite3.connect(tmp_path/SOURCE/'session.db')
        db.execute('CREATE TABLE whatsmeow_event_buffer(plaintext BLOB)');db.commit();db.close()
        device=(tmp_path/SOURCE/'session.db').read_bytes()
        item={k:v for k,v in BINDING.items() if k!='gateway_token'}
        item.update(gateway_id=SOURCE,public_key=public,generation='transition-g1')
        manifest=json.dumps([item])
        with pytest.raises(ValueError,match='exclusive_startup'):supervisor.apply_messaging_transitions(manifest)
        supervisor.volume_lock=object()
        for field in ('tenant_id','channel_id','account_jid','public_key','gateway_id'):
            with pytest.raises(Exception):supervisor.apply_messaging_transitions(json.dumps([{**item,field:'other'}]))
            assert (tmp_path/SOURCE/'source.json').read_bytes()==saved
        supervisor.sources[SOURCE]['task']=object()
        with pytest.raises(ValueError,match='exclusive_startup'):supervisor.apply_messaging_transitions(manifest)
        supervisor.sources[SOURCE]['task']=None
        db=sqlite3.connect(tmp_path/SOURCE/'session.db');db.execute('INSERT INTO whatsmeow_event_buffer VALUES(?)',(b'pending',));db.commit();db.close()
        with pytest.raises(ValueError,match='buffer_migration_required'):supervisor.apply_messaging_transitions(manifest)
        assert (tmp_path/SOURCE/'source.json').read_bytes()==saved
        # Synthetic pending buffer only; production is never erased by transition.
        db=sqlite3.connect(tmp_path/SOURCE/'session.db');db.execute('DELETE FROM whatsmeow_event_buffer');db.commit();db.close()
        before_device=(tmp_path/SOURCE/'session.db').read_bytes()
        supervisor.apply_messaging_transitions(manifest)
        after=json.loads((tmp_path/SOURCE/'source.json').read_text())
        assert after['messaging']['generation']=='transition-g1'
        assert {k:v for k,v in after.items() if k!='messaging'}==json.loads(saved)
        assert json.loads((tmp_path/SOURCE/'source.before-native.json').read_text())==json.loads(saved)
        assert (tmp_path/SOURCE/'session.db').read_bytes()==before_device
        supervisor.apply_messaging_transitions(manifest)
        assert json.loads((tmp_path/SOURCE/'source.json').read_text())==after
        with pytest.raises(ValueError,match='generation_changed'):
            supervisor.apply_messaging_transitions(json.dumps([{**item,'generation':'transition-g2'}]))
        with pytest.raises(ValueError,match='manifest_invalid'):supervisor.apply_messaging_transitions(json.dumps([item,item]))
    finally:await client.close()


@pytest.mark.asyncio
async def test_native_send_requires_explicit_binding_and_exact_scope(tmp_path):
    supervisor,client=await client_for(tmp_path)
    headers={'Authorization':'Bearer '+TOKEN}
    try:
        await client.post('/v1/sources/'+SOURCE,json=BINDING,headers=headers)
        endpoint='/v1/sources/'+SOURCE+'/messages'
        assert (await client.post(endpoint,json={},headers=headers)).status==409
        supervisor.sources[SOURCE]['data']['messaging']={'generation':'g1','key':'a'*64}
        expected={'tenant':BINDING['tenant_id'],'source':BINDING['channel_id'],'account':BINDING['account_jid'],'generation':'g1'}
        for suffix in ('','/group'):
            for key in expected:
                value={**expected,key:'other'}
                assert (await client.post(endpoint+suffix,json={'scope':value},headers=headers)).status==403
        for extra in ({'Origin':'https://crm.pro.az'},{'Cookie':'session=synthetic'}):
            assert (await client.post(endpoint,json={'scope':expected},headers={**headers,**extra})).status==403
        supervisor.configure(SOURCE)
        config=json.loads((tmp_path/SOURCE/'gateway.json').read_text())
        assert config['messaging']['scope']==expected
        assert config['messaging']['key']
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_audio_proxy_diagnosis_distinguishes_stage_without_error_text():
    import asyncio
    from types import SimpleNamespace
    from aiohttp import WSMsgType
    from supervisor import copy_audio
    async def frames(data=b'\x00\x01'):
        yield SimpleNamespace(type=WSMsgType.BINARY,data=data)
    target=SimpleNamespace(send_bytes=AsyncMock())
    assert await copy_audio(frames(),target,'engine')=='engine_closed'
    target.send_bytes.assert_awaited_once_with(b'\x00\x01')
    target.send_bytes.reset_mock()
    assert await copy_audio(frames(b'\x00'),target,'worker')=='worker_invalid_pcm'
    target.send_bytes.assert_not_awaited()
    target.send_bytes.side_effect=asyncio.TimeoutError('private payload')
    assert await copy_audio(frames(),target,'engine')=='engine_send_timeout'
    target.send_bytes.side_effect=RuntimeError('private credentials')
    assert await copy_audio(frames(),target,'worker')=='worker_transport_error'

@pytest.mark.asyncio
async def test_video_binary_proxy_checks_scope_and_streams_to_engine(tmp_path):
    import base64
    from aiohttp import ClientSession, web

    observed=[]
    async def upstream(request):
        content=await request.read()
        observed.append((content, request.content_type, request.headers['X-Native-Video-Command']))
        return web.json_response({'confirmed':True})

    engine=web.Application(handler_args={'max_field_size':32768})
    engine.router.add_post('/internal/messages/video',upstream)
    supervisor,client=await client_for(tmp_path)
    endpoint='/v1/sources/'+SOURCE
    headers={'Authorization':'Bearer '+TOKEN}
    scope={'tenant':BINDING['tenant_id'],'source':BINDING['channel_id'],'account':BINDING['account_jid'],'generation':'g1'}
    def command(target):
        fields={'scope':target,'kind':'video','mime':'video/mp4','data':'','text':'təbrik'*1500}
        return base64.b64encode(json.dumps(fields,ensure_ascii=False).encode()).decode()
    content=b'\0\0\0\x18ftypmp42'+b'\0'*64
    try:
        assert (await client.post(endpoint,json={**BINDING,'messaging_generation':'g1'},headers=headers)).status==200
        async with TestServer(engine) as engine_server, ClientSession() as proxy:
            supervisor.client=proxy
            supervisor.sources[SOURCE]['data']['port']=engine_server.port
            upload={**headers,'Content-Type':'application/octet-stream'}
            assert (await client.post(endpoint+'/messages/video',data=content,headers={
                **upload,'X-Native-Video-Command':command({**scope,'generation':'other'})})).status==403
            assert not observed
            encoded=command(scope)
            response=await client.post(endpoint+'/messages/video',data=content,
                headers={**upload,'X-Native-Video-Command':encoded})
            assert response.status==200 and (await response.json())['confirmed']
            assert observed==[(content,'application/octet-stream',encoded)]
    finally:
        await client.close()
