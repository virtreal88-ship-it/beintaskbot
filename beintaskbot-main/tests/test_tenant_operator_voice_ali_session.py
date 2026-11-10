"""Synthetic real Session plus hybrid wire; no network or credentials."""
import asyncio,base64,copy,unittest,uuid
from tenant_operator_realtime_wire import SyntheticHybridRealtimeWire,MODEL,QUOTE,MINIMUM_HOLD
from tenant_operator_voice_session import Session

class Socket:
    synthetic_only=True
    def __init__(self):self.sent=[];self.events=asyncio.Queue();self.handshakes=0;self.closed=False
    async def send_json(self,e):self.sent.append(copy.deepcopy(e))
    async def receive_json(self):
        self.handshakes+=1
        if self.handshakes==1:return {'type':'session.created','session':{'id':'synthetic'}}
        if self.handshakes==2:return {'type':'session.updated','session':{**copy.deepcopy(self.sent[-1]['session']),'id':'synthetic'}}
        if self.handshakes==3:return {'type':'conversation.item.added','item':copy.deepcopy(self.sent[-1]['item'])}
        return await self.events.get()
    async def close(self):self.closed=True
    def put(self,e):self.events.put_nowait(e)
class Guard:
    async def consume(self,*_):return True
class Authority:
    async def check(self,*_):return True
class Context:
    async def read_authorized(self,_):return {'existing_copilot_instructions':'Never send messages','lines':[],'extra_context':{}}
    async def revalidate(self,_):return True
class Ledger:
    async def reserve_session(self,scope,sid):return {'state':'reserved','scope':scope,'session_id':sid,'reserved_microusd':5_000_000}
    async def reserve(self,s):return {**s,'state':'reserved','reserved_microusd':MINIMUM_HOLD}
    async def claim(self,s):return {**s,'state':'claimed','reserved_microusd':MINIMUM_HOLD,'model':MODEL,'quote_version':QUOTE,'tenant_reservation_id':str(uuid.uuid4())}
    async def mark_unknown(self,_):return {'state':'unknown'}
class Lease:
    def __init__(self,socket,streams):self.socket=socket;self.streams=streams;self.held=False;self.released=False
    async def claim(self,_):self.held=True;return 1
    async def assert_current(self,_,fence):
        if not self.held or fence!=1:raise ValueError('lease_lost')
        return True
    async def confirm_provider_open(self,scope,fence):return await self.assert_current(scope,fence)
    async def release(self,_,fence):
        assert self.socket.closed and all(stream.closed for stream in self.streams)
        self.held=False;self.released=True;return True
class Client:
    def __init__(self):self.events=[]
    async def send(self,e):self.events.append(e)
class FakeTTS:
    def __init__(self,guard,loader,epoch,current,emit):self.epoch=epoch;self.emit=emit;self.closed=False;self.cancelled=False;self.tasks=[]
    def start(self):return self
    def _emit(self,kind,payload=None):self.tasks.append(asyncio.create_task(self.emit((kind,self.epoch,payload))))
    def feed(self,text):
        assert text=='Привет.'
        self._emit('audio',base64.b64encode(bytes([1,0])*240).decode())
    def finish_input(self):self._emit('done')
    def cancel_now(self):
        if not self.cancelled:self.cancelled=True;self._emit('cancelled')
    async def wait_closed(self):await asyncio.gather(*self.tasks);self.closed=True

class AliSessionIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.socket=Socket();self.streams=[];self.lease=Lease(self.socket,self.streams);self.client=Client()
        def factory(*args):
            stream=FakeTTS(*args);self.streams.append(stream);return stream
        self.wire=SyntheticHybridRealtimeWire(self.socket,Guard(),None,factory)
        self.scope=(str(uuid.uuid4()),7,str(uuid.uuid4()),'synthetic')
        self.session=Session(self.scope,authority=Authority(),ledger=Ledger(),lease=self.lease,context=Context(),provider=self.wire,client=self.client,duplex=True)
        await self.session.start()
    async def asyncTearDown(self):
        if not self.session.closed:await self.session.end('test_cleanup')
    async def event(self,e):
        self.socket.put(e);result=await asyncio.wait_for(self.session.next_provider_event(),3)
        await self.session.receive_provider(result);return result
    async def turn(self,item):
        await self.event({'type':'input_audio_buffer.speech_started'})
        await self.event({'type':'input_audio_buffer.speech_stopped'})
        await self.event({'type':'conversation.item.added','item':{'id':item,'type':'message','role':'user'}})
        await self.event({'type':'input_audio_buffer.committed','item_id':item})
        return [e for e in self.socket.sent if e['type']=='response.create'][-1]
    async def begin_output(self,rid,item):
        await self.event({'type':'response.created','response':{'id':rid}})
        await self.event({'type':'response.output_item.added','response_id':rid,'output_index':0,'item':{'id':item,'type':'message','role':'assistant'}})
        e=await self.event({'type':'response.output_text.delta','response_id':rid,'item_id':item,'output_index':0,'content_index':0,'delta':'Привет.'})
        self.assertEqual(e['type'],'response.output_audio.delta');self.assertEqual(self.client.events[-1]['pcm'],bytes([1,0])*240)
    async def test_text_to_audio_completion_and_confirmed_cleanup(self):
        await self.turn('user_1');await self.begin_output('reply_1','item_1')
        self.socket.put({'type':'response.output_text.done','response_id':'reply_1','item_id':'item_1','output_index':0,'content_index':0,'text':'Привет.'})
        self.socket.put({'type':'response.output_item.done','response_id':'reply_1','output_index':0,'item':{'id':'item_1','type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':'Привет.'}]}})
        self.socket.put({'type':'response.done','response':{'id':'reply_1','status':'completed','output':[{'id':'item_1','type':'message','role':'assistant'}]}})
        kinds=[]
        for _ in range(2):
            e=await asyncio.wait_for(self.session.next_provider_event(),3);kinds.append(e['type']);await self.session.receive_provider(e)
        self.assertEqual(kinds,['response.output_audio.done','response.done']);self.assertTrue(self.session.item)
        await self.session.receive_client({'type':'played_all','item_id':'item_1'})
        self.assertIsNone(self.session.item);self.assertEqual(self.client.events[-1]['type'],'listening')
        self.assertTrue(await self.session.end('test'));self.assertTrue(self.socket.closed and self.lease.released and all(s.closed for s in self.streams))
    async def test_barge_in_delete_ack_excludes_old_text_from_followup(self):
        await self.turn('user_1');await self.begin_output('reply_1','item_1')
        await self.session.receive_client({'type':'stop_response','item_id':'item_1','audio_end_ms':5})
        self.assertEqual([e for e in self.socket.sent if e['type']=='conversation.item.delete'][-1]['item_id'],'item_1')
        self.assertTrue(self.streams[0].cancelled)
        await self.event({'type':'conversation.item.deleted','item_id':'item_1'})
        await self.event({'type':'response.done','response':{'id':'reply_1','status':'cancelled','output':[]}})
        command=await self.turn('user_2')
        self.assertNotIn('item_1',[item['id'] for item in command['response']['input']])
        self.assertTrue(await self.session.end('test'));self.assertTrue(self.socket.closed and self.lease.released and all(s.closed for s in self.streams))
    async def test_vad_interrupt_requires_delete_ack_before_followup(self):
        await self.turn('user_1');await self.begin_output('reply_1','item_1')
        await self.event({'type':'input_audio_buffer.speech_started'})
        self.assertTrue(self.streams[0].cancelled)
        self.assertEqual([e for e in self.socket.sent if e['type']=='conversation.item.delete'][-1]['item_id'],'item_1')
        self.assertTrue(self.session.truncate_pending)
        await self.event({'type':'input_audio_buffer.speech_stopped'})
        await self.event({'type':'conversation.item.added','item':{'id':'user_2','type':'message','role':'user'}})
        await self.event({'type':'input_audio_buffer.committed','item_id':'user_2'})
        await self.event({'type':'response.done','response':{'id':'reply_1','status':'cancelled','output':[]}})
        self.assertEqual(len([e for e in self.socket.sent if e['type']=='response.create']),1)
        await self.event({'type':'conversation.item.deleted','item_id':'item_1'})
        command=[e for e in self.socket.sent if e['type']=='response.create'][-1]
        self.assertEqual(len([e for e in self.socket.sent if e['type']=='response.create']),2)
        self.assertNotIn('item_1',[entry['id'] for entry in command['response']['input']])
    async def test_unconfirmed_tts_close_keeps_lease_held(self):
        await self.turn('user_1');await self.begin_output('reply_1','item_1')
        async def fail_close():raise ValueError('tts_close_unconfirmed')
        self.streams[0].wait_closed=fail_close
        self.assertFalse(await self.session.end('test'))
        self.assertTrue(self.lease.held);self.assertFalse(self.lease.released)
        self.assertEqual(self.client.events[-1]['type'],'end_unconfirmed')
