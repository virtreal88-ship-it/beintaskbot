# Companion-reservation counterexamples — test only

This directory contains no application integration. It invokes no provider,
uses synthetic rows in a disposable local PostgreSQL database, and must not be
deployed or connected to production. Voice remains disabled. No broad tenant
fence or modifications to the existing budget module are proposed here.

## Result

An immutable-scope conflict does **not** by itself establish whether an
existing positive tenant-ledger contribution is the same voice exposure or an
independent legitimate cost. For the same observed DB row of $2.112 and a voice
exposure of $3, both histories are compatible with the snapshot:

* If the existing row is the same voice, the correct total is $3. Adding a
  $3 companion makes the ledger count $5.112 and double-counts $2.112.
* If the existing row is a separate legitimate cost, the correct total is
  $5.112. Taking the larger of the existing amount and the voice exposure
  gives $3 and undercounts $2.112.

The database snapshot is identical in the test; the two ownership histories are
external ground truth, **not** an assertion that either occurred in production.
Consequently neither full addition nor subtraction/max can guarantee both no
undercount and no double count without stronger trusted evidence linking each
ledger contribution to its original operation. A full companion is a safe
conservative *upper bound* but may unnecessarily exhaust the existing company
cap and affect unrelated AI. That is a product consequence, not an exact cost.

## Bounded locked-snapshot experiment

The test-only helper tests a narrower idea: take the global grant row lock,
validate the quarantined voice request, then take the tenant budget advisory
lock (`global -> tenant`). Inspect rows colliding by original reservation ID or
call key; refuse if any is not `released` with `actual_microusd=0`. At that
instant the generic ledger gives those rows zero contribution. Derive a stable
companion ID and key from tenant+request, insert an `unknown` hold with the
larger of the original quote and observed cost; repeated requests return the
same row and only increase it. Refuse a collision on the companion's own ID or
key. The real generic capped reserve sees that hold. No fabricated actual cost
is written. All these operations run inside the caller's transaction.

Even that limited construction is not sufficient for production. The test
deliberately simulates a later database writer changing the previously zero
row to a contributing state: the pre-existing row and companion are then both
counted. The simulation does **not** claim an existing application path does so.
There is no proven immutable provenance, universal writer fencing or trusted
single admission boundary. The normal generic API does not reopen a released
reservation, but legacy workers, direct SQL, recovery tools and future code
have not been completely excluded. The uncapped generic path currently skips
monetary accounting, and some provider paths do not use the generic ledger.
The two true costs therefore cannot be isolated safely merely by this helper.

To move beyond the blocker, first design and verify an immutable mapping from
the original provider operation to financial entries, transactional monotonic
state across **all** writers and workers, and a single server-owned admission
authority. A deliberately conservative duplicate reserve instead requires a
reviewed decision about its impact on the tenant's existing AI operations.
Neither a false exact total nor automatic activation is acceptable.

## Run the local tests

The shared budget experiment's localhost-only guards are reused. Supply a
disposable local PostgreSQL server; tests create and drop a random database:

```sh
VOICE_BUDGET_POC_ADMIN_URL='postgresql://testuser:testpass@127.0.0.1:PORT/postgres' \
  PYTHONPATH=beintaskbot-main:experiments/voice_budget_poc \
  /workspace/.venvs/beintaskbot311/bin/python -m unittest discover \
  -s experiments/voice_companion_poc -p 'test_*.py' -v
```

The tests exercise 20 concurrent requests for one deterministic companion,
terminal-zero snapshot, monotonic increase, conflicting companion key, the
generic capped admission path and two counterexamples. The account, contact,
events, credentials and results are synthetic. These are not real API costs or
proof of a hard global budget.
