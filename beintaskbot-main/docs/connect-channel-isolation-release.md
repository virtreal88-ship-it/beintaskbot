# Connect channel isolation: release record

Branch: `codex/connect-channel-isolation`, based on testing-center integration `f38f1a5e`.
Production references rechecked: Beintaskbot `f49e8b5c`; Connect `ea2179b3`.
No changes to the Connect repository are needed for this fix.

## Cause and correction

The AI state primary key omitted `channel_id`; cancellation and state-projection queries reused the same contact/JID across channels. The job primary key omitted channel too, so an agent scoped to multiple channels could overwrite its other channel's pending work. Connect's own conversation records already have the correct tenant/channel/conversation key.

State is now identified by `(tenant_id, provider, channel_id, conversation_id)`. Jobs add `scope_channel_id` to their existing tenant/agent/provider/conversation key. This generated column contains the channel for Connect and an empty string for Kommo, preserving Kommo's prior identity semantics.

The change covers webhook admission and provenance, event supersession, queue claim/recovery/retry, state and revision joins, Human/Pause/Handoff/Resume, manual drafts and final send, voice Resume cutoffs, diagnostics, operator projections and support ticket state. Existing run UUID and immutable event receipts remain the idempotency authority; receipt fingerprints were not rewritten. Viewing media and virtual testing do not create real outbound messages.

Per-dialogue advisory transaction locks include tenant, provider, channel and conversation. A completed operator takeover wins over a waiting AI send on the same channel. Other channels retain independent states and jobs. Final send checks the scoped state and expected revision while holding the lock; uncertain provider outcomes are not retried automatically.

Real multi-process PostgreSQL tests also reproduced a cold-start migration deadlock in the shared platform schema. A cross-process schema advisory lock now serializes that migration before DDL begins.

## Files and integration points

- `tenant_ai_dialog_scope.py`, `tenant_ai_channel_schema.py`: scope validation, locking, migration and guarded rollback.
- `tenant_ai_agents_schema.py`, `tenant_platform_schema.py`: schema integration and startup serialization.
- `tenant_ai_agent_connect.py`, `tenant_connect_transport.py`, `tenant_connect_voice.py`: inbound/outbound, takeover and voice history.
- `tenant_ai_agent_run_store.py`, `tenant_ai_agent_actions.py`, `tenant_ai_agent_crm_actions.py`, `tenant_ai_agent_queue.py`: scoped work, leases, completion, handoff and Kommo-compatible conflict targets.
- `tenant_ai_agents_store.py`, `tenant_ai_reliability.py`, `tenant_operator_dialogs.py`, `tenant_support_inbox_store.py`: APIs, drafts, status and ticket projection.
- `docs/tenant-operator-i18n.js`: legacy ambiguity explanation in RU/AZ/EN, under `/app` only.
- `scripts/audit_connect_channel_scope.py`: read-only production data gate, counts only.
- Regression tests cover channel isolation, migration/rollback and independent processes, alongside existing testing-center/media/Kommo/pilot tests.

## Existing data and rollout gate

Migration version 1 snapshots all legacy Connect states in `saas_ai_channel_legacy_state`. It assigns a channel only when exactly one persisted route exists and neither existing jobs nor stored webhook provenance contradict that route. State, reasons and Resume cutoffs are preserved. Ambiguous rows retain an empty channel and block admission, claim, AI send, draft sending and Resume for that tenant/JID. They remain readable; no message, ticket, session or agent setting is deleted or merged. Resolving these rows requires evidence and an explicit data-resolution procedure; the release does not invent one.

Before publishing, run `python scripts/audit_connect_channel_scope.py` inside a trusted environment with the existing database URL in its environment. It sets a read-only transaction and a statement timeout, never calls schema initialization, prints counts only and exits nonzero for ambiguity, inconsistent job provenance or lack of a verified connection. Do not paste the URL into tickets or logs.

The subsequent authorized audit ran in the existing worker's Railway web console, using its DATABASE_URL without disclosure. PostgreSQL 18.6 returned 16 legacy Connect states: 12 uniquely assignable and 4 ambiguous. All 7 Connect jobs had consistent route/event provenance. The old primary keys matched expectations and had no inbound foreign keys. The user explicitly approved retaining ambiguous rows in quarantine; the original CLI gate still reports those rows conservatively as blocked.

## Safe publication and rollback

