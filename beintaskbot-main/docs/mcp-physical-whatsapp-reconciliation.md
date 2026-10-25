# Physical WhatsApp reconciliation (Issue 260)

MCP schema `2026-10-25.1` adds four tools alongside the existing logical overlay.
No second merge implementation or admin UI is introduced.

1. Call `whatsapp_direct_reconcile_preview` with `channel_id: "all"` and `limit: 25` for a tenant-wide read-only scan. Follow `next_cursor` until `scan_complete`. A changed inventory invalidates the signed cursor and requires restarting. Channels are scanned separately; numbers are never combined.
2. Inspect pair `state`, exact `conflicts`, provider receipt/source counts, opaque route and evidence message references, media counts and dependency counts. `review_blocked` is a hint, not a verified duplicate. No name, phone, avatar or owner guess upgrades identity. Existing signed `directIdentity` receipts and protected provider aliases are consumed by the unchanged identity gate. This interface cannot manufacture or register arbitrary aliases.
3. For `verified_eligible`, owner-authorized pairs in the OAuth connection's exact channel, obtain a fresh preview and call `whatsapp_direct_reconcile_apply` with its exact `pair_id`, `precondition`, a new UUID `request_id` and `confirm: true`. Reuse the same payload/UUID on retry. Never merge a blocked pair or bypass a dependency conflict.
4. Re-preview the same pair. `already_merged` exposes current message/media, route, stopped-state, job, draft and read-marker counts. Existing source-link redirects remain guarded by tenant, channel and original lead checks.
5. Run `whatsapp_direct_reconcile_rollback_preflight` for a dry-run under the existing dependency locks. `whatsapp_direct_reconcile_rollback` is an explicitly confirmed recovery operation, not a production smoke test. New data/dependency changes block unsafe recovery.

## Permissions and deployment

Read tools require existing `ai_learning.read`. Physical writes require the **new** explicit `ai_learning.direct_reconcile` OAuth grant and an active owner. Existing logical `ai_learning.dialogue_merge` grants are not silently widened. The owner must approve the physical permission through the ordinary connector OAuth consent flow once; no Railway Console or credentials are needed for individual pairs. Existing refresh tokens retain their original scopes. Refresh connector tools after deployment if a client caches `tools/list`; initialize reports the version above.

All operations use the existing #248 transaction, table locks, snapshots, request ledger and audit. The MCP handler serializes its response before committing, so encoder/audit failures roll back physical changes. No customer messages, paid AI requests, AI Resume, session changes or new services occur. Private JID/LID identifiers, contents, filenames and timestamp-touch route keys are not returned. UI remains unchanged and retains its current design.

## Validation

`BEINTASKBOT_TEST_POSTGRES_DSN` must refer to a disposable localhost PostgreSQL server. Run:

```
python -m pytest tests/test_direct_reconcile_mcp.py tests/test_connect_dependent_reconcile_postgres.py tests/test_connect_identity_inventory_postgres.py
python -m pytest tests/test_ai_learning_mcp.py
```

The E2E test uses real HTTP MCP calls and synthetic 85-message/14-media data. It tests discovery, merge, idempotent retry, current counts, old links, Human Mode, rollback preflight and exact restoration. Native #248 tests also cover concurrency, webhook/dependency fencing and rollback conflicts. Fictional 476/3357-shaped data stays blocked without provider proof.
