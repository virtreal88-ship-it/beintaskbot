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
AI extraction cannot establish confirmed cause. New operator_actions are concise translations
in the requested output language, paired with operator_action_evidence containing exact
redacted quotes and original message IDs. Unsupported actions are dropped. Evidence messages
and IDs remain unchanged; source evidence is rechecked on save and approve. Remote/phone
without supported actions clears unsupported instruction steps. Employee PATCH may supply
actions after investigation.

Existing PATCH /api/platform/ai/knowledge-candidates/{id} accepts these content fields plus
expected_version. approve/reject/clarify routes and Knowledge Sources publication remain.
Full legacy cards are accepted; old stored JSON receives defaults on read without rewriting it.

## Language

Manual extraction POST and /batches POST accept language: ru | az | en. Support-case UI requests
AZ for new cases regardless of source-message or general interface language. A request with no
language uses AZ, including campaign worker requests. API callers can explicitly request ru/en.
No company/agent settings are changed. Provider output_language is explicit and is part of dedup
identity. Evidence, quotes and message IDs remain in their original language with existing
PII/secret masking. Languages are tested with provider mocks, not paid calls.

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

In improve mode, the model edits title and symptoms ONLY. In translate mode, it proposes
AZ text for title, symptoms, cause, resolution_steps, operator_actions and outcome while
preserving certainty, method, product/version and evidence references. It sees redacted original
evidence, current card and employee clarifications. The translation is only a proposal: the
employee compares every explanatory field, confirms the translation and explicitly saves it.
Cause/outcome statuses, outcome/source message IDs, case_kind and product/version are protected.
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
