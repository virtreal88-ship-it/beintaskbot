# Reliable Connect dialogue assignment

## Root cause

Connect inbound handling first evaluated the company/server/auto-send admission gate. When that gate was closed, the event and CRM message were retained but the handler returned before selecting or binding a scoped agent. Binding only happened later in the queue-admission path. No retry ran when an agent or company setting changed, or when Connect was configured again, so an eligible route could remain unassigned indefinitely until an owner bulk-assigned it.

This was an assignment/execution coupling bug. Runtime enablement, company mode, auto-send, emergency stop, schedule, and Automatic/Manual mode govern execution; they do not establish which enabled Primary agent owns an unambiguous, channel-scoped conversation.

## Change

- A channel-local, transaction-locked reconciler binds only one enabled Primary agent whose Connect scope includes that exact channel. Multiple candidates remain unresolved with `automatic_assignment_agent_selection_required`; there is no query-order fallback or inferred priority.
- Reconciliation runs for a blocked inbound event, on company settings and agent saves, and after Connect channel configuration/reconnection. It retries previously marked automatic no-agent states.
- New bindings remain `paused` with `automatic_assignment_pending`; no job, run, draft, activation, or outbound send is created by the reconciler. Existing AI execution admission remains behind its existing gates.
- Explicit owner/operator assignments, explicit no-agent choices (`agent_assignment_id`), Human/Pause states, existing agent bindings, tenant/channel mismatch, inactive/disconnected routes, and identity/legacy quarantine are preserved.
- Group conversations continue through their separate coordinator and explicit group-enabled scope. An operator's explicit unassignment/pause cannot be overwritten by the inbound retry.

## Verification

The focused isolated PGlite checks passed: 130 passed, 11 skipped, with the three existing admission subtests failing in the same way on the untouched base revision. The full suite (`pytest -q --ignore=test_api.py`) completed with 3,337 passed, 259 skipped, and 59 failures; selected failures were reproduced on the untouched base revision, so the repository is not green for release. Plain `pytest -q` cannot collect `test_api.py` because its external token is absent. Production inventory was not queried: the available prior Railway/source authentication denial was explicitly not to be retried. No bulk assignments or production changes were made.
