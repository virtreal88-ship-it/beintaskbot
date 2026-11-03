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
        return {**scope,'state':'reserved','reserved_microusd':1}
    async def claim(self, scope):
        self.events.append(('claim', scope.copy()))
        return {**scope,'state':'claimed','reserved_microusd':1}
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
    mock_only=True
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
        await self.session.receive_provider({'type':'response.created','response':{'id':'response-'+str(self.session.response_number)}})

    async def test_existing_policy_and_disabled_automatic_spend(self):
        self.assertEqual(self.provider.config,{'tools':[],'create_response':False,'interrupt_response':True,'transcription':False,'reconnect':False})
        self.assertEqual(self.ledger.events,[])
        await self.turn()
        self.assertEqual([x[0] for x in self.ledger.events],['reserve','claim'])
        self.assertEqual(self.provider.events[-1],{'type':'response.create'})
        await self.session.receive_provider({'type':'response.done','response':{'id':'response-1'}})
        self.assertEqual([x[0] for x in self.ledger.events],['reserve','claim','unknown'])

    async def test_interrupt_truncates_heard_audio_before_new_response(self):
        await self.turn()
        await self.session.receive_provider({'type':'response.output_audio.delta','response_id':'response-1','item_id':'item-1','content_index':0,
                                            'delta':base64.b64encode(bytes(4800)).decode()})
        await self.session.receive_provider({'type':'input_audio_buffer.speech_started'})
        self.assertEqual(self.client.events[-1],{'type':'interrupt','item_id':'item-1'})
        await self.session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
        await self.session.receive_provider({'type':'response.done','response':{'id':'response-1'}})
        self.assertEqual(len(self.ledger.events),3)  # response waits for playback ack
        await self.session.receive_client({'type':'played','item_id':'item-1','played_ms':0})
        self.assertEqual(self.provider.events[-2],{'type':'conversation.item.truncate','item_id':'item-1','content_index':0,'audio_end_ms':0})
        self.assertEqual(self.provider.events[-1],{'type':'response.create'})
        self.assertEqual([x[0] for x in self.ledger.events],['reserve','claim','unknown','reserve','claim'])

    async def test_bad_playback_ack_fails_closed(self):
        await self.turn()
        await self.session.receive_provider({'type':'response.output_audio.delta','response_id':'response-1','item_id':'i','content_index':0,'delta':base64.b64encode(bytes(4800)).decode()})
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
            entered.set();await release.wait();return await original(scope)
        self.ledger.claim=slow_claim
        pending=asyncio.create_task(self.session.receive_provider({'type':'input_audio_buffer.speech_stopped'}))
        await entered.wait();await self.session.end('user_end');release.set()
        with self.assertRaises(Refused): await pending
        self.assertNotIn({'type':'response.create'},self.provider.events)
        self.assertEqual([x[0] for x in self.ledger.events],['reserve','claim','unknown'])

    async def test_missing_or_wrong_claim_refuses_before_response(self):
        for receipt in [None, {'state':'claimed'}, {'state':'reserved'}]:
            s=Session(('tenant',42,'channel','conversation'),authority=Authority(),ledger=Ledger(),lease=Lease(),
                      context=Context(),provider=Provider(Ledger()),client=Client())
            async def bad_claim(scope): return receipt
            s.ledger.claim=bad_claim
            await s.start()
            with self.assertRaises(Refused): await s.receive_provider({'type':'input_audio_buffer.speech_stopped'})
            self.assertNotIn({'type':'response.create'},s.provider.events)
            self.assertTrue(s.provider.closed)

    async def test_wrong_scope_claim_refused(self):
        async def bad_claim(scope): return {**scope,'channel_id':'different','state':'claimed','reserved_microusd':1}
        self.ledger.claim=bad_claim
        with self.assertRaises(Refused): await self.session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
        self.assertNotIn({'type':'response.create'},self.provider.events)

    async def test_bad_reservation_refused(self):
        async def bad_reserve(scope): return {**scope,'state':'claimed','reserved_microusd':1}
        self.ledger.reserve=bad_reserve
        with self.assertRaises(Refused): await self.session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
        self.assertNotIn({'type':'response.create'},self.provider.events)

    async def test_two_completed_turns_and_conservative_history(self):
        await self.turn()
        delta={'type':'response.output_audio.delta','response_id':'response-1','item_id':'a','content_index':0,
               'delta':base64.b64encode(bytes(480)).decode()}
        await self.session.receive_provider(delta)
        await self.session.receive_provider({'type':'response.output_audio.done','response_id':'response-1','item_id':'a','content_index':0})
        await self.session.receive_provider({'type':'response.done','response':{'id':'response-1'}})
        await asyncio.sleep(.02)
        await self.session.receive_client({'type':'played_all','item_id':'a'})
        self.assertEqual(self.provider.events[-1]['audio_end_ms'],0)
        await self.turn()
        await self.session.receive_provider({**delta,'response_id':'response-2','item_id':'b'})
        self.assertEqual(self.client.events[-1]['item_id'],'b')

    async def test_duplicate_response_or_wrong_id_closes(self):
        await self.turn()
        with self.assertRaises(Refused): await self.session.receive_provider({'type':'response.created','response':{'id':'another'}})
        self.assertTrue(self.provider.closed)

    async def test_wrong_response_id_closes(self):
        await self.turn()
        with self.assertRaises(Refused):
            await self.session.receive_provider({'type':'response.output_audio.delta','response_id':'different','item_id':'a','content_index':0,'delta':base64.b64encode(bytes(480)).decode()})
        self.assertTrue(self.provider.closed)

    async def test_missing_playback_ack_cannot_start_next_response(self):
        await self.turn()
        await self.session.receive_provider({'type':'response.output_audio.delta','response_id':'response-1','item_id':'a','content_index':0,'delta':base64.b64encode(bytes(480)).decode()})
        await self.session.receive_provider({'type':'response.output_audio.done','response_id':'response-1','item_id':'a','content_index':0})
        await self.session.receive_provider({'type':'response.done','response':{'id':'response-1'}})
        await self.session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
        self.assertEqual(sum(e['type']=='response.create' for e in self.provider.events),1)
        self.assertEqual(len(self.ledger.events),3)

    async def test_open_failure_and_unknown_close_keeps_lease(self):
        class BrokenProvider(Provider):
            async def open(self,*args,**kwargs): raise RuntimeError('failed')
            async def close(self): raise RuntimeError('unknown')
        lease=Lease();p=BrokenProvider(self.ledger)
        s=Session(('tenant',42,'channel','conversation'),authority=Authority(),ledger=self.ledger,lease=lease,context=Context(),provider=p,client=Client())
        with self.assertRaises(RuntimeError): await s.start()
        self.assertTrue(lease.held)

    async def test_audio_after_delayed_authority_check_does_not_send_on_closed_session(self):
        entered=asyncio.Event();release=asyncio.Event();original=self.auth.check
        async def slow(scope,stage):
            if stage=='input':entered.set();await release.wait()
            await original(scope,stage)
        self.auth.check=slow
        pending=asyncio.create_task(self.session.receive_client({'type':'audio','pcm':bytes(480)}))
        await entered.wait();await self.session.end('user_end');release.set()
        with self.assertRaises(Refused): await pending
        self.assertNotIn('input_audio_buffer.append',[item['type'] for item in self.provider.events])

    async def test_shutdown_drains_provider_send_before_releasing_lease(self):
        entered=asyncio.Event();release=asyncio.Event();original=self.provider.send
        async def slow(event):
            if event['type']=='input_audio_buffer.append':entered.set();await release.wait()
            await original(event)
        self.provider.send=slow
        sending=asyncio.create_task(self.session.receive_client({'type':'audio','pcm':bytes(480)}))
        await entered.wait();closing=asyncio.create_task(self.session.end('user_end'))
        await asyncio.sleep(.03)
        self.assertTrue(self.lease.held);self.assertNotIn('ended',[e['type'] for e in self.client.events]);self.assertFalse(closing.done())
        release.set();await sending;self.assertTrue(await closing);self.assertFalse(self.lease.held)
        self.assertEqual(self.provider.events[-1]['type'],'input_audio_buffer.append')
        self.assertEqual(self.client.events[-1]['type'],'ended')

    async def test_shutdown_drains_client_send_before_ended(self):
        await self.turn();entered=asyncio.Event();release=asyncio.Event();original=self.client.send
        async def slow(event):
            if event['type']=='audio':entered.set();await release.wait()
            await original(event)
        self.client.send=slow
        sending=asyncio.create_task(self.session.receive_provider({'type':'response.output_audio.delta','response_id':'response-1',
            'item_id':'a','content_index':0,'delta':base64.b64encode(bytes(480)).decode()}))
        await entered.wait();closing=asyncio.create_task(self.session.end('user_end'));await asyncio.sleep(.03)
        self.assertTrue(self.lease.held);self.assertNotIn('ended',[e['type'] for e in self.client.events])
        release.set();await sending;self.assertTrue(await closing);self.assertEqual([e['type'] for e in self.client.events][-2:],['audio','ended'])

    async def test_stuck_send_returns_unconfirmed_and_keeps_lease(self):
        entered=asyncio.Event();release=asyncio.Event();original=self.provider.send
        async def stuck(event):
            entered.set();await release.wait();await original(event)
        self.provider.send=stuck;self.session.shutdown_timeout=.03
        sending=asyncio.create_task(self.session.receive_client({'type':'audio','pcm':bytes(480)}))
        await entered.wait();self.assertFalse(await self.session.end('user_end'))
        self.assertTrue(self.lease.held);self.assertNotIn('ended',[e['type'] for e in self.client.events])
        release.set();await sending
        self.assertTrue(self.lease.held);self.assertFalse(await self.session.end('again'))

    async def test_stuck_client_returns_unconfirmed_and_keeps_lease(self):
        await self.turn();entered=asyncio.Event();release=asyncio.Event();original=self.client.send
        async def stuck(event):
            if event['type']=='audio':entered.set();await release.wait()
            await original(event)
        self.client.send=stuck;self.session.shutdown_timeout=.03
        sending=asyncio.create_task(self.session.receive_provider({'type':'response.output_audio.delta','response_id':'response-1',
            'item_id':'a','content_index':0,'delta':base64.b64encode(bytes(480)).decode()}))
        await entered.wait();self.assertFalse(await self.session.end('user_end'))
        self.assertTrue(self.lease.held);self.assertNotIn('ended',[e['type'] for e in self.client.events])
        release.set();await sending;self.assertTrue(self.lease.held)

    async def test_cancel_suppressed_by_close_never_releases_lease(self):
        entered=asyncio.Event();release=asyncio.Event()
        async def stubborn_close():
            entered.set()
            try:await release.wait()
            except asyncio.CancelledError:await release.wait()
        self.provider.close=stubborn_close;self.session.shutdown_timeout=.03
        self.assertFalse(await self.session.end('user_end'))
        self.assertTrue(entered.is_set());self.assertTrue(self.lease.held)
        self.assertNotIn('ended',[e['type'] for e in self.client.events])
        release.set();await asyncio.sleep(.01);self.assertTrue(self.lease.held)

    async def test_stop_while_open_suppresses_cancel_never_shows_ready(self):
        entered=asyncio.Event();release=asyncio.Event();cancelled=asyncio.Event();ledger=Ledger();lease=Lease();client=Client()
        class Slow(Provider):
            async def open(self,*args,**kwargs):
                entered.set()
                try:await release.wait()
                except asyncio.CancelledError:
                    cancelled.set();await release.wait()
        provider=Slow(ledger)
        s=Session(('tenant',42,'channel','conversation'),authority=Authority(),ledger=ledger,lease=lease,context=Context(),provider=provider,client=client)
        opening=asyncio.create_task(s.start());await entered.wait()
        self.assertFalse(await s.end('disconnected'));await cancelled.wait()
        self.assertTrue(lease.held);self.assertEqual(client.events,[])
        release.set()
        with self.assertRaises(Refused):await opening
        self.assertEqual(client.events,[]);self.assertTrue(provider.closed);self.assertFalse(lease.held)

    async def test_stop_before_late_lease_claim_never_opens_provider(self):
        entered=asyncio.Event();release=asyncio.Event();ledger=Ledger();client=Client()
        class SlowLease(Lease):
            async def claim(self,scope):entered.set();await release.wait();return await super().claim(scope)
        lease=SlowLease();provider=Provider(ledger)
        s=Session(('tenant',42,'channel','conversation'),authority=Authority(),ledger=ledger,lease=lease,context=Context(),provider=provider,client=client)
        opening=asyncio.create_task(s.start());await entered.wait();self.assertFalse(await s.end('disconnected'))
        release.set()
        with self.assertRaises(Refused):await opening
        self.assertFalse(lease.held);self.assertIsNone(provider.config);self.assertEqual(client.events,[])

    async def test_revocation_during_open_prevents_ready(self):
        entered=asyncio.Event();release=asyncio.Event();ledger=Ledger();lease=Lease();client=Client();auth=Authority()
        class Slow(Provider):
            async def open(self,*args,**kwargs):entered.set();await release.wait()
        provider=Slow(ledger)
        s=Session(('tenant',42,'channel','conversation'),authority=auth,ledger=ledger,lease=lease,context=Context(),provider=provider,client=client)
        opening=asyncio.create_task(s.start());await entered.wait();auth.allowed=False;release.set()
        with self.assertRaises(Refused):await opening
        self.assertTrue(provider.closed);self.assertEqual(client.events,[]);self.assertFalse(lease.held)

    async def test_end_during_ready_drains_before_single_lease_release(self):
        entered=asyncio.Event();release=asyncio.Event();ledger=Ledger();lease=Lease();provider=Provider(ledger)
        class SlowReady(Client):
            async def send(self,event):
                if event['type']=='ready':entered.set();await release.wait()
                await super().send(event)
        client=SlowReady()
        s=Session(('tenant',42,'channel','conversation'),authority=Authority(),ledger=ledger,lease=lease,context=Context(),provider=provider,client=client)
        opening=asyncio.create_task(s.start());await entered.wait()
        closing=asyncio.create_task(s.end('disconnected'));await asyncio.sleep(.03)
        self.assertTrue(lease.held);self.assertFalse(closing.done())
        release.set();self.assertTrue(await closing)
        with self.assertRaises(Refused):await opening
        self.assertFalse(lease.held);self.assertEqual([e['type'] for e in client.events],['ready','ended'])


if __name__ == '__main__': unittest.main()
