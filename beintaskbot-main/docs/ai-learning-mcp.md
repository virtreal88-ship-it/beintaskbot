# ChatGPT read-only access to AI Learning Loop

`/mcp/ai-learning` is a remote MCP server.  It is disabled by default and has
no write tools. The three original tools read only Simulation records whose
tenant, primary agent and Connect channel exactly equal its Railway configuration.
The owner-authorized administrator read tools read the configured company.

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
connection to company-wide reading. Existing access and refresh tokens work;
no new consent or client credentials are required. Each request rechecks active
membership and the current owner role in PostgreSQL. Revoking membership or
removing the owner role immediately prevents further reads.

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
Write/apply/approval/continuous-processing capabilities are later stages and
remain unavailable in this release.

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
