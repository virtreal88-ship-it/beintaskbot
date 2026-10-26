# WhatsApp photo inputs — Sol

Base: origin/main 39cac3d9. Branch codex/whatsapp-vision. No production requests,
merge, deployment, agent settings or WhatsApp session changes.

Connect worker now resolves incoming images through the existing protected /media
route. No customer URL, alternate credentials, public storage or independent AI
provider is introduced. The current job's tenant/channel/conversation/lead and
incoming persisted message are checked before reading bytes. Live agent/company
configuration, conversation state/lease and group_revision authority are checked
around media work and immediately before vision generation. Existing final
Manual/Automatic, Human/Pause, schedule and idempotent send boundaries stay intact.

Unified groups retain shared text/authorship. The internal grouped source metadata
now includes external_id, so a deduplicated image can use the current job channel's
own replica. No foreign-only media is fetched using another channel's credentials;
that evidence remains explicitly unavailable. No transport authority is widened.

History counts image/video/document attachments even when media_url is empty,
preserves captions and author roles, and names unavailable evidence explicitly.
Only images actually included in the request are marked supplied for analysis.
This means supplied bytes, never verified problem resolution or guaranteed OCR.
Images' embedded text is untrusted; missing/unreadable content requires clarification.
Video/PDF/document analysis is not enabled.

## Models and budgets

Exact reviewed OpenAI IDs: gpt-4o, gpt-4.1, gpt-4.1-2025-04-14, gpt-4.1-mini, gpt-4.1-nano.
No model is switched automatically. Unknown/date/custom IDs remain text-only until
operator-confirmed via server-only AI_AGENTS_VISION_MODELS (comma-separated exact
IDs; an explicitly empty value disables photo processing). This is a capability
registry, not permission to use a model: existing model/key authorization applies.
Reference: https://developers.openai.com/api/docs/guides/images-vision

At most 2 recent incoming photos since the last employee reply, within 10 minutes
of the current context; at most 8 MiB total, 2 concurrent downloads per process,
35-second network-turn budget, protected per-file timeout 30s. JPEG/PNG/GIF/WebP
MIME and signatures checked. Existing DNS/public-host, HTTPS, no redirect/proxy,
response channel/message identity and byte bounds are reused. Hash-identical
images share one model input. Detail=low bounds visual processing; small interface
text may require the customer to send a crop. No auto high-detail retry.

The existing single generation reservation/daily call budget, company token caps,
provider usage accounting and max output tokens apply; no additional model call
for vision or separate paid service. This is not a guaranteed currency ceiling:
provider usage is reconciled after the bounded call, as in the current worker.

## Voice verification boundary

Existing ASR is unchanged: platform default gpt-4o-mini-transcribe or configured
BYOK transcription model, auto language. AZ/RU/EN synthetic transcript fixtures
exercise the complete worker path with a mocked ASR response. They do NOT prove
actual speech recognition quality. No local OpenAI credential is configured;
therefore no real paid ASR or vision calls were made. Real synthetic speech quality
validation remains pending secure provider access; ASR was not replaced. Local
ffmpeg/decoder checks may skip where ffmpeg is unavailable.

## Tests / integration

Targeted tests cover photos/captions/authorship, absent files, MIME/size/time/budget,
unknown models, Human takeover, Manual drafts, duplicate image inputs, original
voice paths, shared two-channel group replica selection, group fences and existing
protected endpoint isolation/redirect/DNS checks. All bytes are synthetic fixtures.
Two unrelated wizard tests fail on the base (trigger role mock and advisory-lock
SQL order); these are reported separately, not patched as part of media work.

Before rollout check deployed Connect supports the existing protected /media route,
confirm the configured exact model capability and perform an owner-approved
synthetic vision/ASR quality smoke test. No production deploy is included here.

Verification: 194 passed, 5 skipped, 2 deselected, 19 subtests passed. The two
deselected wizard failures were reproduced in a clean archive of 39cac3d9.


Snapshot fix: gpt-4.1-2025-04-14 is an exact supported ID for the existing
OpenAI Responses image input. Agent model strings pass unchanged from a.model
through load_agent_config and the worker to the request model field. No live
agent settings were read or modified; Nizam Ai ID is owner-reported. Explicit
AI_AGENTS_VISION_MODELS overrides still replace defaults and must include this ID
if configured. Capability source: https://developers.openai.com/api/docs/models/gpt-4.1
