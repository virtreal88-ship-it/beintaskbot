# Text model retirement and the requested company default

## This code change

`gpt-4o-mini` and its `YYYY-MM-DD` text snapshots are not admitted for new
text generation. Catalogues omit them. An existing selection produces
`configuration_required` before a new provider request or reservation; it is
not replaced by a differently priced model. Legacy `OPENAI_MODEL` no longer
has a hardcoded mini default. Existing non-retired choices and each path's
selection order remain intact.

Historical model IDs, price catalogues, spend reservations (including unknown
holds), receipts, and transcription models are unchanged. Inactive settings can
still be disabled or have their key removed without selecting a replacement.

## Requested Luna default: activation still pending

The owner selected `gpt-6-luna` as the desired company default, with explicit
agent overrides retained. OpenAI documents Responses, image input, and structured
output support: https://developers.openai.com/api/docs/models/gpt-6-luna

The existing Responses request supports an explicitly selected Luna; its image
capability is admitted and covered by a mock request test. This does not change
any deployed model, credential, tariff, mode, or permission. It does not establish
access for the saved company key. Legacy Chat Completions function calls also
need `reasoning_effort=none` before a Luna activation there; this PR does not
activate Luna in those paths.

Current orders, deliberately preserved in this retirement patch:

| Path | Existing selection order |
| --- | --- |
| Primary, AI Testing, Simulation | explicit agent, ready platform default, company model |
| Media/extraction, Expert, support summary | explicit agent, company model, platform default |
| Instructor | explicit agent, company model |
| Legacy | configured OPENAI_MODEL; history replies have a separate setting |

Consequently, changing only the company model is insufficient to promise Luna
in every default path when a platform default is active. The next activation
must be company-scoped and explicitly reconcile that default order, preserve
all explicit non-retired agent overrides, and leave other tenants unchanged.
No such configuration migration is included here.

## Existing free company-key catalogue

The authenticated owner/admin UI uses
`GET /api/platform/chat/ai/models?expected_tenant_id=...&expected_user_id=...`.
The server rechecks active membership and tenant, decrypts only the saved
`chat_ai` company credential, and calls fixed `https://api.openai.com/v1/models`.
There is no platform-key fallback, generation request, customer content, or key
in the response. The UI action is Integrations > OpenAI > Settings > AI Copilot >
"List models for this key" ("Показать модели ключа"). Presence in this list does
not prove Responses or image-generation access.

The connected MCP `company_ai_settings_read` currently projects agent settings
from SQL only; it does not expose this catalogue. A possible narrow follow-up
is an explicit opt-in model-list argument under the existing active owner/read
scope, reusing `available_models()` and returning only model IDs and sanitized
availability/error state. Ordinary settings reads must remain network-free.
This proposal does not change existing grants or create a key.
