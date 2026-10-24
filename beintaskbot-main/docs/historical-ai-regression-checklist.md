# Historical AI regression check

Every AI behaviour change must be checked against this compact historical
sample before it is described as behaviourally verified. Unit, browser and
synthetic tests remain required, but they do not prove a reply is useful for a
customer.

## Fixed sample

Use exactly five distinct, recent conversations from the approved tenant and
channel. Record the selected message UUIDs only in the private report; do not
commit customer text or identifiers. The selection must cover the categories
in [`tests/fixtures/historical_ai_regression/sample.json`](../tests/fixtures/historical_ai_regression/sample.json):

1. ordinary technical request;
2. follow-up after clarification or operator involvement;
3. warehouse/settings request that can require an Expert;
4. unavailable audio or image context; and
5. a group, pause, handoff, missing-knowledge or other deliberate policy stop.

Only a direct customer message with verified channel identity can enter the
paid replay. A group, ambiguous JID/LID, employee message, or media with no
persisted context is reported as an explicit non-paid stop. It must never be
substituted with a different customer message just to obtain five replies.

## Procedure

1. Run the read-only inventory and select the five messages using the fixture.
   Confirm tenant, channel, direct identity, author role, time and provenance.
2. Run `preflight` through the owner-only historical replay route. It confirms
   scope, current model price, the shared tenant ledger, five-source ceiling
   and the no-retry rule. This step has no provider call.
3. Obtain a separate owner approval before `execute`. It may use the existing
   `$5` tenant cap only; it must not change Simulation queue, conversations,
   CRM, WhatsApp or Active mode.
4. Read the saved private report. For each sample record: source question and
   author role, expected policy, virtual reply or stop, knowledge references,
   Expert result, quality status, ledger receipt and later staff comparison.
   Only history at or before the selected message is supplied to the model.
5. Compare before/after reports only for the same fixed categories. A later
   employee reply is comparison evidence and is never included in model input.
   A successful provider call is **not** a correct answer: `not_reviewed`
   remains the status until an employee records a quality review.

The runner deliberately uses a one-shot call key. It will not rerun an
existing historical source after a deployment; retain the private report and
compare it with a separately approved, equivalent fixed sample. Never clear a
reservation or replay a source to force a result.

## Required report outcome

Mark the change `not checked on real answers` when no owner-approved replay
was executed. Do not call this a pass. Report exact media diagnostics (for
example `simulation_audio_transcription_failed` or
`simulation_image_analysis_not_requested`) and precise group binding stops.
These are valid safety outcomes, not model successes.

The existing commands and owner-only HTTP transport are documented in
[`whatsapp-recovery-operations.txt`](whatsapp-recovery-operations.txt). No
new queue, public endpoint or UI is part of this check.
