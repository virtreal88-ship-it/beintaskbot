# Railway cutover: independent read-only acceptance contract

**Decision: NO-GO.** This is an investigation, not an implemented admission
system, an authorization to change Railway, or a statement that production is
ready. The experimental voice-write gate always denies. The existing text
assistant is not stopped by this artifact. An operator's assertion, an env var,
or this document cannot open the gate.

## What was observed

On 2026-11-03 around 04:32 UTC, the Railway read-only service and deployment
metadata showed, for `powerful-solace` production:

* `worker`: configured one replica, `python bot.py`, deployment
  `d243f6e5-3c97-47d2-901f-7dc3f540ea7f` SUCCESS at source SHA
  `fc75c2899b7b86de42bf2cafdbdd116e9717d821`. In the bounded deployment list,
  the preceding entries were REMOVED and separate status-filtered queries
  found no REMOVING, SLEEPING, DEPLOYING, INITIALIZING, QUEUED, BUILDING or
  CRASHED deployments. This is a point-in-time, service-specific metadata
  snapshot; the list was limited and is not evidence of live replica identity,
  traffic routing, all historic deployments, or in-flight request completion.
  Runtime replica count was not returned by the status tool.
* `ai-agents-clone-check`: configured one replica from the same repository,
  branch `codex/ai-agents-tenant-audit-diagnostics`, source SHA
  `4ad078aea2c574f57263f1c86e21b12db4a489c1`, with Dockerfile
  `/beintaskbot-main/scripts/railway_ai_agents_clone_check/Dockerfile` and no
  configured start-command override. At that source revision its Dockerfile
  specifies a volume-audit entrypoint that says it does not connect to a DB.
  This is source/config evidence, **not** proof of actual running command,
  mounted code, effective DB privileges or absence of a different write path.
  The variable *name* `SOURCE_READONLY_DATABASE_URL` proves no privilege.

Neither service is the proposed Phase A revision. No production database,
credential values, requests, processes, or logs were inspected. Other services,
jobs, clients and database roles have not been exhaustively inventoried.
Recollect evidence for the exact target and time of any future cutover.

