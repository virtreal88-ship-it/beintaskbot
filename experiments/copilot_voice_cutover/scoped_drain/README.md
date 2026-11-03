# Scoped legacy-drain experiment — NO-GO for release

This is an isolated test of a *conditional* per-conversation handoff. It is not
connected to a route, not an implementation of voice, not a fleet barrier, and
must not be used in production. The existing voice admission remains hard-off.
The tables and sample protocol are synthetic and have no authorization or
production migration. All tests use a disposable local PostgreSQL database.

## Mechanism and precise assumptions

After **every** potentially affected old writer is independently drained and
prevented from reappearing, a hypothetical Phase A binary would register every
legacy history operation before it can read or modify that scope, including
text work that waits on a provider, clear, and cleanup. The scope's durable row
is locked when admitting a v1 operation; its durable lease persists until the
operation is proven unable to mutate history. A crashed worker leaves its lease
in place: no TTL, inference from a killed process, or optimistic reaper. While
any v1 lease exists, a scope cannot switch to v2. Further text admissions can
still choose v1; a persistent lease can delay voice for that scope indefinitely.

Promotion and admission serialize using the same scope-row lock. Promotion of a
scope succeeds only while its v1 lease set is empty, and the transaction records
v2 before a competing admission can select v1. Assuming every writer correctly
obeys the protocol forever, no v1 mutation can overlap v2 in the same scope.
This is scoped; it does not imply that another tenant or dialog has drained.
There is no automated fleet proof, irreversible old-writer fence, or reliable
operational integration in this experiment. A previously deployed binary never
registers a lease; it can overwrite v2 history after promotion. Thus this model
must not be mistaken for a safe rolling-deployment protocol.

The synthetic functions accept their caller's scope and lease ID. Production
would require authorization, request/lease identity, transactional handoff,
replay protection, lifecycle and failure handling, isolation of DB permissions,
supervision and immutable rollback/restart prevention. `resolve_v1` is unsafe
if called before the actual **last possible** legacy mutation. Successful HTTP
return, process death, provider timeout, empty receipt count or lease age alone
are not sufficient proof. If a process crashes after saving but before release,
text availability may continue in v1 but transition can remain stuck until
independent evidence resolves it. This is intentional safety, not a recovery
implementation. No automatic unknown result retry is permitted.

All old tenant-wide expiration deletes require a compatible replacement before
any scope can become v2: legacy cleanup is not scoped by v2 generation and could
otherwise delete a voice-bearing thread. No such integration is implemented.
Other mutation and clear paths must be inventoried and covered. The experiment
also does not establish retention, source freshness, tool/tenant auth, client
transcript ordering, provider termination, budget or cached-result semantics.

## What the PostgreSQL tests demonstrate

* Scoped row locking gives either (v1 admission, promotion denied) or
  (promotion succeeds, v2 admission) in the tested concurrent race.
* An unresolved v1 operation does not prevent further v1 admissions, while
  keeping voice transition unavailable for that scope.
* With mocked authorization, the actual baseline `_finish` completes before
  scope promotion; a later synthetic v2 voice append preserves that text.
* In v2, the experimental thread helper rejects an append with an old clear
  revision; a subsequent new-revision partial voice entry and text persist.
* **Counterexample:** even with a v1 lease registered, the actual legacy
  `_finish` can restore a pre-clear snapshot after legacy clear. The test passes
  by reproducing this bad outcome. A lease is not a scoped clear fence. Existing
  legacy text semantics remain faulty before the promotion; this candidate
  therefore does not satisfy a requirement to prevent all clear resurrection.
* **Counterexample:** the baseline tenant-wide expiry DELETE can remove a v2
  thread with an unfinished writer. A late v2 append recreates revision zero
  and accepts a stale completion. Passing reproduces the bug, not safe cleanup.

These are small isolated schedules, not a correctness proof, real access tests,
production migration test, live voice session or rollout. An untracked writer,
a late mutation after lease release, the legacy cleanup, or loss of fleet
fencing invalidates the model. The previous zero-row-clear ambiguity still
applies. Do not enable voice or claim full clear safety based on these tests.

Run with the application dependencies and a **disposable localhost** database
named `copilot_voice_poc...` via `COPILOT_POC_POSTGRES_URL`:

```
python -m unittest discover -s experiments/copilot_voice_cutover/scoped_drain -v
```
