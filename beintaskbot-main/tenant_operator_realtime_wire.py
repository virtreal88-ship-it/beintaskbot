"""Server-owned Realtime protocol for the single-account Copilot pilot."""
import asyncio
import base64
import binascii
import hashlib
import json
import logging
import re
import uuid
from collections import deque
import aiohttp
from tenant_linear_policy import TenantLinearError

MODEL='gpt-realtime-2.1-mini'
# The session API accepts 1..4096 or "inf", regardless of the larger model
# maximum. Keep the larger existing hold until a full billing bound is proven.
OUTPUT_LIMIT=4096
QUOTE='realtime-2.1-mini-gpt-transcribe-2026-11-03-v1'
MINIMUM_HOLD=2_130_018
OPENAI_URL='wss://api.openai.com/v1/realtime'
MAX_WIRE_MESSAGE=262144
CONNECT_DEADLINE=4
CLOSE_DEADLINE=3
CANCEL_RACE_DEADLINE=2
_LOG=logging.getLogger(__name__)

# Only stable provider machine codes that are useful to the operator may reach
# logs. Unknown codes are deliberately collapsed to ``provider_error``.
SAFE_PROVIDER_ERROR_CODES=frozenset({
    'invalid_api_key','model_not_found','rate_limit_exceeded','server_error',
    'invalid_request_error','session_expired','input_audio_buffer_commit_empty',
    'input_audio_buffer_empty','audio_unintelligible','input_audio_transcription_failed',
})


class SafeRealtimeFailure(ValueError):
    """Internal failure with a fixed, non-sensitive diagnostic category."""
    def __init__(self, diagnostic_code, *, event_type=None, close_code=None, transport_kind='none'):
        self.diagnostic_code=diagnostic_code
        self.diagnostic_event_type=event_type
        self.diagnostic_close_code=close_code
        self.diagnostic_transport_kind=transport_kind
        super().__init__(diagnostic_code)


def _safe_transport_kind(error):
    if error is None:return 'none'
    if isinstance(error,asyncio.TimeoutError):return 'timeout'
    if isinstance(error,aiohttp.ClientError):return 'aiohttp_client_error'
    if isinstance(error,OSError):return 'socket_error'
    return 'unknown'


class RedirectRefused(ValueError):
    pass


async def _deny_redirect(_session,_trace_context,_params):
    # aiohttp emits this before making the redirected request.
    raise RedirectRefused('realtime_redirect_forbidden')


def _new_owned_session():
    trace=aiohttp.TraceConfig()
    trace.on_request_redirect.append(_deny_redirect)
    return aiohttp.ClientSession(trust_env=False,trace_configs=[trace],
        timeout=aiohttp.ClientTimeout(total=CONNECT_DEADLINE,connect=3,sock_connect=3))


async def _connect_websocket(session,url,headers):
    # Outer deadline includes the complete HTTP upgrade, not only TCP setup.
    return await asyncio.wait_for(session.ws_connect(url,headers=headers,timeout=CLOSE_DEADLINE,
                                  heartbeat=20,max_msg_size=MAX_WIRE_MESSAGE),CONNECT_DEADLINE)


def _unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate_json_key')
        result[key]=value
    return result


async def _require_live_admission(guard,scope=None,session_id=None):
    """Recheck the same owner authorization and lease admitted by Session.start."""
    from tenant_operator_voice_pilot import PilotAdmission
    if type(guard) is not PilotAdmission:
        raise TenantLinearError('Голосовой режим пока недоступен.',503)
    try:
        await guard.assert_live(guard.scope if scope is None else scope,
                                guard.session_id if session_id is None else session_id)
    except Exception:
        raise TenantLinearError('Допуск или lease голосового сеанса недействителен.',503) from None


class AiohttpRealtimeSocket:
    """Bounded JSON adapter around an already-open aiohttp WebSocket."""
    def __init__(self,ws,owned_session=None):
        self.ws=ws;self.owned_session=owned_session;self._close_task=None

    async def send_json(self,event):
        if not isinstance(event,dict):raise ValueError('wire_event_invalid')
        raw=json.dumps(event,ensure_ascii=False,separators=(',',':'),allow_nan=False)
        if len(raw.encode('utf-8'))>MAX_WIRE_MESSAGE:raise ValueError('wire_message_too_large')
        await self.ws.send_str(raw)

    async def receive_json(self):
        message=await self.ws.receive()
        if message.type in (aiohttp.WSMsgType.CLOSE,aiohttp.WSMsgType.CLOSED,aiohttp.WSMsgType.CLOSING):
            raise SafeRealtimeFailure('websocket_closed',close_code=self.ws.close_code,
                                      transport_kind=_safe_transport_kind(self.ws.exception()))
        if message.type==aiohttp.WSMsgType.ERROR:
            raise SafeRealtimeFailure('websocket_error',close_code=self.ws.close_code,
                                      transport_kind=_safe_transport_kind(self.ws.exception()))
        if message.type!=aiohttp.WSMsgType.TEXT:raise SafeRealtimeFailure('websocket_nontext_frame')
        if len(message.data.encode('utf-8'))>MAX_WIRE_MESSAGE:raise SafeRealtimeFailure('provider_message_too_large')
        try:
            data=json.loads(message.data,object_pairs_hook=_unique_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError,TypeError,RecursionError):raise SafeRealtimeFailure('provider_json_invalid') from None
        if not isinstance(data,dict):raise SafeRealtimeFailure('provider_event_invalid')
        if data.get('type')=='error':
            error=data.get('error')
            code=error.get('code') if isinstance(error,dict) else None
            event_id=error.get('event_id') if isinstance(error,dict) else None
            if code=='response_cancel_not_active' and isinstance(event_id,str) and 0<len(event_id)<=128:
                # Correlation stays inside the transport; no provider text is retained.
                result={'type':'_cancel_not_active','event_id':event_id}
                if 'session_id' in data:result['session_id']=data['session_id']
                return result
            safe_code=code if isinstance(code,str) and code in SAFE_PROVIDER_ERROR_CODES else 'provider_error'
            raise SafeRealtimeFailure(safe_code,event_type='error')
        return data

    async def close(self):
        # Multiple callers/cancellations share one cleanup. A caller timing
        # out must not cancel cleanup or claim that the lease is releasable.
        if self._close_task is None:self._close_task=asyncio.create_task(self._close_owned())
        return await asyncio.shield(self._close_task)

    async def _close_owned(self):
        try:
            await asyncio.wait_for(self.ws.close(),CLOSE_DEADLINE)
        finally:
            if self.owned_session is not None:
                await asyncio.wait_for(self.owned_session.close(),CLOSE_DEADLINE)
        # aiohttp marks a socket closed even when the peer never replies and
        # close() internally catches an error. Only a valid peer close frame,
        # with no recorded transport exception, confirms clean shutdown.
        code=self.ws.close_code
        valid_code=(isinstance(code,int) and not isinstance(code,bool) and
                    ((1000<=code<=1014 and code not in (1004,1005,1006)) or 3000<=code<=4999))
        if not self.ws.closed or not valid_code or self.ws.exception() is not None:
            raise ValueError('wire_close_unconfirmed')
        if self.owned_session is not None and not self.owned_session.closed:raise ValueError('wire_session_close_unconfirmed')


