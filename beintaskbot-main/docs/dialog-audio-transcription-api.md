# Single-message audio transcription API (proposed backend/UI contract)

`/api/platform/ai/dialogs/audio-transcription`, authenticated existing Dialogs
membership and Chat AI permission. Connect routes only. No main LLM, sends,
agent/configuration changes, automatic retry or comparison experiment access.

## Identity

The UI sends the CURRENT viewed `channel_id`/`conversation_id`, selected DTO
`id` as `message_row_id`, and selected DTO `external_id` as `message_id`
(`connect:<uuid>`). All four identity fields are required. Server proves this
exact row belongs to the authorized view/history, then derives its physical
source route; duplicated external IDs across channels cannot choose another file.
Direct history uses existing audited equivalence members; group history uses
exact group route membership. No client source lead/URL is authority. The assigned
agent always belongs to the current viewed dialogue, never an old source chat.

GET uses `X-Tenant-Id` and `X-User-Id`. POST includes `expected_tenant_id` and
`expected_user_id`, JSON content type and the existing same-origin requirement.
No client model, media URL, prompt, language, agent ID or permission flag is accepted.

## GET preview/status

Query: the four identity fields above. Optional opaque `history_before` requests an older page; default history page
limit 20. Optional `idempotency_key` polls the same actor/source operation
without dispatch. The returned `history_next_cursor` binds actor/route and the stable
(created_at, attempt_id) ordering, including tied generation 0 imports. GET creates no attempt, chat run, paid
reservation or preview row, and does not download media or call a provider.

Response wrapper: `{ "success": true, "result": { ... } }`.

```json
{
  "source": {
    "channel_id": "<uuid>",
    "conversation_id": "<selected-route>",
    "message_id": "connect:<uuid>",
    "message_row_id": "<selected-DTO-id>",
    "source_version": "<opaque-current-source-hash>"
  },
  "state": "ready",
  "generation": 0,
  "effective_model": "<server-selected-ASR>",
  "price": {
    "currency": "USD",
    "reserve_microusd": 30000,
    "max_duration_seconds": 300,
    "accounting": "fixed_quote"
  },
  "allowed_actions": ["transcribe"],
  "blocked_reason": null,
  "blocked_message": null,
  "cost_token": "<opaque-server-token>",
  "cost_token_expires_at": "<UTC>",
  "current_attempt": null,
  "requested_attempt": null,
  "latest_success": null,
  "history": [],
  "history_next_cursor": null,
  "message": { "id": "<existing-DTO-id>", "external_id": "connect:<uuid>" }
}
```

`price.reserve_microusd` is the server quote/max cost for the confirmation,
not an already-created reservation or an OpenAI invoice. The UI renders
"up to $0.03" for a 30000 quote. Actual monetary accounting retains the existing
conditional-cap semantics; GET performs no reservation.

`state`: `ready`, `completed`, `pending`, `unknown`, or `blocked`.
`latest_success` is the currently applicable successful transcript, independently
of a newer pending/unknown attempt. It contains `attempt_id`, `origin`,
`source_version`, `source_current`, `model` (nullable for old evidence), `text`,
`created_at`. Imported old/unverified evidence is labelled, never asserted to
belong to a newer source. History entries also contain `generation`, `state`,
`code`, `published`, and safe accounting/usage metadata. Full redacted text is
retained up to the provider's 60000-character limit; existing LLM context limits
remain separate. No raw provider response, credential, raw media URL or webhook
payload is returned. `message` is the existing safe Dialogs message DTO, not a
replacement HTML fragment; UI patches only this selected message.

## POST one explicit attempt

```json
{
  "channel_id": "<uuid>",
  "conversation_id": "<selected-route>",
  "message_id": "connect:<uuid>",
  "message_row_id": "<selected-DTO-id>",
  "source_version": "<GET value>",
  "action": "transcribe",
  "cost_token": "<GET value>",
  "idempotency_key": "<fresh UUID for this confirmed click>",
  "expected_tenant_id": "<current tenant>",
  "expected_user_id": "<current user>"
}
```

The ordinary UI shows one cost confirmation, then POST. `action` is `transcribe`
or `retranscribe`. Initial transcribe reuses an existing verified completed result
without a paid call; retranscribe permits a new paid attempt only after a
completed/proven safe previous outcome and a fresh confirmation/token.

