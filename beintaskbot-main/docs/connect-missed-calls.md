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
