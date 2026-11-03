"""Server-owned Realtime protocol candidate. Live creation is hard disabled.

Only injected sockets reach the wire today. The aiohttp transport is usable in
isolated tests, but live connection requires a reviewed, immutable admission
change and proof of the budget, source and history fences.
"""
import base64
import binascii
import json
import uuid
import aiohttp
from tenant_linear_policy import TenantLinearError

MODEL='gpt-realtime-2.1-mini'
OUTPUT_LIMIT=32000
QUOTE='mini-2026-11-03-1resp-v1'
MINIMUM_HOLD=2_112_000
OPENAI_URL='wss://api.openai.com/v1/realtime'
MAX_WIRE_MESSAGE=262144


def _unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate_json_key')
        result[key]=value
    return result


def _require_live_admission():
    # Never toggle through request arguments, deployment config or env vars.
    # Claims, pre-open context linearization, writer cutover and billing are
    # not yet proven with the actual upstream API.
    raise TenantLinearError('Голосовой режим пока недоступен.',503)


class AiohttpRealtimeSocket:
    """Bounded JSON adapter around an already-open aiohttp WebSocket.

    This wrapper owns no key. Its caller owns the session. Production cannot
    obtain a socket through this module while admission is disabled.
    """
    def __init__(self,ws):
        self.ws=ws

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
        await self.ws.close()
        if not self.ws.closed:raise ValueError('wire_close_unconfirmed')


async def _open_openai_socket(session,api_key):
    """Future server-only connector. Gate fires before key use or any I/O."""
    _require_live_admission()
    # Kept behind the immutable gate; not exercised against OpenAI. A future
    # release must also prove redirect, timeout and key-source handling.
    ws=await session.ws_connect(OPENAI_URL,params={'model':MODEL},
                                headers={'Authorization':'Bearer '+api_key},
                                timeout=3,
                                heartbeat=20,max_msg_size=MAX_WIRE_MESSAGE)
    return AiohttpRealtimeSocket(ws)


def create_production_provider(*_args,**_kwargs):
    # No environment/config override: wiring has not been independently proven.
    raise TenantLinearError('Голосовой режим пока недоступен.',503)


class _RealtimeWire:
    def __init__(self,socket,claim_guard):
        if not callable(getattr(claim_guard,'consume',None)):
            raise ValueError('claim_guard_required')
        self.socket=socket;self.guard=claim_guard;self.ready=False;self.bound=None;self.seen=set();self.config=None;self.session_id=None;self.conversation_id=None

    def _note_conversation(self,event):
        conversation=event.get('conversation')
        value=conversation.get('id') if isinstance(conversation,dict) else None
        if self.conversation_id is not None or not isinstance(value,str) or not value or len(value)>128:
            raise ValueError('provider_conversation_invalid')
        self.conversation_id=value

    async def open(self,snapshot,*,tools,create_response,interrupt_response,transcription,reconnect):
        if (tools!=[] or create_response is not False or interrupt_response is not False or transcription is not False
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
          'audio':{'input':{'format':{'type':'audio/pcm','rate':24000},'transcription':None,
                            'turn_detection':{'type':'server_vad','create_response':False,'interrupt_response':False,'idle_timeout_ms':None}},
                   'output':{'format':{'type':'audio/pcm','rate':24000}}}}
        await self.socket.send_json({'type':'session.update','session':config})
        # GA may announce the conversation between session.created and
        # session.updated. Reject all other interleavings; caller bounds open.
        updated=await self.socket.receive_json()
        if isinstance(updated,dict) and updated.get('type')=='conversation.created':
            self._note_conversation(updated)
            updated=await self.socket.receive_json()
        self._verify(updated,config,session_id)
        # Existing dialog/customer material stays user-level untrusted input;
        # it must never be appended to the privileged system instructions.
        await self.socket.send_json({'type':'conversation.item.create','item':{'type':'message','role':'user',
                                    'content':[{'type':'input_text','text':'Untrusted dialog context: '+data}]}})
        self.config=config;self.session_id=session_id;self.ready=True

    @staticmethod
    def _verify(event,expected,session_id):
        try:
            if event['type']!='session.updated':raise ValueError()
            s=event['session'];audio=s['audio'];incoming=audio['input'];vad=incoming['turn_detection']
            good=(s['id']==session_id and s['type']=='realtime' and s['model']==MODEL and s['output_modalities']==['audio'] and s['tools']==[]
              and s['tool_choice']=='none' and s['max_output_tokens']==OUTPUT_LIMIT and s['instructions']==expected['instructions']
              and incoming['format']=={'type':'audio/pcm','rate':24000} and incoming['transcription'] is None
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

    async def send(self,event):
        if not self.ready or not isinstance(event,dict):raise ValueError('session_not_ready')
        kind=event.get('type')
        if kind=='response.create' and set(event)=={'type'}:
            if not self.bound:raise ValueError('claim_missing')
            scope,receipt=self.bound;self.bound=None
            # This test callback must model an atomic, committed, globally
            # one-use dispatch capability; current production ledger has no
            # such reviewed integration, so the live factory stays blocked.
            if await self.guard.consume(scope,receipt) is not True:raise ValueError('claim_not_consumed')
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
