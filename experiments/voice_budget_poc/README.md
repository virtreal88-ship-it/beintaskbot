# Isolated voice-test budget ledger experiment

This experiment does not call OpenAI or authenticate users. Its test harness
accepts only a dedicated localhost test database. It cannot establish a
project-wide spending ceiling. The user has authorized a
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
  32,000 model-maximum output tokens as a conservative reservation assumption,
  although the session API now explicitly requests 4,096. Charge every input/output token as audio (the
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

## Disabled server candidate and migration gate

`beintaskbot-main/tenant_voice_test_grant.py` is a review candidate with no
importing route, separate in-source `ENFORCEMENT_VERIFIED=False` and
`RECONCILIATION_VERIFIED=False` guards, and a database grant whose proposed
migration defaults to disabled. It does
not connect to a database on its own. Its caller supplies an open transaction
after authorization. `reserve()` locks a cross-tenant singleton first and then
reuses `tenant_ai_spend_budget.reserve()` under its existing tenant advisory
lock in the same transaction. A voice session is bound to one tenant, actor,
channel, dialog and executor. `claim()` rechecks both reservations and the
current tenant cap and moves the tenant hold to `unknown` atomically. Claim
alone does not authorize send. A separate `issue_dispatch()` obtains the
same grant lock, rechecks freeze, the exact immutable scope/fingerprint, quote,
expiry, tenant hold and current cap, and transitions `claimed -> dispatching`
exactly once across processes. Its transaction must commit before *any*
provider send; a crash after commit, timeout, or ambiguous response retains
the full hold and must never issue/send again. The caller must repeat real
Copilot authorization within the same transaction and must keep the return
value server-side. This state machine cannot prevent bypassing it from other
code or prove a provider-side cost bound. The tested generic release/settle
methods only act on `reserved` and cannot release a claimed hold. An actual
provider cost is recorded separately in the voice ledger; the tenant record is
left `unknown` with a full hold and no false `actual_microusd`. If late cost
exceeds that hold, the tenant `reserved_microusd` rises monotonically under
its existing advisory lock before subsequent ordinary AI admissions; the
voice grant also freezes. New admissions can be disabled independently of
trusted reconciliation so that later usage is still recorded and can freeze
the grant. Both code gates default closed and the actual trusted reconciliation
entry point/authentication is not implemented. This is a
temporary conservative accounting limitation, not a complete reporting or
settlement integration. On an inconsistent tenant hold, reconciliation locks
the global grant then the tenant advisory lock, and attempts repair only when
tenant, reservation ID, call key, kind and pinned model all match. It can also
recreate a missing exact ID from the voice row if there are no collisions.
Under normal constraints the foreign key prevents deletion; the missing-row
test disables that constraint only in the disposable database. Repair preserves
any existing `actual_microusd`, never lowers the hold, records a permanent
repair status, and freezes **voice** admissions. An immutable scope mismatch or
ID/call-key collision leaves the unrelated row untouched and quarantines the
voice operation. The trusted caller must commit that quarantine, not roll it
back as an exception. These proofs depend on a trusted, intact voice row and
one shared database; they do not protect calls admitted before the repair or
database writers that bypass the tenant advisory lock. If scope cannot be
proven, ordinary tenant AI may still undercount until manual reconciliation.
These remain release blockers.

`voice_test_grant.sql` is a **one-shot review artifact**, not integrated in the
repository migration runner. A second application fails rather than being
idempotent. Its added `dispatching` state needs an explicitly versioned upgrade
for any existing schema; the v1 CHECK constraint does not admit it. Version
handling, partial migration recovery, migration review,
privilege boundaries and safe deployment/rollback remain release blockers.
Do not run it in production. Existing tenant budget code is not modified.
Tests temporarily patch the code gate and enable only a disposable test DB.

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
   This candidate's `unknown` tenant holds preserve existing reporting semantics
   but intentionally over-reserve until a trusted reconciliation method is
   designed; no false actual costs are written. Accounting integration remains
   incomplete and the module must remain closed.

### Path inventory and fence boundary

The review identified `reserve`/`reserve_media` consumers in agent run store,
simulation, testing, saved-only Copilot, media transcription, knowledge and
expert services. Their generic financial operations share `tenant-ai-spend`
advisory locks only when their code actually reserves. In particular, those
functions return immediately on an uncapped path unless explicitly tracking a
reservation. The ordinary `tenant_operator_copilot.run` has its own daily-call
limits and invokes `tenant_chat_ai_provider.generate` without this monetary
reservation. `tenant_chat_ai_service`, news provider and legacy `bot.py` expose
additional provider entrypoints whose relationship to tenant scope requires
further review. This is an inventory of observed paths, not proof that every
route reaches a live provider.

A broad tenant quarantine fence would require an agreed behavior change and
auditing/instrumenting all applicable provider admissions, including uncapped
and old-worker paths. A fence only in `reserve` would not cover the Copilot
example and would risk an unreviewed interruption of unrelated AI functions.
It is not implemented. The narrow repair does not change ordinary AI code or
add a broad fence, but increasing a tenant's restored reserve can cause its
existing monetary cap to reject later unrelated AI requests. That product
effect and the manual-reconciliation process must be reviewed before enabling
voice. If the repair cannot prove the original hold, voice remains closed
instead of asserting a tenant-wide guarantee.

## Isolated tests

Use a throwaway local PostgreSQL server with a dedicated test admin account.
The test creates/drops a random database named `test_voice_budget_<uuid>`;
the guard rejects URL query parameters, non-local hosts and other DB names.

```sh
VOICE_BUDGET_POC_ADMIN_URL='postgresql://testuser:testpass@127.0.0.1:PORT/postgres' \
  PYTHONPATH=beintaskbot-main \
  /workspace/.venvs/beintaskbot311/bin/python -m unittest discover \
  -s experiments/voice_budget_poc -p 'test_*.py' -v
```

The tests use synthetic inputs, independent PostgreSQL connections, 32 parallel
executor reservations, duplicate/fingerprint and claim races, a child process
that exits after claim and retries from a fresh process, 40 simultaneous
dispatch claims with exactly one winner, a child that exits after committed
dispatch and a restart that cannot reissue it, unknown-result holds,
cancel/claim races and overrun freeze. No provider call is made. They do not
prove live auth, spend controls, actual prices or upstream shutdown.
