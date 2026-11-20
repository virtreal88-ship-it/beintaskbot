# Local dialog reply templates rollout

This pilot is limited to the single UUID-configured BeinSystems workspace through the shared `is_operator_workspace` predicate. The API rejects every other workspace and the client hides its control there. Within BeinSystems, existing tenant, department, membership, and permission checks remain in force. It has no Operator calls and does not write template text or drafts into message history.

## Preflight

`tenant_dialog_reply_templates.migrate(cursor)` is an additive, idempotent schema migration. Run it only through a separately reviewed tenant database preflight with an approved backup and recovery procedure. This repository does not provide an approved live procedure for this migration. The explicit server gate defaults off and requires both the exact pilot workspace and the initialized table; the request handler never performs DDL. This PR does not run a production migration or deploy the feature.

Proposed sequence, not an approved live procedure: obtain owner approval and a verified recoverable backup; in a short tenant-scoped transaction apply the additive migration, verify the table/index, and commit; enable `DIALOG_REPLY_TEMPLATES_ENABLED=true` only after the migration is confirmed. Roll back the application by disabling that setting while retaining the additive table. Do not drop the table as part of application rollback. No new backup service or data export is part of this proposal.

The migration creates `saas_dialog_reply_templates` and an index for active visibility scopes. It does not alter memberships, existing CRM records, customer conversations, or messages.

## Behavior

- Personal templates are visible to their author.
- Team templates are visible inside their department and manageable by members with the existing department update scope.
- Shared templates are visible to dialogue viewers; creation and editing require the existing all-scope update permission.
- Template view and update scope currently derive from the existing `dialogs` view/update policy. There are no separate configurable template permissions. Whether to introduce distinct template capabilities is an open policy decision; it requires a separately reviewed rights model before any permission changes.
- A team edit that keeps `team` visibility retains the row's original department, including for an all-scope editor with no department. Only the author can change visibility; transitions to `team` use the editor's current department and require one.
- An idempotent create retry rechecks current view access before returning an existing row, so a former department member cannot retrieve old team text through the replay path.
- The API rechecks active tenant membership and current dialog permissions on each request.
- Typing filters already loaded tenant-scoped template text in memory. It does not send the draft to the server.
- Selecting a suggestion only inserts text into the existing editable composer. Sending remains an explicit action in the existing composer.
- The module uses only the local same-origin template API. No template or unsent draft is synchronized to Operator.
- There is no localStorage persistence, automatic send, or template export.

## Tests

Synthetic tests cover scope isolation, tenant binding, stale versions, idempotent retries, department-preserving edits and transitions, route wiring, and that typing/insertion/management do not call a message endpoint or store drafts locally.

The dedicated disposable PostgreSQL CI test runs the actual migration twice, checks synthetic tenant/department CRUD and rollback, and drops its isolated schema. The profile capability is false unless the server opt-in is true, the configured pilot tenant matches, and the schema exists; the browser does not mount or call template endpoints when false.
