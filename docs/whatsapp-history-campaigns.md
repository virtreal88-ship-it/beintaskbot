# Continuous WhatsApp knowledge accumulation — release candidate

Base: production main 3debb64e. Branch: codex/whatsapp-knowledge-backfill.

## Implemented
- Integrates Sol Beintaskbot 89e296b1 as 55ff595b; protected video/document support requires Connect c2dd3f6 first.
- Opt-in campaigns reuse existing extraction, Support Cases, Knowledge Sources and private virtual AI tests.
- Selected channels and call/token caps require separate versioned owner/admin consent. Deployment never starts a campaign.
- Keyset source inventory, durable source checkpoints, <=30-day windows and existing <=80-message pages; no age cutoff.
- Verified group identity only, all participating channels must be explicitly approved; original media source routes preserved.
- New ingestion requires an explicitly resolved support ticket stable for 30 minutes. Inactivity does not resolve a ticket. New messages after resolution require another explicit resolution.
- Atomic tenant reservations, one fragment at a time, lease fencing, durable completed-receipt recovery. Unknown paid attempts retain reservations and are never automatically replayed.
- Pause/Resume/Stop and emergency-stop checks. Resume may retry bounded failed/incomplete fragments with known receipts; it never retries unknown calls.
- Tenant-scoped ASR receipts prevent repeat transcription, including concurrent processes. Existing group read deduplication remains unchanged.
- Photo/audio/video/PDF/TXT/DOCX use bounded existing media handling. Missing media stays partial. Sampled video is explicitly partial, never full analysis.
- Exact evidence/card duplicate suppression and deterministic possible-conflict hints. No automatic approval or publication.
- Published candidate sources can trigger a budgeted private virtual check; retrieval failure and answer-review status are separate. This never invokes customer transports.
- Support Cases UI includes explicit source/budget confirmation, progress, controls and quality results in RU/AZ/EN.

## Exact additive migration
Five new tables: saas_knowledge_campaigns, saas_knowledge_campaign_fragments,
saas_knowledge_campaign_sources, saas_knowledge_media_cache, saas_knowledge_quality_checks.
Two campaign indexes (one active campaign per tenant; pending-fragment index),
plus primary/unique indexes and foreign keys on the new tables.
Existing extraction: media_results JSONB NOT NULL DEFAULT '[]'.
Existing candidate: quality_flags JSONB NOT NULL DEFAULT '[]'.
No message/history deletion, no agent-setting updates or state transfer.
Existing candidate schema code retains its pre-existing status constraint refresh.
Local disposable PostgreSQL compatibility and preservation tests pass; production
schema/inventory checks still require working authenticated Console access.

## Deployment / rollback
Connect c2dd3f6 protected historical media first, then Beintaskbot/worker.
Verify ffmpeg and optional pdftoppm; missing tooling must produce explicit unsupported/partial media.
Do not create services or campaigns as part of deployment.
Before application rollback pause campaigns and drain/record in-flight receipts;
retain all additive tables/columns and reservations. Never replay unknown receipts.
Do not remove schema to roll back the application.

## Validation / remaining release gate
153 combined targeted Python tests passed before final bounded-discovery/recovery tightening.
Real PostgreSQL two-process claim and ASR-cache races passed.
Four JS suites passed (campaign controls, existing cases, batch UI, card refinement).
Chrome used real local assets and API with disposable PostgreSQL and mock AI:
prepare, explicit consent, background candidate creation, pause and retained state.
This is not a production or real-provider test.
Railway production remains 3debb64e / c7ee7807-9783-4198-bf15-776227ecd0b1.
Authenticated Console renders Connected but browser input fails with CDP timeouts;
no production SELECT was confirmed and no deployment was attempted.

## Honest limitations
- Currency cost is unknown, not zero: call/token reservations are not a monetary invoice. A model/source/media inventory and proposed budget must be approved before real processing.
- ASR max60s, visuals detail low, bounded video frames/PDF pages; no claim of full long-video/document coverage.
- Conflict detection is deterministic, not semantic proof. Virtual answer checking only accepts exact supported steps automatically; paraphrases/additional claims require employee review.
- Initial metadata inventory is paginated but committed atomically; a crash during inventory repeats reads, not AI calls. Source/message processing progress is durable.
- Continuous mode depends on explicit resolved support tickets. Ordinary unfinished chats remain unprocessed as completed solutions.
- No real messages/media were sent to AI or customers. No production source counts or price estimate are asserted without evidence.
