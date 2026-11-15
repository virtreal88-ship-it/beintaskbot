# Explicit dialog lifecycle: rollout and review

**Draft release gates:** full native PostgreSQL concurrency CI, final permission
review and the explicit episode AI/publication path must pass before activation.
The draft PR itself does not enable production behavior or apply its schema.

Status: prepared behind `TENANT_DIALOG_LIFECYCLE_ENABLED`, default **off**.
Do not enable in production until this change's full CI (including native PostgreSQL
concurrency tests), access-path review and the additive schema migration are approved.
No production migration is executed by serving a request or importing this module.

## Behavior

- Existing tenant/source view and update grants remain prerequisites. There is no
  owner/admin exception to operator ownership.
- A route with no workflow is initially waiting. Accept creates an episode. Only
  the accepting operator can read/send on its exact source and canonical dialog.
- Transfer requires a currently active employee with source view and update access.
  The same episode continues; the previous operator immediately loses access.
- Close records its end, removes it from the active queue, and keeps a readonly
  detail for the last operator's explicit **Send to cases** action.
- A newly inserted, verified customer incoming event with a provider timestamp
  later than close returns the route to the waiting queue. Echoes, webhook retries,
  historical events, unknown timestamps and events preceding close do not reopen.
- **Does not require a reply** is a manual decision on a specific latest waiting
  incoming message. It dismisses that queue item without creating an episode,
  sending a message or creating a case. A newer incoming reopens it. A stale
  message id/revision is rejected instead of silently dismissing another question.
- AI is gated at admission and final dispatch for the managed route. Accept does
  not change the existing independent AI human/paused setting or another source.
  Future waiting episodes respect the original AI configuration.
- A known outgoing dispatch is bound before its route lock is released; its later
  receipt remains in that episode even if the operator has already closed it.
  Pending or uncertain outgoing sends prevent an incomplete case snapshot.
- Cases are not created by accepting or completing. Explicit case submission pins
  durable message-to-episode membership on the same verified route,
  including approved aliases. It creates a separate completed manual case, not an
  upsert into an unrelated old open ticket. No automatic summary, publishing,
  paid AI generation or developer dispatch is triggered.

## Additive migration

`tenant_dialog_lifecycle.migrate(cursor)` creates five new tables and one index:
`saas_dialog_episodes`, `saas_dialog_workflows`, `saas_dialog_lifecycle_receipts`,
`saas_dialog_episode_messages`, `saas_dialog_episode_sends`.
Foreign keys retain tenant, employee and Connect route integrity. No old tickets,
messages, rights, AI assignments or provider records are rewritten or deleted.

Run the migration with the application's authorized database connection in a
controlled deployment transaction, after migration approval; commit only on
success. Then verify table/constraint presence. Keep `TENANT_DIALOG_ACCEPT_ENABLED=false`
while rolling out `TENANT_DIALOG_LIFECYCLE_ENABLED=true` to every process. No
exclusive workflow may be created during this guard-only phase. Confirm all
serving replicas and workers use the guard-enabled revision, then enable
`TENANT_DIALOG_ACCEPT_ENABLED=true`. Old replicas in this second phase already
enforce ownership and merely reject new claims until replaced. Never mix legacy
unguarded replicas with accepting replicas. A fully drained maintenance window
is the alternative.
Deploying code with the flag off does not create these tables or change behavior.
If the flag is mistakenly enabled before migration, operations fail closed rather
than falling back to unowned source-wide access.

There is intentionally no automatic ownership backfill: old history cannot prove
which employee accepted an episode. Existing dialogs start as waiting. Existing
cases remain stored and use their existing case permissions; new dialog/attachment
route reads additionally enforce current ownership. Past messages/notifications
already delivered to a device cannot be recalled by this change.

## Operational risks requiring verification

- Taking away the assigned employee's source access leaves an accepted route
  inaccessible (fail-closed). No owner takeover exception or forced reassignment
  is invented. An explicit recovery workflow is a separate product decision.
- A provider without reliable incoming timestamps cannot automatically reopen a
  closed route. Connector event contracts must be verified in smoke testing.
- Late messages with provider time before close remain history, not a new episode.
- New incoming resets route visibility according to existing source authorization;
  participants can then see the authorized conversation history, not just one
  episode. This does not grant access to the other WhatsApp source.
- Closing while a provider call is already in progress waits on the same route
  lock. A message already dispatched cannot be unsent.
- Disabling the flag restores the old source-wide behavior. Do not treat a blind
  flag rollback as a safe way to preserve exclusive ownership. Prefer forward
  correction or temporarily disable dialog access through an authorized process.
- The preexisting AI group coordination mechanism can deduplicate group events
  across sources. This patch must not weaken that mechanism or accidentally use
  its group-wide assigned_to field as human ownership.

## Checks

Local tests use synthetic PGlite/PostgreSQL data, never production or WhatsApp.
PGlite proves SQL/schema/idempotency but cannot prove concurrent transaction timing.
Native multiprocessing tests are mandatory before enabling the feature.
Run the lifecycle tests plus the existing scoped dialogs, channel/group isolation,
attachments, chat send, consultations, notifications and support suites. Browser
checks must cover stale open tabs, repeated actions, transfer, clear-on-denial,
mobile/desktop, and legacy behavior with the rollout flag off.

## Full episode AI coverage and publication limits

The internal episode submission uses the existing analyzer maximum of 80 messages
and 12,000 characters of complete text (plus its existing token/budget checks).
It does not inherit the separate public 30-message selection limit. Neither a
message nor the episode is silently truncated. If the complete pinned evidence
cannot fit, preflight rejects before any model call or paid reservation. The full
episode remains stored. This is an explicit limitation, not a successful analysis.

To support larger episodes, a later bounded workflow must first freeze the full
message manifest and media coverage, price/reserve a total request budget, then
extract evidence in deterministic bounded chunks and synthesize ONE case. Every
chunk needs a durable completion receipt and the final synthesis must cover all
manifest IDs. Any missing/unknown chunk stops publication and cannot silently
retry spending. No per-chunk cases or automatic analysis of unrelated history.
The current patch does not implement or enable this multi-call workflow.

Automatic publication is more conservative than draft generation: original actor
needs existing publication authority, the exact assigned agent/version must still
match, customer confirmation and current source evidence must pass, and procedural
steps must literally occur in pinned operator text. Attachments in the no-paid-media
path, unsupported/paraphrased procedures or insufficient authority leave a draft
with a reason. This prevents a model's suitability assertion from becoming an
authorization or invented instruction. Existing redaction and audit remain in force.
