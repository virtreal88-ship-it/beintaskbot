# Local dialog reply templates rollout

This pilot is limited to the single UUID-configured BeinSystems workspace through the shared `is_operator_workspace` predicate. The API rejects every other workspace and the client hides its control there. Within BeinSystems, existing tenant, department, membership, and permission checks remain in force. It has no Operator calls and does not write template text or drafts into message history.

## Preflight

The provisioning and UI switches are independent and both default off. Stock boot neither probes nor creates the template schema. API and request paths never provision it. The pilot API remains disabled unless `DIALOG_REPLY_TEMPLATES_ENABLED=true`, the exact configured BeinSystems workspace matches, and the schema is present.

**Proposed release sequence, not an approved live change:**

1. Obtain separate owner approval and verify the current recoverable backup and restore procedure before any production provisioning. A historic restore test is not current recovery evidence. Do not turn on backups, export data, or run a live restore as part of this change.
2. Release the code with both switches off. After approval and recovery verification, leave the UI switch off and enable `DIALOG_REPLY_TEMPLATES_PROVISION_ON_STARTUP=true` only for the controlled startup. The configured `BEINSYSTEMS_OPERATOR_TENANT_ID` must be a valid UUID. This setting provisions shared, empty schema, never a tenant's data.
3. The dedicated startup connection waits on the existing startup advisory lock. After the normal base schema commits, one transaction with the existing 5-second transaction and 2-second lock limits creates and verifies only the reviewed template table and partial scope index. An error or incompatible existing shape rolls that transaction back without undoing the already committed base schema. Do not repair or replace an unexpected table or index in this rollout.
4. Verify the worker booted and the table/index match. Turn provisioning off again; subsequent boots do not need it. Keep the UI off until the pilot activation is separately approved, then enable only the feature switch for the existing exact-workspace gate.

Rollback: disable the feature and provisioning switches. Retain the additive table and index; do not drop or rewrite them. No membership, CRM, chat, message, or template rows are created or changed by provisioning.

### Read-only schema observation

If no approved catalogue client is available, a separately approved Worker
rollout can set `DIALOG_REPLY_TEMPLATES_OBSERVE_ON_STARTUP=true` once with
both template UI and provisioning off. Under the existing bounded startup
lock it explicitly opens a read-only transaction. The startup log message
`Reply template schema observation` reports only `absent`, `matching`, or
`unverified` (or `workspace_not_configured`). Matching uses the canonical
schema verifier. No template rows or customer content are read, and no new
schema writes occur within this observation. The normal Worker startup still
runs its existing unrelated setup. Remove the observation switch afterwards.
This does not validate recovery or approve live provisioning.

## Behavior

- Personal templates are visible to their author.
- Team templates are visible inside their department and manageable by members with the existing department update scope.
- Shared templates are visible to dialogue viewers; creation and editing require the existing all-scope update permission.
- Template view and update scope currently derive from the existing `dialogs` view/update policy. There are no separate configurable template permissions. Whether to introduce distinct template capabilities is an open policy decision; it requires a separately reviewed rights model before any permission changes.
- A team edit that keeps `team` visibility retains the row's original department, including for an all-scope editor with no department. Only the author can change visibility; transitions to `team` use the editor's current department and require one.
- An idempotent create retry rechecks current view access before returning an existing row, so a former department member cannot retrieve old team text through the replay path.
- Template author/editor and department foreign keys use the database's default restrictive delete behavior. The supported member and department APIs archive/deactivate records; source review found no hard-delete flow. This intentionally prevents identity/department removal from silently deleting or orphaning template content.
- The API rechecks active tenant membership and current dialog permissions on each request.
- Typing filters already loaded tenant-scoped template text in memory. It does not send the draft to the server.
- Selecting a suggestion only inserts text into the existing editable composer. Sending remains an explicit action in the existing composer.
- The module uses only the local same-origin template API. No template or unsent draft is synchronized to Operator.
- There is no localStorage persistence, automatic send, or template export.

## Tests

Synthetic tests cover scope isolation, tenant binding, stale versions, idempotent retries, department-preserving edits and transitions, route wiring, and that typing/insertion/management do not call a message endpoint or store drafts locally.

The disposable PostgreSQL CI tests cover repeated migration, preserved synthetic data, the startup lock and commit boundary, stock boot without template probes, and rollback on incompatible schema or error. The profile capability is false unless the server opt-in is true, the configured pilot tenant matches, and the schema exists; the browser does not mount or call template endpoints when false.
