# Separate analysis evidence from later request summaries

The audio card's latest diagnostic can originate from a completed Copilot request's context_usage metadata. Older summaries saved status/code but not failure_stage. A later request summary could therefore replace the visible original receipt's known stage with unknown and its timestamp could be mistaken for a fresh ASR attempt.

Keep the latest request diagnostic unchanged and expose the original exact-version analysis receipt separately. The original receipt has its own stage and timestamp. Its pre_dispatch_proven boolean uses the same strict server-owned criteria as retryable_audio_download_failure: exact version/provenance, failed audio/voice download stage, known generic failure code and no transcript/observation. A missing record or ambiguous/paid stage is not proof. Never apply the older receipt's phase to the newer request merely because their status/code match.

This flag describes that saved receipt only. It does not authorize an ASR retry, clear an unknown flight/cache result, release a reservation or modify any retry policy. Existing rights, tenant/channel/message/version checks and unknown/paid fences remain unchanged. The projection reads metadata only; no original audio, prompt/private Copilot text, provider URL or raw error is returned.

A separate supported attachment read (the already-authorized audio.src endpoint) can test transport without invoking ASR: one Range GET is bounded by the existing download limit and scope. It supplies current HTTP status, validated Content-Type and Content-Range or a sanitized JSON error. A success does not prove transcription; raw upstream MIME is not generally exposed. Unknown paid analysis must not be retried on the strength of a successful transport read.

No live files, production logs, ASR, paid provider calls or sends were used while preparing this change. Actual Kovser read/analysis failure is not established by these code tests.
