# Bounded native offline catch-up

The pinned whatsmeow version reports `OfflineSyncPreview` but does not request `offline_batch`. A hosted source can authenticate and acknowledge activation while receiving no messages and never reaching `OfflineSyncCompleted`.

The new path defaults off. `CALLS_NATIVE_OFFLINE_CATCHUP_SOURCES` is an explicit comma-separated list of existing source UUIDs. Begin with the agreed test source only; then expand after actual inbound journal and conversation evidence. This is a protocol queue read, not an outgoing customer message, history replay, QR reset, or permission grant.

One connection-local loop requests up to 50 batches of 100 events, with five seconds between requests and a five-second write deadline. Duplicate previews do not start extra loops. Completion, disconnect, logout, source replacement, capture failure, app cancellation, or write failure stops the loop. Hitting the cap produces a diagnostic and never reports success. No force-flush, synthetic ACK, or clearing of retained data is used. Encrypted durable capture remains before acknowledgement.

Activation of an existing source also requires a validated encrypted incoming message in its current generation. A positive cursor from a group metadata read or an outgoing echo alone is insufficient. The check is scoped by tenant, source, account and generation and bounded to the latest 100 retained events; uncertain evidence fails closed.

Protocol background (not proof of our runtime result):
- https://github.com/WhiskeySockets/Baileys/blob/master/src/Socket/socket.ts
- https://github.com/devlikeapro/waha/issues/2349

Implementation is original Go code using the existing whatsmeow socket wrapper; no Baileys/Evolution code was copied. Live acceptance remains required: queue completion, incoming message exactly once, media and statuses, then source-preserving cutover.

An additional confirmed projection blocker on the test source was an authenticated group message whose sender and alternate included a linked-device suffix. The old direct-address validator rejected it, returning 409 for every delivery retry. Projection now accepts bounded protocol device addresses for participants only and retains the exact account/domain, conversation, encrypted event and source binding. Tests cover device bounds, cross-conversation rejection and idempotent ingestion of retained device-addressed events. No journal row is edited or skipped.
