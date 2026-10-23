# Simulation pricing correction, 2026-10-23

Read-only inspection of run `21193878-f18e-4cfa-93f3-49f97a63290b`
confirmed `ai_spend_price_unknown`, zero tokens and a released primary token
reservation. No provider reply or Expert result was recorded.

The tenant Primary and its allowed Expert both have an empty model setting.
The worker's `AI_AGENTS_PLATFORM_DEFAULT_MODEL` and admitted model list select
`gpt-4.1-2025-04-14`. The price catalog contained only `gpt-4o-mini`.
There is no automatic provider fallback in the Simulation call path. The empty
agent model is resolved before monetary reservation, which checks exact IDs.
The old run did not persist its resolved model; its historical model is therefore
inferred from the configuration/call path, not an immutable per-call model receipt.

Official standard API tariff, verified on the linked model page:
https://developers.openai.com/api/docs/models/gpt-4.1

- Input: $2 per million tokens = 2,000,000 microUSD per million tokens.
- Output: $8 per million tokens = 8,000,000 microUSD per million tokens.
- Cached input: $0.50 per million. Existing accounting conservatively charges
  all input at the full input tariff rather than relying on the cache discount.

Existing settlement also takes the greater of input/output pricing and the
total-token upper price. For 100 input + 20 output tokens the standard tariff is
360 microUSD, while the existing ledger settles 960 microUSD. This conservative
over-accounting is preserved here; ledger `actual_microusd` is not an exact bill.

Corrected server catalog (retain the existing entry):

```json
{"gpt-4o-mini":{"input":150000,"output":600000},"gpt-4.1-2025-04-14":{"input":2000000,"output":8000000}}
```

The pilot's `AI_TENANT_SPEND_LIMITS_MICROUSD` remains 5,000,000 microUSD.
No wildcard/model alias/default price is added. Unknown models remain blocked.

Simulation Primary and Expert use direct Responses text calls without hosted
tools, search, image generation, transcription or a model fallback. Token output
is bounded by the request; aggregate reservations use the higher input/output
tariff. Attachment transcription is not silently initiated by Simulation.
An audio attachment with no text context remains explicitly unsupported.

These prices cover standard OpenAI text token billing. They do not establish
prices for another service tier, regional surcharge, custom contract, transcription
or hosted tools. Any future such component needs its own verified upper bound.

The Railway catalog correction is saved with skipDeploys=true so the owner can
activate it with the controlled worker deployment; no paid recheck is run here.
No runtime monetary or provider code needs changing. The regression tests
exercise the actual default-selection path, old-catalog fail-closed behavior,
corrected mock reply, idempotent reservation, settlement, unknown holds and cap.
