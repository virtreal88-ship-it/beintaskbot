# AI Learning Loop V1

The AI Dashboard's **AI improvements** tab extends its existing Simulation issues,
review table and explicit recheck queue. It does not train a model or change agents,
knowledge, live dialogs, CRM, pilot scope, flags or monetary limits.

## Evidence and grouping

The read model derives groups automatically from recorded Simulation runs in the
Dashboard's explicit rolling 30-day window. It performs no model calls and queues
no work. Repeated reads/worker restarts do not create issue rows or duplicate counts.
Each run contributes once per exact category/reason/media group. Tenant ownership
is checked before reads and writes; agents/channels remain visible within a group.
Only examples (latest five) and comparisons (latest ten) are bounded; group counts
cover the full window before cursor pagination.

Recognized evidence includes empty retrieval, missing knowledge, unavailable Expert,
validator-rejected/conflicting evidence, unavailable media context, unsupported
operations, provider uncertainty, budget/price blocks, routing/identity context,
and other recorded terminal stop codes. Restrictions are review prompts, not proof
of a bug. `active_mode_stop_observed` never creates a finding. Neither an absent
Expert call nor a successful reply without references implies a defect.

V1 cannot prove a missing webhook/job, incomplete history, or a missing obligatory
trigger when the necessary telemetry was not recorded. It does not invent these
events. It does not rate semantic answer correctness or infer customer resolution.

## Review and recheck

Knowledge recommendations link to the Knowledge Center; agent recommendations link
to existing agent settings. Technical recommendations offer a copyable brief.
No GitHub task or PR is created by the product.

Group decisions use the existing `saas_ai_dashboard_issue_reviews` table with an
additive history column and a unique tenant/group index. Decisions also use the
existing tenant audit log. Existing per-run reviews remain available through the
unchanged ungrouped endpoint; they are not automatically promoted to judgments on
every occurrence in a group. New occurrences after a group was marked fixed reopen
its displayed status while retaining the decision history.

Review status changes never enqueue work. Recheck uses the existing run screen and
isolated Simulation executor, current configuration and shared monetary reservations.
The browser asks for explicit cost consent; the server rejects requests without
`confirm_paid_recheck: true`. Existing scope, budget and emergency-stop checks remain.
No historical messages are added to the ordinary live queue. Original runs are not
edited. Comparison reports `persists`, `not_observed`, or `inconclusive`; absence of
the original reason is not proof of a correct answer. Staff decisions are separate
from these technical comparisons.

## Authorized read API

Existing session-authenticated endpoints are reused (owner, or an admin with
`ai_agents_manage`; membership revocation is checked by the backend):

- `GET /api/platform/ai/simulation/issues?grouped=1&limit=30&cursor=...&status=...`
  returns grouped evidence, counts, review history, comparisons, a next cursor and
  aggregate monetary usage from the existing ledger. The money period is explicitly
  ledger lifetime, unlike the 30-day issue window. No configured cap is returned as
  `null`, never as a fictitious zero-dollar limit.
- Existing runs, run detail, events and dashboard routes retain their behavior.
  Run detail contains knowledge references, Expert results and token usage. Grouped
  output excludes customer messages, arbitrary error strings and credentials.

No new public endpoint or token/credential system is introduced. A Beintaskbot data
connector was not available in this Codex session. These APIs do not by themselves
connect ChatGPT or authorize unattended external access; no such connection is claimed.

## Validation and rollout

Use the isolated PGlite PostgreSQL runner and mocked provider in
`tests/test_ai_learning_loop.py` and `tests/test_tenant_support_inbox.py`.
`tests/ai-learning-smoke.cjs` exercises the built Dashboard in real Chromium using
synthetic API fixtures for RU/AZ/EN, light/dark and desktop/mobile. It is not a
production browser test. Build with the existing `npm run build --prefix ui`.

The migration retains all existing reviews/runs and broadens allowed review
statuses. Rollback the application commit if needed; do not remove the history
column, index or review data. No pilot activation or paid recheck is part of rollout.
Railway deployment and production verification remain with the owner.
