# Shared Copilot context: evidence and release gates

This is an isolated investigation, not an adapter authorized for production.
No production routes, database schema, credentials or provider calls changed.

## Experimental shared-history change

`tenant_operator_copilot._read_history` fetches the customer history and the
private `saas_operator_copilot_threads` row by tenant, employee, channel and
conversation, and filters turns by the assigned-agent binding. The isolated
PostgreSQL test verifies that query behavior for synthetic rows. It does not
prove real authorization: `tenant_operator_access.access` and `assert_route`,
AI consent, config/version checks and `_assigned_context` still must be called
by the real admission and at all appropriate subsequent boundaries.

The base text implementation released its row lock before provider I/O and
overwrote the thread using an earlier snapshot. In this local experimental branch,
`tenant_operator_copilot_threads.append` locks/creates the current row and appends
to its current contents; `_finish` uses it without holding a database transaction
over provider I/O. `clear_revision` on an empty thread fences responses that
started before the user cleared history. Scoped PostgreSQL tests cover an
intervening voice-shaped row and two simultaneous first writers.

This is a proposed change, **not yet reviewed or production-ready**. The helper
does not authorize callers or enforce voice session/turn idempotency, per-turn
ordering or transcript correctness. There is no voice writer yet. Every future
writer must use this protocol and prove those controls in its own transaction.
The existing 20-turn retention limit can still remove older messages. Test clear,
replay, migrations, rolling versions and legacy text flows before integration.

The local `_draft_context` now passes allowlisted voice session UUID, sequence,
partial status and quality through to text context and labels incomplete
evidence in instructions. Unknown/unavailable transcript text is omitted.
The meaning of `complete` versus `partial`, actual audio playback alignment and
voice-to-text persistence remain to be designed and tested; never fabricate a
heard prefix from an audio timestamp when alignment is unavailable.

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
context replay on each response, output, separately billed input transcription
and reconnect before admitting sessions. Do not assume a cache hit. A duration
limit alone does not bound the full token-based cost.
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
tests create and drop a random schema. A passing synthetic test is not evidence
that a voice writer, real authorization, provider or production rollout exists.
