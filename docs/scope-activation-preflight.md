# Read-only scope activation inventory and deployment drain

This tool does not enable the flag, modify grants, repair schema, register identities,
retry delivery, or call providers. It cannot certify a safe full rollout.

## Run the inventory

Use a separately authorized read-only DB identity. Supply its complete connection
through a private file (not a command-line credential and not DATABASE_URL).
Do not attach that file to reports. The DSN must explicitly contain host, dbname,
and user; service files and ambient PG variables are not used.

```
python beintaskbot-main/scripts/scope_activation_preflight.py --dsn-file /private/readonly-dsn --sample-limit 20 > preflight.json
code=$?
test "$code" -eq 2   # 2 means inventory produced, NOT_SAFE_TO_ENABLE
```

Exit 3 means connection/snapshot failure; missing table, column, permission or
query timeout yields an unknown check, never a zero. Exit 2 is deliberately never
a success-to-enable signal. JSON contains only code-owned labels, counts and salted
opaque sample tokens. It excludes names, message text, phone/email, external IDs,
account domains, credentials, DSN, raw database errors and workflow JSON. Samples
cannot be resolved across reports; use a separately authorized owner review to
resolve records. Counts span all tenants and are one repeatable-read snapshot.
No query repairs even an empty database. Use only a fresh dedicated connection.

Historical issue receipts and current NULL owners are separate observations:
receipts can survive resolution, while newly created system documents can be NULL
without being failed historical migrations. Missing departments/inactive members
are review inventory, not automatic defects. Identity-table presence does not prove
current account/generation validity. All mandatory evidence remains uncertified.

## Executable deployment/drain checklist

These are operator-executed steps, not permission for automatic production actions.
Use the deployment platform's authenticated service/process listing and release
records, not a local branch, guessed container tag or the frontend version.

1. Record the exact immutable candidate Git SHA and successful CI run IDs for it.
   Require real PostgreSQL concurrency tests, browser tests, and startup/migration
   checks. Record baseline schema and a verified recoverable backup.
2. Export the actual service inventory: web replicas, queue consumers, schedulers,
   one-off jobs and autoscaling templates. Record each process ID, image digest,
   deployed SHA, start time and rollout-flag value. A missing process/version or
   unknown flag is a STOP, not an assumption that the default is off.
3. Keep the global flag off. Deploy compatible code through the existing authorized
   platform workflow. Replace every old process and its restart/autoscale template.
   Query the platform again and compare *every* live process to the target digest
   and SHA. Do not proceed while old versions or unidentifiable workers remain.
4. Stop old-worker admission through the approved platform drain mechanism. Observe
   old process termination and record its request/queue completion evidence. Wait
   for tracked provider calls to finish. Lease expiry, empty queue, process death,
   or a later GET with the desired values is NOT proof an old remote call finished.
5. Run the read-only inventory. Reconcile processing/unknown/attempted legacy rows
   with durable sending/unknown and claim-aware not_started evidence. Never reset
   attempts, delete markers, or replay a possible send merely to make counts zero.
   For unknown calls obtain provider completion evidence and use a reviewed recovery
   workflow; if absent, STOP activation.
6. Review actual source assignments, owner issues, departments and before/after
   effective access with the owner. Verify account-generation mapping evidence,
   production adapter registration and provider guarantees separately. Do not
   infer ownership/mappings or change real rights as part of this inventory.
7. Confirm a durable status/recovery path for conflicts, unmapped identities,
   superseded values and mixed-writer quarantine. Record approval/evidence for
   every mandatory item. Missing adapter/CAS/unknown completion guarantee means
   STOP full-model activation even when all database counts are zero.
8. If a later separately authorized activation proceeds, record the exact flag
   change, process/version inventory and observation window. This document and
   tool neither perform nor authorize that action.

## Compatible rollback

Turning the flag off does not erase scoped provenance. Preserve scalar/relation
journals and heads, legacy delivery evidence and claim attempts, ownership/mapping
receipts, and additive schema/triggers. Never truncate/reset them or run a down
migration. The rollback image must retain actorless-export quarantine, same-card
admission and possible-send barriers, and AI archive-reference fences. Verify the
candidate rollback image against synthetic persisted post-scope data before use.
An older pre-fence binary is not compatible. Re-query actual live processes after
rollback and keep uncertain events blocked; flag-off alone does not prove safety.
