"""Server-owned Realtime protocol for the single-account Copilot pilot."""
import asyncio
import base64
import binascii
import json
import uuid
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
        if message.type!=aiohttp.WSMsgType.TEXT:raise ValueError('wire_socket_closed_or_nontext')
        if len(message.data.encode('utf-8'))>MAX_WIRE_MESSAGE:raise ValueError('wire_message_too_large')
        try:
            data=json.loads(message.data,object_pairs_hook=_unique_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError,TypeError,RecursionError):raise ValueError('wire_json_invalid') from None
        if not isinstance(data,dict):raise ValueError('wire_event_invalid')
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
    except BaseException:
        await owner._close_opening_session()
        raise
    socket=AiohttpRealtimeSocket(ws,owned_session=session)
    owner.socket=socket
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
    if type(policy) is not PilotAdmission or type(credential_loader) is not ServerCredentialLoader:
        raise ValueError('trusted_provider_dependencies_required')
    if policy.authority is not credential_loader.authority:
        raise ValueError('pilot_authority_mismatch')
    return OpenAIRealtimeProvider(credential_loader.load,policy)


class _RealtimeWire:
    def __init__(self,socket,claim_guard):
        if not callable(getattr(claim_guard,'consume',None)):
            raise ValueError('claim_guard_required')
        self.socket=socket;self.guard=claim_guard;self.ready=False;self.bound=None;self.seen=set();self.config=None;self.session_id=None;self.conversation_id=None

    def _note_conversation(self,event,session_id=None):
        conversation=event.get('conversation')
        value=conversation.get('id') if isinstance(conversation,dict) else None
        expected=session_id if session_id is not None else self.session_id
        if (self.conversation_id is not None or not isinstance(value,str) or not value or len(value)>128 or
            ('session_id' in event and event['session_id']!=expected)):
            raise ValueError('provider_conversation_invalid')
        self.conversation_id=value

    async def open(self,snapshot,*,tools,create_response,interrupt_response,transcription,reconnect):
        if (tools!=[] or create_response is not False or interrupt_response is not False or transcription!='gpt-transcribe'
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
          'output_modalities':['audio'],'tools':[],'tool_choice':'none','max_output_tokens':OUTPUT_LIMIT,
          'audio':{'input':{'format':{'type':'audio/pcm','rate':24000},
                            'transcription':{'model':'gpt-transcribe','languages':['ru','az']},
                            'turn_detection':{'type':'server_vad','create_response':False,'interrupt_response':False,'idle_timeout_ms':None}},
                   'output':{'format':{'type':'audio/pcm','rate':24000}}}}
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
        context_id='ctx_'+uuid.uuid4().hex
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
        self.config=config;self.session_id=session_id;self.ready=True

    @staticmethod
    def _verify(event,expected,session_id):
        try:
            if event['type']!='session.updated':raise ValueError()
            s=event['session'];audio=s['audio'];incoming=audio['input'];vad=incoming['turn_detection']
            good=(s['id']==session_id and s['type']=='realtime' and s['model']==MODEL and s['output_modalities']==['audio'] and s['tools']==[]
              and s['tool_choice']=='none' and s['max_output_tokens']==OUTPUT_LIMIT and s['instructions']==expected['instructions']
              and incoming['format']=={'type':'audio/pcm','rate':24000}
              and incoming['transcription']=={'model':'gpt-transcribe','languages':['ru','az']}
              and vad['type']=='server_vad' and vad['create_response'] is False and vad['interrupt_response'] is False
              and vad['idle_timeout_ms'] is None
              and set(vad)<={'type','create_response','interrupt_response','idle_timeout_ms','threshold','prefix_padding_ms','silence_duration_ms'}
              and audio['output']['format']=={'type':'audio/pcm','rate':24000})
            if not good:raise ValueError()
        except (KeyError,TypeError,ValueError):raise ValueError('unsafe_session_readback') from None

    async def bind_claim(self,scope,receipt):
        if not self.ready or self.bound is not None or not isinstance(scope,dict) or not isinstance(receipt,dict):
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
        if type(self.guard) is not PilotAdmission or not self.ready or self.bound is not None:
            raise ValueError('pilot_claim_not_available')
        if not isinstance(scope,dict) or not isinstance(receipt,dict):raise ValueError('pilot_claim_invalid')
        expected=(scope.get('tenant_id'),scope.get('actor_id'),scope.get('channel_id'),scope.get('conversation_id'))
        if (expected!=self.guard.scope or scope.get('session_id')!=self.guard.session_id or
            receipt.get('session_id')!=self.guard.session_id or receipt.get('request_id')!=scope.get('request_id') or
            scope.get('request_id') in self.seen):raise ValueError('pilot_claim_scope_invalid')
        self.bound=(dict(scope),dict(receipt))

    async def send(self,event):
        if not self.ready or not isinstance(event,dict):raise ValueError('session_not_ready')
        kind=event.get('type')
        if kind=='response.create' and set(event)=={'type'}:
            if not self.bound:raise ValueError('claim_missing')
            scope,receipt=self.bound;self.bound=None
            # This test callback must model an atomic, committed, globally
            # one-use dispatch capability; current production ledger has no
            # such reviewed integration, so the live factory stays blocked.
            from tenant_operator_voice_pilot import PilotAdmission
            if type(self.guard) is PilotAdmission:
                admitted_scope=(scope['tenant_id'],scope['actor_id'],scope['channel_id'],scope['conversation_id'])
                consumed=await self.guard.consume(admitted_scope,receipt)
            else:
                consumed=await self.guard.consume(scope,receipt)
            if consumed is not True:raise ValueError('claim_not_consumed')
            self.seen.add(scope['request_id'])
        elif kind=='input_audio_buffer.append' and set(event)=={'type','audio'}:
            try:audio=base64.b64decode(event['audio'],validate=True)
            except (ValueError,TypeError,binascii.Error):raise ValueError('audio_invalid') from None
            if not audio or len(audio)>9600 or len(audio)%2:raise ValueError('audio_invalid')
        elif kind=='input_audio_buffer.clear' and set(event)=={'type'}:pass
        elif kind=='response.cancel' and set(event) in ({'type'},{'type','response_id'}):
            if 'response_id' in event and (not isinstance(event['response_id'],str) or not event['response_id'] or len(event['response_id'])>128):
                raise ValueError('response_id_invalid')
        elif kind=='conversation.item.truncate' and set(event)=={'type','item_id','content_index','audio_end_ms'}:
            if not isinstance(event['item_id'],str) or event['content_index']!=0 or event['audio_end_ms']!=0:
                raise ValueError('truncate_invalid')
        else:raise ValueError('provider_event_forbidden')
        await self.socket.send_json(event)

    async def receive(self):
        if not self.ready:raise ValueError('session_not_ready')
        event=await self.socket.receive_json()
        if not isinstance(event,dict):raise ValueError('provider_event_invalid')
        if 'session_id' in event and event['session_id']!=self.session_id:
            self.ready=False;raise ValueError('provider_session_changed')
        if event.get('type') in {'session.created','session.updated'}:
            # No settings changes after the attested startup are permitted.
            self.ready=False
            raise ValueError('provider_configuration_changed')
        if event.get('type')=='conversation.created':
            try:self._note_conversation(event)
            except ValueError:self.ready=False;raise
        return event

    async def close(self):
        self.ready=False;self.bound=None
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

    def __init__(self,credential_loader,claim_guard):
        if not callable(credential_loader):raise ValueError('credential_loader_required')
        super().__init__(None,claim_guard)
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
        except Exception:
            try:await self.socket.close()
            except Exception:raise ValueError('provider_cleanup_unconfirmed') from None
            raise ValueError('provider_open_unavailable') from None

    async def close(self):
        self._closing=True;self.ready=False;self.bound=None
        if self.socket is not None:await self.socket.close()
        elif self._opening_session is not None:await self._close_opening_session()
