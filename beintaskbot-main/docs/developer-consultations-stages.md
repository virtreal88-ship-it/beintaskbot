# Developer consultations: staged simplification

## Stage 1: safe entry and routing configuration

- A direct Ask developer action opens the existing consultation preview from the client dialogue. The existing Copilot entry remains compatible.
- Case creation does not assign a developer and does not send a consultation. The responsible employee remains separate. Old developer-assignment fields are retained as history; the active assignment editor is removed in favor of the existing consultation action.
- In the separate consultation action, employees choose a marked, currently accessible developer conversation or group, inspect selected context and attachments, edit the question and explicitly confirm sending.
- The new primary/reserve routing mode has at most one initial automatic recipient. The reserve is stored separately and is never dispatched automatically. No timeout or fallback policy is introduced.
- Existing multi-recipient settings are not silently migrated. Editing them requires an explicit primary selection before saving the new routing mode.
- Owner, tenant, current destination visibility, manual-mode, assignment, case and delivery-receipt checks remain in force. Unknown delivery never authorizes another send.
- The unread bell excludes only exact, unambiguously matched approval requests whose authoritative command is already terminal. Historical notifications and their read timestamps are retained. Missing or ambiguous proof remains visible.

## Remaining implementation stages

1. The editable initial developer question is implemented in Stage 2 below; further media processing remains a separate explicit action.
2. Extend the existing case-bound continuation into a complete short developer/customer exchange. Every clarification and answer must remain bound to one consultation and original case, with current source/destination rights and assignment checks before each external send.
3. Make automatic client answers follow the configured agent instructions and verified developer evidence. Switching to manual must invalidate outstanding automatic sends at the final dispatch boundary, including delayed worker results.
4. Suppress routine bell notices only for a proven successful automatic path. Blocked, failed, uncertain or unprocessable replies must reliably reach a human; manual developer replies remain actionable. Reading stays separate from handling.
5. Define reserve escalation rules separately with the user. Until then, no automatic reserve sharing, retries or timeouts are enabled.

## Verification and release boundaries

All implementation tests use synthetic messages and isolated databases/providers. Do not test sending against real clients. Run focused UI/policy/projection tests, PostgreSQL-backed integration tests and independent review against the final commit. Coordinate the notification projection with the owner-only proactive-risk work in PR #540. Do not remove that separately authorized signal or restore its removed Linear panel.

Stage 1 is not the complete automatic mediation feature and does not generate the initial manual AI question. It does not enable any company or agent setting during deployment.


## Cross-entry admission and conservative legacy boundaries

Both the dialogue header and existing case form call the same prepare API with
`reuse_existing=true`. The old API default remains fail-closed for a new UUID.
The common source lock is also used by case creation and its automatic enqueue.
Exact request/input-hash retries retain their old identity. Safe reuse requires
identical selected messages, destination, question, effective attachments,
identity disclosure and source versions; the UI shows the existing record and
explicitly says no new request was sent. Reuse never calls dispatch.

An implicit case is accepted only from one bounded causal material event covering
all selected messages on the exact tenant/channel/dialogue. Full history,
snapshots (including old manually selected case-snapshot events), picker additions
and partial/ambiguous overlaps do not prove an episode. Staff must open the exact
case explicitly in these situations. This is intentionally not seamless automatic
matching for all old manually created cases.

An existing independent employee consultation is never retroactively converted
into a case-owned or automatic consultation. If a case was created later, the
second entry fails with an explicit instruction to inspect the original dialogue;
it does not create another request or expand permissions. Different edited
questions on identical sources also conflict instead of silently discarding edits.
Disjoint questions are not merged merely because they share a dialogue or case.
Unknown/inflight transport is fenced even if its old case was closed.

Automatic queued requests are matched to their original handoff material event,
not every later question in that open case. Legacy explicitly configured fanout
is preserved only for siblings with exact original delivery/request provenance.
An automatic worker that encounters an employee consultation records a blocked
review-needed result, never copies its receipt state or sends its prepared body.
Native PostgreSQL cross-entry races run through the disposable CI fixture; no
production messaging or database is used.


## Stage 2: employee-reviewed AI question

Both existing consultation forms have one explicit AI question action. It fills the
editable question field and resets review. Generating never prepares or dispatches a
consultation, creates a public share, or sends a message. Existing explicit send and
cross-entry admission remain authoritative; no automatic reserve or customer reply
is added. Public identity remains off by default.

`POST /api/platform/ai/dialog-consultations/question-draft` accepts the exact selected
message UUIDs, checked media UUIDs, preview `source_version` hashes, optional case UUID
and employee notes. Only this bounded interval is read. There is no latest-history,
knowledge search, or private Copilot thread fallback. Saved evidence is reused only
for checked files with exact source provenance and complete coverage. Missing,
partial, oversized, unselected and unsupported evidence is explicitly unavailable;
this action never downloads or analyzes attachments and never transmits binary media.

The existing Copilot ledger retains tenant/actor, assignment, consent, model and daily
call limits. A deterministic request identity covers employee, route, case, selection,
source versions, files and notes. Reopening or rotating browser UUIDs cannot repay an
identical uncertain call. If only agent settings or saved evidence changes, identical
input fails closed rather than silently retrying; a deliberately changed question or
selection is needed. No additional ledger or schema is introduced.

Current access, case status, message versions and saved evidence are rechecked before
paid text I/O, after generation, on cache reuse, on preparation and again on dispatch.
Employee edits are retained, but they cannot be sent against stale selected evidence.
The AI proof is private consultation evidence, never part of the public fragment.
In-flight results cannot overwrite later edits, switched selections or another route,
case or identity. Dismissal does not send, and repeated clicks share the same attempt.

All tests use synthetic local fixtures and mocked providers. Native concurrent UUID,
unknown-result and source-edit tests run under the existing disposable PostgreSQL CI
job. A local run without `BEINTASKBOT_TEST_POSTGRES_DSN` skips those tests explicitly.


## Automatic continuation groundwork: ingress-generation fence

The existing signed ingress `_case_followup_intent` is revalidated for automatic
case replies at enqueue, before generation, and under the final transport lock.
Every coalesced reply must still match its admitted agent, assignment, agent
version and tenant owner. An automatic → manual → automatic mode cycle cannot
revive an older intent by acquiring a fresh worker reservation. Missing legacy
stamps fail closed for automatic continuation; manual evidence and explicit
resolution retain their existing authority checks.

This slice adds no schema, sender, routing setting, reserve escalation or bell
suppression. The older scoped pilot is not enabled or generalized. Synthetic
PostgreSQL tests cover delayed enqueue/claim, generation and dispatch boundaries,
mixed-generation coalescing, missing stamps and unchanged successful operation.
