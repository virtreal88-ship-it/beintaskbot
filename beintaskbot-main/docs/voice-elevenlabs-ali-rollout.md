# ALi RADIO owner-only voice rollout

The optional output path keeps OpenAI Realtime for microphone understanding and
text responses, and streams those responses through the existing ElevenLabs
ALi RADIO voice. It does not create a hosted agent or change text Chat AI.
The browser continues receiving bounded mono PCM16 at 24 kHz. No provider
credential is sent to the browser. Session instructions select Russian or
Azerbaijani per utterance; pronunciation still requires a real listening check.

## Configuration and order

1. Run the voice lifecycle CI, real isolated PostgreSQL tests, and independent
   review on the exact candidate SHA.
2. Read the production lease through the official Railway SQL UI. Do not reset
   a live or uncertain lease. Apply `migrations/voice_lease_provider_kind.sql`
   after `voice_lease_provider_open.sql`, before deploying the new worker.
   This additive migration marks existing provenance unknown; it does not
   manufacture an opening timestamp or close a lease.
3. Deploy with `COPILOT_VOICE_TTS_PROVIDER` absent or `openai` first. The new
   schema protects overlap with old workers. Verify the SHA and healthy worker.
4. Securely provision `ELEVENLABS_API_KEY` on the Railway **worker**, with access
   to Text to Speech and the existing saved ALi RADIO voice. Do not paste it
   into chat, commit it, print it, copy connector credentials, or change the
   working company OpenAI key. The ElevenLabs connector is not a runtime key.
5. Set `COPILOT_VOICE_TTS_PROVIDER=elevenlabs_ali` only after the above checks.
   Invalid selection or a missing key fails admission before a paid call.
6. Re-read the lease and perform one short owner-only conversation in `/app`:
   Russian question, Azerbaijani question, audible replies, voice interruption,
   explicit end, closed lease and subsequent admission. Record actual provider
   usage and observed latency; do not equate mocked PCM with real pronunciation.

## Failure and rollback

Hybrid/unknown leases have no automatic age recovery: OpenAI's documented
maximum duration is not evidence that ElevenLabs is finished. Only confirmed
provider closure and drained operations allow the exact fenced owner to release
the row. Uncertain cleanup remains fail-closed. Never change a provider kind or
timestamp to recover an old row.

Rollback the feature selection to `openai` for **new** sessions after normal
cleanup. Leave the additive migration in place; older workers must remain
unable to take over an uncertain hybrid lease. Interrupted text responses are
excluded in full because an ElevenLabs playback offset cannot safely truncate
the upstream text item. Audio, response text and secrets must never enter logs.

Official protocol references:
- https://elevenlabs.io/docs/api-reference/text-to-dialogue/ttd-websocket
- https://elevenlabs.io/docs/eleven-api/guides/how-to/websockets/realtime-tdd
- https://elevenlabs.io/docs/overview/models
