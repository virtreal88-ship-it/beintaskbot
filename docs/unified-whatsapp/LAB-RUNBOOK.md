# First laboratory slice — not a production migration

The `unifiedlab` build adds text and small fixture media send, bounded media
download, allowlisted group metadata, incoming message/edit/receipt journaling,
and a minimal authenticated `/unified-lab` page to the **existing call server**.
It attaches to the same `whatsmeow.Client` as the existing call handlers. It does
not create a messaging-only client or copy any Evolution credentials.

Normal builds contain neither the flag nor the experimental endpoints. The
production Dockerfile does not enable this tag. There is no Worker migration or
new Railway configuration in this slice. Do not merge as a completed integration.

## Prerequisites

- The existing isolated Linux lab VM, with the previously consented test number.
- Confirm the actual account JID and session slot from the lab DB, not from a
  display name. Never copy the production session DB/key into the lab.
- Stop the old lab process cleanly before replacing its executable. Keep its
  session DB on the same disk; the existing runtime lock must be acquired by the
  new process. Do not clone a running auth DB into another process.
- Keep loopback binding and the existing private lab password, SSH host-key
  verification and network isolation. No public lab endpoint.
- A private JSON config, mode 0600, containing `scope` (`tenant`, `source`,
  `account`, `generation`), `session_id`, `allowed_chats`, and a random 32-byte
  base64 `key`. Use synthetic lab tenant/source IDs. Keep the encryption key
  outside version control and outside the DB. Do not print it in a report.
- The existing DB may contain only the specified test slot/account. Startup
  rejects other sessions before Restore connects them. Use the existing slot's
  QR/re-pair screen; this slice does not replace the application's pairing flow.
  The legacy call UI's create/delete/logout routes are blocked in this mode;
  no new slot can bypass the exact test-account guard.

## Build and launch boundary

From `services/whatsapp-calls/engine`:

```sh
go test ./...
go test -race -tags unifiedlab ./cmd/server ./internal/unified
go build -tags unifiedlab -o /private/lab/unified-call-server ./cmd/server
```

Use the lab's existing verified arguments for `-addr`, `-db`, `-static`,
`-lab-token-file`, and optional test recording configuration, plus
`-unified-lab-config /private/lab/message-config.json`. **Do not pass
`-app-gateway-config`**: this prototype rejects SaaS/gateway exposure. No automatic
start script is supplied until the current VM/session state is reverified.

After logging into the existing lab page, open `/unified-lab`. Select only a
configured test chat and send one agreed test phrase. The page displays safe
message/receipt fields. Protocol bytes and media keys never leave the encrypted
journal through this endpoint. There is no automatic message generation.

The media form accepts only purpose-built PNG/JPEG, TXT/PDF or small Ogg Opus
fixtures up to 64 KiB, within the existing lab request cap. Media upload and send
share one durable operation reservation; retrying an unknown result cannot upload
or send again. This fixture admission is **not** the production voice/video decoder
and duration policy. Downloads are limited to 10 MiB, checked before and during
transfer, and scoped to the allowed chat even after a peer is revoked. Only HTTPS
WhatsApp/Meta CDN names and public resolved IPs are dialed, without environment
proxies or redirects. Media keys/URLs stay in the encrypted journal; downloads
use attachment disposition, no-store and sandbox headers. Test group metadata
requires the exact allowlisted group JID; names never establish identity.

Keep the existing call page open alongside it. Incoming-call handling still
uses the existing call path and same client. The legacy isolated lab guard's
outgoing-call prohibition remains; the existing scoped gateway outgoing path
needs a separate test adapter before full inbound/outbound acceptance. Do not
disable that guard as a shortcut.

## Durable behavior covered by isolated tests

- Scope includes tenant, source, account and generation; swapping ciphertext
  across scopes fails authentication. A changed key fails startup.
- SQLite FULL/EXTRA synchronous mode is required. Events commit before the
  message callback returns success; insertion failure returns false.
- For this pinned whatsmeow version, ordinary decrypted-message handling checks
  the callback success before message receipt and event-buffer clearing. The
  decryption/ratchet transaction is retained; incoming and outgoing retry buffers
  are encrypted through the existing EventBuffer interface.
- Each send reserves its immutable operation and provider-generated ID before
  one transport call. A timeout, process crash or local result-commit failure
  leaves `unknown`; repeating the same operation cannot invoke transport again.
  Send identity and protocol buffers survive an owner-generation change; changing
  generation cannot reset an unknown operation. Event pages remain generation
  scoped, so a future Worker dispatcher must drain retained generations too.
