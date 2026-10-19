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

## Group identity contract

Connect must preserve the Evolution `remoteJid` as `conversationId`. A JID
ending in `@g.us` is a WhatsApp group; a JID ending in `@s.whatsapp.net` is a
direct conversation. For installations where a legacy adapter cannot preserve
the suffix, signed webhook data must include `isGroup: true` or
`conversationType: "group"`; a numeric identifier alone is deliberately not
treated as either a group title or a phone number.

For every `message.inbound.received` / `message.outbound.observed` webhook,
Connect should additionally send these signed fields when Evolution has them:

- `groupName` (or `conversationName`) — current WhatsApp group subject;
- `participantJid` and `participantName` — author of an inbound group message;
- `fromMe` — the Evolution message-direction fact.

Outbound events should also include `source` when the adapter can establish it:
`linked_phone` for a message sent from the linked WhatsApp client, `ai_agent`
for a CRM-originated AI delivery, and `crm_operator` for a human CRM send.
`fromMe` alone establishes direction, not authorship. Beintaskbot uses known
provenance to distinguish AI echoes from staff takeover; an unclassified
outbound event is conservatively treated as a staff reply for AI stop rules.

Beintaskbot keeps the original signed webhook data for diagnostics, stores the
group title separately from the conversation JID, and exposes the participant
per message. If title or participant metadata are absent, the UI uses an
explicit generic label and keeps the technical identifier only in diagnostics;
it never invents a group or member name. Historic conversations acquire a
title only after a new metadata-bearing event or an explicit provider resync.

AI replies to groups are denied by default. A tenant AI Agent administrator
must explicitly enable **Allow AI replies in WhatsApp groups** for that
agent's Connect scope before a group event is admitted to the existing queue.
