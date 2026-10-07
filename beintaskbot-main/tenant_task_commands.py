"""Durable task command checkpoints with cross-process exclusion.

The session advisory lock survives checkpoint commits, but releases on process
death. Pending external writes are never replayed automatically after a crash.
"""
import json
from tenant_platform import _connect, _ensure_schema, TenantPlatformError


class TaskCommandPending(TenantPlatformError):
    """An external write may have succeeded; do not change the request key."""


class TaskCommandStore:
    def __init__(self, tenant_id: str, actor_id: int, request_id: str, fingerprint: str):
        self.key = (tenant_id, actor_id, request_id)
        self.conn = _connect()
        try:
            _ensure_schema(self.conn)
            with self.conn.cursor() as cur:
                cur.execute('SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS locked',
                            (':'.join(map(str, self.key)),))
                if not cur.fetchone()['locked']:
                    raise TaskCommandPending('Bu sorğu artıq işlənir. Bir az gözləyin.')
                cur.execute('''INSERT INTO saas_task_commands (tenant_id, actor_id, request_id, fingerprint)
                               VALUES (%s::uuid, %s, %s::uuid, %s) ON CONFLICT DO NOTHING''',
                            (*self.key, fingerprint))
                cur.execute('''SELECT fingerprint, state FROM saas_task_commands
                               WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid''', self.key)
                row = cur.fetchone()
                if row['fingerprint'] != fingerprint:
                    raise TenantPlatformError('Eyni sorğu kodu ilə fərqli tapşırıq göndərilib.')
                self.state = row['state'] or {}
            self.conn.commit()
        except BaseException:
            self.conn.close()
            raise

    def save(self, state: dict) -> None:
        # Keep the review envelope through provider checkpoints and retries.
        state = {**{key: self.state[key] for key in ('approval', 'task_input') if key in self.state}, **state}
        with self.conn.cursor() as cur:
            cur.execute('''UPDATE saas_task_commands SET state=%s::jsonb, updated_at=now()
                           WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid''',
                        (json.dumps(state), *self.key))
        self.conn.commit()
        self.state = state

    def lock_deal(self, lead_id: int) -> None:
        """Different requests must not reassign the same deal concurrently."""
        with self.conn.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS locked',
                        (f'task-deal:{self.key[0]}:{lead_id}',))
            if not cur.fetchone()['locked']:
                raise TaskCommandPending('Bu sövdələşmə üzrə başqa əməliyyat işlənir. Bir az gözləyin.')
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


def pending_task_approvals(profile: dict) -> list[dict]:
    """Only active administrators with task access can review this tenant."""
    from tenant_policy import TenantPolicy
    policy = TenantPolicy(profile)
    if not policy.privileged or not policy.allows('tasks'):
        raise TenantPlatformError('Təsdiq üçün icazəniz yoxdur.')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT c.actor_id, c.request_id, c.state, c.created_at,
                                  m.display_name AS creator_name, e.display_name AS executor_name
                           FROM saas_task_commands c
                           LEFT JOIN saas_tenant_members m ON m.tenant_id=c.tenant_id AND m.telegram_id=c.actor_id
                           LEFT JOIN saas_tenant_members e ON e.tenant_id=c.tenant_id
                             AND e.telegram_id::text=c.state->'task_input'->>'executor_id'
                           WHERE c.tenant_id=%s::uuid AND c.state ? 'approval'
                             AND c.state->>'step' NOT IN ('done', 'rejected')
                           ORDER BY c.created_at ASC LIMIT 200''', (profile['tenant_id'],))
            rows = cur.fetchall()
    return [{'creator_id': row['actor_id'], 'creator_name': row['creator_name'] or 'Əməkdaş',
             'executor_name': row['executor_name'] or 'Əməkdaş',
             'request_id': str(row['request_id']), 'created_at': row['created_at'].isoformat(),
             'step': row['state'].get('step'), 'task': row['state'].get('task_input') or {}}
            for row in rows]


def approval_command(profile: dict, creator_id: int, request_id: str) -> dict:
    """Read review input by tenant and actor, never by request ID alone."""
    from tenant_policy import TenantPolicy
    policy = TenantPolicy(profile)
    if not policy.privileged or not policy.allows('tasks'):
        raise TenantPlatformError('Təsdiq üçün icazəniz yoxdur.')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT state FROM saas_task_commands
                           WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid''',
                        (profile['tenant_id'], creator_id, request_id))
            row = cur.fetchone()
    if not row or not (row['state'] or {}).get('approval'):
        raise TenantPlatformError('Təsdiq sorğusu tapılmadı.')
    return row['state']


def task_executor_profiles(profile: dict) -> list[dict]:
    """Minimal active employee snapshots, from one tenant query (no N+1)."""
    from tenant_policy import TenantPolicy
    if not TenantPolicy(profile).allows('tasks'):
        return []
    if not TenantPolicy(profile).privileged:
        return [profile]
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT telegram_id, display_name, role, permissions, active
                           FROM saas_tenant_members WHERE tenant_id=%s::uuid AND active=TRUE
                           ORDER BY display_name, telegram_id''', (profile['tenant_id'],))
            rows = cur.fetchall()
    return [{**profile, **dict(row)} for row in rows]
