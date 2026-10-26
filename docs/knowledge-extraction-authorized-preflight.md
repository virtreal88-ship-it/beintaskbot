# Owner media-extraction preflight

`administrator_knowledge_extraction_preflight` is read-only.  It accepts an
existing CRM message UUID, Connect channel UUID and lead ID, verifies all three
against the MCP tenant/channel scope on the server, and never returns the
conversation ID, message body, media URL, JID, credential or tariff values.

For a video it reports an admitted model ID, static provider domain and boolean
tariff readiness for text, vision and ASR.  It does not call a provider, reserve
budget, create a campaign, or start an extraction.

## Existing safe execution path

The only supported execution remains the existing approved Knowledge Campaign
fragment flow.  A company owner must use the standard campaign UI to select the
already authorised channel/conversation and approve the bounded campaign.  The
worker then claims a live fragment and calls the existing
`tenant_knowledge_candidates_service.analyze` path.  That service rechecks the
fragment lease, source channel, message version, consent, reservations and
idempotency before provider dispatch.

The preflight intentionally returns `approved_campaign_fragment_required` when
no matching live fragment exists.  It does not create, approve, resume or
broaden a fragment, and it cannot turn a case `403` into source access.
