# Missed call service events

`call.missed` is a signed Connect event, not a customer message. Its data contains
`externalCallId`, `channelId`, a direct `conversationId`, `type: call`,
`direction: incoming`, `callStatus: missed`, `isGroup: false` and boolean `isVideo`.
The standard envelope `occurred_at` must be timezone-aware. Only a confirmed
provider timeout is eligible; offers, accepts, rejects and generic termination
must never be inferred as missed.

Worker authenticates the existing tenant signature and active source, reuses
source-local verified direct identity resolution, and stores a `system`
`call_missed` timeline item. The external key hashes source plus exact call ID.
Retries cannot duplicate it across new delivery envelope IDs. Group calls are
rejected until an exact group contract exists. Processing returns before all
message AI/control/consultation paths. No reply, auto-reject or customer
notification is sent. The dialog shows localized audio/video label and time;
it is not treated as a downloadable attachment or quotable message.

There is no schema migration. Existing subscriptions require an explicitly
scoped rollout to add CALL on Evolution and call.missed on the already-owned
Connect callback. Preserve other events, URL, secret, active state and rejection
settings. Code deployment by itself does not prove events are flowing, and
historical missed calls are not backfilled. Test with synthetic events; no real
calls are placed by this change.

## Rejected incoming calls

`call.rejected` uses the same signed source-local envelope, with
`callStatus: rejected`. It stores `call_rejected`, displayed separately as
"Rejected incoming call" / "Rejected incoming video call", never as missed.
Connect must establish an incoming direct offer and its provider-confirmed
rejection with the same call ID, source and verified identity before emitting
this event. A generic termination is not sufficient evidence.

The worker validates the exact event/status pairing. Both final statuses share
one source+call-ID deduplication key; a contradictory final status fails closed
rather than producing two timeline rows. These system events are excluded from
AI context and return before conversation control or automatic replies. Existing
managed webhook subscriptions add `call.rejected` alongside `call.missed`
atomically, preserving all previous events and webhook settings. This release
does not backfill historical calls or send test calls/messages.

## Correction protocol (local candidate; requires matching Connect release)

A source-authenticated `call.invalidated` event carries `callStatus: invalidated`,
`callRevision: 2`, and `correctionOf: call.rejected`. It retracts only a previously
inferred rejected classification after contradictory provider evidence. It never
asserts that a call was answered, completed or missed and supplies no duration.
The timeline shows a neutral localized “Call status corrected” service item.

Rejected and missed events are revision 1 (omitted for legacy events). A correction
can change only rejected revision 1, not missed. Exact source/call-ID projection
uses the existing unique external key and a locked row, preserving prior evidence
and original occurrence time. If correction arrives first, it inserts the neutral
revision-2 row; later rejected retries cannot restore the stale classification.
Source, media type, canonical conversation and equivalent lead checks precede any
update or stale-event ignore. These records remain outside all AI histories.

Deploy compatible Worker validation/rendering before subscribing to the additive
`call.invalidated` event. Enable rejected and invalidated together, never rejected
alone. Native PostgreSQL concurrent-delivery tests must pass before production;
PGlite tests verify SQL/order behavior but not independent-session races. No real
calls or historical reconstruction are performed by this implementation.

A historical raw identity may differ after an already-approved route reconciliation.
Corrections resolve that old identity read-only through source-local established
provider mappings and active dialog equivalence; absent/ambiguous mappings fail
closed. Existing equivalent-lead and media/call-ID checks remain mandatory. This
runs inside ingestion's canonical dialogue lock; mapping/reconciliation writers
fence old and new canonical routes before changes. No new alias lock is acquired
after the message row lock, and corrections never create identity proof themselves.
