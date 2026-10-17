# AI Expert — stage 1

Base: origin/main `ffc5a677f01c0a2c1ecbf4f576036b5e5714567d`.
Branch: `codex/ai-expert-stage-one`. Separate worktree; no production changes, merge or deployment.

## Existing mechanisms reused

`tenant_ai_agents_store.py` and `tenant_ai_agents_policy.py` retain the agent CRUD, current membership/management grant checks, tenant-scoped model approval and setup-key idempotency. `tenant_ai_agent_run_store.py` retains the primary reservation, model billing, exact Connect/Kommo route, draft lifecycle and send gates. `tenant_chat_ai_provider.generate` is the only model client (store=false); optional output/HTTP timeout bounds preserve previous defaults. `knowledge_context` retains company-shared and agent-private source filtering. Existing Human/Pause, emergency stop, schedules and `tenant_ai_agent_actions.handoff` remain authoritative.

Audit found no production BeinSystems diagnostic API client. No production diagnostic capabilities are claimed.

## Agent configuration

Additive columns on existing `saas_ai_agents`:

- `role`: primary (default, preserving existing agents) or expert.
- `expert_policy`: {} or `{expert_agent_id, tools, max_calls, timeout_seconds, max_context_chars, max_tokens}` on a primary.

An expert must be Manual, have no channel scopes and cannot delegate. Changing the role of an enabled agent is rejected. An administrator explicitly enables an expert only to make it available internally; inbound queues and draft-send gates exclude expert roles. Configuration and runtime both verify the expert belongs to the same tenant and is enabled. No existing agent is activated or reconfigured by the migration.

Defaults: 2 calls per principal/provider/channel/conversation, 20 seconds, 6000 serialized context characters, 12000 total token budget per call. Bounds: calls 1–5, timeout 1–30 seconds, context 500–12000 characters, tokens 2048–20000. The conversation call cap is lifetime in stage 1, including failed attempts; it is not reset by Resume or a replay. No automatic retry of an uncertain paid request.

## Internal invocation contract

`await tenant_ai_expert_service.request_expert(job, parent_reservation, *, problem, lines, triggers, checks=(), previous_actions=())`

Only matching enabled primary triggers containing `call_expert` invoke it. Existing immediate handoff takes precedence. The worker invokes after reserving its own primary run. The caller is server code, not a public browser endpoint. The function checks the current leased job, parent run, primary/config/settings/dialog versions, tenant state, enabled flags, emergency stop, Human/Pause and both agent schedules.

Context contains tenant, provider, exact channel and conversation, short problem, selected recent text (up to 8 messages), bounded checks and previous action results, expert-scoped knowledge, allowlisted diagnostic results and explicit time/token bounds. Raw webhook payloads, media URLs, credentials and complete conversations are not copied. Full serialized context is bounded; UTF-8 byte accounting further trims evidence to fit the token envelope. Oversized configured instructions fail closed with `expert_context_budget` rather than silently discarding safety instructions.

Returns None (no matching action), or `{task_id?, status, result?, error_code?}`. Structured result has exactly:

`{cause, evidence[], confidence: low|medium|high, safe_actions[], additional_information, requires_human: boolean, ticket_summary}`.

Only validated concise conclusions are stored; raw model output, private reasoning and secret-like outputs are discarded. The primary receives an explicitly advisory object. A current result recommending a human uses the primary's existing handoff function; the expert never calls it. Stale results stop the primary run. Errors/timeouts allow the primary to continue with unavailable diagnostic evidence; normal draft/send gates remain authoritative.

## Diagnostic registry

`tenant_ai_expert_tools.diagnose` is the sole runtime registry entry. Registered IDs: system_status, application_errors, sync_status, database_metrics, cash_register, previous_incidents. All currently return `integration_unavailable`, read_only=true, empty evidence. No real diagnostic adapter is installed.

`mock_diagnose` uses synthetic fixtures only in tests and is not selected by production runtime or browser configuration. Real capabilities available now: existing model generation and tenant/agent-scoped knowledge search. No SQL, shell, arbitrary URLs, secrets, CRM mutation, Linear, webhook execution or WhatsApp send interface is exposed to the expert. Future adapters must derive customer/system identity from an authorized tenant route and retain read-only allowlisted parameters.