async def _open_openai_socket(api_key,owner):
    """Server-only connector scoped to the configured single-user pilot."""
    await _require_live_admission(getattr(owner,'guard',None))
    session=_new_owned_session()
    owner._opening_session=session
    try:
        ws=await _connect_websocket(session,OPENAI_URL+'?model='+MODEL,{'Authorization':'Bearer '+api_key})
    except BaseException as error:
        if not isinstance(error,asyncio.CancelledError):
            # The handshake body may contain provider details. Preserve only
            # the exception class and numeric HTTP status needed to diagnose access.
            status=getattr(error,'status',None)
            if type(status) is not int or not 100<=status<=599:status='none'
            kind=type(error).__name__
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,63}',kind):kind='UnknownError'
            _LOG.warning('realtime_provider_connect_failed stage=websocket_upgrade error=%s status=%s',kind,status)
        await owner._close_opening_session()
        raise
    socket=AiohttpRealtimeSocket(ws,owned_session=session)
    owner.socket=socket
    # A timed-out or stalled network upgrade can complete after this worker
    # has lost its distributed lease. Recheck after the upgrade, before the
    # provider session is exposed to the rest of the startup path.
    try:
        await _require_live_admission(getattr(owner,'guard',None))
    except BaseException:
        try:await socket.close()
        except BaseException:raise ValueError('provider_cleanup_unconfirmed') from None
        raise
    return socket


def create_production_provider(*args,**kwargs):
    # Never accept loader, guard or endpoint from an HTTP request. Imported
    # lazily to avoid a dependency cycle; types are server-owned implementations.
    raise TenantLinearError('Платный провайдер доступен только через пилотный допуск.',503)
    from tenant_operator_voice_orchestration import ScopedDispatchGuard, ServerCredentialLoader
    if kwargs or len(args)!=2 or type(args[0]) is not ScopedDispatchGuard or type(args[1]) is not ServerCredentialLoader:
        raise ValueError('trusted_provider_dependencies_required')
    return OpenAIRealtimeProvider(args[1].load,args[0])


def create_pilot_provider(policy,credential_loader):
    from tenant_operator_voice_pilot import PilotAdmission
    from tenant_operator_voice_orchestration import ServerCredentialLoader
    from tenant_ai_business_analytics_voice import AnalyticsVoiceCredentialLoader
    if (type(policy) is not PilotAdmission or
        type(credential_loader) not in (ServerCredentialLoader,AnalyticsVoiceCredentialLoader)):
        raise ValueError('trusted_provider_dependencies_required')
    if policy.authority is not credential_loader.authority:
        raise ValueError('pilot_authority_mismatch')
    return OpenAIRealtimeProvider(credential_loader.load,policy)


