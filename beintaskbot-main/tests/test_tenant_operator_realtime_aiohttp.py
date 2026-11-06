"""A local fake WS server exercises framing only; no OpenAI calls or keys."""
import asyncio
import base64
import json
import time
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web

from tenant_linear_policy import TenantLinearError
from tenant_operator_realtime_wire import (
    AiohttpRealtimeSocket, _RealtimeWire, OpenAIRealtimeProvider, _open_openai_socket, _new_owned_session,
    _connect_websocket, RedirectRefused, SafeRealtimeFailure,
    MAX_WIRE_MESSAGE, MINIMUM_HOLD, MODEL, QUOTE,
)
from tenant_operator_voice_session import Session
from tenant_operator_voice_session import Refused, ProviderResponseRejected, ProviderEventGuardRejected
from tenant_operator_voice_pilot import PilotAdmission
from tenant_operator_voice_gateway import (
    _provider_failure_fields,_log_provider_failure,_safe_voice_activity_fields,
    _expected_close_after_session_end,_safe_client_transport_kind,_enqueue_client_message,
)


class OneUseGuard:
    def __init__(self): self.used=set()
    async def consume(self,scope,receipt):
        if scope['request_id'] in self.used: return False
        self.used.add(scope['request_id']);return True


class AiohttpRealtimeTest(unittest.IsolatedAsyncioTestCase):
    def test_provider_failure_diagnostic_logs_only_code_stage_and_event_type(self):
        event={'type':'conversation.item.input_audio_transcription.delta','delta':'synthetic-private-transcript',
               'audio':'synthetic-private-audio'}
        failure=Refused('unmatched_input_transcript_delta')
        fields=_provider_failure_fields('provider_event',event,failure)
        self.assertEqual(fields,('unmatched_input_transcript_delta','provider_event',
                                 'conversation.item.input_audio_transcription.delta','none','none',False,False))
        self.assertNotIn('synthetic-private-transcript',repr(fields))
        self.assertNotIn('synthetic-private-audio',repr(fields))
        self.assertNotIn('synthetic-private-transcript',str(failure))

    def test_provider_failure_diagnostic_redacts_arbitrary_values(self):
        event={'type':'synthetic-private-value','text':'synthetic-private-text'}
        failure=ValueError('synthetic-private-secret')
        fields=_provider_failure_fields('unexpected-stage',event,failure)
        self.assertEqual(fields,('provider_error','provider_error','unavailable','none','none',False,False))
        self.assertNotIn('synthetic-private',repr(fields))

    def test_provider_failure_log_contains_only_safe_code_and_boolean_flags(self):
        event={'type':'response.output_audio_transcript.delta','response_id':'private-response-id',
               'delta':'private transcript and audio'}
        failure=ProviderResponseRejected('response_id_mismatch',True,False)
        with self.assertLogs('tenant_operator_voice_gateway',level='WARNING') as captured:
            _log_provider_failure('provider_event',event,failure)
        line=captured.output[0]
        self.assertEqual(line,'WARNING:tenant_operator_voice_gateway:realtime_voice_provider_reader_failed '
                         'code=response_id_mismatch stage=provider_event event_type=response.output_audio_transcript.delta '
                         'close_code=none transport_kind=none response_inflight=True response_id_matches=False')
        for forbidden in ('private-response-id','private transcript','private transcript and audio',
                          'response_id_mismatch transcript payload'):
            self.assertNotIn(forbidden,line)

    def test_provider_failure_diagnostic_classifies_safe_provider_codes_and_event(self):
        failure=SafeRealtimeFailure('model_not_found',event_type='error')
        self.assertEqual(_provider_failure_fields('provider_receive',None,failure),
                         ('model_not_found','provider_receive','error','none','none',False,False))
        unknown=SafeRealtimeFailure('synthetic-secret-code',event_type='error')
        self.assertEqual(_provider_failure_fields('provider_receive',None,unknown),
                         ('provider_error','provider_receive','error','none','none',False,False))

    def test_provider_failure_diagnostic_identifies_pre_audio_interrupt_race(self):
        for reason in ('interrupted_before_audio','input_turn_commit_pending'):
            fields=_provider_failure_fields('provider_event',{'type':'input_audio_buffer.speech_started'},Refused(reason))
            self.assertEqual(fields,(reason,'provider_event','input_audio_buffer.speech_started','none','none',False,False))

    async def test_provider_event_access_and_lease_failures_have_safe_distinct_codes(self):
        class Authority:
            def __init__(self,fail):self.fail=fail
            async def check(self,*_):
                if self.fail:raise ValueError('private authorization details')
        class Lease:
            async def assert_current(self,*_):raise ValueError('private lease details')
        for authority,lease,expected in (
            (Authority(True),object(),'provider_authority_rejected'),
            (Authority(False),Lease(),'provider_lease_rejected')):
            session=object.__new__(Session)
            session.closed=False;session.authority=authority;session.lease=lease
            session.scope=('tenant','actor','channel','conversation');session.fence=5
            session.response_inflight=False;session.provider_response_id=None
            with self.assertRaises(ProviderEventGuardRejected) as caught:
                await session._receive_provider({'type':'input_audio_buffer.speech_started'})
            fields=_provider_failure_fields('provider_event',{'type':'input_audio_buffer.speech_started'},caught.exception)
            self.assertEqual(fields,(expected,'provider_event','input_audio_buffer.speech_started',
                                     'none','none',False,False))
            self.assertNotIn('private',str(caught.exception))

    def test_provider_failure_diagnostic_distinguishes_socket_and_parse_failures(self):
        self.assertEqual(_provider_failure_fields('provider_receive',None,SafeRealtimeFailure('websocket_closed')),
                         ('websocket_closed','provider_receive','unavailable','none','none',False,False))
        self.assertEqual(_provider_failure_fields('provider_receive',None,SafeRealtimeFailure('websocket_error')),
                         ('websocket_error','provider_receive','unavailable','none','none',False,False))
        self.assertEqual(_provider_failure_fields('provider_receive',None,SafeRealtimeFailure('provider_json_invalid')),
                         ('provider_json_invalid','provider_receive','unavailable','none','none',False,False))

    def test_provider_failure_diagnostic_records_only_valid_close_metadata(self):
        exc=SafeRealtimeFailure('websocket_closed',close_code=1000,transport_kind='socket_error')
        self.assertEqual(_provider_failure_fields('provider_receive',None,exc),
                         ('websocket_closed','provider_receive','unavailable',1000,'socket_error',False,False))
        malformed=SafeRealtimeFailure('websocket_closed',close_code=4444,transport_kind='synthetic-private-detail')
        self.assertEqual(_provider_failure_fields('provider_receive',None,malformed),
                         ('websocket_closed','provider_receive','unavailable',4444,'unknown',False,False))
        invalid=SafeRealtimeFailure('websocket_closed',close_code=1006,transport_kind='none')
        self.assertEqual(_provider_failure_fields('provider_receive',None,invalid),
                         ('websocket_closed','provider_receive','unavailable','none','none',False,False))

    def test_voice_activity_diagnostics_are_bounded_counts_only(self):
        activity={'input_audio_frames':12,'input_audio_bytes':28800,'vad_speech_started':1,
                  'vad_speech_stopped':1,'input_transcriptions_completed':1,'responses_created':1,
                  'output_audio_chunks':4,'responses_completed':1,'browser_playback_acks':1,
                  'transcript':'private phrase','audio':'private audio'}
        fields=_safe_voice_activity_fields(True,activity)
        self.assertEqual(fields,(True,'unavailable','none','none',False,12,28800,1,1,1,1,4,1,1))
        self.assertNotIn('private',repr(fields))
        self.assertEqual(_safe_voice_activity_fields(False,{'input_audio_frames':-1,'input_audio_bytes':'secret'}),
                         (False,'unavailable','none','none',False,0,0,0,0,0,0,0,0,0))

    def test_client_close_diagnostic_is_allowlisted_and_redacted(self):
        fields=_safe_voice_activity_fields(True,{
            'client_reader_exit':'peer_close','client_close_code':1000,
            'client_transport_kind':'none','client_end_received':False,
        })
        self.assertEqual(fields,(True,'peer_close',1000,'none',False,0,0,0,0,0,0,0,0,0))
        malformed=_safe_voice_activity_fields(True,{
            'client_reader_exit':'secret reason','client_close_code':1006,
            'client_transport_kind':'private exception text','client_end_received':True,
        })
        self.assertEqual(malformed,(True,'unavailable','none','transport_error',True,0,0,0,0,0,0,0,0,0))
        self.assertNotIn('secret',repr(malformed))
        self.assertEqual(_safe_client_transport_kind(None),'none')
        self.assertEqual(_safe_client_transport_kind(TimeoutError()),'timeout')

    async def test_full_bounded_client_queue_backpressures_without_dropping_audio(self):
        incoming=asyncio.Queue(maxsize=1)
        first={'type':'audio','pcm':b'first'}
        second={'type':'audio','pcm':b'second'}
        incoming.put_nowait(first)
        queued=asyncio.create_task(_enqueue_client_message(incoming,second))
        await asyncio.sleep(0)
        self.assertFalse(queued.done(),'a full bounded queue pauses the reader instead of dropping the connection')
        self.assertIs(incoming.get_nowait(),first)
        await asyncio.wait_for(queued,1)
        self.assertIs(incoming.get_nowait(),second)

    async def test_valid_audio_refreshes_idle_activity_after_provider_accepts_it(self):
        class Authority:
            async def check(self,*_):return None
        class Lease:
            async def assert_current(self,*_):return None
        class Provider:
            def __init__(self):self.events=[]
            async def send(self,event):self.events.append(event)
        session=object.__new__(Session)
        session.closed=False;session.authority=Authority();session.lease=Lease()
        session.scope=('tenant','actor','channel','conversation');session.fence=3
        session.muted=False;session.greeting_pending=False;session.response_inflight=False;session.item=None
        session.input_bytes=0;session.max_input_bytes=192000
        session.io_lock=asyncio.Lock();session.started=0;session.max_seconds=120
        session.provider=Provider();session.activity=1.0
        await session._receive_client({'type':'audio','pcm':b'\x01\x00'*1024})
        self.assertEqual(session.input_bytes,2048)
        self.assertGreater(session.activity,1.0,'ongoing audio must prevent a false idle timeout')
        self.assertEqual(session.provider.events[0]['type'],'input_audio_buffer.append')

    async def test_audio_reaches_provider_during_response_start_for_barge_in(self):
        class Authority:
            async def check(self,*_):return None
        class Lease:
            async def assert_current(self,*_):return None
        class Provider:
            def __init__(self):self.events=[]
            async def send(self,event):self.events.append(event)
        session=object.__new__(Session)
        session.closed=False;session.authority=Authority();session.lease=Lease()
        session.scope=('tenant','actor','channel','conversation');session.fence=4
        session.muted=False;session.greeting_pending=False;session.response_inflight=True;session.item=None
        session.input_bytes=0;session.max_input_bytes=192000;session.max_seconds=120;session.started=0
        session.provider=Provider();session.activity=1.0;session.io_lock=asyncio.Lock()
        frame=b'\x01\x00'*1024
        await session._receive_client({'type':'audio','pcm':frame})
        self.assertEqual(session.provider.events[0]['type'],'input_audio_buffer.append',
                         'microphone input must remain live while the assistant response starts')
        self.assertEqual(session.input_bytes,len(frame))
        session.item='first-output-item'
        await session._receive_client({'type':'audio','pcm':frame})
        self.assertEqual(len(session.provider.events),2)

    async def test_watchdog_logs_authority_and_lease_failures_separately(self):
        class Authority:
            def __init__(self,fail):self.fail=fail
            async def check(self,*_):
                if self.fail:raise RuntimeError('private details must not be logged')
        class Lease:
            def __init__(self,fail):self.fail=fail
            async def assert_current(self,*_):
                if self.fail:raise RuntimeError('private details must not be logged')
        for expected,authority_fails,lease_fails in (
            ('authority_check_failed',True,False),('lease_check_failed',False,True)):
            session=object.__new__(Session)
            session.closed=False;session.started=time.monotonic();session.activity=session.started
            session.max_seconds=120;session.idle_seconds=30;session.poll_seconds=.001
            session.scope=('tenant','actor','channel','conversation');session.fence=4
            session.authority=Authority(authority_fails);session.lease=Lease(lease_fails);session.end=AsyncMock()
            await session._watch()
            session.end.assert_awaited_once_with(expected)

    async def test_watchdog_tolerates_slow_successful_authority_recheck(self):
        class SlowAuthority:
            def __init__(self):self.calls=0
            async def check(inner,*_):
                inner.calls+=1
                await asyncio.sleep(1.1)
                session.closed=True
        class Lease:
            async def assert_current(self,*_):return None
        session=object.__new__(Session)
        session.closed=False;session.started=time.monotonic();session.activity=session.started
        session.max_seconds=120;session.idle_seconds=30;session.poll_seconds=.001
        session.scope=('tenant','actor','channel','conversation');session.fence=4
        session.authority=SlowAuthority();session.lease=Lease();session.end=AsyncMock()
        await session._watch()
        self.assertEqual(session.authority.calls,1)
        session.end.assert_not_awaited()

    def test_closed_provider_socket_after_terminal_session_is_expected_cleanup(self):
        session=type('SessionState',(),{'closed':True})()
        self.assertTrue(_expected_close_after_session_end(session,SafeRealtimeFailure('websocket_closed')))
        self.assertFalse(_expected_close_after_session_end(session,SafeRealtimeFailure('websocket_error')))
        session.closed=False
        self.assertFalse(_expected_close_after_session_end(session,SafeRealtimeFailure('websocket_closed')))

    async def test_output_transcript_delta_accepts_matching_live_response(self):
        class Authority:
            async def check(self,*_):return None
        class Lease:
            async def assert_current(self,*_):return None
        session=object.__new__(Session)
        session.closed=False;session.authority=Authority();session.lease=Lease()
        session.scope=('tenant','actor','channel','conversation');session.fence=9
        session.response_inflight=True;session.provider_response_id='opaque-response-id'
        await session._receive_provider({'type':'response.output_audio_transcript.delta',
                                         'response_id':'opaque-response-id','delta':'private transcript'})

    async def test_output_transcript_delta_logs_response_id_mismatch_as_bools_only(self):
        class Authority:
            async def check(self,*_):return None
        class Lease:
            async def assert_current(self,*_):return None
        session=object.__new__(Session)
        session.closed=False;session.authority=Authority();session.lease=Lease()
        session.scope=('tenant','actor','channel','conversation');session.fence=9
        session.response_inflight=True;session.provider_response_id='expected-private-id'
        event={'type':'response.output_audio_transcript.delta','response_id':'other-private-id',
               'delta':'synthetic private transcript'}
        with self.assertRaises(ProviderResponseRejected) as caught:
            await session._receive_provider(event)
        fields=_provider_failure_fields('provider_event',event,caught.exception)
        self.assertEqual(fields,('response_id_mismatch','provider_event',
                                 'response.output_audio_transcript.delta','none','none',True,False))
        self.assertNotIn('private-id',repr(fields))
        self.assertNotIn('synthetic private transcript',repr(fields))

    async def test_output_transcript_delta_distinguishes_authority_and_lease_rejection(self):
        class AuthorityDenied:
            async def check(self,*_):raise RuntimeError('private authorization details')
        class LeaseDenied:
            async def assert_current(self,*_):raise RuntimeError('private lease details')
        event={'type':'response.output_audio_transcript.delta','response_id':'same-private-id',
               'delta':'private transcript'}
        for authority,lease,expected in [
            (AuthorityDenied(),object(),'output_authorization_rejected'),
            (type('Authority',(),{'check':lambda self,*_: asyncio.sleep(0)})(),LeaseDenied(),'voice_lease_rejected'),
        ]:
            session=object.__new__(Session)
            session.closed=False;session.authority=authority;session.lease=lease
            session.scope=('tenant','actor','channel','conversation');session.fence=9
            session.response_inflight=True;session.provider_response_id='same-private-id'
            with self.assertRaises(ProviderResponseRejected) as caught:
                await session._receive_provider(event)
            fields=_provider_failure_fields('provider_event',event,caught.exception)
            self.assertEqual(fields,(expected,'provider_event','response.output_audio_transcript.delta',
                                     'none','none',True,True))

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
                elif event['type']=='conversation.item.create':
                    await ws.send_json({'type':'conversation.item.added','item':event['item']})
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

    async def admitted_policy(self, scope=None):
        scope=scope or (str(uuid.uuid4()),7,str(uuid.uuid4()),'synthetic-dialog')
        session_id=str(uuid.uuid4())
        class Authority:
            async def check(self,*_):return None
        class Lease:
            held=True
            async def claim(self,_):self.held=True;return 1
            async def assert_current(self,*_):
                if not self.held:raise ValueError('lease_not_current')
            async def release(self,*_):self.held=False;return True
        lease=Lease();policy=PilotAdmission(Authority(),lease,scope,session_id)
        await policy.admit(scope,session_id,1)
        return policy,lease

    async def test_real_aiohttp_frames_use_policy_and_one_claim(self):
        wire=_RealtimeWire(self.socket,OneUseGuard())
        snapshot={'existing_copilot_instructions':'Never send messages or perform external actions.',
                  'lines':[{'text':'синтетический mətn'}],'extra_context':{}}
        await wire.open(snapshot,tools=[],create_response=False,interrupt_response=False,transcription=None,reconnect=False)
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
        self.assertEqual(settings['max_output_tokens'],4096)
        self.assertFalse(vad['create_response']);self.assertFalse(vad['interrupt_response']);self.assertIsNone(vad['idle_timeout_ms'])
        self.assertNotIn('transcription',settings['audio']['input'])
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
        owner=type('Owner',(),{'guard':None})()
        with self.assertRaises(TenantLinearError):await _open_openai_socket(Forbidden(),owner)

    async def test_upgrade_completed_after_lease_loss_is_closed(self):
        policy,lease=await self.admitted_policy()
        owner=type('Owner',(),{'guard':policy,'_opening_session':None,'socket':None})()
        async def delayed_upgrade(*_):
            lease.held=False
            return self.ws
        with patch('tenant_operator_realtime_wire._new_owned_session',return_value=self.session),\
             patch('tenant_operator_realtime_wire._connect_websocket',side_effect=delayed_upgrade):
            with self.assertRaises(TenantLinearError):
                await _open_openai_socket('synthetic-key',owner)
        self.assertIsNotNone(owner.socket)
        self.assertTrue(self.ws.closed)
        self.assertTrue(self.session.closed)

    async def test_websocket_upgrade_diagnostic_keeps_status_but_redacts_body(self):
        class Owner:
            guard=object()
            _opening_session=None
            async def _close_opening_session(self):
                await self._opening_session.close()
        owner=Owner()
        request=aiohttp.RequestInfo(url=aiohttp.client_reqrep.URL('https://api.openai.com/v1/realtime'),
                                    method='GET',headers={},real_url=aiohttp.client_reqrep.URL('https://api.openai.com/v1/realtime'))
        failure=aiohttp.ClientResponseError(request,(),status=404,message='synthetic-secret-provider-body')
        with patch('tenant_operator_realtime_wire._require_live_admission',return_value=None),\
             patch('tenant_operator_realtime_wire._connect_websocket',side_effect=failure),\
             self.assertLogs('tenant_operator_realtime_wire',level='WARNING') as captured:
            with self.assertRaises(aiohttp.ClientResponseError):
                await _open_openai_socket('synthetic-api-key',owner)
        joined='\n'.join(captured.output)
        self.assertIn('stage=websocket_upgrade',joined)
        self.assertIn('status=404',joined)
        self.assertNotIn('synthetic-secret',joined)
        self.assertNotIn('synthetic-api-key',joined)

    async def test_provider_gate_fires_before_credential_loader(self):
        called=[]
        async def credential():called.append(True);return 'synthetic-key'
        provider=OpenAIRealtimeProvider(credential,OneUseGuard())
        with self.assertRaises(TenantLinearError):await provider.open({},tools=[],create_response=False,
                            interrupt_response=False,transcription=None,reconnect=False)
        self.assertEqual(called,[])

    async def test_composed_provider_protocol_over_loopback_with_test_connector(self):
        calls=[]
        async def credential():calls.append('credential');return 'synthetic-key-never-log'
        async def test_connector(value,_owner):
            self.assertEqual(value,'synthetic-key-never-log');calls.append('connector');return self.socket
        policy,_=await self.admitted_policy()
        provider=OpenAIRealtimeProvider(credential,policy)
        snapshot={'existing_copilot_instructions':'Never send messages or perform external actions.',
                  'lines':[],'extra_context':{}}
        with patch('tenant_operator_realtime_wire._open_openai_socket',test_connector):
            await provider.open(snapshot,tools=[],create_response=False,interrupt_response=False,transcription=None,reconnect=False)
            self.assertTrue(provider.ready);self.assertEqual(calls,['credential','connector'])
            self.assertNotIn('synthetic-key-never-log',json.dumps(self.received))
            await provider.close();self.assertTrue(self.ws.closed)

    async def test_provider_redacts_loader_and_readback_errors(self):
        async def bad_credential():raise ValueError('synthetic-secret-do-not-log')
        policy,_=await self.admitted_policy()
        provider=OpenAIRealtimeProvider(bad_credential,policy)
        with self.assertRaisesRegex(ValueError,'provider_credential_unavailable') as failure:
            await provider.open({},tools=[],create_response=False,interrupt_response=False,transcription=None,reconnect=False)
        self.assertNotIn('synthetic-secret',str(failure.exception))
        async def credential():return 'synthetic-secret-do-not-log'
        async def test_connector(_,_owner):return self.socket
        policy,_=await self.admitted_policy()
        provider=OpenAIRealtimeProvider(credential,policy)
        snapshot={'existing_copilot_instructions':'Never send messages.', 'lines':[],'extra_context':{}}
        with patch('tenant_operator_realtime_wire._open_openai_socket',test_connector),\
             patch.object(_RealtimeWire,'_verify',side_effect=ValueError('synthetic-secret-do-not-log')):
            with self.assertLogs('tenant_operator_realtime_wire',level='WARNING') as captured:
                with self.assertRaisesRegex(ValueError,'provider_open_unavailable') as failure:
                    await provider.open(snapshot,tools=[],create_response=False,interrupt_response=False,transcription=None,reconnect=False)
        self.assertNotIn('synthetic-secret',str(failure.exception));self.assertTrue(self.ws.closed)
        self.assertNotIn('synthetic-secret','\n'.join(captured.output))

    async def test_pilot_provider_session_runs_only_with_scoped_policy_and_loopback_peer(self):
        class Authority:
            async def check(self,*_):pass
        class Context:
            async def read_authorized(self,_):
                return {'existing_copilot_instructions':'Never send messages.','lines':[],'extra_context':{}}
            async def revalidate(self,_):return True
        class Lease:
            held=False
            async def claim(self,_):self.held=True;return 1
            async def assert_current(self,*_):assert self.held
            async def confirm_provider_open(self,*_):assert self.held;return True
            async def release(self,*_):self.held=False;return True
            async def release(self,*_):self.held=False;return True
        class Ledger:
            async def reserve_session(self,scope,session_id):
                return {'state':'reserved','scope':scope,'session_id':session_id,'reserved_microusd':5_000_000}
            async def reserve(self,s):return {**s,'state':'reserved','reserved_microusd':MINIMUM_HOLD}
            async def claim(self,s):
                return {**s,'state':'claimed','reserved_microusd':MINIMUM_HOLD,'model':MODEL,'quote_version':QUOTE,
                        'tenant_reservation_id':str(uuid.uuid4())}
            async def mark_unknown(self,s):return {**s,'state':'unknown'}
        class Client:
            def __init__(self):self.events=[]
            async def send(self,event):self.events.append(event)
        async def credential():return 'synthetic-key'
        async def connector(_value,_owner):return self.socket
        authority=Authority();provider=None;lease=Lease();client=Client()
        scope=(str(uuid.uuid4()),7,str(uuid.uuid4()),'synthetic')
        session_id=str(uuid.uuid4());policy=PilotAdmission(authority,lease,scope,session_id)
        provider=OpenAIRealtimeProvider(credential,policy)
        deps=dict(authority=authority,ledger=None,lease=lease,context=Context(),provider=provider,client=client,
                  pilot_policy=policy,session_id=session_id)
        # The loopback peer is synthetic; only the test substitutes the network
        # connector, while the production policy still requires the exact pilot.
        with patch('tenant_operator_realtime_wire._open_openai_socket',connector):
            session=Session(scope,**deps)
            try:
                await session.start()
                self.assertFalse(session.greeting_pending)
                self.assertFalse(any(event.get('type')=='response.create' for event in self.received),
                                 'voice waits for the employee instead of spending a greeting response')
                await session.receive_client({'type':'audio','pcm':bytes(480)})
                self.assertEqual(session.input_bytes,480,'microphone audio is accepted immediately after ready')
                await asyncio.sleep(.01)
                self.assertTrue(any(event.get('type')=='input_audio_buffer.append' for event in self.received))
                # Realtime may report a created message item separately from
                # the added/done notifications. Accept it only under the same
                # strict message-role checks as the other item events.
                await session.receive_provider({'type':'conversation.item.created',
                    'item':{'id':'synthetic-context','type':'message','role':'user','content':[]}})
                await session.receive_provider({'type':'input_audio_buffer.speech_started'})
                await session.receive_client({'type':'audio','pcm':bytes(480)})
                await session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
                await session.receive_provider({'type':'input_audio_buffer.committed','item_id':'input-1'})
                await session.receive_provider(await asyncio.wait_for(provider.receive(),1))
                self.assertTrue(session.response_inflight)
                self.assertTrue(await session.end('test'));self.assertFalse(lease.held)
                self.assertEqual(client.events[-1]['type'],'ended')
            finally:await session.end('cleanup')

    async def test_uncertain_provider_open_keeps_lease_after_start_timeout(self):
        entered=asyncio.Event();cancel_seen=asyncio.Event();finish=asyncio.Event()
        class Authority:
            async def check(self,*_):return None
        class Context:
            async def read_authorized(self,_):
                return {'existing_copilot_instructions':'Never send messages or perform external actions.',
                        'lines':[],'extra_context':{}}
            async def revalidate(self,_):return True
        class Lease:
            def __init__(self):self.held=False;self.releases=0
            async def claim(self,_):self.held=True;return 1
            async def assert_current(self,*_):
                if not self.held:raise ValueError('lease_not_current')
            async def release(self,*_):self.releases+=1;self.held=False;return True
        class Provider:
            mock_only=True
            def bind_pilot_claim(self,*_):return None
            async def open(self,*_,**__):
                entered.set()
                try:await finish.wait()
                except asyncio.CancelledError:
                    cancel_seen.set()
                    # Model an open path that does not stop immediately on
                    # cancellation. Session.start must retain its lease.
                    await finish.wait()
            async def close(self):return None
        class Client:
            async def send(self,*_):return None
        authority=Authority();lease=Lease();scope=(str(uuid.uuid4()),7,str(uuid.uuid4()),'synthetic')
        session_id=str(uuid.uuid4());policy=PilotAdmission(authority,lease,scope,session_id)
        session=Session(scope,authority=authority,ledger=None,lease=lease,context=Context(),provider=Provider(),
                        client=Client(),pilot_policy=policy,session_id=session_id)
        with patch('tenant_operator_voice_session.PROVIDER_OPEN_DEADLINE',.01):
            with self.assertRaisesRegex(Refused,'provider_open_timeout'):
                await session.start()
        await asyncio.wait_for(entered.wait(),.2)
        await asyncio.wait_for(cancel_seen.wait(),.2)
        self.assertTrue(lease.held)
        self.assertEqual(lease.releases,0)
        finish.set()
        done,_=await asyncio.wait({session._open_task},timeout=.2)
        self.assertIn(session._open_task,done)
        await asyncio.gather(session._open_task,return_exceptions=True)
        self.assertTrue(lease.held)
        self.assertEqual(lease.releases,0)

    async def test_owned_session_refuses_redirect_before_second_request(self):
        visited=[]
        async def destination(_request):
            visited.append('visited');return web.Response(text='not a websocket')
        destination_app=web.Application();destination_app.router.add_get('/target',destination)
        target_runner=web.AppRunner(destination_app);await target_runner.setup()
        target_site=web.TCPSite(target_runner,'127.0.0.1',0);await target_site.start()
        target_port=target_site._server.sockets[0].getsockname()[1]
        async def redirect(_request):raise web.HTTPFound(f'http://127.0.0.1:{target_port}/target')
        source_app=web.Application();source_app.router.add_get('/redirect',redirect)
        source_runner=web.AppRunner(source_app);await source_runner.setup()
        source_site=web.TCPSite(source_runner,'127.0.0.1',0);await source_site.start()
        source_port=source_site._server.sockets[0].getsockname()[1]
        try:
            async with _new_owned_session() as owned:
                with self.assertRaises(RedirectRefused):
                    await _connect_websocket(owned,f'http://127.0.0.1:{source_port}/redirect',{'Authorization':'Bearer synthetic-only'})
            self.assertEqual(visited,[])
        finally:
            await source_runner.cleanup();await target_runner.cleanup()

    async def test_stalled_upgrade_has_outer_deadline(self):
        accepted=asyncio.Event();release=asyncio.Event()
        async def stalled(reader,writer):
            accepted.set()
            try:await release.wait()
            finally:writer.close();await writer.wait_closed()
        server=await asyncio.start_server(stalled,'127.0.0.1',0)
        port=server.sockets[0].getsockname()[1]
        try:
            async with _new_owned_session() as owned:
                with patch('tenant_operator_realtime_wire.CONNECT_DEADLINE',.05):
                    with self.assertRaises(asyncio.TimeoutError):
                        await _connect_websocket(owned,f'http://127.0.0.1:{port}/stall',{})
                    self.assertTrue(accepted.is_set())
        finally:
            release.set();server.close();await server.wait_closed()

    async def test_peer_disconnect_fails_closed(self):
        await self.socket.receive_json()
        await self.server_socket.close(code=4001,message=b'synthetic-private-close-reason')
        with self.assertRaises(SafeRealtimeFailure) as caught:
            await asyncio.wait_for(self.socket.receive_json(),1)
        self.assertEqual(caught.exception.diagnostic_code,'websocket_closed')
        fields=_provider_failure_fields('provider_receive',None,caught.exception)
        self.assertEqual(fields[0:3],('websocket_closed','provider_receive','unavailable'))
        self.assertEqual(fields[3],4001)
        self.assertIn(fields[4],('none','timeout','aiohttp_client_error','socket_error','unknown'))
        self.assertNotIn('synthetic-private-close-reason',repr(fields))

    async def test_unexpected_binary_provider_frame_is_not_misreported_as_close(self):
        await self.socket.receive_json()
        await self.server_socket.send_bytes(b'private binary payload')
        with self.assertRaises(SafeRealtimeFailure) as caught:
            await self.socket.receive_json()
        self.assertEqual(caught.exception.diagnostic_code,'websocket_nontext_frame')

    async def test_provider_error_event_exposes_only_allowlisted_code(self):
        await self.socket.receive_json()
        await self.server_socket.send_json({'type':'error','error':{'code':'model_not_found',
            'message':'synthetic secret details'}})
        with self.assertRaises(SafeRealtimeFailure) as caught:
            await self.socket.receive_json()
        fields=_provider_failure_fields('provider_receive',None,caught.exception)
        self.assertEqual(fields,('model_not_found','provider_receive','error','none','none',False,False))
        self.assertNotIn('synthetic secret',repr(fields))

    async def test_malformed_provider_json_has_distinct_safe_category(self):
        await self.socket.receive_json()
        await self.server_socket.send_str('{malformed synthetic secret}')
        with self.assertRaises(SafeRealtimeFailure) as caught:
            await self.socket.receive_json()
        self.assertEqual(caught.exception.diagnostic_code,'provider_json_invalid')

    async def test_owned_socket_closes_its_client_session(self):
        port=self.site._server.sockets[0].getsockname()[1]
        owned=_new_owned_session()
        ws=await _connect_websocket(owned,f'http://127.0.0.1:{port}/local',{})
        wrapped=AiohttpRealtimeSocket(ws,owned_session=owned)
        try:
            self.assertEqual((await wrapped.receive_json())['type'],'session.created')
            await wrapped.close()
            self.assertTrue(ws.closed);self.assertTrue(owned.closed)
        finally:
            await owned.close()

    async def test_repeated_cancellation_does_not_cancel_owned_cleanup(self):
        ws_entered=asyncio.Event();ws_release=asyncio.Event();session_entered=asyncio.Event();session_release=asyncio.Event()
        class SlowWS:
            closed=False
            close_code=1000
            def exception(self):return None
            async def close(self):
                ws_entered.set();await ws_release.wait();self.closed=True
        class SlowSession:
            closed=False
            async def close(self):
                session_entered.set();await session_release.wait();self.closed=True
        slow_ws=SlowWS();slow_session=SlowSession();wrapped=AiohttpRealtimeSocket(slow_ws,owned_session=slow_session)
        first=asyncio.create_task(wrapped.close());await ws_entered.wait();first.cancel()
        with self.assertRaises(asyncio.CancelledError):await first
        ws_release.set();await session_entered.wait()
        second=asyncio.create_task(wrapped.close());await asyncio.sleep(0);second.cancel()
        with self.assertRaises(asyncio.CancelledError):await second
        self.assertFalse(slow_session.closed);self.assertFalse(wrapped._close_task.done())
        session_release.set();await wrapped.close()
        self.assertTrue(slow_ws.closed);self.assertTrue(slow_session.closed)

    async def test_abnormal_or_unacknowledged_close_is_not_confirmed(self):
        class BrokenWS:
            closed=False
            def __init__(self,code,error=None):self.close_code=code;self.error=error
            def exception(self):return self.error
            async def close(self):self.closed=True;return True
        for code,error in [(1006,None),(None,None),(1000,ConnectionError('synthetic'))]:
            wrapped=AiohttpRealtimeSocket(BrokenWS(code,error))
            with self.assertRaisesRegex(ValueError,'wire_close_unconfirmed'):
                await wrapped.close()

    async def test_real_local_peer_abort_is_not_a_confirmed_close(self):
        async def abort(request):
            ws=web.WebSocketResponse();await ws.prepare(request)
            request.transport.abort()
            return ws
        app=web.Application();app.router.add_get('/abort',abort)
        runner=web.AppRunner(app);await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        client=aiohttp.ClientSession()
        try:
            port=site._server.sockets[0].getsockname()[1]
            ws=await client.ws_connect(f'http://127.0.0.1:{port}/abort')
            await asyncio.wait_for(ws.receive(),1)
            with self.assertRaisesRegex(ValueError,'wire_close_unconfirmed'):
                await AiohttpRealtimeSocket(ws).close()
        finally:
            await client.close();await runner.cleanup()

    async def test_repeated_cancel_during_connect_cleanup_remains_observable(self):
        entered=asyncio.Event();release=asyncio.Event()
        class SlowSession:
            closed=False
            async def close(self):entered.set();await release.wait();self.closed=True
        owned=SlowSession()
        async def cancelled_connect(*_args):raise asyncio.CancelledError()
        async def credential():return 'synthetic-key'
        policy,lease=await self.admitted_policy()
        provider=OpenAIRealtimeProvider(credential,policy)
        with patch('tenant_operator_realtime_wire._new_owned_session',return_value=owned),\
             patch('tenant_operator_realtime_wire._connect_websocket',cancelled_connect):
            task=asyncio.create_task(provider.open({},tools=[],create_response=False,
                                         interrupt_response=False,transcription=None,reconnect=False))
            await entered.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            closing=asyncio.create_task(provider.close());await asyncio.sleep(0)
            self.assertFalse(closing.done());self.assertFalse(owned.closed)
            release.set();await closing;self.assertTrue(owned.closed)

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
            async def claim(self,_):self.held=True;return 1
            async def assert_current(self,*_):assert self.held
            async def confirm_provider_open(self,*_):assert self.held;return True
            async def release(self,*_):self.held=False;return True
            async def release(self,*_):self.held=False;return True
        class Ledger:
            events=[]
            async def reserve_session(self,scope,session_id):
                return {'state':'reserved','scope':scope,'session_id':session_id,'reserved_microusd':5_000_000}
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
            await session.receive_provider({'type':'input_audio_buffer.speech_started'})
            await session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
            await session.receive_provider({'type':'input_audio_buffer.committed','item_id':'input-1'})
            await session.receive_provider({'type':'conversation.item.input_audio_transcription.delta','item_id':'input-1',
                                            'content_index':0,'delta':'synthetic '})
            await session.receive_provider({'type':'conversation.item.input_audio_transcription.completed','item_id':'input-1',
                                            'content_index':0,'transcript':'synthetic input','usage':{'type':'duration','seconds':1}})
            await session.receive_provider(await asyncio.wait_for(provider.receive(),1))
            prior=list(client.events)
            # Generated transcripts are not proven heard and are neither
            # exposed nor stored by the currently blocked history path.
            await session.receive_provider({'type':'response.output_item.added','response_id':'synthetic-response',
                                            'item':{'id':'audio-1','type':'message','role':'assistant'}})
            await session.receive_provider({'type':'response.output_audio_transcript.done','content_index':0,
                                            'item_id':'audio-1','response_id':'synthetic-response',
                                            'transcript':'synthetic unverified text'})
            self.assertEqual(client.events,prior)
            # Provider deltas may be larger than one browser frame; the
            # gateway must split them without losing audio or exceeding the
            # browser's per-message contract.
            await self.server_socket.send_json({'type':'response.output_audio.delta','response_id':'synthetic-response',
                                               'item_id':'audio-1','content_index':0,
                                               'delta':base64.b64encode(bytes(19200)).decode()})
            await session.receive_provider(await asyncio.wait_for(provider.receive(),1))
            self.assertEqual([len(e['pcm']) for e in client.events[-2:]],[9600,9600])
            await session.receive_provider({'type':'input_audio_buffer.speech_started'})
            self.assertEqual(client.events[-1],{'type':'interrupt','item_id':'audio-1'})
            await session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
            await session.receive_provider({'type':'input_audio_buffer.committed','item_id':'input-2'})
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
