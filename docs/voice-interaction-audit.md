# Voice interaction: audit and delivery plan

Audit baseline: Worker `92172cb2d3e5d8594c8bf1a12b2cd1b37835534d`, Connect `bd0828eb7fa203c4f5bfa3a68e5a71e7a16d585f`. Both were the successful production deployments when inspected. Recheck before release.

## Product behaviour

An employee explicitly records, stops, previews and sends a voice message in the current customer dialog. Cancel must never send. Unknown delivery must keep the recording and original request ID, prevent blind resend and show the uncertainty. Incoming and outgoing audio belong in the ordinary history and use the existing audio player.

The employee's AI conversation is a separate microphone mode. It requires an interactive streaming implementation, interruption, shared authorised context and cleanup, not a sequence of manual uploads. No voice feature is accepted on mock results alone. Real customer contacts are forbidden; an authorised test contact, CRM login and a separately approved live-call budget are outstanding.

## Existing components and gaps

* `tenant-operator-dialogs.js` owns the dialog composer, existing native audio controls and Copilot; `tenant-chat-send.js` binds scoped send receipts. Its file upload is deliberately disabled for Connect.
* The older deal interface in `beintaskbot-main/docs/index.html` already contains `toggleDealVoice`, `stopDealVoiceAndGetFile`, preview and legacy Kommo voice conversion. They are embedded in that page's global deal state, and its send path can stop and immediately send a recording. They are not directly usable in `/app` with the required preview-before-send guarantee. The isolated recorder preserves the familiar MIME preferences while adding cancellation-race, size, lifecycle and no-send guards; consider later extracting one shared primitive instead of silently changing the legacy screen. Existing `tenant_connect_voice.validate_audio` has bounded local decoding and duration checks, but its transcription-specific imports and policy must be assessed before reuse. The old `bot.py` converter may fall back to returning unconverted input as Ogg and is not sufficient validation for this route.
* `tenant_chat_upload_api.py`, `tenant_chat_upload_policy.py` and `tenant_chat_upload_service.py` handle bounded Kommo Drive uploads, not Connect audio. Reuse their admission and receipt ideas, not a misleading Kommo route for Connect.
* `tenant_chat_send_store.py` checks tenant/member/dialog authority and reserves actor-scoped request IDs with payload fingerprints; sending and unknown outcomes are durable. Access and routing must be checked again at the send boundary.
* Connect's `src/main.ts`, `src/persistence.ts`, `src/providers.ts` and worker implement message identity, persistence and provider transport. The generic schema mentions audio but `sendMessage` calls Evolution `sendText`; generic outbox failure handling may retry. Voice needs bounded, verified bytes and a durable one-shot protocol that never repeats a potentially accepted provider request. Existing quoted-once flow is a useful reference, not automatically a voice implementation.
* Evolution 2.3.7's `audioWhatsapp` implementation accepts base64, can convert to Ogg/Opus and sends with `ptt: true`. This is source-code evidence, not proof that the deployed instance accepts a request or delivers a voice note: https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/src/api/integrations/channel/whatsapp/whatsapp.baileys.service.ts#L2949-L2987 . Do not let a caller provide URLs to this endpoint.
* Existing incoming/outgoing media preview is tenant/channel/conversation scoped and validated in `src/media.ts` and `tenant_connect_media.py`. Confirm the provider identifier for outgoing playback and actual status evidence; do not claim acceptance is delivery.
* Dialog Copilot uses `tenant_operator_copilot.py`, assigned-agent guidance, saved dialog history and knowledge. Its current policy explicitly prohibits tool calls and external actions. Do not expand permissions; determine which existing employee assistant the voice mode is intended to share before promising tool parity. Reuse its server-side context and accounting only when the same authority and confirmation semantics can be enforced.
* Parallel #473 changed `tenant-app.html`, workspace and member UI. The voice integration is likely to touch the shared HTML script list and dialog bundle; keep modules separate, rebase and retest against the then-current main before merging.

## Proposed safety bounds, subject to review

Customer recording: stop at 120 seconds or 5 MiB, whichever is first; validate actual container and duration on the server, not just browser MIME. Capability-probe WebM/Opus, Ogg/Opus and MP4/AAC; display an unsupported-browser error when recording or safe conversion is unavailable. Test actual desktop Chromium/Firefox and Android Chrome, plus iOS Safari; MIME support alone is not device acceptance. Confirm conversion and native PTT compatibility against the deployed Evolution version without customer traffic.

AI session: propose a 10-minute hard cap and 60-second idle timeout, one active session per employee, with server-enforced expiry and configurable stricter tenant limits. These are proposed, not activated or billed. Measure time from end of speech to first audible output, targeting p50 under 1.5 seconds and p95 under 3 seconds on a good network; record actual test conditions. Provider/model, price, safe server tool adapter, storage, and limits require separate current compatibility and cost review after the credential decision. Do not activate paid production voice until the budget and cost limits are agreed.

## Delivery gates

1. Isolated recording, preview and resource cleanup with offline tests; no sending or UI activation until the transport is ready.
2. End-to-end Connect voice transport with fresh authority, input verification, bounded storage, idempotent one-shot delivery, scoped history and non-retrying unknown state; test failures, crashes, duplicate requests and existing media.
3. Separately authorised AI integration, shared context and permissions, server-controlled lifetime/cost, microphone exclusion and interruption-aware history.
4. Browser/device, access, navigation, network, restart and cross-tab checks; independent review and exact-head CI.
5. Recheck both production SHAs and parallel changes, merge only after checks, verify Railway health and rollout/rollback. Record actual WhatsApp and interactive voice evidence separately from automated results.
