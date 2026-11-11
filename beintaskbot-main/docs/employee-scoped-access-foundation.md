# Employee access scopes: foundation and rollout contract

## Default behavior and release gate

`TENANT_SCOPED_ACCESS_ENABLED` is **off by default**. Additive schema installation does not rewrite permissions, `native_crm_actions`, roles, source bindings, integration configuration, AI settings, services, or notification preferences. While the gate is off, legacy authorization remains in force, the scoped editor catalog is empty, and the employee-card writer rejects scoped edits.

Enabling this flag is a separate rollout decision after all applicable routes and their tests are verified. A passing foundation test suite is not sufficient evidence to enable it. The current catalog contains native task/deal/contact/company CRUD, real dialog read/update operations, finance read, night-report read, AI document create/read/update plus unused-agent archive, employee-card create/read/update, support-ticket create/read/update, and shared-integration CRUD. Employee creation supports only none/department/all, not self. Agent D does not transfer data or permanently delete an agent. No dialog create/delete, employee delete, or support delete operation is invented. Each section retains the narrower contracts documented in its scope audit; catalog presence is not rollout approval.

For a member with any explicit scope in a section, coarse legacy entry points for that section fail closed. This prevents unconverted external-provider/AI/legacy routes from bypassing a restriction. It may remove previously available provider operations, so rollout requires a source-specific access comparison and resolution, not silent activation. Existing source bindings are never inferred or imported.

Turning the rollout flag off is not permission to discard safety provenance. Records already marked by scoped CRM edits retain their actorless-export barrier, and uncertain delivery attempts remain blocked. A binary rollback must retain these protections and the additive tables; an older worker that lacks them is not a safe rollback target. See `../../docs/crm-scoped-scalar-sync.md` for transition and recovery prerequisites.

## Stored data and compatibility

- `saas_tenant_departments` is tenant-owned, with composite primary key `(tenant_id, id)`.
- `saas_tenant_members.department_id` is nullable and references the department in the same tenant.
- `workflow.policies.members[actor_id].access_scopes` is a **sparse** object: section → action (`view/create/update/delete`) → scope (`none/self/department/all`). An absent cell means the previous action policy, including all its provider/pipeline/role prerequisites. It never means an automatically generated tenant-wide grant.
- Contacts use `customers`; `contacts` is a read-time alias. Companies have an independent scoped section and use the old `customers` policy only when their corresponding scope is absent.
- A configured but malformed scope denies access. Explicit `none` wins over legacy grants.
- Legacy Boolean action patches cannot silently change a cell already controlled by the matrix. The generic workflow writer can echo a matrix or omit it, but it cannot add/change/remove one. Omitted matrices are preserved from the locked current database value, including inactive members.
- The employee-card matrix writer retains unrelated work settings and uses its existing optimistic access-version contract. Changes to department or matrix alter the access version.

## Historical document ownership

The migration adds `access_responsible_id` to native contacts, companies, deals, and tasks. AI agents and support tickets use the same transaction-safe ownership approach. Schema installation alone does not enable the registered scopes. All existing `responsible_id` and `created_by` values remain unchanged. In particular, a task's original `responsible_id` continues to mean its executor.

For each existing resource table, the initial migration assigns unowned historical rows to the company's owner only if:

1. Exactly one membership has the `owner` role.
2. That owner membership is active.
3. It matches `saas_tenants.owner_telegram_id`.

Missing, duplicate, inactive, or contradictory owner records are not guessed. Their affected document IDs are recorded in `saas_access_migration_issues` with reason `sole_active_company_owner_required`, and their new owner field remains NULL. Review these issues before enabling self/department access. No memberships or permissions are changed to repair them.

`saas_access_schema_migrations` records a separate durable completion marker per resource table. A later startup does **not** backfill a newly inserted system document whose owner is NULL. All DDL, ownership updates, issue records, and migration markers participate in the caller's shared transaction. The migration uses an advisory lock and a table write fence for its historical snapshot. Rollback does not leave an applied marker.

New user-authored documents must explicitly set `access_responsible_id` to their authenticated creator. The field is not an editable responsibility selector or a replacement executor. For new background records with no known human creator, callers must not invent an owner; NULL is accessible only through a permitted `all` scope or preserved legacy behavior until a separate rule is approved.

## Server contract

`TenantPolicy.action_scope(section, action)` returns one of the four scopes or `None` for the legacy path. `allows_action()` is an action eligibility test, not permission to fetch an arbitrary record. Scoped endpoints must additionally enforce the resource/source predicate.

`tenant_scoped_access_store` provides:

- `read_access_profile(cur, tenant_id, actor_id, lock=False)`: re-read current tenant, active membership, department, onboarding data, and workflow. Returns `None` for unavailable membership/company. The broad lifecycle eligibility is active/onboarding/ready_for_integration; existing domain-specific lifecycle checks still apply.
- `resource_scope_sql(profile, section, action, alias='r', responsible_column='access_responsible_id')`: parameterized tenant-bound SQL predicate plus parameters. With `None` scope it checks the old CRUD grant only; the caller retains additional legacy restrictions.
- `owned_resource_metadata(cur, tenant_id, row, lock=False, responsible_column='access_responsible_id')`: resolve a responsible employee's current department from the same company.

Locking order is tenant → sorted policy keys → numerically ordered memberships → domain rows. `read_access_profile(lock=True)` takes a tenant shared lock. Department changes and department catalog mutation must take the tenant update lock first, so a department move cannot invalidate a scoped operation already checked in its transaction. Resource mutations and external dispatches must hold their necessary authorization fences until their operation is complete.

Actions are independent. Broad read does not grant update/delete. The task executor exception permits viewing and completion only, with constrained completion payload. Task navigation remains available for that assigned-only feed even when generic task CRUD is entirely denied.

## Verification performed

Foundation tests cover every CRUD action across the four scopes, two departments and two tenants; disabled memberships; setup/suspended lifecycle states; sparse legacy preservation; catalog validation; uneditable authoritative cells; generic workflow bypass attempts; access versions; SQL parameterization; and assigned-only task navigation.

Disposable localhost PostgreSQL tests verify historical backfill and unresolved owners, unchanged task executors, tenant-composite foreign keys, transactional rollback, idempotent startup without filling new NULL documents, department changes, membership revocation, shared/exclusive tenant fencing, workflow write rejection and preservation of unrelated settings. These tests use synthetic databases only and send no provider traffic.


## Night-report read scopes

`night_report.view` is an independent read grant. Connect activity/error figures use current source ownership; ticket queues and ticket counts use the ticket's `access_responsible_id`. Ticket-scoped correspondence requires explicit message links and never grants the complete conversation. See [the report boundary audit](night-report-scopes.md) for all read routes, stale-page handling, compatibility and verification. This addition does not enable the rollout gate or grant report generation/settings authority.
