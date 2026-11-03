# Shared Copilot context: evidence and release gates

This is an isolated investigation, not an adapter authorized for production.
No production routes, database schema, credentials or provider calls changed.

## Shared history cannot yet be written safely

`tenant_operator_copilot._read_history` fetches the customer history and the
private `saas_operator_copilot_threads` row by tenant, employee, channel and
conversation, and filters turns by the assigned-agent binding. The isolated
PostgreSQL test verifies that query behavior for synthetic rows. It does not
prove real authorization: `tenant_operator_access.access` and `assert_route`,
AI consent, config/version checks and `_assigned_context` still must be called
by the real admission and at all appropriate subsequent boundaries.

The text request releases its `FOR UPDATE` thread lock when it commits before
provider I/O (`tenant_operator_copilot.py`, `_read_history` and `run`). On
completion `_finish` computes `new_turns` from that earlier snapshot and
unconditionally overwrites the row through `ON CONFLICT ... turns=EXCLUDED.turns`.
Consequently, a voice writer can commit after the snapshot and be erased by the
text completion. A voice-only lock, one-time row lock or independently atomic
voice UPDATE does not protect against the unchanged text writer. The isolated
PostgreSQL test invokes actual `_finish` with synthetic authorized dependencies,
and asserts the currently missing voice turn. Its success reproduces the defect;
it is not a safety pass.

The same problem applies in the opposite direction when a voice session saves
its own stale snapshot. Two text requests can also race. No shared write adapter
was added because it could not meet the requested serialization guarantee.
Before integration, factor a single shared append/clear protocol used by every
text and voice writer. It must lock the exact thread even when the row is absent,
re-read current history under that lock or use equivalent conditional CAS, and
atomically verify tenant/member/dialog/agent binding, current config version,
session/revision, turn idempotency and ordering before committing. Clear and
assignment changes must be fenced too. Do not hold a DB transaction open over
provider I/O. Define semantics for concurrent prompts and responses rather than
claiming chronological order from commit time alone.

The context constructor `_draft_context` currently projects each prior turn to
`role`, `text`, `agent_binding`. A test invokes the real constructor with a
synthetic partial voice turn and demonstrates that `voice_session_id` and
`transcript_status` are stripped. Merely persisting these fields is therefore
insufficient: text continuation would see the partial content without its
qualification. Reuse a common projection which preserves explicit ASR quality,
partial/interrupted state and ordered session/turn IDs; omit text whose heard
prefix is not established. Update the prompt rules and retention tests together.
Do not fabricate a prefix from an audio timestamp when alignment is unavailable.

## Required regression matrix

On disposable real PostgreSQL, use two or more independent connections and
barriers around the actual shared text and voice methods:

1. Text snapshot → voice commit → text finish, and the reverse order. Both
   committed results survive once, in the specified order, for existing and
   initially absent thread rows.
2. Two concurrent text requests, two voice sessions, duplicate/reordered events,
   restart/replay, clear, simultaneous agent reassignment and config/consent
   revocation. Verify scope, monotonic sequence, no cross-tenant disclosure and
   no stale delivery or resurrection after clear.
3. Complete, missing, failed, partial and interrupted ASR/transcripts pass from
   voice to text and text to voice without falsely claiming exactness. Persist
   no raw voice audio; prove duplicate session/turn IDs are idempotent.
4. Reuse actual Copilot customer history, agent instructions, approved knowledge,
   media evidence guards and existing private thread; prove no tool, CRM write or
   customer send is exposed. Add real access/tenant/role tests, not just mocks.

## The shared $5 test ceiling and forced shutdown

Owner approval is up to $5 **in total across all executors**. It is not permission
for independent live calls: a common ledger and a proven bound are still missing.
Before paid testing, a durable shared transaction must reserve a conservative
maximum cost *before* opening each provider session, check `spent + held + new
hold <= $5`, bind the hold to the exact executor, scope, session and versioned
tariff/model, and be idempotent for retries. Every concurrent executor must use
the same authority; local counters, an in-memory mutex or separate per-agent $5
allowances do not qualify. Unknown or missing provider results consume the full
hold until independently reconciled; do not release on timeout or restart.

Establish a provider-supported worst-case charge bound for every permitted input,
context replay, output, transcription and reconnect before admitting sessions.
Metering may arrive late or be absent and usage-based charges can overshoot a
client-side threshold. If a hard bound cannot be established from provider terms
and enforced server-side, the strict ceiling cannot be guaranteed and paid tests
remain blocked. Changes in tariffs/model versions invalidate prior quotations.

The provider transport must be owned by a server process; enforce maximum active
and idle times using independent monotonic timers even with no incoming events,
and stop local capture/playback and close upstream on end, network loss, revoked
rights, timeout and logout. A background reconciler must fence orphan sessions
after crash, and provider-specific termination/expiry must be verified rather
than assumed. The current mock only calls `tick` when its caller asks and does
not terminate a provider. Until actual upstream termination and billing bounds
are demonstrated, it is not evidence of cost control.

## How to run the isolated evidence

`python -m unittest discover -s experiments/copilot_voice_poc -v` runs the pure
mock cases. To run the PostgreSQL cases set `COPILOT_POC_POSTGRES_URL` to a
throwaway localhost database named with the `copilot_voice_poc` prefix. The
tests create and drop a random schema. Two tests explicitly assert the current
lost-turn and stripped-metadata defects: their successful reproduction means
those requirements remain unresolved, not that the integration is safe.
