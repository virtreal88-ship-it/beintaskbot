# Experimental Copilot Realtime adapter (not production)

This directory is a local, disconnected design and test artifact. It does not
register a route, load UI inside `/app`, instantiate a real provider, read any
credentials, send customer messages, or make OpenAI calls. The base is Worker
`fc75c2899b7b86de42bf2cafdbdd116e9717d821`. Shared history and the $5 ledger
are being developed separately; this artifact is not their integration.

## Existing Copilot reuse and scope

The existing employee/dialog Copilot is `tenant_operator_copilot.py` via
`tenant_operator_api.py` and `tenant_web_routes.py`. Its `INSTRUCTIONS` forbid
tools, CRM modifications, customer sends and external actions. It also applies
`tenant_operator_access.access`/`assert_route`, member policy, consent,
company integration configuration, assigned-agent binding, historical message
and published-knowledge/media-evidence checks. A real `context.read_authorized`
must derive `(tenant, actor, channel, conversation)` from the authenticated
server request, invoke all equivalent checks, and construct the same authorized
prompt and context; it must not trust a browser-provided tenant or context.
Existing `_draft_context` also executes a provider path, so it cannot simply be
called to prepare context without carefully separating preparation from execution.

The new voice transport can be developed independently of the WhatsApp outbound
voice transport, but the Copilot may consume WhatsApp history through Evolution.
The independently identified Baileys advisory means transport success or an
event's arrival does not establish that the underlying WhatsApp event is
authentic. Keep all existing source and consent rules, don't claim that history
is verified, and assess source integrity before allowing affected data into AI.

## Dependencies enforced by the prototype

`Session(scope, authority, ledger, lease, context, provider, client)` rejects
missing dependencies. All six dependencies are synthetic in tests; production
must supply trusted implementations. `scope` is server-derived, never read
from client event data. `authority.check(scope, stage)` and
`lease.assert_current(scope, fence)` are called on input/output/dispatch and
periodically. A shared lease must fence concurrent sessions across processes.
The prototype does not prove atomic revocation around dispatch, or distributed
lease fencing, or any production implementation of these methods.

On a started mock session, shutdown marks it closed, concurrently closes the
provider and waits for both provider and client sends to drain. It emits the
terminal event and releases its lease only after those steps complete. A hung
send, close or terminal event returns an unconfirmed result and keeps the lease;
late completion does not automatically release it. The local barrier tests
cover provider and client sends, and a close that ignores cancellation. This
does not prove what a real provider closes or whether a distributed lease can
expire or be stolen while an old process is still running.

The intended shared budget interface is `reserve(scope)` then `claim(scope)`
before each server-originated `response.create`, where scope contains unique
`request_id`, `tenant_id`, `session_id`, `actor_id`, `channel_id`,
`conversation_id`, `response_number`, and `executor`. The async adapter must
return newly committed matching `reserved` and then `claimed` receipts; absent,
replayed or wrong-scope receipts fail closed. A `mark_unknown(scope)` retains a conservative hold after unknown or
unreconciled use. No in-memory or per-executor account is sufficient. No
connection from the browser to a provider is allowed, and VAD auto-response,
transcription, tools and reconnect are disabled in the provider contract. The
prototype only accepts an explicitly `mock_only` provider; it cannot verify real
upstream configuration. Its mock ledger does not enforce $5. Real upstream enforcement,
current tariff and maximum token charges, a shared durable ledger, reconciliation,
crash handling and limits must be proven before a paid call.

Voice/text history is deliberately not written: the existing text writer may
overwrite concurrent voice turns. A trusted shared history adapter is needed,
with auth and agent binding, voice session/turn IDs, atomic idempotent ordering,
clear/revocation fencing, incomplete ASR marking and a documented retention
policy. User transcripts are not yet handled. Never persist raw voice audio.
No external-action tools may be introduced. Interrupted assistant output cannot
be saved as exact speech merely by dividing its transcript by duration.

