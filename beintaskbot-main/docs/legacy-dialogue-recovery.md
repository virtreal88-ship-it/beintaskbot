# Administrative legacy recovery

Base: production ff976323. No migration, production writes or deployment.

The existing dialogue diagnostic modal offers a lazy check to administrators.
GET/POST `/api/platform/ai/dialogs/legacy-recovery` uses the existing session,
expected tenant/user identity and POST origin checks. Server authorization requires
owner/admin plus dialogs and ai_agents_manage. Full tenant/channel/conversation/lead
scope is required; groups stay blocked for separate coordination review.

Recovery requires exactly one persisted route, only the legacy state, an existing
primary agent with matching channel scope, consistent job/event/archive evidence,
and no queued/processing/unknown jobs or pending/sending/unknown drafts. A preview
hash and explicit confirmation are mandatory. Evidence is rechecked transactionally.
The short recovery transaction locks evidence tables against concurrent writes
(1.5 second lock timeout; 5 second statement timeout); contention fails closed.
No automatic retry with new evidence and no external network calls occur inside it.

The original state is retained in the existing legacy archive. The state row is
re-keyed to its verified channel with revision increment; Human/Pause and message
markers remain. Active legacy states become paused. No jobs, drafts, settings,
messages, group revisions or other-channel states are changed. Resume is separate
and uses the existing new-inbound-only boundary. Repeated recovery fails harmlessly
once the legacy row is gone; it cannot resume or enqueue anything.

The diagnosed production contact has two persisted routes, one unscoped Human
state linked to Nizam, and two newer channel-local Human states without an agent.
A historical completed job points at the primary channel, but it does not resolve
the conflicting states. It is intentionally ineligible. No production data changed.
This release does not implement manual merging or speculative reassignment.