class _RealtimeWire:
    def __init__(self,socket,claim_guard,*,output_modality='audio'):
        if not callable(getattr(claim_guard,'consume',None)):
            raise ValueError('claim_guard_required')
        if output_modality not in ('audio','text'):raise ValueError('output_modality_invalid')
        self.socket=socket;self.guard=claim_guard;self.ready=False;self.bound=None;self.seen=set();self.config=None;self.session_id=None;self.conversation_id=None
        self.output_modality=output_modality
        self.duplex=False;self.context_id=None;self._prepared_response=None;self._cancel_events={};self._completed_responses=set();self._pending_cancel_races={}
        self.tool_names=frozenset();self._tool_calls={};self._pending_tool_output=None;self._confirmed_tool_outputs={}

    def _note_conversation(self,event,session_id=None):
        conversation=event.get('conversation')
        value=conversation.get('id') if isinstance(conversation,dict) else None
        expected=session_id if session_id is not None else self.session_id
        if (self.conversation_id is not None or not isinstance(value,str) or not value or len(value)>128 or
            ('session_id' in event and event['session_id']!=expected)):
            raise ValueError('provider_conversation_invalid')
        self.conversation_id=value

    async def open(self,snapshot,*,tools,create_response,interrupt_response,transcription,reconnect,duplex=False):
        analytics_tools=False
        if tools:
            from tenant_operator_voice_analytics import realtime_definitions
            analytics_tools=(tools==realtime_definitions())
        if (type(duplex) is not bool or (tools!=[] and not analytics_tools) or
            (analytics_tools and not duplex) or create_response is not False or interrupt_response is not duplex or transcription is not None
            or reconnect is not False or not isinstance(snapshot,dict)
            or set(snapshot)!={'existing_copilot_instructions','lines','extra_context'}):
            raise ValueError('unsafe_realtime_configuration')
        instructions=snapshot['existing_copilot_instructions']
        if not isinstance(instructions,str) or 'Never send messages' not in instructions:
            raise ValueError('copilot_instructions_required')
        data=json.dumps({'lines':snapshot['lines'],'extra_context':snapshot['extra_context']},ensure_ascii=False,separators=(',',':'))
        if len(instructions.encode())>120000 or len(data.encode())>120000:
            raise ValueError('context_too_large')
        created=await self.socket.receive_json()
        if not isinstance(created,dict) or created.get('type')!='session.created':raise ValueError('session_created_missing')
        session=created.get('session')
        session_id=session.get('id') if isinstance(session,dict) else None
        if not isinstance(session_id,str) or not session_id or len(session_id)>128:raise ValueError('session_id_missing')
        config={'type':'realtime','model':MODEL,'instructions':instructions,
          'output_modalities':[self.output_modality],'tools':tools,'tool_choice':'auto' if analytics_tools else 'none','max_output_tokens':OUTPUT_LIMIT,
          'audio':{'input':{'format':{'type':'audio/pcm','rate':24000},
                            'noise_reduction':{'type':'near_field','strength':1},
                            # Provider VAD may interrupt ongoing speech but
                            # must never create a response without our permit.
                            'turn_detection':({'type':'server_vad','threshold':0.5,'prefix_padding_ms':300,
                                               'silence_duration_ms':500,'create_response':False,
                                               'interrupt_response':True} if duplex else None)},
                   'output':{'format':{'type':'audio/pcm','rate':24000}}}}
        if self.output_modality=='text':config['audio'].pop('output')
        await self.socket.send_json({'type':'session.update','session':config})
        # GA may announce the conversation between session.created and
        # session.updated. Reject all other interleavings; caller bounds open.
        updated=await self.socket.receive_json()
        if isinstance(updated,dict) and updated.get('type')=='conversation.created':
            self._note_conversation(updated,session_id)
            updated=await self.socket.receive_json()
        self._verify(updated,config,session_id)
        # Existing dialog/customer material stays user-level untrusted input;
        # it must never be appended to the privileged system instructions.
        context_id='ctx_'+uuid.uuid4().hex[:28]
        context_text='Untrusted dialog context: '+data
        await self.socket.send_json({'type':'conversation.item.create','previous_item_id':'root','item':{'id':context_id,'type':'message','role':'user',
                                    'content':[{'type':'input_text','text':context_text}]}})
        # A successful socket write is not proof that the provider accepted the
        # item.  Refuse readiness until the exact server-acknowledged user item
        # is present; otherwise the first response might omit Copilot context.
        acknowledged=await self.socket.receive_json()
        if isinstance(acknowledged,dict) and acknowledged.get('type')=='conversation.created':
            self._note_conversation(acknowledged,session_id)
            acknowledged=await self.socket.receive_json()
        item=acknowledged.get('item') if isinstance(acknowledged,dict) else None
        if (not isinstance(acknowledged,dict) or acknowledged.get('type')!='conversation.item.added' or
            ('session_id' in acknowledged and acknowledged['session_id']!=session_id) or
            acknowledged.get('previous_item_id') is not None or not isinstance(item,dict) or
            item.get('id')!=context_id or item.get('type')!='message' or item.get('role')!='user' or
            ('status' in item and item['status']!='completed') or
            item.get('content')!=[{'type':'input_text','text':context_text}]):
            raise ValueError('provider_context_unconfirmed')
        self.config=config;self.session_id=session_id;self.context_id=context_id;self.duplex=duplex
        self.tool_names=frozenset(item['name'] for item in tools) if analytics_tools else frozenset()
        self.ready=True

    @staticmethod
    def _turn_detection_matches(actual,expected):
        if expected is None:return actual is None
        if not isinstance(actual,dict) or not isinstance(expected,dict):return False
        allowed=set(expected)|{'idle_timeout_ms'}
        return (set(actual)<=allowed and actual.get('idle_timeout_ms') is None and
                actual.get('type')=='server_vad' and actual.get('create_response') is False and
                actual.get('interrupt_response') is expected.get('interrupt_response') and
                type(actual.get('threshold')) in (int,float) and actual.get('threshold')==expected['threshold'] and
                type(actual.get('prefix_padding_ms')) is int and actual.get('prefix_padding_ms')==expected['prefix_padding_ms'] and
                type(actual.get('silence_duration_ms')) is int and actual.get('silence_duration_ms')==expected['silence_duration_ms'])

    @staticmethod
    def _verify(event,expected,session_id):
        try:
            if event['type']!='session.updated':raise ValueError()
            s=event['session'];audio=s['audio'];incoming=audio['input']
            noise=incoming.get('noise_reduction')
            checks={
              'id':s.get('id')==session_id,'type':s.get('type')=='realtime','model':s.get('model')==MODEL,
              'output_modalities':s.get('output_modalities')==expected['output_modalities'],'tools':s.get('tools')==expected['tools'],
              'tool_choice':s.get('tool_choice')==expected['tool_choice'],'max_output_tokens':s.get('max_output_tokens')==OUTPUT_LIMIT,
              'instructions':s.get('instructions')==expected['instructions'],
              'audio.input.format':incoming.get('format')=={'type':'audio/pcm','rate':24000},
              'audio.input.noise_reduction.type':isinstance(noise,dict) and noise.get('type')=='near_field',
              'audio.input.noise_reduction.strength':(isinstance(noise,dict) and type(noise.get('strength')) in (int,float)
                                                       and noise.get('strength')==1),
              'audio.input.noise_reduction.fields':isinstance(noise,dict) and set(noise)<={'type','strength'},
              # OpenAI normalizes an omitted transcription config to null in
              # session.updated. Null means no separate transcription model.
              'audio.input.transcription':incoming.get('transcription') is None,
              'audio.input.turn_detection':_RealtimeWire._turn_detection_matches(incoming.get('turn_detection'),expected['audio']['input']['turn_detection']),
              'audio.output.format':(expected['output_modalities']==['text'] or
                                     audio['output'].get('format')=={'type':'audio/pcm','rate':24000}),
            }
            mismatch=[name for name,valid in checks.items() if not valid]
            if mismatch:
                _LOG.warning('realtime_session_readback_mismatch fields=%s',','.join(mismatch))
                raise ValueError()
        except (KeyError,TypeError,ValueError):raise ValueError('unsafe_session_readback') from None

    async def bind_claim(self,scope,receipt):
        if not self.ready or self.bound is not None or self._prepared_response is not None or not isinstance(scope,dict) or not isinstance(receipt,dict):
            raise ValueError('claim_not_available')
        required={'request_id','tenant_id','session_id','actor_id','channel_id','conversation_id','response_number','executor'}
        if set(scope)!=required or receipt.get('state')!='claimed' or any(str(receipt.get(k))!=str(v) for k,v in scope.items()):
            raise ValueError('claim_scope_invalid')
        if receipt.get('model')!=MODEL or receipt.get('quote_version')!=QUOTE or receipt.get('reserved_microusd')!=MINIMUM_HOLD:
            raise ValueError('claim_quote_invalid')
        try:uuid.UUID(str(receipt.get('tenant_reservation_id') or ''))
        except (ValueError,TypeError,AttributeError):raise ValueError('claim_tenant_hold_missing') from None
        if scope['request_id'] in self.seen:raise ValueError('claim_reused')
        self.bound=(dict(scope),dict(receipt))

    async def bind_pilot_claim(self,scope,receipt):
        # No financial reservation is represented by a pilot permit. This
        # path still requires the exact server-owned policy and one-use guard.
        from tenant_operator_voice_pilot import PilotAdmission
        if type(self.guard) is not PilotAdmission or not self.ready or self.bound is not None or self._prepared_response is not None:
            raise ValueError('pilot_claim_not_available')
        if not isinstance(scope,dict) or not isinstance(receipt,dict):raise ValueError('pilot_claim_invalid')
        expected=(scope.get('tenant_id'),scope.get('actor_id'),scope.get('channel_id'),scope.get('conversation_id'))
        if (expected!=self.guard.scope or scope.get('session_id')!=self.guard.session_id or
            receipt.get('session_id')!=self.guard.session_id or receipt.get('request_id')!=scope.get('request_id') or
            scope.get('request_id') in self.seen):raise ValueError('pilot_claim_scope_invalid')
        self.bound=(dict(scope),dict(receipt))

    async def send(self,event):
        return await self._send(event)

    async def send_function_call_output(self,call_id,output):
        """Return one result for an exact provider-issued analytics call."""
        offer=self._tool_calls.get(call_id) if isinstance(call_id,str) else None
        if (not self.ready or not self.tool_names or offer is None or offer.get('used') is True or
            self._pending_tool_output is not None or not isinstance(output,str) or
            len(output.encode('utf-8'))>24576):
            raise ValueError('analytics_tool_output_unavailable')
        offer['used']=True
        self._pending_tool_output={'call_id':call_id,'output':output}
        try:
            await self.socket.send_json({'type':'conversation.item.create','item':{
                'type':'function_call_output','call_id':call_id,'output':output}})
        except BaseException:
            self._pending_tool_output=None
            raise
        return True

    async def send_guarded_response(self,event,dispatch_guard):
        if not callable(dispatch_guard):raise ValueError('response_guard_required')
        return await self._send(event,dispatch_guard=dispatch_guard)

    async def prepare_guarded_response(self,event,dispatch_guard):
        if not callable(dispatch_guard):raise ValueError('response_guard_required')
        return await self._prepare_response(event,dispatch_guard)

    async def _prepare_response(self,event,dispatch_guard=None):
        if not self.ready or not isinstance(event,dict):raise ValueError('session_not_ready')
        if event.get('type')!='response.create' or self._prepared_response is not None:
            raise ValueError('response_prepare_invalid')
        if not self.bound:raise ValueError('claim_missing')
        from tenant_operator_voice_pilot import PilotAdmission
        if self.duplex and (dispatch_guard is not None or type(self.guard) is PilotAdmission):
            response=event.get('response')
            refs=response.get('input') if isinstance(response,dict) else None
            allowed_response_fields={'input'}
            if isinstance(response,dict) and response.get('tool_choice')=='none':
                allowed_response_fields.add('tool_choice')
            if (set(event)!={'type','response'} or not isinstance(response,dict) or set(response)!=allowed_response_fields or
                not isinstance(refs,list) or not 2<=len(refs)<=1024 or
                any(not isinstance(item,dict) or set(item)!={'type','id'} or
                    item.get('type')!='item_reference' or not isinstance(item.get('id'),str) or
                    not re.fullmatch(r'[A-Za-z0-9_-]{1,32}',item['id']) for item in refs) or
                refs[0]['id']!=self.context_id or len({item['id'] for item in refs})!=len(refs)):
                raise ValueError('response_context_invalid')
        elif set(event)!={'type'}:
            raise ValueError('response_context_invalid')
        scope,receipt=self.bound;self.bound=None
        if type(self.guard) is PilotAdmission:
            admitted_scope=(scope['tenant_id'],scope['actor_id'],scope['channel_id'],scope['conversation_id'])
            consumed=await self.guard.consume(admitted_scope,receipt)
        else:
            consumed=await self.guard.consume(scope,receipt)
        if consumed is not True:raise ValueError('claim_not_consumed')
        self.seen.add(scope['request_id'])
        if not self.ready or (dispatch_guard is not None and not dispatch_guard()):return None
        token=object()
        self._prepared_response=(token,event)
        return token

    def discard_prepared_response(self,token):
        if self._prepared_response is not None and self._prepared_response[0] is token:
            self._prepared_response=None

    async def send_prepared_response(self,token,dispatch_guard=None):
        if self._prepared_response is None or self._prepared_response[0] is not token:
            if not self.ready:return False
            raise ValueError('response_prepare_invalid')
        _,event=self._prepared_response;self._prepared_response=None
        if not self.ready or (dispatch_guard is not None and not dispatch_guard()):return False
        await self.socket.send_json(event)
        return True

    async def _send(self,event,dispatch_guard=None):
        if not self.ready or not isinstance(event,dict):raise ValueError('session_not_ready')
        kind=event.get('type')
        if kind=='response.create':
            token=await self._prepare_response(event,dispatch_guard)
            if token is None:return False
            return await self.send_prepared_response(token,dispatch_guard)
        if kind=='input_audio_buffer.append' and set(event)=={'type','audio'}:
            try:audio=base64.b64decode(event['audio'],validate=True)
            except (ValueError,TypeError,binascii.Error):raise ValueError('audio_invalid') from None
            if not audio or len(audio)>9600 or len(audio)%2:raise ValueError('audio_invalid')
        elif kind=='input_audio_buffer.commit' and set(event)=={'type'}:pass
        elif kind=='input_audio_buffer.clear' and set(event)=={'type'}:pass
        elif kind=='response.cancel' and set(event) in ({'type'},{'type','response_id'}):
            if 'response_id' in event and (not isinstance(event['response_id'],str) or not event['response_id'] or len(event['response_id'])>128):
                raise ValueError('response_id_invalid')
            if self.duplex:
                if 'response_id' not in event:raise ValueError('cancel_response_id_required')
                event=dict(event);event_id='cancel_'+uuid.uuid4().hex
                event['event_id']=event_id;self._cancel_events[event_id]=event['response_id']
        elif kind=='conversation.item.truncate' and set(event)=={'type','item_id','content_index','audio_end_ms'}:
            # The server bounds this offset against the exact PCM bytes it
            # sent for the active item. Preserve the same finite ceiling here
            # while allowing push-to-talk to retain the portion actually
            # played before the user interrupted.
            if (not isinstance(event['item_id'],str) or not event['item_id'] or len(event['item_id'])>128 or
                event['content_index']!=0 or type(event['audio_end_ms']) is not int or
                not 0<=event['audio_end_ms']<=100_000):
                raise ValueError('truncate_invalid')
        else:raise ValueError('provider_event_forbidden')
        await self.socket.send_json(event)
        return True

    async def receive(self):
        if not self.ready:raise ValueError('session_not_ready')
        while True:
            if self._pending_cancel_races:
                remaining=min(deadline for _,deadline in self._pending_cancel_races.values())-asyncio.get_running_loop().time()
                if remaining<=0:raise SafeRealtimeFailure('provider_error',event_type='error')
                try:event=await asyncio.wait_for(self.socket.receive_json(),remaining)
                except asyncio.TimeoutError:raise SafeRealtimeFailure('provider_error',event_type='error') from None
            else:event=await self.socket.receive_json()
            if not isinstance(event,dict):raise ValueError('provider_event_invalid')
            if 'session_id' in event and event['session_id']!=self.session_id:
                self.ready=False;raise ValueError('provider_session_changed')
            if event.get('type')!='_cancel_not_active':break
            event_id=event.get('event_id')
            if not isinstance(event_id,str):raise SafeRealtimeFailure('provider_error',event_type='error')
            response_id=self._cancel_events.get(event_id)
            if response_id is None or event_id in self._pending_cancel_races:
                raise SafeRealtimeFailure('provider_error',event_type='error')
            if response_id in self._completed_responses:
                self._cancel_events.pop(event_id,None)
                return {'type':'response_cancel_noop'}
            # The provider may report this error before response.done reaches
            # us. Keep reading in order, with a hard deadline; never infer a
            # successful completion from the error alone.
            self._pending_cancel_races[event_id]=(response_id,asyncio.get_running_loop().time()+CANCEL_RACE_DEADLINE)
        if event.get('type')=='response.done':
            response=event.get('response');response_id=response.get('id') if isinstance(response,dict) else None
            if isinstance(response_id,str) and 0<len(response_id)<=128 and (
                    self.duplex or response_id in self._cancel_events.values()):
                if response_id not in self._completed_responses and len(self._completed_responses)>=256:
                    raise SafeRealtimeFailure('provider_error',event_type='response.done')
                self._completed_responses.add(response_id)
                for event_id,(pending_response,_) in list(self._pending_cancel_races.items()):
                    if pending_response==response_id:
                        self._pending_cancel_races.pop(event_id,None);self._cancel_events.pop(event_id,None)
            output=response.get('output') if isinstance(response,dict) else None
            function_items=[item for item in output if isinstance(item,dict) and item.get('type')=='function_call'] if isinstance(output,list) else []
            if function_items:
                item=function_items[0]
                item_id=item.get('id');call_id=item.get('call_id');name=item.get('name');arguments=item.get('arguments')
                if (not self.tool_names or len(output)!=1 or len(function_items)!=1 or
                    not isinstance(item_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}',item_id) or
                    not isinstance(call_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',call_id) or
                    name not in self.tool_names or not isinstance(arguments,str) or len(arguments.encode('utf-8'))>8192 or
                    call_id in self._tool_calls or len(self._tool_calls)>=6):
                    self.ready=False
                    raise ValueError('provider_function_call_invalid')
                self._tool_calls[call_id]={'item_id':item_id,'name':name,'arguments':arguments,'used':False}
        if event.get('type') in {'session.created','session.updated'}:
            # No settings changes after the attested startup are permitted.
            self.ready=False
            raise ValueError('provider_configuration_changed')
        if event.get('type')=='conversation.created':
            try:self._note_conversation(event)
            except ValueError:self.ready=False;raise
        if event.get('type') in ('conversation.item.added','conversation.item.created','conversation.item.done'):
            item=event.get('item')
            if isinstance(item,dict) and item.get('type')=='function_call_output':
                expected=self._pending_tool_output
                item_id=item.get('id')
                valid_id=isinstance(item_id,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,32}',item_id)
                confirmed=self._confirmed_tool_outputs.get(item_id) if valid_id else None
                if confirmed is not None and item.get('call_id')==confirmed['call_id'] and item.get('output')==confirmed['output']:
                    pass
                elif (expected is None or item.get('call_id')!=expected['call_id'] or
                    item.get('output')!=expected['output'] or not valid_id):
                    self.ready=False
                    raise ValueError('provider_function_output_ack_invalid')
                else:
                    self._confirmed_tool_outputs[item_id]={'call_id':expected['call_id'],'output':expected['output']}
                    self._pending_tool_output=None
        return event

    async def close(self):
        self.ready=False;self.bound=None;self.context_id=None;self._prepared_response=None;self._cancel_events.clear();self._completed_responses.clear();self._pending_cancel_races.clear()
        self._tool_calls.clear();self._pending_tool_output=None;self._confirmed_tool_outputs.clear();self.tool_names=frozenset()
        await self.socket.close()


class SyntheticRealtimeWire(_RealtimeWire):
    mock_only=True

    def __init__(self,socket,claim_guard):
        if getattr(socket,'synthetic_only',None) is not True:
            raise ValueError('synthetic_socket_and_guard_required')
        super().__init__(socket,claim_guard)


class OpenAIRealtimeProvider(_RealtimeWire):
    """Server-only provider composition for the scoped no-tools pilot."""
    mock_only=False

    def __init__(self,credential_loader,claim_guard,*,output_modality='audio'):
        if not callable(credential_loader):raise ValueError('credential_loader_required')
        if output_modality!='audio' and type(self) is OpenAIRealtimeProvider:
            raise ValueError('hybrid_provider_required')
        super().__init__(None,claim_guard,output_modality=output_modality)
        self._credential_loader=credential_loader;self._closing=False
        self._opening_session=None;self._opening_close_task=None

    async def _close_opening_session(self):
        if self._opening_session is None:return
        if self._opening_close_task is None:
            self._opening_close_task=asyncio.create_task(self._opening_session.close())
        await asyncio.shield(self._opening_close_task)
        if not self._opening_session.closed:raise ValueError('provider_cleanup_unconfirmed')

    async def open(self,snapshot,**options):
        await _require_live_admission(self.guard)
        if self._closing:raise ValueError('provider_closed')
        try:
            credential=await asyncio.wait_for(self._credential_loader(),CONNECT_DEADLINE)
        except Exception:
            raise ValueError('provider_credential_unavailable') from None
        try:
            if not isinstance(credential,str) or not credential:raise ValueError('credential_unavailable')
            if self._closing:raise ValueError('provider_closed')
            self.socket=await _open_openai_socket(credential,self)
        except Exception:
            raise ValueError('provider_connect_unavailable') from None
        finally:
            credential=None
        try:
            if self._closing:raise ValueError('provider_closed')
            await super().open(snapshot,**options)
        except asyncio.CancelledError:
            # Session cleanup must still retain its lease if close is unknown.
            await self.socket.close()
            raise
        except Exception as error:
            # Protocol/provider exception text can contain untrusted response
            # content. Emit only a fixed runtime class in production logs.
            reason=type(error).__name__
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,63}',reason):reason='UnknownError'
            _LOG.warning('realtime_provider_open_failed stage=context_or_session reason=%s',reason)
            try:await self.socket.close()
            except Exception:raise ValueError('provider_cleanup_unconfirmed') from None
            raise ValueError('provider_open_unavailable') from None

    async def close(self):
        self._closing=True;self.ready=False;self.bound=None
        if self.socket is not None:await self.socket.close()
        elif self._opening_session is not None:await self._close_opening_session()


