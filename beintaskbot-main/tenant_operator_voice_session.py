"""Server-owned Realtime session lifecycle and admission enforcement."""
import asyncio
import base64
import binascii
import logging
import re
import time
import uuid


class Refused(Exception):
    pass


class LeaseAlreadyActive(Refused):
    """A scoped lease claim was declined before any provider open."""


_SAFE_RESPONSE_REJECTION_CODES=frozenset({
    'session_closed','output_authorization_rejected','voice_lease_rejected',
    'response_not_inflight','response_id_mismatch',
})
_SAFE_PROVIDER_EVENT_GUARD_CODES=frozenset({'provider_authority_rejected','provider_lease_rejected'})
_TERMINAL_RESPONSE_EVENT_TYPES=frozenset({
    'response.done','response.output_item.added','response.output_item.done',
    'response.content_part.added','response.content_part.done',
    'response.output_audio.delta','response.output_audio.done',
    'response.output_audio_transcript.delta','response.output_audio_transcript.done',
})


class ProviderResponseRejected(Refused):
    """Response-event rejection with metadata safe for diagnostic logs."""
    def __init__(self,code,response_inflight,response_id_matches):
        if not isinstance(code,str) or code not in _SAFE_RESPONSE_REJECTION_CODES:code='provider_error'
        self.diagnostic_code=code
        self.diagnostic_response_inflight=response_inflight is True
        self.diagnostic_response_id_matches=response_id_matches is True
        super().__init__(code)


class ProviderEventGuardRejected(Refused):
    """Allowlisted authorization/lease failure while handling a provider event."""
    def __init__(self,code):
        if not isinstance(code,str) or code not in _SAFE_PROVIDER_EVENT_GUARD_CODES:code='provider_error'
        self.diagnostic_code=code
        super().__init__(code)


_LOG=logging.getLogger(__name__)

# Provider startup includes credential retrieval, the WebSocket upgrade and the
# Realtime session handshake.  Keep it bounded, but longer than the provider's
# own network deadline so a healthy but slow upgrade is not cancelled early.
PROVIDER_OPEN_DEADLINE=12
# The periodic watchdog may wait for a database-backed authority/lease check.
# A transient slow query must not kill an otherwise active conversation after
# the default one-second poll interval; per-event authorization remains in
# place and the watchdog still fails closed if the check does not finish.
AUTHORITY_RECHECK_TIMEOUT=5


def _require_live_session_admission(scope,session_id,authority,lease,provider,pilot_policy):
    """Bind construction to the policy enforced by start() and the provider."""
    from tenant_operator_voice_pilot import PilotAdmission
    if type(pilot_policy) is not PilotAdmission or getattr(provider,'guard',None) is not pilot_policy:
        raise Refused('live_admission_dependencies_invalid')
    try:pilot_policy.validate_binding(scope,session_id,authority,lease)
    except Exception:raise Refused('live_admission_dependencies_invalid') from None


