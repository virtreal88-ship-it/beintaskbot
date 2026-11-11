# Sparse CRM read scopes

Legacy provider snapshots do not have the native resource ownership required for
self/department filtering. The new `TenantPolicy.can_read_legacy_crm` boundary
checks the requested resource's explicit **view** scope before considering the
historical deals-or-customers entitlement.

- Absent view override retains legacy read grants, including when create/update/
  delete has an independent scope.
- Explicit none/self/department cannot use the other module's legacy grant to
  read unfiltered provider snapshots. Native resource-aware reads still apply.
- Explicit all retains an existing legacy read entitlement and existing tenant,
  pipeline and stage filters. It does not create a new external-provider grant.
- External contact-directory rows include deal context. Both customers and deals
  view restrictions apply to that projection; native contacts remain independent.
- Owners and flag-off legacy behavior are unchanged. Explicit source-scoped dialog
  reads continue through their existing transactional reader, not this fallback.

The same boundary covers legacy deal list/detail, `can_access_deal`, board/chat-AI
metadata, legacy chat fallback, external contact directory, operator pipeline
choices and employee-grant scope comparisons. The CRM sync-status endpoint uses
it for ordinary deal access; existing pipeline-task status access remains separate.

The bot's `_sync_tenant_crm` compatibility function is an enqueue-only service
read request, not a record-returning authorization route. It and actorless inbound
import are unchanged. No provider credentials, writes, binding evidence, ownership
migration or new synchronization guarantees are introduced.

Regression tests reproduce sparse overrides with the other legacy grant retained,
assert rejection before storage/refresh, preserve absent/all/action-only behavior,
and exercise current stored matrices against PGlite and native PostgreSQL. The
native suite requires an explicit disposable localhost DSN and is selected in CI.

Both task deal-picker implementations additionally suppress only the arbitrary
`deals` choices when deals.view is none/self/department. They retain executors,
task operations and the existing task-only/pipeline picker entitlement when view
is absent/all. This restriction predicate does not require legacy deals/customers
permissions, and does not change already-assigned task context.
