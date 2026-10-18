# Knowledge candidate refinement API

Base production: 35886f54. Existing session, owner/admin AI management and dialog/channel
permissions, same Origin and expected_tenant_id/expected_user_id checks remain mandatory.
No agent/runtime/Connect/webapp changes; no new tables or production migration required.

## Card fields

Existing content and expected_version remain compatible. Optional editable content fields:
- resolution_method: chat | remote | phone | unknown (legacy default unknown).
- operator_actions: up to 10 strings, each <=800 chars (legacy default []).
- case_kind: technical | informational (legacy default technical).

Computed response fields (do not PATCH): result_confirmed, clarification_status
(required | not_required), can_approve, approval_block_reason. Change confirmation through
existing outcome.status/text/message_ids. Source evidence is rechecked on save and approve;
a boolean supplied by a caller cannot manufacture confirmation.

Known remote/phone success is distinct from known actions. A confirmed outcome with missing
operator_actions remains needs_clarification and cannot be published. Technical instructions
also require nonempty resolution_steps. Informational cases require no technical cause or
technical steps, but still require source confirmation. Unknown/suspected cause is explicitly
labelled; a confirmed technical cause is not required for an evidence-backed workaround.
AI extraction cannot establish confirmed cause and only accepts operator_actions that are
literal redacted spans from selected source messages. Remote/phone without actions clears
unsupported instruction steps. Employee PATCH may supply actions after investigation.

Existing PATCH /api/platform/ai/knowledge-candidates/{id} accepts these content fields plus
expected_version. approve/reject/clarify routes and Knowledge Sources publication remain.
Full legacy cards are accepted; old stored JSON receives defaults on read without rewriting it.

## Language

Manual extraction POST and /batches POST accept language: ru | az | en. Pass the UI-selected
company/user language explicitly. Fallback: profile.language, language_code, company_language,
then ru; no company/agent settings are changed. Provider output_language is explicit and is
part of dedup identity. Evidence and literal action excerpts stay in their original language
with existing PII/secret masking. Languages are tested with provider mocks, not paid calls.

## AI improvement

POST /api/platform/ai/knowledge-candidates/{id}/improve

JSON: request_id (UUID), expected_version (integer), language (ru|az|en),
clarifications (optional string <=2000 chars), analysis_agent_id (optional UUID),
expected_tenant_id, expected_user_id.

Response result: request (existing extraction receipt with status/error_code), candidate_id,
expected_version, language, proposal (content object or null), requires_explicit_save:true,
protected_fields. Synchronous bounded call; retry the SAME request_id to fetch its receipt
without another provider call. Same version/language/clarifications with a different ID also
reuses the receipt. Reusing an ID with changed parameters returns 409. Unknown/failed calls
are not auto-retried; unknown usage retains its existing token reservation.

The model edits title and symptoms ONLY. It sees redacted original evidence, current card
and employee clarifications. cause, resolution_steps, operator_actions, resolution_method,
outcome, source_message_ids, case_kind, product/version are protected and copied by server.
Unexpected factual output fields fail closed. This intentionally does not translate or
rewrite factual instructions; edit those explicitly with PATCH before requesting editorial
improvement. Free-text editorial suggestions still need employee review; semantic truth or
perfect PII detection cannot be guaranteed by a language model or deterministic masking.

Show a comparison first. Save only after employee action:
PATCH /{id} with expected_version from response and proposal content fields. No automatic
save, version increment, candidate creation, publication or external customer messages occurs
on /improve. Stale version, changed evidence, revoked rights, archive/emergency stop or expired
deadline prevents a usable proposal. A stale proposal's later PATCH also fails version check.

Uses the existing provider credential policy and run/token ledger, 16000 token reservation,
1800 output limit, 25s provider timeout/30s receipt deadline, company daily/token limits,
10 combined extraction/improvement requests per day and one active request per tenant.
Review proposals live in existing saas_ai_agent_runs.result; notes are not stored separately.
No second knowledge base, scheduler, new service or automatic reanalysis.

## Terra integration

Add selectors/fields for method, kind, employee actions and confirmation evidence; show
result_confirmed independently from clarification_status. Use can_approve/block_reason to
disable approve. Add language to analysis/batch/improve requests. Display source quotes as
returned, without translation. AI action opens a proposal comparison and explicit Save,
with stable request_id on retry and expected_version on save. Existing status filters remain.
Backend branch: codex/knowledge-card-refinement. No UI files changed in this branch.

## Verification

Targeted regression suite covers candidate/batch SQL, route registration, editorial endpoint
Origin/session checks, remote/phone missing actions, informational confirmation RU/AZ/EN,
explicit save/version checks, stale/revoked/emergency/deadline cases, no publication/sends,
redaction, quota accounting, unknown timeout reservations and idempotent request replay.
Provider calls are mocked; no production or paid model analysis is used.

Final result: 89 targeted tests passed in 38.67s. git diff --check passed;
AI worker, Connect and /webapp unchanged. Contract communicated and acknowledged by Terra.
