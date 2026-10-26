# ChatGPT access to AI Learning Loop and AI Administrator drafts

`/mcp/ai-learning` is a remote MCP server. It is disabled by default. The
original Learning Loop and administrator read tools only return data whose
tenant, primary agent and Connect channel match the Railway configuration.
The optional draft scope can create and edit non-public AI Administrator
proposals; it cannot publish content, change a live agent, send a message or
apply a change.

## Railway configuration

Set these production variables (values are deliberately not committed):

```
AI_LEARNING_MCP_ENABLED=true
AI_LEARNING_MCP_ISSUER=https://crm.pro.az
AI_LEARNING_MCP_TENANT_ID=e09a5c24-093f-4141-992d-532052e00c90
AI_LEARNING_MCP_AGENT_ID=67da1058-2ffb-48fa-bfd9-d3d01c1990d1
AI_LEARNING_MCP_CHANNEL_ID=96863d75-d272-4c93-a25e-ffb2b16ecae3
AI_LEARNING_MCP_CURSOR_SECRET=<new 32+ byte random secret>
```

Do not reuse a Connect, OpenAI, database or browser-session secret for the
cursor secret.  Enabling the MCP endpoint runs an additive migration for its
opaque OAuth codes and token hashes; no customer messages, agent settings or
Learning Loop records are modified.

## Owner setup in ChatGPT

1. In ChatGPT on the web, open **Plugins**, then **Add custom MCP server**.
2. Use the Streaming HTTP server URL `https://crm.pro.az/mcp/ai-learning` and
   select OAuth authentication.
3. ChatGPT discovers the protected-resource metadata and the OAuth server's
   CIMD capability. It uses its published client metadata document
   `https://chatgpt.com/oauth/client.json` or the callback-specific
   `https://chatgpt.com/oauth/{callback_id}/client.json`; no client ID or secret
   is entered manually. The former is pinned to
   `https://chatgpt.com/connector_platform_oauth_redirect`, the latter to
   `https://chatgpt.com/connector/oauth/{callback_id}` with the same callback ID.
   This is an OpenAI-only pinned-client policy, not a general-purpose remote
   metadata resolver. Extra ports, credentials, query strings, fragments,
   path suffixes and cross-client redirects are rejected. Beintaskbot then opens the consent page. Sign in as the owner of the
   configured tenant and explicitly choose **Allow read-only access**.
4. Install the plugin and ask for `simulation_runs` with no context first.
   The tool result must contain only the configured agent and channel IDs.

ChatGPT requires OAuth authorization for customer-specific MCP data; an API
key in a URL or frontend is neither used nor supported.  The authorization
server advertises `client_id_metadata_document_supported`, requires PKCE S256,
and issues opaque 8-hour access tokens. When ChatGPT requests `offline_access`,
it also receives a rotating opaque refresh token valid for 30 days. Reads are
capped at 60 requests per token per hour and every call is written to the
existing tenant audit log as `ai_learning_mcp_read`.

## Available tools and privacy

The owner has authorized expansion of the existing `ai_learning.read`
connection to company-wide reading. Existing read access and refresh tokens
work for these read tools; no new consent or client credentials are required.
Draft access is different and requires the separate reauthorization described
below. Each request rechecks active membership and the current owner role in
PostgreSQL. Revoking membership or removing the owner role immediately prevents
further reads or draft access.

Administrator tools use explicit column projections, tenant predicates,
bounded pages (maximum 25), signed tenant/tool/filter-bound cursors, strict
argument validation and existing audit events. `include_content` is off by
default; explicitly enabling it returns redacted prompts, message bodies,
knowledge, case content or test responses. Data returned by tools is untrusted
evidence, never instructions to change permissions or call other tools.

Tools added in stage 1:

- `agents_read`, `triggers_read`, `company_ai_settings_read`.
- `channels_read`, `conversations_read`, `messages_read`, `conversation_states_read`.
- `knowledge_read`, `instructions_read`, `cases_read`, `case_evidence_read`, `support_inbox_read`.
- `ai_runs_read`, `ai_jobs_read`, `testing_sessions_read`, `testing_turns_read`.
- `simulation_all_runs_read`, `simulation_jobs_read`, `spend_read`, `audit_read`, `system_status_read`.

