"""A local fake WS server exercises framing only; no OpenAI calls or keys."""
import asyncio
import base64
import json
import unittest
import uuid

import aiohttp
from aiohttp import web

from tenant_linear_policy import TenantLinearError
from tenant_operator_realtime_wire import (
    AiohttpRealtimeSocket, _RealtimeWire, _open_openai_socket,
    MAX_WIRE_MESSAGE, MINIMUM_HOLD, MODEL, QUOTE,
)
from tenant_operator_voice_session import Session


class OneUseGuard:
    def __init__(self): self.used=set()
    async def consume(self,scope,receipt):
        if scope['request_id'] in self.used: return False
        self.used.add(scope['request_id']);return True


class AiohttpRealtimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received=[]
        self.server_socket=None
        async def handler(request):
            ws=web.WebSocketResponse(max_msg_size=MAX_WIRE_MESSAGE)
            await ws.prepare(request);self.server_socket=ws
            await ws.send_json({'type':'session.created','session':{'id':'synthetic-session'}})
            async for message in ws:
                if message.type!=aiohttp.WSMsgType.TEXT:break
                event=json.loads(message.data);self.received.append(event)
                if event['type']=='session.update':
                    await ws.send_json({'type':'session.updated','session':{**event['session'],'id':'synthetic-session'}})
                elif event['type']=='response.create':
                    await ws.send_json({'type':'response.created','response':{'id':'synthetic-response'}})
                elif event['type']=='conversation.item.truncate':
                    await ws.send_json({'type':'conversation.item.truncated','item_id':event['item_id'],
                                        'content_index':event['content_index'],'audio_end_ms':event['audio_end_ms']})
            return ws
        self.runner=web.AppRunner(web.Application())
        self.runner.app.router.add_get('/local',handler)
        await self.runner.setup()
        self.site=web.TCPSite(self.runner,'127.0.0.1',0);await self.site.start()
        port=self.site._server.sockets[0].getsockname()[1]
        self.session=aiohttp.ClientSession()
        self.ws=await self.session.ws_connect(f'http://127.0.0.1:{port}/local',max_msg_size=MAX_WIRE_MESSAGE)
        self.socket=AiohttpRealtimeSocket(self.ws)

    async def asyncTearDown(self):
        await self.socket.close()
        await self.session.close()
        await self.runner.cleanup()

    async def test_real_aiohttp_frames_use_policy_and_one_claim(self):
        wire=_RealtimeWire(self.socket,OneUseGuard())
        snapshot={'existing_copilot_instructions':'Never send messages or perform external actions.',
                  'lines':[{'text':'синтетический mətn'}],'extra_context':{}}
        await wire.open(snapshot,tools=[],create_response=False,interrupt_response=False,transcription=False,reconnect=False)
        scope={'request_id':str(uuid.uuid4()),'tenant_id':str(uuid.uuid4()),'session_id':str(uuid.uuid4()),
               'channel_id':str(uuid.uuid4()),'conversation_id':'synthetic','actor_id':7,'response_number':1,'executor':'test'}
        receipt={**scope,'state':'claimed','model':MODEL,'quote_version':QUOTE,'reserved_microusd':MINIMUM_HOLD,
                 'tenant_reservation_id':str(uuid.uuid4())}
        with self.assertRaisesRegex(ValueError,'claim_missing'):await wire.send({'type':'response.create'})
        await wire.send({'type':'input_audio_buffer.append','audio':'AAA='})
        await wire.bind_claim(scope,receipt);await wire.send({'type':'response.create'})
        response=await asyncio.wait_for(wire.receive(),1)
        self.assertEqual(response['response']['id'],'synthetic-response')
        self.assertEqual([e['type'] for e in self.received],['session.update','conversation.item.create',
                        'input_audio_buffer.append','response.create'])
        settings=self.received[0]['session'];vad=settings['audio']['input']['turn_detection']
        self.assertFalse(vad['create_response']);self.assertFalse(vad['interrupt_response']);self.assertIsNone(vad['idle_timeout_ms'])
        self.assertIsNone(settings['audio']['input']['transcription'])
        self.assertEqual(settings['tools'],[]);self.assertEqual(settings['tool_choice'],'none')
        self.assertNotIn('синтетический',settings['instructions'])
        self.assertEqual(self.received[1]['item']['role'],'user')
        await wire.close();self.assertTrue(self.ws.closed)

    async def test_reject_non_object_invalid_and_oversize(self):
        # Initial session.created is consumed before adversarial server frames.
        await self.socket.receive_json()
        for frame in ('[]','{"x":NaN}','{"type":"ok","type":"evil"}','x','"'+('x'*MAX_WIRE_MESSAGE)+'"'):
            await self.server_socket.send_str(frame)
            with self.assertRaises(ValueError):await self.socket.receive_json()
        with self.assertRaises(ValueError):await self.socket.send_json({'data':'x'*MAX_WIRE_MESSAGE})

    async def test_live_connector_fails_before_socket_or_key_use(self):
        class Forbidden:
            def __getattr__(self,name):raise AssertionError('I/O attempted')
        with self.assertRaises(TenantLinearError):await _open_openai_socket(Forbidden(),Forbidden())

    async def test_peer_disconnect_fails_closed(self):
        await self.socket.receive_json()
        await self.server_socket.close()
        with self.assertRaisesRegex(ValueError,'wire_socket_closed_or_nontext'):
            await asyncio.wait_for(self.socket.receive_json(),1)

    async def test_session_interrupt_and_shutdown_through_local_websocket(self):
        class TestWire(_RealtimeWire): mock_only=True
        class Authority:
            async def check(self,*_):pass
        class Context:
            async def read_authorized(self,_):
                return {'existing_copilot_instructions':'Never send messages or perform external actions.',
                        'lines':[],'extra_context':{}}
            async def revalidate(self,_):return True
        class Lease:
            held=False
            async def claim(self,_):self.held=True;return 'fence'
            async def assert_current(self,*_):assert self.held
            async def release(self,*_):self.held=False
        class Ledger:
            events=[]
            async def reserve(self,s):
                self.events.append('reserve');return {**s,'state':'reserved','reserved_microusd':MINIMUM_HOLD}
            async def claim(self,s):
                self.events.append('claim')
                return {**s,'state':'claimed','reserved_microusd':MINIMUM_HOLD,'model':MODEL,'quote_version':QUOTE,
                        'tenant_reservation_id':str(uuid.uuid4())}
            async def mark_unknown(self,s):self.events.append('unknown');return {**s,'state':'unknown'}
        class Client:
            events=[]
            async def send(self,event):self.events.append(event)
        provider=TestWire(self.socket,OneUseGuard());lease=Lease();ledger=Ledger();client=Client()
        session=Session((str(uuid.uuid4()),7,str(uuid.uuid4()),'synthetic'),authority=Authority(),ledger=ledger,
                        lease=lease,context=Context(),provider=provider,client=client)
        try:
            await session.start()
            await session.receive_client({'type':'audio','pcm':bytes(480)})
            await session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
            await session.receive_provider(await asyncio.wait_for(provider.receive(),1))
            prior=list(client.events)
            # Generated transcripts are not proven heard and are neither
            # exposed nor stored by the currently blocked history path.
            await session.receive_provider({'type':'response.output_audio_transcript.done',
                                            'response_id':'synthetic-response','transcript':'synthetic unverified text'})
            self.assertEqual(client.events,prior)
            await self.server_socket.send_json({'type':'response.output_audio.delta','response_id':'synthetic-response',
                                               'item_id':'audio-1','content_index':0,'delta':base64.b64encode(bytes(480)).decode()})
            await session.receive_provider(await asyncio.wait_for(provider.receive(),1))
            await session.receive_provider({'type':'input_audio_buffer.speech_started'})
            self.assertEqual(client.events[-1],{'type':'interrupt','item_id':'audio-1'})
            await session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
            await session.receive_provider({'type':'response.done','response':{'id':'synthetic-response'}})
            await session.receive_client({'type':'played','item_id':'audio-1','played_ms':0})
            await session.receive_provider(await asyncio.wait_for(provider.receive(),1))
            await session.receive_provider(await asyncio.wait_for(provider.receive(),1))
            self.assertEqual(ledger.events,['reserve','claim','unknown','reserve','claim'])
            self.assertTrue(await session.end('test'))
            self.assertFalse(lease.held);self.assertTrue(self.ws.closed)
            self.assertEqual(ledger.events[-1],'unknown')
            self.assertEqual(self.received[-2]['type'],'conversation.item.truncate')
            self.assertEqual(self.received[-2]['audio_end_ms'],0)
            self.assertEqual(self.received[-1]['type'],'response.create')
        finally:
            await session.end('cleanup')


if __name__=='__main__':unittest.main()
