# Synthetic bound dispatch and writer inventory

This branch is an isolated, test-only continuation of `31e9d62`. No application
code, ordinary text path, shared database table, production setting or live
provider is changed. The fixture creates disposable objects only inside a
random PostgreSQL schema and tears them down. `FLEET_VERIFIED` and the budget
gates are overridden **only in selected tests**. The real immutable gates remain
closed. The sample payload is synthetic text; this is not an audio-retention
design or permission to store real voice.

## Reproduction

Use fresh disposable PostgreSQL on localhost, no real data, and set
`VOICE_AUTH_PG_URL` to its URL. The database name must be `test_voice_auth_`
plus exactly 32 lowercase hexadecimal digits. The inherited fixture refuses
non-local hosts, URL query arguments and `PG*` environment variables. From
the repository root with the project dependencies installed:

```sh
PYTHONPATH=beintaskbot-main:experiments/copilot_realtime_adapter:experiments/voice_linearization_poc:experiments/voice_two_phase_poc:experiments/voice_bound_dispatch_poc \
  python -m unittest -q \
  test_bound_dispatch.BoundDispatch \
  test_group_admission.GroupAdmission.test_actual_voice_check_accepts_assigned_group_while_context_remains_closed
```

## Boundary the synthetic code actually tests

The inherited first phase uses one synthetic snapshot, exact scope, tenant and
channel/actor epochs, and a hash of server-built synthetic bytes. The second
phase commits budget reservation/claim and the prepared intent. A third,
synthetic `arm_once` transaction checks the real test-authority function,
grant availability, scoped reservation and tenant cap, both epochs, exact
payload digest and quote. It atomically changes the local dispatch and budget
hold to `unknown`, commits, then returns immutable bytes to the caller. No
network operation takes place while its database locks are held.

Tests check one winner under concurrent arm, scope and byte mismatches,
immutable fields, rollback before commit, and a crash after commit with no
blind retry. Revoke, source edit, clear and grant disable before arming refuse
it. The fake sink is an in-memory list: it is not a provider or a transport.

This creates a **conditional database authorization point** for an operation,
not an exactly-once external delivery guarantee. The test demonstrates that
a caller or provider that does not participate can send different bytes than
the DB hash. Another test commits revocation after `arm_once` and then appends
the already-authorized bytes to the fake sink; a further test sends after the
quote expired. These are expected counterexamples: the DB cannot retract bytes
held by another process, prove provider acceptance or enforce a deadline at the
remote end. A committed unknown hold must not be retried as if it were unsent.
Actual end-to-end binding needs a trusted transport that cannot substitute the
bytes, provider-compatible deadline/cancellation semantics, and reconciliation.
Even a final wall-clock check before commit cannot prove the commit or external
send finishes before expiry under arbitrary pauses.

The synthetic bytea column is deliberately useful for demonstrating exact
bytes, but storing complete real voice or personal context is **not** proposed.
A real design must use an approved data-minimization/storage policy, establish
the trustworthy exact payload without persisting raw audio, and prove both
initial context upload and every billable operation are budgeted and fenced.

## Static writer inventory and limits

`writers-static.tsv` records explicit SQL writer matches for 33 candidate
tables in the checked-out Beintaskbot base. `inventory_writers.py` regenerates
the index. The index is a **starting inventory**, not a claim that all writers
or dependencies have been found: dynamically composed SQL, ORM-generated SQL,
stored procedures, triggers, DDL, TRUNCATE, migrations, administrative SQL,
other repositories, older running releases and external ingestion can escape a
text search. Some hits may be migrations or unreachable paths. There is no
fleet or database-role attestation here.

| Read boundary | Candidate state requiring complete write coverage |
| --- | --- |
| `tenant_operator_access.access`, `_read_tenant_workflow` | tenant, members, workflows, pipelines and stages |
| `voice.authorize`, route and `tenant_connect_identity.conflicted` | integration consent, settings, direct routes, channels, links, conflict events, owner assertions and native contact/identity evidence |
| `tenant_dialog_ai_context.resolve` / `assignment` | direct conversation state and agents; for groups also group coordination, group state, channels, agents and links |
| `_read_history`, episode and media receipt projection | messages, their raw/version fields, scoped Copilot thread and clear, assignment state and saved media receipts |
| `knowledge_context` and provenance | source/chunk selection including inserts, candidate links and review, instruction state/publication and active agent binding |
| grant and spend | global voice grant/session/hold and tenant AI spend reservations and cap |

In the inspected local Connect checkout `66a241d967a0a609ea8980bb97219659fbb7dbe5`,
`src/evolution-events.ts`, `src/webhooks.ts`, `src/worker.ts` and persistence
code participate in event delivery before worker-side message projection.
That checkout is not attested as the deployed SHA. Messages or status events
in flight, Connect's own database, replay/import/backfill, and writes by another
worker version must be accounted for by a real cutover. A live fleet/version,
database permissions and provider inventory are unavailable to this experiment.

An actual local PostgreSQL `voice.check` test shows that an assigned group JID
can pass current preflight on this base. This is **not** a voice session and
does not mean group coverage exists: the context/history/provider gates still
refuse. Group assignment reads additional coordination/channel/agent state
that the epoch test fixture does not cover. Group calls must remain blocked
until their full semantics and sources are independently reviewed. A separate
branch proposes a direct-only preflight and must be reviewed independently.

The inherited zero-row clear counterexample remains decisive: old clear code
can make no row-level change and thus evade row triggers. Merely declaring old
writers absent in a Boolean is not verification. Tenant-wide trigger/epoch
locks can also cause contention across unrelated conversations and interact
with budget locks; the inherited test demonstrates a possible deadlock without
asserting any particular production path executes that ordering. A real fleet
barrier must prove every relevant writer and clear event is covered, establish a
safe lock order with existing work, and measure compatibility. Until then no
claim of a complete inventory, production-safe freshness fence or safe rollout
is warranted.
