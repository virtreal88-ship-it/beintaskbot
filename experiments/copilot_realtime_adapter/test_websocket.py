import asyncio
import base64
import unittest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer
from session import Session
from websocket import make_handler
from test_session import Authority, Ledger, Lease, Context, Provider


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
        await session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
        self.assertEqual([e[0] for e in self.ledger.events],['reserve','claim'])
        await session.receive_provider({'type':'response.created','response':{'id':'response-1'}})
        await session.receive_provider({'type':'response.output_audio.delta','response_id':'response-1','item_id':'synthetic','content_index':0,
                                        'delta':base64.b64encode(bytes(4800)).decode()})
        audio=await ws.receive_json();self.assertEqual(audio['item_id'],'synthetic');self.assertEqual(len(base64.b64decode(audio['pcm_base64'])),4800)
        await session.receive_provider({'type':'input_audio_buffer.speech_started'})
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

    def test_missing_security_dependencies_rejected(self):
        with self.assertRaises(ValueError): make_handler(allowed_origins={'*'},authenticate=None,build_session=None)


if __name__=='__main__': unittest.main()
