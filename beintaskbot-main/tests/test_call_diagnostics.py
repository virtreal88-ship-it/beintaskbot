"""Call attribution, replay and bounded technical evidence in disposable PostgreSQL."""
import uuid
import pytest

import tenant_call_diagnostics as diagnostics
from test_browser_calls import gateway, command, snapshot, provider, calls, PROFILE, A, BOOT, GATEWAY
from test_call_recordings import pilot
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db


def test_gateway_diagnostics_remain_scoped_to_each_call(gateway):
    database,_,config=gateway
    def finish(seq, code):
        started=calls.action(PROFILE,command())
        work=calls.sync(config,snapshot(seq))['commands'][0]
        external='provider-'+str(seq)
        item=provider('ended',peer=started['conversation_id'],direction='outbound',id=external)
        item['diagnostics']={'version':1,'end':{'origin':code,'code':'user_ended','at_ms':1000+seq},
                             'whatsapp_to_engine':{'frames':30,'gaps_over_200ms':3,'max_gap_ms':700}}
        calls.sync(config,snapshot(seq+1,ended=[item],results=[{'id':work['id'],'ok':True,'provider_id':external}]))
        return started,item

    first,ended=finish(2,'whatsapp_terminate')
    second,_=finish(4,'audio_transport')
    calls.sync(config,snapshot(6,ended=[ended]))
    rows=database.execute("SELECT id::text,diagnostics FROM saas_browser_calls ORDER BY created_at")
    assert len(rows)==2
    assert rows[0]['diagnostics']['gateway']['end']['origin']=='whatsapp_terminate'
    assert rows[1]['diagnostics']['gateway']['end']['origin']=='audio_transport'
    assert calls.status(PROFILE)['calls'][0]['state']=='ended'


def test_browser_metrics_require_same_actor_and_call_nonce(gateway):
    database,_,config=gateway
    cmd=command()
    started=calls.action(PROFILE,cmd)
    evidence={'version':1,'seq':2,'reason':'audio_closed','close_code':1006,
              'playback_underrun_samples':3200}
    report={**cmd,'op':'diagnostics','call_id':started['id'],'diagnostics':evidence}
    calls.action(PROFILE,report)
    calls.action(PROFILE,{**report,'diagnostics':{**evidence,'seq':1,'reason':'user_end'}})
    row=database.execute('SELECT diagnostics FROM saas_browser_calls WHERE id=%s',(started['id'],))[0]
    assert row['diagnostics']['browser']==evidence
    with pytest.raises(diagnostics.TenantLinearError):
        calls.action(PROFILE,{**report,'request_id':str(uuid.uuid4())})
    with pytest.raises(diagnostics.TenantLinearError):
        calls.action(PROFILE,{**report,'diagnostics':{**evidence,'secret':'raw provider error'}})


def test_arrivals_distinguish_pause_from_silence():
    arrivals=diagnostics.Arrivals()
    for now in (0,.02,.04,.8,.82):
        arrivals.add(now)
    assert arrivals.snapshot()['frames']==5
    assert arrivals.snapshot()['gaps_over_200ms']==1
    assert arrivals.snapshot()['max_gap_ms']==760


@pytest.mark.parametrize('value',[True,-1,'secret',2**53])
def test_diagnostic_counters_are_bounded(value):
    with pytest.raises(diagnostics.TenantLinearError):
        diagnostics.checked({'version':1,'browser_to_engine':{'frames':value}},'gateway')


def test_relay_persistence_is_bound_to_original_call_authority(gateway,monkeypatch):
    import tenant_platform
    database,_,config=gateway
    monkeypatch.setattr(tenant_platform,'_connect',lambda:database)
    cmd=command()
    started=calls.action(PROFILE,cmd)
    work=calls.sync(config,snapshot(2))['commands'][0]
    calls.sync(config,snapshot(3,results=[{'id':work['id'],'ok':True,'provider_id':'exact-provider'}]))
    evidence={'version':1,'code':'provider_closed','at_ms':1000,
              'browser_to_worker':{'frames':50,'gaps_over_200ms':0},
              'engine_to_worker':{'frames':40,'gaps_over_200ms':2,'max_gap_ms':800}}
    authority=(GATEWAY,'exact-provider',started['id'])
    diagnostics.save_relay({**PROFILE,'telegram_id':33},authority,cmd['request_id'],evidence)
    assert database.execute('SELECT diagnostics FROM saas_browser_calls')[0]['diagnostics']=={}
    diagnostics.save_relay(PROFILE,authority,cmd['request_id'],evidence)
    assert database.execute('SELECT diagnostics FROM saas_browser_calls')[0]['diagnostics']['relay']==evidence


