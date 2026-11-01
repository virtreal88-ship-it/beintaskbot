# Dialog and manual knowledge save audit

The owner's exact failed form/draft is not identified. These findings are local
reproductions and source traces, not a claim to have reproduced that live failure.

## Three distinct flows

1. Dialog **Send for learning** calls `operator-preview` then `operator-submit`.
   `tenant_operator_case_submission.py` validates the selected continuous source
   interval and exact assigned agent/version/revision, then calls candidate
   analysis. The committed result is a case candidate for review, not an active
   knowledge source. Existing fixture checks confirm idempotent resubmission,
   changed-assignment rejection and cross-tenant/noncontiguous selection rejection.
2. Case edit saves a private candidate. Approval/confirmed publication and owner
   guidance publication are separate explicit actions in
   `tenant_knowledge_candidates_store.py`. Existing fixture checks confirm private
   pending-review state and atomic/idempotent owner guidance publication. No live
   case was edited or published during this audit.
3. Agent Knowledge tab text POST `/knowledge` and multipart `/knowledge/upload`
   call `save_knowledge`, which validates, inserts the source and chunks, audits,
   commits, then returns the source ID. Existing direct-add semantics are active
   knowledge: primary-agent additions are company-wide; expert additions include
   `agent_id`. This scope and activation behavior is preserved. The top agent-save
   action does not save knowledge fields, so it is hidden on the Knowledge tab;
   explicit Add text/Upload actions remain visible.

## Reproduced and fixed

With the real DOM/scripts and a synthetic persisted HTTP store, the original
handlers issued two POSTs for rapid activation, and successful POST followed by a
failed GET erased the input and overwrote the refresh failure with a success notice.
The new shared in-flight guard prevents duplicate clicks. Success requires a source
ID. Known success clears only submitted values still unchanged; later input and
other agent/tenant editors are not cleared. Readback checks that exact ID. Failed
readback reports that the object is saved and the list needs refresh, with its ID.
Failed POST preserves input. Ambiguous transport/JSON/5xx outcomes (including a
committed write followed by malformed HTTP 200 JSON) warn that save
is unconfirmed and the list must be checked before resubmission; no automatic
retry occurs. Durable retry deduplication across reloads is not added to the
company-wide endpoint, and a manual resubmission after an unknown outcome remains
a limitation.

Native PostgreSQL/psycopg tests read a committed source through fresh connections,
including chunks/audience metadata and foreign-tenant denial. This is separate
from the mocked UI tests. No database schema or backend mutation path is changed.

## Validation

- Before patch: `node tests/test_manual_knowledge_save_ui.cjs --baseline` reproduced
  double creation and hidden readback failure.
- After patch: the same fixture (without `--baseline`) passes text/file failure,
  duplicate, readback, later-edit, stale-target, audience, uncertain outcome and
  explicit-save scenarios.
- Seven focused Python checks pass: two fresh-connection manual persistence cases
  and five candidate submission/review/publication regressions, providers mocked.
- Existing Knowledge Center and case-loading UI suites pass.
- `test_tenant_operator_workspace_ui.cjs` fails at workspace bootstrap/navigation
  unchanged on base 89583dd; it does not reach the modified save handlers.
- `test_tenant_knowledge_candidates_card_ui.cjs` also fails unchanged on base
  89583dd at its missing-field fixture (line 18); no candidate-card code changed.
  The broader candidate UI and app syntax checks pass.

Real-site acceptance still belongs to the release coordinator. No synthetic
production knowledge, arbitrary publication, message or paid AI request was made.