The cost token expires after 10 minutes and binds actual tenant/actor, exact source
version, current assignment/configuration, effective model/request policy, quote,
action eligibility and prior head generation. Changing a UUID alone cannot repeat
a token after an admitted attempt, even if it already completed. The same
idempotency key and same input return the same durable attempt without dispatch;
key reuse for another input/actor is409.

POST returns the same result shape as GET, with `requested_attempt` identifying
the exact stable operation for this idempotency key. `current_attempt` is the
global source head and may already be newer. A duplicate must return its original
requested attempt even after a later generation completed; UI patches the card
from `message`/`latest_success`, not from an older requested attempt. GET with
`idempotency_key` returns that same requested operation for its actor/source.
A transcribe click reusing completed text records a durable zero-dispatch reuse
operation (no ASR/chat slot/monetary reservation) so its identity/text remain
stable when a later retranscription succeeds.

POST returns the same result shape as GET. An attempt is `reserved`, `sending`,
`completed`, `failed_safe`, or `unknown`; public state combines reserved/sending
as pending. A crashed dispatched attempt remains unknown/non-retryable. Prior
success remains visible and labelled previous if a newer attempt is unknown.
No polling request or reload initiates work; GET polls status read-only.

403: access/origin denied. 409: stale source/token/assignment/price/generation,
paid pending/unknown, or unavailable adapter. 429: existing daily/monetary cap.
Errors use `{ "success": false, "error": "<safe explanation>", "code": "<stable-code>" }`.
GET resource conflicts include `scope_verified: true` only after the expected
tenant/user headers were validated; identity mismatches never include this flag.
`blocked_message` is an optional safe server explanation for a blocked preview.

## Durable schema and coordination

Two small tables: source heads keyed by `(tenant_id, message_row_id)`
with generation/current-success/active-attempt pointers; immutable attempt/history
rows keyed by attempt UUID, source/version, generation, origin, actor,
idempotency/input/token hashes, frozen agent/config/ASR binding, state,
call-key/quote, original/upload hashes, safe transcript/usage and publication
status. Old legacy/evidence successes are archived on first POST, not destroyed.
Manual generations have a source-unique constraint; tenant request UUID is unique.

Admission uses the existing tenant Chat AI and voice-source advisory locks.
One ASR-only chat run reserves exactly one daily call with a prepaid media slot;
existing company/Copilot/dialog daily caps and monetary cap semantics apply.
There is no text monetary reservation or Copilot conversation turn. The ledger
records the actual ASR model; #408's scoped adapter/pricing remains applicable.
Provider dispatch is durably marked before HTTP and rechecks source/assignment
at the final boundary. Source/model changes after generation retain paid history
but withhold publication to the current message.

New attempts never reset the model-independent episode flight or old unknown
receipt. Small table-exists/read-only pending/unknown guards coordinate ordinary
source claims and artifact-cache ASR claims with explicit attempts. Genuine
pending/paid-unknown outcomes block another dispatch across UUID/token changes;
an old Copilot error summary alone is not evidence of a paid unknown.

An exact-source explicit latest-success projection takes precedence only for
this new result in Dialogs/common evidence reads; raw customer body/webhook and
old receipts remain retained. A failed/new unknown attempt does not replace the
previous successful transcript. No background processing or mass jobs are added.

Proposed SQL shape (identifiers/constraint details may be tightened in review):

```sql
saas_dialog_audio_sources (
  tenant_id uuid, message_row_id uuid, channel_id uuid, external_id text,
  generation bigint not null default 0,
  active_attempt_id uuid, current_success_id uuid,
  primary key (tenant_id,message_row_id)
)
saas_dialog_audio_attempts (
  tenant_id uuid, id uuid, message_row_id uuid, channel_id uuid, external_id text,
  view_channel_id uuid, view_conversation_id text,
  source_version text, generation bigint, origin text,
  actor_id bigint, idempotency_key uuid, input_hash text, cost_token_hash text,
  agent_id uuid, agent_binding text, config_version text,
  model text, request_policy jsonb, quote_microusd bigint, call_key text,
  state text, code text, dispatched_at timestamptz,
  original_sha256 text, upload_sha256 text, upload_mime text,
  transcript text, usage jsonb, published boolean,
  created_at timestamptz, completed_at timestamptz,
  primary key (tenant_id,id)
)
```

Manual rows have physical-row-unique positive generations and tenant-unique request
UUIDs; imported previous-success rows have generation 0 and deterministic
source/version/content-bound IDs, so repeated imports cannot duplicate history.
No imported row consumes a paid/daily slot. Scope is the authorized selected
Connect audio/voice message, incoming or outgoing; no caller-supplied media.
