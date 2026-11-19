# Dialog lifecycle: activation gate

This plan is not evidence of any production changes.

## Before release
- Test the exact merged tree against current main; required CI includes native PostgreSQL ownership/identity races and legacy behavior.
- Refresh production schema, services, replicas, running jobs, and actual backup/restore evidence. Prior snapshots are insufficient.
- Verify a recoverable backup and documented restore target; obtain approval for any backup cost or new sensitive-data destination. Never put production dumps in public artifacts or CI.
- Specify one target tenant. Other tenants remain legacy.

## Order
1. Take and verify the approved backup; record timestamp and recovery procedure without secrets.
2. Use a separate short transaction with local lock_timeout=2s, statement_timeout=30s to run reviewed tenant_dialog_lifecycle.migrate(cursor). It creates rollout, five lifecycle/episode tables, and the local employee presence table/index. No backfill, deletion, role changes or ownership reassignment. Abort on timeout/unexpected schema; verify definitions and constraints.
3. Only after schema verification merge/deploy the compatible tested code. Main auto-deploys; code deployed before schema fails closed even for legacy tenants.
4. Keep every tenant legacy. Verify existing list/detail/history, sends, notifications, workers. No real customer test messages; only test doubles or separately approved staff smoke tests.
5. Verify ALL web/worker replicas run compatible SHA; drain old containers and in-flight jobs, prevent restarting older deployments. Advisory barriers cannot fence binaries which do not acquire them. Stop if this cannot be established.
6. In a fresh short transaction activate only the explicit tenant: mode=active, admission_enabled=true, fresh expected_generation. Exclusive try-lock fails fast if protected operations are in flight. Never force locks/reset rows. Audit actor, tenant, SHA, old/new generation and reason. No provider call inside activation.
7. Verify first-wins acceptance, exact-source exclusivity (including owner), transfer, close, genuine incoming reopen, no-reply action and AI pause. Verify other tenant remains legacy; stale clients must discard denied data.

## Rollback and limitations
- Before activation: compatible code rollback, retain additive tables. No schema drop or blanket database restore.
- After activation: NEVER run old binaries or switch legacy; that exposes claimed histories/sends. Use a compatible forward fix. admission_enabled=false stops new claims while retaining guards; it is a service limitation, not transparent rollback.
- Already dispatched messages/notifications may arrive later; locks cannot retract provider delivery.
- Identity merge/rollback touching any workflow/episode is denied until episode-preserving migration exists; unrelated routes remain eligible. Existing links/history stay intact.
- Existing cases/messages/receipts remain. No automatic historical analysis, segmentation or assignment backfill.
- Routes without workflow remain eligible; accept starts exact episode, close ends it; others regain visibility only after genuine new incoming.
- Manual Send to cases uses exact completed episode and existing budget/AI-use rights. Maximum80messages/12,000characters: reject BEFORE spend, never truncate. Unresolved old unknown receipts prevent claiming complete coverage.
- Autopublish requires initiating actor's existing publish authority and evidence gates; otherwise draft/reason. No owner impersonation or new grants.
- No paid AI smoke call without separately bounded scope.
