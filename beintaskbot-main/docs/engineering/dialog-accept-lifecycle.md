# Explicit dialog lifecycle: rollout and review

**Draft release gates:** full native PostgreSQL concurrency CI, final permission
review and the explicit episode AI/publication path must pass before activation.
The draft PR itself does not enable production behavior or apply its schema.

Status: tenant-scoped staged rollout under development. Activation is stored in
`saas_dialog_rollout`, never selected by a process environment flag. All production
migration, activation and data changes remain separately gated. No runtime DDL.
A tenant without an activated row retains legacy behavior; shadow also retains
legacy behavior and creates no episodes. Missing/unreadable rollout schema fails
closed. This revision therefore requires the additive schema before deployment.

## Behavior

- Existing tenant/source view and update grants remain prerequisites. There is no
  owner/admin exception to operator ownership.
- A route with no workflow is initially waiting. Accept creates an episode. Only
  the accepting operator can read/send on its exact source and canonical dialog.
- Only an employee explicitly marked Online can accept/start a new episode or receive a transfer. The server rechecks status while holding the employee row lock; a stale candidate list is rejected. Lunch/away does not revoke an already assigned episode.
- Transfer still requires current source view/update access and continues the same episode; once transfer commits, the previous operator immediately loses access.
- Close records its end, removes it from the active queue, and keeps a readonly
  detail for the last operator's explicit **Send to cases** action.
- The last operator may explicitly **Start dialogue** from a closed or dismissed
  detail when admission is enabled and their existing source update grant still
  applies. This creates a fresh accepted episode under the same route lock,
  preserves all prior episodes and sends no customer message. A changed revision,
  another operator's ownership, or a concurrent inbound event makes the action
  fail closed; an inbound event that wins first restores the waiting workflow.
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
  including approved aliases. Only this explicit action starts budgeted AI
  suitability analysis: unsuitable returns a reason without a case; suitable
  creates one knowledge candidate. Eligible automatic publication is subject to
  original-actor rights and the evidence gates below; otherwise it stays draft.
  No analysis or publication runs merely because an episode was closed, and no
  developer dispatch or unrelated old-ticket update is triggered.

## Additive migration

`tenant_dialog_lifecycle.migrate(cursor)` creates seven tables and two indexes:
`saas_dialog_rollout`, `saas_dialog_episodes`, `saas_dialog_workflows`,
`saas_dialog_lifecycle_receipts`, `saas_dialog_episode_messages`,
`saas_dialog_episode_sends`, and `saas_dialog_employee_presence` (plus its status lookup index). No existing messages, cases or rights are rewritten.

Required rollout order, after a verified backup/restore plan and explicit approval:

1. Check current main/worker revision, backup, transactions/locks, pending sends,
   connector contracts and all process/job inventories. Production has multiple
   tenants: activation is for one explicitly identified tenant, never global.
2. Apply only the additive migration in a short transaction with bounded
   `lock_timeout` and `statement_timeout`; abort and roll back on contention.
   Verify tables, constraints and indexes. Do not backfill ownership.
3. Deploy the compatible revision with every tenant still legacy (or explicitly
   shadow). Shadow is observation-only, not a half-enabled exclusive queue.
   Verify all serving processes, workers, draining instances and old jobs have
   finished. A running old binary does not acquire the new barrier: the new code
   CANNOT protect against its later use. Do not activate on heartbeat alone.
4. In a dedicated transaction call the authorized tenant transition with the
   expected generation, mode active and admission enabled together. It uses a
   nonblocking exclusive tenant advisory lock: an in-flight protected operation
   causes 409 with no change, rather than an unbounded wait. Retry only after the
   operation ends. No exclusive ownership is created with admission disabled.
5. Validate owner and employee views, first-wins claim, transfer, source isolation,
   incoming reopen, manual start from closed/dismissed, stale tabs, delivery
   reconciliation, manual cases and budgets.
   No real customer message or paid AI call is implied by this read-only smoke.

Normal protected operations hold the shared tenant transaction barrier through
final dispatch/read. Send receipts pin rollout generation; prepared legacy sends
cannot silently become sends in a new exclusive episode. This intentionally
requires a new preparation after a generation change, never an automatic resend.
The operational rollout must prohibit redeploy/rollback to pre-feature binaries
once any tenant has activated; an independent infrastructure minimum-version
fence is not implemented by this patch and would require separate authorization.

Activation is latched. Neither env flags nor changing admission to false restores
source-wide visibility. Emergency admission stop leaves existing ownership guards
on, and can prevent waiting dialogs from being accepted/sent: it is a controlled
stop, not a seamless rollback. Use a compatible forward fix. Never delete the
rollout/workflow tables or rows as a rollback strategy.

There is intentionally no automatic ownership backfill: old history cannot prove
which employee accepted an episode. Existing dialogs start as waiting. Existing
cases remain stored and use their existing case permissions; new dialog/attachment
route reads additionally enforce current ownership. Past messages/notifications
already delivered to a device cannot be recalled by this change.

## Operational risks requiring verification

- Taking away the assigned employee's source access leaves an accepted route
  inaccessible (fail-closed). No owner takeover exception or forced reassignment
  is invented. An explicit recovery workflow is a separate product decision.
- Pre-rollout uncertain send receipts have no proven episode. An unmatched late
  echo stays unbound; manual case analysis on that exact route is blocked before
  any AI cost until its uncertain send is reconciled. No retry or fabricated
  episode binding is performed by rollout.
- A provider without reliable incoming timestamps cannot automatically reopen a
  closed route. Connector event contracts must be verified in smoke testing.
- Late messages with provider time before close remain history, not a new episode.
- New incoming resets route visibility according to existing source authorization;
  participants can then see the authorized conversation history, not just one
  episode. This does not grant access to the other WhatsApp source.
- Closing while a provider call is already in progress waits on the same route
  lock. A message already dispatched cannot be unsent.
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
mobile/desktop, and legacy/shadow behavior for unactivated tenants.

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
