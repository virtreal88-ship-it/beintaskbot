# Synthetic authority epoch + lease — HOLD

This local-only experiment composes the earlier disabled server lease with a
**test fixture** authority revision and the existing `Session` using in-memory
mock authority, budget, context, client and provider. It neither modifies a
production route nor supplies a migration. The live gate and release remain
hard-off. No audio, contact or external provider call is used. Any reference to
"send" or "upload" below means an in-memory mock with synthetic bytes.

## Evidence

The fixture adds a tenant-wide revision row and synthetic row triggers to the
14 direct-dialog fixture tables named in `AUTH_FIXTURE_TABLES`. The experimental
`SyntheticEpochLease` takes the revision row lock, runs real
`tenant_operator_voice_context.authorize` and records the revision with the
lease in the same local claim transaction. During heartbeat it locks the same
revision and lease row, checks the stored revision, then rechecks real authority.
If a participating mutation committed between the two boundaries, including an
edit and revert, the heartbeat commits quarantine and refuses; a cancelled or
failed commit still relies on the base lease's sticky in-process failure state.
The update is fail-closed for a missing revision or lease row.

The tests demonstrate two separate successful commits of `active=false` then
`active=true` on the real fixture membership table invalidate the old lease;
the real `Session` subsequently refuses a new mock audio frame. Synthetic
pipeline, stage, workflow, conflict and agent writes increment the fixture
revision, without changing another tenant's revision. This is a **conditional
ABA proof for participating fixture writers**, not a claim of complete coverage
or a safe migration.

Two counterexamples delimit the result. A mock audio send already in progress
can complete after the revoke commits; quarantine and failed release remain in
place. An initial mock context upload can also have started before revocation;
`Session.start` notices the changed epoch afterwards and does not emit `ready`,
but cannot retract already delivered context. No database transaction can
recall bytes already handed to a remote provider, prove it stopped or establish
a billing cap. A cancelled, unknown or in-flight external operation must keep
its cost hold and lease pending until trusted provider evidence allows recovery.
A separate test demonstrates an actual PostgreSQL deadlock involving the
synthetic revision lock and the existing `authorize` membership `FOR SHARE`:
the epoch-first reader waits for the member writer while its row trigger waits
for the epoch. One transaction is aborted. This is a counterexample against
installing these triggers on the shared database, not evidence of a live
production deadlock.

A further mock suppresses task cancellation and completes its already started
send; `Session.end` times out, retaining the quarantined lease and refusing
release. A local cancellation signal therefore is not proof of provider stop.

## Exact incomplete writer / reader boundary

The local source permits a group route to reach `authorize`: it calls
`tenant_dialog_ai_context.resolve`, which calls
`tenant_operator_case_submission.assignment`. Its group branch calls
`tenant_ai_group_coordination.authority`; that code reads
`saas_ai_group_coordination` and `saas_connect_channels`. The actual
`tenant_ai_group_coordination.py` also writes group coordination assignments
and revisions (e.g. the UPDATE statements near lines 661 and 736 on this base).
Neither table nor those writers is in this fixture's trigger set. Thus the
fixture cannot prove monotonic revision across **all** actual voice-authority
paths. This is a concrete missing writer path, not an assertion that a live
production incident occurred. Group-specific behavior must stay gated.

The direct identity path also calls `tenant_connect_owner_identity.active_link`,
which reads `saas_connect_owner_identity_assertions`; its real `merge` path
inserts assertions (near line 143 on this base). That table is omitted from the
fixture. Some application transactions may also mutate other instrumented rows;
we do not claim an isolated assertion insert was observed in production. The
same limitation applies to conflict-event and native-contact tables in the
writer inventory at `/workspace/voice-bound-dispatch` commit `79c86ef`.

A broad production proof must inventory every deployed old/new worker, service,
background job, direct SQL role, migration, trigger disable/replication mode,
TRUNCATE, tenant move and upstream ingestion writer; map group and direct route
reads; ensure every relevant mutation bumps the correct durable revision; and
bind the actual prompt/history/knowledge selection to the same snapshot. The
real context and knowledge readers use multiple connections. Legacy zero-row
thread clear does not fire row triggers and requires its own scoped event and
fleet-compatible cutover. Code/config deployments can change behavior without a
DB row change. No such fleet/privilege/coverage proof exists.

Before any future integration, establish a global lock order across existing
membership, group, source, lease and shared budget locks and independently test
mixed workers and failure modes. A tenant-wide counter adds write contention and
may interfere with unrelated users; coalescing checks has an unresolved
revocation window. No load, latency or safety claim about production follows.
The base lease's process, clock, bounded recovery, auth ABA between observations,
provider cancellation, spend and pending-support limitations still apply outside
this particular synthetic model. Rollback/restart must not allow an old fence or
release to bypass independent proof.

## Local tests

Use `VOICE_AUTH_PG_URL` for a **disposable localhost** database named
`test_voice_auth_` plus 32 lowercase hex digits. The inherited fixture rejects
other targets and creates/drops one random schema. With project dependencies:

```sh
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=beintaskbot-main:experiments/copilot_realtime_adapter:experiments/voice_session_lease:experiments/voice_authority_epoch \
python -m unittest -v \
 test_epoch_pg.EpochLeasePostgres.test_actual_member_revoke_regrant_invalidates_epoch_and_fence \
 test_epoch_pg.EpochLeasePostgres.test_fixture_workflow_and_identity_changes_increment_same_tenant_epoch \
 test_epoch_pg.EpochLeasePostgres.test_session_blocks_new_audio_after_participating_revoke_regrant \
 test_epoch_pg.EpochLeasePostgres.test_started_mock_send_can_finish_after_revoke_and_remains_unconfirmed \
 test_epoch_pg.EpochLeasePostgres.test_started_mock_context_upload_cannot_be_retracted_by_revoke \
 test_epoch_pg.EpochLeasePostgres.test_cancel_suppressed_mock_send_remains_inflight_and_lease_held \
 test_epoch_pg.EpochLeasePostgres.test_epoch_before_real_authorize_can_deadlock_with_member_writer
```

Some tests intentionally **reproduce failures**. A green result is evidence the
counterexample occurred, not evidence of safe external delivery or rollout.
