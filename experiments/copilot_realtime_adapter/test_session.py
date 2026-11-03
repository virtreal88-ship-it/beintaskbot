import asyncio
import base64
import unittest
from session import Session, Refused


class Authority:
    def __init__(self): self.allowed = True
    async def check(self, scope, stage):
        if not self.allowed: raise Refused('revoked')


class Ledger:
    def __init__(self): self.events=[]; self.deny=False
    async def reserve(self, scope):
        if self.deny: raise Refused('budget')
        self.events.append(('reserve', scope.copy()))
    async def claim(self, scope): self.events.append(('claim', scope.copy()))
    async def mark_unknown(self, scope): self.events.append(('unknown', scope.copy()))


class Lease:
    def __init__(self): self.held=False
    async def claim(self, scope):
        if self.held: return None
        self.held=True; return 'fence'
    async def assert_current(self, scope, fence):
        if not self.held or fence != 'fence': raise Refused('lease_lost')
    async def release(self, scope, fence): self.held=False


class Context:
    async def read_authorized(self, scope): return {'existing_copilot_instructions':'synthetic'}


class Provider:
    def __init__(self, ledger): self.events=[];self.config=None;self.closed=False;self.ledger=ledger
    async def open(self, snapshot, **config): self.config=config
    async def send(self, event):
        if event['type']=='response.create':
            assert [x[0] for x in self.ledger.events][-2:] == ['reserve','claim']
        self.events.append(event)
    async def close(self): self.closed=True


class Client:
    def __init__(self): self.events=[]
    async def send(self, event): self.events.append(event)


class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.auth=Authority();self.ledger=Ledger();self.lease=Lease();self.context=Context()
        self.provider=Provider(self.ledger);self.client=Client()
        self.session=Session(('tenant',42,'channel','conversation'), authority=self.auth,ledger=self.ledger,
            lease=self.lease,context=self.context,provider=self.provider,client=self.client,
            max_seconds=1,idle_seconds=.8,poll_seconds=.02)
        await self.session.start()

    async def asyncTearDown(self): await self.session.end('test_end')

    async def turn(self):
        await self.session.receive_provider({'type':'input_audio_buffer.speech_started'})
        await self.session.receive_client({'type':'audio','pcm':bytes(4800)})
        await self.session.receive_provider({'type':'input_audio_buffer.speech_stopped'})

    async def test_existing_policy_and_disabled_automatic_spend(self):
        self.assertEqual(self.provider.config,{'tools':[],'create_response':False,'interrupt_response':True,'transcription':False,'reconnect':False})
        self.assertEqual(self.ledger.events,[])
        await self.turn()
        self.assertEqual([x[0] for x in self.ledger.events],['reserve','claim'])
        self.assertEqual(self.provider.events[-1],{'type':'response.create'})
        await self.session.receive_provider({'type':'response.done'})
        self.assertEqual([x[0] for x in self.ledger.events],['reserve','claim','unknown'])

    async def test_interrupt_truncates_heard_audio_before_new_response(self):
        await self.turn()
        await self.session.receive_provider({'type':'response.output_audio.delta','item_id':'item-1','content_index':0,
                                            'delta':base64.b64encode(bytes(4800)).decode()})
        await self.session.receive_provider({'type':'input_audio_buffer.speech_started'})
        self.assertEqual(self.client.events[-1],{'type':'interrupt','item_id':'item-1'})
        await self.session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
        await self.session.receive_provider({'type':'response.done'})
        self.assertEqual(len(self.ledger.events),3)  # response waits for playback ack
        await self.session.receive_client({'type':'played','item_id':'item-1','played_ms':50})
        self.assertEqual(self.provider.events[-2],{'type':'conversation.item.truncate','item_id':'item-1','content_index':0,'audio_end_ms':50})
        self.assertEqual(self.provider.events[-1],{'type':'response.create'})
        self.assertEqual([x[0] for x in self.ledger.events],['reserve','claim','unknown','reserve','claim'])

    async def test_bad_playback_ack_fails_closed(self):
        await self.turn()
        await self.session.receive_provider({'type':'response.output_audio.delta','item_id':'i','content_index':0,'delta':base64.b64encode(bytes(4800)).decode()})
        await self.session.receive_provider({'type':'input_audio_buffer.speech_started'})
        with self.assertRaises(Refused): await self.session.receive_client({'type':'played','item_id':'i','played_ms':1000})
        self.assertTrue(self.provider.closed)

    async def test_client_cannot_send_provider_commands_or_tools(self):
        with self.assertRaises(Refused): await self.session.receive_client({'type':'response.create'})
        self.assertTrue(self.provider.closed)
        self.assertEqual(self.ledger.events,[])

    async def test_unsolicited_provider_response_is_closed(self):
        with self.assertRaises(Refused): await self.session.receive_provider({'type':'response.created'})
        self.assertTrue(self.provider.closed)

    async def test_revocation_and_timeout_close(self):
        self.auth.allowed=False
        await asyncio.sleep(.08)
        self.assertTrue(self.provider.closed)
        self.assertFalse(self.lease.held)

    async def test_mute_discards_audio(self):
        await self.session.receive_client({'type':'mute','muted':True})
        await self.session.receive_client({'type':'audio','pcm':bytes(100)})
        self.assertEqual(self.provider.events,[{'type':'input_audio_buffer.clear'}])

    async def test_budget_refusal_prevents_dispatch_and_closes(self):
        self.ledger.deny=True
        with self.assertRaises(Refused): await self.turn()
        self.assertNotIn({'type':'response.create'},self.provider.events)
        self.assertTrue(self.provider.closed)

    async def test_idle_closes_without_input(self):
        self.session.idle_seconds=.05
        await asyncio.sleep(.11)
        self.assertTrue(self.provider.closed)

    async def test_second_session_cannot_share_lease(self):
        other=Session(('tenant',42,'channel','conversation'),authority=self.auth,ledger=self.ledger,lease=self.lease,
                      context=self.context,provider=Provider(self.ledger),client=Client())
        with self.assertRaises(Refused): await other.start()
        self.assertTrue(self.lease.held)

    async def test_end_during_budget_claim_never_dispatches_late_response(self):
        entered=asyncio.Event();release=asyncio.Event();original=self.ledger.claim
        async def slow_claim(scope):
            entered.set();await release.wait();await original(scope)
        self.ledger.claim=slow_claim
        pending=asyncio.create_task(self.session.receive_provider({'type':'input_audio_buffer.speech_stopped'}))
        await entered.wait();await self.session.end('user_end');release.set()
        with self.assertRaises(Refused): await pending
        self.assertNotIn({'type':'response.create'},self.provider.events)
        self.assertEqual([x[0] for x in self.ledger.events],['reserve','claim','unknown'])


if __name__ == '__main__': unittest.main()
