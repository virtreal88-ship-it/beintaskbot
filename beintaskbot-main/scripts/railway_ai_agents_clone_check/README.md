# Railway one-off PostgreSQL clone check

This one-shot service creates a compressed logical dump through a Railway private
network connection, restores it into a separate PostgreSQL database on its
own persistent volume, and runs `tests/postgres/verify_ai_agents.py` only against
that loopback clone. It does not start `bot.py`, a Telegram poller, an AI Agent
worker, a webhook, or any send/CRM integration.

## Railway service configuration

- Project: `powerful-solace`, current production environment.
- Git branch: `staging-ai-agents`.
- Root directory: `/beintaskbot-main`.
- Builder: Dockerfile at `/scripts/railway_ai_agents_clone_check/Dockerfile`.
- Start command: leave the Dockerfile entrypoint unchanged.
- Restart policy: Never.
- Volume: one 1 GB volume mounted at `/data`.
- Do not link the production worker's variables or shared variables. In particular,
  do not add `DATABASE_URL`, `PGPASSWORD`, `POSTGRES_PASSWORD`, AI, Kommo, or
  Telegram credentials.
- The only database variable this runner accepts is
  `SOURCE_READONLY_DATABASE_URL`, containing the dedicated read-only login.

The runner refuses to start a database check if it sees `DATABASE_URL` or
administrative PostgreSQL environment variables. It requires the source hostname
to end in `.railway.internal`, checks that the login is non-privileged and has
only the `pg_read_all_data` membership, confirms no object write grants, and
requires the role default `default_transaction_read_only=on`. The dump client
and clone use the same supported PostgreSQL major version (14 through 18).
Source-side work is limited to those checks, size/version reads, and `pg_dump`
with read-only session options. The source connection string is never passed to
the migration harness.

Before writing the dump, the runner checks that the source DB is at most 180 MiB
and that the dump, restored data, and reserved space fit within the 1 GB volume.
If either limit is exceeded, it stops without increasing resources. The dump and
restored database stay under `/data` after the process finishes; do not delete the
volume or files until the owner confirms the backup is no longer needed.

The harness accepts only `AI_AGENTS_TEST_DATABASE_URL` on `127.0.0.1` and a
database name beginning `ai_agents_test_`. The runner gives the harness a clean
environment containing only this local URL and a generated test encryption key.
The production URL is not passed to it, and migration code cannot resolve the
source database through `DATABASE_URL`.

## Preparing a read-only source login

The Railway Postgres service currently exposes its standard owner credentials;
do not copy those credentials into the runner. Use a separately created login
with the built-in `pg_read_all_data` role, no elevated role attributes, no other
role memberships, no object ownership or write privileges, and a role default of
`default_transaction_read_only=on`. The runner verifies these properties before
calling `pg_dump`.

Set the connection string only as the runner's Railway variable
`SOURCE_READONLY_DATABASE_URL`, using the Postgres service's private hostname and
port. Do not put credentials in source code, GitHub, shell command arguments, or
logs. If no suitable read-only account exists, stop and have the database owner
provision one; the runner must not receive the standard owner URL.

## Output

Only status lines, archive size/hash, PostgreSQL major version, and PASS/FAIL
labels are printed. Database rows, SQL errors, connection strings, and tool
diagnostics are suppressed. On success, the runner stays idle so the Railway
service can be stopped manually while preserving the `/data` volume and dump.

