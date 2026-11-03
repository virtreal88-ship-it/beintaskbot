# Offline Copilot voice protocol experiment

This is a **mock-only experiment**, not a working voice assistant, application
endpoint, deployment, provider adapter, security approval, or proof of delivery.
It has no network calls and accepts only injected synthetic callbacks. No API
keys, browser tokens, audio files, real customers or paid calls are involved.

With the project's Python dependencies installed, run
`python -m unittest discover -s experiments/copilot_voice_poc -v` from the
repository root. The unit tests exercise a scoped server-owned event boundary,
client-event allowlist, PCM framing/size limits, revocation, duration/idle checks,
and cancellation of unplayed assistant audio. Time checks require a transport
that calls `tick` even when no events arrive. Client playback reports are
untrusted estimates, not proof of what was heard.

The separate [context and budget investigation](CONTEXT_AND_BUDGET_GAPS.md)
includes tests which deliberately reproduce current defects. A passing
reproduction is evidence that those defects exist, not a release approval.

## Existing assistant and mandatory reuse

The intended UI is the private per-dialog Copilot in
`beintaskbot-main/docs/tenant-operator-dialogs.js`; it is not the separate AI
Administrator. `tenant_operator_api.py` and `tenant_operator_access.py` enforce
session identity, origin, membership and exact dialog scope. The assistant's
`tenant_operator_copilot.py` has explicit system rules forbidding tools, sending
customer messages and external actions. No voice adapter may relax these rules.

The real text path resolves the currently assigned agent and authorized
published knowledge, bounded customer history and saved evidence; its private
employee/Copilot turns live in `saas_operator_copilot_threads`, keyed by tenant,
employee, channel and conversation, with an agent-binding guard. It reserves
request counts in `saas_chat_ai_runs` and checks consent/config/access. This is
not a duration or cost cap. Voice must extract and reuse the same authorized
context builder and common thread, preserve its assignment and source checks,
and serialize text and voice updates without overwriting concurrently added
turns. Do not create an independent assistant or grant it existing CRM tools.

## Required work before any rollout

* Implement an authenticated, same-origin server-owned streaming transport,
  bounded queues and heartbeats, one durable session per authorized scope, and
  repeated access/config/agent-binding checks. Filter browser events; never
  allow the browser to override session instructions, model, tools or limits.
  A direct WebRTC client is not by itself a server enforcement boundary.
* Bind provider setup to current company consent, the existing authorized
  credential path and approved model, without exposing the permanent key or
  changing the agent's normal text model. No provider call is currently wired.
* Reserve and enforce an explicitly approved voice budget, duration, inactivity
  and concurrency in durable storage. `response.done` usage can be delayed or
  missing; token reports cannot alone guarantee a strict bill ceiling. Close
  the actual upstream session on every limit, logout, lost access, disconnect,
  crash recovery and normal end. This experiment's `stop` only notifies a test
  callback and must not be used as evidence of upstream resource cleanup.
* Use real provider VAD and process interruption according to the selected
  transport. The WebSocket client manages playback and must stop locally and
  send `conversation.item.truncate` with the actual played duration; do not
  persist unheard output as a complete statement. The mock uses an estimate.
* Safely commit ordered voice transcripts to the same scoped private thread,
  with session/turn IDs, binding, explicit completeness and ASR quality. A
  transcript for an interrupted response may not align with heard audio;
  omit or mark incomplete rather than fabricate an exact prefix. Avoid raw
  audio storage unless a separate approved retention policy requires it.
* Check RU/AZ, switching, microphone exclusivity, mobile lifecycle, retry and
  failure behavior with browsers and synthetic providers; independently review
  and verify live behavior only after a budget and safe test scope are approved.

Official documentation: [Realtime conversations and interruption](https://developers.openai.com/api/docs/guides/realtime-conversations),
[server-side controls](https://developers.openai.com/api/docs/guides/voice-server-controls),
and [voice cost optimization](https://developers.openai.com/api/docs/guides/voice-latency-cost).
