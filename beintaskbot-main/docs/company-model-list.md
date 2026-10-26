# Explicit company model listing through MCP

`company_ai_settings_read({"list_provider_models": true})` opts into the existing
company Chat AI model listing. Omitted or false leaves ordinary settings reads
network-free. Only this tool accepts the option.

The server revalidates active company-owner membership, the exact current OAuth
token/connection/tenant/agent/channel binding, expiry, revocation and read scope.
It reuses `tenant_chat_ai_settings.available_models()` and the saved `chat_ai`
company credential. It does not substitute the platform key or accept a caller
URL, tenant, API key, or model. The fixed GET to OpenAI `/v1/models` runs off the
HTTP event loop with the existing bounded timeout and redirects disabled.

The `provider_models` result contains bounded model IDs, status, credential source
`company`, and sanitized error categories. `generation_access_verified` is always
false: being listed is not proof of Responses/image generation access. Errors do
not trigger retries or return keys, provider bodies, or customer data.

No model setting, default, catalogue policy, tariff, budget reservation, grant,
AI flag, or historical record changes. No generation or compatibility probe.
The separate retirement and Luna activation work is not part of this change.