class Session:
    SAMPLE_RATE = 24000
    MAX_FRAME = 9600  # 200 ms PCM16 mono
    MAX_OUTPUT_BYTES = 4_800_000
    # VAD may split a natural utterance into several pauses while the model
    # is speaking. Keep a bounded backlog large enough for a short interruption
    # burst without ending the whole conversation.
    MAX_QUEUED_INPUT_TURNS = 8

    def __init__(self, scope, *, authority, ledger, lease, context, provider, client, history=None,
                 max_seconds=120, idle_seconds=30, poll_seconds=1, executor='local-prototype', session_id=None,
                 pilot_policy=None):
        if any(x is None for x in (authority, lease, context, provider, client)) or (ledger is None and pilot_policy is None):
            raise Refused('trusted_dependencies_missing')
        if getattr(provider, 'mock_only', None) is not True:
            from tenant_operator_realtime_wire import OpenAIRealtimeProvider
            from tenant_operator_voice_pilot import PilotAdmission
            if (type(provider) is not OpenAIRealtimeProvider or type(pilot_policy) is not PilotAdmission or
                provider.guard is not pilot_policy or history is not None):
                raise Refused('provider_not_trusted')
        if pilot_policy is not None:
            from tenant_operator_voice_pilot import PilotAdmission
            if type(pilot_policy) is not PilotAdmission or history is not None:
                raise Refused('pilot_policy_invalid')
        if not callable(getattr(provider,'bind_pilot_claim' if pilot_policy is not None else 'bind_claim',None)):
            raise Refused('provider_claim_binding_required')
        if not isinstance(scope, tuple) or len(scope) != 4 or any(not str(x) for x in scope):
            raise Refused('invalid_scope')
        # Only the exact, scope-bound owner pilot admits a longer session.
        # The general ledger path retains its original reservation/time bound.
        maximum_seconds = 600 if pilot_policy is not None else 120
        if not 0 < idle_seconds <= max_seconds <= maximum_seconds or not 0 < poll_seconds <= 1:
            raise Refused('invalid_limits')
        self.scope = scope  # (tenant, actor, channel, conversation), server-derived
        self.authority, self.ledger, self.lease = authority, ledger, lease
        self.pilot_policy=pilot_policy
        self.history = history
        self.history_clear_revision = None
        self.context, self.provider, self.client = context, provider, client
        self.max_seconds, self.poll_seconds = max_seconds, poll_seconds
        # Keep accepting idle_seconds for compatibility with older callers;
        # user silence no longer closes an otherwise healthy conversation.
        self.idle_seconds = idle_seconds
        # Wall-clock expiry alone does not bound audio uploaded faster than
        # real time. Count all attempted PCM input before provider I/O, since
        # a failed write can still have reached the provider.
        self.max_input_bytes = int(max_seconds * self.SAMPLE_RATE * 2)
        self.input_bytes = 0
        self.started = self.activity = 0
        self.closed = True
        self.open_attempted = False
        self.muted = False
        self.fence = self.watchdog = None
        try:
            self.session_id = str(uuid.UUID(session_id)) if session_id is not None else str(uuid.uuid4())
        except (ValueError,TypeError,AttributeError):
            raise Refused('invalid_session_id') from None
        if pilot_policy is not None and (pilot_policy.scope!=scope or pilot_policy.session_id!=self.session_id or
                                         pilot_policy.authority is not authority or pilot_policy.lease is not lease):
            raise Refused('pilot_policy_scope_mismatch')
        if getattr(provider,'mock_only',None) is not True:
            _require_live_session_admission(scope,self.session_id,authority,lease,provider,pilot_policy)
        self.transcript = None
        if history is not None:
            try:
                from tenant_operator_voice_transcript import TranscriptDraft
                self.transcript = TranscriptDraft(self.session_id, authority.scope.agent_binding)
            except (AttributeError,TypeError,ValueError):
                raise Refused('voice_transcript_scope_invalid') from None
        self.executor = executor
        self.response_number = 0
        self.response_inflight = False
        self.cancel_requested = False
        self.response_pending = False
        self.input_sequence = 0
        self.input_turn = None
        self.manual_turns = True
        self.stopped_input_turns = []
        self.pending_input_item_id = None
        self.queued_input_item_ids = []
        self.seen_input_item_ids = set()
        self.current_reservation = None
        self.current_input_item_id = None
        self.current_response_usage_microusd = None
        self.current_response_kind = None
        self.input_transcription_costs = {}
        self.input_transcription_events = {}
        self.seen_assistant_item_ids = set()
        self.assistant_response_ids = {}
        self.provider_response_id = None
        # Realtime may flush a queued delta after the terminal response.done
        # event. Keep the exact response IDs scoped to this short-lived
        # session so late output is discarded without closing the next turn.
        self.finished_response_ids = set()
        self.item = None
        self.produced = 0
        self.interrupt_pending = False
        self.truncate_pending = None
        self.truncate_pending_audio_end_ms = None
        self.output_finished = False
        self.greeting_pending = False
        self.greeting_item_id = None
        self.lock = asyncio.Lock()
        self.io_lock = asyncio.Lock()
        self.receive_lock = asyncio.Lock()
        # Drain complete event handlers before releasing the distributed lease.
        # receive_lock only protects the websocket read itself; the handler is
        # intentionally run after that lock is released by the gateway.
        self.provider_event_lock = asyncio.Lock()
        self.client_event_lock = asyncio.Lock()
        self.client_lock = asyncio.Lock()
        self.shutdown_timeout = .5
        self._end_task = None
        self._opening = False
        self._stop_requested = False
        self._open_task = None

    async def _send_provider(self, event):
        async with self.io_lock:
            if self.closed:
                raise Refused('session_closed')
            # A busy event loop or a slow authorization/lease check can delay
            # the watchdog. Do not initiate billable input or a new response
            # after the session deadline while waiting for that watchdog.
            if (event.get('type') in ('input_audio_buffer.append','response.create') and
                self.started and time.monotonic()-self.started >= self.max_seconds):
                raise Refused('session_duration_limit')
            await self.provider.send(event)

    async def _send_client(self, event):
        async with self.client_lock:
            if self.closed:
                raise Refused('session_closed')
            await self.client.send(event)

    async def next_provider_event(self):
        async with self.receive_lock:
            if self.closed:raise Refused('session_closed')
            return await self.provider.receive()

    async def start(self):
        if self.open_attempted or self._stop_requested:
            raise Refused('session_already_started')
        if self.pilot_policy is not None:
            try:self.pilot_policy.validate_binding(self.scope,self.session_id,self.authority,self.lease)
            except Exception:raise Refused('live_admission_dependencies_invalid') from None
        self.open_attempted = True
        self._opening = True
        stage='owner_auth'
        # All pilot admission dependencies are server-owned and fail closed.
        try:
            await self.authority.check(self.scope, 'start')
            if self._stop_requested: raise Refused('session_stopped_during_start')
            stage='context_read'
            snapshot = await self.context.read_authorized(self.scope)
            if self._stop_requested: raise Refused('session_stopped_during_start')
            if not isinstance(snapshot, dict) or not snapshot.get('existing_copilot_instructions'):
                raise Refused('existing_copilot_context_unavailable')
            # Initial provider.open sends the context, so validate before open
            # as well as before each response.
            stage='context_revalidation'
            await self.context.revalidate(self.scope)
            if self._stop_requested: raise Refused('session_stopped_during_start')
            stage='lease_claim'
            self.fence = await self.lease.claim(self.scope)
            if not self.fence:
                raise LeaseAlreadyActive('session_already_active')
            if self._stop_requested: raise Refused('session_stopped_during_start')
            # The owner pilot intentionally has no monetary reserve. It does
            # require owner authorization and the durable user lease.
            if self.pilot_policy is not None:
                stage='pilot_admission'
                await self.pilot_policy.admit(self.scope,self.session_id,self.fence)
            else:
                stage='budget_admission'
                reserve_session=getattr(self.ledger,'reserve_session',None)
                if not callable(reserve_session):raise Refused('session_budget_unavailable')
                admission=await reserve_session(self.scope,self.session_id)
                if (not isinstance(admission,dict) or admission.get('state')!='reserved' or
                    admission.get('scope')!=self.scope or admission.get('session_id')!=self.session_id or
                    type(admission.get('reserved_microusd')) is not int or admission['reserved_microusd']!=5_000_000):
                    raise Refused('session_budget_unconfirmed')
            if self._stop_requested: raise Refused('session_stopped_during_start')
            if self.history is not None:
                # Fence legacy whole-snapshot writers before any provider I/O.
                # A subsequent context read catches a text turn that won the
                # row lock just before this marker was committed.
                stage='history_begin'
                await self.lease.assert_current(self.scope,self.fence)
                self.history_clear_revision=await self.history.begin(self.fence)
                stage='history_revalidation'
                await self.context.revalidate(self.scope)
            stage='dispatch_authorization'
            await self.authority.check(self.scope, 'dispatch')
            stage='dispatch_lease'
            await self.lease.assert_current(self.scope,self.fence)
            if self._stop_requested: raise Refused('session_stopped_during_start')
            # The injected provider must use the existing Copilot prompt and
            # context, no function tools, and never expose a permanent key.
            stage='provider_open'
            self._open_task = asyncio.create_task(self.provider.open(snapshot, tools=[], create_response=False, interrupt_response=False,
                                                                    transcription=None, reconnect=False))
            done, pending = await asyncio.wait({self._open_task}, timeout=PROVIDER_OPEN_DEADLINE)
            if pending:
                self._open_task.cancel()
                raise Refused('provider_open_timeout')
            await self._open_task
            if self._stop_requested: raise Refused('session_stopped_during_start')
            stage='post_open_authorization'
            await self.authority.check(self.scope, 'post_open')
            stage='post_open_lease'
            await self.lease.assert_current(self.scope, self.fence)
            stage='provider_open_confirmation'
            confirm_provider_open=getattr(self.lease,'confirm_provider_open',None)
            if not callable(confirm_provider_open):raise Refused('provider_open_confirmation_unavailable')
            if await confirm_provider_open(self.scope,self.fence) is not True:
                raise Refused('provider_open_confirmation_failed')
            if self._stop_requested: raise Refused('session_stopped_during_start')
            self.closed = False
            self.started = self.activity = time.monotonic()
            self.watchdog = asyncio.create_task(self._watch())
            stage='client_ready'
            await self._send_client({'type': 'ready','greeting_pending':self.greeting_pending})
            if self._stop_requested: raise Refused('session_stopped_during_start')
        except BaseException as error:
            if not isinstance(error,asyncio.CancelledError):
                reason=type(error).__name__
                if not re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,63}',reason):reason='UnknownError'
                _LOG.warning('realtime_voice_start_failed stage=%s reason=%s',stage,reason)
            # Failed or uncertain startup must never leave a dispatch permit.
            if self.pilot_policy is not None:
                try:await asyncio.shield(self.pilot_policy.close())
                except BaseException:pass
            if self._open_task and not self._open_task.done():
                self._open_task.cancel()
                self._open_task.add_done_callback(self._consume_task)
                # A cancellation-suppressing open may resurrect a connection.
                # Keep the lease; an external reconciler must fence it.
            elif self._end_task is not None:
                # An end may already be draining a delayed ready/send. Never
                # race it by separately closing or releasing the same lease.
                await asyncio.shield(self._end_task)
            elif not self.closed:
                self._opening = False
                await self.end('startup_failed')
            elif self.fence:
                # No open in flight. A failed or completed open must be
                # closed and drained before releasing the acquired lease.
                closed = not self._open_task or await self._bounded(self.provider.close(), self._drain(self.io_lock), self._drain(self.receive_lock), self._drain(self.client_lock))
                if closed:
                    if self.history is not None and self.history.clear_revision is not None:
                        async def finish_history():
                            if not await self.history.close(self.fence):
                                raise Refused('history_close_unconfirmed')
                        closed=await self._bounded(finish_history())
                    if closed:
                        async def release_start_lease():
                            if not await self.lease.release(self.scope, self.fence):
                                raise Refused('lease_release_unconfirmed')
                        await self._bounded(release_start_lease())
            raise
        finally:
            self._opening = False

    @staticmethod
    def _check_receipt(receipt, scope, state):
        # The injected ledger adapter MUST commit before returning this receipt.
        # Fail closed for stale/replayed/wrong-scope or missing rows.
        if not isinstance(receipt, dict) or receipt.get('state') != state:
            raise Refused('budget_receipt_invalid')
        if any(str(receipt.get(key)) != str(value) for key,value in scope.items()):
            raise Refused('budget_scope_mismatch')
        if type(receipt.get('reserved_microusd')) is not int or receipt['reserved_microusd'] <= 0:
            raise Refused('budget_receipt_invalid')

    async def _dispatch_pending(self):
        # Caller holds lock. VAD detects turns only; it must never launch an
        # response until the exact committed input item has been acknowledged.
        # The reservation authority is shared externally.
        # A speaker may pause mid-sentence long enough for VAD to split their
        # turn while the previous interrupted assistant item is still being
        # truncated. Keep those committed turns in order instead of treating
        # a legitimate second speech_started event as a fatal protocol error.
        if (self.pending_input_item_id is None and self.queued_input_item_ids and
            not self.response_pending and not self.response_inflight and
            self.current_reservation is None and self.item is None and
            not self.interrupt_pending and not self.truncate_pending and not self.closed):
            self.pending_input_item_id=self.queued_input_item_ids.pop(0)
            self.response_pending=True
        if (not self.response_pending or not self.pending_input_item_id or self.interrupt_pending or
            self.truncate_pending or self.response_inflight or self.current_reservation is not None or
            self.item is not None or self.closed):
            return
        self.response_number += 1
        if self.pilot_policy is None and self.response_number > 2:
            raise Refused('session_response_limit')
        # Validate the server-owned context before issuing a one-use pilot
        # permit and again as close as possible to dispatch.
        await self.context.revalidate(self.scope)
        tenant, actor, channel, conversation = self.scope
        input_item_id=self.pending_input_item_id
        reservation = {'request_id': str(uuid.uuid4()), 'tenant_id': str(tenant), 'session_id': self.session_id,
                       'actor_id': actor, 'channel_id':str(channel), 'conversation_id':str(conversation),
                       'response_number': self.response_number, 'executor': self.executor}
        if self.pilot_policy is not None:
            claimed=await self.pilot_policy.issue(self.scope,reservation['request_id'])
        else:
            reserved=await self.ledger.reserve(reservation)
            self._check_receipt(reserved,reservation,'reserved')
            claimed=await self.ledger.claim(reservation)  # MUST commit before return
            self._check_receipt(claimed,reservation,'claimed')
        self.current_reservation = reservation
        self.current_input_item_id=input_item_id
        self.current_response_kind='user'
        self.current_response_usage_microusd=None
        self.response_pending = False
        self.pending_input_item_id = None
        self.response_inflight = True
        self.cancel_requested = False
        try:
            await self.authority.check(self.scope, 'response_dispatch')
            await self.lease.assert_current(self.scope, self.fence)
            await self.context.revalidate(self.scope)
            if self.closed:
                raise Refused('session_closed_before_dispatch')
            # The provider consumes the one-use pilot permit before sending a
            # response.create event upstream.
            if self.pilot_policy is not None:
                await self.provider.bind_pilot_claim(reservation,claimed)
            else:
                await self.provider.bind_claim(reservation,claimed)
            if self.closed:
                raise Refused('session_closed_before_dispatch')
            await self._send_provider({'type': 'response.create'})
        except BaseException:
            marked=await self.ledger.mark_unknown(reservation) if self.pilot_policy is None else {'state':'unknown'}
            if isinstance(marked,dict) and marked.get('state')=='unknown':
                self.current_reservation=None
            raise

    async def _dispatch_initial_greeting(self):
        """Use the owner pilot's normal one-use dispatch gate for a brief greeting."""
        if (self.pilot_policy is None or self.closed or self.response_inflight or
            self.current_reservation is not None or self.response_number!=0):
            raise Refused('initial_greeting_not_available')
        await self.context.revalidate(self.scope)
        tenant,actor,channel,conversation=self.scope
        request_id=str(uuid.uuid4())
        reservation={'request_id':request_id,'tenant_id':str(tenant),'session_id':self.session_id,
                     'actor_id':actor,'channel_id':str(channel),'conversation_id':str(conversation),
                     'response_number':1,'executor':self.executor}
        await self.authority.check(self.scope,'greeting_dispatch')
        await self.lease.assert_current(self.scope,self.fence)
        await self.context.revalidate(self.scope)
        claim=await self.pilot_policy.issue(self.scope,request_id)
        self.current_reservation=reservation
        self.current_input_item_id=None
        self.current_response_kind='greeting'
        self.response_number=1
        self.response_inflight=True
        self.response_pending=False
        self.pending_input_item_id=None
        self.greeting_pending=True
        self.greeting_item_id=None
        try:
            await self.authority.check(self.scope,'greeting_dispatch')
            await self.lease.assert_current(self.scope,self.fence)
            await self.context.revalidate(self.scope)
            if self.closed:raise Refused('session_closed_before_greeting')
            await self.provider.bind_pilot_claim(reservation,claim)
            if self.closed:raise Refused('session_closed_before_greeting')
            await self._send_provider({'type':'response.create'})
        except BaseException:
            # A one-use permit may already have been consumed when provider I/O
            # becomes uncertain; the session is closed by start() and never retries it.
            raise

    async def _accept_committed_input(self, turn):
        """Dispatch only after VAD stop and provider commit identify one item."""
        if not turn['stopped'] or not turn['item_id']:
            return
        if self.pending_input_item_id is None and not self.response_pending:
            self.pending_input_item_id=turn['item_id']
            self.response_pending=True
        else:
            pending_count=(len(self.stopped_input_turns)+len(self.queued_input_item_ids)+
                           int(self.pending_input_item_id is not None))
            if pending_count>=self.MAX_QUEUED_INPUT_TURNS:
                raise Refused('input_turn_queue_full')
            self.queued_input_item_ids.append(turn['item_id'])
        await self._dispatch_pending()

    async def _notify_listening_if_idle(self):
        if (not self.closed and not self.response_inflight and not self.response_pending and
            self.current_reservation is None and self.item is None and not self.interrupt_pending and
            self.truncate_pending is None and self.input_turn is None and self.pending_input_item_id is None and
            not self.queued_input_item_ids and not self.greeting_pending):
            await self._send_client({'type':'listening'})

    async def _watch(self):
        try:
            while not self.closed:
                await asyncio.sleep(self.poll_seconds)
                if time.monotonic() - self.started >= self.max_seconds:
                    return await self.end('duration_limit')
                # Silence is not an end-of-conversation signal. Keep the
                # assistant available until the user presses Stop; the hard
                # session duration below remains the bounded-cost failsafe.
                try:
                    await asyncio.wait_for(self.authority.check(self.scope, 'ongoing'), timeout=AUTHORITY_RECHECK_TIMEOUT)
                except asyncio.TimeoutError:
                    return await self.end('authority_check_timeout')
                except Exception:
                    return await self.end('authority_check_failed')
                try:
                    await asyncio.wait_for(self.lease.assert_current(self.scope, self.fence), timeout=AUTHORITY_RECHECK_TIMEOUT)
                except asyncio.TimeoutError:
                    return await self.end('lease_check_timeout')
                except Exception:
                    return await self.end('lease_check_failed')
        except asyncio.CancelledError:
            pass

    async def receive_client(self, event):
        # End is the operation that closes the session and waits for the other
        # in-flight handlers. It must not hold client_event_lock while doing so.
        if isinstance(event,dict) and event.get('type')=='end':
            await self._receive_client(event)
            return
        try:
            async with self.client_event_lock:
                await self._receive_client(event)
        except Exception:
            await self.end('client_or_authority_error')
            raise

    async def _receive_client(self, event):
        if not isinstance(event, dict) or self.closed:
            raise Refused('session_closed')
        kind = event.get('type')
        if kind == 'end':
            await self.end('user_end')
            return
        try:
            await self.authority.check(self.scope, 'input')
        except Refused:
            raise
        except Exception:
            raise Refused('client_authority_rejected') from None
        try:
            await self.lease.assert_current(self.scope, self.fence)
        except Refused:
            raise
        except Exception:
            raise Refused('client_lease_rejected') from None
        if self.closed:
            raise Refused('session_closed')
        if kind == 'mute' and set(event) == {'type', 'muted'} and type(event['muted']) is bool:
            async with self.lock:
                self.muted = event['muted']
                if self.muted:
                    # Do not turn a late VAD stop into a response after mute.
                    self.input_turn=None
                    self.stopped_input_turns.clear()
                    await self._send_provider({'type': 'input_audio_buffer.clear'})
            return
        if kind in ('talk_start','stop_response') and set(event) in ({'type'},{'type','item_id','audio_end_ms'}):
            item_id=event.get('item_id')
            audio_end_ms=event.get('audio_end_ms')
            if 'item_id' in event and (not isinstance(item_id,str) or not item_id or len(item_id)>128):
                raise Refused('talk_interrupt_item_invalid')
            if 'audio_end_ms' in event and (type(audio_end_ms) is not int or audio_end_ms<0):
                raise Refused('talk_interrupt_offset_invalid')
            async with self.lock:
                # Only the explicit Stop button may interrupt an assistant
                # response. An automatic listening request racing provider
                # output must not cancel, truncate, or close the conversation.
                if kind=='talk_start' and (self.response_inflight or self.item is not None or
                    self.current_reservation is not None or self.pending_input_item_id is not None or
                    self.response_pending or self.interrupt_pending or self.truncate_pending or self.greeting_pending):
                    return
                if (self.muted or self.input_turn is not None or self.pending_input_item_id is not None or
                    self.response_pending or self.interrupt_pending or self.truncate_pending or self.greeting_pending):
                    # Duplicate Stop or a Stop while input is still committing
                    # cannot create another turn or repeat a cancellation.
                    return
                if self.response_inflight and self.item is None:
                    # The user can interrupt while the model is still thinking,
                    # before its first audio chunk reaches the browser.
                    if item_id is not None and item_id not in self.seen_assistant_item_ids:
                        raise Refused('talk_interrupt_item_unexpected')
                    if not self.cancel_requested:
                        self.cancel_requested=True
                        await self._send_provider({'type':'response.cancel'})
                elif self.item is not None:
                    # Stop local playback immediately and trim the provider's
                    # conversation item at the browser's conservative playback
                    # offset. If the client has not seen the first audio frame,
                    # offset zero safely discards all unheard output.
                    target_item=self.item
                    if item_id is not None and item_id!=target_item:
                        raise Refused('talk_interrupt_item_mismatch')
                    offset=audio_end_ms if item_id is not None else 0
                    max_audio_ms=self.produced//(self.SAMPLE_RATE*2//1000)
                    if offset>max_audio_ms:
                        raise Refused('talk_interrupt_offset_out_of_range')
                    self.interrupt_pending=True
                    self.truncate_pending=target_item
                    self.truncate_pending_audio_end_ms=offset
                    if self.transcript is not None:self.transcript.interrupted(target_item)
                    if self.response_inflight and not self.cancel_requested:
                        self.cancel_requested=True
                        await self._send_provider({'type':'response.cancel'})
                    await self._send_provider({'type':'conversation.item.truncate','item_id':target_item,
                                               'content_index':0,'audio_end_ms':offset})
                elif self.response_pending or self.response_inflight or self.current_reservation is not None:
                    raise Refused('talk_not_ready')
                elif item_id is not None and item_id not in self.seen_assistant_item_ids:
                    raise Refused('talk_interrupt_item_unknown')
                self.input_sequence+=1
                self.input_turn={'sequence':self.input_sequence,'stopped':False,'item_id':None,
                                 'manual':True,'audio_bytes':0}
                self.activity=time.monotonic()
                await self._send_client({'type':'talk_ready'})
            return
        if kind == 'talk_end' and set(event)=={'type'}:
            async with self.lock:
                turn=self.input_turn
                if not isinstance(turn,dict) or turn.get('manual') is not True or turn.get('stopped'):
                    raise Refused('talk_not_active')
                if not turn.get('audio_bytes'):
                    self.input_turn=None
                    await self._send_client({'type':'talk_empty'})
                    return
                turn['stopped']=True
                self.activity=time.monotonic()
                await self._send_provider({'type':'input_audio_buffer.commit'})
            return
        if kind == 'audio' and set(event) == {'type', 'pcm'} and isinstance(event['pcm'], bytes):
            data = event['pcm']
            if not data or len(data) > self.MAX_FRAME or len(data) % 2:
                raise Refused('invalid_pcm_frame')
            if not self.muted:
                if self.greeting_pending or (self.input_turn is None and (
                    self.response_inflight or self.item is not None or self.response_pending or
                    self.current_reservation is not None or self.pending_input_item_id is not None)):
                    # Ignore residual microphone/echo frames until playback
                    # completes, unless explicit Stop opened a replacement turn.
                    return
                if not isinstance(self.input_turn,dict) or self.input_turn.get('stopped') is True:
                    raise Refused('audio_without_talk_start')
                if self.input_bytes + len(data) > self.max_input_bytes:
                    raise Refused('session_input_limit')
                self.input_bytes += len(data)
                if self.input_turn.get('manual') is True:
                    self.input_turn['audio_bytes']+=len(data)
                await self._send_provider({'type': 'input_audio_buffer.append', 'audio': base64.b64encode(data).decode('ascii')})
                self.activity=time.monotonic()
            return
        if kind == 'played' and set(event) == {'type', 'item_id', 'played_ms'}:
            async with self.lock:
                ms = event['played_ms']
                if type(ms) is not int or ms != 0:
                    raise Refused('invalid_playback_ack')
                # Server VAD now owns the authoritative truncate request. A
                # browser playback ACK can arrive before or after its provider
                # confirmation, so accept it as advisory and never make the
                # next turn depend on it.
                if event['item_id'] == self.truncate_pending:
                    return
                if event['item_id'] != self.item:
                    if event['item_id'] in getattr(self,'seen_assistant_item_ids',set()):
                        return  # late ACK for an already completed item
                    raise Refused('invalid_playback_ack')
                if not self.interrupt_pending:
                    raise Refused('invalid_playback_ack')
            return
        if kind == 'played_all' and set(event) == {'type','item_id'}:
            async with self.lock:
                if (self.interrupt_pending or not self.item or event['item_id'] != self.item or not self.output_finished):
                    # Playback completion is advisory browser telemetry. A
                    # completion callback can race a server-VAD interruption
                    # (or arrive after a newer item has started). Ignore that
                    # stale acknowledgement; tearing down the whole session
                    # here makes a normal barge-in look like a provider error.
                    return
                # This is an app-level playback signal, not proof that a person
                # heard the answer. The text history marks it unverified.
                if self.transcript is not None:self.transcript.device_playback_complete(self.item)
                completed_item=self.item
                self.item,self.produced=None,0
                self.output_finished=False
                if self.greeting_pending and completed_item==self.greeting_item_id:
                    self.greeting_pending=False
                    self.greeting_item_id=None
                await self._dispatch_pending()
                if (not self.greeting_pending and not self.response_inflight and not self.response_pending and
                    self.pending_input_item_id is None):
                    await self._send_client({'type':'listening'})
            return
        if kind == 'playback_scheduled' and set(event)=={'type','item_id','context_state'}:
            if (event['item_id']!=self.item or event['context_state'] not in {'running','suspended'}):
                raise Refused('invalid_playback_start_ack')
            return
        if kind == 'playback_failed' and set(event)=={'type','item_id'}:
            if event['item_id']!=self.item:
                raise Refused('invalid_playback_failure_ack')
            return
        raise Refused('client_event_not_allowed')

    async def receive_provider(self, event):
        try:
            async with self.provider_event_lock:
                await self._receive_provider(event)
        except Exception:
            await self.end('provider_or_budget_error')
            raise

    async def _receive_provider(self, event):
        response_transcript_delta=(isinstance(event,dict) and
                                   event.get('type')=='response.output_audio_transcript.delta')
        response_inflight=self.response_inflight is True
        response_id_matches=(
            isinstance(event,dict) and isinstance(event.get('response_id'),str) and
            isinstance(self.provider_response_id,str) and
            event.get('response_id')==self.provider_response_id
        )

        def response_rejection(code):
            return ProviderResponseRejected(code,response_inflight,response_id_matches)

        if self.closed or not isinstance(event, dict):
            if response_transcript_delta:raise response_rejection('session_closed')
            raise Refused('session_closed')
        try:await self.authority.check(self.scope, 'output')
        except Exception:
            if response_transcript_delta:raise response_rejection('output_authorization_rejected') from None
            raise ProviderEventGuardRejected('provider_authority_rejected') from None
        try:await self.lease.assert_current(self.scope, self.fence)
        except Exception:
            if response_transcript_delta:raise response_rejection('voice_lease_rejected') from None
            raise ProviderEventGuardRejected('provider_lease_rejected') from None
        if self.closed:
            if response_transcript_delta:raise response_rejection('session_closed')
            raise Refused('session_closed')
        kind = event.get('type')
        response_id=event.get('response_id')
        if kind=='response.done':
            response=event.get('response')
            response_id=response.get('id') if isinstance(response,dict) else None
        if (kind in _TERMINAL_RESPONSE_EVENT_TYPES and isinstance(response_id,str) and
                response_id in getattr(self,'finished_response_ids',())):
            # Never expose late audio, transcripts or items from a response
            # whose exact terminal event has already been accepted.
            return
        if kind == 'input_audio_buffer.speech_started':
            # The production pilot disables provider VAD. Ignore unsolicited
            # VAD notifications so they can never open or cancel a click-owned
            # microphone turn if a provider event is delayed or duplicated.
            if getattr(self,'manual_turns',False):
                return
            async with self.lock:
                if self.muted:
                    return
                # Duplicate VAD starts for the same live buffer are harmless.
                if self.input_turn is not None:
                    if not self.input_turn['stopped']:
                        return
                    raise Refused('input_turn_commit_pending')
                self.activity = time.monotonic()
                if self.response_inflight:
                    # server_vad interrupt_response owns cancellation. Sending
                    # response.cancel here races the provider's own turn-detected
                    # cancellation: if response.done is already queued, that
                    # duplicate cancel is rejected as a provider error and used
                    # to tear down the entire conversation. Mark the response
                    # interrupted locally, stop browser playback below, and let
                    # the provider emit the single authoritative response.done.
                    self.cancel_requested=True
                if self.item and not self.interrupt_pending:
                    self.interrupt_pending = True
                    # Stop local playback immediately, but do not wait for a
                    # browser ACK before telling Realtime to trim the unheard
                    # output. With the server-side WebSocket transport, the
                    # browser owns playback timing; at an interruption the
                    # conservative zero offset prevents unplayed speech from
                    # remaining in conversation context.
                    self.truncate_pending=self.item
                    self.truncate_pending_audio_end_ms=0
                    if self.transcript is not None:self.transcript.interrupted(self.item)
                    await self._send_client({'type': 'interrupt', 'item_id': self.item})
                    await self._send_provider({'type': 'conversation.item.truncate', 'item_id': self.item,
                                               'content_index': 0, 'audio_end_ms': 0})
                self.input_sequence += 1
                self.input_turn = {'sequence':self.input_sequence,'stopped':False,'item_id':None}
            return
        if kind == 'input_audio_buffer.speech_stopped':
            if getattr(self,'manual_turns',False):
                return
            async with self.lock:
                if self.muted or self.input_turn is None:
                    # Replayed stop events must not enqueue another response.
                    return
                turn=self.input_turn
                if turn['stopped']:
                    return
                turn['stopped']=True
                self.activity = time.monotonic()
                self.input_turn=None
                if turn['item_id']:
                    await self._accept_committed_input(turn)
                else:
                    pending_count=(len(self.stopped_input_turns)+len(self.queued_input_item_ids)+
                                   int(self.pending_input_item_id is not None))
                    if pending_count>=self.MAX_QUEUED_INPUT_TURNS:
                        raise Refused('input_turn_queue_full')
                    self.stopped_input_turns.append(turn)
            return
        if kind == 'input_audio_buffer.committed':
            item_id=event.get('item_id')
            if not isinstance(item_id,str) or not item_id or len(item_id)>128:
                raise Refused('invalid_committed_input')
            async with self.lock:
                if item_id in self.seen_input_item_ids:
                    return
                if self.muted:
                    # A commit racing with mute is deliberately discarded.
                    self.seen_input_item_ids.add(item_id)
                    return
                if isinstance(self.input_turn,dict) and self.input_turn.get('manual') is True:
                    turn=self.input_turn
                    if not turn.get('stopped') or turn.get('item_id') not in (None,item_id):
                        raise Refused('unmatched_manual_commit')
                    turn['item_id']=item_id
                    self.input_turn=None
                elif self.stopped_input_turns:
                    turn=self.stopped_input_turns.pop(0)
                    turn['item_id']=item_id
                else:
                    turn=self.input_turn
                if turn is None or not turn['sequence']:
                    raise Refused('unmatched_committed_input')
                if turn['item_id'] not in (None,item_id):
                    raise Refused('multiple_commits_for_input_turn')
                turn['item_id']=item_id
                self.seen_input_item_ids.add(item_id)
                if self.transcript is not None:self.transcript.register(item_id,'employee')
                if turn['stopped']:
                    await self._accept_committed_input(turn)
            return
        if kind == 'conversation.item.input_audio_transcription.delta':
            item_id=event.get('item_id');content_index=event.get('content_index');delta=event.get('delta')
            # Realtime streams partial transcription before the final receipt.
            # Keep it private and accept it only for the committed input item;
            # the completed event remains the sole transcript/usage authority.
            if (item_id not in self.seen_input_item_ids or type(content_index) is not int or content_index!=0 or
                not isinstance(delta,str) or len(delta)>1600 or item_id in self.input_transcription_events):
                raise Refused('unmatched_input_transcript_delta')
            return
        if kind == 'conversation.item.input_audio_transcription.completed':
            item_id=event.get('item_id');content_index=event.get('content_index');text=event.get('transcript')
            if (item_id not in self.seen_input_item_ids or type(content_index) is not int or content_index!=0 or
                not isinstance(text,str) or len(text)>1600):
                raise Refused('unmatched_input_transcript')
            from tenant_operator_voice_transcript import transcription_cost_microusd
            amount=transcription_cost_microusd(event.get('usage'))
            exact=(text,amount)
            if item_id in self.input_transcription_events:
                if self.input_transcription_events[item_id]!=exact:
                    raise Refused('conflicting_input_transcript_usage')
                if self.transcript is not None:self.transcript.input_completed(item_id,content_index,text)
                return
            self.input_transcription_events[item_id]=exact
            self.input_transcription_costs[item_id]=amount
            if self.transcript is not None:self.transcript.input_completed(item_id,content_index,text)
            async with self.lock:
                await self._settle_response_if_ready()
                await self._dispatch_pending()
            return
        if kind == 'conversation.item.input_audio_transcription.failed':
            item_id=event.get('item_id');content_index=event.get('content_index')
            if item_id not in self.seen_input_item_ids or type(content_index) is not int or content_index!=0:
                raise Refused('unmatched_input_transcript_failure')
            if item_id in self.input_transcription_events and self.input_transcription_events[item_id]!=(None,None):
                raise Refused('conflicting_input_transcript_usage')
            if item_id in self.input_transcription_events:return
            self.input_transcription_events[item_id]=(None,None)
            self.input_transcription_costs[item_id]=None
            if self.transcript is not None:self.transcript.input_failed(item_id,content_index)
            async with self.lock:
                await self._settle_response_if_ready()
                await self._dispatch_pending()
            return
        if kind == 'response.output_audio.delta':
            if not self.response_inflight or not self.provider_response_id or event.get('response_id') != self.provider_response_id:
                raise Refused('unreserved_response')
            if self.cancel_requested and self.item is None:
                # VAD cancelled the response before any audio reached the
                # browser. Discard late deltas: there is nothing audible to
                # truncate and stale speech must not race the user's turn.
                return
            encoded=event.get('delta')
            max_encoded=4*((self.MAX_OUTPUT_BYTES+2)//3)
            if not isinstance(encoded,str) or not encoded:
                raise Refused('provider_audio_delta_invalid')
            if len(encoded)>max_encoded:
                raise Refused('provider_audio_delta_too_large')
            try:
                data = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError, TypeError):
                raise Refused('provider_audio_base64_invalid')
            item = event.get('item_id')
            if not isinstance(item, str) or not item or len(item) > 128 or event.get('content_index') != 0 or not data or len(data) % 2:
                raise Refused('provider_audio_chunk_invalid')
            async with self.lock:
                if self.interrupt_pending:
                    return  # queued unplayed audio must not reach playback
                if self.output_finished:
                    raise Refused('audio_after_completion')
                if self.item not in (None, item):
                    raise Refused('overlapping_output_items')
                if self.item is None:
                    if self.greeting_pending:self.greeting_item_id=item
                self.item = item
                if self.produced + len(data) > self.MAX_OUTPUT_BYTES:
                    raise Refused('provider_audio_output_limit')
                self.produced += len(data)
                # Realtime output deltas vary in size. Keep the browser's
                # bounded frame contract by splitting a valid provider delta
                # into ordered PCM chunks rather than rejecting anything
                # larger than one client frame.
                for offset in range(0,len(data),self.MAX_FRAME):
                    await self._send_client({'type': 'audio', 'item_id': item,
                                             'pcm': data[offset:offset+self.MAX_FRAME]})
            return
        if kind == 'conversation.item.truncated':
            async with self.lock:
                if (not self.truncate_pending or event.get('item_id')!=self.truncate_pending or
                    event.get('content_index')!=0 or type(event.get('audio_end_ms')) is not int or
                    event.get('audio_end_ms')!=self.truncate_pending_audio_end_ms):
                    raise Refused('invalid_truncation_confirmation')
                self.truncate_pending=None
                self.truncate_pending_audio_end_ms=None
                self.interrupt_pending=False
                self.item,self.produced=None,0
                self.output_finished=False
                await self._dispatch_pending()
                await self._notify_listening_if_idle()
            return
        if kind == 'response.done':
            async with self.lock:
                response=event.get('response')
                if (not self.current_reservation or not self.response_inflight or not self.provider_response_id or
                    not isinstance(response,dict) or response.get('id') != self.provider_response_id):
                    raise Refused('unreserved_response')
                output=response.get('output',[])
                if (not isinstance(output,list) or any(not isinstance(item,dict) or item.get('type')!='message' or
                    item.get('role')!='assistant' for item in output)):
                    raise Refused('provider_item_forbidden')
                # A provider can queue audio deltas around response.done. Once
                # this exact ID is terminal, discard any later event for it;
                # an unrelated ID still fails the active-response guard.
                self.finished_response_ids.add(response['id'])
                # The speech model and committed-turn transcription are billed
                # separately. Reconcile only after both exact receipts arrive.
                from tenant_operator_realtime_usage import usage_cost_microusd
                self.current_response_usage_microusd=usage_cost_microusd(response.get('usage'))
                self.response_inflight = False
                self.provider_response_id = None
                self.cancel_requested = False
                await self._settle_response_if_ready()
                await self._dispatch_pending()
                await self._notify_listening_if_idle()
            return
        if kind == 'response.created':
            rid=(event.get('response') or {}).get('id')
            if not self.response_inflight or self.provider_response_id or not isinstance(rid,str) or not rid or len(rid)>128:
                raise Refused('unreserved_or_duplicate_response')
            self.provider_response_id=rid
            await self._send_client({'type':'thinking'})
            return
        if kind == 'response.output_audio.done':
            async with self.lock:
                if self.cancel_requested and self.item is None:
                    # A cancelled response can still flush a terminal audio
                    # event after its first delta was discarded.
                    return
                if (not self.response_inflight or event.get('response_id')!=self.provider_response_id or
                    event.get('item_id')!=self.item or not self.item or event.get('content_index')!=0):
                    raise Refused('invalid_audio_completion')
                if self.output_finished:
                    return
                if not self.interrupt_pending:
                    self.output_finished=True
                    if self.transcript is not None:self.transcript.assistant_audio_done(self.item)
                    await self._send_client({'type':'audio_done','item_id':self.item})
            return
        if kind in ('input_audio_buffer.cleared','rate_limits.updated','conversation.created'):
            return
        if kind in ('conversation.item.added','conversation.item.created','conversation.item.done'):
            item=event.get('item')
            if isinstance(item,dict) and item.get('type')=='message' and item.get('role') in ('user','assistant'):
                return
            raise Refused('provider_item_forbidden')
        if kind in ('response.output_item.added','response.output_item.done'):
            item=event.get('item')
            if (self.response_inflight and event.get('response_id')==self.provider_response_id and
                isinstance(item,dict) and item.get('type')=='message' and item.get('role')=='assistant' and
                isinstance(item.get('id'),str) and item['id']):
                if item['id'] in self.assistant_response_ids and self.assistant_response_ids[item['id']]!=event['response_id']:
                    raise Refused('assistant_item_response_conflict')
                self.seen_assistant_item_ids.add(item['id'])
                self.assistant_response_ids[item['id']]=event['response_id']
                if self.transcript is not None:self.transcript.register(item['id'],'copilot')
                return
            raise Refused('provider_item_forbidden')
        if kind=='response.output_audio_transcript.done':
            response_id=event.get('response_id')
            item_id=event.get('item_id')
            if self.cancel_requested and self.item is None:
                return
            if (isinstance(item_id,str) and self.assistant_response_ids.get(item_id)==response_id and
                type(event.get('content_index')) is int and event['content_index']==0 and
                isinstance(event.get('transcript'),str) and len(event['transcript'])<=6000):
                if self.transcript is not None:
                    self.transcript.assistant_transcript_completed(item_id,event['transcript'])
                return
            raise Refused('unreserved_response')
        if kind=='response.output_audio_transcript.delta':
            if not response_inflight:raise response_rejection('response_not_inflight')
            if not response_id_matches:raise response_rejection('response_id_mismatch')
            return
        if kind in ('response.content_part.added','response.content_part.done'):
            if self.response_inflight and event.get('response_id')==self.provider_response_id:return
            raise Refused('unreserved_response')
        # Unknown provider events and tool calls never reach the browser.
        raise Refused('provider_event_forbidden')

    async def _settle_response_if_ready(self):
        reservation=self.current_reservation
        item_id=self.current_input_item_id
        if reservation is None or self.response_inflight:
            return False
        if self.pilot_policy is not None:
            if self.current_response_kind=='greeting':
                self.current_reservation=None;self.current_input_item_id=None;self.current_response_usage_microusd=None
                self.current_response_kind=None
                return True
            if item_id is None:return False
            self.current_reservation=None;self.current_input_item_id=None;self.current_response_usage_microusd=None
            self.current_response_kind=None
            return True
        if item_id is None:return False
        if self.current_response_usage_microusd is None:
            receipt=await self.ledger.mark_unknown(reservation)
            if not isinstance(receipt,dict) or receipt.get('state')!='unknown':
                raise Refused('budget_unknown_unconfirmed')
            self.current_reservation=None
            self.current_input_item_id=None
            self.current_response_usage_microusd=None
            return True
        if item_id not in self.input_transcription_costs:
            return False
        transcription_cost=self.input_transcription_costs[item_id]
        model_cost=self.current_response_usage_microusd
        if transcription_cost is None or model_cost is None:
            receipt=await self.ledger.mark_unknown(reservation)
            if not isinstance(receipt,dict) or receipt.get('state')!='unknown':
                raise Refused('budget_unknown_unconfirmed')
        else:
            receipt=await self.ledger.note_actual(reservation,model_cost+transcription_cost)
            if not isinstance(receipt,dict) or receipt.get('state') not in {'settled','quarantined'}:
                raise Refused('usage_reconciliation_unconfirmed')
        self.current_reservation=None
        self.current_input_item_id=None
        self.current_response_usage_microusd=None
        return True

    async def end(self, reason):
        safe_reason=reason if isinstance(reason,str) and re.fullmatch(r'[a-z_]{1,48}',reason) else 'unknown'
        _LOG.info('realtime_voice_session_ended reason=%s',safe_reason)
        self._stop_requested = True
        if self._opening and self.closed:
            if self._open_task and not self._open_task.done():
                self._open_task.cancel()
            # start() owns the eventual cleanup. Never release a lease or
            # claim a confirmed end while its open may still be in flight.
            return False
        if self._end_task is not None:
            return await asyncio.shield(self._end_task)
        if self.closed:
            return False
        self.closed = True
        if self.watchdog and self.watchdog is not asyncio.current_task():
            self.watchdog.cancel()
        self._end_task = asyncio.create_task(self._finish_end(reason))
        return await asyncio.shield(self._end_task)

    @staticmethod
    def _consume_task(task):
        # A cancelled dependency is allowed to ignore cancellation. Keep no
        # assumption that cancellation stopped I/O, and consume its result.
        try:
            task.exception()
        except BaseException:
            pass

    async def _bounded(self, *coros):
        tasks=[asyncio.create_task(coro) for coro in coros]
        try:
            done, pending = await asyncio.wait(tasks, timeout=self.shutdown_timeout)
            for task in pending:
                task.cancel()
                task.add_done_callback(self._consume_task)
            if pending:
                return False
            return all(not task.cancelled() and task.exception() is None for task in done)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()

    @staticmethod
    async def _drain(lock):
        async with lock:
            pass

    async def _finish_end(self, reason):
        reservation=self.current_reservation
        self.current_reservation=None
        async def hold_unknown():
            if self.pilot_policy is not None:
                await self.pilot_policy.close()
            elif reservation:
                await self.ledger.mark_unknown(reservation)
        # close and both drains are independent: close can unblock a running
        # send, but the lease remains held until *all* I/O is really drained.
        drained=await self._bounded(self.provider.close(),self._drain(self.io_lock),self._drain(self.receive_lock),
                                    self._drain(self.provider_event_lock),self._drain(self.client_event_lock),
                                    self._drain(self.client_lock),hold_unknown())
        async def terminal(event):
            async with self.client_lock:
                await self.client.send(event)
        if not drained:
            await self._bounded(terminal({'type':'end_unconfirmed'}))
            return False
        if self.history is not None and self.fence is not None:
            async def finish_history():
                entries=self.transcript.finalize() if self.transcript is not None else []
                if not await self.history.flush(entries,self.fence):
                    raise Refused('history_close_unconfirmed')
            if not await self._bounded(finish_history()):
                await self._bounded(terminal({'type':'end_unconfirmed'}))
                return False
        # Release only after provider close and all I/O drains. A failed or
        # uncertain release must never be reported as a confirmed end.
        async def release_lease():
            if not await self.lease.release(self.scope, self.fence):
                raise Refused('lease_release_unconfirmed')
        if not await self._bounded(release_lease()):
            await self._bounded(terminal({'type':'end_unconfirmed'}))
            return False
        return await self._bounded(terminal({'type':'ended','reason':reason}))