## Receipts, limits and failures

`saas_ai_expert_tasks` stores internal results keyed by tenant and task/run ID, unique per parent run, linked to the same tenant's agents/runs. It is not another agent store. Expert model calls use existing `saas_ai_agent_runs` and token usage; shared daily call admission includes them. The tenant quota advisory lock serializes admission. `reserved_tokens` on existing usage rows holds the full expert envelope until actual usage is reported; the shared token-cap query includes these reservations.

Known failures before any provider invocation release the token reservation and mark the run skipped. Invalid structured results still record provider usage. Missing usage, provider/network uncertainty or a process crash conservatively retain reserved tokens and count the call. There is no automatic refund/retry; an operator must reconcile uncertain usage before any manual correction. Daily usage follows the existing UTC accounting day.

The async deadline stops waiting for a result and marks the task timeout. It cannot cancel a potentially accepted HTTP request. The bounded request may finish in the background only to settle billing; late/stale results never resume the primary or send to a customer. No new worker/service is needed. Current-thread cancellation likewise cannot grant delivery authority.

## UI and APIs

Only `/app` changes. Existing agent editor has role, enabled same-tenant expert/model, trigger condition explanation, read-only tools and limits. Create an expert using the editor's “Создать AI-эксперта”, save its instructions/model/knowledge (new expert uploads are agent-private, existing company-shared sources remain available) and explicitly enable internal availability. Choose it on a primary and add an enabled trigger action “Вызвать AI-эксперта”. Company Active/Test and auto-send switches are unchanged.

Existing APIs only: agents list/create/update, agent triggers, knowledge, support ticket detail. The ticket detail query uses the authorized tenant plus exact Connect channel/conversation and displays internal structured diagnostics only to existing permitted support staff. No public expert execution endpoint. No `/webapp`, main-menu or production agent settings changes.

## Astra integration contract

Agreed with Astra (Multi-Agent Coordination):

`tenant_ai_group_coordination.authority(cur, tenant, channel, conversation, agent_id=None, expected_revision=None) -> {revision, primary_agent_id, sending_channel_id} | None`.

For a group, the server job must carry integer `payload.group_revision`; `group_event_key` stays in the verified server payload. Missing module/fence or rejected authority blocks expert admission. Capture the returned revision and check it again before generation and before accepting the result. Expert output never changes the coordinator revision/state, primary assignment or delivery authority. Primary send still needs Astra's final route/schedule/Human/revision check. Group functionality is not claimed integration-tested against Astra's unmerged implementation: regression uses the agreed interface stub.

Merge shared edits carefully: additive `tenant_ai_agents_schema`/store/policy, primary-only queue filtering, run-store role checks, provider optional parameters, shared token cap, two small primary worker hooks, ticket detail and agent-editor scripts/assets. Preserve Astra's coordination/Connect isolation/send fences, voice reservations and Pause/Resume behavior. Run integrated group tests for changed primary/channel/revision, missing fences and Human/Pause while expert runs, plus existing private-dialog/Kommo regressions. No foreign worktree/files were edited.

## Validation

Tests use isolated PostgreSQL/WASM (PGlite) and mocked model/transport providers; no production data or paid API calls. Coverage includes role/CRUD/setup retry, permissions, tenant/channel/expert ownership, scoped knowledge, structured context/result, secret rejection, billing/reservation and shared company caps, per-dialog caps, no cycles, timeout/late result/provider error, Human/Pause/emergency/archive, group revision contract, ticket history scope, and full Connect primary → expert → primary/handoff/takeover paths without Kommo. JavaScript covers editor binding/model/tools/defaults/escaping and existing creation library. Full regression counts are reported with the commit handoff.


Verified results:

- Full Python regression: 1622 passed, 20 skipped, 240 subtests passed (294.40 s).
- Final focused AI Expert suite: 33 passed (includes three additional provider/context-budget checks added after the full run started).
- JavaScript: all 62 regression suites passed; editor, existing creation library and support-inbox suites were rechecked after final UI refinements.
- `git diff --check` passed. No tracked `/webapp` changes. No real provider, diagnostic, CRM or production database calls were made by these checks.
