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
