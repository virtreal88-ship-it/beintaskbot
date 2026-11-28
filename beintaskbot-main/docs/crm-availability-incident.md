# CRM availability, 28 November 2026

All times below are UTC+4. The Worker and PostgreSQL logs and Railway deployment records were compared for both windows.

* At 12:50:58 PostgreSQL timed out a shared row lock on `saas_tenants`. At 12:52:09 three other sessions timed out acquiring transaction advisory locks. The HTTP witness at 12:52:40 and repeatedly thereafter found the aiohttp listener thread inside psycopg, executing line 15 of `read_access_profile` from the avatar handler. The call requests a shared lock on the tenant row. Railway's proxy recorded 499 and 502 responses. At 13:44:01–13:46:25 the witness recorded the same blocked stack on another deployment. These snapshots identify the waiter. Neither window contains a historical waiter-to-holder edge that establishes the initial blocking transaction.
* During the subsequent 13:02 startup, role-constraint DDL timed out on the membership table. At 13:03:56 PostgreSQL explicitly named PIDs 3989607 and 3989596 in a deadlock between a CRM contact identity read/lock and creation of the message-list index. These are startup conflicts, after the first freeze. A widget provider 401 has its own failure path.
* The public listener runs one asyncio loop. A synchronous database wait on that thread prevents unrelated handlers from starting, even when the container, TCP listener and PostgreSQL are alive.

| Priority | Path | Protection and remaining limit |
| --- | --- | --- |
| P0 | Avatar and its authorization | PR 1022 isolates protected database work. Avatar session lookup and downloads also have independent admission. A read cannot start schema DDL. The normal initials remain visible on failure. |
| P0 | CRM login, session bootstrap, operator list and history | Separate finite session/read pools and connection, SQL and caller deadlines. No cached authorization or early acknowledgement. Some other legacy routes still call synchronous session or database helpers on the listener. |
| P1 | Connect provider and signed webhook | Connect settings and ingestion use separate bounded pools. Under pressure a retryable error replaces accumulation. A running transaction keeps its slot until it finishes. Provider events retain their existing durable deduplication. Native WhatsApp already admits two operations and acknowledges persisted receipts. |
| P1 | Background Kommo CRM sync | One batch per process, bounded provider reads and SQL waits. The existing session lock and atomic page/cursor commit retain their restart semantics. Other background jobs still share resources. |
| P1 | Migration overlap | Startup retains the existing advisory lock, 2 s lock wait, 5 s transaction limit and bounded retries. A named session identifies startup as a blocker in future samples. Heavy DDL and sustained database unavailability can still prevent readiness. |

## Detection

The independent Worker witness samples the local HTTP listener and PostgreSQL lock graph every ~15 seconds. Search Railway Worker logs for `worker_http_witness`, `worker_db_lock_wait` and `worker_db_lock_sample_unavailable`. Up to 12 edges identify waiting and blocking backend PIDs, transaction ID when assigned, age, state and an allowlisted operation class. No query text, arguments, tenant identifiers or credentials are emitted. Short waits between samples, prepared transactions and disconnected backends may be missed.

The external GitHub Actions workflow **CRM production availability** checks the public shell, `/readyz` and the unauthenticated session boundary twice before failing. Scheduled about every five minutes, subject to Actions delays. Failures appear in Actions and follow the recipient’s GitHub Actions notification preferences. No dedicated paging contact is configured by this change. The DB check acquires read locks on the CRM tables without returning rows. It does not log in as a customer or guarantee that every tenant row is unlocked. `/health` remains the separate lightweight listener check.
