# Isolated voice-test budget ledger experiment

This directory is **not production code**, does not call OpenAI, does not
authenticate users, and accepts only a dedicated localhost test database. It
cannot establish a project-wide spending ceiling. The user has authorized a
single $5 ceiling across all executors, not a separate ceiling per person,
tenant, process or session. This experiment must not be used for live calls.

## Prototype semantics

One singleton budget row serializes all reservations with `FOR UPDATE`.
Reservation and scope are pinned by an immutable fingerprint and request ID;
the unique tenant/session/response tuple also stops a second request ID for the
same response. There is at most one successful `claim`, committed before any
hypothetical dispatch. Claim is never repeated, including after process death.
A claimed request with no confirmed result retains its full hold. Reserved and
unknown results never expire automatically. A pre-claim cancellation releases
its hold but leaves a tombstone. An observed test cost can be recorded only
after claim, and **does not refund** the unused amount; this is conservative,
and may prevent further tests even after a cheap result. An amount above the
quote freezes future admissions and counts the higher amount, although any
actual overspend may already have happened.

The prototype reserves $2.112 per one hypothetical response and permits no
more than two responses per synthetic session. This is a deliberately
conservative arithmetic example, not approval for a live session:

* Official `gpt-realtime-2.1-mini` model page lists 128,000 context tokens and
  32,000 maximum output tokens. Charge every input/output token as audio (the
  highest published modality rate for the selected model), at $10/$20 per
  million, with a 10% regional uplift: `(128000*10+32000*20)*1.1` microdollars.
* No discount, caching or special credit is assumed. This quote does not cover
  separately billed transcription, external tools, or model calls/reconnects.
  `response_number` is not a mechanism for limiting provider responses.
* Tariffs/model aliases can change. The example is pinned to the published
  2026-11-03 observation and fails closed after 2026-11-04 00:00 UTC. Model
  availability, billing terms and whether provider limits bound every charged
  token need verification for the intended account and endpoint.

Sources: [pricing](https://developers.openai.com/api/docs/pricing),
[model limits](https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini),
[Realtime costs](https://developers.openai.com/api/docs/guides/voice-latency-cost).
Realtime can repeatedly charge accumulated context; input transcription is
separate. [OpenAI project hard limits](https://help.openai.com/en/articles/9186755-managing-projects-in-the-api-platform)
are not instantaneous and recorded spend may exceed the configured limit.

## Remaining work before any paid test

1. Deploy/review **one authoritative** shared ledger and force every executor
   through it. This local test can create multiple isolated databases and hence
   cannot enforce cross-executor accounting in the real environment. Do not
   reuse the separate Customer Memory allowance or adjust any global setting.
2. Implement trusted server admission and auth for exact tenant/actor/session,
   durable claim/dispatch atomicity and replay fencing. The caller can currently
   invent these values. The application must not give clients DB credentials,
   ledger update access, a long-lived provider key, or a direct provider path.
3. Prove every billed operation is admitted through this shared authority and
   is bounded by the chosen quote. Disable auto-generated responses, unbounded
   reconnects, transcription, paid tools, untracked parallel calls and direct
   client sessions. Enforce per-response and per-session limits server-side.
   Implement separate monotonic duration/idle timers, kill switch, revocation,
   crash recovery and verified upstream termination. A ledger alone cannot
   stop an already admitted provider session or make a duration-based estimate
   an enforced cost ceiling.
4. Verify actual usage, complete response accounting, surcharge/billing policy,
   and how to reconcile missing or late usage. Unknown results retain the
   entire hold; never blindly retry. Do not refund based on untrusted metadata.
5. Reuse the existing `tenant_ai_spend_budget.py` where appropriate: it already
   offers tenant-scoped reservations and shared advisory locking. Its default
   uncapped behavior, tenant scope, text-token pricing and campaign semantics
   do not on their own enforce this specific cross-executor $5 voice-test grant.
   Integration requires review of interaction with existing tenant limits and a
   migration plan, not a second independent production accounting system.

## Isolated tests

Use a throwaway local PostgreSQL server with a dedicated test admin account.
The test creates/drops a random database named `test_voice_budget_<uuid>`;
the guard rejects URL query parameters, non-local hosts and other DB names.

```sh
VOICE_BUDGET_POC_ADMIN_URL='postgresql://testuser:testpass@127.0.0.1:PORT/postgres' \
  /workspace/.venvs/beintaskbot311/bin/python -m unittest discover \
  -s experiments/voice_budget_poc -p 'test_*.py' -v
```

The tests use synthetic inputs, independent PostgreSQL connections, 32 parallel
executor reservations, duplicate/fingerprint and claim races, a child process
that exits after claim and retries from a fresh process, unknown-result holds,
cancel/claim races and overrun freeze. No provider call is made. They do not
prove live auth, spend controls, actual prices or upstream shutdown.