## Browser and transport

`browser.js` is an unmounted view with start, status, mute and end. It requires
the existing `acquireTenantMicrophone` helper by injection and fails closed if
it is unavailable. The helper currently exists in held PR #474 only, so an
independent AI release must first extract it to a common compatible base; do
not add another microphone mutex. Its `beintaskbot-microphone-v1` Web Lock is
origin-wide when supported. `audio.js` is a provisional PCM16 mono 24 kHz Web
Audio capture/player; it refuses unsupported 24 kHz contexts. It is untested
on real iOS/Android and uses deprecated ScriptProcessor. Do not ship it without
an AudioWorklet, resampling, echo-cancellation and mobile/browser validation.
On interruption it deliberately reports **0 ms** played because a browser
cannot prove what reached a speaker; no unverified spoken prefix is preserved.
For the same reason even normal playback completion discards the response audio
from upstream context. This loses assistant context and does not satisfy the
real conversational acceptance criteria.

`websocket.py` provides an unregistered aiohttp boundary with explicit
origin allowlisting, an injected pre-upgrade authentication callback, a 9,600
byte frame cap and a simple 60-frame/second limit. `channel.js` implements the
matching same-origin browser transport. The loopback aiohttp test exercises
synthetic audio, interruptions and a mock provider. Those injected callbacks
do not establish real production authentication or authorization.

`client`/`provider` are abstract. A real server-side WebSocket gateway needs
authenticated and origin-checked upgrades, short-lived single-use session
binding, strict frame/queue limits, backpressure and timeout handling, reliable
timer cancellation, revoked access handling, and proven teardown after process
failure. aiohttp exists but the current Worker does not yet expose this path;
Railway upgrade support has not been established. Client reconnect, playback
completion, end-to-end barge-in, context handoff, and actual AI interaction
are not implemented. A startup stop now fences readiness, asks the mock open
to cancel, and keeps the lease when open has not stopped; the websocket reads
disconnect independently. If a dependency suppresses cancellation, this
prototype cannot prove closure or automatically recover its retained lease.
Real provider cancellation, process-crash recovery, lease expiry/fencing and
further independent concurrency review remain mandatory. Do not enable
or deploy this experiment as a feature.

