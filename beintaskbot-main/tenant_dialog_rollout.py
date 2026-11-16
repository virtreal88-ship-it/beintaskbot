"""Durable, tenant-local lifecycle rollout fences. No commits or runtime DDL.

Acquire guard BEFORE authorization/member/source/group/route locks and retain the
transaction through protected reads or provider dispatch. Every participating
process must run compatible code before transition() can activate a tenant.
These locks cannot fence old binaries which do not acquire them.
"""
from dataclasses import dataclass
import uuid
from tenant_linear_policy import TenantLinearError


@dataclass(frozen=True)
class RolloutState:
    mode: str = 'legacy'
    activated: bool = False
    admission_enabled: bool = False
    generation: int = 0

    @property
    def enforced(self):
        return self.activated


def migrate(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_dialog_rollout (
      tenant_id UUID PRIMARY KEY REFERENCES saas_tenants(id),
      mode TEXT NOT NULL CHECK(mode IN ('legacy','shadow','active')),
      activated_at TIMESTAMPTZ,
      admission_enabled BOOLEAN NOT NULL DEFAULT FALSE,
      generation BIGINT NOT NULL CHECK(generation > 0),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
      CHECK((mode='active') = (activated_at IS NOT NULL)),
      CHECK(NOT admission_enabled OR mode='active'))''')


def _lock(cur, tenant, *, exclusive):
    tenant = str(uuid.UUID(str(tenant)))
    # Nonblocking by design: never create a lock queue behind a slow network
    # dispatch, nor overwrite the caller's transaction-wide lock_timeout.
    function = 'pg_try_advisory_xact_lock' if exclusive else 'pg_try_advisory_xact_lock_shared'
    cur.execute(f'SELECT {function}(hashtextextended(%s,0)) AS acquired',
                ('dialog-rollout:' + tenant,))
    if not (cur.fetchone() or {}).get('acquired'):
        raise TenantLinearError('Режим диалогов обновляется. Повторите запрос.', 409)
    return tenant


def _read(cur, tenant):
    cur.execute('''SELECT mode,activated_at,admission_enabled,generation
      FROM saas_dialog_rollout WHERE tenant_id=%s::uuid''', (tenant,))
    row = cur.fetchone()
    if not row:
        return RolloutState()
    return RolloutState(row['mode'], row['activated_at'] is not None,
                        bool(row['admission_enabled']), int(row['generation']))


def guard(cur, tenant, *, allow_unmigrated=False):
    """Shared barrier + fresh state; do not cache across transactions.

    allow_unmigrated is ONLY for the preactivation bridge release. After schema
    migration use the default; missing/unreadable schema must fail closed.
    An env flag must never select this compatibility option or disable guards.
    """
    tenant = _lock(cur, tenant, exclusive=False)
    if allow_unmigrated:
        cur.execute("SELECT to_regclass('saas_dialog_rollout') IS NOT NULL AS ready")
        if not (cur.fetchone() or {}).get('ready'):
            return RolloutState()
    return _read(cur, tenant)


def validate_transition(current, mode, admission_enabled, expected_generation):
    if mode not in {'legacy','shadow','active'} or type(admission_enabled) is not bool:
        raise TenantLinearError('Некорректный режим диалогов.', 400)
    if type(expected_generation) is not int or expected_generation != current.generation:
        raise TenantLinearError('Режим диалогов уже изменился.', 409)
    if current.activated and mode != 'active':
        raise TenantLinearError('Защиту принятых диалогов нельзя отключить.', 409)
    if admission_enabled and mode != 'active':
        raise TenantLinearError('Приём доступен только в активном режиме.', 409)
    if mode == 'active' and not current.activated and not admission_enabled:
        raise TenantLinearError('Первое включение требует одновременного открытия приёма.', 409)


def transition(cur, tenant, *, mode, admission_enabled, expected_generation):
    """Internal approved rollout command, NOT an unauthenticated public API.

    Run in a fresh short transaction: do not upgrade a shared guard or acquire
    member/source/route locks first. Caller owns authorization, audit and commit.
    Admission-off is a stop on new claims, never an ownership-guard rollback.
    """
    tenant = _lock(cur, tenant, exclusive=True)
    current = _read(cur, tenant)
    validate_transition(current, mode, admission_enabled, expected_generation)
    cur.execute('''INSERT INTO saas_dialog_rollout
      (tenant_id,mode,activated_at,admission_enabled,generation)
      VALUES(%s::uuid,%s,CASE WHEN %s='active' THEN clock_timestamp() END,%s,%s)
      ON CONFLICT(tenant_id) DO UPDATE SET mode=EXCLUDED.mode,
      activated_at=COALESCE(saas_dialog_rollout.activated_at,EXCLUDED.activated_at),
      admission_enabled=EXCLUDED.admission_enabled,generation=EXCLUDED.generation,
      updated_at=clock_timestamp()''',
      (tenant, mode, mode, admission_enabled, current.generation + 1))
    return RolloutState(mode, current.activated or mode == 'active',
                        admission_enabled, current.generation + 1)
