"""Local-only Realtime gateway prototype; NOT an HTTP endpoint or provider client.

All authority, ledger, session lease, context, transport and persistence must be
provided by trusted server implementations.  None is shipped here. No provider
connection is possible without explicitly injected dependencies.
"""
import asyncio
import base64
import binascii
import time
import uuid


class Refused(Exception):
    pass


def _require_live_session_admission():
    # No feature flag or request can bypass source, history and billing gates.
    raise Refused('real_provider_not_supported')


class Session:
    SAMPLE_RATE = 24000
    MAX_FRAME = 9600  # 200 ms PCM16 mono
    MAX_OUTPUT_BYTES = 4_800_000

    def __init__(self, scope, *, authority, ledger, lease, context, provider, client,
                 max_seconds=120, idle_seconds=30, poll_seconds=1, executor='local-prototype', session_id=None):
        if any(x is None for x in (authority, ledger, lease, context, provider, client)):
            raise Refused('trusted_dependencies_missing')
        if getattr(provider, 'mock_only', None) is not True:
            from tenant_operator_realtime_wire import OpenAIRealtimeProvider
            if type(provider) is not OpenAIRealtimeProvider:raise Refused('provider_not_trusted')
            _require_live_session_admission()
        if not callable(getattr(provider,'bind_claim',None)):
            raise Refused('provider_claim_binding_required')
        if not isinstance(scope, tuple) or len(scope) != 4 or any(not str(x) for x in scope):
            raise Refused('invalid_scope')
        if not 0 < idle_seconds <= max_seconds <= 120 or not 0 < poll_seconds <= 1:
            raise Refused('invalid_limits')
        self.scope = scope  # (tenant, actor, channel, conversation), server-derived
        self.authority, self.ledger, self.lease = authority, ledger, lease
        self.context, self.provider, self.client = context, provider, client
        self.max_seconds, self.idle_seconds, self.poll_seconds = max_seconds, idle_seconds, poll_seconds
        self.started = self.activity = 0
        self.closed = True
        self.open_attempted = False
        self.muted = False
        self.fence = self.watchdog = None
        try:
            self.session_id = str(uuid.UUID(session_id)) if session_id is not None else str(uuid.uuid4())
        except (ValueError,TypeError,AttributeError):
            raise Refused('invalid_session_id') from None
        self.executor = executor
        self.response_number = 0
        self.input_turns = {}
        self.response_inflight = False
        self.cancel_requested = False
        self.response_pending = False
        self.current_reservation = None
        self.provider_response_id = None
        self.item = None
        self.produced = 0
        self.interrupt_pending = False
        self.truncate_pending = None
        self.output_finished = False
        self.output_started_at = None
        self.lock = asyncio.Lock()
        self.io_lock = asyncio.Lock()
        self.receive_lock = asyncio.Lock()
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
        self.open_attempted = True
        self._opening = True
        # These calls are deliberately abstract and must fail closed.  In
        # particular, reserve must be atomic across all voice-test executors.
        try:
            await self.authority.check(self.scope, 'start')
            if self._stop_requested: raise Refused('session_stopped_during_start')
            snapshot = await self.context.read_authorized(self.scope)
            if self._stop_requested: raise Refused('session_stopped_during_start')
            if not isinstance(snapshot, dict) or not snapshot.get('existing_copilot_instructions'):
                raise Refused('existing_copilot_context_unavailable')
            # Initial provider.open sends the context, so validate before open
            # as well as before each response. This remains non-atomic with
            # network I/O; immutable production gate remains closed.
            await self.context.revalidate(self.scope)
            if self._stop_requested: raise Refused('session_stopped_during_start')
            self.fence = await self.lease.claim(self.scope)
            if not self.fence:
                raise Refused('session_already_active')
            if self._stop_requested: raise Refused('session_stopped_during_start')
            await self.authority.check(self.scope, 'dispatch')
            await self.lease.assert_current(self.scope,self.fence)
            if self._stop_requested: raise Refused('session_stopped_during_start')
            # The injected provider must use the existing Copilot prompt and
            # context, no function tools, and never expose a permanent key.
            self._open_task = asyncio.create_task(self.provider.open(snapshot, tools=[], create_response=False, interrupt_response=False,
                                                                    transcription=False, reconnect=False))
            done, pending = await asyncio.wait({self._open_task}, timeout=3)
            if pending:
                self._open_task.cancel()
                raise Refused('provider_open_timeout')
            await self._open_task
            if self._stop_requested: raise Refused('session_stopped_during_start')
            await self.authority.check(self.scope, 'post_open')
            await self.lease.assert_current(self.scope, self.fence)
            if self._stop_requested: raise Refused('session_stopped_during_start')
            self.closed = False
            self.started = self.activity = time.monotonic()
            self.watchdog = asyncio.create_task(self._watch())
            await self._send_client({'type': 'ready'})
            if self._stop_requested: raise Refused('session_stopped_during_start')
        except BaseException:
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
                    await self._bounded(self.lease.release(self.scope, self.fence))
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
        # unbudgeted response. The reservation authority is shared externally.
        if (not self.response_pending or self.interrupt_pending or self.truncate_pending or self.response_inflight or
            self.item is not None or self.closed or any(not (turn['stopped'] and turn['committed'] and turn['added'])
                                                       for turn in self.input_turns.values())):
            return
        self.response_number += 1
        if self.response_number > 2:
            raise Refused('session_response_limit')
        # Validate a server-owned context before reserving and again as close
        # as possible to dispatch. This does not prove an atomic DB→network
        # fence; production admission remains hard-disabled.
        await self.context.revalidate(self.scope)
        tenant, actor, channel, conversation = self.scope
        reservation = {'request_id': str(uuid.uuid4()), 'tenant_id': str(tenant), 'session_id': self.session_id,
                       'actor_id': actor, 'channel_id':str(channel), 'conversation_id':str(conversation),
                       'response_number': self.response_number, 'executor': self.executor}
        reserved=await self.ledger.reserve(reservation)
        self._check_receipt(reserved,reservation,'reserved')
        claimed=await self.ledger.claim(reservation)  # MUST commit before return
        self._check_receipt(claimed,reservation,'claimed')
        self.current_reservation = reservation
        self.response_pending = False
        self.response_inflight = True
        self.cancel_requested = False
        try:
            await self.authority.check(self.scope, 'response_dispatch')
            await self.lease.assert_current(self.scope, self.fence)
            await self.context.revalidate(self.scope)
            if self.closed:
                raise Refused('session_closed_before_dispatch')
            # A production transport must independently consume a committed
            # one-use dispatch capability. Only the local mock protocol below
            # implements this contract; the public provider is hard-disabled.
            await self.provider.bind_claim(reservation,claimed)
            if self.closed:
                raise Refused('session_closed_before_dispatch')
            await self._send_provider({'type': 'response.create'})
        except BaseException:
            marked=await self.ledger.mark_unknown(reservation)
            if isinstance(marked,dict) and marked.get('state')=='unknown':
                self.current_reservation=None
            raise

    async def _note_input_item(self, item_id, marker):
        """Called under self.lock for server-owned VAD/item events."""
        if not isinstance(item_id,str) or not item_id or len(item_id)>128:
            raise Refused('input_item_invalid')
        turn=self.input_turns.get(item_id)
        if marker=='started':
            if turn is not None or len(self.input_turns)>=20:raise Refused('input_item_replayed')
            self.input_turns[item_id]={'started':True,'stopped':False,'committed':False,'added':False,'scheduled':False}
            return
        if turn is None or marker not in ('stopped','committed','added') or turn[marker]:
            raise Refused('input_item_unmatched')
        turn[marker]=True
        if turn['stopped'] and turn['committed'] and turn['added'] and not turn['scheduled']:
            # More than one unanswered item in the mutable default provider
            # conversation cannot safely be assigned to separate responses.
            # Fail closed until an ordered, scoped input protocol is proven.
            if self.response_pending:raise Refused('multiple_unanswered_input_turns')
            turn['scheduled']=True
            self.response_pending=True
            await self._dispatch_pending()

    async def _watch(self):
        try:
            while not self.closed:
                await asyncio.sleep(self.poll_seconds)
                if time.monotonic() - self.started >= self.max_seconds:
                    return await self.end('duration_limit')
                if time.monotonic() - self.activity >= self.idle_seconds:
                    return await self.end('idle_limit')
                try:
                    await asyncio.wait_for(self.authority.check(self.scope, 'ongoing'), timeout=self.poll_seconds)
                    await asyncio.wait_for(self.lease.assert_current(self.scope, self.fence), timeout=self.poll_seconds)
                except Exception:
                    return await self.end('access_or_lease_lost')
        except asyncio.CancelledError:
            pass

    async def receive_client(self, event):
        try:
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
        await self.authority.check(self.scope, 'input')
        await self.lease.assert_current(self.scope, self.fence)
        if kind == 'mute' and set(event) == {'type', 'muted'} and type(event['muted']) is bool:
            self.muted = event['muted']
            if self.muted:
                await self._send_provider({'type': 'input_audio_buffer.clear'})
                # A clear can race VAD/commit. Until we can prove that a
                # partially recorded item is absent from upstream context,
                # terminate rather than resume with uncertain speech.
                async with self.lock:
                    uncertain=any(not (turn['stopped'] and turn['committed'] and turn['added'])
                                  for turn in self.input_turns.values())
                if uncertain:
                    await self.end('mute_during_unconfirmed_input')
            return
        if kind == 'audio' and set(event) == {'type', 'pcm'} and isinstance(event['pcm'], bytes):
            data = event['pcm']
            if not data or len(data) > self.MAX_FRAME or len(data) % 2:
                raise Refused('invalid_pcm_frame')
            if not self.muted:
                await self._send_provider({'type': 'input_audio_buffer.append', 'audio': base64.b64encode(data).decode('ascii')})
                if any(not turn['stopped'] for turn in self.input_turns.values()):
                    self.activity=time.monotonic()
            return
        if kind == 'played' and set(event) == {'type', 'item_id', 'played_ms'}:
            async with self.lock:
                ms = event['played_ms']
                if not self.interrupt_pending or event['item_id'] != self.item or type(ms) is not int or ms != 0:
                    raise Refused('invalid_playback_ack')
                self.truncate_pending=self.item
                await self._send_provider({'type': 'conversation.item.truncate', 'item_id': self.item,
                                          'content_index': 0, 'audio_end_ms': ms})
                self.interrupt_pending = False
                self.item, self.produced = None, 0
                self.output_finished, self.output_started_at = False, None
                await self._dispatch_pending()
            return
        if kind == 'played_all' and set(event) == {'type','item_id'}:
            async with self.lock:
                if (self.interrupt_pending or not self.item or event['item_id'] != self.item or not self.output_finished or
                    self.output_started_at is None or time.monotonic()-self.output_started_at < self.produced/(self.SAMPLE_RATE*2)):
                    raise Refused('invalid_playback_completion')
                # Client playback acknowledgments cannot prove what a human
                # heard. Until a trustworthy model exists, remove all audio
                # from upstream history rather than retain an unverified reply.
                self.truncate_pending=self.item
                await self._send_provider({'type':'conversation.item.truncate','item_id':self.item,
                                          'content_index':0,'audio_end_ms':0})
                self.item,self.produced=None,0
                self.output_finished,self.output_started_at=False,None
                await self._dispatch_pending()
            return
        raise Refused('client_event_not_allowed')

    async def receive_provider(self, event):
        try:
            await self._receive_provider(event)
        except Exception:
            await self.end('provider_or_budget_error')
            raise

    async def _receive_provider(self, event):
        if self.closed or not isinstance(event, dict):
            raise Refused('session_closed')
        await self.authority.check(self.scope, 'output')
        await self.lease.assert_current(self.scope, self.fence)
        kind = event.get('type')
        if kind == 'input_audio_buffer.speech_started':
            if self.muted:return
            self.activity = time.monotonic()
            async with self.lock:
                await self._note_input_item(event.get('item_id'),'started')
                if self.response_inflight and not self.cancel_requested:
                    event={'type':'response.cancel'}
                    if self.provider_response_id:event['response_id']=self.provider_response_id
                    await self._send_provider(event)
                    self.cancel_requested=True
                    if self.item is None:
                        # No stable item exists to truncate. A generated but
                        # unheard transcript could otherwise leak into the
                        # next turn; terminate rather than invent an alignment.
                        raise Refused('interrupted_before_audio')
                if self.item and not self.interrupt_pending:
                    self.interrupt_pending = True
                    await self._send_client({'type': 'interrupt', 'item_id': self.item})
            return
        if kind == 'input_audio_buffer.speech_stopped':
            if self.muted:
                return
            self.activity = time.monotonic()
            async with self.lock:
                await self._note_input_item(event.get('item_id'),'stopped')
            return
        if kind == 'response.output_audio.delta':
            if not self.response_inflight or not self.provider_response_id or event.get('response_id') != self.provider_response_id:
                raise Refused('unreserved_response')
            encoded=event.get('delta')
            if not isinstance(encoded,str) or len(encoded)>4*((self.MAX_FRAME+2)//3):
                raise Refused('invalid_provider_audio')
            try:
                data = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError, TypeError):
                raise Refused('invalid_provider_audio')
            item = event.get('item_id')
            if not isinstance(item, str) or not item or len(item) > 128 or event.get('content_index') != 0 or not data or len(data) > self.MAX_FRAME or len(data) % 2:
                raise Refused('invalid_provider_audio')
            async with self.lock:
                if self.interrupt_pending:
                    return  # queued unplayed audio must not reach playback
                if self.output_finished:
                    raise Refused('audio_after_completion')
                if self.item not in (None, item):
                    raise Refused('overlapping_output_items')
                if self.item is None:
                    self.output_started_at = time.monotonic()
                self.item = item
                self.produced += len(data)
                if self.produced > self.MAX_OUTPUT_BYTES:
                    raise Refused('output_limit')
                await self._send_client({'type': 'audio', 'item_id': item, 'pcm': data})
            return
        if kind == 'conversation.item.truncated':
            async with self.lock:
                if (not self.truncate_pending or event.get('item_id')!=self.truncate_pending or
                    event.get('content_index')!=0 or event.get('audio_end_ms')!=0):
                    raise Refused('invalid_truncation_confirmation')
                self.truncate_pending=None
                await self._dispatch_pending()
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
                # Until a verified tariff adapter reconciles usage, the
                # conservative hold remains spent/unknown.
                await self.ledger.mark_unknown(self.current_reservation)
                self.current_reservation = None
                self.response_inflight = False
                self.provider_response_id = None
                await self._dispatch_pending()
            return
        if kind == 'response.created':
            rid=(event.get('response') or {}).get('id')
            if not self.response_inflight or self.provider_response_id or not isinstance(rid,str) or not rid or len(rid)>128:
                raise Refused('unreserved_or_duplicate_response')
            self.provider_response_id=rid
            return
        if kind == 'response.output_audio.done':
            async with self.lock:
                if (not self.response_inflight or event.get('response_id')!=self.provider_response_id or
                    event.get('item_id')!=self.item or not self.item or event.get('content_index')!=0):
                    raise Refused('invalid_audio_completion')
                if not self.interrupt_pending:
                    self.output_finished=True
                    await self._send_client({'type':'audio_done','item_id':self.item})
            return
        if kind=='input_audio_buffer.committed':
            async with self.lock:
                await self._note_input_item(event.get('item_id'),'committed')
            return
        if kind in ('input_audio_buffer.cleared','rate_limits.updated','conversation.created'):
            return
        if kind in ('conversation.item.added','conversation.item.done'):
            item=event.get('item')
            if isinstance(item,dict) and item.get('type')=='message' and item.get('role') in ('user','assistant'):
                if kind=='conversation.item.added' and item.get('role')=='user':
                    content=item.get('content')
                    if (not isinstance(content,list) or len(content)!=1 or not isinstance(content[0],dict)
                        or content[0].get('type')!='input_audio'):
                        raise Refused('input_item_content_invalid')
                    async with self.lock:
                        await self._note_input_item(item.get('id'),'added')
                return
            raise Refused('provider_item_forbidden')
        if kind in ('response.output_item.added','response.output_item.done'):
            item=event.get('item')
            if (self.response_inflight and event.get('response_id')==self.provider_response_id and
                isinstance(item,dict) and item.get('type')=='message' and item.get('role')=='assistant'):
                return
            raise Refused('provider_item_forbidden')
        if kind in ('response.content_part.added','response.content_part.done',
                    'response.output_audio_transcript.delta','response.output_audio_transcript.done'):
            if self.response_inflight and event.get('response_id')==self.provider_response_id:return
            raise Refused('unreserved_response')
        # Unknown provider events and tool calls never reach the browser.
        raise Refused('provider_event_forbidden')

    async def end(self, reason):
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
            if reservation:
                await self.ledger.mark_unknown(reservation)
        # close and both drains are independent: close can unblock a running
        # send, but the lease remains held until *all* I/O is really drained.
        drained=await self._bounded(self.provider.close(),self._drain(self.io_lock),self._drain(self.receive_lock),
                                    self._drain(self.client_lock),hold_unknown())
        async def terminal(event):
            async with self.client_lock:
                await self.client.send(event)
        if not drained:
            await self._bounded(terminal({'type':'end_unconfirmed'}))
            return False
        # Release only after provider close and all I/O drains. A failed or
        # uncertain release must never be reported as a confirmed end.
        if not await self._bounded(self.lease.release(self.scope, self.fence)):
            await self._bounded(terminal({'type':'end_unconfirmed'}))
            return False
        return await self._bounded(terminal({'type':'ended','reason':reason}))