- `accepted` is provider server acceptance, **not delivered/read**. Receipts are
  retained separately. This slice does not yet reconcile unknown operations.
- Same-source event replay is idempotent; edits/reactions have separate event
  identities; cursor reads stay scoped even when other sources interleave.
- View-once/ephemeral content is deliberately not retained by this lab journal.
  Production policy for these message classes remains a migration gate.
- The lab caps its journal at 10,000 events and payloads at 1 MiB; backpressure
  returns failure. It never evicts unprocessed data to claim success.

## Live results and remaining gates

The isolated existing test session has connected with the unified build without
another QR and survived graceful process replacement. One agreed text and one
small TXT document were accepted by WhatsApp; the participant confirmed receiving
both. Repeating the text operation retained the original provider ID and did not
create another send row. Provider delivery/read receipt events are not confirmed.

Reported participant replies did not appear in the old linked device's encrypted event journal.
Content-free diagnostics on that device showed a live provider connection and service IQ
responses, but no message or receipt frames. This does not establish whether the
linked device received the reply; the owner confirmed delivery to the primary
test phone. Do not label it a decryption, peer-filter or
journal bug without evidence from that stage. The diagnostic logger is installed
before client construction (whatsmeow snapshots child loggers), classifies only
fixed frame tags and never serializes nodes, IDs, contents or errors.
Call-frame subtypes distinguish an actionable offer from an offer notice and
termination; the authenticated lab status also reuses the existing safe call
counters. A call frame or notice alone is not evidence that the browser could
accept a call.

An authenticated, same-origin `POST /api/unified-lab/receive-probe` accepts only
`{"action":"devices"}`, `{"action":"announce-offline"}`, or
`{"action":"announce-online-bounded"}`, once each per process.
The former queries only the pinned test account's device roster and exposes
counts/booleans, never device IDs. The latter writes `PresenceUnavailable` using
the same client. A successful write is not receipt/delivery confirmation. Neither
probe accepts a target account, sends a message, alters privacy settings, pairs,
logs out or removes a device. The online probe writes available presence for at
most two minutes, then writes unavailable even when the request is canceled or
the initial write fails. It captures the original client, never its replacement;
cleanup uses an independent bounded context. Counters distinguish attempted and
successful socket writes. Process death still relies on transport disconnection.
The entire route is absent from production builds.

Fresh-link diagnosis uses a separate private **lab-only** database after the old
process exits and its calls/unknown sends are confirmed empty. The original DB,
binary and launch arguments are retained for inspection/rollback. Auth keys are
not copied into the new running database. No working source is switched. This is
not the production source migration strategy, which must preserve/deliver all
durable state and keep the existing source identity.

The lab's explicit pairing dialog opens without generating a QR. Its Generate
action and the existing call screen's Pair action share one serialized attempt
for the configured slot, reusing SessionManager and the existing QR/SSE display.
Repeated clicks reuse pending pairing; a paired slot cannot be reset this way.
The pre-pair callback rejects every account except the configured test account,
before persistence. Startup/read-only page loads do not create a slot or QR.

The fresh link was scanned by the owner on 18 November. The same unified binary
now receives/decrypts message frames and filters unapproved chats. One new
outgoing control message was accepted; delivery/read receipt events were retained.
The participant's matching reply appeared once in the encrypted journal and in
the browser at 09:24:08 UTC. This proves a bidirectional text exchange on the fresh
link, not the root cause of the old device's missing incoming path. The old device
was not logged out or deleted, and no production source was reset.

The roster query returned the current local device and primary phone. The owner
confirmed the earlier reply exists on the primary test phone and the latest
outgoing control text was delivered. This narrows the missing evidence to the
linked client's incoming path but does not establish the cause. A fresh reply
after the unavailable-presence probe was reported sent but was not observed by
the engine. A later bounded online probe restored unavailable as intended; fresh
message/call stimulus during its window has not been confirmed. No automatic
initialization fix is claimed. Read-only production process inventory found no
running engine containing this lab account/device.

On 18 November the fresh test link completed two incoming browser calls using
the same whatsmeow client as messaging (runtime source `fa6d3cce`, binary SHA-256
`34cebc99300a5deb8d97611aefebea59fe247f2ef904ef0a5be98b15ec5382d0`).
The first connected conversation was 17.928 seconds and ended from the phone.
The second was 42.452 seconds. During its connected interval, one test text and
one small TXT document were accepted, each with one durable send row and a read
receipt; a new participant text was journaled during that same call. The
participant confirmed both-way audio and receipt of the text and file.

