import asyncio
import base64
import unittest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer
from session import Session
from websocket import make_handler, SocketClient
from unittest.mock import patch
from test_session import Authority, Ledger, Lease, Context, Provider, begin_input, finish_input
from test_vad_fence import BlockReserve


class GatewayTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.auth=Authority();self.ledger=Ledger();self.lease=Lease();self.provider=Provider(self.ledger);self.sessions=[]
        async def authenticate(request):
            if request.headers.get('X-Synthetic-Auth')!='yes': raise ValueError('denied')
            return ('tenant',42,'channel','conversation')
        def build(scope, client):
            session=Session(scope,authority=self.auth,ledger=self.ledger,lease=self.lease,context=Context(),
                            provider=self.provider,client=client,max_seconds=5,idle_seconds=4)
            self.sessions.append(session);return session
        app=web.Application();app.router.add_get('/local-voice',make_handler(allowed_origins={'https://crm.pro.az'},authenticate=authenticate,build_session=build))
        self.client=TestClient(TestServer(app));await self.client.start_server()

    async def asyncTearDown(self): await self.client.close()

    async def test_origin_and_auth_refused_before_upgrade(self):
        for headers in [{'Origin':'https://evil.example','X-Synthetic-Auth':'yes'}, {'Origin':'https://crm.pro.az'}]:
            response=await self.client.get('/local-voice',headers=headers)
            self.assertEqual(response.status,403)
        self.assertEqual(self.sessions,[])

    async def test_binary_audio_budgeted_response_and_interruption(self):
        ws=await self.client.ws_connect('/local-voice',headers={'Origin':'https://crm.pro.az','X-Synthetic-Auth':'yes'})
        self.assertEqual((await ws.receive_json())['type'],'ready')
        session=self.sessions[0]
        await ws.send_bytes(bytes(4800))
        for _ in range(50):
            if self.provider.events: break
            await asyncio.sleep(.01)
        self.assertEqual(self.provider.events[0]['type'],'input_audio_buffer.append')
        await finish_input(session)
        self.assertEqual([e[0] for e in self.ledger.events],['reserve','claim'])
        await session.receive_provider({'type':'response.created','response':{'id':'response-1'}})
        await session.receive_provider({'type':'response.output_audio.delta','response_id':'response-1','item_id':'synthetic','content_index':0,
                                        'delta':base64.b64encode(bytes(4800)).decode()})
        audio=await ws.receive_json();self.assertEqual(audio['item_id'],'synthetic');self.assertEqual(len(base64.b64decode(audio['pcm_base64'])),4800)
        await begin_input(session)
        self.assertEqual((await ws.receive_json())['type'],'interrupt')
        await ws.send_json({'type':'played','item_id':'synthetic','played_ms':0})
        for _ in range(50):
            if self.provider.events[-1]['type']=='conversation.item.truncate': break
            await asyncio.sleep(.01)
        self.assertEqual(self.provider.events[-1]['audio_end_ms'],0)
        await ws.close();await asyncio.sleep(.02)
        self.assertTrue(self.provider.closed);self.assertFalse(self.lease.held)

    async def test_raw_provider_event_is_not_accepted(self):
        ws=await self.client.ws_connect('/local-voice',headers={'Origin':'https://crm.pro.az','X-Synthetic-Auth':'yes'})
        await ws.receive_json();await ws.send_json({'type':'response.create'})
        await asyncio.sleep(.02)
        self.assertTrue(self.provider.closed)
        self.assertEqual(self.ledger.events,[])
        await ws.close()

    async def test_provider_receive_pump_forwards_audio_and_closes(self):
        class QueueProvider(Provider):
            def __init__(self,ledger):super().__init__(ledger);self.queue=asyncio.Queue()
            async def receive(self):return await self.queue.get()
            async def close(self):self.closed=True;self.queue.put_nowait(None)
        self.provider=QueueProvider(self.ledger)
        ws=await self.client.ws_connect('/local-voice',headers={'Origin':'https://crm.pro.az','X-Synthetic-Auth':'yes'})
        self.assertEqual((await ws.receive_json())['type'],'ready')
        await self.provider.queue.put({'type':'input_audio_buffer.speech_started','item_id':'synthetic-input'})
        await self.provider.queue.put({'type':'input_audio_buffer.speech_stopped','item_id':'synthetic-input'})
        await self.provider.queue.put({'type':'input_audio_buffer.committed','item_id':'synthetic-input'})
        await self.provider.queue.put({'type':'conversation.item.added','item':{'id':'synthetic-input',
                                      'type':'message','role':'user','content':[{'type':'input_audio'}]}})
        for _ in range(100):
            if self.ledger.events:break
            await asyncio.sleep(.005)
        self.assertEqual([x[0] for x in self.ledger.events],['reserve','claim'])
        await self.provider.queue.put({'type':'response.created','response':{'id':'synthetic-pump'}})
        await self.provider.queue.put({'type':'response.output_audio.delta','response_id':'synthetic-pump','item_id':'audio',
                                       'content_index':0,'delta':base64.b64encode(bytes(480)).decode()})
        self.assertEqual((await asyncio.wait_for(ws.receive_json(),1))['type'],'audio')
        await ws.close();await asyncio.sleep(.03)
        self.assertTrue(self.provider.closed);self.assertFalse(self.lease.held)

    async def test_reader_observes_speech_while_processor_waits_for_reserve(self):
        class QueueProvider(Provider):
            def __init__(self,ledger):super().__init__(ledger);self.queue=asyncio.Queue()
            async def receive(self):return await self.queue.get()
            async def close(self):self.closed=True;self.queue.put_nowait(None)
        self.ledger=BlockReserve();self.provider=QueueProvider(self.ledger)
        ws=await self.client.ws_connect('/local-voice',headers={'Origin':'https://crm.pro.az','X-Synthetic-Auth':'yes'})
        await ws.receive_json()
        for kind in ('input_audio_buffer.speech_started','input_audio_buffer.speech_stopped','input_audio_buffer.committed'):
            await self.provider.queue.put({'type':kind,'item_id':'A'})
        await self.provider.queue.put({'type':'conversation.item.added','item':{'id':'A','type':'message','role':'user','content':[{'type':'input_audio'}]}})
        try:
            await asyncio.wait_for(self.ledger.enter.wait(),1)
            await self.provider.queue.put({'type':'input_audio_buffer.speech_started','item_id':'B'})
            for _ in range(100):
                if self.sessions[0].speech_epoch==2:break
                await asyncio.sleep(.005)
            self.assertEqual(self.sessions[0].speech_epoch,2)
            self.ledger.go.set()
            for _ in range(100):
                if self.sessions[0].closed:break
                await asyncio.sleep(.005)
            self.assertTrue(self.sessions[0].closed)
            self.assertNotIn('response.create',[e['type'] for e in self.provider.events])
        finally:
            self.ledger.go.set();await ws.close()

    async def test_provider_queue_overflow_closes_without_dispatch(self):
        class QueueProvider(Provider):
            def __init__(self,ledger):super().__init__(ledger);self.queue=asyncio.Queue()
            async def receive(self):return await self.queue.get()
            async def close(self):self.closed=True;self.queue.put_nowait(None)
        self.ledger=BlockReserve();self.provider=QueueProvider(self.ledger)
        ws=await self.client.ws_connect('/local-voice',headers={'Origin':'https://crm.pro.az','X-Synthetic-Auth':'yes'})
        await ws.receive_json()
        for kind in ('input_audio_buffer.speech_started','input_audio_buffer.speech_stopped','input_audio_buffer.committed'):
            await self.provider.queue.put({'type':kind,'item_id':'A'})
        await self.provider.queue.put({'type':'conversation.item.added','item':{'id':'A','type':'message','role':'user','content':[{'type':'input_audio'}]}})
        try:
            await asyncio.wait_for(self.ledger.enter.wait(),1)
            for _ in range(40):await self.provider.queue.put({'type':'rate_limits.updated'})
            for _ in range(100):
                if self.sessions[0].closed:break
                await asyncio.sleep(.005)
            self.assertTrue(self.sessions[0].closed)
            self.ledger.go.set()
            await asyncio.sleep(.02)
            self.assertNotIn('response.create',[e['type'] for e in self.provider.events])
        finally:
            self.ledger.go.set();await ws.close()

    def test_missing_security_dependencies_rejected(self):
        with self.assertRaises(ValueError): make_handler(allowed_origins={'*'},authenticate=None,build_session=None)

    async def test_disconnect_is_read_while_provider_open_pending(self):
        entered=asyncio.Event();cancelled=asyncio.Event();release=asyncio.Event()
        async def slow(*args,**kwargs):
            entered.set()
            try:await release.wait()
            except asyncio.CancelledError:cancelled.set();await release.wait()
        self.provider.open=slow
        ws=await self.client.ws_connect('/local-voice',headers={'Origin':'https://crm.pro.az','X-Synthetic-Auth':'yes'})
        await asyncio.wait_for(entered.wait(),1);await ws.close()
        await asyncio.wait_for(cancelled.wait(),1)
        self.assertTrue(self.lease.held);self.assertFalse(self.provider.closed)
        release.set()
        await asyncio.sleep(.05)
        # Handler cancellation may race the provider's cancelled open. If it
        # cannot prove closure, it must keep the fence; never assert success.
        self.assertTrue(self.provider.closed or self.lease.held)
        if not self.lease.held:self.assertTrue(self.provider.closed)
        self.assertEqual(self.ledger.events,[])

    async def test_frame_after_visible_ready_is_queued_before_ready_send_returns(self):
        ready_sent=asyncio.Event();release=asyncio.Event();original=SocketClient.send
        async def slow_send(client,event):
            await original(client,event)
            if event.get('type')=='ready':ready_sent.set();await release.wait()
        with patch.object(SocketClient,'send',slow_send):
            ws=await self.client.ws_connect('/local-voice',headers={'Origin':'https://crm.pro.az','X-Synthetic-Auth':'yes'})
            self.assertEqual((await ws.receive_json())['type'],'ready');await ready_sent.wait()
            await ws.send_bytes(bytes(480))
            await asyncio.sleep(.03)
            self.assertFalse(self.sessions[0].closed)
            release.set()
            for _ in range(50):
                if self.provider.events:break
                await asyncio.sleep(.01)
            self.assertEqual(self.provider.events[0]['type'],'input_audio_buffer.append')
            await ws.close()


if __name__=='__main__': unittest.main()
