"""Review candidate for a server-owned Realtime wire. Live creation is blocked.

Only injected synthetic sockets are accepted. This module does not read keys,
open sockets, issue tokens, or register routes. A production factory requires
independent budget, context, auth and lifecycle enforcement before admission.
"""
import base64
import binascii
import json
import uuid
from tenant_linear_policy import TenantLinearError

MODEL='gpt-realtime-2.1-mini'
OUTPUT_LIMIT=32000
QUOTE='mini-2026-11-03-1resp-v1'
MINIMUM_HOLD=2_112_000


def create_production_provider(*_args,**_kwargs):
    # No environment/config override: wiring has not been independently proven.
    raise TenantLinearError('Голосовой режим пока недоступен.',503)


class SyntheticRealtimeWire:
    mock_only=True

    def __init__(self,socket,claim_guard):
        if getattr(socket,'synthetic_only',None) is not True or not callable(getattr(claim_guard,'consume',None)):
            raise ValueError('synthetic_socket_and_guard_required')
        self.socket=socket;self.guard=claim_guard;self.ready=False;self.bound=None;self.seen=set();self.config=None

    async def open(self,snapshot,*,tools,create_response,interrupt_response,transcription,reconnect):
        if (tools!=[] or create_response is not False or interrupt_response is not True or transcription is not False
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
        config={'type':'realtime','model':MODEL,'instructions':instructions,
          'output_modalities':['audio'],'tools':[],'tool_choice':'none','max_output_tokens':OUTPUT_LIMIT,
          'audio':{'input':{'format':{'type':'audio/pcm','rate':24000},'transcription':None,
                            'turn_detection':{'type':'server_vad','create_response':False,'interrupt_response':True}},
                   'output':{'format':{'type':'audio/pcm','rate':24000}}}}
        await self.socket.send_json({'type':'session.update','session':config})
        updated=await self.socket.receive_json()
        self._verify(updated,config)
        # Existing dialog/customer material stays user-level untrusted input;
        # it must never be appended to the privileged system instructions.
        await self.socket.send_json({'type':'conversation.item.create','item':{'type':'message','role':'user',
                                    'content':[{'type':'input_text','text':'Untrusted dialog context: '+data}]}})
        self.config=config;self.ready=True

    @staticmethod
    def _verify(event,expected):
        try:
            if event['type']!='session.updated':raise ValueError()
            s=event['session'];audio=s['audio'];incoming=audio['input'];vad=incoming['turn_detection']
            good=(s['type']=='realtime' and s['model']==MODEL and s['output_modalities']==['audio'] and s['tools']==[]
              and s['tool_choice']=='none' and s['max_output_tokens']==OUTPUT_LIMIT and s['instructions']==expected['instructions']
              and incoming['format']=={'type':'audio/pcm','rate':24000} and incoming['transcription'] is None
              and vad['type']=='server_vad' and vad['create_response'] is False and vad['interrupt_response'] is True
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
        elif kind=='conversation.item.truncate' and set(event)=={'type','item_id','content_index','audio_end_ms'}:
            if not isinstance(event['item_id'],str) or event['content_index']!=0 or event['audio_end_ms']!=0:
                raise ValueError('truncate_invalid')
        else:raise ValueError('provider_event_forbidden')
        await self.socket.send_json(event)

    async def receive(self):
        if not self.ready:raise ValueError('session_not_ready')
        event=await self.socket.receive_json()
        if not isinstance(event,dict):raise ValueError('provider_event_invalid')
        if event.get('type') in {'session.created','session.updated'}:
            # No settings changes after the attested startup are permitted.
            self.ready=False
            raise ValueError('provider_configuration_changed')
        return event

    async def close(self):
        self.ready=False;self.bound=None
        await self.socket.close()
