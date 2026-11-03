# Byte-bound session sender — synthetic HOLD

A proof-of-mechanism, not production code. `SYNTHETIC_ENABLED` is false, the only socket is an in-memory fake, and no connector, credentials, gateway, real provider or live migration is present. The session-budget, ASR and provider-admission gates remain closed. No paid calls are authorized by this experiment.

## Mechanism

The synthetic sender copies the bound scope, accepts immutable PCM `bytes` only, builds the exact compact ASCII JSON frame internally and validates the same PCM/frame relationship again in the transaction worker. In one PostgreSQL transaction it consumes the session's one-use audio receipt or response dispatch, updates the existing session allowance, and stores the exact wire frame's SHA-256, byte length, full immutable scope fingerprint and operation UUID. Only after that transaction returns successfully does the same sender pass that unchanged immutable frame to its privately owned synthetic sink. Responses permit only the literal `{\"type\":\"response.create\"}` and consume the already-claimed response under its bound session scope. A duplicate ID cannot send twice, including after process death. A write timeout/partial error poisons that sender, attempts to mark the whole session uncertain, preserves the full hold and never retries. Unknown commit likewise sends nothing and attempts uncertainty reconciliation; a database outage can prevent that reconciliation, so durable crash recovery/reaping remains required.

No raw-send method or socket is part of the public test API. **This is not a security boundary:** Python reflection, untrusted code in the same interpreter, direct DB writes, an alternative service or a retained connector reference can bypass private attributes. The sample `_SyntheticSocket` can never contact a real provider, and receipt hashes alone do not constrain the real socket. Exact-once provider delivery and remote close after timeout are not claimed. A cancellation or process death after DB commit also leaves the session potentially `ready`; recovery, in-flight work from other executors, and wall-clock revocation have not been fenced. A non-returning socket that ignores cancellation may continue an in-flight send. Existing source/lease authorization and revocation are not integrated, and their global lock order remains unproved.

## Integration assessment

At the audited `/workspace/voice-ai-integration/beintaskbot-main/tenant_operator_realtime_wire.py`, `_RealtimeWire.send` directly calls `self.socket.send_json(event)` for both PCM and response, while `open` directly sends configuration and context. The socket is publicly reachable as `self.socket`; the current claim guard covers the response branch only. The session uses `_send_provider` for generated events. No safe drop-in adapter can prohibit the existing raw path or bind all prior context/configuration sends merely by wrapping that guard. Before live adoption, refactor the server-owned factory and session together: issue the session-wide hold **before connection**, give the network-capable writer exclusive ownership of its credential and socket, route **all** billable frames through a bounded reviewed command API, remove the old raw send and alternate connection paths, bind each response and the exact bytes at the last write, and prove restart, cancellation, deadline, source/lease fencing, permission revocation and provider cleanup. OS/process identity, outbound network/secret access and DB privileges must prevent bypass; language-level encapsulation is insufficient. This requires coordinated changes beyond this isolated experiment and must remain feature-disabled until independently reviewed. No generic AI path was changed; existing capped tenant AI can consume or exhaust its established cap when a legitimate voice hold is present.

The tested pre-open $4.224 reservation for two responses is a conditional arithmetic estimate, not a provable provider-side cost cap. Billing of all billable input, output, configuration, rounding, future provider changes and remotely enforceable cutoffs still needs verified bounds. ASR is explicitly off and any mode requesting it is rejected by the underlying budget candidate. Quote expires 2026-11-04 00:00 UTC. Existing conflicting tenant holds can cause unrelated tenant cost to be undercounted; no broad tenant freeze is introduced. These independently block paid use even if the synthetic sender tests pass.

`sender_schema.sql` is an intentionally simple local-only test fixture: it is **not versioned/idempotent**, lacks an upgrade/rollback policy and requires role/ACL, trigger cost, mixed-worker and migration review before any production consideration. It stores hashes and sizes, not raw audio. Its FK prevents normal deletion, but DB owners and privileged writers can bypass or forge rows. No migration has been run outside disposable synthetic PostgreSQL.

## Tests

Use a disposable localhost PostgreSQL and set a synthetic `VOICE_BUDGET_POC_ADMIN_URL` pointing at its `/postgres` database. The test helper rejects nonlocal hosts/inherited libpq overrides and creates/removes only a randomized `test_voice_budget_*` database.

```sh
env -i PATH="$PATH" VOICE_BUDGET_POC_ADMIN_URL="$VOICE_BUDGET_POC_ADMIN_URL" \
  PYTHONPATH="$PWD/beintaskbot-main:$PWD/experiments/voice_budget_poc:$PWD/experiments/voice_session_budget_poc:$PWD/experiments/voice_bound_sender_poc" \
  python -m unittest discover -s experiments/voice_bound_sender_poc -p 'test_sender.py' -v
```

Tests deliberately turn on otherwise-closed flags and use test DB owner privileges; they establish neither provider acceptance nor production security.
