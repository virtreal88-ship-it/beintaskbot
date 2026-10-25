# WhatsApp dialogue integrity repair — 2026-10-23

## Confirmed production facts (read-only)

The authorized owner session belonged to tenant `e09a5c24-093f-4141-992d-532052e00c90`.
The complete paginated projection returned 170 routes across two Connect channels:
115 phone JIDs, 6 LIDs, 47 groups, and two other routes. No duplicate nonempty
stored phone was found within one channel. This does not prove that LID/phone
histories are unified: the old normalized events discarded the alternative ID.

The owner-reported split is present on channel `96863d75-d272-4c93-a25e-ffb2b16ecae3`:
lead 7557 (`994557773054@s.whatsapp.net`, seven outgoing records) and lead 668
(`80427641475293@lid`, nine incoming records). Names/avatars were not used as
identity evidence. Existing APIs do not expose the original Evolution envelope;
a provider-confirmed historical LID/phone pairing remains unavailable here.

Push delivery is configured, but the current user's device list was empty.
Actual browser/Windows notification delivery was not tested or enabled.

## Changes and compatibility

Deploy the Beintaskbot guard before the companion Connect change. Connect learns
aliases only from an authenticated same-event remoteJid/remoteJidAlt phone/LID
pair, retains a sole existing route, and quarantines an existing split or a
contradictory pairing without moving historical records. Alias state is scoped
to tenant/channel and survives restart. Without provider evidence it cannot
resolve a LID to a phone. The additive Connect migration adds arrays/flags only.

Beintaskbot preserves conflict receipts and messages, but blocks Active,
Simulation, extraction and Copilot from using the split history. It also
recognizes registered employee numbers on direct inbound messages, preserves
that role in AI/knowledge context, prepares notification DDL before ingestion,
and retries only PostgreSQL-confirmed rolled-back transactions. No uncertain
external sends or connection outcomes are retried.

Visible-list avatar requests have a two-request admission limit and reuse
successful results; header images no longer inherit container positioning.
Download and AI-analysis notices remain separate. Read markers refresh other
same-user/tenant tabs; supported Chromium Web Locks serialize message sounds.
The existing Push enrollment is exposed in Dialogues with RU/AZ/EN guidance.
Avatar access is revalidated after protected download.

## Historical recovery requires owner approval

Do not merge the two production histories automatically. Obtain a trusted
provider pairing; preview affected messages, assignments, unread markers,
handoff/stop state, references and receipts under the exact tenant/channel.
After separate approval, use a locked, audited transaction with a fresh preview
check. Preserve IDs and provenance, retain the strongest stop condition, retain
existing explicit assignments or require conflict resolution, and do not replay
AI jobs, extraction or old webhooks. Take a reversible mapping snapshot first.

Rollback of Beintaskbot code is schema compatible; additional Connect columns
can remain. Do not drop columns, erase identity conflicts, or revert to routing
that ignores proven aliases while continuing affected automatic processing.
Keep the compatibility guard deployed until any Connect rollback is evaluated.
No flags, production records, AI modes or WhatsApp sessions were changed by this work.

## Validation and limits

Connect: full native PostgreSQL/mocked provider suite, build and additive migration.
Beintaskbot: targeted PostgreSQL/WASM and native multiprocess suites; no paid AI.
Chromium component smoke: RU/AZ/EN, light/dark, 1440x900 and 390x844; complete
32x32 header images and no horizontal overflow in all 12 combinations.
The visual fixture uses real application markup/assets and synthetic API data;
production interactions were read-only.

Two baseline test contracts were corrected: the assignment audit action was
renamed to ai_conversation_agent_configured; the legacy extraction fixture now
explicitly enables tenant extraction, with an additional disabled-tenant guard
test. The old pilot-send expectation is still rejected by current admission;
this was reproduced on baseline, without bypassing any security policy. Windows-only skips
are not counted as passes. Polling remains bounded at 30 seconds; no new realtime
transport was introduced. Avatar refresh remains subject to the existing saved
Connect copy/one-time source-fetch policy, rather than periodic Evolution calls.

## Owner reconciliation of stopped direct conversations (Issue 248)

The existing physical reconciliation now snapshots both routes, all messages,
conversation states, terminal AI jobs, drafts, read cursors and identity aliases.
Preview includes counts and a full-row digest; changes invalidate approval.
Fixed-order PostgreSQL table locks (5-second lock timeout) fence concurrent writes
during the short transaction. Audit, backup, receipt and migration commit together.
Request UUID reuse requires identical parameters. No worker, provider or sender is
called by this path; jobs keep their terminal state, revision and evidence.

Only stopped states are supported: Human takes precedence over Paused, assignments
must agree, and the state revision increases without resuming AI. Both originals
are retained for rollback. Read-cursor collisions retain the earlier cursor, so
unread messages cannot silently become read. Draft UUIDs, run/source evidence,
message UUIDs, raw payloads, media and timestamps remain unchanged. Only routing
fields move. In-flight/unknown jobs or sends, unscoped legacy states, conflicting
assignments, unsupported case/contact/CRM/virtual-state dependencies and unknown
foreign keys block reconciliation. Nothing clears those guards automatically.

Version-2 rollback compares the complete current scoped dataset with the saved
post-merge snapshot, then restores every original row and verifies equality before
commit. A later message, read, edit, new dependency or schema mismatch blocks
rollback rather than overwriting it. Legacy backups require separate review.
Old source links resolve through the exact active backup only for history reads,
with tenant/channel/lead matching; mutation routes do not gain this redirect.
Backups remain private PostgreSQL records, never public media or audit payloads.
