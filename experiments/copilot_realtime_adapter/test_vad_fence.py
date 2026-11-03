"""Adversarial synthetic VAD races, no real provider or credentials."""
import asyncio
import unittest
from session import Session, Refused
from test_session import Authority,Client,Context,Lease,Ledger,Provider

class BlockReserve(Ledger):
    def __init__(self):
        super().__init__(); self.enter=asyncio.Event(); self.go=asyncio.Event()
    async def reserve(self,scope):
        self.enter.set(); await self.go.wait(); return await super().reserve(scope)

class BlockOutput(Authority):
    def __init__(self):
        super().__init__();self.first=True;self.enter=asyncio.Event();self.go=asyncio.Event()
    async def check(self,scope,stage):
        if stage=='output' and self.first:
            self.first=False;self.enter.set();await self.go.wait()
        await super().check(scope,stage)

class BlockClaim(Ledger):
    def __init__(self):
        super().__init__();self.enter=asyncio.Event();self.go=asyncio.Event()
    async def claim(self,scope):
        self.enter.set();await self.go.wait();return await super().claim(scope)

class Fencing(unittest.IsolatedAsyncioTestCase):
    async def make(self,auth=None,ledger=None):
        self.ledger=ledger or Ledger();self.provider=Provider(self.ledger)
        self.session=Session(('tenant',42,'channel','conversation'),authority=auth or Authority(),
            ledger=self.ledger,lease=Lease(),context=Context(),provider=self.provider,client=Client())
        await self.session.start()
    async def tear(self):
        await self.session.end('test')
    async def event(self,k,i):
        e={'type':k,'item_id':i}
        if k=='conversation.item.added':e={'type':k,'item':{'id':i,'type':'message','role':'user','content':[{'type':'input_audio'}]}}
        return await self.session.receive_provider(e)
    async def test_started_during_reserve_fences_response(self):
        l=BlockReserve();await self.make(ledger=l)
        try:
            for k in ['input_audio_buffer.speech_started','input_audio_buffer.speech_stopped','input_audio_buffer.committed']:
                await self.event(k,'A')
            completing=asyncio.create_task(self.event('conversation.item.added','A'))
            await asyncio.wait_for(l.enter.wait(),1)
            started=asyncio.create_task(self.event('input_audio_buffer.speech_started','B'))
            await asyncio.sleep(.01);l.go.set()
            await asyncio.gather(completing,started,return_exceptions=True)
            self.assertNotIn('response.create',[e['type'] for e in self.provider.events])
        finally:l.go.set();await self.tear()
    async def test_mute_pending_unobserved_input_ends_before_unmute(self):
        a=BlockOutput();await self.make(auth=a)
        try:
            await self.session.receive_client({'type':'audio','pcm':bytes(480)})
            started=asyncio.create_task(self.event('input_audio_buffer.speech_started','A'))
            await asyncio.wait_for(a.enter.wait(),1)
            await self.session.receive_client({'type':'mute','muted':True})
            self.assertTrue(self.session.closed)
            with self.assertRaises(Refused):await self.session.receive_client({'type':'mute','muted':False})
            a.go.set();await asyncio.gather(started,return_exceptions=True)
            self.assertNotIn('response.create',[e['type'] for e in self.provider.events])
        finally:a.go.set();await self.tear()

    async def test_started_during_committing_claim_keeps_unknown_hold(self):
        l=BlockClaim();await self.make(ledger=l)
        try:
            for k in ['input_audio_buffer.speech_started','input_audio_buffer.speech_stopped','input_audio_buffer.committed']:
                await self.event(k,'A')
            completing=asyncio.create_task(self.event('conversation.item.added','A'))
            await asyncio.wait_for(l.enter.wait(),1)
            started=asyncio.create_task(self.event('input_audio_buffer.speech_started','B'))
            await asyncio.sleep(.01);l.go.set()
            await asyncio.gather(completing,started,return_exceptions=True)
            self.assertNotIn('response.create',[e['type'] for e in self.provider.events])
            self.assertIn('unknown',[e[0] for e in l.events])
        finally:l.go.set();await self.tear()

    async def test_already_observed_queued_speech_prevents_dispatch(self):
        await self.make()
        try:
            for k in ['input_audio_buffer.speech_started','input_audio_buffer.speech_stopped','input_audio_buffer.committed']:
                await self.event(k,'A')
            # The network reader can see B while A remains in its queue.
            self.session.observe_provider_event({'type':'input_audio_buffer.speech_started','item_id':'B'})
            await self.event('conversation.item.added','A')
            self.assertEqual(self.ledger.events,[])
            await self.session.receive_provider({'type':'input_audio_buffer.speech_started','item_id':'B'},observed=True)
            self.assertNotIn('response.create',[e['type'] for e in self.provider.events])
        finally:await self.tear()

    async def test_speech_during_inflight_send_quarantines_until_drain(self):
        await self.make();entered=asyncio.Event();go=asyncio.Event()
        original=self.provider.send
        async def delayed(event):
            if event.get('type')=='response.create':
                entered.set();await go.wait()
            await original(event)
        self.provider.send=delayed
        try:
            for k in ['input_audio_buffer.speech_started','input_audio_buffer.speech_stopped','input_audio_buffer.committed']:
                await self.event(k,'A')
            completing=asyncio.create_task(self.event('conversation.item.added','A'))
            await asyncio.wait_for(entered.wait(),1)
            self.session.observe_provider_event({'type':'input_audio_buffer.speech_started','item_id':'B'})
            for _ in range(100):
                if self.session.closed:break
                await asyncio.sleep(.005)
            self.assertTrue(self.session.closed)
            await asyncio.sleep(self.session.shutdown_timeout+.05)
            self.assertTrue(self.session.lease.held)
            # The fake transport may still complete its in-flight write;
            # local closure cannot prove remote cancellation.
            go.set();await asyncio.gather(completing,return_exceptions=True)
            self.assertTrue(self.session.lease.held)
        finally:go.set();await self.tear()
