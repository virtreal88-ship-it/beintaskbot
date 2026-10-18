# WhatsApp case batches — API extension

Base: current origin/main 241b74a9 plus knowledge-candidates eb6187ca, applied locally
as 030d6703. Feature branch: codex/whatsapp-case-batches.
No merge, push, deployment or production reads. Original candidates branch is preserved.

## Manual batch API for Terra

POST /api/platform/ai/knowledge-candidates/batches
JSON: request_id UUID, channel_id UUID, period_start, period_end.
Optional: provider=connect, conversation_id, history_mode=channel|unified_group,
analysis_agent_id UUID, before UUID (only with a specific conversation).

Period is timezone-aware RFC3339, normalized to UTC, half-open [start,end), max31 days.
The company comes ONLY from the authorized session/current company. All requests
include expected_tenant_id and expected_user_id; writes require canonical Origin + JSON.

Without conversation_id, discover up to four most recently active conversations
of the selected tenant/channel during that period. In unified_group mode, a group
linked to the selected channel is also discovered when only its other same-tenant
channel has period activity. Private chats never union across channels.
With conversation_id, read that exact authorized route. No dialog needs to be opened.

Response: HTTP202, {success:true,result:{batch:{...}}}.
A finite process-local task starts only for a newly accepted explicit POST.
There is no persistent polling worker, startup replay or scheduled history scan.
POST replay returns the same batch and never starts another task.
GET /api/platform/ai/knowledge-candidates/batches/{batch_id} returns the same envelope.

Batch fields:
id, status, params, items[], continuations[], routes_truncated, message_count,
error_code, limits, candidates_query:{batch_id}.
Statuses: running, completed, partial, failed, interrupted.
Items: ordinal, route, extraction_id (nullable), status, error_code, message_count.
Safe item errors: access_denied, limit_exceeded, analysis_unavailable,
model_unavailable/invalid_model_result and existing extraction failure codes;
no provider exception body or private message is returned as an error.

Partial means fragment capacity was reached, routes were truncated, or some work
failed while other fragments succeeded. A continuation contains the exact route,
period and before cursor. To continue manually, POST a NEW request_id for that
conversation and returned before cursor. No automatic continuation or retry.
routes_truncated means only the first four discovered routes were included:
choose a specific remaining conversation or narrow the period.
Queue and status responses do not claim complete analysis of an entire company.

Process restart/cancellation may interrupt work. After the180-second receipt
deadline, polling reports interrupted/batch_interrupted; the batch is not replayed.
Existing paid extraction receipts/token reservations remain conservative and
idempotent. A new explicit selection may reuse prior evidence, never silently refund
or retry an uncertain provider request. This is intentionally not a durable job queue.

## Existing review queue API

GET /api/platform/ai/knowledge-candidates now accepts optional:
channel_id, conversation_id (requires channel_id), status, batch_id, limit1..50,
before (candidate UUID keyset cursor). No channel filter gives the permitted
current-tenant queue. Result: {candidates,limit,next_before}. Reset cursor when filters change.
Every candidate's source route is checked server-side. Cross-tenant filters/cursors
cannot expose cards. Same owner/admin ai_agents_manage + dialogs rights as before.

POST /api/platform/ai/knowledge-candidates/{id}/clarify
JSON: expected_version. Marks needs_clarification, increments version and audits.
This nonfinal state remains editable/rejectable and may be approved only after
the existing human-confirmation check succeeds. The model does not set review status.
Initial extracted cards always remain pending_review, even when unconfirmed.

Existing GET detail/evidence, PATCH, approve/reject and agent-scoped Knowledge Sources
publication remain in use. Pending/needs_clarification/rejected cards are never
knowledge sources. An approved source still needs the original explicit employee
approval and selected recipient agents. No new runtime knowledge database.

## Limits and evidence integrity

Per batch: <=4 fragments, <=4 paid calls, <=320 source messages, <=20 cards,
<=64000 token reservations (4x existing16000), plus existing company daily token/call
limits and10 analyses per UTC day. One active batch per tenant; each fragment also
uses the existing one-active-extraction guard. Each fragment keeps the prior
80-message/12000-text-character/input-token limits, PII masking and safe provider.

When the character/token cap trims a window, the continuation points to the oldest
ACTUALLY analyzed message, so older unprocessed messages are not skipped.
An attachment-only window can be skipped with an explicit continuation, still within
the four-fragment cap. No audio download, import, OCR or unscheduled analysis.

Unified history reuses the existing verified event/participant deduplication and
tenant/channel authorization without modifying Connect or the coordination layer.
Analysis fingerprints use actual authorized evidence/model rather than period/cursor
selection metadata. Identical fragments in overlapping periods reuse their existing
extraction/cards; the response retains the current selection's continuation cursor.
Legacy eb6187ca receipts are found by matching evidence hashes, refs and model/agent.
No semantic deduplication of merely similar unrelated incidents is claimed.

Model generation and billing still go through existing candidates service.
No client sends, agent settings, session state, transport, worker or /webapp changes.
PII masking retains the first-stage documented limitations: deterministic patterns
and known identities are not a universal natural-language privacy classifier.

## Integration

Terra received launch/poll/queue/clarify contract directly. UI should show period,
selected channel/group, partial/truncated scope, item errors and actual message count;
retain request_id across retries and require a new manual action for continuation.
Evidence routes open the existing dialog view. No automatic approve/publish controls.

Combine additive batch schema, status constraint migration, route registration and
candidate queue API changes with current main carefully. eb6187ca is a prerequisite;
030d6703 is only its local cherry-pick on the verified production-main base.
Migration preserves old pending/approved/rejected cards; no agent data rewrite.


## Verification

77 targeted/regression tests passed (34.02 seconds), using isolated PostgreSQL/WASM
and mocked model calls. Coverage includes multiple cases, two-channel group copies,
group discovery from the other linked channel, timezone/half-open bounds, caps,
queue filters/keyset paging, clarification/edit/reject/approve, legacy source
publication, overlapping-period deduplication, finite HTTP dispatch/idempotency,
interrupted jobs, migration preservation, PII and existing candidate/group/library
regressions. git diff --check passed. No production or paid model calls were made.
