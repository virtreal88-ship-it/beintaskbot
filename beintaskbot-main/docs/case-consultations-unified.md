# Unified developer consultations

The existing dialogue-consultation API now accepts an optional existing `case_id`.
The caller supplies one marked Developer destination, a contiguous selected
fragment and its selected attachments. Case scope is validated by
`tenant_support_case_context`; neither preparation nor public sharing changes it.
A share remains consultation-owned (`ticket_id IS NULL`), and the consultation
retains the existing case FK. Independent helper consultations keep `case_id=NULL`
and never create a ticket.

Case transport retains owner + support + dialogue visibility. Plain case sending
requires no AI agent. Independent helper preparation retains the assigned Manual
agent gate. Private case drafts use the current assigned agent and existing actor
AI permission. Legacy support-only internal read/manual-answer APIs remain.

Preparation reserves one request UUID; dispatch uses that same UUID. A Connect
queue receipt is `pending`; only a verified provider receipt changes it to `sent`.
An expired in-flight attempt becomes `unknown`, never eligible for another POST.
A signed late echo may reconcile its exact channel/conversation/request or known
Connect ID. Explicit failed echoes never establish `sent`.

CASE and CONSULT provider replies share the durable reply ledger and personal
notification events. Existing pilot CASE processing retains its own admission
path; new explicit employee case replies do not enter destination AI processing.
Manual assignments never enqueue or materialize automatic customer follow-ups.
Already-linked saved case events are rejected at processing and final send using
the persisted case-turn flags; unrelated initial manual-live admission remains.
The bounded reconciler can derive missing replies from retained CRM evidence or
an already-linked legacy group reply. Legacy derivation records an audit event
and keeps source provenance. It sends no message. Case/legacy/manual replies have
`draft_state=not_requested`, so reconciliation never starts paid case AI work.

The existing manual reply API remains compatible. The new explicit
`POST /dialog-consultations/{id}/manual-reply` wraps it, records the actor and
`source_kind=manual`, and never fabricates a provider receipt. Historical manual
text stays on the existing consultation record; reads do not duplicate it.

Migration is additive: reply `source_kind` (provider by default) and nullable
`source_actor`. Old rows, source messages, assignments, grants and receipts remain.
No destructive down migration is needed when rolling application code back;
older code ignores both additional columns.

Quoted developer audio/video/image/document messages also enter the same ledger,
with canonical message type and original source external ID. An empty caption is
represented by an explicit attachment placeholder, never a fabricated transcript.
Neither ingestion nor reconciliation downloads the media or starts ASR/AI.
The projection offers a protected source route anchored to the original developer
message and an attachment URL only after the viewer's existing destination-dialog
rights and the exact stored source message are checked. These files are never
added to the public customer snapshot. Media replies require inspection/manual
text before AI draft; they never start background paid processing. The additional
message_type/source_external_id columns default to text/empty for old records.

## Case card tabs

The existing case modal has Dialogue history, Case details and Developer response
tabs. Panels stay mounted while switching, retaining edits and the case-scoped
consultation form. The selected tab is restored in the case URL; a developer
reply link selects the developer panel before focusing the exact reply.

Dialogue history uses the same `dialogs/history` route and operator message
renderer as the main dialogue, with read-only presentation and the existing
protected attachment loader/download actions. Loading more dialogue history
never adds messages to the case material map. The case's saved materials and
explicit attachment picker remain in Case details, and only the case preview
endpoint supplies the developer's shareable fragment.

The recipient checkbox is replaced by the explicit Send button naming the
selected developer. Only submission emits the existing `confirm: true` request.
New case consultation requests explicitly send `include_customer_identity: false`;
there is no identity-publication control or restored legacy opt-in. Existing
public links, old identity disclosures and historical developer data are unchanged.

`tests/test_case_card_tabs_browser.cjs` exercises the actual app in Chromium at
320, 390 and 1280 pixels, including ARIA navigation, shared media controls,
separate material scope, drafts, notification reply links, reload, late responses
and revoked dialogue access. Its screenshots are synthetic local QA artifacts.