The second call ended through the user's browser cross, as explicitly confirmed
by the owner. The legacy HTTP close path reported `user_ended` with origin
`unknown`; this is not evidence of an automatic drop. The owner listened to the
42-second saved recording to its end and confirmed both sides with no missing
words. Its metadata remains `incomplete/audio_gaps`: 84 packet arrival gaps over
200 ms were recorded on the remote track. Listening does not disprove those
intervals or establish their cause (silence/DTX versus transport loss), so the
warning was not removed. No three-minute or outgoing unified call is claimed;
the current lab limit is still 120 seconds.

The initial fresh-link call attempts produced call frames without a usable
browser offer. After a graceful restart with subtype-only diagnostics, actual
offers were accepted. No causal fix to WhatsApp signaling is established by
that diagnostic-only change. The original linked device's missing inbound path
also remains unexplained; re-pairing is a lab diagnostic, not a silent production
reset strategy.

Next, one participant voice message and one small document were received once
each (journal sequences 14 and 15) and downloaded through the authenticated
media route. Both returned HTTP 200, exact declared lengths (7,541 and 90 bytes),
`no-store` and `nosniff`. The voice payload has Ogg/Opus headers. This verifies
receipt/decryption/download, not a listening or ASR quality test. Payloads, media
keys, filenames, phone numbers and live event IDs are not included in this report.

Media messages are retained encrypted as protocol payloads; this slice does not
yet implement production media admission, group-change/history import, live Worker
event dispatch/projection, permission-checked SaaS APIs or source cutover.
Receipt/history/undecryptable event acknowledgement behavior requires
separate failure tests; a message callback test does not prove no-loss behavior
for every event type. Distributed ownership across copied volumes is not solved
by the existing local file lock.

### Durable Worker handoff foundation (not enabled)

`internal/unified/delivery.go` adds one generation-scoped consumer cursor. Reading
the next event does not acknowledge it. A matching post-commit `stored` receipt
advances with compare-and-set; an opaque HMAC proof binds source, generation,
cursor, event and payload. Timeout, restart, a delayed old receipt, interleaved
source sequences and a failed cursor write cannot skip or delete retained events.
The proof does not replace authenticated transport.

`tenant_whatsapp_native_inbox.py` reuses managed gateway authentication and
rechecks the existing source/token inside the storage transaction. Only an
explicitly registered capture generation is admitted; duplicates are compared
against sequence and content, encrypted before storage, and acknowledged after
commit. Its private HTTP route now exists behind the default-off inbox flag,
with two independent delivery slots, bounded authenticated JSON and safe errors.
There is no startup migration, source activation, CRM mutation, AI dispatch or
send path. Capture registration is
not a protocol ownership lease and must never authorize a second WhatsApp owner.

Synthetic tests cover encryption, tenant/source/generation boundaries, revoked
credentials, replay conflicts, lost commit responses, rollback and independent
PostgreSQL connections. The HTTP handoff has not been enabled on a live source
or production. The Go delivery client refuses redirects and malformed receipts;
failed HTTP responses cannot advance the durable event cursor.

Do not switch working sources or remove Connect/Evolution until every gate in
AUDIT-AND-ROLLOUT.md and LICENSES.md is closed.

### Explicit outbound lab profile

The private unifiedlab configuration can opt into `call_tests: true` only for
one pre-approved PN peer and its retained LID alias. Account and recording scope
must match, and a managed source is refused. The lab endpoint uses the existing
appGateway command journal and WebRTC/recording path with a server-selected lab
owner. Concurrent starts serialize; an unresolved journal entry prevents a new
operation from routing around an unknown start. The legacy arbitrary dial route
remains denied; the lab route and opt-in flag are absent from production builds.

The explicit profile allows a four-minute capture with the same 24-hour test
retention. Legacy imports remain limited to 120 seconds and managed calls retain
their existing 900-second/30-day policy. Long lab recordings are local acceptance
artifacts, not production import bundles.

`lab-client-outbound.patch` preserves the exact isolated client change (local
WaCalls commit `1c5adb30bc874819419f95191d437bc0c12eb064`). It reuses capture/playback
and asks for the microphone before submitting one start operation. Only the
configured peer appears, and an unknown result leaves the control blocked for
inspection. It is not the `/app` SaaS integration. The client builds with
`npm run build`; Go server/recording/journal tests pass with and without the lab
tag, including scope, replay, unknown concurrent starts and production absence.