Official references: [Realtime speech conversations and interruption](https://developers.openai.com/api/docs/guides/realtime-conversations),
[Voice activity detection](https://developers.openai.com/api/docs/guides/realtime-vad),
[Realtime cost calculation](https://developers.openai.com/api/docs/guides/voice-latency-cost).

Run isolated tests in this directory:

* `/workspace/.venvs/beintaskbot311/bin/python -m unittest -v test_session test_websocket`
* `node --test test_browser.cjs test_chromium.cjs`

The Chromium test uses fake audio hardware and the microphone helper from this
checkout (override `VOICE_MICROPHONE_HELPER` if needed); it confirms two tabs
cannot acquire it together and tracks stop on end.
Tests use mock authority, ledger and provider and do not prove real security,
cost control, microphone permission recovery, voice quality or delivery.

## Synthetic cross-component integration

`test_real_authority_pg.py` uses actual membership, policy, direct-route,
configuration and assignment code against random synthetic tenant records.
`test_integrated_synthetic_pg.py` joins real authorization, text history reader,
`_draft_context(context_only=True)` with an explicit placeholder and allowlist,
the pinned shared history append, the pinned global+tenant budget grant, the
server WebSocket protocol and an injected synthetic Realtime socket. The test
supplies a synthetic partial transcript itself; there is no real ASR, no speech
model, and no raw audio persistence. The in-memory dispatch-consumption guard
is test-only, not a cross-process one-time capability. Production authorization
and provider factory remain hard-disabled. Direct-dialog checks do not prove
group or granular deal permissions; they match the existing Connect policy.

The cohesive candidate imports the history helper and budget grant directly
from the same checkout. The helper is not yet connected to the production
text writer; it does not solve the rolling-version clear/write race. Both
grant gates are force-enabled only inside the isolated test. The URL must
point to a disposable localhost database
named `test_voice_auth_` followed by 32 lowercase hexadecimal digits; the test
creates and drops a random schema. Example with an existing disposable server:

```
cd experiments/copilot_realtime_adapter
VOICE_AUTH_PG_URL=postgresql://postgres:<synthetic-password>@127.0.0.1:<port>/test_voice_auth_11111111111111111111111111111111 \
  python -m unittest -v test_real_authority_pg test_integrated_synthetic_pg
```

The actual UI does not mount this experiment; its separate default-hidden
placeholder cannot acquire the microphone or start a call. OpenAI GA effective
session readback and one-use dispatch binding are tested only with an injected
synthetic socket. The real provider factory always rejects. Even a successful
readback is not proof that a real provider cannot charge before policy attests,
nor proof of a conservative tariff bound or durable dispatch fencing.

## Server-owned composition candidate

`tenant_operator_voice_orchestration.py` now assembles the existing tenant and
route authority, scoped Copilot history reader/context preparation, shared
voice-test grant and the single `tenant_operator_voice_session.py` candidate.
Database and context preparation run in `asyncio.to_thread`; each operation
uses a separate connection, and the history row lock is released before any
potential provider I/O. Scope and executor are server-derived. Reserve/claim
reauthorize within their transaction and commit before returning; reconciliation
of an already claimed hold does not require a still-active employee. If an
awaited DB claim is cancelled, the worker thread may still commit: its hold
remains charged/reserved, and the mock session cannot dispatch after closure.

The production WS boundary calls this module but returns 503 before upgrade.
The voice writer has an immutable `require_history_cutover()` refusal because
old text workers can still overwrite voice turns, and context handoff has an
independent immutable `require_sealed_source_snapshot()` refusal because
freshness cannot yet be fenced atomically with provider I/O. The live
provider factory independently refuses. These gates have no environment or
request override. Integration tests exercise actual authority, context and
ledger components against local PostgreSQL, and explicitly verify refusal;
they do not bypass the writer gate or establish an active real voice session.
The older separate prototype test directly inserts a synthetic partial turn to
exercise the lower-level history helper; it is not the blocked voice writer.

## aiohttp transport candidate

The wire protocol now has a bounded aiohttp WebSocket JSON adapter and a
server-only OpenAI connection implementation behind an unconditional admission
refusal. The production factory is separately unconditionally disabled and
`Session` rejects non-mock providers. No environment variable, browser input,
or feature flag opens either gate. Local loopback tests exercise real aiohttp
frames with a fake peer, safe session readback, one-use mock response claims,
interruption/truncation and cleanup; this is not an OpenAI compatibility test.

The GA protocol shape was checked against the official [WebSocket guide](https://developers.openai.com/api/docs/guides/voice-websockets),
[Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations),
and [model documentation](https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini).
The model and quote in this candidate are provisional; real effective session
readback, immutable model pricing, all billable paths, provider cancellation,
redirect/connection timeout semantics, and shared dispatch enforcement remain
unverified. The no-tools/no-transcription/no-automatic-response settings are
requested and checked against the peer readback before audio is accepted, but
mock readback cannot prove actual provider behavior or cost prevention.
`ScopedDispatchGuard` now reauthorizes and commits the ledger CAS from
`claimed` to `dispatching` before the mock socket send, comparing the returned
scope, fingerprint, quote and tenant hold. Local PostgreSQL tests cover this
ordering, replay, revocation, cancellation and uncertain send; global admission,
old-schema migration and actual upstream cost bounds remain unproven. Unknown
outcomes retain their hold and cannot be retried blindly.

Input ASR remains disabled because no separately metered ASR admission exists.
Output transcripts are not stored or presented as heard. Incomplete/interrupted
audio is conservatively truncated to zero; no aligned text is invented. The
voice history writer still refuses until every legacy text writer has cut over.
Consequently this candidate does not yet provide a working live conversation,
text-to-voice-to-text continuity, or verified Russian/Azerbaijani behavior.
`OpenAIRealtimeProvider` composes the wire, trusted credential loader and owned
aiohttp socket behind separate immutable connector, factory and Session gates.
The production route now has a real server WebSocket gateway and provider event
pump, but its distributed lease factory deliberately refuses until the separate
lease implementation is reviewed and integrated. A local synthetic PG/HTTP
test overrides gates only inside the test and exercises the actual composition,
with fake provider/socket/context/lease and committed durable dispatch guard.
It does not prove real provider, source or billing behavior. Cleanup now uses a
shared shielded task and the Session drains the provider receive lock before
lease release; startup/remote close and cancellation still need review.

`transcript_draft.py` is an isolated mock of ordered item registration and
later input-transcription completion/failure; it only generates candidate
entries accepted by the existing thread validator and stores no audio. Empty,
missing or failed input is unavailable and automatic ASR is unverified. The
mock can retain complete generated text only after a full app-level playback
signal; it separately marks `device_playback_complete`. A client can forge that
signal and it does not prove human hearing. The marker is mock-only, not stored
by the current DB schema; the real Session still truncates to zero. Interruptions
and unknown playback omit generated text without inventing an aligned prefix.
Item ordering in a real
provider stream must additionally be checked against linked previous-item IDs;
the mock's order assumption is not an upstream guarantee. A future writer must
accept server-derived scope, session/item idempotency keys, ordered entries,
agent binding and clear revision, reauthorize transactionally and use the
existing thread adapter after old-writer cutover. ASR also needs a separate,
durable upper-bounded reservation and usage reconciliation before it is enabled.
The mock is not connected to sessions or any database writer.

`VoiceHistory.append` has a separately gated server-only candidate path using
the existing thread writer, transactional authorization, session binding,
clear revision, contiguous order and digest-based idempotency. The matching
`voice_history_candidate.sql` is synthetic test schema, not a migration. No
call can write while the immutable history gate is present. This candidate is
not ready to release: legacy writers can overwrite or delete thread rows,
clear on an absent row may not advance a durable epoch, and migration, mixed
workers, role privileges, retention and idempotency across rollback remain to
be designed and independently reviewed. It is not yet fed by transcript events.

The live connector remains unusable. The candidate now owns a ClientSession
with `trust_env=False`, an aiohttp pre-redirect trace refusal and connection and
close deadlines; localhost tests verify that a redirect target is never called
and a stalled handshake times out. These tests do not prove all redirect,
DNS/TLS/proxy, cancellation-suppression or cleanup paths, nor establish a remote
billing-stop guarantee. Local `ws.closed` does not establish that guarantee.

## Source fingerprints and remaining fence

The candidate captures server-derived scope, the allowlisted context and
history, full selected message rows with their database IDs, all scoped message
external IDs, and selected knowledge, chunks and available approval/provenance
records. It compares two captures and checks again before provider open and
before and after a response claim. Synthetic PostgreSQL tests cover edits,
deletes, recreation with the same external ID, new/backfilled messages, raw
JSON and bytea edits, knowledge changes and access revocation. No raw audio is
persisted. These are observed-change checks, not an atomic fence: an ABA edit
between reads and changes after the final check can escape detection, including
before initial context transmission. A synthetic ABA test demonstrates this.
The immutable gate therefore remains closed even when revalidation succeeds.

The candidate fails closed above 5,000 scoped message IDs, 1,000 knowledge
records/chunks, or 2 MB per canonical serialized component; larger dialogs or
knowledge sources may be unavailable and repeated scans add database load.
These limits and the source/version protocol require production design and
performance review. The current route checks inherit existing access policy;
they do not establish stronger per-deal authorization. No test proves live
provider billing, real-time operation, or safety across legacy history writers.
