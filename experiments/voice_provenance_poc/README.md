# Offline voice provenance and bounded migration experiment

**Do not deploy or use for paid calls.** No real server endpoint imports this
directory. `ENFORCEMENT_VERIFIED` and `RECONCILIATION_VERIFIED` remain false;
the database grant defaults disabled. The user-approved $5 test budget is not
enforced outside the disposable database. Provider limits and billing bounds,
authorization, client isolation, all-executor connectivity and shutdown remain
unproven.

## New operations: pinned ownership

`provenance.sql` is a test-only proposal. On insertion of a *new* voice hold,
an AFTER INSERT trigger checks the already-reserved tenant record and pins
tenant, request, reservation ID, call key, kind, model, quote, initial amount
and scope fingerprint in an append-only witness, in the same transaction.
Subsequent row triggers reject deletion, owner/identity changes, hold reduction,
incorrect state transitions and fake tenant actual values for that bound
record. Unknown holds may only grow. Unrelated tenant-budget rows are passed
through; local tests check generic release on an unrelated AI request.

The pre-claim cancellation protocol changes the exact voice request to
`released` and the tenant hold to released with zero actual in the same
transaction. A generic tenant release alone is rejected. Claim and cancellation
race under the existing grant and tenant locks. Once claimed, the tenant hold
is `unknown` and is not released by generic settlement. Actual voice usage stays
in the voice ledger; the tenant hold grows monotonically for an overrun.
`pinned_dispatch.issue` is only a synthetic integration seam: it checks the
witness and calls the candidate durable `claimed -> dispatching` CAS in the
same transaction. It must commit before any hypothetical network send. It does
not authorize a user, return a browser capability, make a network call, or
prevent another route from bypassing it.

This only establishes new-operation invariants **assuming** the DB role cannot
disable/bypass triggers, truncate or rewrite tables/functions and every caller
uses the intended authenticated transaction. None of these privileges or
production boundaries has been verified. A privileged local test deliberately
disables a trigger to verify that mismatched witness is rejected at the seam;
it is not a production attack demonstration. Another negative test shows a
database owner can issue raw `claimed -> dispatching` SQL while the grant is
disabled: provenance triggers are **not** a DB-enforced kill switch. A trusted
server path and restricted DB privileges remain mandatory. DB constraints, mandatory
server-side credentials, other worker versions and side effects require
independent security and operational review. Conditional triggers inspect the
shared tenant reservation table only when OLD or NEW kind is voice for UPDATE,
or OLD kind is voice for DELETE; nonvoice -> voice forgery is rejected. A local
test-only role without witness privileges can still update/delete an unrelated
row, while its voice update fails closed. These synthetic grants are not
representative of production roles. In one local 5,000-row no-op UPDATE
microbenchmark (7 measured samples), median was 83.26ms without and 87.07ms
with conditional triggers (ratio 1.046). This is not zero overhead, a production
benchmark, SLA or proof across all operations. Do not deploy or activate it.

## Legacy conflicts and migration

The one-shot provenance installer requires the tested dispatch schema v2,
disabled voice grant and **empty** voice hold history under a bounded lock. It
never backfills witnesses or infers ownership of an existing conflicting
record. If any legacy row exists, installation fails and rolls back. Historical
charges, unknown results and scope collisions need trusted external evidence and
an explicit non-destructive repair decision; without proof the voice feature
stays closed. A separate $5 authority must not silently reset or discard earlier
usage.

`migration_dispatch_v2.sql` and `rollback_dispatch_v1.sql` are candidates for
the existing hold state CHECK. The upgrade acquires a schema advisory lock and
bounded locks in schema -> grant table/row -> voice table order, recognizes only exact known v1/v2 CHECK definitions,
version-marks v2, can be reapplied, and does not rewrite existing rows. Unknown
schema/version fails closed. An installed persistent voice-grant trigger rejects
enabling, deletion or enabled insertion even after rollback; activating requires
a separate reviewed change. Concurrent enable is serialized by the grant table
lock. This is not proof that a privileged owner cannot bypass the trigger. The
rollback refuses enabled grants, unresolved
holds, or an installed provenance table; it does not delete entries. Schema
rollback does not establish that upstream provider sessions stopped or that
old workers understand new states. The provenance installer itself is
one-shot and has no reviewed data-preserving removal/rollback once witness
data exists. None is wired into the repo's migration runner.

The candidate lock order is global grant -> voice row -> tenant advisory lock.
The generic tenant-budget path takes the tenant lock only. A deterministic
local AB/BA test intentionally reverses this order and sees one transaction
aborted; this is evidence of a possible deadlock, **not** a fix for every
external writer. Source/revision writers, migration table locks, job workers,
provider network I/O, and rolling versions need a single audited order and
coordinated cutover. The installer has `lock_timeout` but a deadlock victim
could still be another AI transaction. Quiescing and rollout procedures must
be reviewed; no production migration has run.

## Local validation

Use a disposable localhost-only PostgreSQL server. The shared guard accepts
only random `test_voice_budget_<uuid>` databases. One test creates a separate
random database for a legacy failure and deletes it afterward. No production
contacts, provider credentials or audio are used.

```sh
VOICE_BUDGET_POC_ADMIN_URL='postgresql://testuser:testpass@127.0.0.1:PORT/postgres' \
  PYTHONPATH=beintaskbot-main:experiments/voice_budget_poc \
  /workspace/.venvs/beintaskbot311/bin/python -m unittest discover \
  -s experiments/voice_provenance_poc -p 'test_*.py' -v
```

Tests cover atomic multi-connection provenance, changed fields/deletion,
unrelated release and limited-role access, cancellation vs claim, single-use dispatch, simulated
tampering, legacy refusal, migration reentrancy/rollback/unknown versions,
bounded locks, concurrent enable/installation and AB/BA behavior. Tests use database owner privileges for
fixture setup and therefore **do not** prove production role separation. The
old `voice_budget_poc` suite remains separately runnable.
