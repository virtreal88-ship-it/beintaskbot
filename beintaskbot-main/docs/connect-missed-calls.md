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

For operators, a confirmed `call_missed` timeline item contributes one unread
count, like an incoming message. Opening its conversation advances the normal
route read cursor through that item. Other system call events (rejected,
observed or corrected) remain informational and do not add unread counts or
become read-cursor targets. A first-visible conversation uses its latest
ordinary incoming/outgoing message as the baseline, so a newer missed call
remains unread even when it is the conversation's first displayed call event.

The source-bound WhatsApp call engine reports incoming call lifecycle through
its ordered gateway snapshots. When a provider-confirmed `ended` snapshot closes
an inbound call that no employee claimed, the worker inserts the equivalent
`call_missed` timeline item using the existing tenant/channel/conversation/lead
binding. The item uses a stable call-derived ID and external key, so replayed
snapshots cannot duplicate it. Calls claimed by an employee (including calls
they reject) and outbound calls do not become missed-call items. This makes
native-engine missed calls use the same unread and open-chat read cursor as
Connect call events; read state does not alter the dialog's "does not require an
answer" lifecycle state. No schema change or extra message to the customer is
involved.

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
managed webhook startup synchronization adds only legacy `call.missed`. Rejected
and invalidated require an explicit single-tenant activation after every Worker
and Connect instance is upgraded; `enable_rejected_call_subscription` verifies
the owner, stored webhook ID and expected URL, then adds both events atomically,
preserving previous events and settings. There is no all-tenant activation. This release
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


## Neutral incoming observation

`call.observed` carries `callStatus: observed`, `callRevision: 0` and
`outcome: unknown`. Only authenticated Evolution evidence joining an incoming
direct offer with its exact termination is admitted. Required `offerOccurredAt`
and `terminatedAt` are timezone-qualified, ordered within 30 minutes;
`occurred_at` equals `terminatedAt`. These are evidence, not talk duration.
No answered/missed/rejected outcome is inferred from generic termination.

The `call_observed` system row has a neutral RU/AZ/EN label. Later proven
missed/rejected revision 1 or invalidated revision 2 updates the same tenant +
source + call ID row, retaining original occurrence and observation evidence.
Reverse/repeated neutral deliveries never downgrade a known result. Conflicting
peer, media or provider facts fail closed. These rows are outside AI context and
inbound automation. If a late known outcome has an earlier provider time, the
original neutral row time remains unchanged; the new evidence retains its time.

Activation is explicit and single-tenant through
`enable_observed_call_subscription(profile, expected_webhook_id=...,
expected_url=...)`, after matching Worker and Connect releases. Startup still
adds only legacy `call.missed`. Historical restoration is a separate task and
requires complete authenticated facts, not screenshot/timestamp guesses.

Observation retraction uses `call.invalidated`, revision 2,
`correctionOf: call.observed`, `correctionReason: provider_contradiction`,
original offer/terminate timestamps and identity, with no outcome. It retracts
only a neutral observation, never a proven missed/rejected classification.
Priority is provenance-specific: observed0 < observed-invalidated2 < known1
< rejected-invalidated2. Thus reordering cannot erase proven evidence or restore
an invalidated observation. Activation adds observed + invalidated atomically.

Retractions are durably fenced in `saas_connect_call_retractions`, keyed by
validated tenant/channel/call and original signed peer/media/timing. Every call
ingestion takes the call-key transaction lock before alias/dialogue/row locks.
After signature, rollout and active-channel authorization, observed corrections
bypass mutable identity resolution. They update only an existing exact-provenance
row. Before-original corrections create only the ledger entry and receipt, never
a CRM message/lead/route; a later matching observation is suppressed. Known
outcomes remain admissible. Conflicting ledger/projection evidence rejects the
whole transaction. Reconciled rows match preserved original provider identity,
not a fresh alias guess. No external action or AI work occurs in this path.
