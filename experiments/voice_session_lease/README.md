# Distributed voice lease — gated local candidate

This checkout is based on realtime transport commit `e585e23`. Nothing here is
wired to a route or production, no migration is supplied for production, and
`LEASE_ENFORCEMENT_VERIFIED` is hard-coded false. `_release` always denies,
including for tests: there is **no recovery or release implementation**. The
module exists to review the state model and run tests using disposable local
PostgreSQL. Do not enable it as a feature.

## Scope and lifetime

The synthetic table has one row per `(tenant, actor)`, across tabs, processes,
devices and dialogs; channel, conversation, server-generated session UUID and
hash of a random server-only fence bind the holder. This is deliberately stricter
than one session per route. It does not block other employees, other tenants or
text AI. A duplicate or uncertain claim cannot return an existing capability.
A DB transaction commits before `claim` returns its token.

`assert_current` performs the existing Copilot authority checks, compares the
full server-derived VoiceScope (including agent binding and configuration), and
renews a live matching heartbeat with database wall clock. A *detected*
authorization failure attempts to commit quarantine before returning an error;
if committed, subsequent regrant cannot restart that session. Any error,
unknown commit or cancellation makes the individual adapter terminal in memory,
preventing it from retrying the old fence. An observed expiry is committed as
quarantine before returning denial, so a later wall-clock reversal cannot revive
that committed state. An expiry does **not** delete or release the row. Neither timeout,
crashed process, cancelled Python task, disconnect, local websocket closure nor
a user-supplied statement is evidence that upstream compute or billing stopped.
Consequently a lost session may block this employee's voice mode indefinitely.
This is a safety limitation, not an acceptable finished UX.

Local terminal state is not durable or distributed revocation. If quarantine
rolls back, the record can remain active; a newly constructed adapter handed
the original fence can renew it after access returns. A synthetic test explicitly
reproduces this blocker. A gateway must end/fence on **any** error and never
transfer or retry that capability, including across workers and restart. A
future design needs durable independent fencing when the quarantine commit
fails or is unknown, before enabling this feature.

There is also a time-source limitation: an expiry which has not yet been
observed and committed can appear unexpired if the database wall clock moves
backward. This experiment has no durable monotonic-time service or independent
process fence and cannot rule that out. The clock test modifies only the
synthetic deadline to simulate the ordering after a backwards step; it does
not change a host clock or establish fleet clock guarantees.

The heartbeat is only a liveness hint: no DB write can stop an old worker in
another process from directly using already acquired network resources. Each
media/event/send must recheck a server-owned fence, but even that is not an
atomic network fence; previously authorized in-flight I/O can complete after
revocation. Membership re-enabled before the next poll may not be observed at
all. The actual authority checks and this lease do not provide the missing
source-version/revocation linearization proof, remote cancellation or provider
billing limit. Release must account for those independently.

The current prototype gateway invokes `assert_current` on input and output
events, and this adapter opens a DB connection and performs authorization and
an UPDATE each time. At 1,024 input samples per 24 kHz frame this is roughly
23.4 DB checks per second per active session before output events. This is a
calculation from the capture frame size, not a production load measurement;
buffering can alter the rate. It may harm existing AI and other workloads. No
performance or capacity guarantee is claimed. Before integration, separately
design and test bounded/coalesced heartbeats **and** an independently reviewed
revocation fence with a specified maximum delay; simple caching would weaken
revocation and has not been implemented. Measure under realistic concurrency
against representative DB pools and verify behavior on loss of authority.

`ServerLease` matches the prototype async `claim(scope)`,
`assert_current(scope, fence)`, `release(scope, fence)` interface. The test-only
factory `create_server_lease(authority, session_id)` requires an authority from
trusted server authentication; its provenance cannot be validated merely by
Python type. No browser may supply the profile, scope, transport, fence, session
owner or evidence. DB connections used for these operations must target one
shared authoritative database; pools, missing/stale reads, multiple DBs,
privilege bypass and uncertain commits require a separate review. Cancelling an
async claim may leave its background DB thread committing; the row remains and
retry fails closed. No auto-resume or unexamined repair is permitted.

## Required recovery contract — not implemented

Before any release can be introduced, design and independently verify all of:

1. **Detect and explain.** After a bounded heartbeat/connection failure, present
   the affected employee with a clear voice-only state such as: “Не удалось
   подтвердить завершение предыдущего голосового сеанса. Текстовый помощник
   доступен; повторите попытку позже или обратитесь в поддержку.” Do not expose
   provider IDs, tokens, customer content or an unsafe force-unlock control.
2. **Bound reconciliation work, not safety.** A server-owned reconciler with a
   fixed retry/deadline budget should identify the exact tenant, actor, route,
   session, provider instance and lease epoch; stop or fence every process and
   in-flight network operation and request upstream termination. A proposed
   operational target is to surface pending status promptly and finish one
   automated attempt within five minutes; it is not a promise that proof will
   be available in that time. On deadline, stop retrying automatically, retain
   the lease and create a restricted support/admin investigation item.
3. **Prove terminal state and cost.** An authoritative provider-specific
   mechanism must establish that the exact session cannot resume, send output or
   generate billable work, including reconnects and delayed tasks; a local close
   acknowledgement or elapsed timer alone is insufficient. Reconcile usage from
   trusted provider records, or retain a proven conservative reservation for
   all uncertain cost. Session and shared-budget evidence must be durable and
   bound to the exact lease generation. If the provider cannot prove termination
   or a total upper cost, recovery remains pending.
4. **Release atomically.** A separately authorized server workflow must lock
   the same lease generation, revalidate the evidence and budget hold, prevent
   old processes/tokens from starting I/O, write an auditable terminal record,
   and only then allow a new claim. The administrative path may investigate and
   retry proof; it must not bypass missing provider evidence. Handle unknown
   outcomes, concurrent recovery, rollback, permissions, retention and replay.
   Do not treat a fixture toggle, mutable client JSON or standalone Boolean as
   proof. No such verifier, audit schema or release operation exists here.

An expiry/revocation must not change roles or disable the existing text
assistant. A safe operational escape is intentionally not implemented while its
preconditions cannot be demonstrated. Scope/product policy, customer-facing
message and operational deadline require product review before activation.

## Verification scope

The test schema is `synthetic_schema.sql`, applied only to a random schema in a
disposable localhost test DB. Tests cover simultaneous contenders, scope and
fence binding, heartbeat/expiry, cancellation of a background claim, observed
revocation and regrant, actor/tenant isolation, and permanent release denial.
Real authorization functions use synthetic rows. The tests do not establish
provider state, a production migration, cross-deployment identity, billing,
end-to-end revocation, actual browser control or recovery. In particular, no
real requests, voice, customer contact, tokens, provider API or paid calls are
used. No automatic unlock claim is made.

With the repository's Python dependencies and a disposable localhost database
named `test_voice_auth_` plus 32 lowercase hex digits:

```sh
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=beintaskbot-main:experiments/copilot_realtime_adapter:experiments/voice_session_lease \
python -m unittest -v test_lease_pg.LeasePostgres
```

Set `VOICE_AUTH_PG_URL` to that database. The inherited fixture rejects remote
hosts, unexpected libpq environment variables and unsafe database names, then
creates and drops a random schema. The class includes inherited authorization
tests in addition to the lease-specific tests.
