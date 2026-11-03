# Realtime session-wide budget: source review, not an approval to call

Reviewed **3 November 2026** against the official OpenAI documentation linked
below. No provider request, credential access or production operation was
performed. This is a design analysis, not a provider contractual quote or proof
that the existing implementation enforces a strict $5 ceiling.

## Conclusion

The current candidate's reservation immediately before `response.create` is
**not an admission boundary for the entire session**. Its server currently
opens the provider connection and sends initial context before that reservation;
it accepts audio frames before the per-response claim. Even if a specific
configuration happens not to incur a cost before a Response, the provider
configuration, early failures and additional billed services are not protected
by the per-response ledger. Reserve the entire worst-case session exposure in
the one shared, durable authority **before any provider connection attempt or
other potentially paid context/media operation**. A per-response one-use claim
must consume a portion of that existing session reservation, rather than create
an independent hold that double-counts the same exposure.

It is possible to write a conditional arithmetic bound for a tightly restricted
configuration. The public documentation plus this offline code review do not
establish all the conditions required to say that bound is a hard upper limit
for the present implementation or account. Therefore no live test is authorized
by this analysis, even though the owner approved up to $5 across all executors.

## What the official documentation says

* For conversational Realtime, model charges accrue when a Response is created;
  connection and network bandwidth currently have no separate charge. Each
  Response includes the conversation context, so later turns can repeatedly
  incur input-token charges. Caching is best effort and cannot be assumed.
  [Realtime cost optimization](https://developers.openai.com/api/docs/guides/voice-latency-cost).
* Input transcription, if enabled, is billed **separately**. Committing audio
  can start transcription without creating a model Response; server VAD may
  commit the buffer automatically. Input transcription defaults off, but
  the session must verify its own effective configuration, not rely on a
  default. [Client events](https://developers.openai.com/api/reference/resources/realtime/client-events/),
  [server events](https://developers.openai.com/api/reference/resources/realtime/server-events/).
* In the documented speech-to-speech session, VAD `create_response` defaults
  to true. A configured idle timeout can also generate a response. Realtime
  session maximum duration is 60 minutes, not the local application's 120-second
  target. A provider session or response must not be assumed to end simply
  because a client timer or process stopped. [Server events](https://developers.openai.com/api/reference/resources/realtime/server-events/),
  [Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations).
* `response.done` contains usage when available and may report completed,
  cancelled, failed or incomplete; a lost or missing event is not evidence
  that no cost occurred. [Server events](https://developers.openai.com/api/reference/resources/realtime/server-events/).
  OpenAI documents that project hard-spend enforcement is not instantaneous,
  so tracked spending can exceed the configured value. [API project limits](https://help.openai.com/en/articles/9186755-managing-projects-in-the-api-platform).
* GPT-Live is a different product and pricing model: voice time is billed by
  duration and backend model/tool work is separately charged. Do not apply
  the conversational Realtime per-Response formula to it. [Cost optimization](https://developers.openai.com/api/docs/guides/voice-latency-cost),
  [pricing](https://developers.openai.com/api/docs/pricing).

## Conditional example, not a hard guarantee

On the review date, the published model page for `gpt-realtime-2.1-mini` lists
a 128,000-token context window, 32,000 maximum output tokens, and per-million
audio input/output rates of $10/$20; text and image rates shown are lower.
Using the full context and full model output for **each** Response, charging
every token at the highest listed audio rate and including a 10% uplift:

```
per Response = (128000 * $10 + 32000 * $20) / 1000000 * 1.10
             = $2.112
two Responses in one session = $4.224
```

This deliberately does not assume prompt caching or credit discounts. It is a
conditional conservative calculation only if the published context/output
limits include every billed input/output token of this exact provider/model
configuration, the effective tariffs and uplift are pinned, at most two
Responses can be created including automatic/retried/partially completed ones,
and **all** other paid services are disabled or separately bounded. The current
public docs do not by themselves prove those conditions for this application's
live provider configuration, session failures, account-specific pricing, or
future mutable model alias. [Model limits and rates](https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini),
[pricing](https://developers.openai.com/api/docs/pricing).

Two separately opened two-Response sessions would need a $8.448 hold under this
example and cannot both fit the owner's total $5 grant. Two one-Response sessions
would conditionally reserve $4.224 total. A reservation of $2.112 per Response
made only after connection is not equivalent to either session-level admission.

The published client-event reference specifies a numerical `max_output_tokens`
between **1 and 4096**, or `"inf"` for the model maximum. The model page's 32k
output maximum does not establish that the integer `32000` is accepted by the
session API. The inspected committed transport snapshot
`fe7bc4ff4bc43adc82756430d713ca38e79c0032` sets `OUTPUT_LIMIT=32000`; a
parallel uncommitted change observed during review sets it to `4096`. That
working-tree change is not a tested release or proof of real provider
compatibility. Verify the final exact SHA, handshake and quote together. Do not
silently assume an override or change the model. [Client event reference](https://developers.openai.com/api/reference/resources/realtime/client-events/).

For illustration only, the pricing page lists `gpt-transcribe` at an estimated
$0.0045 per minute: two 120-second sessions give a raw arithmetic estimate of
$0.018 before any applicable uplift. The reviewed material does not establish
the required maximum billed-duration units, per-commit minimums/rounding,
retries, or incomplete-session accounting. Those must be confirmed and a
separate bounded transcription component reserved before enabling transcription.
Disabling input transcription avoids this particular billed service but leaves
the user's spoken text potentially unavailable; it is not proof that the full
voice-history acceptance criteria are met. [Pricing](https://developers.openai.com/api/docs/pricing),
[Realtime transcription](https://developers.openai.com/api/docs/guides/realtime-transcription).

## Required admission and shutdown boundary

1. In a single authoritative shared database, atomically reserve the full
   session quote across all executors under the owner's $5 total. The quote must
   cover every possible Response up to the enforced session limit, transcription
   if enabled, paid tools, media/context generation, retries/reconnects and any
   other charged component. An unsupported or unbounded component means reject
   before network, not a zero reserve. The existing context loader must be
   proven read-only with respect to billed services or included in that quote.
2. Persist a unique, one-use **session opening claim** before any provider
   connection attempt. After an attempted/uncertain open, retain the full hold
   until trusted reconciliation; a timeout, crash, empty output or missing
   usage does not release it. Do not blindly retry an uncertain open or send.
   Enforce one active session and isolate tenant, employee, dialog and role.
3. Ensure the browser cannot connect directly to the provider, change session
   configuration, create Responses, activate input transcription/tools, inject
   model events or bypass the shared authority. Use only a server-owned
   transport; explicitly request and then verify `session.updated` for the
   intended model, no tools, `transcription=null`, `create_response=false`,
   `interrupt_response` policy, `idle_timeout_ms=null`, and the accepted
   output limit **before** forwarding context or audio. An unexpected event,
   mismatched setting or unknown response must stop further forwarding and keep
   the hold. The default VAD behavior is not the safety policy.
4. Limit and meter the **actual cumulative audio bytes** and allowed format
   before forwarding; a per-frame maximum or wall-clock timer alone does not
   bound how much data can be queued or replayed. Bound context/items, Response
   count, in-flight work and any reconnection; enforce server-side VAD and
   interruption behavior without granting extra automatic Responses. Recheck
   consent, authorization, session lease and source binding at appropriate
   boundaries. A crash must fence older owners and must not issue new work.
5. For each permitted Response, atomically consume its exact one-use subclaim
   from the already-reserved session **before** send, commit it, and refuse
   reuse across processes. A durable `dispatching` row is not by itself a
   network guard: every actual send, including retry code and old workers, must
   be forced through it. There must be no paid ASR/tool side path.
6. Enforce independent monotonic total/idle deadlines, access revocation,
   kill switch, closed browser, network loss and crash cleanup at the server;
   verify provider-specific termination/expiration and record unknown if final
   accounting is not received. Never release a hold just because a local socket
   closed, an output was interrupted, or `response.done` did not arrive. Retain
   and reconcile authenticated usage and each component once; any amount
   above its reserved upper bound freezes further admissions and remains a
   financial incident, not proof the $5 ceiling was respected.

The local candidate currently caps individual PCM frames and has a 120-second
watchdog, but those are not a proven aggregate input or crash-safe billing cap.
The exposed provider/session interfaces and all upstream enforcement must be
reviewed together before enabling any live test. Existing monetary accounting,
unresolved legacy conflicts, ACLs and mixed-worker cutover remain separate
release blockers documented in the adjacent README.
