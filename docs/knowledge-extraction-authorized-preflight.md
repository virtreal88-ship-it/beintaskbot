# Owner media-extraction preflight

`administrator_knowledge_extraction_preflight` is read-only.  It accepts an
existing CRM message UUID, Connect channel UUID and lead ID, verifies all three
against the MCP tenant/channel scope on the server, and never returns the
conversation ID, message body, media URL, JID, credential or tariff values.

For a video it reports an admitted model ID, static provider domain and boolean
tariff readiness for text, vision and ASR.  It does not call a provider, reserve
budget, create a campaign, or start an extraction.  A `ready` result additionally
requires the exact owner consent, unchanged safe media-version fence, one-message
route, matching agent and media policy, the one approved source channel, and the
worker's live fragment lease.  Tariff presence alone is not readiness: the
combined text/ASR/vision upper bound must still fit the tenant cap.

## Existing safe execution path

The model/app-visible `administrator_single_message_extraction_review` launcher
creates only a short-lived pending owner consent for exactly one permitted attachment.
Its TEST-ONLY app-only completion revalidates the owner OAuth connection, tenant, channel,
lead, media-version fence and policy, then creates one existing Campaign with
one pending fragment.  It never makes a provider call directly.  A rejected,
expired, stale, foreign or replayed capability cannot activate the fragment.

The only supported execution remains the existing approved Knowledge Campaign
fragment flow.  The worker then claims that live fragment and calls the existing
`tenant_knowledge_candidates_service.analyze` path.  That service rechecks the
fragment lease, source channel, message version, consent, reservations and
idempotency before provider dispatch.

The preflight returns `owner_consent_required` before this exact consent exists,
and `lease_or_exact_fragment_required` before the worker has claimed its exact
fragment.  It cannot turn a case `403` into source access or authorize history
outside the selected message.
