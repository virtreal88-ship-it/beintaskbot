# WhatsApp knowledge candidates — stage 1

Extension: see whatsapp-case-batches.md for optional period bounds, the paginated
company review queue, needs_clarification and manual batch launch/poll APIs.

Base production: a656dbe3. Design input: Terra audit 021aaa31.
Branch: codex/whatsapp-knowledge-candidates, separate worktree.
No production access, merge, push or deployment; no UI, /webapp, AI worker,
group coordination, transport/session or existing agent-setting changes.

## API contract for Terra

Base URL: /api/platform/ai/knowledge-candidates.
Responses: {success:true,result:...}; errors {success:false,error:...}; no-store.
Use current authenticated session. ALL requests including GET require
expected_tenant_id / expected_user_id (query for GET, JSON for writes).
POST/PATCH require exact canonical Origin and JSON.
Rights: existing owner/admin ai_agents_manage AND dialogs access. Current
membership, tenant lifecycle and permissions are reread at review/publication.

- POST collection: required request_id UUID, channel_id UUID, conversation_id.
  Optional provider:"connect" (only supported provider),
  history_mode:"channel"|"unified_group" (default channel), before message UUID,
  message_ids UUID[] (1–80), ticket_id UUID, analysis_agent_id UUID.
  Model/credential comes from an existing same-tenant agent and company provider;
  omitted analysis_agent_id selects the first configured agent. No agent is enabled
  or changed. Company AI must be enabled, emergency stop off.
  Result: {request:{id,status,error_code,route,source_message_ids,message_count,limits},candidates:[card,...]}.
- GET collection: requires exact channel_id + conversation_id; returns
  {candidates:[...],limit:50}. No unbounded company-wide feed.
- GET /requests/{request_id}: poll the same request/result shape.
- GET /{candidate_id}: {candidate,evidence:[...],route}.
- PATCH /{candidate_id}: expected_version plus any editable card fields.
  Nested cause/outcome objects are replaced as a whole.
- POST /{candidate_id}/approve: expected_version, agent_ids UUID[] (1–20).
- POST /{candidate_id}/reject: expected_version.
- PATCH/review returns detail. Identical successful approve retry returns
  {candidate} with the existing publications, without writes.

Card:
id, extraction_id, title, symptoms[], cause:{status:confirmed|suspected|unknown,text},
resolution_steps[], outcome:{status:confirmed|unconfirmed|unknown,text,message_ids:[]},
source_message_ids[], product, product_version, status, version, reviewed_by,
reviewed_at, created_at, updated_at, publications:[{agent_id,source_id}],
can_approve, approval_block_reason.
States: pending_review / approved / rejected.
Route: provider, anchor channel_id, exact conversation_id, history_mode,
optional ticket_id and server-derived source_channels.

Evidence: real message UUID id, provider, channel_id, conversation_id, lead_id,
redacted text, role (customer/operator/ai/system). Pass these route fields to the
existing dialog opener. Never derive a route from model output.
The model receives opaque m1/m2 refs, not raw event/media fields or source IDs.

## Source scope and privacy

Manual launch only. Latest window: 80 saved messages, 12000 text characters,
1500 per message; recent evidence prioritized. before is the existing keyset
cursor. Selected IDs must be inside that bounded window; otherwise 404.
No import, new transcription, audio download or OCR. Existing saved body text/
transcripts may be used. Attachments without text are skipped.

Unified group mode is explicit and reuses tenant_connect_group_history.page.
Only verified same-group event/participant duplicates collapse; ambiguous copies
remain distinct. Every contributing tenant/channel route is checked again.
Private dialogs never implicitly read other channels.
No second chat store: only structured cards, source IDs/hashes and route metadata.
Source deletion/body changes block stale approval. Ticket binding must match the
exact route; resolved ticket status alone is never successful-resolution evidence.

