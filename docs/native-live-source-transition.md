# Existing call device to unified WhatsApp

Operator maintenance only. No public endpoint and no automatic startup migration
without the exact one-source manifest. Keep Connect and Evolution sessions and
history until all sources have been accepted and deletion is separately approved.

## Before switching

- Check current deployed Worker, calls and Connect revisions and all replicas.
- Finish real acceptance on the test source: both directions, attachments,
  quoted reply, receipt, group title/participants and calls using the same device.
- Confirm the target account, source ID, tenant and current owner profile.
- Save read-only counts/identities for source history and source grants.
- Apply additive native schema migration before deploying dependent Worker code.
- Install Connect's source dispatch fence and explicit audited rollback method.
- Never import an Evolution device database into whatsmeow.

## Transition

1. Call `tenant_whatsapp_native_transition.prepare(profile, source, account,
   generation)`. It holds the existing tenant maintenance lock, exclusive
   source dispatch lock and browser gateway lock. It checks exact connected
   account, no live/unknown calls, no pending call commands and empty runtime
   call registries. It inserts a disabled route and capture generation, keeping
   source/history/grants active. This route fences new Worker sends and a late
   browser accept/start. End/reject remain allowed. The return is a public
   identity manifest, not credentials.
2. With all call engines quiescent, deploy calls with
   `CALLS_NATIVE_MESSAGING_TRANSITIONS` containing a one-item JSON array of that
   exact manifest. Startup holds the existing OS-exclusive volume lock before
   any engine launch. It validates account/company/source/public key and saves
   private metadata backup before atomically adding a new encrypted journal key.
   Device DB, provider session, signing keys and recording keys stay in place.
   Nonempty legacy plaintext retry/decryption buffers block transition; never
   delete those buffers to pass. Resolve/migrate them separately if encountered.
3. Verify connected exact account and generation, then call `enable_capture`.
   Capture stores encrypted events, with CRM projection and sends still fenced.
   Verify a real authenticated event reaches the new generation. Read-only
   group metadata can supply this evidence; a fabricated SQL cursor cannot.
4. Call Connect `freezeSourceForNative` with exact company/source/account and
   the same generation. Its exclusive lock drains admitted provider operations
   and rejects unknown/pending sends. It disables only old outbound admission;
   it does not remove the linked device or historical media access.
5. Call transition `activate`. It rechecks runtime account/generation, closure,
   stored event and actual disabled Connect endpoint under the same source
   lock before enabling native projection and sends. Retained events replay
   through existing exact provider-ID deduplication, never by name or phone.
6. Verify UI with agreed test counterpart only, then compare history/grants and
   source IDs with baseline. Record actual deployments and observations.

## Rollback

Never retry an uncertain operation through the other engine. First call
`fence_for_rollback`; this commits native admission off, retains the route,
and allows existing captured events to drain through normal scoped ingestion.
Then `rollback_readiness` requires exact quiescent runtime, no unknown native
sends and no unacknowledged retained events. Any failure leaves the fence.

Only after that proof call Connect `restoreSourceAfterNative` for the same
freeze marker. It checks the prior source/provider identity under its own
exclusive lock. Finally Worker `restore_legacy` rechecks readiness and actual
legacy connected account, explicitly enabling legacy admission. Retain native
inbox, journals, device and dedup marker; messages received after cutover remain
in the same source/history. There is no timed fallback and no route deletion.

If transition failed before Connect was frozen, skip the Connect thaw and use
the same Worker proof and explicit restore. If new capture has not started,
the old runtime generation must be absent and the stored cursor must be zero.

The manifest may be removed after successful transition; its repeat is
idempotent, but another generation is rejected. It must never be used to rotate
a live journal key. A later source transition uses a new reviewed manifest.
