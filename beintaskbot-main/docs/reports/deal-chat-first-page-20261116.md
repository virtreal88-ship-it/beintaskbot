# Deal chat first page — 2026-11-16

Baseline: main 49b639b01c7337c9123f93c193a68e0eb42d3ccb, production deployment 722dade2-0a4b-4190-954d-39f241b54264. Chrome owner session, read-only deal openings. No sends or business record edits. Mobile measurements use a 390×844 desktop Chrome viewport, not a physical phone.

| Case | Card first/repeat ms | Messages first/repeat ms |
|---|---:|---:|
| Desktop short (14) |341 / 328|5482 / 3344|
| Desktop long (120 loaded) |319 / 1201|9176 / 6895|
| Narrow short |658 / 318|4870 / 3999|
| Narrow long |324 / 323|6160 / 5210|

The old chain waits for deal metadata, then provider refresh, then reads cache again. Saved history HTTP reads took 305–407 ms; the refresh endpoint took 2694–8534 ms including its own DB/authorization/provider work. These HTTP measurements do not isolate pure provider time. The browser began at scrollTop=0 on every long chat. Hidden Info eagerly requested tasks and notes.

## Change

Show an empty deal shell immediately. Fetch deal metadata and 40 authorized cached messages concurrently. Refresh separately through the existing sync/cache pipeline, coalescing matching in-flight work. The incremental provider read uses the documented created_at[from] filter with a five-minute overlap, at most one page of 50 per validated talk, maximum six talks and a 12-second provider-read deadline. Overflow is visibly stale; it never silently treats a partial provider page as complete. No full history import on opening. Kommo reference: https://developers.kommo.com/reference/get-conversation-messages

Fresh membership and source authorization remain in the existing transactional page reader before and after provider IO. No new persistent or cross-user message cache. Background updates keep composer/media nodes and the original pagination cursor. A disconnected newer window is offered through an explicit freshly authorized latest-page read. Older pages preserve the reading anchor and deduplicate exact stored message IDs. Close, account/profile change and A→B switching invalidate/abort old work. Info tasks/notes load on first expansion. Header, pipeline picker and sending protocol remain unchanged.

## Verification

- 68 Python tests including real isolated PostgreSQL/WASM pagination with equal timestamps, concurrent new arrival, restricted source and membership revocation.
- Browser suite uses the shipped HTML/controllers and rebuilt production React bundle, 390/1440 widths, owner/limited fixtures. Slow/offline provider, 14/120 messages, empty history, repeated open, older pages, new arrivals, non-overlapping tail, draft/media preservation, A→B, closing before metadata, denial clearing and auth change.
- Controlled 1200 ms provider latency: baseline messages 1446–1518 ms; changed 159–234 ms. This is a synthetic HTTP-boundary comparison, not production performance evidence.
- Existing send/history, Info actions, reference forms, pipeline picker and mobile composer/AI layout checks also pass. Three older unit fixtures were updated for the existing rollout guards, receipt scope variable and intentional lazy Info loading.
- Production bundle rebuild has no content diff. Browser traces/screenshots are retained locally and uploaded by the existing Kommo reference CI workflow.

Production after-deployment measurements are reported separately after release.