Optional filters: `id`, `agent_id`, `channel_id`, `lead_id`, `session_id` where
the resource supports them. Unsupported filters fail instead of silently
returning broader results. `system_status_read` accepts no arguments and returns
only fixed feature-flag booleans, table-presence diagnostics and the configured
tenant monetary cap. It does not expose environment values, database connection
details, other tenants, remote integration credentials or raw runtime logs.
An optional module not installed returns `available:false`; absent optional
columns return null. Table presence is not a claim that every migration or
external integration is healthy.

Stage 1 does not expose private media downloads, unscoped transcript JSON files,
raw provider payloads, global runtime logs or schema-wide SQL. Tenant-safe media
and transcript access must reuse verified application authorization before being
exposed. These reads do not enqueue analysis, inference, simulation or extraction.
## Draft proposal scope and reauthorization

`ai_learning.read`, `ai_learning.drafts`, and `ai_learning.knowledge_dedup`
are separate OAuth scopes.
`ai_learning.read` is sufficient for all read tools. `ai_learning.drafts` is
needed by `administrator_draft_list` and the other non-public draft tools. This
is intentional: a read-only access token cannot create or edit a proposal.

To enable draft-only work, set `AI_LEARNING_MCP_DRAFTS_ENABLED=true` and repeat
the normal OAuth consent flow with both scopes. The consent page then says
**Allow draft proposal access** and explicitly states that the grant does not
send messages, publish knowledge, change settings or apply a prompt. Existing
access and refresh tokens keep their original scopes; refreshing a read token
does not upgrade it. If ChatGPT retains an older connection, disconnect and
reconnect the MCP server so it can request `ai_learning.drafts` again.

`Additional OAuth scope required` means the presented token is missing
`ai_learning.drafts`; it is resolved by the scoped reauthorization above. The
separate error `Administrator drafts are disabled` means the Railway feature
flag is off. Neither error indicates that a draft was applied to production.

## Knowledge duplicate maintenance scope

`ai_learning.knowledge_dedup` exposes three owner-only MCP tools:
`knowledge_duplicate_preview`, `knowledge_duplicate_archive`, and
`knowledge_duplicate_rollback`. Request it alongside `ai_learning.read` in
the same consent flow. Existing tokens keep their original scopes, so reconnect
ChatGPT after deployment to request this scope.

Preview returns eligibility, reference checks, and an opaque precondition; it
never returns source text. Archive and rollback require that exact
precondition and a new UUID request key. Archive only sets `active=false` on
byte-identical same-agent TXT duplicates with matching chunks and no known
candidate or quality binding. Historical Simulation and agent-run citations
remain immutable evidence: they do not block archival and are never rewritten
to the canonical source. It preserves source rows and chunks. Retrying the same key returns the stored result; a changed
source or reused key with different parameters fails closed.

Drafts are the durable proposal boundary: they preserve evidence, immutable
versions and audit history before an owner chooses whether to approve anything.
Only the existing owner-controlled approval flow can authorize a live prompt
application; autonomous apply and autonomous observation processing remain
separately disabled by default.

- `simulation_runs`: cursor-paginated runs; context is off by default.  When
  requested, it is limited to four messages and phone numbers, email addresses
  and common secret assignments are redacted.
- `learning_issues`: deterministic Learning Loop findings based only on the
  same scoped runs.
- `usage_summary`: scoped Simulation input/output token counters.

No tool can send WhatsApp messages, change an agent, change a review, enqueue
a recheck, access another tenant/channel, or invoke a paid model.  Hourly
monitoring should persist the last returned cursor outside Beintaskbot and make
no call when there is no new cursor page.

## Revocation

Set `AI_LEARNING_MCP_ENABLED=false` for immediate global disablement, then
restart the Railway service.  Token rows are opaque hashes and expire after
eight hours.  A future owner-facing revocation screen may mark individual token
rows revoked; until that UI is shipped, use the global disable switch rather
than editing production data directly.

After PR #149 Railway recorded callback-specific CIMD IDs rejected by the
single CHATGPT_CLIENT_ID check. Both official forms now work through consent,
PKCE exchange and refresh rotation. Optional resource indicators must match
the configured MCP URL. Code/refresh exchange also checks the configured
tenant, agent and channel before issuing tokens.

