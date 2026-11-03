# Copilot history rolling-cutover experiment

This branch is based on draft PR #480 at
`156ce9ce15d8e4e3cddcbca2006614a827f028e3`. It is an experiment, not an
approved migration or release. Voice admission, transcript provenance,
provider, cost and distributed lease gates remain closed.

## Observed writers and protocol

The old `tenant_operator_copilot.run` reads a private thread into memory,
commits before provider I/O, then `_finish` writes the whole stale array with
`ON CONFLICT ... SET turns=EXCLUDED.turns`. It computes `completed` from the
configuration and writes the thread even if its `sending` receipt UPDATE
matched no row. `clear` is a scoped DELETE, including when no row exists;
automatic retention also DELETEs. `tenant_saved_only_copilot._read` uses the
shared history reader but does not use this normal history writer. Consultation
requests read source context and intentionally skip this private-thread write.
No additional SQL thread writers were found in this checkout. This inventory
is source evidence, not proof that no other deployed binary, job or SQL actor
exists.

The candidate text writer captures `clear_revision` on read and, after the
provider call, atomically CASes its own `sending` receipt and appends to the
current thread under the row lock. New clear creates a durable empty row and
increments its revision, including for an initially absent thread. Retention
scrubs expired content before a normal request snapshots history, increments
the revision and retains empty tombstones. Read-only consumers filter expired
turns without writing; a later normal request performs the scrub. Safe garbage
collection and metadata-retention policy remain unsolved. No DB
append may refresh an expired nonempty row: it scrubs it under the row lock,
increments the generation and refuses the stale append, so read-only filtering
cannot resurrect old turns. Callers must catch the refusal within their DB
transaction to commit that scrub. No DB
lock is held during provider I/O. If clear wins, the generated answer remains
available, no thread turn is added, and the same `history_status=cleared` is
persisted with the receipt and shown by the updated client in RU/AZ/EN. Older
clients still show the answer but ignore the new warning; this is a verified
mixed-client limitation, not a fully transparent rollout.

`rolling_history_expand.sql` is a manual **review-only** additive expansion.
It uses a short `lock_timeout` and checks the exact target column's type,
nullability and default. It does not make a production change and must precede
deployment of the new reader/writer. The actual lock acquired by ALTER can
briefly block other traffic: a fail-fast timeout is not proof of zero impact.
The regular bootstrap includes the column for fresh databases only; it does
not silently expand existing schemas. Locally, an old writer still reads and
writes after expansion, and old code works after dropping the added column only
in the no-voice synthetic rollback case.

## Fundamental mixed-version boundary

Synthetic PostgreSQL tests run the exact base-commit old `_finish` body and
show that it loses a concurrent voice entry, including after the receipt has
been moved out of `sending`. A legacy clear against an absent row affects zero
rows and leaves no generation signal: even an upgraded in-flight writer then
cannot distinguish that clear from no clear. A row trigger cannot recover the
scope of a zero-row DELETE. Therefore this branch does **not** establish safe
voice history with any old writers or unproven old in-flight processes.

A possible operational sequence requires an independently verified inventory
of *all* writer processes and database roles, additive schema expansion,
rolling the upgraded text writer everywhere while voice stays disabled, then
draining and proving termination of every old process and every old in-flight
provider completion before allowing voice. The `sending` receipt count alone
is not proof: the old finalizer can still write after its receipt changed.
The actual orchestrator, workers, database direct-write permissions, restart
and job lifecycle have not been attested. If that proof cannot be obtained,
the voice-history gate must remain closed. Text must not be paused or old
workforce processes killed merely to satisfy this experiment.

After any voice history is written, rolling back to an old writer is unsafe:
it can overwrite voice turns or lose clear fences. A safe rollback must keep
the new history writer (or use a separately validated compatible backport),
disable voice and reconcile all in-flight sessions. Dropping the new column,
deleting history or reenabling legacy writers after voice is outside the
tested rollback and requires a separate plan and data-policy review. A stale
open browser is another boundary: its missing clear warning requires a
compatible fallback or evidence that the updated UI is in use.

## Evidence and remaining work

`test_rolling_history_pg.py` runs exclusively on a disposable localhost
PostgreSQL and includes old/new overwrite counterexamples, clear of an absent
thread, competing new writers, clear racing the final receipt/append, a real
backend termination/rollback, retention, schema expansion/lock timeout and a
no-voice rollback smoke test. The normal text/saved-only and consultation test
suite uses synthetic PGlite and a mocked provider. These tests do not prove
real rolling deployment, request drain, production privileges or performance.
The example transcript draft still has no live ASR or authoritative playback;
late input transcription completions must be correlated to their item IDs and
committed item ordering, never ordered by completion time.
