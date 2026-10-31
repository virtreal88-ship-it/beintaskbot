# Latest unread voice: AI Reply admission and scoped diagnostics

This is a defense and a read-only diagnostic surface, not a claim that an existing customer's audio can now be downloaded or transcribed.

On main 373e436, manual AI Reply accepted a current episode receipt marked unavailable/unsupported/error/unknown and continued into KB retrieval and the main model with older history. Its KB query could also fall back to the voice row's legacy body even though preparation had explicitly cleared that body from the model context. A synthetic latest-voice regression reproduces both behaviors. Existing prompts instruct the model not to infer skipped content but are not an admission fence.

AI Reply now requires a processed, nonempty exact-version transcript when the latest customer request itself is audio/voice. Known partial, unknown, missing or empty transcripts stop before KB retrieval and main-model dispatch. The request stays quarantined; no paid flight or reservation is released and no replay is started. A newer independent text request and the developer-consultation draft path retain their existing behavior. The reply cache policy is versioned so replies made under the older permissive policy cannot silently replay.

The ordinary authorized dialog history now supplies fixed-enum audio diagnostics from exact-message/version receipts and bounded metadata-only projections of the same dialog's copilot runs. No copilot text, prompts, raw errors, credentials, signed URLs, audio bytes or logs are returned. For pre-existing completed attempts, only the status/code recorded in context_usage is available: missing stage remains unknown. New blocked replies persist a small media_failure object without changing unknown run status or retry policy.

Open the audio card's Audio diagnostics details to see the existing opaque attachment ID plus saved status/code/stage/time. Opening details does not request audio, transcribe, generate or send anything. If no diagnostic was saved, it says so. This allows an employee to identify the exact existing voice; it does not ask the customer to resend it.

Code-only evidence cannot determine the HTTP status/MIME or transport failure of the specific live voice. The pending scoped production-log approval is not bypassed by this work. The automatic worker's separate admission safeguard is not included here; its stale-query fallback and missing-media generation path require separate integration/review.
