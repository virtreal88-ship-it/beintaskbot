import asyncio
import base64
import unittest
from types import SimpleNamespace
from session import Session, Refused


class Authority:
    def __init__(self): self.allowed = True
    async def check(self, scope, stage):
        if not self.allowed: raise Refused('revoked')


class Ledger:
    def __init__(self): self.events=[]; self.deny=False
    async def reserve_session(self,scope,session_id):
        return {'state':'reserved','scope':scope,'session_id':session_id,'reserved_microusd':5_000_000}
    async def reserve(self, scope):
        if self.deny: raise Refused('budget')
        self.events.append(('reserve', scope.copy()))
        return {**scope,'state':'reserved','reserved_microusd':1}
    async def claim(self, scope):
        self.events.append(('claim', scope.copy()))
        return {**scope,'state':'claimed','reserved_microusd':1}
    async def mark_unknown(self, scope):
        self.events.append(('unknown', scope.copy()))
        return {**scope,'state':'unknown','reserved_microusd':1}
    async def note_actual(self, scope, amount):
        self.events.append(('settled', scope.copy(), amount))
        return {**scope,'state':'settled','actual_microusd':amount}


class Lease:
    def __init__(self): self.held=False
    async def claim(self, scope):
        if self.held: return None
        self.held=True; return 1
    async def assert_current(self, scope, fence):
        if not self.held or fence != 1: raise Refused('lease_lost')
    async def confirm_provider_open(self, scope, fence):
        await self.assert_current(scope, fence)
        return True
    async def release(self, scope, fence): self.held=False;return True


class Context:
    def __init__(self):self.checks=0;self.fail_at=None
    async def read_authorized(self, scope): return {'existing_copilot_instructions':'synthetic'}
    async def revalidate(self, scope):
        self.checks+=1
        if self.fail_at==self.checks:raise Refused('stale_context')
        return True


class Provider:
    mock_only=True
    def __init__(self, ledger): self.events=[];self.config=None;self.closed=False;self.ledger=ledger
    async def open(self, snapshot, **config): self.config=config
    async def bind_claim(self,scope,receipt):
        if not isinstance(receipt,dict) or receipt.get('state')!='claimed' or receipt.get('request_id')!=scope['request_id']:
            raise Refused('claim_invalid')
    async def send(self, event):
        if event['type']=='response.create':
            assert [x[0] for x in self.ledger.events][-2:] == ['reserve','claim']
        self.events.append(event)
    async def close(self): self.closed=True


class Client:
    def __init__(self): self.events=[]
    async def send(self, event): self.events.append(event)


async def commit_input_turn(session, item_id):
    await session.receive_provider({'type':'input_audio_buffer.speech_started'})
    await session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
    await session.receive_provider({'type':'input_audio_buffer.committed','item_id':item_id})


