# Unified WhatsApp integration card

The owner opens **Ватсапп**, sees native sources and their live message/call
readiness, and opens a form before explicitly generating a QR. Enter the exact
number including country code. The gateway checks the scanned account against
that number; a mismatch cannot activate CRM delivery or sends.

This reuses managed call sources, native capture generations/routes, existing
Connect-shaped source IDs and dialog permissions. It does not copy WhatsApp
session keys between engines. No additional service or volume is provisioned.

## Existing source transfer

1. Disconnect the source in the reserve Connect integration.
2. In Ватсапп enter the same number and generate a QR.
3. Worker verifies the phone against Connect, preserves the original source ID,
   and invokes Connect's `native-transfer` operation. Connect requires a closed
   provider and takes its existing dispatch fence. Pending/unknown sends block
   transfer. The old sender is disabled without deleting history or media.
4. Scan the new QR with that number. Activation requires exact account identity
   and a confirmed connection, under the existing source mode lock.

Only sources without an earlier call-only binding use this self-service flow.
A call-only source requires the existing controlled maintenance transition;
the UI reports this instead of restarting an active call engine.

…101 and …103 were already native. …102 remains on Connect in this release;
no migration endpoint is invoked for it during release verification.

## Retry and rollback

Onboarding has one durable row per company/number. Retries reuse its source,
generation, gateway identity and token. The form displays unfinished attempts.
A response lost after provisioning does not create a second session.

Connect remains installed as reserve, including retained media references.
It is not automatic failover: never re-enable an old sender while the native
sender is enabled. An operator rollback must fence native dispatch, resolve
unknown sends, drain capture, then use the existing verified transition. This
change does not add a one-click rollback or delete either engine's data.

## Validation

Synthetic PostgreSQL covers exact account activation, duplicate requests,
transfer refusal, unchanged historical IDs, cross-company/revoked access and
pending-attempt bounds. HTTP tests cover origin/context and strict payloads.
Chromium loads the built bundle at desktop/mobile widths and covers explicit
QR generation, activation, and clearing QR/polling after lost access.
No live message or call is required merely to display the integration card.
