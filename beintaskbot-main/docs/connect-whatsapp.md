# Beintaskbot Connect WhatsApp transport

Connect is an alternative transport to Kommo.  A company administrator opens
`/app?view=settings`, creates a Connect API key with `channels:read`,
`messages:write` and `webhooks:manage`, pastes it in **Connect WhatsApp**,
loads the available channels and selects the lines used by that company.

The key and webhook secret are encrypted with `TENANT_ENCRYPTION_KEY`; neither
is returned to the browser after the setup request.  The CRM saves only the
channels belonging to that key.  Connect signs incoming events with HMAC-SHA256
and the CRM rejects bad signatures and duplicate event IDs before writing a
tenant-scoped conversation, message and CRM cache row.

Replies from the existing chat UI use the saved conversation/channel mapping
and Connect's idempotency key.  They do not call Kommo.  Existing Kommo chats
retain their current transport; a conversation mapped to Connect cannot fall
back to Kommo automatically.

For the first release, Connect's direct message path is text-first.  Existing
Kommo attachments and histories remain unchanged.  Rich-media forwarding is
kept behind Connect's provider adapter and must be enabled only when the
Evolution storage URL contract is configured for the deployment.
