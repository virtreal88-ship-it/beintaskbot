# Copilot history compatibility — candidate rollout only

Production metadata read-only: Worker `fc75c2899b7b86de42bf2cafdbdd116e9717d821`, one running replica, successful deployment. Connect had one running replica; literal source search at `bd0828eb7fa203c4f5bfa3a68e5a71e7a16d585f` did not find the history table. A separate `ai-agents-clone-check` reported online but zero running replicas; that inconsistency proves neither presence nor absence of writers. One metadata snapshot cannot establish all in-flight requests, direct SQL clients, autoscaling, or other processes. No production database was queried or changed.

The old Worker schema module creates the thread table; `tenant_operator_copilot.py` reads threads, deletes on clear/retention, and overwrites them in `_finish`. Saved-only uses the shared reader. Voice history remains hard-disabled. Clear does not delete the separate idempotent run receipt; it is not deletion of every copy and existing receipt retention is unchanged.

The new UI advertises support for a cleared-history notice; old servers ignore the extra field. If clear races generation, an old UI receives a clear 409 instead of placing a warning or answer in a customer composer. The completed receipt retains the answer under existing retention and access rules; retrying the exact request ID with a compatible UI recovers it without another provider call. Reload may lose an old UI's in-memory request ID, so recovery is not guaranteed and a new request may incur cost. Saved-only continues to use its strict contract without the capability field. This is an intentional user-visible tradeoff, not lossless recovery.

A genuinely absent `clear_revision` selects legacy text read/cleanup/upsert behavior; catalog lookup holds an AccessShare lock. Missing table/permission and malformed column fail closed. Synthetic tests show an old in-flight request can finish after expansion and recreate a cleared thread, and old writers can overwrite voice turns. Metadata permissions and current production fleet have not been verified beyond the snapshot. Fallback therefore does not authorize voice.

Candidate sequence, requiring independent review and operational proof before action:

1. Inventory all writers, deployment controllers, in-flight work, roles, external clients and runtime grants; one replica snapshot is insufficient.
2. Deploy compatible text/UI code before additive expansion only after reviewing behavior, cost and monitoring. New requests on the old schema retain legacy semantics.
3. Review additive expansion separately, including lock timeout and lock contention. Neither DDL nor metadata SELECT is proven zero-downtime. Drain all old processes and requests; no voice admission until this is independently proven and all relevant hard gates are resolved.
4. For rollback, prefer retaining the additive column with compatible text code. Removing it while a new request is in flight can leave the receipt `sending`; a tested drain and recovery are required. Old writers after voice writes can overwrite voice history, so safe zero-downtime voice rollback is not established.

No production DDL, deploy, paid provider call or voice activation was performed. Synthetic role and data are local and ephemeral.
