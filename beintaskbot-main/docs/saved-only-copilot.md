# One owner-approved saved-only Copilot draft

This bounded action uses the existing `/api/platform/ai/dialogs/copilot` route and the same assigned-agent context, instructions, style, knowledge retrieval and text-provider serialization. It is **disabled** while `APPROVED_SCOPE_DIGEST` is `None` (and the matching owner UI digest is `null`). No real target identifiers are stored in source. The parent must supply and verify the target/owner before activation or any paid test.

The owner-only button appears in the existing AI helper panel for that exact approved scope. First click performs a read-only preview. A second explicit confirmation may dispatch one draft; there is no automatic POST, customer send, composer replacement, media download, ASR or vision. A collapsed details section shows model, standard service tier, snapshot fingerprint, context counts, source IDs/version hashes and knowledge publication hashes. It never exposes full prompts, credentials or attachment URLs.

## Wire contract

GET `?saved_only=1&lead_id=...&channel_id=...&conversation_id=...&expected_tenant_id=...&expected_user_id=...` uses the normal authenticated owner/dialog/Chat-AI access checks and matching identity headers. It performs no schema setup, data mutations, reservation or provider call. It returns a stable `request_id`, state/result (when still authorized and context-current), model, token upper bounds, `reserve_microusd`, `cap_microusd`, safe audit metadata and an encrypted five-minute `cost_token` only for an unused eligible case. An orphan monetary record blocks preview/dispatch without being reset.

POST adds `saved_only:true`, that exact `request_id` and `cost_token` to the route/identity fields. Model, URL, keys, tools and client price overrides are rejected. Actual active owner and exact approved owner/tenant/dialog scope are server-verified. The UUID derives only from the fixed consent-case identity and canonical tenant, so changing owner, route, model, source versions, knowledge, thread or configuration cannot mint another paid slot. The original actor owns the durable run. Completed results reuse; sending/unknown and any other admitted state cannot redispatch. No old records are cleared.

## Evidence and financial bounds

Only exact-source saved processed receipts are read. Shared pure normalization preserves production captions, historical labels, receipt wrappers and source order. Current voice/photo completeness checks are retained; saved documents and selected current text must also fit completely in the existing 20-message / 1200-character-per-message / 14000-character context bounds. Missing, partial or uncertain evidence blocks. Full video coverage is unsupported and blocks before provider work. **This action retains the existing latest-episode selection; it does not implement the separate whole-current-request boundary architecture.**

The actual assigned model must be `gpt-4.1` or its approved pinned snapshot; no model setting is changed. Only this action sends `service_tier:default` to require standard pricing. Ordinary provider payloads omit that optional field as before. Explicit incompatible/malformed model tariffs fail closed. Rates are pinned at 2,000,000 / 8,000,000 microUSD per million input/output tokens. The shared provider payload builder is used for both quote and HTTP; only the six approved fields are allowed. No tools, previous-response chains, reasoning, premium tiers or image inputs are possible.

The conservative input bound is the complete escaped UTF-8 serialized provider payload (including full instructions and nested conversation/knowledge JSON) plus 8192 framing tokens. Output is capped at 900. The existing maximum-rate total-token upper-cost function must yield at most **940000 microUSD ($0.94) for the entire one-call case**. This is a maximum internal reserve, not an OpenAI invoice. Atomic ordinary daily/company/dialog caps and all positive tenant spend caps still apply; a private server opt-in makes reservation/settlement mandatory even when the existing tenant financial cap is absent/zero. Ordinary paths keep their defaults.

Snapshot fingerprints cover the actual final payload, actor/assignment/config/credential version, exact sources and receipt-publication state, and selected knowledge publication/version metadata beyond the displayed excerpt. They are rebuilt immediately before HTTP. A known result is durably stored before settlement/current-context publication checks. Uncertain provider outcomes retain holds; a known pre-dispatch stop releases only its unused hold and still cannot redispatch the admitted case. Missing/invalid usage keeps the conservative hold. Settlement errors cannot discard known text. Saved results do not modify customer messages, media receipts or the ordinary private thread.

Official standard-model and service-tier references:
- https://developers.openai.com/api/docs/models/gpt-4.1
- https://developers.openai.com/api/reference/cli/resources/responses/methods/create/
- https://developers.openai.com/api/docs/guides/fast-mode

All implementation verification uses synthetic data and mocked providers; the sole paid acceptance remains owned by the parent coordinator and requires a fresh target-bound preview and explicit source/cost choice.
