# WhatsApp history campaign foundation — NOT a deployable release

Base: origin/main 3debb64e. Branch: codex/whatsapp-knowledge-backfill.

Implemented and tested independently of the in-progress multimodal branch:
- Additive, opt-in campaign and fragment tables; existing message/candidate tables untouched.
- Preparation records selected tenant-owned channels, analysis agent and hard call/token caps.
- Preparation always remains awaiting_budget; no paid operation is started.
- Server authorization, idempotent preparation/enqueue, optimistic control versions.
- PostgreSQL tenant advisory lock + row lock reserve one fragment and its budget atomically.
- Pause/Resume/Stop preserve records; Resume cannot approve an unapproved campaign.
- Expired in-flight attempts become unknown, retain their reservation and block automatic replay.
- Completion is lease-fenced. Missing/nonprocessed media marks a fragment partial.
- Successful fragment continuations reuse the existing authorized extraction route and cursor.
- Unknown media/message/currency metrics remain null, never presented as observed zero.

## Integration boundary / release blockers

This is a preparatory commit, not the requested finished automatic process.
The schema is intentionally NOT wired into production startup, and no new public
API, background runner or approval endpoint is registered. No production change.

Sol is currently implementing codex/history-media on the same base. There is no
finished commit to integrate yet. His media_results contract needs final tests.
A fragment with new audio transcription can consume more than the one model call
reserved by this foundation. Before enabling execution, reserve the STRICT upper
bound of all model/ASR calls and token/media budgets, including unknown results.
Never pass allow_paid_asr through this one-call reservation.

Remaining work:
1. Integrate Sol's completed commit and verify media provenance/completeness and
   all paid-call/token accounting. Text completion must not hide pending media.
2. Add bounded, durable all-history discovery with authoritative source checkpoint,
   month/window splitting within existing <=31-day limits, and subsequent ingestion.
   Deal with late-arriving messages, unified-group duplicate routes and empty pages.
3. Add receipt reconciliation after restart (reuse completed receipts; never retry
   uncertain paid calls). Retry only failed/incomplete evidence with explicit caps.
4. Wire existing worker, explicit budget/source confirmation and authorized status/
   control API; build progress panel inside existing Support Cases, RU/AZ/EN.
5. Aggregate unique evidence/message/media counts, actual usage and unknown costs.
6. Validate contradiction handling and cross-fragment candidate deduplication.
7. Real multi-process PostgreSQL concurrency tests plus integrated browser checks.

Tests: 9 new disposable PostgreSQL/PGlite tests passed. Existing knowledge batch
regressions: 17 passed. Providers are mocks; no real AI calls or customer sends.
No runtime full-history scan, production SQL, migration, merge or deployment.
No agent settings, sessions, Connect or /webapp modifications.
