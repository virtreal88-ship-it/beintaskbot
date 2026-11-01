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
The shared in-flight guard prevents duplicate clicks. A browser-wide Web Lock
also serializes tabs. Before any POST, a tenant/actor-scoped localStorage registry
records a random UUID, audience, payload fingerprint and recoverable text draft.
An existing fingerprint always performs GET by that exact UUID, never another POST.
404, malformed responses, timeout, changed identity or unavailable readback cannot
unlock a resend. Edited content is a new operation; another primary editor/channel
still shares the original company-wide audience rather than silently narrowing it.

`create_request_id` uses the existing source UUID primary key for atomic creation.
The existing provenance JSON stores an immutable request hash over normalized
payload, tenant, actor and audience. Identical concurrent or restarted requests
return the same source; changed payload/actor/tenant/audience or an unrelated legacy
source is a conflict. Existing setup-key/wizard recovery is unchanged and cannot be
combined with this explicit request identity. No schema change, migration, new index
or request-path DDL is added. Upload forwards the same identity after local file
extraction; no provider is invoked.

Success requires an exact committed receipt. Receipt-mode GET additionally checks
the request creator server-side before returning source content; ordinary authorized
company reads retain their existing policy. The detail GET must match source ID,
request ID, tenant, author and audience. Only then are still-unchanged submitted
fields cleared; later edits and other editors remain intact. A known successful
POST with failed detail/list readback reports saved-but-unreadable and keeps its
operation locked. Lost or malformed POST responses report unconfirmed and retain
input. Repeated clicks only reconcile with GET. Records confirmed via readback keep
only identity/fingerprint metadata, removing stored draft text and title.

Native PostgreSQL/psycopg tests read committed sources through fresh connections,
including chunks/audience metadata, concurrent identity races and foreign-tenant
or actor conflicts. Arbitrary legacy IDs cannot be adopted or reconciled as manual
requests; cross-author/tenant receipt reads fail without exposing a source. This is
separate from mocked UI tests.

## Durability and limits

Text drafts and uncertain locks survive full UI reload/browser restart while that
origin's localStorage is retained. Web Locks and persistent storage are required;
unavailable/corrupt storage fails closed before dispatch. Different browsers or
cleared site storage cannot recover the old client-generated identity automatically;
no cross-device content-based deduplication is claimed. The server still deduplicates
the same UUID while its source/receipt exists. Normal archival keeps that receipt;
physical deletion is not a durable separate request ledger. There is no expiry or
unsafe manual unlock for uncertain operations.

For files, binary input cannot be restored to a browser file picker after restart.
The registry retains name/title and fingerprint; reselecting the same bytes/name
reconciles the same operation without upload. A request that never reached the
server remains locked if reads find nothing; an operator must establish its outcome
rather than blindly retry. Persisted draft data is scoped to tenant/actor within the
same browser origin and is removed on confirmed readback; it is not encrypted browser
storage. No paid model call or production write is used to test this behavior.

## Validation

- Expanded API/native PostgreSQL/candidate aggregate: 340 passed before the final
  receipt-read guard and conflicting-race additions; those additions are covered
  by the subsequent focused API/native run: 19 passed.

- The initial baseline fixture reproduced double creation and hidden readback
  failure. The current `test_manual_knowledge_save_ui.cjs` is the expanded
  stable-operation regression; its earlier baseline mode is no longer retained.
- Seven native PostgreSQL checks pass, including fresh connections, concurrent
  identical requests and conflicting actor/tenant/audience/payload identities.
- UI fixture covers commit + lost response, malformed 200, known success + failed
  readback, same-operation repeat, edited new operation, full JS restart, file
  re-selection, tenant/actor/audience boundaries, concurrent tabs and storage denial.
- Existing Knowledge Center and case-loading UI suites pass.
- `test_tenant_operator_workspace_ui.cjs` fails at workspace bootstrap/navigation
  unchanged on base 89583dd; it does not reach the modified save handlers.
- `test_tenant_knowledge_candidates_card_ui.cjs` also fails unchanged on base
  89583dd at its missing-field fixture (line 18); no candidate-card code changed.
  The broader candidate UI and app syntax checks pass.

Real-site acceptance still belongs to the release coordinator. No synthetic
production knowledge, arbitrary publication, message or paid AI request was made.
