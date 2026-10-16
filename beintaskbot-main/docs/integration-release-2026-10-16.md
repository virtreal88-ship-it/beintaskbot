# Coordinated release: voice, support inbox, Pause / Handoff / Resume

Release ID: connect-agent-inbox-2026-10-16.
Beintaskbot branch: codex/integration-voice-inbox-resume.
Connect branch: codex/integration-voice-inbox-resume.
Connect revision: 59b4a9d78e436ac054e78bb34b25e65930c212a7.

## Provenance and isolation

Latest remote production revisions checked twice during preparation:
- Beintaskbot main: 0cf99046caf41b54ae0edbe94f673c5855429ee1.
- Connect master: cb9ffcd09df53e78447d18a310b11ba5b8b9457a.

Imported Beintaskbot commits:
- feature/voice 6e75fe51ac2e5b41b6c2c267aca7b72c551cdebe.
- feature/support-inbox 0dd6bf6cfaf323819ead2901b08910c986d9bcdb.
- feature/resume-ui 05cb1991188d3b3eb3777cc3f28570767a0be874.

Imported Connect commit:
- feature/voice ec78f17775c7d3f1dea5d8a8e46dc7164f7ad35b.

The three Beintaskbot features share the exact main base above. Connect voice
has the exact master base above. Source worktrees were clean. All changes and
dependency installations for integration were made in separate worktrees.
No main/master merge, production deployment, force push, customer message,
Evolution restart, Nizam Ai configuration change or secret rotation was performed.

## Unified behavior and semantic conflicts resolved

WhatsApp audio is stored as an opaque Connect message UUID. The existing worker
retrieves audio from authenticated Connect, validates actual content/duration
with local ffmpeg, transcribes with the existing AI credential policy, then uses
the existing agent instructions, knowledge, dialogue and text sender. Successful
transcripts remain in the tenant-local history with an audio marker.

Cherry-picks had no textual conflicts. Integration fixes address behavior:
- Support summaries count every reserved voice call, including interrupted and
  superseded voice runs; summary and worker use the same daily reservation budget.
- Summaries read only completed, tenant/channel-scoped voice receipts. They include
  one audio transcript marker, without duplicating text or falsely calling a
  successfully transcribed voice unavailable.
- Durable resumed_at prevents coalesced history recovery from transcribing audio
  received before Resume. Existing completed transcripts remain context, while
  Resume never queues old events.
- Accepted reply / Human Mode / support event and ticket commit together, for
  automatic responses and approved manual drafts. Summary failure cannot roll
  back a ticket. Final callbacks are idempotent and cannot undo a later Resume.
- Handoff replay checks lock conversation state before checking the event receipt.
  A repeated manual Handoff in Human Mode does not reopen a resolved ticket.
- Observed operator-message receipt and Human Mode now commit together. A crash
  before commit can be retried; a duplicate after Resume cannot pause again.
- Operating mode is carried through the loaded agent configuration.
- The legacy AI state key lacks channel_id. If a JID exists on multiple channels
  in the same tenant, admission, controls and final send fail closed. Supporting
  independent AI states for that case needs a separate key migration; this
  release does not risk cross-channel state changes.
- Railpack deployment configuration explicitly includes ffmpeg, retaining its
  default packages. Both repository root and Python app root have the config,
  while existing Nixpacks ffmpeg configuration remains compatible.

## Validation

Full Python suite: 1293 passed, 4 skipped, 240 subtests passed.
The four skipped cases are existing Kommo webhook tests disabled on Windows
because of sandbox loopback socketpair behavior; run them on Linux before broad
rollout. No PostgreSQL integration suite was skipped.
After the final lock-order change, all 94 affected boundary tests and 14 subtests
passed again. Isolated PGlite ran real PostgreSQL DDL, SQL, FK constraints,
unique indexes, commits and rollbacks; no production database was contacted.

New integrated SQL scenarios cover:
- Text -> voice -> text with Azerbaijani/Russian mocked transcripts and central key.
- Shared paid-call/token totals; summary budget after superseded voice calls.
- Pause, Resume-only-new, duplicate webhook, manual takeover during transcription.
- Voice-triggered handoff, trusted transcript in summary, summary failure.
- Automatic and manual-draft reply-then-handoff before final callbacks.
- Repeated callbacks after Resume; one ticket even after resolving it.
- Same-JID channel ambiguity fails closed; tenant-scoped routes and receipt FKs.
- Restart after a durable voice claim without repeated paid call.
- Manual/Automatic and Test/Active gates.
- Idempotent additive schema initialization preserving existing voice receipts.
- Simulated interruption before the operator webhook / Human Mode commit.

