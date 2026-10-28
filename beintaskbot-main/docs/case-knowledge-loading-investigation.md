# Cases / Knowledge Center loading

The independent stage changes only the two frontend controllers, regression tests,
and the loading CI workflow. No campaign/model/backend logic, API permissions,
analysis budgets, media grants, mobile layout, or production data were changed.

## Reproducible baseline and after

Run from `beintaskbot-main` with the isolated JSDOM dependency installed:

```sh
NODE_PATH=./.support-test-deps/node_modules node tests/test_case_knowledge_loading.cjs --baseline-ref 9339533f7bae70a3b45cffe6cbab98b4df9a2270
NODE_PATH=./.support-test-deps/node_modules node tests/test_case_knowledge_loading.cjs
python -m pytest -q tests/test_knowledge_center_request_contract.py
```

These execute the shipped controllers (including the actual campaign module) with
synthetic HTTP, not provider calls or production mutations. The baseline is main after PR306; PR306 adds one campaign photo-model settings GET to both first and repeated entries compared with the earlier baseline. Request counts exclude `/me`, performed by the
app shell. Deferred-response barriers establish what is visible before HTTP finishes;
these are not measurements of production wall-clock seconds.

| Journey | Before | After |
| --- | --- | --- |
| Cases first entry | 6 GET; list waits for dialogs/agents/channels | 6 GET; list starts independently and renders before metadata |
| Cases repeated entry within 30s | 6 GET; cards cleared | 3 GET (list + campaign status + photo-model settings); existing rows immediately visible |
| Sources first / repeated entry | 4 / 4 GET; list cleared | 1 / 1 GET; existing rows retained during revalidation |
| Case detail with conversation route | fresh detail + history each time | still 2 GET per open; concurrent detail opens share their pending GET |

Expired metadata is revalidated (6 GET for Cases again). Sources no longer waits
for unrelated instructions, candidate lists or the unused agents collection.
Instructions and Cases tabs still load their own data and surface their errors.

## Reported parameter error

Reported scenario: Knowledge Center → Knowledge Sources → Any status, empty search,
Refresh; banner `Некорректные параметры.`. No production endpoint/status trace was
available. Injecting candidate HTTP400 reproduced the old Sources blank list/banner
although the knowledge request succeeded. This proves an unnecessary all-or-nothing
dependency, not the cause of the production request failure.

Three contract tests prove the exact `status=all&limit=50` request passes the real
handler's identity check and the real store's status/limit validation with synthetic
DB fixtures. The handler also maps processing ValueError/TypeError/KeyError to the
same generic400. API validation was not weakened. Required Sources/Cases endpoint
errors remain visible. Production resolution must be verified after an authorized
release; do not claim the production error's root cause is identified.

## Retention and invalidation

- Memory belongs to this page and authenticated tenant/user/authorization scope;
  no localStorage, cross-session response cache, or detail-result cache is added.
- Cases retains only the currently applied filter and loaded pages. Pagination
  uses the applied query even while filter controls have unapplied edits. Applying
  another filter clears incompatible rows. Revalidation replaces the first page
  and cursor; searches remain local. Knowledge tabs own their pagination.
- Concurrent reads coalesce. Request epochs reject old session and pre-mutation
  responses; selection generations prevent late detail opens or Close reopening.
- Successful case edit/review/publication/archive/delete/boundary/source-review
  invalidates retained lists and reloads. Knowledge mutations invalidate both
  controllers through `tenant-knowledge-changed`. Campaign/proposal POSTs do not
  erase cases. Existing campaign/batch refresh mechanisms remain in use; there is
  no new server push subscription. Returning to a view always revalidates.
- Auth pending conceals data and blocks API work. Same-user auth resumes case
  drafts, including when display metadata or permission ordering changes. Changed
  authority, failed auth, logout, or HTTP401/403 discard retained data and modals.
- Shared knowledge rows commit when their own response arrives, so an unrelated
  slow tab cannot later overwrite a fresher Sources result.

## Validation and review

New executable suite checks counts and pre-response rendering, required endpoint
errors, coalescing, filter/page boundaries, metadata expiry, fresh detail/history,
Close/Cancel, same-user dirty editor, authorization failure, late tenant/user/logout
responses, local mutation races, campaign/proposal reads, batch monitoring and
cross-tab source ordering. The real API/store contract suite passes (3 tests).

Existing current workflow journeys pass: support cases integration, modal editing /
boundaries / deletion, locales, Knowledge Center and profile-driven source deletion;
identity, case media admission/coverage, campaign controls/reconciliation also passed.
Independent read-only review and independent new-suite execution found no new
blocking regressions after the fixes.

Three older standalone tests fail identically in the unchanged controller on the
PR304/main-based worktree: `test_tenant_knowledge_candidates_batch_ui.cjs` lacks a
JSDOM confirm stub; `..._card_ui.cjs` expects removed `caseMethod`; and
`..._media_review_ui.cjs` expects an old English label. They are outside the current
CI journeys and were not rewritten as part of this change.

Inherited limitations confirmed by review, not introduced here:

- `showBatch` can stop scheduling its next poll if a competing list/filter load
  supersedes its list load. The same early-return structure predates this branch.
- Remounting the existing campaign panel resets an unstarted campaign form draft;
  that module and its remount behavior remain unchanged. Case editor draft retention
  is separate.
- A cached list is a previous snapshot while revalidation is pending; fresh detail
  and server version/authorization checks still govern actions. No production
  latency claim or production-error resolution claim is made.
