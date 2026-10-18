# Support cases: read-only production check, 2026-10-18

Deployed worker: abee439d, deployment 3d162c9c-e5e2-40b6-a3e4-e3cb7abece35.
Evidence: read-only SQL (default_transaction_read_only=on, statement_timeout=10000),
saas_knowledge_batches/items, saas_knowledge_extractions, saas_ai_agent_runs/token_usage,
saas_crm_messages counts. No message bodies, credentials or model output were retrieved.

Three batch receipts, Asia/Baku:
- 05:16:00: period [17 Oct 00:00, 19 Oct 00:00), channel history, 6 text messages
  from 7 stored messages. New completed extraction, 399 input / 5 output tokens, 0 cards.
- 05:17:18: period [10 Oct 00:00, 19 Oct 00:00), channel history, 7 text messages
  from 8 stored messages. New completed extraction, 423 input / 6 output tokens, 0 cards.
- 05:17:34: period [10 Oct 00:00, 20 Oct 00:00), channel history, same 7 messages;
  reused the preceding extraction, no additional model call.

Both extractions and runs completed with empty error codes. Under deployed parse/finish,
completed extraction with zero persisted cards means an empty validated model card list;
there is no candidate rejection/filter stage after that list other than exact insert dedup.
Raw model output / pre-validation telemetry are not retained, so there is no independent
raw-response record. The fresh extractions had no candidates to deduplicate.

All three batch params specify channel, not unified_group. Both routes are groups.
Same-group history links two channels: 27 stored / 26 nonempty-text messages in the first
period; 29 stored / 27 nonempty-text messages in the second. These are BEFORE unified
history dedup, not a promised AI input count. No continuations or route truncation.
The recorded periods/modes differ from the user-reported 16-18 Oct unified request;
there is no receipt of that exact request among the three batches. Why UI selection
and persisted params differ is not established. Do not claim a verified absence of cases.

Independent reproducible code defects: prompt permitted omitting anything without a
reusable solution; strict card validation rejected empty solution steps; unconfirmed
cards were inserted as pending_review instead of needs_clarification. Local fix explicitly
includes unresolved problems, allows empty steps only for unconfirmed/unknown outcomes,
and assigns needs_clarification after evidence checks. Approval still requires confirmed
outcome, evidence and nonempty solution. Existing paid receipts remain immutable and
idempotently reused: this patch does not silently re-run or rebill old requests.

Suggested UI message: “AI returned 0 cards from N text messages. History: selected channel
/ unified group; period: actual local bounds. This does not prove there are no support
problems. Messages without saved text were not analysed.” Show reused-result indication
and distinguish model errors from empty results; verify actual submitted mode/date before
any newly authorized paid analysis. No deployment, migration or live mutation performed.

Verification: 55 targeted candidate/batch tests passed in 20.87s against isolated
PGlite with mocked AI. Includes four new cases: unresolved with/without suggested
steps, rejection of confirmed-without-steps, empty result and paid-call-free replay.
No live model call or production writes were made during this investigation.