1. Complete the read-only data gate. Under the subsequent user authorization, ambiguous states may remain quarantined. Do not infer a channel or reset Human Mode. Inconsistent job provenance remains a blocker pending investigation.
2. Back up the database. Pause application writers/AI workers for the key migration; do not overlap old and new binaries. Old binaries use the old `ON CONFLICT` keys and are not rolling-deployment compatible with the new primary keys.
3. Apply the transactional schema once, rerun the gate, then start the tested Beintaskbot revision. Connect remains on `ea2179b3`; no Connect deploy or session reset is required.
4. Check health, worker recovery, media HTTP 403 correction, all three testing modes, and two selected owned WhatsApp channels. Verify manual A leaves B active, Resume processes only new inbound messages, and drafts/tickets/history belong to the selected channel. Do not message third-party contacts.
5. On failure before commit, PostgreSQL rolls back the migration. An offline `rollback_keys` helper restores old key constraints only if no multi-channel routes or duplicate old identities exist. It preserves added columns and snapshots and refuses unsafe collapse. After multi-channel data exist, use a forward correction; never blindly redeploy the old binary or discard new records.

No production migration, merge, push, deployment, secret rotation, proxy change, session reset or agent-settings change was performed. The integration's Connect media onboarding-403 correction is retained unchanged. `/webapp` entrypoints/assets and `bot.py` have no diff against production.

## Validation

- Full Python suite: **1544 passed, 4 skipped, 240 subtests passed** (220.31 seconds).
- After the final legacy-status presentation correction: **128 affected integration/reliability/testing/media/release-gate tests passed** (34.30 seconds).
- **7 real PostgreSQL multi-process race tests passed**, including simultaneous leases and duplicate signed webhooks; also included in the full run.
- **56 JavaScript test suites passed**.
- The 4 skips are existing Windows asyncio loopback limitations; the signed webhook/storage paths are additionally covered in the local PostgreSQL process tests.
- `git diff --check`, Python parsing and absence of `/webapp` changes verified.

 Real PostgreSQL race tests use a disposable database on a loopback-only temporary server, with all Connect/model calls mocked. The database is dropped and the local server stopped after validation. Portable test binaries came from [EDB's PostgreSQL binaries](https://www.enterprisedb.com/download-postgresql-binaries).

Live WhatsApp and production data migration have not been exercised for this revision. Railway read-only status shows the existing application, Connect, Evolution and PostgreSQL services online with no pending deployment. Existing application deployment: `26973d29-ec76-4352-9118-fc521af8aa3d`; Connect: `9bc33025-cd1f-4e09-85ba-a4d6064c86f4`. There is no new deployment ID.


## Production preflight and startup correction (2026-10-16)

A custom-format pg_dump was written inside the existing PostgreSQL container to
`/var/lib/postgresql/data/beintaskbot-release-8ab2d57d-20261016/database.dump`.
The directory is private (umask 077); the archive is mode 0600. No dump or secret
was exported to logs or another service. The archive was successfully restored
with `pg_restore --exit-on-error --single-transaction` into a temporary database
on the same existing PostgreSQL server. This is migration recovery protection,
not an off-volume disaster-recovery backup. Railway's built-in backup page had
no backups and required a plan upgrade to create one; no upgrade was made.

The exact accepted migration (verified SHA-256) and full AI schema were rehearsed
on the restored database. The result was 12 resolved / 4 quarantined states,
unchanged original state fields and job rows, safe repeated migration, and refusal
of an unsafe downgrade to channel-less keys. Never restore this archive over a
live database with later messages: stop writers and reconcile newer data first.
Prefer a forward correction after multi-channel state has been created.

The first production rollout exposed a cold-start lock inversion not present in
the single-process schema rehearsal. A webhook acquired Connect DDL locks before
waiting for the AI schema Python lock; the startup thread held that Python lock
and waited for Connect DDL. Manual outbound recording had the same order. The
worker was stopped; PostgreSQL confirmed zero remaining lock waiters, no committed
channel migration, and all 16 legacy states retained. The follow-up correction
initializes AI schema before Connect DDL in those two entrypoints. It does not
change business data, send rules, agent settings or the pilot UI.

Two deterministic real-PostgreSQL thread regressions cover webhook and manual
outbound cold startup. The follow-up blocking suite passed 150 tests, including
9 real PostgreSQL concurrency cases, testing-center, channel isolation, migration
and media checks. Final deployment ID and post-release checks are recorded in the
release conversation; the historical validation above describes the accepted base.
