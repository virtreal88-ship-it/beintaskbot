# Historical WhatsApp evidence

Bases: Beintaskbot origin/main 3debb64e; Connect origin/master f4577c1.
Two coordinated branches named codex/history-media, no production deployment.

## API and review

Existing knowledge-candidates extraction and batch endpoints accept optional boolean
`allow_external_media` (default false) and `allow_paid_asr` (default false).
The latter requires the former. The UI must obtain explicit employee permission
before setting these flags: image/page/frame bytes cannot be guaranteed PII-free.
No existing agent/company setting is changed. Same request ID cannot change consent.
Consent is included in fingerprint/replay checks. No automatic uncertain paid retry.

Existing request detail returns `request.media_results`: source `id`, `channel_id`,
`lead_id`, `role`, `happened_at`, `kind`, `status`, safe `code`; SHA256 when downloaded,
duplicate source ID, sampled frame timestamps/page numbers where applicable.
Statuses: processed, unavailable, unsupported, error, pending_processing.
`processed` means bounded evidence was decoded/extracted/supplied, never successful
resolution or verified knowledge. Candidate review, confirmation gates and existing
Knowledge Sources publication remain mandatory. Original conversations are not copied.

Media-only messages are no longer omitted. Captions and author roles stay in input.
Unavailable attachments remain explicit evidence gaps. Normal unified-group message
deduplication is reused; SHA256 prevents identical attachments becoming multiple
vision inputs. Each canonical source is resolved through its own authorized channel;
no foreign channel credentials, caller URLs or channel substitutions are accepted.
Source access is checked before/after downloads and before paid calls/publication.

## Capabilities and limits

- Images: existing exact vision model registry, low detail, 2 visual inputs per
  extraction. No automatic model change or high-detail retry.
- Audio: reuse exact tenant/channel/message existing voice receipts. Missing audio
  is pending unless both flags authorize one new ASR call, using the existing
  transcription provider/model configuration. Real decoder limit is 60 seconds.
  Redacted transcript and model provenance are stored in the extraction receipt;
  no CRM message body is rewritten. Recognition quality AZ/RU/EN is NOT proven by
  mocked-language tests; owner-approved synthetic speech quality check remains.
- Video: protected /media supports MP4/WebM, 16 MiB, real decoded duration <=60s,
  native decoder timeout. Existing chat already renders video controls from the
  authenticated blob. At most start/middle frames are analyzed (not a full-video
  understanding guarantee). With explicit paid ASR permission, an available audio track can use the same
  single reserved transcription call (60s maximum); frames remain bounded.
- Documents: protected PDF/TXT/DOCX <=5 MiB, existing bounded extractor. No Office
  execution, external relationships, arbitrary URLs or additional formats. Text is
  masked before model input. Textless valid PDFs can render first 2 pages locally
  for permitted vision input; renderer absent => explicit unsupported. Rendering
  does not claim OCR success. Encrypted/corrupt/oversized PDFs fail closed.
- 4 files, 20 MiB total input, 2 visuals total; 10s each download, 65s admission
  window, 240s extraction deadline. Larger histories remain paginated. Context is
  bounded after added media text; truncation is explicit. Decoders have fixed file/
  pipe protocols, fixed formats, timeouts and private temporary files, no shell.
- Existing company call/token caps apply. ASR permission reserves 2 calls instead
  of 1 and an extra 8000 tokens conservatively. Uncertain ASR usage retains its
  reservation. This is not a hard currency-price guarantee. Output remains 1800
  tokens, documents/transcripts up to 4000 chars each; text proof is masked.

## Integration with Astra

Deploy coordinated Connect change first (after separate approval); Beintaskbot
must see matching protected /media behavior and identity headers. Connect Docker
image adds ffmpeg; Beintaskbot runtime needs ffmpeg and pdftoppm for optional
video/scanned PDF paths. Missing tools return explicit statuses, never success.
Apply additive `media_results JSONB` column via existing schema bootstrap. No new
service, bucket, parallel knowledge base, WhatsApp session, AI worker or /webapp.

Evolution retrieves historical bytes best-effort from the stored message key;
deleted/expired provider media cannot be reconstructed. No archive/import was added.
Do not test real customer attachments or paid recognition without separate consent.
No real AI/provider/media requests were made during development.

Verification: broad synthetic regression 276 passed + 19 subtests; final media
suite 28 passed (real synthetic video, audio track and PDF rendering included).
Existing protected-video DOM test passed; this is not a real-browser codec check.
Connect: TypeScript build + 23 protected media tests passed. All ASR responses
were mocked, so real AZ/RU/EN recognition quality remains unverified.

