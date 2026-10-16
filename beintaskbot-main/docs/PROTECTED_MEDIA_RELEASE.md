# Protected WhatsApp media — Beintaskbot integration

Prepared on 2026-10-16 from production main `ccca01ee`, in isolated branch `codex/protected-whatsapp-media`. No merge, push, production deployment or Railway mutation. Nizam Ai settings, prompts, model and WhatsApp sessions are untouched.

## User flow and boundaries

In `/app` → Dialogs → conversation, an image has a View photo button and audio has a Listen button. Both use the existing `tenant-chat-attachments.js` click-to-download lifecycle. The audio player uses a local object URL, controls and preload=none; there is no autoplay. A completed existing transcript can be expanded locally. Opening a list, conversation or transcript never downloads media or calls AI.

The existing `/api/platform/crm/chat/attachment` API resolves a Connect message using its persisted reference and server-side conversation mapping. Fresh membership, active tenant, dialogs permission, local message/lead/channel/conversation ownership and an active Connect integration are required. Connect does not require a Kommo integration or a synthetic CRM deal. Authority and credentials are checked again after downloading, before any bytes reach the browser. Kommo retains its existing deal/pipeline and Drive checks.

The server fetches only its configured HTTPS Connect host with public DNS results, certificate verification, no redirects, no proxy environment, and no automatic decompression. Channel/message response headers must match. Media is bounded to 8 MiB photos / 10 MiB audio, with a 30-second overall deadline. Connect additionally validates MIME/signatures, bounds Evolution base64, and has a 20-second provider deadline. No caller-controlled URL is accepted. Files are held only in bounded memory; no binary database fields, disk retention, new services or transcoding.

Admission: three attachment downloads per Beintaskbot process, 20 requests per employee and 60 per tenant per minute; Connect has two concurrent preview resolutions and 20 per tenant/IP per minute. These counters are per process, reset on restart, and are not distributed across replicas. The browser retains at most three previews, revokes object URLs on close/identity change/visibility cleanup, and rejects stale results. Reopening a preview after cleanup downloads it again; there is intentionally no durable binary cache.

Group authors, participant identifiers, direction, title and existing group AI policy are preserved. Existing Connect metadata with no upstream file remains a readable message with an unavailable-media error. No 24-hour limit is imposed on human previews; actual retention is owned by WhatsApp/Evolution. The worker's existing voice-processing policy is unchanged.

## Local request / byte comparison

Reproducible `node tests/test_connect_media_preview_ui.cjs` compares the actual base scripts at `ccca01ee` with these scripts, using identical synthetic group history and mocked responses. This is a functional measurement, not a production latency/capacity benchmark; it excludes HTTP/TLS headers and common JS/CSS assets.

- Fixture profile/list startup: 3 requests and 948 JSON bytes before and after (includes shared profile/list initialization).
- Opening one operator conversation: 1 history request before and after. JSON: 749 → 774 bytes, only a 25-byte `has_transcription` flag. The transcript text is not duplicated in the payload.
- Opening either list/history: 0 media requests / 0 media bytes both versions.
- Explicitly viewing the synthetic PNG and WAV: 2 attachment requests / 1,712 binary bytes. Reading the saved transcript: 0 extra requests.
- Each successful uncached real preview adds one browser→Beintaskbot request, one Beintaskbot→Connect request and one Connect→Evolution request. Evolution's base64 JSON adds roughly 4/3 of the original binary length on that internal leg. Browser traffic is binary, not base64. Replaying audio within an already loaded player adds no upstream request.

## Tests and practical verification

Python regressions: 1,429 passed, 240 subtests passed, 4 existing Windows-skipped webhook tests; new media module has 39 passing cases (isolated PGlite PostgreSQL authority plus mocked HTTP). Covers companies, channels, conversations, employees, revoked membership, disabled integration/channel, DNS/private IP/redirect protection, MIME and size limits, truncation, timeout, old media failures, stored transcription and no AI/ticket side effects.

JavaScript: all 55 available test scripts, including lazy preview, stale identity cleanup, group/direct history, permissions, operator state controls and existing attachment behavior. Connect: 45 tests and TypeScript build pass.

Chrome against a local-only fixture server: PNG decoded at 640px; WAV played with a 1-second duration and no media error; saved AZ/RU transcription expanded. No real WhatsApp media was fetched and no customer message was sent. Browser-specific codec support and real Evolution retention remain deployment smoke-test items.

The pilot `/webapp` assets/entrypoints and bot.py are byte-for-byte unchanged from the base. Shared attachment backend is covered by existing Kommo tests and the full test suite. No interface functions were moved between `/webapp` and `/app`.

## Security findings and rollout

Read-only Railway checks confirm Evolution runtime `apikey` output and a public Postgres TCP proxy. No secrets are printed in this report. Current OAuth access provides variable names only, so database routing and the precise identity of the exposed key cannot be verified. Connect's paired `docs/PROTECTED_MEDIA_RELEASE.md` contains the detailed log suppression, coordinated credential rotation and private-DB cutover plan. These changes must be performed separately; do not remove the proxy or rotate active keys blindly.

After approval: deploy Connect first, test the protected `/media` route with an owned test message, then deploy Beintaskbot. No schema migrations are added. Check photo/audio (including old unavailable media), groups/direct chats, an employee with only dialogs rights, denial after rights revocation, and unchanged AI job counts during preview. Keep Nizam settings unchanged. For rollback, revert Beintaskbot first; the additive Connect endpoint can stay deployed, then roll Connect back if required.

Limitations: originals are best effort, not archived; corrupt codecs may only fail in the browser after MIME/signature checks; no audio transcoding is performed for unsupported browsers. Existing four Windows-skipped webhook tests require a Linux run. There has been no production acceptance test of this unreleased feature.
