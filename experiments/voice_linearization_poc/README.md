# Synthetic revision and budget linearization experiment

This experiment uses disposable local PostgreSQL and the synthetic fixture on
base `8417b28fa408d6c43804a52c5acbe18fe1ae1566`. It changes no application,
provider, budget configuration, production database, or production gate.
The gate overrides are scoped to tests; no network provider exists here.

## Reproduction

Use a fresh local PostgreSQL database with no real data. Set
`VOICE_AUTH_PG_URL` to a localhost URL whose database name is `test_voice_auth_`
plus exactly 32 lowercase hexadecimal digits. The inherited fixture refuses
non-local URLs, URL query parameters and `PG*` environment variables, creates
one randomly named test schema and removes it. From the repository root with
the project dependencies installed:

```sh
PYTHONPATH=beintaskbot-main:experiments/copilot_realtime_adapter:experiments/voice_linearization_poc \
  python -m unittest -v \
  test_linearization.Linearization.test_mutations_invalidate_including_aba_and_roll_back_budget \
  test_linearization.Linearization.test_committed_one_shot_intent_and_budget \
  test_linearization.Linearization.test_competing_duplicate_dispatch_has_one_committed_intent \
  test_linearization.Linearization.test_wrong_snapshot_scope_refused_before_budget \
  test_linearization.Linearization.test_writer_started_before_L_cannot_commit_until_after_L \
  test_linearization.Linearization.test_legacy_source_then_budget_vs_dispatch_budget_then_epoch_deadlocks \
  test_linearization.Linearization.test_truncate_bypasses_row_triggers \
  test_linearization.Linearization.test_zero_row_legacy_clear_does_not_invalidate \
  test_linearization.Linearization.test_quote_can_expire_after_grant_check_before_commit \
  test_linearization.Linearization.test_uninstrumented_identity_source_does_not_increment_epoch
```

## Conditional model

The test schema installs illustrative row triggers on `TABLES`, using a
tenant-wide monotonic counter. A snapshot reads authority and a message in one
repeatable-read transaction; the synthetic dispatcher requires the same fixed
tenant, actor, channel and conversation as that fixture. A second, short transaction calls the real voice
grant's reserve and claim, locks the epoch, checks the snapshot epoch, inserts
one scoped one-shot intent, and commits. Successful commit is the
linearization point **L**. On mismatch or failure, intent and both grant and
tenant budget writes roll back. No connection is held through a provider
request; this experiment makes no provider request at all.

With all relevant changes participating in the same epoch and within this
model's other assumptions, a participating write that commits before L
invalidates the snapshot, including edit/revert ABA. A competing writer that
reaches the epoch after the intent transaction locks it cannot commit until
after L. Duplicate same-request attempts commit one intent. Local conditional
intent consumption binds request, tenant, epoch and scope fingerprint and
transitions once. The synthetic test does not turn that local transition into
exactly-once network delivery: crashing before or after provider I/O remains
ambiguous and requires conservative reconciliation.

This can at most establish that the authorized snapshot was current at L. It
does **not** ensure the provider receives nothing after a revocation committed
later, retract audio or data already sent, prevent output already in flight,
or prove any real provider's cancellation semantics. Strict no-delivery-after-
revocation requires a separately designed end-to-end protocol and may not be
possible with an external provider. The first context upload needs its own
equivalent authorization boundary, as does every subsequent billable response.

## Coverage and blockers

`TABLES` is an intentionally incomplete test-fixture inventory, not a
production migration. Direct review finds at least these dependencies:

* `tenant_operator_access`, `tenant_operator_voice_context`: tenant status,
  membership, workflow policies, dialog route, identity conflict state,
  integration consent/settings, assigned agent and its configuration.
* `tenant_connect_identity` and related owner/phone/equivalence helpers:
  conflict events, owner identity assertions and native contact/identity
  relations, in addition to the simple identity/link tables in the fixture.
* `tenant_operator_copilot._read_history`, episode and media-evidence readers:
  messages including raw and timestamps, private thread and clear, assignment
  state, and saved media evidence. `tenant_ai_agent_run_store.knowledge_context`
  additionally reads knowledge sources/chunks, candidates and their links,
  and instruction publication/provenance. Source selection can change on new
  as well as modified records.

The current actual `_capture_once` and `knowledge_context` use separate
connections; the experiment's one-transaction snapshot does not exercise the
full real prompt or ensure its source selection is atomic. A future integration
must inventory all branches, tables, old and new writers, direct SQL,
migrations, deletes, tenant moves, and bypass paths, and prove snapshot and
revision refer to the same version. This implementation rejects tenant moves,
misses an illustrative owner identity table and does not intercept TRUNCATE;
the latter two counterexamples are explicitly tested. They do not assert those
operations occur in production.

An independent review identified a particularly relevant current-writer gap:
the legacy scoped thread clear can issue a DELETE when there is no row. No row
trigger fires, so even an otherwise comprehensive row-trigger set cannot see
that semantic clear; a stale intent can still be committed. The synthetic test
reproduces this exact SQL shape. A durable scoped clear event/protocol with
fleet-wide writer coverage is required; retain hard-off while old clear writers
can execute.

The intent stores a scope fingerprint and epoch, but **not** the exact
authorized provider payload or its hash. A real protocol must bind and verify
the exact immutable bytes before external transmission, including the initial
context upload. Moreover the underlying grant checks its expiry before later
locks and before commit. The synthetic expiry test deliberately pauses the
transaction past expiry and shows it can still commit. Checking database time
after locks is necessary for an eventual design, but a pause before commit or
network can still cross a deadline; the permitted quote-validity instant and
maximum queueing delay need an explicit enforceable contract. The PoC is not a
production-ready admission, pricing or dispatch mechanism.

The trigger ordering also has an observed synthetic deadlock: a writer holding
the epoch and then acquiring the existing tenant budget advisory lock conflicts
with a dispatcher holding that advisory lock and waiting for the epoch. The
test deliberately constructs that order; it does not establish that a current
production writer does so. Existing Copilot authorization/history readers also
take row locks, so their exact lock ordering, all actual writer paths, retries,
and cross-tenant operations require dedicated analysis. A tenant-wide epoch
would make unrelated dialogues and AI work contend and invalidate one another.
No rollout, limits change, lock change, or migration is justified by these
tests. The existing immutable live gate remains closed.
