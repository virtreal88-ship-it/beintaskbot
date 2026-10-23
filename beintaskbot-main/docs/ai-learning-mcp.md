# ChatGPT read-only access to AI Learning Loop

`/mcp/ai-learning` is a remote MCP server.  It is disabled by default and has
no write tools.  It reads only Simulation records whose tenant, primary agent
and Connect channel exactly equal its Railway configuration.

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
