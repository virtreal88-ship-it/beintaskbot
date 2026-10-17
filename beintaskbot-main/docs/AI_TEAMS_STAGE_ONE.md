# AI teams: first stage

Branch: `codex/ai-teams-internal-agents`. Based on production `7858d6e8`.

## Architecture

Uses existing agents, role (`primary` / `expert`), expert_policy, call_expert triggers,
expert receipts, token accounting and worker send fences. No second agent system.
Existing agents and legacy single-expert policies retain their behavior.

A primary policy accepts `expert_agent_ids` (or legacy `expert_agent_id`). A trigger
may select a subset in `condition.expert_agent_ids`; otherwise it invokes the team
sequentially. Each primary run/specialist pair has one durable receipt. Running or
uncertain timed-out calls prevent further calls. Only the primary can produce a
customer reply; specialist results remain structured advisory evidence.

New internal policies use `internal: true`, `allowed_primary_ids`, `tools`,
`max_calls`, `timeout_seconds`, `max_context_chars`, and `max_tokens`. New internal
agents default to an empty allowlist. Existing experts with an empty policy keep
legacy explicit primary binding; explicit internal policies cannot be cleared to
bypass permission checks. Internal agents cannot have client scopes or triggers,
external sending, recursive delegation, or automatic mode.

Effective limits/tools are the intersection/minimum of primary and internal policy.
All specialists share primary-run token and wall-clock budgets; platform/company
limits still apply. Version, tenant, schedule, permissions, Human Mode and group
revision/event fences are rechecked. Completed team evidence is discarded if an
earlier helper is revoked during a later call.

Activation is serialized per tenant. Scope conflicts include channels and UTC
schedule intersections (including overnight shifts and DST). Explicit existing
conversation assignments and group priorities retain their authority. Ambiguous
runtime candidates fail closed. Existing enabled agents are not modified.

## Migration and release boundary

Existing expert schema initialization adds unique index
`saas_ai_expert_parent_specialist` on `(tenant_id,parent_run_id,expert_agent_id)`
and drops only the old `(tenant_id,parent_run_id)` unique constraint
`saas_ai_expert_tasks_tenant_id_parent_run_id_key`.
No data deletion, type backfill, new service or binary storage.
This migration has been exercised on disposable local PostgreSQL, not production.

Upgrade all workers before configuring multiple helpers. After multi-helper
receipts exist, old parent-only uniqueness cannot be restored without conflict.
Do not delete receipts or automatically roll back old workers; retain the new
schema and use a reviewed forward fix. Production rollout is outside this task.

## UI and integration

Existing /app agent editor: type, internal helper selection, per-helper allowed
primaries, tools and limits. Existing model, knowledge and schedule editors remain.
AI Teams displays named relationships; triggers can target specific helpers.
RU/AZ/EN labels; no technical agent IDs in visible controls. /webapp untouched.

## Targeted verification

Python suites: test_ai_teams, test_ai_teams_postgres, test_tenant_ai_expert,
test_coordination_expert_integration, test_group_coordination,
test_tenant_agent_library, test_ai_agent_test_mode,
test_tenant_ai_agent_connect_admission, test_tenant_kommo.
Real local PostgreSQL races verify one conflicting activation winner and one
expert receipt under simultaneous admission. Mock provider/transport tests cover
sequential helpers, budgets, permissions, revoked evidence, group takeover,
Pause/Resume, Manual/Automatic, stale events and duplicate receipts.
UI suites: tenant_ai_expert_ui, tenant_agent_library_ui, group_coordination_ui.

## Limits

- Sequential explicit call_expert triggers; no autonomous model tool-selection loop.
- Existing diagnostic adapters still report integration_unavailable; selecting an
  allowed tool does not create a new integration or grant arbitrary SQL access.
- Existing conservative lifetime per-dialogue call caps are preserved.
- Activation schedule comparison covers 400 days. Runtime ambiguity still blocks
  admission if future timezone rules or schedules invalidate that comparison.
- No real customer messages, production migration or browser/live WhatsApp test.
- Company agent-management rights continue to govern team configuration.
