# Local dialog reply templates rollout

This pilot is limited to the single UUID-configured BeinSystems workspace through the shared `is_operator_workspace` predicate. The API rejects every other workspace and the client hides its control there. Within BeinSystems, existing tenant, department, membership, and permission checks remain in force. It has no Operator calls and does not write template text or drafts into message history.

## Preflight

`tenant_dialog_reply_templates.migrate(cursor)` is an additive, idempotent schema migration. Run it only through the reviewed tenant database preflight with the approved backup and recovery procedure. The request handler fails closed until the table exists; it never performs DDL. This PR does not run a production migration or deploy the feature.

The migration creates `saas_dialog_reply_templates` and an index for active visibility scopes. It does not alter memberships, existing CRM records, customer conversations, or messages.

## Behavior

- Personal templates are visible to their author.
- Team templates are visible inside their department and manageable by members with the existing department update scope.
- Shared templates are visible to dialogue viewers; creation and editing require the existing all-scope update permission.
- The API rechecks active tenant membership and current dialog permissions on each request.
- Typing filters already loaded tenant-scoped template text in memory. It does not send the draft to the server.
- Selecting a suggestion only inserts text into the existing editable composer. Sending remains an explicit action in the existing composer.
- The module uses only the local same-origin template API. No template or unsent draft is synchronized to Operator.
- There is no localStorage persistence, automatic send, or template export.

## Tests

Synthetic tests cover scope isolation, tenant binding, stale versions, idempotent retries, route wiring, and that typing/insertion/management do not call a message endpoint or store drafts locally.