On 18 November the explicit profile completed one outbound call with 224,112 ms
of capture. The user confirmed two-way audio. The same client sent the previously
received synthetic test voice during that call (provider accepted in 508 ms);
the user confirmed receipt and playback. The operator ended the call through
the browser; registry returned to zero, and the user confirmed both sides with
no audible missing words while reviewing the recording. The automated
`audio_gaps` warning remains: 493 caller-side arrival gaps do not by themselves
distinguish silence suppression from missing speech.

The deployed lab binary for that call has SHA-256
`db94a065f9699b5eb9aa763331003657599b881e99503b85b3c8c4e38a39b238`.
The later group-discovery binary is
`e422512edfabce36fd78e8247a456366c91680165d93c5a561d667c1ec8b7c0d`.
Each replacement followed a fresh zero-active/zero-unknown check, graceful old
process exit and a retained SQLite/config/binary backup. No new QR or production
source changes occurred. These are lab results, not `/app` acceptance.

### Group and restart evidence, 18 November

The owner created one isolated two-person group. Read-only provider discovery
required the exact approved PN members as well as the test name; only then was
that exact group JID added to the lab allowlist. The outgoing control arrived.
The later explicit incoming personal/group controls were retained as sequences
26/27, each once, with the correct chat and the approved sender. A group name
alone was never used to combine or authorize chats.

With active calls zero and no unknown send/start operations, the test process
was gracefully stopped for 315 seconds. The user sent a new direct control
while it was down. The same binary/config/database resumed without QR and
captured it once at sequence 28. All first 27 encrypted row hashes were
unchanged; five accepted send operations and one completed call command were
unchanged. Private `offline-recovery-evidence.json` retains before/after hashes
and times. No message or session data was deleted. This does not cover forced
crash during send, a network partition, or SaaS `/app` projection.

The Worker HTTP delivery permit now survives caller cancellation until its
synchronous DB transaction actually finishes. A cancellation regression test
checks repeated cancellation and a late DB error without releasing capacity or
returning a false stored receipt.

### Reject investigation (not yet accepted)

The first reject removed the local card but the caller reported continued
ringing. The old call manager launched a query with the HTTP request context
and ignored its result; the HTTP route also forced local success. The first
candidate completed a detached, bounded write through the pinned client's
RejectCall. Its `transport_sent` diagnostic still did **not** stop the caller
(owner explicitly confirmed continued ringing). Do not treat that as success.

The next candidate preserved the offered peer and creator separately and
required a correlated `ack class=call type=reject` without a server error.
The actual test returned `ack_timeout` after eight seconds. Both offered JIDs
were equal, non-device LIDs; stripping a device address therefore did not explain
this test. The later remote terminate cleared the registry with no media or
recording. Private `reject-ack-evidence.json` retains safe counters/times only.
An earlier diagnostic incorrectly attributed that later terminate to the
attempted browser reject; the code now marks local rejection only on ACK.

Deferring preaccept until explicit Accept did not fix rejection: the user again
confirmed continued ringing, `ack_timeout` preceded a remote terminate after
46.599 seconds. Adding a separately correlated offer receipt only in the lab
also returned `ack_timeout` and remote terminate after 45.763 seconds. Both
experiments were removed; the previously working incoming acceptance sequence
is retained. These are falsified explanations for this test, not fixes.