Existing voice tests also cover unavailable/corrupt/oversize/overlong media,
unsupported formats, real synthetic OGG/Opus decoding, SSRF boundaries,
provider failures, cached transcripts and own outbound messages.
Real ffmpeg decoded synthetic test audio locally; OpenAI/WhatsApp calls were
mocked. No live speech-quality or deployed Evolution media test has run.

Eight UI scripts passed: inbox DOM and tenant switching, conversation controls,
app syntax, diagnostics, Connect scopes, Chat AI, chat history, and send recovery.
Connect: Prisma generation, TypeScript build and 25 tests passed.

The initial full suite exposed 12 failures reproduced on untouched production
0cf99046 (37 passed / 12 failed in the same five modules). Stale tests were fixed:
missing Connect lookup in Kommo fixtures, overwritten authorization mock,
immutable SSO context setup, quote-sensitive route assertions, Kommo-only
onboarding expectation and brittle startup line counts. Production auth,
onboarding and legacy chat-send logic were not modified for these corrections.

Reproduce:
- Install requirements.txt and pytest into a local virtual environment.
- npm install --prefix .support-test-deps @electric-sql/pglite@0.5.10 jsdom@29.1.1
- Put ffmpeg on PATH.
- python -c "import psycopg,pytest; raise SystemExit(pytest.main(['tests','-q']))"
  Preimporting real psycopg prevents older mock-only tests from replacing it.
- node tests/test_tenant_support_inbox_ui.cjs
- node tests/test_tenant_ai_conversation_controls_ui.cjs
- In Connect: npm ci; npm run prisma:generate; npm test; npm run build.

## Safe deployment after one final approval

1. Recheck main/master against the production bases above. If either changed,
   integrate and retest before publishing production. Retain the previous
   deployment IDs and take an existing PostgreSQL backup; never expose secrets.
2. Deploy Connect revision above first to the existing beintaskbot-connect
   service. Keep its migrate:deploy startup command. No new Prisma migration.
3. On an owned test channel, verify health, signed inbound text event, audio
   lookup and tenant/channel/conversation rejection. Do not replay old customer
   messages. Do not alter the pinned Evolution image or sessions.
4. Deploy the Beintaskbot integration revision to the existing worker. Verify
   ffmpeg in the built image and HTTP health, then additive startup migration:
   saas_connect_voice_receipts, saas_ai_agent_runs.reserved_calls,
   saas_support_tickets, saas_support_events, and
   saas_ai_conversation_state.resumed_at. Existing tables/data remain intact.
   Check worker startup diagnostics; HTTP health alone does not prove migrations
   or AI worker startup succeeded.
5. On an isolated test company/owned phone, verify text, AZ/RU voice, mixed turns,
   takeover while speech is in flight, Resume/new-only, one handoff ticket,
   summary failure, duplicate delivery and restart. Test Mode remains draft-only;
   use Active/Manual first, then authorized Active/Automatic for text responses.
   Check shared limits, diagnostics and one existing Kommo agent.
6. First-client test is ready only after these deployed smoke gates pass.
   No additional Railway service or paid infrastructure is required.

Read-only Railway inspection: existing worker tracks main, Connect tracks master;
both successful and no staged changes. Evolution image remains pinned to
sha256:1bd8afc4a6cf48822e6cf02469aeae7bd35a12a6b616eacd1291926307f4d339.

## Limits and rollback

Audio: 10 MiB, 300 seconds, Connect retrieval TTL 24 hours, at most three pending
audio messages per worker turn. Failed/unknown transcription submissions are not
automatically replayed. The client can send a new message; no invented transcript.

Summary state may remain generating after process death; the ticket survives.
It is not automatically regenerated, avoiding duplicate paid calls. Investigate
the run before operator recovery. PGlite validates SQL but not independent
multi-process PostgreSQL lock contention.

Distributed uncertainty remains if Connect accepts a send and the database
transaction is lost before commit. Such work is not blindly resent; inspect
Connect's persisted idempotency receipt and operator diagnostics.

Same-JID multi-channel AI is intentionally blocked as described above.
Connect retains baseline lint configuration debt and two moderate runtime
BullMQ/uuid audit findings (no high/critical runtime findings); dependencies
were not changed as part of this release.

Rollback Beintaskbot to the previous main deployment first, then Connect if
needed. Retain additive columns/tables and dedup receipts; do not drop schema
or replay unknown paid calls/sends. No rollback command has been executed.

References:
- Railpack runtime packages: https://railpack.com/guides/installing-packages
- Evolution media contract: https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/src/api/integrations/channel/whatsapp/whatsapp.baileys.service.ts
- Component details: ai-connect-voice-followup.md and support-inbox.md.
