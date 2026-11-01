# Typed product-barcode evidence: implementation and acceptance boundary

## Implemented after plan approval

`tenant_product_identifiers.py` accepts one complete image observation containing
one explicit, bounded field such as `Printed EAN-13 barcode on the bottle label:
4006381333931`. Packaging mentioned elsewhere is insufficient. Supported English
field grammar, ASCII EAN-8/EAN-13/UPC-A length and check digit, no competing barcode
field, no ambiguous/negative/remote/application/other-object context, and separate
complete contact fields are required. A phone heading above a barcode field rejects
the whole candidate. Unknown grammar/language remains unknown. This intentionally
under-accepts observations; it does not prove vision accuracy or product ownership.

The original observation and all existing generic redactors are unchanged. Future
cache processing retains only the typed candidate, observation hash and actual
image-byte hash in existing JSON metadata before redaction. Cache reuse validates
those hashes and never changes its cache key, starts an extra call, or retries an
unknown hold. Receipt persistence binds the field to the exact tenant, channel,
external ID, source hash and media version. Original receipts without an image-byte
hash retain `null` for that hash; their literal must still independently classify.
No byte hash or masked value is reconstructed.

Only private episode context receives the candidate, ahead of the bounded visual
description, with an explicit unconfirmed/same-product qualification. Historical
manual and automatic channel projections are normalized only within the existing
channel fence; a separate exact saved-receipt lookup cannot initiate processing.
Historical context keeps its original source and requirement for a supported
customer follow-up. No intent heuristic turns an unrelated old product into the
current question. Public receipt, learning-MCP and case projections retain their
redaction/content whitelists and omit the private typed field. No schema or DDL
change, agent prompt edit, permission change, or live knowledge write is included.

The old Fəxri source has **not** been restored or classified. Available authorized
connectors do not expose an exact-receipt server-side classifier; the case-source
inspector requires a separately identified case/campaign and exposes safe status,
not this original receipt field. No raw read/export, credential extraction, alternate
runtime access or re-analysis was used. It remains unknown whether the original
literal survives and satisfies the stricter field attribution.

Local acceptance covers pure hostile fixtures, current/historical dialog context,
bare/prefixed channel IDs, provider-call spies, actual collector propagation,
unchanged public/MCP redaction, and full-schema native PostgreSQL receipt/cache
commit and reuse. These establish code/data behavior, not a post-release model
quality pass. Parent coordinates the separate review, CI and release slot.

## Established source behavior

On deployed media merge ee77955:

- `tenant_episode_media.Execution.observe_once` returns the original observation
  bounded to 2400 characters. `tenant_media_evidence.persist` stores observation
  strings without numeric redaction in result JSON, with tenant/channel/external ID,
  source hash, media kind and processor provenance.
- `tenant_knowledge_media_cache.observe_once` calls candidates-policy `redact()`
  before writing the cached vision transcript. Generic long-number redaction removes
  EAN/UPC digits there. A later receipt that consumes that text cannot restore them.
- `_safe_text` separately masks long numbers in internal context and public read
  projections. The presence of `[phone]`/`[NUMBER]` in a projection does not prove
  which producer originally stored the evidence or whether literal digits survive.

Eight offline fixtures exercise both actual producer functions using mocked invokes,
then the actual receipt persist projection and unchanged redactors. No database,
provider/network call, cache retry or production read/write occurs. They prove
retention differences, not a barcode classifier or the content of the real case.

The exact Fəxri barcode remains OPEN. Its safe read projection alone cannot establish
that the literal survives in the exact original receipt. No masked digits were
reconstructed, and no image was re-analyzed.

## Proposed small implementation boundary

1. Add a pure typed-identifier extractor for image observations only. Its input must
   be a verified exact-source image receipt, never a user caption, staff hypothesis,
   free chat text, whole concatenated conversation, video sample or unrelated photo.
   Require an explicit barcode/EAN/UPC label plus directly described product-packaging
   context in the SAME observation. The literal must be present and fully readable.
   A valid checksum/length is only an extra consistency check, never identity proof.
2. Reject ambiguous, guessed or unreadable values, screenshot/remote-desktop address
   context, credentials, contact/phone-labelled digits, or an observation that mixes
   product and remote-window identity without separable origin. Product name alone,
   a long number alone, or customer text saying “barcode” is insufficient.
3. Return only a narrow candidate such as type, literal value, observation index,
   product-label context, verification state `observed_unconfirmed`, and immutable
   tenant/channel/external/source-hash/media-version provenance. Keep `untrusted`
   provenance: observing packaging does not prove an admin/POS diagnosis or current
   product registration. Never retain other raw numeric fields or remote identifiers.
4. For existing receipts, attempt typed extraction only from literal surviving
   original image evidence during the exact authenticated internal read. If the
   original contains a mask or lacks the required typed context, return no candidate.
   Do not change cache namespaces, retry unknown holds, trigger vision, or reconstruct
   digits from length/checksum/title/other messages.
5. For FUTURE already-authorized ordinary vision processing, extract the narrow
   candidate before generic redaction and retain only that typed field alongside
   the redacted observation, using existing JSON metadata/result storage. The cache
   artifact may retain candidate evidence but source attribution must be rebound
   and validated against each exact caller receipt. Old cache entries without a
   candidate remain unavailable; adding the field must NOT force re-analysis.
6. Supply candidates through a private, explicitly whitelisted generation-context
   field attached to their original photo/product. Do not change `_safe_text`,
   `global_safe_text`, external MCP/DTO redaction or public response serialization.
   Tests must prove the private typed field cannot leak through generic history/MCP
   DTOs. Do not silently attach a historical product ID to a distinct new topic.
7. When unavailable/uncertain, clarify only the missing barcode field. Already known
   product name, photo and voice must not be requested wholesale again. A candidate
   may be confirmed as a single field; it is not an automatically verified business
   fact and does not authorize creating/publishing knowledge.

No new schema/index/request-path DDL is needed for this design. Exact readback and
source fences must be reused; a minimal implementation should be a separate commit/
PR after root reviews this boundary. No new vision/ASR budget is assumed.

## Acceptance fixtures required before implementation is READY

- Packaging + explicit readable EAN/UPC literal: narrowly typed candidate with exact
  source provenance; text redactors still mask the same digits in generic output.
- Packaging EAN plus a real-looking phone on another line: preserve ONLY the barcode
  candidate; phone remains redacted. Mixed labels or ambiguous origin reject both.
- AnyDesk/Your Address, remote IDs, credentials and contact numbers with checksum-like
  lengths: no candidate, including injected words “barcode”/“packaging”.
- Customer caption claiming a barcode: no image-evidence candidate.
- Missing digits, existing masks, uncertainty/guessing and checksum-only values:
  no candidate and no provider retry.
- Multiple screenshots/observations: no merging field identity across origins,
  applications, photos, tenant/channel/source versions or unrelated historical topics.
- Cache producer already redacted versus direct episode producer retaining literal:
  only the latter can support existing-evidence reuse when all context checks pass.
- Public DTO/MCP projection remains redacted; only authorized internal generation
  receives the typed candidate. No source update, knowledge publication or remote use.

## Exact missing-evidence boundary

Before claiming the real Fəxri barcode is reusable, an authorized exact receipt read
must establish the producer, image/source version and surviving literal+packaging+
barcode-label evidence. This audit did not read raw production observations. If that
receipt is already redacted, existing safe projections cannot repair it; the next
step is single-field confirmation or separately authorized new evidence, not replay.