The next candidate uses the original minimal reject builder (no extra count or
privacy-token child), with bounded cancellation-safe I/O and correlated ACK
validation. The [reverse-engineered reject description](https://wacrg.org/spec/signalling/call-reject/)
is research evidence, not an official Meta contract. Acceptance is still open.
Local tests cover routing, minimal wire shape, timeout,
wrong/late ACK, explicit NACK, request cancellation, duplicate decisions and
remote termination races. `lab-client-reject.patch`, applied with `git apply
--unidiff-zero` after the outbound patch, also stops swallowing HTTP failures
and clearing a newer incoming call.
The lab client builds successfully; its failure message does not claim closure.

These changes are not a production rollout. Remote rejection must be verified
before promoting the candidate. The test client now visibly retains the
failure state; it closes after the later actual remote termination.

After installing the minimal-reject candidate, the linked client received no
raw call frames during owner-reported calls that rang on the primary test phone.
Authentication and IQ replies continued; the bound device was returned by the
provider's device query. A bounded two-minute presence probe and one graceful
restart of the same binary/database did not establish inbound-call recovery.
Only one process held the exact test database/runtime lock. The primary platform
is WhatsApp Business Android and its stored LID migration marker is absent;
similar upstream LID-account reports are therefore not a proven explanation.
`missing-inbound-after-reconnect.json` retains safe counters and binary evidence.
The minimal reject itself has not been exercised successfully on a new offer.

Lab diagnostics now distinguish the SDK's existing activation request and its
matching IQ result/error, offline synchronization, replacement/logout and
keepalive events. Correlation IDs stay only in bounded memory and never enter a
snapshot or log. Old/unrelated/duplicate responses cannot establish activation.
These counters send no extra protocol messages and do not equate authentication
or activation with successful delivery. No logout, session deletion, new QR or
working-source change is a recovery step in this experiment.

The diagnostic binary at commit `4c012161` confirmed a matching activation IQ
result on the same linked device. The owner reported sending direct control 29,
but no incoming frame/event arrived. A single outgoing control 30 received a
message ACK; that confirms provider acceptance, not recipient delivery. Primary
phone receipt and recipient delivery are still being checked. This is a receive
path blocker distinct from the reject request format; no additional calls are
requested until it is understood.

Upstream investigation found a concrete fixed defect on our exact pinned
whatsmeow revision `5f04eac6dbbb`:
[issue #1182](https://github.com/tulir/whatsmeow/issues/1182), fixed by
[592721a](https://github.com/tulir/whatsmeow/commit/592721a00b51a92d4a428aedc003387d81160012),
removes the `tctoken` child from Reject while retaining the account sender and
`count=0`. The adapter now uses that shape and the SDK-generated request ID,
retains exact offered route/creator and still requires a matching ACK. No broad
SDK update or invented protocol request is needed. PN/LID routing and absence of
the token are regression-tested. This corrects a known library defect; its
effect on this live account is not yet accepted, and it does not prove a fix for
the missing incoming stream. The candidate is prepared, not deployed to a
working source.

### Fresh binding and post-restart acceptance (18 November)

The owner confirmed both primary-phone controls: control 29 was visible on the
test phone and outgoing control 30 reached the agreed recipient. The former
linked client still received no corresponding incoming frames. Its process was
stopped after checking zero active calls/audio and no uncertain operations; its
database and journal remain preserved. The cause of that silent receive failure
is not established. A new test-only QR binding restored incoming delivery; it
does not prove the old session was repaired. Working source …101 was untouched.

Code `032085351fa4a4f877a5ef27d1c1308a6b588b6e`, lab binary SHA-256
`34c51df4b45eb1fbf673bcc3cc59f7ab518881a6d16fe132d4acf471a1303c88`:

- Incoming control 31 was retained exactly once (sequence 1) on the fresh binding.
- Reject received a correlated provider ACK without an error. The owner confirmed
  the calling phone stopped immediately; active calls and audio attachments were
  zero, and neither acceptance nor recording was started.
- The same saved session was gracefully stopped for 44,931 ms. Control 32, sent
  during the outage, arrived exactly once (sequence 3) after restart without QR.
  The preceding encrypted journal rows remained byte-identical to the backup.
- A second incoming call after that restart was rejected with a provider ACK.
  The owner again confirmed immediate termination on the calling phone; no media
  or recording started and active calls returned to zero.
- All ten PR workflows for this exact code SHA completed successfully, including
  the isolated recording workflow with PostgreSQL and Go race coverage.

Private evidence is retained under `receive-relink-20261118`: `paired-evidence`,
`control31-evidence`, `upstream-reject-evidence`, `first-restart-evidence` and
`post-restart-reject-evidence` JSON files. They contain bounded diagnostic facts;
session databases, keys and conversation content are not published. These tests
confirm graceful restart and rejection on this binding, not arbitrary network
partitions, crash-during-send behavior, or production migration acceptance.

### CRM projection foundation (not wired to production)

The common Go envelope retains stable event identity, participant/direction,
caption, MIME/size/name and quote metadata while keeping provider protobuf
encrypted/private. A Python converter validates the encrypted inbox scope and
content hash and builds an allowlisted existing CRM event shape. It deliberately
rejects control/reaction/edit events as fresh customer messages and never exposes
raw protobuf, keys or download URLs. Exact provider-ID lookup, the transaction
consumer, current source-generation ownership and media/outgoing adapters still
need integration. There is no automatic CRM mutation or AI dispatch from this
converter. Unit tests are not `/app` acceptance evidence.
