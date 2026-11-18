# First laboratory slice — not a production migration

The `unifiedlab` build adds text send, incoming message/edit/receipt journaling,
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
- `accepted` is provider server acceptance, **not delivered/read**. Receipts are
  retained separately. This slice does not yet reconcile unknown operations.
- Same-source event replay is idempotent; edits/reactions have separate event
  identities; cursor reads stay scoped even when other sources interleave.
- View-once/ephemeral content is deliberately not retained by this lab journal.
  Production policy for these message classes remains a migration gate.
- The lab caps its journal at 10,000 events and payloads at 1 MiB; backpressure
  returns failure. It never evicts unprocessed data to claim success.

## Not yet demonstrated or implemented

No live unified test has run at this stage. Text send acceptance, actual
delivery/read, QR plus calls on the same client, audio quality and concurrency
must be proven with the test number. Existing successful call tests concern the
old call-only path.

Media messages are retained encrypted as protocol payloads; this slice does not
yet implement safe upload/download, group metadata/history import, Worker event
delivery/ack, durable consumer cursors, permission-checked SaaS APIs or source
cutover. Receipt/history/undecryptable event acknowledgement behavior requires
separate failure tests; a message callback test does not prove no-loss behavior
for every event type. Distributed ownership across copied volumes is not solved
by the existing local file lock.

Do not switch working sources or remove Connect/Evolution until every gate in
AUDIT-AND-ROLLOUT.md and LICENSES.md is closed.