class _HybridRealtimeMixin:
    """Bridge one admitted OpenAI text response to a bounded TTD stream."""
    text_output=True
    def _init_hybrid(self,loader,stream_factory):
        self._tts_loader=loader;self._stream_factory=stream_factory
        self._hybrid_events=asyncio.Queue(maxsize=128);self._hybrid_ready=deque()
        self._hybrid_openai_task=None;self._hybrid_tts_task=None
        self._hybrid_streams=[];self._hybrid_active=None;self._hybrid_epoch=0
        self._hybrid_pending_dispatch=None;self._hybrid_cancel_pending=False
        self._hybrid_items={};self._hybrid_pending_delete={};self._hybrid_closing=False
        self._hybrid_close_task=None

    async def send_prepared_response(self,token,dispatch_guard=None):
        if self._hybrid_pending_dispatch is not None or self._hybrid_active is not None:
            raise ValueError('hybrid_response_overlap')
        pending=asyncio.get_running_loop().create_future()
        self._hybrid_pending_dispatch=pending
        try:
            sent=await super().send_prepared_response(token,dispatch_guard)
            if not pending.done():pending.set_result(sent is True)
            if sent is not True:
                self._hybrid_pending_dispatch=None;self._hybrid_cancel_pending=False
            return sent
        except BaseException:
            if not pending.done():pending.set_result(False)
            self._hybrid_pending_dispatch=None;self._hybrid_cancel_pending=False
            raise

    def _cancel_hybrid_response(self):
        # Audio already moved out of the TTS queue is stale too. Keep terminal
        # lifecycle events so the Session can still reconcile the response.
        self._hybrid_ready=deque(event for event in self._hybrid_ready
                                 if event.get('type')!='response.output_audio.delta')
        active=self._hybrid_active
        if active is None:
            if self._hybrid_pending_dispatch is not None:self._hybrid_cancel_pending=True
            return
        if active['cancelled']:return
        active['cancelled']=True
        if active['stream'] is None:active['tts_done']=True
        else:active['stream'].cancel_now()

    def _hybrid_current(self,epoch):
        active=self._hybrid_active
        return (not self._hybrid_closing and self.ready and active is not None and
                active['epoch']==epoch and not active['cancelled'])

    async def _hybrid_emit(self,event):
        await self._hybrid_events.put(event)

    async def _send(self,event,dispatch_guard=None):
        if isinstance(event,dict) and event.get('type')=='response.cancel':
            active=self._hybrid_active
            if active is not None and event.get('response_id')!=active['response_id']:
                raise ValueError('hybrid_response_mismatch')
            self._cancel_hybrid_response()
        if isinstance(event,dict) and event.get('type')=='conversation.item.truncate':
            if (set(event)!={'type','item_id','content_index','audio_end_ms'} or
                event.get('content_index')!=0 or event.get('audio_end_ms')!=0 or
                event.get('item_id') not in self._hybrid_items or
                event['item_id'] in self._hybrid_pending_delete):
                raise ValueError('hybrid_truncate_invalid')
            self._cancel_hybrid_response()
            self._hybrid_pending_delete[event['item_id']]=True
            await self.socket.send_json({'type':'conversation.item.delete','item_id':event['item_id']})
            return True
        return await super()._send(event,dispatch_guard)

    def _hybrid_finish_if_ready(self):
        active=self._hybrid_active
        if active is None or active['response_done'] is None or not active['tts_done']:return
        if not active['cancelled']:
            if not active['audio_seen'] or active['item_id'] is None:raise ValueError('tts_empty_response')
            self._hybrid_ready.append({'type':'response.output_audio.done','response_id':active['response_id'],
                                       'item_id':active['item_id'],'content_index':0})
        self._hybrid_ready.append(active['response_done'])
        self._hybrid_active=None

    async def _handle_hybrid_openai(self,event):
        kind=event.get('type')
        if kind=='input_audio_buffer.speech_started':
            self._cancel_hybrid_response()
            return event
        if kind=='response.created':
            pending=self._hybrid_pending_dispatch
            if pending is None or self._hybrid_active is not None:raise ValueError('hybrid_unsolicited_response')
            allowed=await asyncio.shield(pending)
            self._hybrid_pending_dispatch=None
            response=event.get('response');rid=response.get('id') if isinstance(response,dict) else None
            if not allowed or not isinstance(rid,str) or not rid or len(rid)>128:
                raise ValueError('hybrid_unsolicited_response')
            self._hybrid_epoch+=1
            self._hybrid_active={'epoch':self._hybrid_epoch,'response_id':rid,'item_id':None,'stream':None,
                                 'cancelled':self._hybrid_cancel_pending,'tts_done':self._hybrid_cancel_pending,
                                 'audio_seen':False,'text_done':False,'response_done':None,
                                 'text_digest':hashlib.sha256(),'item_verified':False}
            self._hybrid_cancel_pending=False
            return {'type':'response.created','response':{'id':rid}}
        if kind=='conversation.item.deleted':
            item=event.get('item_id')
            if item not in self._hybrid_pending_delete:raise ValueError('hybrid_delete_unexpected')
            self._hybrid_pending_delete.pop(item,None);self._hybrid_items.pop(item,None)
            return {'type':'conversation.item.truncated','item_id':item,'content_index':0,'audio_end_ms':0}
        if kind in ('response.output_item.added','response.output_item.done'):
            active=self._hybrid_active;item=event.get('item')
            if (active is not None and event.get('response_id')==active['response_id'] and
                type(event.get('output_index')) is int and event['output_index']==0 and
                isinstance(item,dict) and item.get('type')=='function_call'):
                item_id=item.get('id');call_id=item.get('call_id');name=item.get('name')
                arguments=item.get('arguments','')
                if (not self.tool_names or active['stream'] is not None or active['text_done'] or active['audio_seen'] or
                    not isinstance(item_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}',item_id) or
                    not isinstance(call_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',call_id) or
                    name not in self.tool_names or not isinstance(arguments,str) or len(arguments.encode('utf-8'))>8192):
                    raise ValueError('hybrid_function_call_invalid')
                return event
            if (active is None or event.get('response_id')!=active['response_id'] or
                type(event.get('output_index')) is not int or event['output_index']!=0 or not isinstance(item,dict) or
                item.get('type')!='message' or item.get('role')!='assistant'):
                raise ValueError('hybrid_item_invalid')
            item_id=item.get('id')
            if not isinstance(item_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}',item_id):
                raise ValueError('hybrid_item_invalid')
            if active['item_id'] not in (None,item_id):raise ValueError('hybrid_item_invalid')
            if kind.endswith('.done'):
                if active['item_id']!=item_id:raise ValueError('hybrid_item_invalid')
                if not active['cancelled']:
                    content=item.get('content')
                    if (item.get('status')!='completed' or not isinstance(content,list) or len(content)!=1 or
                        not isinstance(content[0],dict) or content[0].get('type')!='output_text' or
                        not isinstance(content[0].get('text'),str) or
                        hashlib.sha256(content[0]['text'].encode('utf-8')).digest()!=active['text_digest'].digest()):
                        raise ValueError('hybrid_text_mismatch')
                    active['item_verified']=True
                return None
            if active['item_id'] is not None:raise ValueError('hybrid_item_invalid')
            active['item_id']=item_id;self._hybrid_items[item_id]=active['response_id']
            if len(self._hybrid_items)>1024:raise ValueError('hybrid_item_limit')
            return {'type':kind,'response_id':active['response_id'],
                    'item':{'type':'message','role':'assistant','id':item_id}}
        if kind in ('response.content_part.added','response.content_part.done',
                    'response.output_text.delta','response.output_text.done'):
            active=self._hybrid_active
            if (active is None or event.get('response_id')!=active['response_id'] or
                event.get('item_id')!=active['item_id'] or active['item_id'] is None or
                type(event.get('output_index')) is not int or event['output_index']!=0 or
                type(event.get('content_index')) is not int or event['content_index']!=0):
                raise ValueError('hybrid_text_event_invalid')
            if kind=='response.output_text.delta':
                text=event.get('delta')
                from tenant_operator_elevenlabs_voice import MAX_DELTA_CHARS
                if not isinstance(text,str) or not text or len(text)>MAX_DELTA_CHARS or active['text_done']:
                    raise ValueError('hybrid_text_event_invalid')
                if not active['cancelled']:
                    active['text_digest'].update(text.encode('utf-8'))
                    if active['stream'] is None:
                        stream=self._stream_factory(self.guard,self._tts_loader,active['epoch'],
                                                    self._hybrid_current,self._hybrid_emit)
                        active['stream']=stream.start();self._hybrid_streams.append(stream)
                    active['stream'].feed(text)
            elif kind=='response.output_text.done':
                if active['text_done']:raise ValueError('hybrid_text_event_invalid')
                final=event.get('text')
                if (not active['cancelled'] and
                    (not isinstance(final,str) or hashlib.sha256(final.encode('utf-8')).digest()!=active['text_digest'].digest())):
                    raise ValueError('hybrid_text_mismatch')
                active['text_done']=True
                if active['stream'] is not None and not active['cancelled']:active['stream'].finish_input()
            return None
        if kind in ('response.function_call_arguments.delta','response.function_call_arguments.done'):
            active=self._hybrid_active
            if (not self.tool_names or active is None or event.get('response_id')!=active['response_id'] or
                not isinstance(event.get('item_id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}',event['item_id']) or
                not isinstance(event.get('call_id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',event['call_id'])):
                raise ValueError('hybrid_function_call_invalid')
            value=event.get('delta') if kind.endswith('.delta') else event.get('arguments')
            if not isinstance(value,str) or len(value.encode('utf-8'))>8192:
                raise ValueError('hybrid_function_call_invalid')
            return event
        if kind=='response.done':
            active=self._hybrid_active;response=event.get('response')
            if active is None or not isinstance(response,dict) or response.get('id')!=active['response_id']:
                raise ValueError('hybrid_response_mismatch')
            if active['response_done'] is not None:raise ValueError('hybrid_response_mismatch')
            output=response.get('output',[])
            if (isinstance(output,list) and len(output)==1 and isinstance(output[0],dict) and
                output[0].get('type')=='function_call'):
                if (not self.tool_names or active['stream'] is not None or active['text_done'] or active['audio_seen'] or
                    output[0].get('name') not in self.tool_names or
                    not isinstance(output[0].get('arguments'),str) or len(output[0]['arguments'].encode('utf-8'))>8192):
                    raise ValueError('hybrid_function_call_invalid')
                self._hybrid_active=None
                return event
            if (not isinstance(output,list) or len(output)>1 or
                any(not isinstance(item,dict) or item.get('type')!='message' or
                    item.get('role')!='assistant' or item.get('id')!=active['item_id'] for item in output)):
                raise ValueError('provider_item_forbidden')
            safe={'id':active['response_id'],'status':response.get('status'),
                  'output':[{'type':'message','role':'assistant'} for _ in output],
                  'usage':response.get('usage')}
            details=response.get('status_details')
            if isinstance(details,dict):safe['status_details']={'type':details.get('type'),'reason':details.get('reason')}
            active['response_done']={'type':'response.done','response':safe}
            if response.get('status')!='completed':self._cancel_hybrid_response()
            elif not active['cancelled'] and (active['stream'] is None or not active['text_done'] or not active['item_verified']):
                raise ValueError('tts_text_unconfirmed')
            elif not active['cancelled'] and len(output)!=1:
                raise ValueError('tts_text_unconfirmed')
            self._hybrid_finish_if_ready()
            return None
        if kind in ('conversation.item.added','conversation.item.done'):
            item=event.get('item')
            if isinstance(item,dict) and item.get('role')=='assistant':
                if item.get('id') not in self._hybrid_items:raise ValueError('hybrid_item_invalid')
                return None
        if kind in ('response.output_audio.delta','response.output_audio.done',
                    'response.output_audio_transcript.delta','response.output_audio_transcript.done'):
            raise ValueError('hybrid_event_forbidden')
        if kind in ('conversation.created','rate_limits.updated'):return None
        return event

    def _handle_hybrid_tts(self,event):
        kind,epoch,payload=event
        active=self._hybrid_active
        if active is None or active['epoch']!=epoch:return
        if kind=='audio':
            if active['cancelled']:return
            if active['item_id'] is None:raise ValueError('hybrid_item_invalid')
            active['audio_seen']=True
            self._hybrid_ready.append({'type':'response.output_audio.delta','response_id':active['response_id'],
                                       'item_id':active['item_id'],'content_index':0,'delta':payload})
        elif kind in ('done','cancelled'):
            if kind=='cancelled' and not active['cancelled']:raise ValueError('tts_provider_error')
            active['tts_done']=True;self._hybrid_finish_if_ready()
        else:
            raise ValueError(payload if payload in ('tts_provider_error','tts_close_unconfirmed','tts_timeout') else 'tts_provider_error')

    async def receive(self):
        if not self.ready:raise ValueError('session_not_ready')
        while True:
            if self._hybrid_closing or not self.ready:raise ValueError('session_not_ready')
            if self._hybrid_openai_task is None:self._hybrid_openai_task=asyncio.create_task(super().receive())
            if self._hybrid_ready:
                # A hot audio backlog must not keep the OpenAI reader from
                # observing a queued speech_started. One loop turn is bounded:
                # an idle network reader never holds up audio indefinitely.
                await asyncio.sleep(0)
                if self._hybrid_closing or not self.ready:raise ValueError('session_not_ready')
                if not self._hybrid_openai_task.done():return self._hybrid_ready.popleft()
                event=await self._hybrid_openai_task;self._hybrid_openai_task=None
                mapped=await self._handle_hybrid_openai(event)
                if mapped is not None:return mapped
                # Likewise, a hot stream of text deltas must not starve TTS.
                # Prefetch the next OpenAI event while the caller handles PCM.
                if self._hybrid_ready:
                    self._hybrid_openai_task=asyncio.create_task(super().receive())
                    return self._hybrid_ready.popleft()
                continue
            if self._hybrid_tts_task is None:self._hybrid_tts_task=asyncio.create_task(self._hybrid_events.get())
            done,_=await asyncio.wait({self._hybrid_openai_task,self._hybrid_tts_task},return_when=asyncio.FIRST_COMPLETED)
            if self._hybrid_closing or not self.ready:raise ValueError('session_not_ready')
            # Give newly observed VAD priority over queued TTD chunks.
            if self._hybrid_openai_task in done:
                event=await self._hybrid_openai_task;self._hybrid_openai_task=None
                mapped=await self._handle_hybrid_openai(event)
                if mapped is not None:return mapped
            if self._hybrid_tts_task in done:
                event=await self._hybrid_tts_task;self._hybrid_tts_task=None
                self._handle_hybrid_tts(event)

    async def close(self):
        if self._hybrid_close_task is None:self._hybrid_close_task=asyncio.create_task(self._close_hybrid())
        await asyncio.shield(self._hybrid_close_task)

    async def _close_hybrid(self):
        self._hybrid_closing=True;self._cancel_hybrid_response()
        for stream in self._hybrid_streams:stream.cancel_now()
        # Drain a bounded queue so a completing reader cannot hold cleanup.
        while not self._hybrid_events.empty():self._hybrid_events.get_nowait()
        readers=[task for task in (self._hybrid_openai_task,self._hybrid_tts_task) if task is not None]
        # An active aiohttp receive must remain alive through the OpenAI close
        # handshake: cancelling it first makes aiohttp record a false 1006.
        if self._hybrid_tts_task is not None and not self._hybrid_tts_task.done():self._hybrid_tts_task.cancel()
        cleanup=[stream.wait_closed() for stream in self._hybrid_streams]
        cleanup.append(super().close())
        results=await asyncio.gather(*cleanup,return_exceptions=True)
        for task in (self._hybrid_openai_task,self._hybrid_tts_task):
            if task is not None and task not in readers:readers.append(task)
        for task in readers:
            if not task.done():task.cancel()
        if readers:await asyncio.gather(*readers,return_exceptions=True)
        stream_count=len(self._hybrid_streams)
        # Reader errors are drained above, but do not mean a transport is
        # still open: only the corresponding close operations can prove that.
        if (any(isinstance(result,BaseException) for result in results[:stream_count]) or
            isinstance(results[-1],BaseException)):
            raise ValueError('tts_close_unconfirmed')


class SyntheticHybridRealtimeWire(_HybridRealtimeMixin,_RealtimeWire):
    mock_only=True
    def __init__(self,socket,guard,loader,stream_factory):
        if getattr(socket,'synthetic_only',None) is not True:raise ValueError('synthetic_socket_required')
        super().__init__(socket,guard,output_modality='text')
        self._init_hybrid(loader,stream_factory)


class HybridOpenAIRealtimeProvider(_HybridRealtimeMixin,OpenAIRealtimeProvider):
    """Server-only OpenAI input/text with ElevenLabs voice synthesis."""
    mock_only=False
    def __init__(self,credential_loader,claim_guard,ttd_loader):
        from tenant_operator_elevenlabs_voice import ElevenTTDStream
        super().__init__(credential_loader,claim_guard,output_modality='text')
        self._init_hybrid(ttd_loader,ElevenTTDStream)


def create_hybrid_pilot_provider(policy,credential_loader,ttd_loader):
    from tenant_operator_voice_pilot import PilotAdmission
    from tenant_operator_voice_orchestration import ServerCredentialLoader
    from tenant_operator_elevenlabs_voice import ElevenServerCredentialLoader
    from tenant_ai_business_analytics_voice import (AnalyticsVoiceCredentialLoader,
                                                      AnalyticsVoiceElevenCredentialLoader)
    if (type(policy) is not PilotAdmission or
        type(credential_loader) not in (ServerCredentialLoader,AnalyticsVoiceCredentialLoader) or
        type(ttd_loader) not in (ElevenServerCredentialLoader,AnalyticsVoiceElevenCredentialLoader) or
        policy.authority is not credential_loader.authority or
        policy.authority is not ttd_loader.authority or
        getattr(policy.lease,'provider_kind',None)!='openai_elevenlabs_tts'):
        raise ValueError('trusted_provider_dependencies_required')
    return HybridOpenAIRealtimeProvider(credential_loader.load,policy,ttd_loader)
