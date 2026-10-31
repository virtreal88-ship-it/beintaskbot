# Audio MIME and saved download failures

Code-only investigation, 2026-10-31. No production logs, customer bytes, provider calls, tags or sends were accessed.

The protected `/media` endpoint is intentionally used by shared manual/automatic media extraction. `/audio` is a distinct incoming-only legacy contract with a 24-hour expiry; switching endpoints would change admission and is not the fix.

Connect source `fad27a64da4c53fbd220e5130ac1a89b64f4e6f1` rejects audio/opus, audio/x-m4a and audio/m4a even when bytes contain supported Ogg/Opus or MP4 audio. The paired Connect fix canonicalizes these named aliases only after matching container signatures. This worker also canonicalizes them, under the existing byte, identity and timeout bounds, before its existing full ffmpeg decode. Generic/missing MIME, raw Opus packets and spoofed containers remain rejected.

Evolution 2.3.7 returns the stored message's MIME and mediaType `audioMessage`, which Connect correctly expects. No mediaType mismatch is demonstrated: https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/src/api/integrations/channel/whatsapp/whatsapp.baileys.service.ts#L3837

A second defect is durable reuse of old extraction download failures before a fresh fetch. A newly authorized request may now bypass only an exact-version audio/voice receipt whose server provenance is knowledge_extraction, status is failed, diagnostic stage is media_download and failure_code is the generic processing failure, with no known transcript/observation. Normal rights, version, flight and spend fences still apply. Receipts are not deleted; flights are not reclaimed. Paid, partial, unknown, unproven and media_validation-stage outcomes remain protected. Older audio provider errors could retain media_validation, so this stage cannot prove absence of dispatch. New ASR calls set audio_transcription before dispatch.

Validation uses synthetic tones, actual protected downloader plus real local ffmpeg decoding, and a mock ASR. The baseline fails alias and saved-receipt regression tests; fixed code reaches exactly one mock ASR and supplies its transcript to the context. This does not establish the MIME or root cause of any specific live Kovser message.
