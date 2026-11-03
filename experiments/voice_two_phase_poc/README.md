# Synthetic two-phase source admission experiment

This is a test-only experiment built on `7018c5d`. It changes no application
or shared production table, installs no production trigger, opens no provider
and incurs no API charge. The model's `FLEET_VERIFIED` is `False` in code and
is patched only in a test's process. It does not attest to any deployed fleet.
The application's immutable live gates remain closed.

## Reproduce

Use a fresh disposable PostgreSQL on localhost containing only synthetic
data. Set `VOICE_AUTH_PG_URL` to its URL; the database name must be
`test_voice_auth_` plus exactly 32 lowercase hexadecimal digits. The inherited
fixture validates the URL, rejects `PG*` environment variables, creates a
random schema and drops it after the test. Install the project dependencies and
run from the repository root:

```sh
PYTHONPATH=beintaskbot-main:experiments/copilot_realtime_adapter:experiments/voice_linearization_poc:experiments/voice_two_phase_poc \
  python -m unittest -q test_two_phase.TwoPhase
```

## Model

1. Prepare reads real authorization, a sample message, a tenant-wide epoch and
   a tenant/channel/actor epoch in one repeatable-read transaction. It builds
   a **synthetic** server-owned JSON payload, stores SHA-256, full operation
   fingerprint, epochs and quote terms in an immutable prepared row, and
   returns immutable bytes. This is not the complete real Copilot projection.
2. Admission snapshots the supplied bytes, uses the actual (test-gated)
   budget reserve/claim, locks and compares both epochs, checks the prepared
   scope and hash, locks and checks the exact quote/version/model/amount and
   its expiry using database wall time, and inserts one intent in the same
   short transaction. It returns the same bytes only after commit. No lock is
   intentionally held through network I/O; the test never does network I/O.
3. A new scoped clear inserts a request-id event, deletes the thread and bumps
   the scoped epoch in one transaction, even when the delete affects no row.
   Repeating the same event ID for the same scope is idempotent. A different
   scope using the same ID is refused in this model.

The 31 tests include inherited tests for authority, earlier conditional budget
and trigger counterexamples, plus these two-phase tests. They verify mismatched
or mutable payload handling, one concurrent admission, clear with zero deleted
rows, legacy clear bypass, source and permission changes, ABA, quote expiry
during lock waiting, and a clear waiting until after a committed admission.
Only the scoped event/budget/epoch model is tested. The hashes and immutable
rows are local synthetic data; the protocol is not connected to an actual
provider, authorized key, browser, fleet manager, or current chat writer.

## Remaining counterexamples and release blockers

* A Boolean claiming old writers have gone does not fence a legacy zero-row
  DELETE; the test intentionally patches that Boolean and demonstrates the
  stale admission. Both old text and clear writers, all replicas, queued work,
  rollback/redeploy and every indirect path need independently enforceable
  cutover evidence. Until then the actual live gates must remain closed.
* The source inventory is incomplete. The baseline documents omitted identity
  and statement-level operations such as TRUNCATE. The actual context and
  knowledge readers use multiple connections, unlike the synthetic single
  snapshot. A complete source/permission/agent/identity/provenance inventory
  and transactionally consistent projection are unproved.
* A local hash and returned bytes do not force any future provider adapter to
  send exactly those bytes. The first context upload and each billable response
  require their own scoped, one-use, crash-safe dispatch binding. Expired,
  unknown and repeated external operations need explicit reconciliation.
* Expiry is checked after waiting for locks, but the test deliberately pauses
  after that check and commits after expiry. If quote validity is defined at
  commit, this model does not meet it. A stricter deadline contract must name
  its exact validation/commit/send point and handle arbitrary pauses and network
  uncertainty; a last-second wall-clock check alone is insufficient.
* Existing source writers may hold an epoch lock before requesting the budget
  lock, opposite the simulated budget-first admission. The inherited local test
  produces a PostgreSQL deadlock with that ordering. It is a conditional
  counterexample, not proof that a particular production writer does so.
  Tenant-wide epoch updates would also make otherwise unrelated chats and AI
  work contend and invalidate one another; actor/channel clears can affect
  other conversations under that scope. No user-visible impact or safe rollout
  has been measured. Do not change existing AI budget limits or locking.
* At most the model can establish correctness at its defined authorization
  point, conditional on all writers participating. A change after the point
  can close future admissions but cannot unsend context, audio or bytes already
  in flight, and the prototype proves no external provider cancellation.

The above are reasons to keep the existing feature disabled, not a migration or
an instruction to activate it.
