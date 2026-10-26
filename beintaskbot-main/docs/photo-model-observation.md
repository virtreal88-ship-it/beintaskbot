# Owner-selected photo observation

This is an optional mode of the existing exact single-message review/worker.
`photo_model: "gpt-4.1"` (also its exact `gpt-4.1-2025-04-14` snapshot) selects
one image observation. The setting belongs to this consent, not the agent or
company defaults. Omitted means the existing extraction behavior. Text agents,
ASR, video, other agents and tenants are unchanged. This is not a persistent
company-wide photo default.

The owner component shows the exact image/version, selected photo model, one
Vision / zero main / zero ASR calls, and maximum additional cost. Completion
requires the existing owner OAuth component capability. It queues the existing
Campaign worker. A completed photo run stores reusable visual evidence; it
creates no case candidate, publishes nothing, and does not mark a support issue
resolved. An operator/administrator can then review the case using that evidence.

## Model access and prices

Only the saved company credential is used, under its existing enabled/consent
and configured model allowlist. No platform-key fallback or model substitution.
`GET /v1/models` listing alone does not verify Responses/image generation access.

The exact model must have an explicit server-owned token tariff in the existing
`AI_MODEL_PRICES_MICROUSD_PER_MILLION` mapping: input **2000000**, output
**8000000** microUSD / million tokens. Missing or mismatched tariffs fail closed
before enqueue/reservation/provider. This release does not change that setting.
No 4o-mini or fixed media-call tariff is reused. The shared tenant cap must exist;
old unknown reservations continue to count against it.

Official standard GPT-4.1 rates checked 2026-10-26: $2 input / $8 output per
million tokens: https://developers.openai.com/api/docs/models/gpt-4.1 .
Low-detail image input uses 85 tokens:
https://developers.openai.com/api/docs/guides/images-vision .

The fixed, bounded observation prompt and empty conversation envelope plus one
low-detail image reserve at most 2048 input + 600 output tokens: **8896 microUSD
($0.008896)**. The input bound is checked before dispatch. Successful complete
usage settles the actual input/output tokens against the pinned rates; missing,
inconsistent, or excessive usage keeps the new reservation unknown. No text
extraction reserve/call is created in this mode. Cached evidence releases the
unattempted reservation without downloading or generating again.

## Model-access recovery

For an already uncertain image, `retry_of`, exact `source_version`, a unique
`request_id`, and `photo_model` must be supplied together. A model change is
eligible only after recorded `provider_http_403_model_access` at visual analysis,
for the same owner/tenant/agent/channel/message/version/body/external ID and image
bytes. The original one-use retry may receive one such recovery. A photo-mode
recovery cannot become another recovery parent. Unique parent/request indexes
and transactional locks serialize duplicate owner decisions and worker claims.

The component explicitly acknowledges the unknown prior charge. The prior
attempt and its root are revalidated; only that exact lineage is exempted from
the same-source queue hold. Every old cache/extraction/money hold remains intact.
Other unknown sources, running work, source mutation, expired/revoked consent,
revoked OAuth, removed models, stopped agent, changed tariff or exhausted cap
still block. The new call uses a new consent-bound cache namespace and capped
reservation; neither code deployment nor a read starts/retries any photo.

## Verification

Synthetic image bytes -> mocked Responses observation -> reusable receipt ->
scoped MCP content read; no text/ASR invocation; actual-token settlement;
legacy extraction compatibility; tenant/token/source/tariff/allowlist fences;
unknown no repeat; existing-receipt no-download reuse; native PostgreSQL
concurrent approval/claim. No customer data or paid provider tests.
