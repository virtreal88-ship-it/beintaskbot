"""#480 wire over budgeted fake connector only; NEVER connect to provider.

Python attribute privacy is not a security boundary. No real connector is
reachable here. This candidate does not change #480's gated production path.
"""
import asyncio
import base64
import hashlib
import json
import uuid

from ledger import local_connect
import session_budget as budget
from tenant_operator_realtime_wire import _RealtimeWire,MODEL

ENABLED_SYNTHETIC=False


def _encode(event):
    return json.dumps(event,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode('ascii')


class _FakeSocket:
    """No actual network, private to the candidate sender."""
    def __init__(self):self.frames=[];self.closed=False
    async def write(self,frame):self.frames.append(bytes(frame))
    async def read(self):
        if not self.frames:return {'type':'session.created','session':{'id':'sess_fake'}}
        event=json.loads(self.frames[-1]);kind=event['type']
        if kind=='session.update':return {'type':'session.updated','session':{**event['session'],'id':'sess_fake'}}
        if kind=='conversation.item.create':return {'type':'conversation.item.added','item':event['item']}
        raise ValueError('unexpected_fake_read')
    async def close(self):self.closed=True


class _BudgetSocket:
    """Only outbound interface supplied to #480 wire; fake connector only."""
    def __init__(self,dsn,scope,socket):
        self._dsn=dsn;self._scope=budget._scope(json.loads(json.dumps(scope)));self.__socket=socket
        self._lock=asyncio.Lock();self._poisoned=False;self._stage='connected';self._expected_config=None;self._expected_context=None;self._response=None
    def evidence(self):return tuple(self.__socket.frames)
    def _mark_uncertain(self):
        with local_connect(self._dsn) as conn,conn.cursor() as cur:budget.mark_uncertain(cur,self._scope)
    async def abort(self):
        self._poisoned=True;await self.__socket.close()
        try:await asyncio.to_thread(self._mark_uncertain)
        except Exception:pass
    def bind_response(self,scope):
        keys=('tenant_id','session_id','channel_id','actor_id','conversation_id','executor')
        if any(str(scope.get(k))!=str(self._scope[k]) for k in keys):raise ValueError('response_scope_invalid')
        if type(scope.get('response_number')) is not int or not 1<=scope['response_number']<=self._scope['response_limit']:
            raise ValueError('response_scope_invalid')
        self._response=(str(uuid.UUID(scope['request_id'])),scope['response_number'])
    async def receive_json(self):
        event=await self.__socket.read()
        # #480 permits a conversation announcement before either awaited
        # acknowledgement; it is not itself proof of config or context.
        if self._stage in ('config_sent','context_sent') and isinstance(event,dict) and event.get('type')=='conversation.created':
            conversation=event.get('conversation')
            if (('session_id' in event and event['session_id']!='sess_fake') or not isinstance(conversation,dict) or
                not isinstance(conversation.get('id'),str) or not 1<=len(conversation['id'])<=128):
                raise ValueError('provider_conversation_invalid')
            return event
        if self._stage=='config_sent':
            # Both #480 verification and budget's independent policy check.
            _RealtimeWire._verify(event,self._expected_config,'sess_fake')
            with local_connect(self._dsn) as conn,conn.cursor() as cur:row=budget.accept_config(cur,self._scope,event,'sess_fake')
            if not row or row['state']!='ready':raise ValueError('config_not_attested')
            self._stage='config_ack'
        elif self._stage=='context_sent':
            if (not isinstance(event,dict) or event.get('type')!='conversation.item.added' or
                event.get('item')!=self._expected_context or event.get('previous_item_id') is not None or
                ('session_id' in event and event['session_id']!='sess_fake')):
                raise ValueError('context_not_attested')
            self._stage='active'
        return event
    async def close(self):self._poisoned=True;await self.__socket.close()
    def _validate(self,event):
        if not isinstance(event,dict):raise ValueError('event_invalid')
        typ=event.get('type')
        if typ=='session.update' and self._stage=='connected':
            if set(event)!={'type','session'}:raise ValueError('config_invalid')
            c=event['session'];a=c.get('audio',{});i=a.get('input',{});o=a.get('output',{});v=i.get('turn_detection',{})
            if (set(c)!={'type','model','instructions','output_modalities','tools','tool_choice','max_output_tokens','audio'} or
                c.get('type')!='realtime' or c.get('model')!=MODEL or not isinstance(c.get('instructions'),str) or
                'Never send messages' not in c['instructions'] or len(c['instructions'].encode())>120000 or
                c.get('tools')!=[] or c.get('tool_choice')!='none' or c.get('output_modalities')!=['audio'] or c.get('max_output_tokens')!=4096 or
                set(a)!={'input','output'} or set(i)!={'format','transcription','turn_detection'} or set(o)!={'format'} or
                i.get('format')!={'type':'audio/pcm','rate':24000} or o.get('format')!={'type':'audio/pcm','rate':24000} or
                i.get('transcription') is not None or v!={'type':'server_vad','create_response':False,'interrupt_response':False,'idle_timeout_ms':None}):
                raise ValueError('config_invalid')
            return 'config',None
        if typ=='conversation.item.create' and self._stage=='config_ack':
            item=event.get('item',{});content=item.get('content')
            if (set(event)!={'type','previous_item_id','item'} or event['previous_item_id']!='root' or
                set(item)!={'id','type','role','content'} or item['type']!='message' or item['role']!='user' or
                not isinstance(item['id'],str) or not item['id'].startswith('ctx_') or
                not isinstance(content,list) or len(content)!=1 or set(content[0])!={'type','text'} or
                content[0]['type']!='input_text' or not isinstance(content[0]['text'],str) or
                not content[0]['text'].startswith('Untrusted dialog context: ') or len(content[0]['text'].encode())>120100):
                raise ValueError('context_invalid')
            return 'context',None
        if self._stage!='active':raise ValueError('stage_invalid')
        if typ=='input_audio_buffer.append' and set(event)=={'type','audio'}:
            try:pcm=base64.b64decode(event['audio'],validate=True)
            except Exception:raise ValueError('audio_invalid') from None
            if not 2<=len(pcm)<=9600 or len(pcm)%2:raise ValueError('audio_invalid')
            return 'audio',pcm
        if typ=='response.create' and set(event)=={'type'} and self._response:return 'response',None
        if typ=='input_audio_buffer.clear' and set(event)=={'type'}:return 'control',None
        if typ=='response.cancel' and set(event) in ({'type'},{'type','response_id'}):
            if 'response_id' in event and (not isinstance(event['response_id'],str) or not 1<=len(event['response_id'])<=128):raise ValueError('control_invalid')
            return 'control',None
        if typ=='conversation.item.truncate' and set(event)=={'type','item_id','content_index','audio_end_ms'}:
            if not isinstance(event['item_id'],str) or event['content_index']!=0 or event['audio_end_ms']!=0:raise ValueError('control_invalid')
            return 'control',None
        raise ValueError('event_forbidden')
    def _commit(self,kind,operation_id,frame,pcm,ordinal=None):
        frame=bytes(frame)
        with local_connect(self._dsn) as conn,conn.cursor() as cur:
            if kind=='audio':issued=budget.issue_audio(cur,self._scope,operation_id,pcm)
            elif kind=='response':
                issued=budget.issue_response(cur,self._scope,operation_id)
                if issued and issued['ordinal']!=ordinal:raise ValueError('response_ordinal_mismatch')
            else:
                budget._global(cur);budget._quote_fresh();row=budget._session(cur,self._scope)
                expected='opening' if kind=='config' else 'ready'
                issued=row['state']==expected and budget._tenant(cur,self._scope,row,'unknown')
            if not issued:return False
            cur.execute('''INSERT INTO saas_voice_bound_sender_receipt(operation_id,session_id,operation,scope_fingerprint,wire_sha256,wire_bytes)
              VALUES(%s,%s,%s,%s,%s,%s)''',(operation_id,self._scope['session_id'],kind,budget._fingerprint(self._scope),hashlib.sha256(frame).hexdigest(),len(frame)))
        return True
    async def send_json(self,event):
        # Snapshot nested caller objects before first await. No arbitrary JSON
        # reaches the private synthetic socket without type/phase/DB checks.
        frame=_encode(event);snapshot=json.loads(frame)
        async with self._lock:
            if self._poisoned:raise ValueError('sender_poisoned')
            kind,pcm=self._validate(snapshot)
            operation_id,ordinal=self._response if kind=='response' else (str(uuid.uuid4()),None)
            try:
                if not await asyncio.to_thread(self._commit,kind,operation_id,frame,pcm,ordinal):raise ValueError('budget_capability_unavailable')
                if kind=='response':self._response=None
                await asyncio.wait_for(self.__socket.write(frame),0.2)
                if kind=='config':self._expected_config=snapshot['session'];self._stage='config_sent'
                elif kind=='context':self._expected_context=snapshot['item'];self._stage='context_sent'
            except BaseException:
                await self.abort()
                raise


class BoundSyntheticWire(_RealtimeWire):
    mock_only=True
    def __init__(self,socket,guard):
        if type(socket) is not _BudgetSocket:raise ValueError('bound_socket_required')
        super().__init__(socket,guard)
    async def open(self,snapshot,**options):
        try:return await super().open(snapshot,**options)
        except BaseException:
            self.ready=False
            await self.socket.abort()
            raise
    async def bind_claim(self,scope,receipt):
        await super().bind_claim(scope,receipt)
        try:self.socket.bind_response(scope)
        except BaseException:self.bound=None;raise


def _consume_open(dsn,scope):
    with local_connect(dsn) as conn,conn.cursor() as cur:return budget.issue_open(cur,scope)


async def connect_synthetic(dsn,scope,guard):
    if ENABLED_SYNTHETIC is not True:raise ValueError('wire_integration_disabled')
    # Detach scope before first await; caller mutation cannot make the future
    # socket represent a different identity than the durable opening hold.
    scope=budget._scope(json.loads(json.dumps(scope,allow_nan=False)))
    # Session hold and one-use open transition must commit before *creating*
    # even this fake socket; duplicate or uncertain open never reconnects.
    issued=await asyncio.to_thread(_consume_open,dsn,scope)
    if not issued:raise ValueError('open_capability_unavailable')
    return BoundSyntheticWire(_BudgetSocket(dsn,scope,_FakeSocket()),guard)
