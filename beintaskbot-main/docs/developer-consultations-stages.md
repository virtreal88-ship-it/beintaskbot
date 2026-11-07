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

1. Generate an editable developer question from exactly the selected message interval and selected attachments. Bind the generation to source versions, current employee/agent permissions and a durable idempotent request. Do not treat an unrelated prior Copilot answer or an unverified attachment as this question.
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
