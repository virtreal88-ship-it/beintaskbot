# Dormant relationship delivery

## What is implemented

The relation store changes a single native FK and inserts its durable intent and
latest-head marker in the same transaction. It records the authenticated source
card and actor, not merely the changed FK owner. Delivery rechecks the source
card's update permission, active tenant/member, ownership/department scope,
active records, exact local revision/head, current sync policy, verified mapping,
account domain and connection generation. Tenant and row locks stay held through
conditional provider I/O. The tenant key lock also fences insertion of a newly
preferred integration while a manual connection is selected.

Identity rows are separate from legacy unbound IDs. Import/create evidence or an
explicit owner verification is required; reconnect never adopts old mappings
just because the same numeric provider ID exists. Token refresh preserves the
integration generation; a connection or account change invalidates it.

Kommo planning uses only POST /leads/{id}/link and /unlink (or the corresponding
contact/company endpoints), one target at a time. Replacing a main contact links
the new main before detaching only the previous main. Secondary contacts remain
in every planned state. Company replacement is explicitly two steps, not atomic.
All remote snapshots include the complete relevant ID set and main flags; partial,
paged, malformed or ambiguous snapshots fail closed. No record PATCH is used.

The driver commits a sending marker before I/O and keeps a session advisory lock
across commits. A duplicate worker cannot dispatch concurrently. Crash, timeout,
unknown result, or lost read-back causes no replay and no compensating unlink.
Even observing the desired state does not prove an earlier in-flight request has
finished. A later invocation cannot infer permission from a queued event.

## Deliberate activation blocker

There is no production adapter or scheduled caller. Existing scoped relation
requests continue to return 409 when outbound synchronization is configured.
The journal API is dormant; the migration only creates additive empty tables.
No identities are automatically backfilled and no live sync policy is changed.

A valid delivery adapter must compare the full expected remote relation graph and
apply the one narrow operation under an actual atomic/exclusive provider fence.
The test mock does this in memory. Kommo's published link/unlink API does not
supply a documented CAS/version precondition. Read/check/POST/read can detect
some concurrent changes but cannot prevent overwriting a main-contact edit that
arrives between the check and POST. Local PostgreSQL locks do not fence Kommo UI
users or other integrations. This is a missing integration contract, not a new
permanent restriction on the product or the user's approved relation semantics.

The smallest missing safety contract is provider-supported conditional relation
writes (or a verified exclusive-write mechanism covering *all* remote writers),
plus a way to establish that an uncertain prior request has finished before any
subsequent step. Without those guarantees, activating the current mock contract
as ordinary Kommo HTTP would be unsafe. Do not replace it with a boolean flag or
an in-process lock. Unlinking the only main while secondary contacts remain also
needs verified provider behavior regarding automatic main-contact reassignment.

BEIN has no independently verified narrow link/unlink contract here. It remains
unsupported; do not invent PUT/PATCH semantics or route relation intents through
the whole-record exporter. Contact-company activation additionally needs an
explicit supported sync-policy field, since the current policy UI does not offer
company_id for contact synchronization.

## Scoped scalar provenance follow-up

New scoped scalar mutations now have a separate atomic journal with actor,
revision and immutable changed-field masks. See `crm-scoped-scalar-sync.md` for
dispatch fences, narrow API receipts and the remaining activation blockers.
Existing legacy event contents are not rewritten. Unknown historical provenance,
in-flight legacy delivery, recovery UI and provider concurrency guarantees still
need a rollout resolution. This is not a claim that the scoped gate can be enabled.

## Verified primary references (2026-11-11)

- https://developers.kommo.com/reference/linking-entities : POST relation endpoint;
  response metadata main_contact.
- https://developers.kommo.com/reference/unlinking-entities : POST unlink endpoint.
- https://developers.kommo.com/reference/linked-entities : full links observation.
- https://raw.githubusercontent.com/amocrm/amocrm-api-php/master/src/AmoCRM/Models/ContactModel.php :
  getMetadataForLink serializes the request field is_main (not main_contact).

These references establish endpoint shapes, not a concurrency or idempotency
contract. No live provider requests were made during development.

## Verification

Pure contract/planner tests and isolated PostgreSQL/WASM tests exercise rollback,
source-only update authorization, stale/revoked access, reconnect/account changes,
mapping changes, local edits, secondary preservation, duplicate delivery,
timeout-after-apply and simulated process loss. A separate localhost PostgreSQL
suite exercises actual multi-session blocking and concurrent dispatch. It is
skipped without BEINTASKBOT_TEST_POSTGRES_DSN; WASM is not evidence of real
multi-session locking. Tests never use production databases or credentials.
