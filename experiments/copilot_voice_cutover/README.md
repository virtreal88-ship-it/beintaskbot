# Closed voice-write gate and two-phase cutover investigation

This is a local review artifact, not an operational release plan. The adjacent
`cutover_gate.require_voice_write_ready()` **always denies** and is not connected
to `/app`, a backend route, a worker, or a real voice writer. It has no enable
flag and accepts no caller evidence. No code here can make a legacy worker obey
the gate. The existing text workflow does not call it and is not disabled.

## Evidence and what it does not prove

The existing AI heartbeat records one shared `ai_agents` value, not an inventory
of every HTTP/background Copilot writer, executable version or in-flight task.
Text admission inserts a `sending` receipt within a DB transaction. A separate
connection cannot see that receipt before the transaction commits. The isolated
PostgreSQL test demonstrates exactly this: a snapshot is unchanged while a
synthetic legacy admission is open, then changes after commit. Zero visible
`sending` rows therefore **cannot prove drain**. An `unknown` receipt is an
uncertain result, not proof the provider did nothing or permission to retry.

The test also loads the actual baseline `fc75c289` `_finish`: it can overwrite
newer history even after the additive schema exists. A passing test reproduces
that failure. No application-level voice gate can stop that old writer because
it never checks the gate. Schema compatibility alone does not provide a safe
mixed-writer deployment.

`observe_receipts` is a read-only aggregate diagnostic only. It exposes no
prompts or identities, performs no mutations, and cannot open the gate even when
the counts are zero. It is not a replacement for an authoritative fleet,
admission barrier, access checks, or distributed session management.

## Phase 1: compatibility preparation — voice remains closed

* Independently inventory **all** potential writers and clear paths, including
  request handlers, background jobs, old replicas and long-lived in-flight work.
  Establish an authoritative source for live instances and writer versions.
* Design and test a write barrier that covers legacy as well as new workers;
  an environment variable or voluntary new-code check is insufficient. It must
  fence already-running work and restart/rollback, not merely stop new UI calls.
* Plan a separately reviewed additive schema migration and ensure existing
  readers work with it. The current experimental branch also changes the text
  writer; it must **not** be rolled out as a mixed-version preparation step.
* Keep voice admission closed. Do not alter `clear`, expired history or completed
  result semantics as a side effect of a deployment preparation.

## Phase 2: prove drain, then cut over

The following are requirements for a future implementation, not currently
implemented capabilities:

1. Close the proven barrier for every affected write path and maintain it while
   draining all old replicas and in-flight requests. Verify both independently
   from a complete fleet inventory and from an enforcement mechanism that makes
   legacy writes impossible; neither a receipt count nor a heartbeat is enough.
2. Keep uncertain receipts and financial holds intact. Reconcile only using
   authoritative evidence; do not treat an expiry, process exit or absent row
   as proof an external action never happened. No automatic repeat is permitted.
3. Apply and verify the schema on the exact target while writes remain blocked.
   Start and verify only compatible workers at the exact reviewed version. In a
   single fenced transition bind the gate to that schema, fleet and cutover epoch,
   and keep it closed if any evidence is missing, expires or changes.
4. Resume text operations only when their existing behavior is verified. Enable
   voice writes only after a real writer uses the same transactional fencing,
   tenant/actor/agent checks, session idempotency and ordering. The user-approved
   shared test budget additionally requires an authoritative reservation ledger.

The gate must fail closed during deployment races, unknown fleet membership,
worker resurrection, restarts and loss of the evidence source. Rollback requires
blocking and draining the new workers before any old writer is resumed. This
investigation cannot yet prove such a barrier or provide safe activation.

## Independent release gates

Retention and clearing remain separate unresolved product decisions: cleanup is
currently admission-triggered, and a completed receipt can replay a saved result
after thread clear. No assumption is made that clearing the thread erases or
retains all cached results. Full authorization, production schema migration,
independent review, voice provider/session shutdown, real-language tests and
actual paid usage have not been performed.

Run `python -m unittest discover -s experiments/copilot_voice_cutover -v`
with the application's Python dependencies installed. PostgreSQL tests require
`COPILOT_POC_POSTGRES_URL` pointing at a disposable localhost database whose
name begins `copilot_voice_poc`; they create and drop a random schema. The
mixed-version test reads the actual baseline source from local Git history.
