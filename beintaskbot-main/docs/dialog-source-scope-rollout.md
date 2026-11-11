# Dialog source permissions: safe rollout

This is an implementation and test note, not approval to change live users,
connections, channel assignments or to send customer messages.

## Implemented

- `dialogs.view` controls list, history, read markers, previews and attachments.
- `dialogs.update` independently controls existing replies, customer voice and
  the existing Kommo file-send path. Display-name edits also require update.
- Self refers to explicitly bound employees of a source. Department compares
  current active memberships, including a reader with no personal source.
  All remains restricted to the current company. Multiple employees can share
  one Connect source.
- Source bindings are additive metadata in `saas_dialog_source_members`, never
  inferred from a phone, name, deal owner or WhatsApp participant. The owner
  edits them from the employee card, with source revision and tenant/user
  fencing. Other employees on the source are retained by the card editor.
- Unassigned existing sources appear in the preflight. Narrow scopes cannot
  be activated while existing sources have unresolved assignments. No source
  mappings are created by the schema migration.
- The reply route is resolved from the persisted source and rechecked at the
  provider boundary. Membership, permission, source and assigned-employee
  locks remain held through a scoped send. Disabling a Connect route never
  causes fallback to Kommo. Text replies record the employee display name and
  verified employee ID; existing voice receipts already record their author.
- Shared group history, source-copy metadata and anchor lookups are filtered
  by authorized source. Notification list/count/read and notification
  enqueue/claim/provider dispatch use source checks. Current grant locks span
  a bounded delivery attempt, including first activation after claim. Case consultations retain independent
  support permissions and their existing owner-only send restriction.
- Dialog access does not itself grant AI tools or configuration. Scoped AI
  state control requires both the independent AI grant and dialog source
  scope. Business Analyst endpoints remain owner-only.

## Deliberate limits and activation requirements

1. `TENANT_SCOPED_ACCESS_ENABLED` remains an operator-controlled rollout gate.
   Shipping additive schema/code does not convert existing permissions.
2. Kommo's cached channel labels do not reliably identify a number/source.
   Self/Department Kommo access is blocked until source identities and their
   mapping have been agreed. All/None remain meaningful explicit choices.
3. No create/delete dialog API currently exists. No fictional C/D operations
   or silent conversation-claiming workflow were added.
4. Connect file sending was not implemented previously. It now returns a clear
   unsupported-route error before any Kommo lookup/upload instead of attempting
   another provider. Existing Kommo files and Connect text/voice retain their
   established transport limits; adding a new media transport is separate work.
5. Tenant-wide AI diagnostics that cannot prove source scope must remain
   closed for narrow dialog readers until a reviewed scoped projection exists.
6. Source mutations, group memberships and owner review must be checked with
   the current production inventory before enabling narrow grants. No live
   data, connection, history import or outbound provider operation was performed.

## Verification

`tests/test_dialog_source_scopes.py` exercises synthetic companies, departments,
shared/unassigned sources, no personal source, independent R/U, department and
membership changes, source injection, send revocation/rebinding, notification
scoping, AI-grant separation and optimistic source edits using PGlite.
Mixed-channel cache rows are filtered before pagination/limits. Connect lead
identity is globally unique; shared groups have separate routes per source.
AI reserve and finish fence the authorized source expression, so first scope
activation during generation prevents returning or saving the old result.
`tests/test_dialog_source_members_ui.cjs` covers identity/revision submission,
retaining existing employees, repeated clicks and stale card responses.
Existing chat/voice/attachment/operator/notification/analytics route tests were
run with mocked providers. Seven two-connection PostgreSQL tests in
`tests/test_dialog_source_locks_postgres.py` were run on a disposable local
PostgreSQL 17 instance. They verify first activation before dispatch, scope/
department/binding/deactivation waiting during simulated sending, lock release
after a bounded synthetic timeout, and notification claim/dispatch races.
No real provider or paid model was called. The test cluster was stopped.
