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
