# Support inbox employee scopes

This change covers **Обращения / support inbox**, not the AI knowledge-case catalog.
The opt-in `TENANT_SCOPED_ACCESS_ENABLED` gate remains unchanged. No tenant/member
configuration or provider settings are changed by this implementation.

## Enforced operations

- Catalog: independent `support.view`, `support.create`, `support.update` with
  `none`, `self`, `department`, `all`. There is no support-ticket delete route,
  so the editor has no Delete scope.
- View: list, search, pagination, unresolved count, channel options, detail,
  pinned messages/attachments, summary versions and existing in-document context.
  Assignee options are returned only when the displayed page contains a currently
  update-authorized ticket, never solely because the employee may read tickets.
- Update: text, status, executor/developer assignment, completion, summary reserve
  and apply, attaching materials and case-bound consultations. Existing owner-only
  sharing/group dispatch and source/dialog requirements remain additional checks.
- Create: creator is responsible, regardless of executor. Creation on an already
  open case is an update and requires that case's independent update scope. Exact
  idempotent replay of the creator's event needs no new update grant. Actor-driven
  AI handoffs use the same writer and cannot bypass explicit support scopes.
- Create/update without View returns a minimal receipt, not document contents.
  Existing dialog manual-create UI accepts that receipt. Ticket controls use a
  row-specific update capability. Current DB membership and workflow policy are
  reloaded; mutation responsibility/department is checked under transaction locks.
- Notification list/count/direct lookup and dialog ticket/handoff projections do
  not expose support records outside an explicit support View scope. Case-bound
  knowledge extraction also rechecks the case. No general dialog permission is
  implied by support access.

Sparse cells retain their exact old support-module authorization, including old
AI-management inheritance for **absent** cells only. Explicit cells are not
bypassed by AI-management permission. Owners retain existing company authority.
Existing ownership migration is unchanged: historical responsibility requires a
unique active matching company owner. Later actorless service tickets are not
silently assigned or repaired.

## Deliberately conservative aggregate boundary

AI diagnostics already using `require_unrestricted_dialog_read` now also reject
an explicit support View scope narrower than `all`. The tenant-wide night report
likewise rejects narrow support View. These are temporary fail-closed boundaries,
not row-filtered aggregate implementations. Absent scopes and gate-off behavior
are unchanged. These restrictions do not grant AI or night-report permission.

## Background/service boundary and remaining verification

Inbound webhook handoffs, autonomous pilot/group follow-ups, system summaries,
pre-existing public snapshots and service-only receipt recording do not have an
employee request principal. They retain their existing service/owner/source,
consent and dispatch fences; this matrix does not convert those service grants
into employee rights or revoke previously shared public snapshots. The internal
Telegram consultation notifier sends only a generic review notice, not case text;
opening the linked inbox rechecks current scoped access. A future requirement to
change autonomous service ownership or retract delivered/public material needs a
separate design, not an inferred employee grant.

Local tests use synthetic in-memory PGlite and mocked providers. The equivalent
`test_support_inbox_scopes_postgres.py` suite requires the existing disposable
localhost-only `BEINTASKBOT_TEST_POSTGRES_DSN` fixture. No local DSN is available,
so real PostgreSQL locking/races remain CI verification, not a claimed local pass.
No production configuration, customer message, upload, provider call, push,
merge or deployment is part of this change.
