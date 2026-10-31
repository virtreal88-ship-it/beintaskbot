# Independent employee consultations

The employee opens the assigned Connect dialogue's private Copilot, chooses
“Consult a developer”, selects a contiguous interval (maximum 30 messages from
its latest 80), optional attachments and one existing accessible destination,
and confirms sending the summary and public snapshot. This action does not
create a SupportTicket. Ticket submission remains its existing separate action.

## Storage and compatibility

`tenant_dialog_consultations.schema` performs an additive, idempotent migration:

- `saas_internal_consultations.case_id` and delivery `case_id` become nullable.
  Existing ticket foreign keys remain. Legacy ticket-specific joins stay scoped
  to actual tickets and their APIs retain existing authorization.
- Public shares accept exactly one of `ticket_id` or `consultation_id` and retain
  a tenant-scoped consultation FK. Dialogue shares keep immutable sanitized
  message snapshots, selected attachment IDs and source version hashes.
- Replies and per-employee reads have separate durable tables. Handling is shared
  across authorized shifts; reading is personal. Ordinary customer sends never
  clear these records implicitly.

Shares expire after seven days and can be revoked by staff with source-dialogue
access. Their opaque tokens use existing public-case rendering/security headers;
only selected, unchanged attachments can be loaded on click. Provider URLs,
credentials and full customer history are never returned by the share API.
Original CRM evidence is not rewritten.

## Delivery and follow-up

Preparation and dispatch reuse the existing consultation delivery ledger and
Connect sender/receipt format. One UUID is reserved durably before provider I/O.
Concurrent or repeated dispatch cannot resend an inflight/unknown request. A new
UUID cannot bypass an equivalent prepared/sent/unknown consultation. `sent`
requires a usable Connect and upstream provider receipt. Unknown outcomes need
operator review, not an automatic retry.

Signed inbound messages correlate using their actual quoted upstream message ID
or an unambiguous `CONSULT:<uuid>` marker in the exact receipted destination.
The ordinary notification job also reconciles preserved inbound evidence, so a
webhook-before-receipt race does not lose the developer reply. No pilot expiry
applies to reply collection.

The same job prepares a private follow-up through current assigned Copilot
instructions, language, knowledge, employee AI permission and company budgets.
The original author/time/text stays separate from the generated draft. Unknown
AI outcomes are quarantined. Known failures before a Copilot reservation remain
pending with backoff, so authorized next-shift staff can act. No customer send,
auto-close, agent setting change or knowledge publication occurs.

The global bell beside the language selector counts unhandled replies. Its link
opens the exact source dialogue and reply card. Ordinary synchronous Copilot
answers do not produce these notifications. Explicit “Handled” clears pending;
reading, refresh, reassignment and unrelated outgoing messages do not.

## Validation and rollout

Tests use isolated PostgreSQL/WASM and synthetic provider/AI responses. No
production database, logs, grants, messages, live AI calls or credentials are
read or modified during implementation. Publication, exact-head review/CI,
merge and deployment remain coordinated separately.

A generated customer draft can be sent through the ordinary employee-controlled
composer. Staff can explicitly link a relevant outgoing customer reply: only a
message in the original dialogue with an accepted send receipt, recorded after
the developer reply, can be selected. Confirming that link clears pending and
stores the association; unrelated sends never clear it automatically. This does
not change the shared composer or resolve a SupportTicket.
