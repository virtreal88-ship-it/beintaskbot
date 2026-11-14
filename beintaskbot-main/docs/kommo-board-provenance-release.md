# Kommo board provenance release gate

The new table is **not approved for production migration by this implementation**.
Use the narrow helper `python scripts/prepare_kommo_board_provenance.py` for read-only preflight.
Only after owner release approval, run the same command with `--apply`.
It never invokes the broad platform schema migration.

## Additive schema

`saas_crm_board_provenance`: tenant UUID, resource (`leads`/`tasks`), external bigint ID,
account ID, connection generation, SHA-256 raw digest, opaque source revision,
observed timestamp. Composite primary key `(tenant_id,resource,external_id)` is the
only index. No alterations, deletes or backfill of existing CRM/permission rows.
No credentials, tokens or customer text are copied into this table.

Creation is inside the existing platform schema transaction/advisory startup lock.
CREATE TABLE/primary-key creation affect a new empty relation, not a scan/rewrite
of CRM snapshots. The narrow helper uses transaction-local lock_timeout=3s and statement_timeout=15s,
and a nonblocking pg_try_advisory_xact_lock on the existing tenant-platform-schema
key. It aborts immediately if another schema operation holds that lock. It validates
exact columns/types/nullability, primary key and required CHECK constraints before
reporting compatibility; an existing incompatible relation is never repaired silently.
The ordinary platform startup retains its existing broader locking behavior, which
is why the approved narrow preflight must create/verify this table first.
No HTTP board command runs DDL. A missing table fails closed with
`board_source_sync_required`; existing sync remains compatible without proof.

## Evidence and compatibility

Only a successful direct provider page read carries server-side ImportObservation
from credentials actually used. Sync verifies the current account/generation and
commits provenance atomically with snapshots. Old rows are not relabelled. Normal
fresh synchronization is required before new board writes can use older rows.
No real synchronization is initiated by this patch's tests.

The browser gets only an opaque source_revision matched against the exact returned
cached raw snapshot. POST requires the same revision. Provider response JSON
cannot mint an ImportObservation. Reconnect or an old in-flight page cannot
establish proof for the new account. Confirmed board writes refresh proof and
receipt in one DB transaction. Other unrelated cache writes may invalidate proof
until the next genuine sync, intentionally failing closed.

Rollback: deploy the previous application commit; leave the additive table and
its data in place. Do not drop/purge it. Older application code ignores it.

A no-schema alternative would require disabling Kommo writes until a new,
explicit live-preview/confirmation flow replaces cache identity and binds
browser intent to the live account. Merely using synced_at, stamping today's
connection onto cached rows, or trusting provider-supplied raw metadata is not
safe provenance.

Kommo has no verified conditional PATCH contract in this adapter. Expected scalar
checks detect divergence at preflight; external edits between GET and PATCH are
not prevented by our per-record app lock. Unknown writes are never replayed.
