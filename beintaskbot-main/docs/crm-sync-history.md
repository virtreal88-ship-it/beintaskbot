# Local CRM synchronization history

The read-only endpoints are GET
`/api/platform/crm/native/{contacts|deals|companies}/{id}/sync-history`.
The fixed route identifies the source type. Every request reloads current active
tenant/membership and exact source-card R, including department scope, inside
one database transaction. U alone does not authorize history. Owner behavior
uses the existing current policy rather than a new role exception. Foreign,
archived or out-of-scope source cards are not returned.

Scalar and legacy events are bound to the requested card. Relation events are
filtered by `intent.source`: reading the changed target card does not expose
an event authorized from another source card. No related target R is invented.

The public response contains only event ID, kind, normalized recorded state,
safe attempt-outcome label, local revision, created/recorded timestamps and a
small allowlist of issue labels. It excludes field/value snapshots, actor IDs,
credentials, external record IDs, mappings, account/generation data, plans,
observations, raw errors and provider names. Malformed revision text becomes
null rather than being echoed. Conflicting unknown/sending evidence overrides
a legacy row claiming completion. A `not_started` marker is usable only when
its claim counter matches the current outbox attempt. Missing, null or stale
counters and completed evidence for a still-pending/processing attempted claim
remain unknown; they never prove the current claim was unsent or completed. All responses, including denials and errors,
are private/no-store with `Vary: Cookie`.

Pagination defaults to 20 events and is capped at 50. The cursor is the complete
`before`, `before_id`, `before_kind` tuple, ordered by creation timestamp, event
ID and kind. It cannot change source/tenant scope. Each page reauthorizes and
new events require refresh; it is not a frozen multi-request audit snapshot.
The new indexes support these source-bound reads without rewriting events.

The UI reads local journals, never the provider. It shows the latest stored
outcome of each durable event, not an append-only audit of every state
transition. Event identity and local revision are retained, while a dispatcher
may update that event's recorded outcome. Relation-event local revision belongs
to the changed record in its immutable intent; no target identity or contents
are exposed. A recorded `synced` outcome means the journal recorded completion
at that time, not that the current external CRM was checked. Empty history
must not imply that synchronization succeeded, was unnecessary, or that no
untracked historical work exists.

There are no retry, resolve, clear-unknown, mapping-adoption, permission or
provider actions. History does not close the remaining external relation,
verified mapping, unknown-outcome recovery or rollout-transition blockers.
The feature does not activate scoped production synchronization.

Verification suites: `test_crm_sync_history.py` (isolated SQL/API) and
`test_crm_sync_history_postgres.py` (same contracts and real-session revocation
fence on a disposable localhost PostgreSQL DSN). UI tests exercise current card
paths and auth/close/stale-response handling. Test requests use synthetic local
data and no external provider traffic.
