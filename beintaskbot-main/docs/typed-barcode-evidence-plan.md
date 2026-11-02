# Typed product-barcode observation contract

This is a narrow, optional product-identifier pathway, not general barcode/OCR
recognition and not a confirmed business fact. No live model-quality pass or old
Fəxri barcode restoration is claimed.

## Producer and consumer

Both existing visual-observation producers use the same bounded instruction.
For a single product image with exactly one clearly readable printed packaging
barcode, the model is asked to return only this optional plain-text block:

```text
Product packaging observed.
Product name: "KIRLANGIÇ sunflower oil"
Product size: "1L"
Barcode on product packaging: 4006381333931.
```

Name and size are optional JSON-quoted strings that retain their original script.
The fixed field names are English. No additional free prose is accepted with a
candidate, including RU/AZ contradictions. When a barcode is unreadable, ambiguous,
from multiple products, a screenshot, a phone or remote identifier, the producer
must omit the block and use its ordinary observations. The model must not repair,
infer or checksum-complete digits. This changes the observation prompt, not any
configured agent prompt. The existing input ceiling, 600-output-token limit,
cache keys and admission/retry rules remain unchanged.

The consumer independently requires one complete image observation and one
explicit packaging-label field, validates ASCII EAN-8/EAN-13/UPC-A length/check
digit, and rejects conflicts, repeated fields, unknown fields, negation,
remote/application/competing-object contexts, malformed quotes and unsafe decoded
field values. A separately complete contact field may coexist but never becomes
a candidate; a heading or identical digits under contact punctuation rejects.
Quoted JSON escapes cannot hide unsafe words or a second long numeric identifier.

This protocol only provides a request/validation contract. Real models may omit
or violate it; that leaves the barcode unknown. Ordinary prose such as "The bottle
barcode is ..." remains unknown. The tests demonstrate actual producer/consumer
plumbing with mocked HTTP, not compliance or accuracy of a paid vision model.

## Storage, privacy and provenance

The original observation and all generic redactors remain unchanged. Future
cache processing classifies before redaction and retains only the typed artifact,
observation hash and actual image-byte hash in existing JSON metadata. Reuse checks
those hashes, and receipt persistence binds the candidate to tenant, channel,
external ID, source hash, media version and observation index. Original receipts
without a downloaded-byte hash retain null; the surviving literal must still
independently classify. No hash or masked value is reconstructed.

Only private current/historical dialog context receives the unconfirmed candidate,
with same-image/same-product attribution. Fresh generic image text is also passed
through the existing redactor before context insertion; only the validated typed
field retains its digits. Historical lookup is exact-source and read-only and
cannot start processing. Public receipt, case and MCP whitelists omit the typed
field and retain existing redaction. No schema/DDL, access-policy, provider-budget,
knowledge-publication or send changes are included.

## Old evidence and the Fəxri limitation

The direct episode producer saves original bounded observation text; the knowledge
cache producer historically redacts before storage. Consumer projections mask
long digits again. Thus a masked projection cannot prove whether the original
literal survives. Existing exact receipts may qualify only if their literal and
structure already satisfy the contract; old masks cannot be reversed, and legacy
cache entries do not trigger new analysis just because this field is absent.

The exact Fəxri photo has not been classified or restored. Available authorized
connectors do not expose a server-side classifier for this exact stored receipt.
No raw receipt export, credential extraction, alternate runtime route or provider
replay was used. Missing/uncertain identity requires confirmation of only that field,
not a request to resend all previously supplied product/media information.

## Offline acceptance

Synthetic fixtures cover packaging fields; missing/unreadable/ambiguous/checksum
failures; phone/remote masquerading and formatted phone collisions; multiple
observations and changed source/tenant/channel/version; raw versus already-redacted
receipts; unchanged public/MCP redaction; and manual/automatic current/history scope.
Native disposable PostgreSQL tests use full schema/current psycopg to exercise
receipt commit/readback and cache JSONB update/reuse. Actual Responses JSON fixtures
mock only the HTTP boundary, run the real provider parser and both observation
producers through collect/persist/reload/private context, and assert no second call.
Quoted EN/RU/AZ names pass; extra contradictory prose in those languages rejects.
Fresh prepare tests prove generic numbers stay redacted while a typed candidate can
be used. Independent review and exact-head CI precede root-coordinated release.
