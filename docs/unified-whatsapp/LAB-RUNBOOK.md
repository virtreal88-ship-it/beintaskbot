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

## Not yet demonstrated or implemented

The isolated existing test session has connected with the unified build without
another QR and survived graceful process replacement. One agreed text and one
small TXT document were accepted by WhatsApp; the participant confirmed receiving
both. Repeating the text operation retained the original provider ID and did not
create another send row. Provider delivery/read receipt events are not confirmed.

Two reported participant replies have not appeared in the encrypted event journal.
Content-free diagnostics currently show a live provider connection and service IQ
responses, but no message or receipt frames. This does not establish whether the
reply reached the primary test phone. Do not label it a decryption, peer-filter or
journal bug without evidence from that stage. The diagnostic logger is installed
before client construction (whatsmeow snapshots child loggers), classifies only
fixed frame tags and never serializes nodes, IDs, contents or errors.

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

The roster query returned the current local device and primary phone. The owner
confirmed the earlier reply exists on the primary test phone and the latest
outgoing control text was delivered. This narrows the missing evidence to the
linked client's incoming path but does not establish the cause. A fresh reply
after the unavailable-presence probe was reported sent but was not observed by
the engine. A later bounded online probe restored unavailable as intended; fresh
message/call stimulus during its window has not been confirmed. No automatic
initialization fix is claimed. Read-only production process inventory found no
running engine containing this lab account/device.

No live unified call/message conversation test has completed. Calls on the same
client, audio quality and concurrency must still be proven. Existing successful
call tests concern the old call-only path. The legacy lab duration limit is still
120 seconds; the planned three-minute acceptance cannot be claimed under it.

Media messages are retained encrypted as protocol payloads; this slice does not
yet implement production media admission, group-change/history import, Worker event
delivery/ack, durable consumer cursors, permission-checked SaaS APIs or source
cutover. Receipt/history/undecryptable event acknowledgement behavior requires
separate failure tests; a message callback test does not prove no-loss behavior
for every event type. Distributed ownership across copied volumes is not solved
by the existing local file lock.

Do not switch working sources or remove Connect/Evolution until every gate in
AUDIT-AND-ROLLOUT.md and LICENSES.md is closed.
