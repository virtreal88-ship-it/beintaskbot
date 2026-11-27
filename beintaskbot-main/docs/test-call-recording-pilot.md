# Isolated call recording pilot

This is an owner-operated experimental import, disabled unless
`BEINTASKBOT_CALL_RECORDING_PILOT` is configured. It does not accept calls in the
production app, pair WhatsApp, change existing Connect/Evolution sessions, or
automatically process customer calls. The separate lab accepts calls manually.

The lab produces stereo PCM WAV (16 kHz, employee left, caller right), capped at
120 seconds, inside a three-entry ZIP with a signed manifest and SHA-256 audio
digest. Recording requires explicit participant-consent confirmation and a
preconfigured test account/peer allowlist. Its private signing and encryption
keys stay in the lab. Production receives only the pinned public verification
key and the explicit route binding; production credentials never enter the lab.

The deployment configuration contains `tenant_id`, `actor_id`, `channel_id`,
`account_jid`, `public_key` (32-byte Ed25519 hex), `expires_at`, and `routes`, a map
from exact provider peer JID to `{conversation_id, lead_id}`. Resolve LID/PN only
using established provider evidence. Display names are never identity evidence.

For an archived source, the owner must explicitly choose the exact destination.
Set `source_mode: archived_owner_binding` and an `archive_call` containing the
one approved `call_id` and audio `sha256`. This imports only that recording and
does not assert that the archived source is currently connected. No future call
is authorized by that binding. Keep the binding expiry at or before the audio
expiry. Without a configured binding, requests fail closed.

The owner uploads the lab `.beincall.zip` through the dialog's actions menu.
POST `/api/platform/test-call-recordings` requires current owner identity,
same-origin, expected tenant/user, an exact existing route, valid signature,
bounded uncompressed ZIP and canonical WAV, current test binding, and consent.
The file is never sent to a WhatsApp participant. It creates a `system` event,
not an incoming/outgoing message, and does not trigger AI, inbox or send jobs.

Storage is encrypted with the existing tenant encryption key. A transaction
and tenant advisory lock enforce the 20-record/24-hour pilot cap and idempotency.
The unique source/call digest prevents two rows for one call. A changed replay
is rejected. Each audio read checks fresh membership, source/dialog grants and
expiry, including Range requests; there are no public media URLs or media cache.

Audio expires no later than 24 hours after capture starts. The scheduled
retention job clears encrypted audio; reads reject expired records even if that
job is late. Only a small metadata/idempotency tombstone remains. Backups follow
the existing database backup policy; this is logical deletion, not a guarantee
of immediate physical erasure of backups. Any locally downloaded import file
must be removed after a verified upload.

## Validation

- Go capture/store tests: separate channels, gaps, interruption, limits,
  duplicate finish, concurrent add/finish, encrypted storage, scope, expiry,
  failed final-write recovery, late setup after call termination.
- Python/isolated PostgreSQL-WASM: signed archive validation, altered bytes,
  exact route/account/peer, owner-only import, source/tenant isolation,
  membership and source-grant revocation, retention, cap, idempotency and
  internal history projection.
- HTTP: Origin and identity mismatch, authenticated playback, Range and expiry.
- JS: incomplete/expired labels, identity-bound URL, no send/reply/media actions.

The live lab test on 2026-11-17 recorded approximately 27 seconds. The owner
confirmed both speakers and their full phrases on playback. The receiver
reported gaps in decoded frame delivery, so the artifact remains marked
incomplete; a silence interval alone cannot prove packet loss or complete
capture. Recording acceptance does not establish production call reliability,
video support, phone-side parallel recording, or all failure scenarios.


## Per-call diagnostics

Before releasing the Worker and calls engine, run
`tenant_call_diagnostics.migrate(cursor)` in a short transaction with local
`lock_timeout='2s'` and `statement_timeout='30s'`. This adds the JSONB
`diagnostics` column to `saas_browser_calls`.

Each call keeps gateway, relay, browser and control evidence under its existing
tenant and call ID. The gateway preserves the first end origin and fixed end
code, stream closure and PCM arrival counters. Engine counters cover the full
call duration. The relay measures both incoming directions. Browser reports
arrivals, capture drops, playback underruns and overflows, context suspensions
and its first stop reason every ten seconds and on completion. Gap counters use
a 200 ms threshold and retain the largest gap and last occurrence. All payloads
use bounded numbers and fixed enums.

Match per-leg timestamps and counters to locate the first observed interruption.
A gap reports delayed PCM arrival at that observation point. Provider-side
arrival gaps alone cannot distinguish network loss from upstream audio delivery.
A confirmed engine-process exit records `process_closed` for its open calls.

After deployment, reload the CRM and use a dedicated incoming call lasting
3–5 minutes. Check gateway, relay and browser evidence on the same call, then
place another test call and verify that the previous evidence remains.