Reference: https://developers.openai.com/plugins/build/auth

## Approved Administrator applications and discovery

The existing `ai_learning.apply` grant allows only an exact owner-approved
proposal while `AI_AUTONOMOUS_APPLY_ENABLED` is enabled. Prompt changes require
an isolated completed test; configuration patches cannot change mode, enabled,
channels, budget, CRM permissions or expand Expert permissions. New triggers
remain disabled. Knowledge sources remain inactive and unbound. Instructions
remain library drafts; they follow the existing employee review lifecycle.
Each operation has a one-time approval receipt, transactional audit and guarded
rollback. Tools do not approve their own receipts.

Discovery is separate from OAuth authorization. POST `tools/list` to
`https://crm.pro.az/mcp/ai-learning` to inspect what the deployed server exposes.
If the server lists tools but the ChatGPT connection exposes only the old three,
refresh its tool schema or reconnect the existing connector. Reauthorization is
also required if its token lacks `ai_learning.drafts` or `ai_learning.apply`;
refreshing a token never upgrades its scopes. A connector-side `Unknown tool`
before an HTTP request is different from the server's `Additional OAuth scope
required` response. Never paste tokens into chat.

## Five-dialogue Replay preflight without browser automation

An authenticated company owner may call
`administrator_historical_replay_preflight` with exactly five existing message
UUIDs. It uses the existing pilot configuration and shared ledger; it does not
call a model, reserve money, enqueue work, or execute replay. The fixed tenant,
agent and channel must match the OAuth grant. Existing owner API access also
supports GET `/api/platform/ai/testing/historical-replay?inventory=1` to select
five distinct recent verified customer conversations, followed by POST to the
same endpoint with `{"action":"preflight","message_ids":[...]}`. These are
session-authenticated same-origin API calls, with no Chrome CDP dependency.

After obtaining a successful fresh preflight, ask the owner: “Allow one replay
of these five selected messages using the shown model and prices, within the
remaining shared $5 cap, without sending messages or changing CRM?” Include the
five message IDs, exact model, remaining amount and output/call limits. Execute
only after explicit consent through the existing endpoint with a new request
UUID. Do not infer consent from a successful preflight, discovery, or deployment.
Unknown outcomes retain their reservation and must never be retried blindly.

Readiness validation uses isolated PostgreSQL PGlite plus mock providers; it
proves lifecycle and financial contracts, not the quality of real model answers.
Native multi-connection tests remain a separate optional localhost suite.

## Historical Replay eligibility guidance

Call `administrator_historical_replay_candidates` with no arguments for a
bounded scan of the latest 200 pilot-channel messages, or with `message_ids`
(1–25 UUIDs) to diagnose a selection. The existing owner API supports the same
read-only action: `{"action":"eligible_examples","message_ids":[...]}` at
`/api/platform/ai/testing/historical-replay` (omit `message_ids` to scan).
The response contains UUIDs, eligibility and rejection codes, never message
text, customer names, phones or JIDs. `selected_message_ids` proposes up to five
distinct verified direct customer text dialogues. Groups, LIDs, identity
conflicts, staff messages, old/future timestamps, media and
previously attempted examples are excluded from suggestions. This does not
change Simulation observation policy for human-owned conversations.

A scan may find fewer than five eligible examples. It does not authorize or
execute replay. Pass exactly five selected UUIDs to the existing preflight and
obtain explicit owner consent before paid execution. Missing prices, disabled
scope and the existing shared maximum $5 budget still fail closed. A rejected
preflight now includes the failing message UUID and a precise reason code plus
the diagnostic tool name; no customer content is included in error details.


Human/Paused dialogue state is eligible for this isolated historical Replay.
It continues to block real Active sending. Discovery and Replay do not resume
conversations, change handoff, alter agent modes or populate the live Simulation
queue. Context is capped at the triggering customer's message; later staff
answers remain comparison-only evidence. Paid execution still requires a fresh
exact-five preflight, explicit owner approval and the existing shared $5 ledger.

## Substantive historical examples

Automatic candidate discovery now applies conservative, deterministic text
triage after all eligibility guards. Greetings, thanks, bare URLs and text
without an explicit product/support signal do not fill a five-message sample.
`selection_status=insufficient_technical_examples` means the bounded 200-message
scan found fewer than five suitable distinct dialogues; it never pads them.
This is lexical triage, not AI intent classification or a correctness verdict.

Within the suitable set, a source cited by an approved Support Case with a
confirmed outcome and matching exact tenant/channel/conversation ranks first.
The response gives `resolution_case_id` and `resolution_status=confirmed_case`.
Everything else remains `unknown`; an employee reply or thank-you is not proof
of resolution. Later reviewed outcomes influence selection only and are never
included in historical model input. Use the existing Support Case/AI Learning
review workspace to inspect the original evidence before approving a paid run.

Passing `message_ids` explicitly is owner manual selection. Policy-eligible
messages can be chosen even when lexical triage is inconclusive, and the
response retains the technical-signal warning and `selection_mode=manual`.
Identity, media, tenant/channel, date, duplicate and financial guards still
apply. Manual selection and preflight do not grant permission for paid calls.

## Provider-proven direct-dialogue recovery

Issue #248 extends this inventory across the full configured channel history.
It uses the existing persisted provider aliases as well as signed event pairs;
discovery and owner-session historical reconciliation share the same resolver.
Subsequent webhook events reuse a persisted alias even when the event omits
`directIdentity`, including after worker restart.

The read tool accepts optional `search` (up to 100 characters) for an owner to
locate route diagnostics by their saved label/identity. Responses still omit
names, phone numbers, JIDs, LIDs and text. Route diagnostics include lead ID,
channel UUID, identity kind, incoming/outgoing counts, other-channel message
counts, and an opaque route ID. `scan_complete`, `scanned_routes`,
`excluded_routes` and `unmapped_messages` distinguish a complete scan from an
empty result. No heuristic rewrites data: matching labels/stored phones or
linked-device message echoes produce `provider_identity_unproven` candidates.
Different channels remain independent. A different number in a message body
is not the connected account's phone or a provider identity link.

Existing physical reconciliation remains owner-confirmed with backup and
rollback. Discovery now quarantines dependent cases, read markers, CRM contact
links, assignments/jobs and aliases pointing at the split route rather than
detaching references. Its successful reconciliation removes the duplicate
route from the existing Dialogs search; logical mappings alone do not hide
unreconciled histories. No new merge service or consultation workflow is added.

`whatsapp_dialogue_duplicate_candidates` is a tenant/channel-scoped read-only
inventory of possible split direct WhatsApp histories. It returns opaque pair
IDs, aggregate incoming/outgoing message counts, provider receipt counts and
conflict codes. It never returns a phone number, JID, LID, name, message text
or media URL. A duplicate is eligible only when Connect stored the same-event
`directIdentity` LID/JID pair, both direct routes still exist in the configured
channel, and neither side has conflicting provider evidence. Names, avatars,
phone-like values, groups and ambiguous LID/JID graphs are never identity proof.

`whatsapp_dialogue_merge` requires a fresh preview precondition, UUID request
ID, company-owner OAuth token and the separate `ai_learning.dialogue_merge`
scope. Reconnect ChatGPT and explicitly approve this scope before it is
available; existing read tokens cannot merge anything. The operation creates a
locked, audited, idempotent **logical** mapping and can be rolled back using a
new fresh preview. It does not rewrite or delete CRM messages, IDs, media,
timestamps, employee attribution, case links, Connect rows, AI state or
WhatsApp data. This preserves historical evidence while allowing a future
authorized reader to recognize a verified split. If the original customer
conversation is not provider-proven, the result remains unknown rather than
being merged.

Support Case review also now marks evidence made solely from outgoing/internal
messages as `original_customer_unproven`; such a card cannot be approved or
published until an actual customer-origin source message is included.

## Owner-delegated publication (Issue #228)

`ai_learning.publish` is a separate, revocable OAuth consent. Existing read,
draft and apply tokens are not upgraded. Reconnect with this scope when needed.
It exposes `administrator_publication_grants`, `administrator_publish_reviewed`
and `administrator_rollback_publication`. There is deliberately no MCP grant or
self-approval tool.

The existing owner-only AI Administrator panel can delegate one publication:

1. Prepare the existing draft. For knowledge/instructions use the existing owner
   approval and apply flow to materialize the inactive source/library draft.
   For cases, select the existing evidence-linked pending Support Case.
2. Open **Delegate reviewed publication**, inspect the material, choose the agent
   and channel, then explicitly confirm review. The server binds the permission
   to the displayed material hash, exact draft version, tenant, owner, agent and
   channel. The UI grants 30 minutes; the backend permits 1–1440 minutes.
3. MCP lists only grants matching its configured agent/channel and consumes the
   exact permission once. Retries return the stored result without republishing.
   A revoked/expired grant, revoked OAuth token, changed material, changed draft,
   lost owner role or changed agent scope blocks publication.
4. Withdraw an unchanged publication with `administrator_rollback_publication`.
   Instruction rollback uses existing unpublish; case rollback archives and
   disables its sources; knowledge rollback deactivates the published copy.
   No historical material is deleted. Changed/new dependencies require review.
   Rollback remains available after grant expiry; it needs the publish OAuth
   scope and owner access. Revocation blocks unused grants; consumed grants must
   be rolled back instead.

Publication uses existing library transitions and Support Case evidence guards
in the same transaction as permission consumption, result receipt and audit.
Owner review of knowledge is **not** confirmation of a historical case outcome.
Uncertain outcomes cannot bypass the existing case publication checks. No public
share link is created. All publication remains fail-closed behind the existing
`AI_AUTONOMOUS_APPLY_ENABLED`; this change does not enable it or any AI mode.

Knowledge Sources are agent-bound in the existing architecture. Consequently a
delegated publication requires the selected agent to be confined to that Connect
channel (no wildcard/other channel/provider); the server checks this both at grant
and execution. A later deliberate owner expansion of the agent's scope follows
the existing agent configuration rules and expands its existing knowledge access.
This patch does not change those rules or create a parallel knowledge store.

Tests use an authorized synthetic owner/tenant and isolated PostgreSQL WASM
(PGlite), real SQL transactions, HTTP OAuth/MCP handlers and Chromium `/app`
assets. They do not exercise the owner's live ChatGPT connection or write real
production knowledge. No paid calls, messages, or production settings changes.

## Bounded persistent training delegation (Issue #231)

The same owner panel now offers **AI training delegation**. One explicit owner
consent creates a revocable policy for one tenant, one agent and one Connect
channel. Select material types, rights (`create`, `update`, `publish`, `rollback`),
lifetime (1–720 hours), total operation limit (1–1000), and hourly limit (1–100).
No policy is created by deployment. Policy creation/revocation is owner-session
only, same-origin protected; MCP cannot grant or expand its own permissions.
The owner panel shows the policy and its recent operation receipts. Existing
company audit reads provide the longer audit trail.

MCP uses the existing `ai_learning.publish` OAuth permission; creation/update also
requires `ai_learning.drafts`. Listing owner-provided knowledge drafts requires
both scopes. Existing OAuth revocation and tenant/owner checks remain authoritative.
Company-wide delegation is a distinct owner opt-in, disabled until the owner
explicitly creates it in the panel. It covers all active agents in that tenant,
all three material kinds, has no operation limit or expiry, and remains active
until revoked. Every operation still selects exactly one agent/channel; there is
no fanout. New tools:

- `administrator_training_policies`: list policies/audit for the OAuth scope.
- `administrator_training_sources`: list policy-eligible reviewed material for
  one target; ordinary verified sources only.
- `administrator_training_owner_drafts`: with both draft and publish OAuth
  scopes, list exact owner-created knowledge drafts (owner-provided or evidence-backed) and hashes for one target. Evidence-backed hashes also bind the current tenant/channel-scoped source records.
- `administrator_training_create`: prepare material from a verified source, or
  an exact owner-provided or evidence-backed knowledge draft under the company policy. Its full text, including caveats and owner additions, is preserved as delegated content; evidence references do not attest to every claim.
- `administrator_training_update`: refresh an unpublished policy material from
  verified evidence, with the current draft version and material hash.
- `administrator_training_publish`: publish one prepared exact material under
  the policy, minting/consuming the existing one-use publication receipt in the
  same transaction. The assistant must invoke it separately after discussing
  that exact material with the owner in chat. The backend does not verify chat
  consent; it records the delegated policy action accurately. There is no
  background or batch publishing. Owner-provided material is labeled as delegated,
  not as individually approved or CRM-verified.
- `administrator_training_rollback`: guarded withdrawal of a policy publication.

Each mutation carries an explicit UUID `request_id`. A locked policy row
serializes admission, quota checks, evidence validation, business mutations,
internal approval/grant receipts, audit and result persistence across workers.
The `(policy_id, request_id)` key replays the stored result; different parameters
under that key are rejected. Failed transactions consume no quota and leave no
partial material/approval/publication. Expiry uses database wall-clock time.
Revocation serializes with in-flight operations; subsequent new work is blocked.
Safe withdrawal with the explicit rollback right remains available after expiry
or revocation and after quota exhaustion. Repeated withdrawal creates no new
receipts. It preserves objects/history and never deletes permanently.

### Verification boundaries

This is deliberately a verified-material permission, not autonomous factual
approval of arbitrary generated text:

- Knowledge is copied/refreshed from active, agent-bound, reviewed knowledge with
  server-checked provenance. Instruction/case origins and delegated lineage are
  revalidated. An inactive source or arbitrary `approved_by` in a draft is not
  sufficient. Knowledge preparation records source evidence in its immutable
  operation receipt, rather than fabricating message evidence for an instruction.
- Instructions copy an existing employee-approved instruction document, including
  tenant-authorized images/video. New factual/UI steps still require verification;
  this policy does not pretend that an unreviewed generated instruction is proven.
- Cases refer to existing tenant/channel-authorized historical candidates and
  real message evidence. Existing outcome/work-status guards remain in force.
  Unattended operational steps/actions must be supported literally in source
  text. Unsupported paraphrases require clarification rather than automatic
  publication. Preparation labels those cases `needs_clarification`; it does not
  invent an outcome or overwrite real support tickets. Archived cases are not
  restored automatically. Case update refreshes its verified proposal only,
  never copies another conversation's outcome.
- Prompt/trigger/agent edits, Active, CRM actions, client sends, budget changes,
  paid inference, public share links and permanent deletion are not policy rights.
- Version/hash checks and immutable operation provenance prevent generic draft
  tools from injecting unrelated facts into a delegated publication. Existing
  publication rollback checks exact object/source/dependency snapshots.

No new scheduler or AI provider call is added. An assistant invokes the tools;
existing observation/learning workers are unchanged. Policies are permissions,
not authorization to perform paid model processing.

### Production setup / scoped authorization

The bounded policy setup is one submission in the existing AI Administrator
panel for the intended agent/channel, selected rights/materials and limits. The
company-wide policy is a separate owner opt-in in the same panel. Each active
policy is exact tenant-scoped authorization for training publication, while each
publication still requires a distinct tool invocation after owner discussion. It is independent of the
legacy `AI_AUTONOMOUS_APPLY_ENABLED` switch, which remains a kill switch for the
older general application flow (such as agent/prompt changes). Therefore an
operator never has to enable broad application for every tenant in order to use
one owner policy. The panel reports both states explicitly. A connection that
lacks the existing publish/draft OAuth scopes must be reauthorized normally;
the policy itself does not alter OAuth, environment variables, AI modes or any
other tenant. Deployment creates no policy and changes no feature flag.

Tests include disposable native PostgreSQL 17.6 with actual multi-process
publication/idempotency/quota/revocation races, PGlite parity, HTTP owner-consent →
multiple MCP publications → readback → revocation/rollback, and Chromium owner UI
checks in RU/AZ/EN, both themes and desktop/mobile. Three actual historical case
status summaries were read through the authorized MCP and irreversibly redacted
into status-only fixtures. They verify rejection of unknown outcomes; no customer
messages, names, IDs or attachments are copied into tests. These are not full
historical conversation replays. Positive lifecycle tests use synthetic evidence
and a synthetic authorized owner. No production materials are modified.

Company policy mutations recheck and lock the current OAuth token/connection in
the business transaction, including scope, expiry, revocation and token rotation.
A separate authenticated owner is still required. Delegated preparation records
the policy on the receipt with no human `approved_by` or human-approval audit.
Safe withdrawal derives its target from the persisted grant and does not require
the old target agent/channel to remain active or present.