def test_browser_heartbeat_timeout_persists_relay_diagnostics(gateway, monkeypatch):
    import asyncio
    from unittest.mock import Mock

    from aiohttp import WSMsgType, web
    from aiohttp.test_utils import TestClient, TestServer
    import tenant_call_sources_api as api
    import tenant_platform

    class RelayTestServer(TestServer):
        async def _make_runner(self, **kwargs):
            # Match the production AppRunner on an abrupt disconnect.
            return web.AppRunner(self.app, **{**kwargs, 'handler_cancellation': False})

    database, _, config = gateway
    monkeypatch.setattr(tenant_platform, '_connect', lambda: database)
    incoming = provider()
    calls.sync(config, snapshot(2, active=[incoming]))
    call_id = calls.status(PROFILE)['calls'][0]['id']
    accept = command('accept', call_id=call_id)
    calls.action(PROFILE, accept)
    work = calls.sync(config, snapshot(3, active=[incoming]))['commands'][0]
    calls.sync(config, snapshot(4, active=[{**incoming, 'state': 'connected'}], results=[
        {'id': work['id'], 'ok': True, 'provider_id': incoming['provider_id']},
    ]))
    authority = (GATEWAY, incoming['provider_id'], call_id)
    monkeypatch.setattr(api.store, 'audio_authority', lambda *args: authority)
    logger = Mock()

    async def run():
        ws_response = web.WebSocketResponse

        async def engine(request):
            socket = ws_response()
            await socket.prepare(request)
            async for message in socket:
                await socket.send_bytes(message.data)
            return socket

        def short_heartbeat(**kwargs):
            return ws_response(**{**kwargs, 'heartbeat': 0.5})

        upstream = web.Application()
        upstream.router.add_get('/v1/sources/{source}/audio/{provider}/{call}', engine)
        app = web.Application()
        origin = 'https://app.invalid'
        app.router.add_get('/calls/{call_id}/audio', api.handler(lambda _: PROFILE, origin, logger, 'audio'))
        monkeypatch.setattr(api.web, 'WebSocketResponse', short_heartbeat)
        async with TestServer(upstream) as server:
            monkeypatch.setattr(api.store, 'service', lambda: (str(server.make_url('')).rstrip('/'), 'x' * 48))
            async with TestClient(RelayTestServer(app)) as client:
                url = f"/calls/{call_id}/audio?expected_tenant_id={A}&expected_user_id=11&request_id={accept['request_id']}"
                async with client.ws_connect(url, headers={'Origin': origin}, autoping=False) as socket:
                    await socket.send_bytes(bytes(320))
                    assert (await asyncio.wait_for(socket.receive(), 3)).type == WSMsgType.BINARY
                    assert (await asyncio.wait_for(socket.receive(), 3)).type == WSMsgType.PING
                    # Withhold PONG to exercise aiohttp's actual abnormal close.
                    assert (await asyncio.wait_for(socket.receive(), 3)).type == WSMsgType.CLOSED

    asyncio.run(run())
    row = database.execute('SELECT diagnostics FROM saas_browser_calls WHERE id=%s', (call_id,))[0]
    relay = row['diagnostics']['relay']
    assert relay['code'] == 'browser_closed'
    assert relay['browser_close'] == {'close_code': 1006, 'error': 'timeout'}
    assert relay['browser_to_worker']['frames'] == relay['engine_to_worker']['frames'] == 1
    logger.warning.assert_not_called()


def test_invalid_optional_telemetry_cannot_block_hangup(gateway):
    database,_,_=gateway
    cmd=command()
    started=calls.action(PROFILE,cmd)
    calls.action(PROFILE,{**cmd,'op':'end','call_id':started['id'],'diagnostics':{'raw':'untrusted'}})
    assert database.execute('SELECT state FROM saas_browser_calls')[0]['state']=='ending'
    assert database.execute("SELECT count(*) AS n FROM saas_browser_call_commands WHERE op='end'")[0]['n']==1


def test_invalid_gateway_metrics_do_not_block_call_completion(gateway):
    database,_,config=gateway
    started=calls.action(PROFILE,command())
    work=calls.sync(config,snapshot(2))['commands'][0]
    ended=provider('ended',peer=started['conversation_id'],direction='outbound',id='safe-provider')
    ended['diagnostics']={'version':1,'raw':'untrusted'}
    calls.sync(config,snapshot(3,ended=[ended],results=[
        {'id':work['id'],'ok':True,'provider_id':'safe-provider'}]))
    row=database.execute('SELECT state,diagnostics FROM saas_browser_calls')[0]
    assert row=={'state':'ended','diagnostics':{}}
