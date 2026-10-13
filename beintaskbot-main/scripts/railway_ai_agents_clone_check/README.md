# Railway one-off PostgreSQL clone check

This one-shot service creates a logical dump through Railway private networking,
restores it into a separate PostgreSQL database on its own persistent volume,
and runs `tests/postgres/verify_ai_agents.py` only against that loopback clone.
It does not start `bot.py`, a Telegram poller, an AI Agent worker, a webhook, or
any send/CRM integration.

## Railway service configuration

- Project: `powerful-solace`, current production environment.
- Git branch: `staging-ai-agents`.
- Root directory: `/beintaskbot-main`.
- Builder: Dockerfile at `/scripts/railway_ai_agents_clone_check/Dockerfile`.
- Start command: leave the Dockerfile entrypoint unchanged.
- Restart policy: Never.
- Existing volume: 5 GB mounted at `/data`; this cap is authorized and must not
  be increased. The runner checks that `/data` is mounted and that its capacity
  is at most 5 GiB before it connects to the source database.
- No public domain. Keep auto-deploy disabled and do not link production worker
  variables or shared variables.
- Do not add `DATABASE_URL`, owner credentials, AI, Kommo, or Telegram settings.
- The only accepted database variable is `SOURCE_READONLY_DATABASE_URL` for the
  dedicated backup login.

The runner rejects administrative PostgreSQL variables, requires a private
`.railway.internal` source, and verifies the login has no elevated attributes,
role memberships, ownership, persistent writes, or object creation rights. It
checks read access to every data-bearing table, sequence, and large object needed
for a complete dump, and rejects row-level security, foreign tables, executable
security-definer functions, and access to user tables in another database.

The database's existing `TEMPORARY` grant to `PUBLIC` is permitted. The role must
not receive a direct TEMP grant. The runner also requires
`default_transaction_read_only=on`; it does not change the global `PUBLIC` ACL.
Dump and restore clients match the source PostgreSQL major version (14 through
18). Source-side work is limited to read-only catalog/size checks and `pg_dump`
with read-only session options. The source URL is never passed to the migration
harness.

Before creating the dump, the runner checks that the source database is at most
180 MiB and that the dump, restored copy, and reserved space fit within the
mounted 5 GiB volume and the conservative 900 MiB working-set estimate. The
archive retains ownership and ACL metadata; restore applies the data as the local
clone owner. If any limit is exceeded, the runner stops without raising resource
limits. Keep the dump and restored database on `/data` until the owner confirms
they can be deleted.

The harness accepts only `AI_AGENTS_TEST_DATABASE_URL` on `127.0.0.1` and a
database name beginning `ai_agents_test_`. It receives a clean environment with
only that local URL and a generated test encryption key. The source URL is not
passed to it, and migration code cannot resolve production through
`DATABASE_URL`.

## Source backup login

The Railway Postgres owner credentials must never be copied to the runner. Use
the existing `ai_agents_backup_ro` role with `LOGIN`, no elevated role attributes
or memberships, no ownership or write privileges, `USAGE` on the needed schema,
and `SELECT` on the database objects needed by `pg_dump`. Do not grant
`pg_read_all_data`. Set `default_transaction_read_only=on` for the role. The
existing database-level TEMP grant inherited from `PUBLIC` is allowed; do not
change the PUBLIC ACL or add a direct TEMP grant.

Store only the dedicated read-only connection string in the runner's
`SOURCE_READONLY_DATABASE_URL` variable, using the private Postgres hostname and
port. Do not put credentials in source code, GitHub, shell arguments, or logs.

## Output and stop behavior

The runner prints only stage labels, archive size/hash, PostgreSQL major, and
PASS/FAIL lines. It suppresses database rows and utility diagnostics. After a
successful run it stays idle so the Railway service can be stopped while the
dump and volume remain available.
