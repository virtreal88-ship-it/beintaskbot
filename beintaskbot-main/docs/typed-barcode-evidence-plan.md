# Typed product-barcode evidence: separate plan, not a released fix

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
