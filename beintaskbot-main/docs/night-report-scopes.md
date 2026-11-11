# Night report: independent read scope

The sole applicable action is `night_report.view`: `none`, `self`, `department`, or `all`. There are no report create/update/delete cells or new generation/settings endpoints. The global `TENANT_SCOPED_ACCESS_ENABLED` gate remains off by default; this change does not enable it or rewrite any employee rights, department, source mapping or ticket owner.

## Authorized rows and aggregates

Each request rereads the selected tenant, active member, current workflow and department inside the same read-only repeatable-read transaction used by the report. A cached/client role or department is not authority. Owner behavior is preserved. The existing exclusion of the `master` role and tenant lifecycle checks remain.

- Connect conversation rows, distinct activity, AI activity, handoffs, AI errors and Connect errors use the current Connect source owners from `saas_dialog_source_members`. Self requires the current employee to be an active source owner. Department requires an active source owner in the actor's current, non-null department. Unmapped sources are not inferred and are invisible to self/department. All remains tenant-bound.
- Ticket rows, saved descriptions/summaries, unresolved-at-cutoff totals, current attention totals and taken-in-work totals use each ticket's `access_responsible_id`, independently of source ownership. This is the existing access responsibility, not the `assigned_to` executor. Department is resolved from current same-tenant membership. NULL responsibility is visible only to all or preserved legacy authority.
- Ticket and source predicates are applied before aggregate calculation, ordering, LIMIT and OFFSET. The first 20 failure rows and their totals share the source predicate. A ticket on somebody else's source may be readable without adding that source to dialog/error totals.
- Scoped projections exclude internal ticket ownership/configuration hashes, developer-route metadata, raw provider fields and diagnostic event keys. Failure codes are projected as known, safe categories. Names included with a visible row are only that row's customer/channel and ticket assignee; no member/source selector or tenant-wide roster is returned.
- Self/department report defaults use the company timezone or the documented default, not the schedules of inaccessible AI agents. No settings are changed.

## Read route audit

- `GET /api/platform/ai/night-report`: date/time filters, counts, summaries, attention pagination and failure records all use the independent report predicates.
- `GET /api/platform/ai/night-report/tickets/{ticket_id}`: first proves the exact ticket's responsibility scope. Its read-only history then requires `saas_support_ticket_messages` membership for that exact ticket **and** matching tenant, message lead and channel for the ticket's exact conversation. Other cases in the same conversation, unlinked messages, corrupt cross-tenant/channel links and foreign pagination cursors cannot expand it. This strict linked-message rule also applies to old `legacy` or `conversation` cases. No links means empty history; readers never backfill or fall back to the whole conversation.
- `GET /api/platform/ai/night-report/dialog`: separately proves source ownership for the exact tenant/channel/conversation and reads only that route's saved messages. A ticket grant alone cannot call this route for another employee's source.
- No report search, export, mutation or provider-fetch route exists. No new one is added. The three existing routes remain GET-only and no-store.

The report grants no general dialog/support navigation, provider history loading, message send, ticket mutation, AI administration, paid generation or shared settings rights. The scoped report viewer always stays inside its read-only modal, even when an external navigation adapter exists. General AI aggregate guards and operational authority are unchanged.

## Stale pages and revocation

All authorized responses, including legacy and rollout-off reads, include an opaque access snapshot derived only from currently authorized source/ticket IDs and the actor's effective report scope. Pagination/detail calls submit `expected_access_version`; a changed scope, department, source assignment or ticket ownership yields 409 before old and new pages can be combined. Hidden sources and hidden ticket changes do not influence this fingerprint. Exact history pages also bind `expected_history_version` to the currently authorized message set. These values detect stale pages; they never replace current authorization.

The UI compares returned authority/scope and history versions before combining or displaying a continuation, including legacy-to-scoped and reverse transitions. Missing versions from older responses fail closed on continuation. The UI clears cached report rows, modal titles/body, company text and cursors after 403/404/409, on identity/profile change, and before refreshing the report. Pending replies cannot restore cleared content. A full refresh obtains the current scope. Scope explanations and the single report-read cell are available in RU/AZ/EN through the common employee matrix.

## Compatibility and limits

With the gate off, original legacy report permissions, history and defaults remain. With the gate on but no report override, the legacy report grant is still needed and narrower support/dialog scopes continue to fail closed. An explicit report scope supplies the approved independent grant, including when general dialogs/support are denied. No source ownership is inferred; Kommo is not part of this report.

Tests use synthetic PGlite fixtures and no provider or application database access. `test_night_report_scopes.py` covers the scope matrix, all eight metrics, source/ticket independence, exact linked history and malicious links, pagination, lifecycle/department/membership changes, opaque snapshots, actual aiohttp dispatch, and retained general-dialog/AI denial. `test_night_report_scopes_postgres.py` runs the same contract on the existing disposable localhost PostgreSQL fixture; it requires `BEINTASKBOT_TEST_POSTGRES_DSN` and is not simulated with PGlite when unavailable. Existing night-report, API, support scope, shared access and UI regressions remain part of integration verification.

The previous API test compared all repository history to the original report branch base `77d5312a`. Unrelated, later pilot changes made that assertion invalid. It now asserts the actual isolated `/app/night-report` HTML/asset/route/GET-only boundary, without changing the base revision or skipping the test. This implementation changes neither pilot HTML file.
