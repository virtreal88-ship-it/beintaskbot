"""Terminal notification and websocket lifecycle without external services."""
import asyncio
import unittest
from aiohttp import web, WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from tenant_operator_voice_gateway import SocketClient, make_handler
from tenant_operator_voice_session import Session


class TerminalSocketTest(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_user_provider_and_disconnect_share_one_end_operation(self):
        entered=asyncio.Event();release=asyncio.Event();calls=[]
        async def finish(reason):calls.append(reason);entered.set();await release.wait();return True
        session=object.__new__(Session)
        session._opening=False;session._end_task=None;session.closed=False;session.output_finished=False
        session.watchdog=None;session._latency_commit_times={};session._finish_end=finish
        tasks=[asyncio.create_task(session.end(reason)) for reason in ('user_end','provider_disconnected','disconnected')]
        await asyncio.wait_for(entered.wait(),1);release.set()
        self.assertEqual(await asyncio.gather(*tasks),[True,True,True])
        self.assertEqual(calls,['user_end'])

    async def test_error_and_duplicate_terminals_cannot_pass_inflight_terminal(self):
        entered=asyncio.Event();release=asyncio.Event();close_release=asyncio.Event()
        class WS:
            closed=False
            def __init__(self):self.sent=[]
            async def send_json(self,event):
                if event['type']=='ended':entered.set();await release.wait()
                self.sent.append(event)
            async def close(self):await close_release.wait();self.closed=True
        ws=WS();client=SocketClient(ws)
        first=asyncio.create_task(client.send({'type':'ended','reason':'user_end'}))
        await asyncio.wait_for(entered.wait(),1)
        error=asyncio.create_task(client.send({'type':'error','code':'voice_unavailable'}))
        duplicate=asyncio.create_task(client.send({'type':'end_unconfirmed'}))
        release.set();await asyncio.wait_for(first,1)
        with self.assertRaises(ConnectionError):await asyncio.wait_for(error,1)
        await asyncio.wait_for(duplicate,1)
        self.assertEqual(ws.sent,[{'type':'ended','reason':'user_end'}])
        close_release.set();await asyncio.wait_for(client.close(),1)

    async def test_real_finish_end_does_not_weaken_provider_or_lease_gates(self):
        for outcome in ('success','provider_failure','lease_failure'):
            with self.subTest(outcome=outcome):
                order=[];close_release=asyncio.Event()
                class WS:
                    closed=False
                    def __init__(self):self.sent=[]
                    async def send_json(self,event):self.sent.append(event);order.append(event['type'])
                    async def close(self):await close_release.wait();self.closed=True
                class Provider:
                    async def close(self):
                        order.append('provider_closed')
                        if outcome=='provider_failure':raise RuntimeError('synthetic failure')
                class Lease:
                    async def release(self,*_):
                        order.append('lease_release')
                        return outcome!='lease_failure'
                ws=WS();socket=SocketClient(ws);s=object.__new__(Session)
                s.current_reservation=None;s.pilot_policy=None;s.history=None;s.transcript=None
                s.provider=Provider();s.lease=Lease();s.fence=1;s.scope=('tenant','actor','channel','dialog')
                s.client=socket;s.shutdown_timeout=.5
                for name in ('io_lock','receive_lock','provider_event_lock','client_event_lock','client_lock'):
                    setattr(s,name,asyncio.Lock())
                try:
                    result=await asyncio.wait_for(s._finish_end('user_end'),2)
                    self.assertEqual(result,outcome=='success')
                    if outcome=='success':
                        self.assertEqual(order,['provider_closed','lease_release','ended'])
                        self.assertEqual(ws.sent,[{'type':'ended','reason':'user_end'}])
                    else:
                        self.assertEqual(ws.sent,[{'type':'end_unconfirmed'}])
                        if outcome=='provider_failure':self.assertNotIn('lease_release',order)
                finally:
                    close_release.set();await socket.close()

    async def test_terminal_send_does_not_wait_for_close_and_is_single(self):
        started=asyncio.Event();release=asyncio.Event()
        class WS:
            closed=False
            def __init__(self):self.sent=[];self.close_count=0
            async def send_json(self,event):self.sent.append(event)
            async def close(self):self.close_count+=1;started.set();await release.wait();self.closed=True
        ws=WS();client=SocketClient(ws)
        try:
            await asyncio.wait_for(client.send({'type':'ended','reason':'user_end'}),.1)
            await asyncio.wait_for(started.wait(),1)
            await client.send({'type':'ended','reason':'user_end'})
            self.assertEqual(ws.sent,[{'type':'ended','reason':'user_end'}])
            release.set();await asyncio.wait_for(client.close(),1)
            self.assertTrue(ws.closed);self.assertEqual(ws.close_count,1)
        finally:release.set()

    async def test_failed_send_and_timed_out_send_are_not_confirmed(self):
        class WS:
            closed=False
            def __init__(self):self.closed_event=asyncio.Event()
            async def send_json(self,event):raise RuntimeError('synthetic send failure')
            async def close(self):self.closed=True;self.closed_event.set()
        ws=WS();client=SocketClient(ws)
        with self.assertRaises(RuntimeError):await client.send({'type':'ended','reason':'user_end'})
        self.assertFalse(client.terminal_sent)
        await asyncio.wait_for(ws.closed_event.wait(),1)
        with self.assertRaises(ConnectionError):await client.send({'type':'ended','reason':'user_end'})
        class Stalled(WS):
            async def send_json(self,event):await asyncio.Event().wait()
        stalled=Stalled();client=SocketClient(stalled)
        s=object.__new__(Session);s.shutdown_timeout=.01
        self.assertFalse(await s._bounded(client.send({'type':'ended','reason':'user_end'})))
        self.assertFalse(client.terminal_sent)
        await client.close()
        self.assertTrue(stalled.closed)

    async def test_close_task_error_is_observable_to_owner(self):
        class WS:
            closed=False
            async def send_json(self,event):pass
            async def close(self):raise RuntimeError('synthetic close failure')
        client=SocketClient(WS())
        await client.send({'type':'ended','reason':'user_end'})
        with self.assertRaises(RuntimeError):await client.close()

    async def test_user_end_and_idle_provider_disconnect_send_one_terminal_and_close(self):
        for source in ('user_end','provider_disconnected'):
            with self.subTest(source=source):
                completed=asyncio.Event()
                class Provider:
                    async def receive(self):raise RuntimeError('synthetic disconnect')
                class FakeSession:
                    def __init__(self,client):
                        self.client=client;self.provider=Provider() if source=='provider_disconnected' else object()
                        self.closed=False;self.shutdown_timeout=.5;self.terminal_confirmed=False
                    _consume_task=staticmethod(Session._consume_task)
                    async def start(self):pass
                    async def next_provider_event(self):return await self.provider.receive()
                    async def receive_client(self,event):
                        if event['type']=='end':await self.end('user_end')
                    async def end(self,reason):
                        if self.closed:return
                        self.closed=True
                        self.terminal_confirmed=await Session._bounded(self,self.client.send({'type':'ended','reason':reason}))
                        completed.set()
                sessions=[]
                async def authenticate(request):return ('tenant','actor','channel','dialog')
                def build(scope,client):
                    session=FakeSession(client);sessions.append(session);return session
                app=web.Application();app.router.add_get('/voice',make_handler(
                    allowed_origins=['https://test.local'],authenticate=authenticate,build_session=build))
                client=TestClient(TestServer(app));await client.start_server()
                try:
                    ws=await client.ws_connect('/voice',headers={'Origin':'https://test.local'})
                    if source=='user_end':await ws.send_json({'type':'end'})
                    event=await asyncio.wait_for(ws.receive_json(),3)
                    self.assertEqual(event,{'type':'ended','reason':source})
                    await asyncio.wait_for(completed.wait(),3)
                    self.assertTrue(sessions[0].terminal_confirmed)
                    close=await asyncio.wait_for(ws.receive(),3)
                    self.assertIn(close.type,(WSMsgType.CLOSE,WSMsgType.CLOSED))
                finally:await client.close()