Railway documents deployment teardown with overlapping deployments and
SIGTERM/SIGKILL, and identifies deployment statuses including REMOVING and
REMOVED. Its reference gives a default zero-second draining period; no graceful
completion may be assumed. See [teardown](https://docs.railway.com/deployments/deployment-teardown)
and [deployment reference](https://docs.railway.com/deployments/reference).
Even a confirmed terminated process does not prove its provider call completed
or that its result was saved; a rollback or later redeploy can revive old code.

## Candidate phases and compatibility boundary

**Phase A, preparation only:** retain the legacy writer and current text/clear
semantics, make any schema change additive and separately validate it against
legacy readers and writers, and keep voice hard-off. A new binary can contain
inactive code, but must continue legacy behavior while older writers exist.
The current experiment branch unconditionally changes the text writer and is
therefore **not** a mixed-worker Phase A release candidate. Its safety cannot be
inferred from successful migration or CI.

**Phase B, conditional cutover:** activate the transactional new text/voice
history protocol only after all legacy requests and writers are accounted for,
drained, and continuously fenced against revival. An independently enforced
write boundary must cover insert, update, delete, clear, background work and
previously admitted requests. Voice remains hard-off until this boundary and
the separate authorization, retention, session and cost gates are proven.
The required mechanism does not exist here. In particular, Railway deployment
state by itself neither provides an atomic admission fence nor a fleet-wide
request ledger. Nothing in this document permits blocking the text assistant.

The difficult handoff includes **new Phase A requests**, not just requests from
the previous deployment. If these continue using the legacy history algorithm,
they too may hold old snapshots while new-protocol writes start. A real design
must prove an atomic transition for every such request while preserving text
availability. Observing a temporary zero is not a barrier. The experiments in
`MERGE_EVIDENCE.md` reject the tested trigger merge and demonstrate the missing
scoped zero-row-clear information; they do not rule out every possible new
architecture. No safe zero-interruption transition is currently established.

## Independent preflight and acceptance evidence

The following are **requirements**, not implemented checks. A future review
must identify who collects each item, authoritative source, target, complete
scope, time/version, expiry, and independent reviewer. Missing, stale,
inaccessible or contradictory evidence means NO-GO; do not turn a lack of
visibility into a zero. Aggregate evidence must not expose customer messages,
identifiers, secret values, credentials, or raw provider audio.

| Domain | Required positive evidence and acceptance criterion | Current gap |
| --- | --- | --- |
| Inventory | Complete production inventory of all services, jobs, one-off processes and external clients with effective ability to mutate the relevant thread, clear or receipt paths, including non-main services and all DB connection identities. Source, deployed image digest, command, environment and traffic/background scope must be tied to each live instance. | Railway service/config snapshot is not a complete writer or process inventory. |
| Fleet and routing | Authoritative runtime instance membership and per-instance deployed immutable digest; all legacy replicas terminated, no old deployment in progress or able to route, and all relevant ingress, scheduler and background consumers use the reviewed version. Independently cross-check Railway lifecycle and runtime/routing information; configure proof expiry. | Deployment SUCCESS/REMOVED and configured replica count do not establish runtime membership, routes or in-flight work. |
| Drain and requests | An exhaustive, version-bound admission and in-flight record covering work before receipt insertion, DB transactions, provider waits and post-provider persistence; every admitted legacy/Phase A operation has an evidenced terminal state. Unknown external outcomes retain their existing receipts and holds; no blind replay. Provider termination and result persistence are distinct facts. | Existing shared heartbeat and visible committed receipt counts are not such a ledger; transactions can be invisible and calls can be outside a DB transaction. |
| DB authority and sessions | Read-only catalog evidence for effective rights on all history/receipt objects and write-capable functions, including owner, superuser, PUBLIC and inherited grants, role membership/SET ROLE, SECURITY DEFINER and alternate identities. Account for all relevant current connections, transactions and prepared transactions, with sufficient visibility to detect hidden sessions. Bound each connection to an inventoried instance/version or treat it as unknown. Verify any proposed fence also covers existing connections/transactions. | No actual DB permissions, process-to-connection binding or session visibility were inspected. A variable name and a synthetic-role test prove none of them. |
| Durable fence and epoch | Independently controlled and continuously enforced, atomic writer generation/fence checked at the actual DB mutation boundary, including clear and stale requests. It must reject or safely prevent every old writer including old binaries that never check the gate; forbid silent rollback/revival and expire admission when proof is lost. Demonstrate the exact transition without lost/duplicated messages or changed clear semantics, with concurrent real PostgreSQL tests and independent review. | No such live control plane, identity separation, automatic revocation, epoch mechanism or atomic admission exists. A standalone always-closed gate is not a fence. |
| Text continuity | Prove that routing, draining and fencing do not fail or silently lose existing text requests, including one waiting on the provider at the transition. Verify real existing authentication, tenant isolation, receipt replay, clear and rollback. Do not substitute stopping or fencing a live text request as a successful drain. | Synthetic role revocation blocked a late legacy `_finish` and left its receipt `sending`. No safe transition has been established. |
| Voice and separate release gates | Real server-side auth per turn, common ordered history, partial transcript semantics, safe session/provider termination, durable shared budget reservation and confirmed retention/cleanup policy, separately verified. | Not implemented/proven; budget and unknown outcomes need their own controls. |

## Collection procedure (read-only; no production action performed)

1. Obtain complete service/deployment inventories using the Railway read-only
   project, environment, service description and deployment-list APIs, including
   pagination or a documented complete replacement. Record all status filters,
   exact digests and times. Do not request environment variable values.
2. Independently obtain actual process/replica membership, routing and request
   state from an authoritative observer with full scope; establish how it
   detects starts, exits, background workers, old in-flight requests and missing
   telemetry. If unavailable, mark the corresponding criteria NOT VERIFIED.
   Do not infer that clone-check is harmless from its label or Dockerfile alone.
3. If separately authorized and equipped with a proven, sufficiently visible
   read-only DB observation path, inspect catalog/role metadata, effective
   privileges, session/transaction and prepared-transaction metadata without
   querying message content. Read-only transaction and bounded statement/lock
   timeouts are necessary but not a substitute for adequate visibility; a
   restricted `pg_stat_activity` view cannot establish absence of hidden work.
   Do not print connection strings, session query text, role secrets or values.
   This investigation did not connect to the production database.
4. Reconcile every writer and request against the evidence matrix and the exact
   reviewed release, demonstrate the separately built fence and no-interruption
   handoff, then repeat checks at transition under that fence. Evidence must be
   continuously invalidated by new writers, permission changes, loss of
   visibility, deployment, rollback, or restart. Two snapshots alone cannot
   exclude an intervening writer; a human checklist is not atomic enforcement.

Rollback is a new cutover: old binaries must not regain write access to history
after voice writes begin. Loss of fleet visibility or fence keeps voice closed;
it must not silently alter existing text behavior. Existing cached receipt versus
clear semantics and unscheduled cleanup remain separate unresolved decisions.
