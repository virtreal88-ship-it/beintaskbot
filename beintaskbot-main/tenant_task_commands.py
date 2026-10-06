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
