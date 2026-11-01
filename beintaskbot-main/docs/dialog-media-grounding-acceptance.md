# Dialog media grounding

Synthetic regressions mirror a product image/caption, a separate remote-support
window, an unverified staff reply, and a later voice follow-up. They do not replay
an actual customer conversation or call a paid provider.

- Preserve sender captions independently of OCR; exclude legacy audio bodies.
- Keep exact cached photo evidence historical, with source IDs. Do not re-analyze
  old attachments or imply that they belong to a distinct new topic.
- Carry safe customer/employee/AI provenance through grouped history projection.
- Label image/document observations by modality; video observations remain samples.
- Read exact-version saved receipt status in two bounded queries for dialog history.
  Do not expose OCR text, change schema, or use audio status to certify an image.
- Refresh visual status without replacing media nodes; replacement-version receipts
  cannot certify an old preview. Count saved observations separately from pixels.
- Keep existing numeric, phone and secret redaction unchanged. No checksum-based
  barcode exemption is introduced: legacy plain OCR cannot prove identifier type.
- Ground application/license claims in the identified window and exact wording;
  employee guesses and conditional KB suggestions are not confirmed diagnoses.

The authoritative tenant correction (products cannot be created through POS) is
not hard-coded into global behavior or published as new knowledge. It still needs
an authorized tenant knowledge change if it is absent from applicable sources.
The code now requires case evidence plus applicable verified knowledge before
asserting such a mechanism.

## Acceptance still required after coordinated release

Read the authorized actual conversation in the real UI: caption intact, saved
photo status correct on reload, saved-observation counts distinct from new pixels,
voice current, and no cross-window license attribution. A separate authorized model
replay is needed to assess answer quality; fixture assertions test evidence and
prompt contracts, not a guarantee against model hallucination.

## Validation caveats

Broad local suite initially found an ambiguous legacy video-label fixture; fixed
by recognizing existing sampled_frames metadata without inferring video from
incomplete coverage alone. Two group-history tests also fail unchanged on base
89583dd: manual-mode job admission expects a job, and foreign anchor fallback
expects history rather than the current 404 safety boundary. Those baseline
expectations are not changed here. No ASR/provider calls or production writes.
