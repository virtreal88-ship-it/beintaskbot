# Case-bound media evidence

Scope is the selected support request, using its stored `start_message_id` and
`end_message_id`. Existing `_boundary_context` validates tenant, channel, dialog,
endpoint existence and `(timestamp,id)` order. The read path inventories only
media inside that interval, including attachments omitted from sparse case
source references. It does not substitute every extraction reference or the
whole customer conversation. A later request's nonoverlapping interval excludes
older files. The existing 80-message context bound remains; absent, invalid or
larger boundaries produce explicit uncertainty, never a wider scan or analysis.
Boundary validation means route/order checks, not new human approval of the AI's
segmentation. The existing boundary editor remains authoritative.

A read-only projection derives per-attachment outcomes and aggregate coverage
from the same extraction/shared media receipts. Missing historic results are
`unknown/status_not_recorded`, not proof that analysis did not occur. Consent
stops are `pending_consent`; unsupported formats, errors, unknown provider
outcomes and sampled video remain explicit. Caption and UI preview are not
analysis evidence. A saved result is reused only for exact attachment/version;
replacement/withdrawal cannot reuse a stale observation.
An image is complete only with saved nonempty visual text; legacy
`processed/vision_supplied` without an observation remains partial. A stripped
reuse stub is enriched from the exact current shared receipt before reporting
case completeness or supplying an improvement. A historical availability flag
cannot survive withdrawal of that shared result.
This current shared-result check is mandatory for every reused receipt,
including newer receipts that copied visual text; a copied payload cannot
restore evidence after withdrawal or a version mismatch.

`public_request.status` can now be `partial`, with the original execution state
in `execution_status`; database extraction state/idempotency is unchanged.
Fragment diagnostics are labelled `extraction_fragment`. Case UI/MCP diagnostics
are separately labelled `case_interval`. Metadata is visible under existing
read scopes; saved text observations require the existing content scope and
redaction. No raw media/URLs, provider inputs or secrets are returned.

Improvement uses a fresh bounded snapshot of the existing case interval, rather
than the original potentially multi-case extraction. It consumes saved media
observations/transcripts as untrusted evidence. Missing/incomplete results block
an analysis improvement before preparing a new model reservation, so a caption
cannot silently stand in for the photo. Missing stored boundaries and context
overflow also fail closed. Explicit translation remains a text transformation;
it does not claim to analyse missing media or grant analysis permission.

The existing knowledge extractor/campaign lease, observation cache, monetary
reserve/settle logic, consent, tenant/stop and token/daily guards remain the
processing mechanism. Unknown cache outcomes are now distinguishable from
ordinary errors; the cache still blocks repeats. There is no new queue, provider
or permission. Partial batch/live results are recorded without auto-retrying
unknown media. Public API/UI reads do not start processing or warming downloads.
Photo 20 MiB, video 100 MiB / 120 seconds and existing per-fragment bounds remain.

## Verification and remaining execution gate

Offline regression covers 14 files inside one request in a multiyear dialog,
files before/after, later request isolation, absent boundaries, version changes,
tenant isolation, seven existing bounded cache-reuse pages, consent/errors/
unknown results and read-only UI partial coverage. The image regression runs
actual PNG bytes through the existing approved campaign/lease, real isolated
monetary reservations and mocked Vision, persists the observation, then reads
it through the original case and MCP. The technical fact is absent from caption.
Repeating the request does not download or invoke Vision again.

This PR does not start production work or automatically enqueue all missing case
files. Bounded admission for missing media must use an already approved existing
campaign/single-message consent and exact case/source version; otherwise it stays
pending consent. No bulk historical processing, new grants, limits, or policy
activation is performed. The current specific production image preflight reports
`owner_consent_required`; neither its historical unknown result nor that missing
consent is replaced with an automatic paid retry.
