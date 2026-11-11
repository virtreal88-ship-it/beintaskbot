# Operator Copilot Realtime pilot

The production route is enabled only for the exact tenant and employee named by
`COPILOT_VOICE_PILOT_TENANT_ID` and `COPILOT_VOICE_PILOT_ACTOR_ID`, and the
employee must be the active company owner. Both values are required; all other
users remain unavailable. The current company Chat AI credential is decrypted
server-side and is never sent to `/app`.

The pilot uses `gpt-realtime-2.1-mini` and has no fixed dollar ceiling. Each
session is limited to 600 seconds and 28,800,000 bytes of incoming PCM audio.
Idle silence does not end a session or submit an empty turn. The existing
9,600-byte frame cap, 4,800,000-byte generated-audio cap per response, and
4,096 output-token cap per response remain unchanged. A longer session may
use more paid model input/output; this duration limit is not a dollar ceiling.
The legacy monetary-ledger path retains its 120-second maximum and existing
reservations; its transcript pricing bounds are unchanged.
PostgreSQL fencing permits one session per tenant/user and keeps an uncertain
session blocked until a confirmed provider close. Voice audio and conversation
turns are not persisted. The separate Business Analyst voice mode can call the
existing audited, owner-only, read-only analytics tools for sources, company
risks, bounded task, conversation, support-case, and knowledge lookups. Each
call rechecks live voice authority and owner status before and after reading;
arguments and results are bounded, and each session allows at most eight calls.
The selected-dialog voice context does not bind these tools. No CRM/task
changes, settings changes, or customer-send actions are available.

The legacy shared monetary-ledger route remains closed. Do not change its
verification constants or flags to enable the pilot. The pilot admission policy
is the explicit alternative and does not assert or enforce a dollar cap.

For a live acceptance check, use the authenticated pilot employee in `/app`,
open an eligible direct Connect dialog with AI assigned, start one brief voice
session, exchange a short spoken turn, then end it and verify the server's
confirmed `ended` event. A failed provider handshake is reported without
logging or exposing the company key; verify model access using this same path.
