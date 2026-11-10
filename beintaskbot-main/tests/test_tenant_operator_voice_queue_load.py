"""Local deterministic queue load; no external requests or voice payloads."""
import asyncio
import unittest
from unittest.mock import patch
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import tenant_operator_voice_gateway as gateway


class QueueLoadTest(unittest.IsolatedAsyncioTestCase):
    async def test_queued_pcm_is_coalesced_without_crossing_ack_barrier(self):
        entered=asyncio.Event();release=asyncio.Event();queued=asyncio.Event()
        seen=[];checks=[];count=0
        class FakeSession:
            def __init__(self):self.closed=False;self.provider=object();self.duplex=True;self.MAX_FRAME=9600
            async def start(self):pass
            async def receive_client(self,event):
                checks.append(('authority','lease'))
                if event['type']=='audio':
                    if not entered.is_set():entered.set();await release.wait()
                    seen.append(('audio',event['pcm']))
                else:seen.append((event['type'],None))
            async def end(self,reason):self.closed=True
        async def authenticate(request):return ('tenant','actor','channel','dialog')
        app=web.Application();session=FakeSession()
        app.router.add_get('/voice',gateway.make_handler(allowed_origins=['https://test.local'],
            authenticate=authenticate,build_session=lambda scope,client:session))
        original=gateway._enqueue_client_message
        async def observe(queue,message):
            nonlocal count
            await original(queue,message);count+=1
            if count>=14:queued.set()
        client=TestClient(TestServer(app));await client.start_server()
        try:
            with patch.object(gateway,'_enqueue_client_message',observe), self.assertLogs('tenant_operator_voice_gateway',level='INFO') as logs:
                ws=await client.ws_connect('/voice',headers={'Origin':'https://test.local'})
                await ws.send_bytes(bytes([1])*2048)
                await asyncio.wait_for(entered.wait(),3)
                for i in range(2,10):await ws.send_bytes(bytes([i])*2048)
                await ws.send_json({'type':'playback_scheduled','item_id':'synthetic','context_state':'running'})
                for i in range(10,12):await ws.send_bytes(bytes([i])*2048)
                await ws.send_json({'type':'stop_response','item_id':'synthetic','audio_end_ms':0})
                await ws.send_bytes(bytes([12])*2048)
                await asyncio.wait_for(queued.wait(),3);release.set()
                await ws.send_json({'type':'end'});await asyncio.wait_for(ws.receive(),3)
            audio=[payload for kind,payload in seen if kind=='audio']
            self.assertEqual(b''.join(audio),b''.join(bytes([i])*2048 for i in range(1,13)))
            self.assertTrue(all(len(frame)<=9600 for frame in audio))
            self.assertEqual([len(frame) for frame in audio],[2048,8192,8192,4096,2048])
            self.assertEqual([kind for kind,_ in seen],['audio','audio','audio','playback_scheduled','audio','stop_response','audio','end'])
            self.assertEqual(len(checks),len(seen))
            self.assertIn('input_audio_frames=12',repr(logs.output))
            self.assertIn('input_audio_batches=5',repr(logs.output))
            self.assertIn('input_audio_process_ms=',repr(logs.output))
            self.assertNotIn('synthetic',repr(logs.output))
        finally:
            release.set();await client.close()
