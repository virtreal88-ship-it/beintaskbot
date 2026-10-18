# Channel-local continuation of quarantined legacy dialogues

Prepared only; no production permission has been granted. Based on main 879c72fd and the locally integrated legacy-recovery foundation c890d167.

## Administrative flow

The existing dialogue diagnostic modal offers `Continue on the existing channel state` to owners/admins with both dialogue and AI-management rights. GET/POST `/api/platform/ai/dialogs/legacy-continuation` also enforce those permissions server-side, the tenant/user expectation, and POST origin checks. The administrator explicitly chooses the existing channel route and an enabled primary agent whose scope includes it, then confirms the current evidence token. Groups are not eligible.

The selected state must already be Human or paused. The operation assigns its agent, increments its revision and records a channel-local permit; it does not move the legacy state, change Human/Manual, queue work or send anything. Existing archive entries are never overwritten. Resume is a separate operation and does not replay inbound messages.

## Fences

`saas_ai_legacy_permits` stores tenant/channel/conversation, selected agent, legacy revision, scoped revision, grant timestamp and administrator. `ai_legacy_blocked` is the shared SQL gate for UI/control, queue admission/claim, reservation/completion, draft reservation and final Connect sending. Work also needs a persisted scoped webhook and incoming CRM message newer than both grant and Resume. An absent source fails closed. Existing dialogues without a legacy row retain previous behavior.

Normal Resume, a verified new inbound and a successfully checked send atomically advance the permit by one scoped revision. Other revision/binding changes invalidate it, including Human takeover and Pause; another administrative check is then needed. Legacy changes invalidate all permits tied to the prior revision. The second channel gains no permission implicitly.

Administrative confirmation uses short SHARE ROW EXCLUSIVE table locks before reading evidence, plus the existing dialogue lock. This prevents concurrent RC writers from inserting unfinished work between validation and grant. Lock timeout is 1.5 seconds; statement timeout is 5 seconds. The transaction performs no network calls. Contention aborts the operation rather than weakening checks. These rare administrative transactions can briefly delay writers in other tenants. In ordinary operations a shared lock on the retained legacy row holds its revision stable through the transaction, including final sending.

Outstanding queued/processing/unknown/drafted jobs, pending/sending/unknown drafts and generating/unknown runs for the same conversation on either channel block a grant. They are neither cancelled nor retried by this action.

## Migration and rollback

Additive schema only: one empty permit table with a tenant/channel/conversation primary key and one SQL function. No existing states, messages, drafts or archive rows are migrated. Schema re-execution is tested. Deploy schema before code/worker that uses the function. Do not grant any production permit as part of deployment.

Application rollback can leave the new table/function in place. Previous code sees the preserved unscoped legacy row and blocks both channels again. Do not delete legacy data or use rollback to retry unknown sends. Deployment remains unperformed.

## Validation

Targeted isolated SQL tests cover two channels, scope/permissions, explicit confirmation, stale previews, legacy/scoped revision invalidation, preserved archives, Human/Resume, old webhook and draft barriers, new Manual drafts, repeated Automatic sends, intervention during generation and the final transport recheck. Existing isolation, assignment, text/voice, group coordination and route tests are included. jsdom checks explicit channel/agent choices, confirmation and absence of implicit Resume, alongside existing RU/AZ/EN diagnostic behavior.

Three genuine multiprocess tests are provided in `tests/test_operator_continuation_multiprocess.py`, using only the existing disposable localhost PostgreSQL fixture. They are skipped unless `BEINTASKBOT_TEST_POSTGRES_DSN` is configured. They must be run before production release; PGlite interleaving tests do not prove multiprocess locking. No production data, live model calls or customer sends were used. `/webapp` files are unchanged.
