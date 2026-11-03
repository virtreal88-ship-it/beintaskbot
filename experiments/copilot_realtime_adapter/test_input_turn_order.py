"""Synthetic event ordering; no real audio or provider connections."""
import unittest
from test_session import Authority, Client, Context, Lease, Ledger, Provider
from session import Session, Refused


class InputTurnOrder(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ledger=Ledger();self.provider=Provider(self.ledger)
        self.session=Session(('tenant',42,'channel','conversation'),authority=Authority(),
                             ledger=self.ledger,lease=Lease(),context=Context(),
                             provider=self.provider,client=Client())
        await self.session.start()

    async def asyncTearDown(self):
        await self.session.end('test')

    async def send(self,kind,item):
        event={'type':kind,'item_id':item}
        if kind=='conversation.item.added':event={'type':kind,'item':{'id':item,'type':'message','role':'user','content':[{'type':'input_audio'}]}}
        await self.session.receive_provider(event)

    async def test_dispatch_waits_for_matching_committed_item(self):
        await self.send('input_audio_buffer.speech_started','turn-1')
        await self.send('input_audio_buffer.speech_stopped','turn-1')
        self.assertEqual(self.ledger.events,[])
        await self.send('input_audio_buffer.committed','turn-1')
        self.assertEqual(self.ledger.events,[])
        await self.send('conversation.item.added','turn-1')
        self.assertEqual([e[0] for e in self.ledger.events],['reserve','claim'])
        self.assertEqual(self.provider.events[-1],{'type':'response.create'})

    async def test_added_before_commit_still_waits(self):
        await self.send('input_audio_buffer.speech_started','turn-1')
        await self.send('input_audio_buffer.speech_stopped','turn-1')
        await self.send('conversation.item.added','turn-1')
        self.assertEqual(self.ledger.events,[])
        await self.send('input_audio_buffer.committed','turn-1')
        self.assertEqual([e[0] for e in self.ledger.events],['reserve','claim'])

    async def test_wrong_item_fails_closed_without_response(self):
        await self.send('input_audio_buffer.speech_started','turn-1')
        await self.send('input_audio_buffer.speech_stopped','turn-1')
        with self.assertRaises(Refused):
            await self.send('input_audio_buffer.committed','turn-2')
        self.assertEqual(self.ledger.events,[])
        self.assertTrue(self.provider.closed)

    async def test_item_may_be_acknowledged_before_speech_stops(self):
        await self.send('input_audio_buffer.speech_started','turn-1')
        await self.send('input_audio_buffer.committed','turn-1')
        await self.send('conversation.item.added','turn-1')
        self.assertEqual(self.ledger.events,[])
        await self.send('input_audio_buffer.speech_stopped','turn-1')
        self.assertEqual([e[0] for e in self.ledger.events],['reserve','claim'])

    async def test_duplicate_ack_never_repeats_dispatch(self):
        await self.send('input_audio_buffer.speech_started','turn-1')
        await self.send('input_audio_buffer.speech_stopped','turn-1')
        await self.send('input_audio_buffer.committed','turn-1')
        await self.send('conversation.item.added','turn-1')
        with self.assertRaises(Refused):
            await self.send('input_audio_buffer.committed','turn-1')
        self.assertEqual([event['type'] for event in self.provider.events].count('response.create'),1)
        self.assertTrue(self.provider.closed)

    async def test_non_audio_item_never_triggers_response(self):
        await self.send('input_audio_buffer.speech_started','turn-1')
        await self.send('input_audio_buffer.speech_stopped','turn-1')
        await self.send('input_audio_buffer.committed','turn-1')
        with self.assertRaises(Refused):
            await self.session.receive_provider({'type':'conversation.item.added','item':{
                'id':'turn-1','type':'message','role':'user','content':[{'type':'input_text','text':'synthetic'}]}})
        self.assertEqual(self.ledger.events,[])