Before the provider call: mask known contact/participant names and known name parts,
phones/account/identity numbers, email/IP, URLs/DSNs, private paths, password/token/
API key/pairing labels, JWT/key-like strings and labeled addresses. Conservatively
mask full names and common self-identification phrases. No raw webhook, media links
or author identities are sent. Output/employee edits are sanitized again. No chat
content or provider exception body is logged.

Deterministic masking may remove technical content. It is not a universal
natural-language PII detector: indirect personal descriptions and unusual secret
formats may need organization-specific rules. Enable the first release only for
authorized staff selecting technical fragments with this limitation disclosed.

## Truthfulness, review and publication

At most five separate problems per extraction. Unknown refs/malformed result create
no knowledge. Model-certified cause is downgraded to suspected; a reviewer may
classify it confirmed after checking evidence. AI/system messages, resolved ticket
status, negative replies, suggestions and questions do not certify success.
Confirmed outcome requires recognizable explicit human confirmation in the selected
evidence (Russian, Azerbaijani and common English patterns). Ambiguous/other wording
stays unconfirmed. can_approve=false / approval_block_reason=confirmation_required;
approve returns 409. Review cannot invent foreign evidence or force AI to be human.

Pending/rejected cards are never used in knowledge search. Approve validates all
recipient agents and source hashes and invokes existing save_knowledge once per
agent in ONE transaction via its new internal-only connection parameter.
Existing chunk/index/audit code remains authoritative; any source failure rolls back
the whole approval. A stable candidate setup_key deduplicates retries.
Knowledge Sources supports one agent_id per row: selecting several agents creates
ordinary agent-scoped rows, never a company-wide row. Main and internal agents are
supported. Provenance records candidate/extraction ID, reviewer, exact routes and
message IDs. Normal source deactivation removes it from agent retrieval.
No model-weight training, automatic publication or separate runtime KB.

Stable tenant + route + bounded source/model fingerprint deduplicates analysis even
with another request_id. Same request_id and parameters keep the original source window even when new messages arrive.
Reusing one request_id for different parameters gives 409.
Unknown paid attempts are not automatically retried; a new fragment/window uses a
new request_id. No semantic merge of unrelated incidents is claimed. Reviewed
cards are immutable; later knowledge revision requires a new review.

## Limits and integration

Existing provider.generate, model/credential policy, agent run/token ledger and
tenant quota lock are reused. Reserve one call + 16000 tokens. Shared company daily
call/platform token caps include extraction; actual usage settles the reservation
even on invalid output. Unknown usage keeps the conservative reservation; known
pre-call failure releases it. Additional ceiling: 10 analyses per tenant/UTC day,
one active extraction, five cards. HTTP read timeout 25 seconds; receipt deadline
30 seconds discards late results, without promising HTTP cancellation.
No new queue worker, service or background scan.

Terra owns UI: show masked evidence, pending/unconfirmed status and can_approve;
keep request_id on retry; handle running/unknown/error states; require explicit
approval plus recipients. Use existing agent list, never select all automatically.
HTTP 200 with request.status=unknown is NOT successful extraction.
Contract was sent to Terra; UI remains a separate integration.

Integration seams: additive schema hook, optional transaction participation in
save_knowledge, new API routes and exact route-registration test. Preserve newer
main changes when combining these hooks. No worker/coordination edits.

Tests use isolated PostgreSQL/WASM and mocked provider: private/group/multi-problem,
cross-channel copies, AI/unconfirmed evidence, edit/review/scoped publication and
deactivation, tenant/channel/rights, source changes, privacy before model,
idempotency/quotas/concurrency, uncertain billing, atomic rollback, HTTP origin/
identity, existing Knowledge Sources/expert/group-history regressions.

## Verified results

118 focused/regression checks passed across candidate workflow, group history, support,
routes, Knowledge Sources library and AI Expert. After final bounds/idempotency refinements,
the candidate suite was rechecked separately (34 tests). No production calls or paid
model requests; all SQL tests use isolated PGlite and mocked provider results.