class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.auth=Authority();self.ledger=Ledger();self.lease=Lease();self.context=Context()
        self.provider=Provider(self.ledger);self.client=Client()
        self.input_id=0
        self.session=Session(('tenant',42,'channel','conversation'), authority=self.auth,ledger=self.ledger,
            lease=self.lease,context=self.context,provider=self.provider,client=self.client,
            max_seconds=1,idle_seconds=.8,poll_seconds=.02)
        await self.session.start()

    async def asyncTearDown(self): await self.session.end('test_end')

    async def test_missing_session_budget_never_opens_provider_and_releases_lease(self):
        class NoSessionBudget(Ledger):
            async def reserve_session(self,scope,session_id):raise Refused('budget_unavailable')
        ledger=NoSessionBudget();provider=Provider(ledger);lease=Lease()
        session=Session(('tenant',42,'channel','another'),authority=Authority(),ledger=ledger,lease=lease,
                        context=Context(),provider=provider,client=Client())
        with self.assertRaisesRegex(Refused,'budget_unavailable'):await session.start()
        self.assertIsNone(provider.config)
        self.assertFalse(lease.held)

    async def test_unmatched_session_budget_never_opens_provider(self):
        class WrongSessionBudget(Ledger):
            async def reserve_session(self,scope,session_id):
                return {'state':'reserved','scope':scope,'session_id':'other','reserved_microusd':5_000_000}
        ledger=WrongSessionBudget();provider=Provider(ledger);lease=Lease()
        session=Session(('tenant',42,'channel','another'),authority=Authority(),ledger=ledger,lease=lease,
                        context=Context(),provider=provider,client=Client())
        with self.assertRaisesRegex(Refused,'session_budget_unconfirmed'):await session.start()
        self.assertIsNone(provider.config)
        self.assertFalse(lease.held)

    async def test_lost_starting_lease_never_opens_provider(self):
        class LostBeforeOpen(Lease):
            async def assert_current(self,scope,fence):raise Refused('lease_lost')
        ledger=Ledger();provider=Provider(ledger);lease=LostBeforeOpen()
        session=Session(('tenant',42,'channel','another'),authority=Authority(),ledger=ledger,lease=lease,
                        context=Context(),provider=provider,client=Client())
        with self.assertRaisesRegex(Refused,'lease_lost'):await session.start()
        self.assertIsNone(provider.config)
        self.assertFalse(lease.held)

    async def turn(self):
        self.input_id+=1
        item_id=f'user-{self.input_id}'
        await self.session.receive_provider({'type':'input_audio_buffer.speech_started'})
        await self.session.receive_client({'type':'audio','pcm':bytes(4800)})
        await self.session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
        await self.session.receive_provider({'type':'input_audio_buffer.committed','item_id':item_id})
        await self.session.receive_provider({'type':'conversation.item.input_audio_transcription.completed',
            'item_id':item_id,'content_index':0,'transcript':'synthetic input','usage':{'type':'duration','seconds':3}})
        await self.session.receive_provider({'type':'response.created','response':{'id':'response-'+str(self.session.response_number)}})

    async def test_existing_policy_and_disabled_automatic_spend(self):
        self.assertEqual(self.provider.config,{'tools':[],'create_response':False,'interrupt_response':False,'transcription':'gpt-transcribe','reconnect':False})
        self.assertEqual(self.ledger.events,[])
        await self.turn()
        self.assertEqual([x[0] for x in self.ledger.events],['reserve','claim'])
        self.assertEqual(self.provider.events[-1],{'type':'response.create'})
        await self.session.receive_provider({'type':'response.done','response':{'id':'response-1'}})
        self.assertEqual([x[0] for x in self.ledger.events],['reserve','claim','unknown'])

    async def test_response_waits_for_exact_committed_input_and_ignores_replays(self):
        await self.session.receive_provider({'type':'input_audio_buffer.speech_started'})
        await self.session.receive_client({'type':'audio','pcm':bytes(4800)})
        await self.session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
        self.assertNotIn({'type':'response.create'},self.provider.events)
        self.assertEqual(self.ledger.events,[])
        await self.session.receive_provider({'type':'input_audio_buffer.committed','item_id':'user-exact-1'})
        self.assertEqual(self.provider.events[-1],{'type':'response.create'})
        await self.session.receive_provider({'type':'input_audio_buffer.committed','item_id':'user-exact-1'})
        await self.session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
        self.assertEqual(sum(event['type']=='response.create' for event in self.provider.events),1)
        self.assertEqual([event[0] for event in self.ledger.events],['reserve','claim'])

    async def test_response_done_settles_only_provider_reported_usage(self):
        await self.turn()
        await self.session.receive_provider({'type':'conversation.item.input_audio_transcription.completed',
            'item_id':'user-1','content_index':0,'transcript':'synthetic input','usage':{'type':'duration','seconds':3}})
        usage={'input_tokens':10,'output_tokens':10,'total_tokens':20,
               'input_token_details':{'text_tokens':5,'audio_tokens':5,'image_tokens':0,'cached_tokens':2,
                                      'cached_tokens_details':{'text_tokens':2,'audio_tokens':0,'image_tokens':0}},
               'output_token_details':{'text_tokens':3,'audio_tokens':7}}
        await self.session.receive_provider({'type':'response.done','response':{'id':'response-1','output':[],'usage':usage}})
        self.assertEqual(self.ledger.events[-1][0],'settled')
        self.assertEqual(self.ledger.events[-1][2],426)

    async def test_duplicate_audio_completion_is_not_forwarded_twice(self):
        await self.turn()
        event={'type':'response.output_audio.done','response_id':'response-1','item_id':'audio-1','content_index':0}
        await self.session.receive_provider({'type':'response.output_audio.delta','response_id':'response-1',
            'item_id':'audio-1','content_index':0,'delta':base64.b64encode(bytes(480)).decode()})
        await self.session.receive_provider(event)
        await self.session.receive_provider(event)
        self.assertEqual(sum(item['type']=='audio_done' for item in self.client.events),1)

    async def test_response_waits_for_separate_transcription_usage_before_settlement_or_next_turn(self):
        await commit_input_turn(self.session,'billable-input')
        await self.session.receive_provider({'type':'response.created','response':{'id':'response-1'}})
        usage={'input_tokens':10,'output_tokens':10,'total_tokens':20,
               'input_token_details':{'text_tokens':5,'audio_tokens':5,'image_tokens':0,'cached_tokens':2,
                                      'cached_tokens_details':{'text_tokens':2,'audio_tokens':0,'image_tokens':0}},
               'output_token_details':{'text_tokens':3,'audio_tokens':7}}
        await self.session.receive_provider({'type':'response.done','response':{'id':'response-1','output':[],'usage':usage}})
        self.assertIsNotNone(self.session.current_reservation)
        self.assertEqual([item[0] for item in self.ledger.events],['reserve','claim'])
        await commit_input_turn(self.session,'next-input')
        self.assertEqual(sum(event['type']=='response.create' for event in self.provider.events),1)
        await self.session.end('missing_transcription_usage')
        self.assertEqual([item[0] for item in self.ledger.events],['reserve','claim','unknown'])

    async def test_commit_before_stop_waits_for_matching_vad_turn(self):
        await self.session.receive_provider({'type':'input_audio_buffer.speech_started'})
        await self.session.receive_provider({'type':'input_audio_buffer.committed','item_id':'user-exact-2'})
        self.assertNotIn({'type':'response.create'},self.provider.events)
        await self.session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
        self.assertEqual(sum(event['type']=='response.create' for event in self.provider.events),1)

    async def test_unmatched_commit_fails_closed(self):
        with self.assertRaisesRegex(Refused,'unmatched_committed_input'):
            await self.session.receive_provider({'type':'input_audio_buffer.committed','item_id':'foreign-input'})
        self.assertTrue(self.provider.closed)
        self.assertEqual(self.ledger.events,[])

    async def test_disconnect_before_commit_never_dispatches_or_reserves(self):
        await self.session.receive_provider({'type':'input_audio_buffer.speech_started'})
        await self.session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
        self.assertTrue(await self.session.end('disconnected'))
        self.assertEqual(self.ledger.events,[])
        self.assertNotIn({'type':'response.create'},self.provider.events)
        self.assertTrue(self.provider.closed)
        self.assertFalse(self.lease.held)

    async def test_interrupt_truncates_heard_audio_before_new_response(self):
        await self.turn()
        await self.session.receive_provider({'type':'response.output_audio.delta','response_id':'response-1','item_id':'item-1','content_index':0,
                                            'delta':base64.b64encode(bytes(4800)).decode()})
        await self.session.receive_provider({'type':'input_audio_buffer.speech_started'})
        self.assertEqual(self.client.events[-1],{'type':'interrupt','item_id':'item-1'})
        self.assertEqual(self.provider.events[-1],{'type':'response.cancel','response_id':'response-1'})
        await self.session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
        await self.session.receive_provider({'type':'input_audio_buffer.committed','item_id':'user-2'})
        await self.session.receive_provider({'type':'response.done','response':{'id':'response-1'}})
        self.assertEqual(len(self.ledger.events),3)  # response waits for playback ack
        await self.session.receive_client({'type':'played','item_id':'item-1','played_ms':0})
        self.assertEqual(self.provider.events[-1],{'type':'conversation.item.truncate','item_id':'item-1','content_index':0,'audio_end_ms':0})
        self.assertEqual(len(self.ledger.events),3) # also wait for server truncate acknowledgement
        await self.session.receive_provider({'type':'conversation.item.truncated','item_id':'item-1','content_index':0,'audio_end_ms':0})
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

    async def test_provider_without_claim_binding_is_rejected(self):
        class NoBinding:mock_only=True
        with self.assertRaisesRegex(Refused,'provider_claim_binding_required'):
            Session(('tenant',42,'channel','conversation'),authority=self.auth,ledger=self.ledger,
                    lease=Lease(),context=self.context,provider=NoBinding(),client=Client())

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
        self.assertEqual(self.session.input_bytes,0)

    async def test_cumulative_audio_limit_closes_before_extra_provider_input(self):
        self.session.max_input_bytes=200
        await self.session.receive_client({'type':'audio','pcm':bytes(120)})
        await self.session.receive_client({'type':'audio','pcm':bytes(80)})
        with self.assertRaisesRegex(Refused,'session_input_limit'):
            await self.session.receive_client({'type':'audio','pcm':bytes(2)})
        inputs=[event for event in self.provider.events if event['type']=='input_audio_buffer.append']
        self.assertEqual(sum(len(base64.b64decode(event['audio'])) for event in inputs),200)
        self.assertTrue(self.provider.closed)
        self.assertFalse(self.lease.held)

    async def test_concurrent_audio_cannot_exceed_session_limit(self):
        self.session.max_input_bytes=100
        results=await asyncio.gather(*(self.session.receive_client({'type':'audio','pcm':bytes(60)})
                                       for _ in range(2)),return_exceptions=True)
        self.assertEqual(sum(isinstance(result,Refused) for result in results),1)
        self.assertEqual(self.session.input_bytes,60)
        self.assertEqual(len([event for event in self.provider.events if event['type']=='input_audio_buffer.append']),1)

    async def test_expired_session_rejects_audio_before_watchdog_wakes(self):
        self.session.watchdog.cancel()
        self.session.started-=2
        with self.assertRaisesRegex(Refused,'session_duration_limit'):
            await self.session.receive_client({'type':'audio','pcm':bytes(100)})
        self.assertFalse(any(event['type']=='input_audio_buffer.append' for event in self.provider.events))
        self.assertTrue(self.provider.closed)

    async def test_expired_session_rejects_response_before_watchdog_wakes(self):
        self.session.watchdog.cancel()
        await self.session.receive_provider({'type':'input_audio_buffer.speech_started'})
        await self.session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
        self.session.started-=2
        with self.assertRaisesRegex(Refused,'session_duration_limit'):
            await self.session.receive_provider({'type':'input_audio_buffer.committed','item_id':'expired-input'})
        self.assertFalse(any(event['type']=='response.create' for event in self.provider.events))
        self.assertTrue(self.provider.closed)

    async def test_budget_refusal_prevents_dispatch_and_closes(self):
        self.ledger.deny=True
        with self.assertRaises(Refused): await self.turn()
        self.assertNotIn({'type':'response.create'},self.provider.events)
        self.assertTrue(self.provider.closed)

    async def test_revalidates_before_reserve_and_after_committed_claim(self):
        await self.turn();self.assertEqual(self.context.checks,3)

    async def test_stale_context_never_reaches_provider_open(self):
        context=Context();context.fail_at=1;ledger=Ledger();provider=Provider(ledger);lease=Lease()
        session=Session(('tenant',42,'channel','conversation'),authority=Authority(),ledger=ledger,lease=lease,
                        context=context,provider=provider,client=Client())
        with self.assertRaisesRegex(Refused,'stale_context'):await session.start()
        self.assertIsNone(provider.config);self.assertFalse(lease.held);self.assertEqual(ledger.events,[])

    async def test_changed_context_after_claim_never_dispatches_and_keeps_hold(self):
        self.context.fail_at=3
        with self.assertRaisesRegex(Refused,'stale_context'):await self.turn()
        self.assertEqual([x[0] for x in self.ledger.events],['reserve','claim','unknown'])
        self.assertNotIn({'type':'response.create'},self.provider.events)

    async def test_idle_closes_without_input(self):
        self.session.idle_seconds=.05
        await asyncio.sleep(.11)
        self.assertTrue(self.provider.closed)

    async def test_second_session_cannot_share_lease(self):
        other=Session(('tenant',42,'channel','conversation'),authority=self.auth,ledger=self.ledger,lease=self.lease,
                      context=self.context,provider=Provider(self.ledger),client=Client())
        with self.assertRaises(Refused): await other.start()
        self.assertTrue(self.lease.held)

    async def test_shielded_socket_cleanup_keeps_lease_until_confirmed(self):
        from tenant_operator_realtime_wire import AiohttpRealtimeSocket
        entered=asyncio.Event();release=asyncio.Event()
        class SlowWS:
            closed=False
            close_code=1000
            def exception(inner):return None
            async def close(inner):entered.set();await release.wait();inner.closed=True
        wrapper=AiohttpRealtimeSocket(SlowWS());lease=Lease();provider=Provider(self.ledger)
        provider.close=wrapper.close
        other=Session(('tenant',42,'channel','other'),authority=self.auth,ledger=self.ledger,lease=lease,
                      context=Context(),provider=provider,client=Client())
        other.shutdown_timeout=.03
        await other.start();ended=asyncio.create_task(other.end('test'))
        await entered.wait();self.assertFalse(await ended);self.assertTrue(lease.held)
        self.assertFalse(wrapper._close_task.done())
        release.set();await wrapper.close()
        # Once shutdown was unconfirmed, no late completion may silently
        # release the old lease; reconciliation must be explicit.
        self.assertTrue(lease.held)

    async def test_unconfirmed_release_never_reports_ended(self):
        class RefusingLease(Lease):
            async def release(self,*_):raise Refused('unconfirmed')
        lease=RefusingLease();client=Client();provider=Provider(self.ledger)
        other=Session(('tenant',42,'channel','release'),authority=self.auth,ledger=self.ledger,lease=lease,
                      context=Context(),provider=provider,client=client)
        await other.start()
        self.assertFalse(await other.end('test'));self.assertTrue(lease.held)
        self.assertEqual(client.events[-1],{'type':'end_unconfirmed'})
        self.assertNotIn('ended',[event['type'] for event in client.events])

    async def test_failed_provider_close_keeps_lease_and_reports_uncertain(self):
        async def unconfirmed_close():raise ValueError('wire_close_unconfirmed')
        self.provider.close=unconfirmed_close
        self.assertFalse(await self.session.end('network_failure'))
        self.assertTrue(self.lease.held)
        self.assertEqual(self.client.events[-1],{'type':'end_unconfirmed'})
        self.assertNotIn('ended',[event['type'] for event in self.client.events])

    async def test_pending_provider_receive_keeps_lease_until_drained(self):
        entered=asyncio.Event();release=asyncio.Event();lease=Lease();provider=Provider(self.ledger)
        async def receive():entered.set();await release.wait();return {'type':'late'}
        provider.receive=receive
        other=Session(('tenant',42,'channel','receive'),authority=self.auth,ledger=self.ledger,lease=lease,
                      context=Context(),provider=provider,client=Client())
        other.shutdown_timeout=.03
        await other.start();pending=asyncio.create_task(other.next_provider_event());await entered.wait()
        self.assertFalse(await other.end('test'));self.assertTrue(lease.held)
        release.set();await pending
        self.assertTrue(lease.held)

    async def test_end_during_budget_claim_never_dispatches_late_response(self):
        entered=asyncio.Event();release=asyncio.Event();original=self.ledger.claim
        async def slow_claim(scope):
            entered.set();await release.wait();return await original(scope)
        self.ledger.claim=slow_claim
        pending=asyncio.create_task(commit_input_turn(self.session,'claim-race-input'))
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
            with self.assertRaises(Refused): await commit_input_turn(s,'bad-claim-input')
            self.assertNotIn({'type':'response.create'},s.provider.events)
            self.assertTrue(s.provider.closed)

    async def test_wrong_scope_claim_refused(self):
        async def bad_claim(scope): return {**scope,'channel_id':'different','state':'claimed','reserved_microusd':1}
        self.ledger.claim=bad_claim
        with self.assertRaises(Refused): await commit_input_turn(self.session,'bad-scope-input')
        self.assertNotIn({'type':'response.create'},self.provider.events)

    async def test_bad_reservation_refused(self):
        async def bad_reserve(scope): return {**scope,'state':'claimed','reserved_microusd':1}
        self.ledger.reserve=bad_reserve
        with self.assertRaises(Refused): await commit_input_turn(self.session,'bad-reserve-input')
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
        self.assertNotIn('conversation.item.truncate',[event['type'] for event in self.provider.events])
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
        self.input_id+=1
        await commit_input_turn(self.session,f'user-{self.input_id}')
        self.assertEqual(sum(e['type']=='response.create' for e in self.provider.events),1)
        self.assertEqual(len(self.ledger.events),3)

    async def test_interrupt_before_first_audio_closes_without_late_audio(self):
        await self.turn()
        with self.assertRaisesRegex(Refused,'interrupted_before_audio'):
            await self.session.receive_provider({'type':'input_audio_buffer.speech_started'})
        self.assertEqual(self.provider.events[-1],{'type':'response.cancel','response_id':'response-1'})
        self.assertTrue(self.session.closed);self.assertTrue(self.provider.closed)
        with self.assertRaises(Refused):
            await self.session.receive_provider({'type':'response.output_audio.delta','response_id':'response-1',
                                                 'item_id':'late','content_index':0,'delta':'AAA='})
        self.assertFalse(any(event.get('type')=='audio' for event in self.client.events))

    async def test_unexpected_item_and_provider_event_close(self):
        await self.turn()
        with self.assertRaisesRegex(Refused,'provider_item_forbidden'):
            await self.session.receive_provider({'type':'response.output_item.added','response_id':'response-1',
                                                 'item':{'type':'function_call','name':'external_action'}})
        self.assertTrue(self.provider.closed)

    async def test_function_call_hidden_in_response_done_closes(self):
        await self.turn()
        with self.assertRaisesRegex(Refused,'provider_item_forbidden'):
            await self.session.receive_provider({'type':'response.done','response':{'id':'response-1',
                'output':[{'type':'function_call','name':'external_action'}]}})
        self.assertTrue(self.provider.closed)

    async def test_wrong_truncation_confirmation_closes_before_next_response(self):
        await self.turn()
        await self.session.receive_provider({'type':'response.output_audio.delta','response_id':'response-1','item_id':'a','content_index':0,
                                             'delta':base64.b64encode(bytes(480)).decode()})
        await self.session.receive_provider({'type':'input_audio_buffer.speech_started'})
        await self.session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
        await self.session.receive_provider({'type':'input_audio_buffer.committed','item_id':'user-2'})
        await self.session.receive_provider({'type':'response.done','response':{'id':'response-1'}})
        await self.session.receive_client({'type':'played','item_id':'a','played_ms':0})
        with self.assertRaisesRegex(Refused,'invalid_truncation_confirmation'):
            await self.session.receive_provider({'type':'conversation.item.truncated','item_id':'different','content_index':0,'audio_end_ms':0})
        self.assertEqual(sum(event['type']=='response.create' for event in self.provider.events),1)

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
        await entered.wait();closing=asyncio.create_task(self.session.end('user_end'))
        await asyncio.sleep(.03)
        self.assertTrue(self.lease.held)
        self.assertFalse(closing.done())
        release.set()
        with self.assertRaises(Refused): await pending
        self.assertTrue(await closing)
        self.assertNotIn('input_audio_buffer.append',[item['type'] for item in self.provider.events])

    async def test_shutdown_drains_inflight_provider_event_before_releasing_lease(self):
        entered=asyncio.Event();release=asyncio.Event();original=self.auth.check
        async def slow(scope,stage):
            if stage=='output':entered.set();await release.wait()
            await original(scope,stage)
        self.auth.check=slow
        self.session.response_inflight=True
        pending=asyncio.create_task(self.session.receive_provider(
            {'type':'response.created','response':{'id':'late-response'}}))
        await entered.wait();closing=asyncio.create_task(self.session.end('user_end'))
        await asyncio.sleep(.03)
        self.assertTrue(self.lease.held)
        self.assertFalse(closing.done())
        release.set()
        with self.assertRaises(Refused):await pending
        self.assertTrue(await closing)
        self.assertIsNone(self.session.provider_response_id)

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

    async def test_text_history_flushes_ordered_transcripts_and_only_completed_assistant_playback(self):
        class BoundAuthority(Authority):
            scope=SimpleNamespace(agent_binding='synthetic-binding')
        class History:
            def __init__(self):self.clear_revision=None;self.entries=[]
            async def begin(self,fence):self.clear_revision=0;return 0
            async def flush(self,entries,fence):self.entries=entries;self.clear_revision=None;return True
        authority=BoundAuthority();ledger=Ledger();lease=Lease();provider=Provider(ledger);client=Client();history=History()
        session=Session(('tenant',42,'channel','conversation'),authority=authority,ledger=ledger,lease=lease,
            context=Context(),provider=provider,client=client,history=history,max_seconds=1,idle_seconds=.8,poll_seconds=.02)
        await session.start();await commit_input_turn(session,'history-input')
        await session.receive_provider({'type':'conversation.item.input_audio_transcription.completed','item_id':'history-input',
            'content_index':0,'transcript':'synthetic RU/AZ transcript','usage':{'type':'duration','seconds':1}})
        await session.receive_provider({'type':'response.created','response':{'id':'response-history'}})
        await session.receive_provider({'type':'response.output_item.added','response_id':'response-history',
            'item':{'id':'assistant-history','type':'message','role':'assistant'}})
        await session.receive_provider({'type':'response.output_audio_transcript.done','response_id':'response-history',
            'item_id':'assistant-history','content_index':0,'transcript':'synthetic generated reply'})
        await session.receive_provider({'type':'response.output_audio.delta','response_id':'response-history',
            'item_id':'assistant-history','content_index':0,'delta':base64.b64encode(bytes(480)).decode()})
        await session.receive_provider({'type':'response.output_audio.done','response_id':'response-history',
            'item_id':'assistant-history','content_index':0})
        usage={'input_tokens':10,'output_tokens':10,'total_tokens':20,
               'input_token_details':{'text_tokens':5,'audio_tokens':5,'image_tokens':0,'cached_tokens':2,
                                      'cached_tokens_details':{'text_tokens':2,'audio_tokens':0,'image_tokens':0}},
               'output_token_details':{'text_tokens':3,'audio_tokens':7}}
        await session.receive_provider({'type':'response.done','response':{'id':'response-history','output':[], 'usage':usage}})
        await asyncio.sleep(.02)
        await session.receive_client({'type':'played_all','item_id':'assistant-history'})
        self.assertTrue(await session.end('test'))
        self.assertEqual([entry['role'] for entry in history.entries],['employee','copilot'])
        self.assertEqual([entry['turn_sequence'] for entry in history.entries],[0,1])
        self.assertEqual([entry['text'] for entry in history.entries],['synthetic RU/AZ transcript','synthetic generated reply'])
        self.assertTrue(all(entry['transcript_quality']=='automatic_unverified' for entry in history.entries))

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
