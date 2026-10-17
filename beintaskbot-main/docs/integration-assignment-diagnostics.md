# Dialogue assignment and diagnostics integration

Base: origin/main d3eb07968392acc8b796791e385a257ad3278ec6 (verified with fetch).
Branch: codex/integration-assignment-diagnostics.
Sources preserved through merges: f0d72fb4 and 4d520c81.

## Resolution
Two conflicts in tenant-operator-dialogs.js: kept both assignment and diagnostics styles; retained assignmentReason and diagnostics cleanup in closeDialog.
Additional integration fixes close stale diagnostics when opening a dialogue and classify expected Human/Pause skips as normal rather than processing failures.
Regression tests cover preserving a selected agent/form while opening diagnostics, read-only diagnostics, channel switching, separate Resume, stable assignment retry token, technical permission gating and normal Human Mode events.

## Checks
Targeted Python: 46 passed (assignment and AI channel state).
Integration Python: 60 passed (channel isolation, integrated Connect release, operator workspace, route catalogue).
Real local PostgreSQL multiprocess tests: 11 passed, covering assignment/inbound races, duplicate webhooks, channel-local takeover, handoffs, leases and send deduplication. Total: 117 Python tests passed. The first attempt hit local PostgreSQL startup recovery; all 11 passed after readiness was confirmed.
UI: 5 suites passed (assignment, diagnostic modal, unified AI state, operator workspace, dialogue regressions). RU/AZ/EN and responsive rules checked by these suites; no new live browser/mobile or WhatsApp send test.

## Schema and release boundaries
Only additive nullable saas_ai_conversation_state.agent_assignment_id UUID; migration preserves legacy/Kommo state and is covered by assignment tests. No new migration added during integration, no production migration executed.
Legacy ambiguity remains blocked. Assignment preserves Human/Pause, does not replay history or send drafts; Resume remains separate. Explicit agent binding is checked by enqueue and worker admission.
/webapp files unchanged relative to main. No production settings, channels, sessions, records or services changed. No production merge, push or deployment.
Rollback after using explicit assignments requires a worker that understands the binding; do not return to an older worker with affected dialogues active.
