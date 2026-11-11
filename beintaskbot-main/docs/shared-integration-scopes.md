# Shared integration scopes

The existing `TENANT_SCOPED_ACCESS_ENABLED` rollout switch remains off by default.
When enabled, `integrations` exposes independent view/create/update/delete scopes.
The responsible person is the current active company owner; department access
compares current actor/owner departments. Cached session role and client resource
identities never grant access.

All active members can see an allowlisted connection-state projection in the
profile response. Management settings use provider-specific guarded endpoints.
Bootstrap never includes arbitrary metadata, account domains, keys or tokens.
`management_actions` describes available controls, not authority for a later call.
Mutations re-read current DB policies/membership, serialize provider changes and
hold authorization fences across provider I/O and storage. Nested admission is
bound to the same tenant, actor, provider and action; schema startup runs before
row fences to avoid nested DDL lock upgrades.

Writes require expected tenant/user identity. Disconnects and replacement keys
require explicit company-impact confirmation. Chat AI key-only writes preserve
unknown settings and key deletion cannot carry an unrelated settings update.
OAuth setup, pipeline/workflow authority, Linear publication/member/runtime rules,
Chat AI photo/video controls and Connect's stronger administrative recovery
operations retain their stricter existing restrictions. Scoped Kommo connection
management uses the existing manual-token flow; official OAuth remains owner-only.
Provider constraints (including official Kommo precedence and per-channel Connect
disconnection) remain unchanged.

Validation: isolated unit/API tests in `test_shared_integration_scopes.py` and
existing provider suites. `test_shared_integration_scopes_postgres.py` requires a
disposable localhost `BEINTASKBOT_TEST_POSTGRES_DSN` and tests real storage and
concurrent policy revocation. No production rights, provider writes or rollout
settings are changed by these tests.

Follow-up authorization checks: BEIN creation with primary selection also needs
update scope, because it rewrites the other CRM's primary selection. Connect
setup with retained channel rows needs update even if its connection row is
missing. Nested reconnect classifies the action on the existing fenced cursor,
then validates the same principal, provider, context and company-impact consent.
Mutation-only callers receive an allowlisted receipt, never prior settings,
models, employee bindings or other channel metadata. The legacy Connect status
read remains available to current active members when the view cell is absent;
explicit scopes still apply, and this exception grants no management access.
