# Support-case live pilot guard

Live extraction stays off unless `SUPPORT_CASES_LIVE_EXTRACTION_ENABLED` is true **and** all four server-side scope variables are valid:

- `SUPPORT_CASES_LIVE_EXTRACTION_TENANT_ID` — tenant UUID.
- `SUPPORT_CASES_LIVE_EXTRACTION_CHANNEL_ID` — Connect channel UUID owned by that tenant.
- `SUPPORT_CASES_LIVE_EXTRACTION_CONVERSATION_ID` — exact conversation ID.
- `SUPPORT_CASES_LIVE_EXTRACTION_START_AFTER` — RFC3339 timestamp with timezone.

The queue accepts and claims only that exact conversation. It verifies the source message belongs to the tenant/channel/dialog and its persisted `happened_at` is at or after `START_AFTER`; extraction is also bounded to the interval from `START_AFTER` through the claimed message. Delayed older webhooks, conversation history before the pilot start, and pre-existing backlog are not eligible. Missing or invalid scope fails closed. Set the four scope values for the owner-approved test dialog before separately enabling the flag; do not use a broad channel or tenant scan.

To stop, unset the flag or set it false. Workers stop claiming and recheck the scope immediately before each provider call. A request already in flight cannot be guaranteed to cancel; its result remains a private review candidate and is never auto-published. This switch does not change AI-agent settings. Turning extraction off does not delete or replay queue rows.
