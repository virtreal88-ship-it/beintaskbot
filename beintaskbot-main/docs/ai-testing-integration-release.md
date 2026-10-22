# AI testing center integration — release blocked

Historical release report. The corporate Test Mode, real-conversation bindings, and their draft-only worker path described below were retired later; the isolated virtual testing chat remains.

Date: 2026-10-16. Branch: `codex/integration-ai-testing-center`.
Base: production `f49e8b5ca7d22adb1e61008a270057bb05d9021d`.
Source: `codex/ai-testing-center` / `f3c8c3c6` (squashed without conflicts).
Connect remains `ea2179b3201acea017bc3e15bd6eb1bd52d53f8b`.

## Release decision

DO NOT DEPLOY. The user's explicit stop condition is met: the same WhatsApp conversation on two channels shares Human Mode/Resume state. No production merge, push, deploy, data updates, agent settings changes or session changes were performed. Existing ambiguous-channel guards remain in place.

Offline PostgreSQL regressions reproduce both failures:

- `mark_operator_reply` on channel A changes channel B's queued job to `skipped`.
- The operator history for channel B then reports `human` although the operator answered only on A.

`saas_ai_conversation_state` is keyed by `(tenant_id, provider, conversation_id)`; it has no channel dimension. Related cancellation, history/resume and handoff predicates also lack it. Connect conversations and message history themselves have channel-bound identities. A read-history assertion confirms the problem is state projection, not just a missing schema column. The pre-existing ambiguous-channel admission guard denies both channels, preventing an unsafe workaround but also preventing normal new Manual drafts for this shared contact.

The two desired invariants are retained as strict XFAIL tests in `tests/test_testing_integration_release_gates.py`. Run with `--runxfail` for a blocking release gate: **2 failed, 1 passed**. Normal suite XFAILs must not be interpreted as release readiness.

Before a future release, channel identity must be carried through state keys, job uniqueness/supersession, leases/recovery, handoff, Resume cutoffs, manual-send final checks and support-ticket links. Migration of existing ambiguous state needs a conservative explicit policy: preserve Human Mode, do not replay old inbound messages or uncertain sends, and validate the two real agent scopes without modifying their settings. Merely removing `NOT EXISTS`/ambiguous-channel guards is unsafe.

## Media HTTP 403

The preceding production check established an active owner membership in a company whose lifecycle status is `onboarding`; the attachment API returned 403 before reaching Connect. Exact cause: `tenant_chat_attachment_store.load` required `tenant_status == 'active'` for all providers, while `tenant_operator_access.access` already permits `active`, `onboarding`, and `ready_for_integration` for Dialogs.

The integration changes only read-only Connect previews to the existing Dialogs lifecycle policy. Suspended/archived companies, inactive/revoked members, missing Dialogs permission, wrong tenant/channel/lead/conversation/message, disabled channel and disconnected integration remain denied. Kommo attachment policy and all send/AI policies remain unchanged. Authority is still checked again after download. No company status is changed.

Tests cover photos/audio for all five lifecycle states; all existing scope/permission denial cases now run in each allowed lifecycle state. AI job count remains unchanged. This correction is local only because deployment is blocked. Production 403 therefore remains. Live remote API-key scope and real-file retrieval were not revalidated: no authenticated browser surface is available in this turn.

## Three testing modes

- Kommo: tenant-owned read-only catalog, exact account/deal/contact/Talk/chat/origin verification and selected-agent admission/rechecks. No live Kommo test binding was changed.
- Connect: selected agent/channel/contact; Test Mode generates drafts only and blocks automatic and manual sending of those test drafts. The company mode is not switched by opening/saving the testing center. A shared contact on two channels remains blocked by the existing safety guard.
- Virtual: isolated tenant/actor sessions use real agent/company/scope instructions, model selection, active scoped knowledge and trigger/schedule policies with a mocked provider in automated tests. Actions are simulated; production jobs, drafts, messages, states, tickets, notifications and settings do not change. UUID retries do not regenerate, uncertain usage remains charged, and late results after Pause/Human Mode/permission changes are suppressed. No paid production AI call was made.

## Schema, routes and compatibility

Two additive private test tables and nullable selected-agent fields on the existing provider test bindings. Tenant-bound foreign keys, owner/authorized-admin checks, same-origin writes and expected tenant/actor checks are retained. Repeated schema initialization preserves private history and existing NULL bindings. PostgreSQL semantics were exercised in isolated PGlite, not the production database; no production migration ran.

New assets and API routes are under the existing /app AI Agents architecture. Entry: `/app` → AI Agents → Testing. Existing night-agent wizard, operator navigation, diagnostics, night report and group/media fixes remain inherited from the latest main. Pilot `/webapp` entrypoints/assets and `bot.py` have no diff; existing route and legacy UI regressions pass.

## Validation

- Full available Python suite after source integration: **1482 passed, 4 existing Windows skips, 240 subtests passed**.
- Final targeted testing/media/schema/release gates after local corrections: **117 passed, 2 strict XFAIL**.
- Explicit channel release-gate run with `--runxfail`: **2 failed, 1 passed**, as detailed above.
- All **56** executable JavaScript test suites passed (PGlite helper excluded).
- `git diff --check` passed. Source and other agents' worktrees were not modified.

The four skipped webhook tests require Linux due to the known Windows asyncio socketpair limitation. Virtual UI text remains Russian in this first version. Virtual testing is text-only and has bounded history; it does not simulate real WhatsApp payloads or media. Real browser/mobile and live provider acceptance could not be repeated because browser inventory is empty.

Production was checked read-only through Railway: Beintaskbot deployment `26973d29-ec76-4352-9118-fc521af8aa3d` and Connect deployment `9bc33025-cd1f-4e09-85ba-a4d6064c86f4` were SUCCESS on the expected revisions. There is **no deployment ID for this integration**.
