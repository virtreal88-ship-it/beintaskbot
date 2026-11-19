# Native WhatsApp operations

## Health notifications

The existing owner Telegram notification queue also samples native source status.
Existing rollout flags and tenant allowlists still apply. Sampling is read-only,
at most eight sources per sweep, four concurrent requests, at least 45 seconds
between observations. Reservations expire after two minutes and have CAS tokens.
The existing 90-second outage debounce applies; short restarts do not notify.
An unreachable control service is unknown, not proof that WhatsApp logged out.
Only a confirmed logged-out state adds a QR relinking instruction.

Notices are checked against the current source, generation, owner and fresh
observation before delivery. Old Connect events and queued notices cannot report
health for a native-owned source. Recovery only follows a corresponding outage
attempt; uncertain Telegram delivery is not automatically retried.

## Stop and resume

The owner can stop one number or all currently listed native numbers from the
WhatsApp card. The confirmation preserves source IDs, conversations, assignments,
recordings and encrypted device state. This stops the Beintaskbot process, not
all linked devices on the phone. Explicit resume reuses the same source/device;
an expired WhatsApp authorization can still require a new QR.

Stop refuses active calls, unfinished commands, unknown sends or pending events.
All requested admissions are fenced before stopping processes. The supervisor
persists `disabled` before terminating and confirms process exit. A lost response
leaves the route blocked, with a retry button; it never reports an unknown stop
as success. Deployment cannot restart a persisted disabled source. Deliberate
stops do not emit outage alerts. The reserve Connect integration is not activated.

## Avatars

Native avatars use the same tenant/source/generation binding and dialog permissions
as the conversation. Membership and binding are rechecked after fetching. Images
are obtained through the existing private gateway and validated bounded CDN
download; no remote image URL or credential is sent to the browser. Caches are
bounded per source (32 entries/4 MiB); transient failures are not cached as missing
for seven days. Avatar concurrency is separate from outgoing message concurrency.

## Release sequence

1. Add nullable-free `paused BOOLEAN NOT NULL DEFAULT FALSE` to native routes using
   the same idempotent DDL in `tenant_whatsapp_native_transport.migrate` before the
   new Worker reads inventory. Old code ignores the added column.
2. Confirm no active calls, release gateway and Worker, wait for both deployments.
3. Clear only derived unavailable/loading avatar cache flags for enabled native
   sources affected by the previous legacy-fetch path. Do not touch messages.
4. Verify real inventory/status sampling and protected avatar responses; then stop
   only the explicitly requested source and compare history/grant fingerprints.

Rollback after a stop must preserve the gateway's durable disabled marker and
Worker's disabled route. Do not roll back to a supervisor that ignores disabled
sources or enable reserve sends automatically.

Tests cover signed legacy suppression, native outage/recovery dedupe, stale and
late observations, concurrent reservations on disposable PostgreSQL, tenant/access
revocation, bounded image handling, process-exit confirmation, durable stop/resume,
and the shipped desktop/mobile UI. Live provider logout/recovery is not simulated
on working numbers merely to produce a Telegram test message.
