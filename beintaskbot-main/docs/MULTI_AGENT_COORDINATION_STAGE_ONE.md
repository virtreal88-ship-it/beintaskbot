# Multi-Agent Coordination — stage 1

Prepared in isolated worktrees on 2026-10-18. No merge, push, production migration or deployment. Bases verified against origin: Beintaskbot `ffc5a677`, Connect `de67f99`. Connect result: `8c830d2f32369ee6edc05c7c218e6eb12a8c6a17` on `codex/group-coordination`.

## Coordination and identity

The coordination key is `(tenant_id, connect, verified group JID)`. Only strict numeric WhatsApp `@g.us` identities are eligible. A title, contact phone, or guessed LID never joins groups. Existing channel IDs, provider, conversation rows, received/sent history, tickets and legacy quarantine are retained. The operator list folds only the presentation; its channel selector opens the original scoped history.

Four additive PostgreSQL tables hold group authority/revision, provider-event receipts, permanent delivery reservations and explicitly confirmed employee identities. No existing conversation is deleted, reassigned, merged or mass-resumed. Groups without an explicit assignment cannot generate or send. Admin configuration always stops AI, preserves existing Human Mode, and requires a separate Resume.

Assignments contain agent ID, sending channel and explicit priority. Eligibility reuses the real enabled flag, channel/group scope and agent schedule/timezone. Highest priority wins; a tie or missing assignment blocks responses. Experts are never eligible as primary. A scheduled change never clears Human Mode. Existing channel-local Human/Pause gates are also preserved; if the newly selected route was stopped, its explicit Resume is required.

Group advisory locks precede channel/row locks. An authenticated operator send claims Human Mode before external I/O and preserves the actor. Human takeover cancels only jobs for that tenant and group, fences prepared results by revision, and leaves other groups/personal chats alone. Resume admits future messages only; provider receipts and stored message timestamps prevent old-message replay. Manual/Automatic and Active/Test settings are not modified.

## Sending and authorship

Only the selected primary/channel can claim a provider event. The shared event ID is hashed and atomically reserved across channels. A permanent delivery reservation is committed before network I/O. The final locked send checks current group authority, channel state/revision, agent, company, Test Mode and automatic-send policy again.

Connect adds `/v1/channels/:id/group-messages`: authenticated, channel-scoped, synchronous, one-shot delivery, without `message.send` outbox entries. Its reservation survives crashes; concurrent calls or retries never call the provider again. An unknown network result stays reserved. Ordinary personal-message transport remains supported. Previously the normal Connect queue could send after the Beintaskbot lock ended; coordinated groups deliberately do not use that queue.

A takeover committed before the final send prevents sending. A provider request already in flight cannot be recalled; no claim of distributed exactly-once delivery is made. No automatic retry follows an uncertain result.

Employee control requires active tenant membership plus an administrator-confirmed exact JID/LID backed by a stored signed `fromMe` company-number observation. Registration API: `POST /api/platform/ai/dialogs/group-staff`. Unknown senders have no control authority. No automatic phone/LID equivalence is inferred.

Connect propagates verified origin client IDs across channels. An early echo waits up to 16 seconds for the provider-ID association. If authorship remains unresolved, the group is paused with a diagnostic reason; existing Human Mode is retained. Known AI echoes never cause Human Mode or enqueue an answer. Employee control receipts are deduplicated across channels and survive Resume, so a late repeated webhook cannot undo it.

## Interface and API

The existing `/app` dialog gains connected-channel selection, primary agent, effective AI state, employee name when available, and the existing Stop/Enable controls. Admins configure primary/channel/priority in the same compact dialog. POST `/api/platform/ai/dialogs/group-coordination` uses revision comparison and current server-side permissions. Saving sends nothing. RU/AZ/EN strings and existing mobile layout are reused. Staff roster configuration is API-only in this stage.

The common state API overlays effective group state; per-channel history and messages keep their original routes. Existing support-ticket and handoff hooks are reused; repeated group handoff creates one ticket. Legacy ambiguous records remain blocked.

Changed modules: `tenant_ai_group_coordination.py`; AI schema, scope, Connect admission, run store, actions and agent store; Connect transport and authenticated manual-send store/service; operator projection/assignment/API/routes; operator JS and localization. Connect changes: domain store, persistence, HTTP routes and scoped provider-status updates, Prisma index migration. `/webapp` files are unchanged.

## Sol contract

Agreed with Sol: expert branch `codex/ai-expert-stage-one`, commits `fd8a0a72` and `6f498744`, not merged here. `request_expert(job, parent_reservation, *, problem, lines, triggers, checks=(), previous_actions=())` returns an internal result only. It requires integer `group_revision` and non-empty `group_event_key`; `authority(...)` / `validate_authority(...)` fence the primary before generation and accepting results. The expert receives no send, assignment, state-change or Resume capability. Actual combined expert integration remains a separate step; no expert execution was enabled for existing agents.

## Verification

Final checks: 273 distinct Python tests passed across the targeted runs, plus 32 subtests; 54 Connect tests passed (including real PostgreSQL migration/reservation tests), TypeScript build passed; 4 operator DOM regression scripts passed. The Python runs were 178 base regressions, then 51 overlapping group/manual-send tests and 16 additional send-store/API tests, 11 Test Mode tests and 51 testing-center/virtual-chat regressions. Five real ffmpeg decoder tests are included, not skipped. `git diff --check` passed. Tests use synthetic conversations, mocked AI/Evolution and disposable localhost PostgreSQL. No real WhatsApp messages were sent.

Coverage includes two channels/agents, cross-channel employee and AI echoes, repeated webhooks after Resume, legacy quarantine, tenant/role isolation, Manual drafts, automatic replies, schedule overlap/midnight, handoff idempotence, competing processes/reviewers/final senders, voice decoding, Kommo and existing operator UI. Real PostgreSQL tests exposed and verified a fix for DDL/group-lock deadlocks: schema work now commits before acquiring message/group locks. Migration tests preserve a pre-migration message and verify distinct channel receipts/statuses. UI checks are DOM regressions; no real-device visual acceptance is claimed.

## Integration and rollout constraints

1. Review both commits together and audit pending legacy group jobs/outbox deliveries before any rollout. Quiesce old worker/outbound processing during the coordinated upgrade; old queued sends must not be mistaken for the new one-shot protocol. Resolve ambiguous pending deliveries without blind resending or deleting history.
2. Apply Connect migration `202610170001_group_event_channel_scope`, then deploy its API. It replaces tenant-wide provider-message uniqueness with tenant + conversation-row + provider ID. No data is removed. Index creation requires a deployment window appropriate to table size; no production load test was performed.
3. Deploy Beintaskbot and additive coordination tables before restarting its workers. Existing groups fail closed until an administrator explicitly configures and resumes them. Existing agent settings and WhatsApp sessions stay untouched.
4. Confirm employee identities, test a controlled group on two company numbers, then enable only the selected primary route. Keep a backup and read-only quarantine audit as release prerequisites.
5. Do not blindly downgrade Connect to the old tenant-wide lookup/index once duplicate provider IDs exist across channels. Preserve the new scope-aware transport or prepare a reviewed forward fix. The new Beintaskbot tables can remain in place on application rollback, but old group automation must stay stopped because it has no coordination fence.

Limitations: no production/real-WhatsApp acceptance in this task; no automatic secondary-channel fallback or shared cross-channel AI history; ingestion through the selected sending channel is required. Unknown sends/authorship intentionally block instead of retrying. Previously stopped selected routes require explicit Resume. Staff proof setup has no dedicated UI. The expert implementation remains on Sol's separate branch.
