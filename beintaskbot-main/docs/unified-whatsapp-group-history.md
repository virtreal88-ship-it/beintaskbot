# Unified WhatsApp group history

Base: production main `7858d6e8`; Connect master inspected at `8c830d2`.
Branch: `codex/group-unified-history`. No production writes or deployment.

## Confirmed cause and production evidence

The operator list folds exact group JIDs into one row, but history previously
filtered by that row's single lead_id/channel. Pagination stayed in the same
channel. The AI worker also read only its sending-channel history.

Read-only queries in the existing Railway PostgreSQL console, with
`default_transaction_read_only=on` and a 10-second statement timeout, verified:

- Company stored as `Bein Systems`, group `İnnab SPA | Dine`: 21 messages on the
  primary WhatsApp and 8 on Texniki Dəstək (29 total, 28 incoming / 1 outgoing).
- Exact group identity count: one within this company.
- All 29 have provider message and participant identifiers; all 29 composite
  event/participant/type identities are distinct.
- Connect's own `connect.Conversation` / `connect.Message` counts match: 21 / 8.
- Another company named `Bein` also has 21 messages for this group. Those are
  excluded. Initial name-only diagnostic counts combined both companies and were
  corrected before implementing or testing the fix.

The current stored messages survive on both channels. This does not prove that
all WhatsApp history from before integration was imported; no backfill was run.
No message text, secrets or customer phone numbers were exported for diagnosis.

## Implementation

`tenant_connect_group_history.page` joins exact tenant + group JID routes to
messages by tenant + channel + local lead. Deduplication requires route-consistent
signed metadata, a provider event ID and an exact participant JID; LID/phone aliases
are never inferred. Missing or inconsistent evidence keeps both rows. Text and
timestamps never establish equivalence. Technical storage remains channel-local.

SQL deduplicates before applying the cursor/limit. The earliest stored copy is
the stable representative; all source channels and their original directions are
returned. Order is event time (created_at fallback), then message UUID. The cursor
is scoped to this group's union, not just the selected channel. No offset-based
history paging or client-side page deduplication.

The existing operator API retains membership/route checks and name overrides
per source channel. UI displays channel sources/directions and uses the message's
actual lead for protected media. Participant editing targets the actual source
channel, including older pages. Media remains click-to-load.

The worker uses the shared reader only for model context. Source admission,
newer-message checks, transcription, queue identity, delivery channel, group
revision/event fences and the final send check are unchanged. Context is bounded
to 80 retrieved rows, 30 formatted messages, no later than the source message;
existing text/audio limits and token accounting apply. Other-channel audio uses
only an already saved transcript; it is never newly transcribed by this read.

## Targeted verification

- New tests: short/long histories, 125-message pagination, dedup before pagination,
  ambiguous identities retained, exact source names/directions, tenant and group
  isolation, foreign cursor rejection, read permissions and no job/state mutation.
- Worker tests in Manual and Automatic prove other-channel context reaches the
  mocked model while only the authorized channel can send; duplicates stay fenced.
- Existing operator, group coordination and expert integration tests cover Human
  Mode, Pause/Resume, stale revisions/events and shared delivery protection.
- Existing media and route tests cover access checks and legacy /webapp routing.
- UI tests cover source-channel participant edits, protected media routing,
  repeated dialog opens, RU/AZ/EN and existing group controls.

## Release boundaries

No schema migration, message transfer/deletion, Connect code change, configuration
change, new service, real AI call or customer send. /webapp files/routes untouched.
Only merge/deploy this branch after approval. Production UI still uses the old
single-channel history until release.

The union query uses existing tenant/lead indexes and performs group-local window
sorting; very large histories may warrant measured indexing in a separate task.
No production load test was performed. Inconsistent historical identifiers remain
visible as separate messages. Live browser checks of the changed UI were not run;
UI validation uses the existing DOM test harness.
