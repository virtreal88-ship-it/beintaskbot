# Group company echo deduplication

Read-only production diagnosis, 2026-10-18. No production writes or replay.

GROSSAUTO.AZ: provider event `3EB046A75D1D599A69D930` has two persisted copies.
Connect IDs: outgoing `e3e2a39c-6f1f-4980-8a37-798ad5be2115`, incoming
`b7cfb615-ff72-4db7-8224-b6f2200019fc`.
Beintaskbot IDs: outgoing `e0365cb3-8c8e-4fc3-b7a5-c4e966f6a935`, incoming
`3272a889-01d4-4cab-8075-ed08a5c24d35`.
The outgoing provider key has participant LID; the incoming key has the same
LID plus participantAlt phone JID. Connect previously preferred participantAlt
and discarded the pair. Both stored copies therefore survived exact-participant
deduplication. The shared read projection also supplies AI context, so the same
failure affects AI context. Outgoing raw fromMe=true and source=linked_phone;
incoming fromMe=false. Neither record contains an AI authorship marker.

Connect now relays participantIdentity={lid,jid} only when both validated IDs
occur in one provider key. Signed webhook ingestion already stores this metadata.
The read projection resolves pairs only within tenant + exact group + provider
message ID + message type, with route validation and one-to-one conflict checks.
No global identity mapping is created. Text/time are never identity evidence.
An outgoing representative preserves company authorship and its media route;
sources retain all per-channel directions. AI uses the same projection.

## Historical limitation and rollout

Existing Beintaskbot records lack the pair; changing code alone cannot safely
collapse these historical LID/phone copies. They deliberately remain separate.
The original Evolution keys retain the evidence for the inspected event.
A separately reviewed metadata-only enrichment can restore the pair after
validating provider instance/channel, tenant, exact group, event ID and participant.
Do not replay message webhooks: that could trigger processing. Do not delete,
move or rewrite source messages, reset Human Mode or infer aliases by text/time.
No enrichment is executed or bundled in this change.

Future rollout: Connect first, Beintaskbot second. No schema migration.
No changes to group admission, employee authority, revisions, sends, settings,
WhatsApp sessions, or /webapp. Metadata supports read deduplication only and
must never confer employee or sending permissions.

Validation: targeted history/coordination SQL tests (50 passed), Connect
normalization/transport tests (13 passed), TypeScript noEmit build passed.
