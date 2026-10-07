"""Bounded PostgreSQL queue/checkpoints. Telegram transport lives separately."""
from tenant_platform import _connect, _ensure_schema, _read_tenant_workflow
from tenant_policy import TenantPolicy
from tenant_notifications import notification_channels


def eligible(profile: dict, event: str, tenant_id: str) -> bool:
    return (profile.get('tenant_status') in {'active', 'onboarding', 'ready_for_integration'}
            and 'telegram' in notification_channels(TenantPolicy(profile), event=event, tenant_id=tenant_id))


def _profiles(cur, tenant_id: str, recipient_id: int | None = None) -> list[dict]:
    cur.execute('''SELECT m.*, t.name AS tenant_name, t.status AS tenant_status,
                          t.modules, t.notification_rules
                   FROM saas_tenant_members m JOIN saas_tenants t ON t.id=m.tenant_id
                   WHERE m.tenant_id=%s::uuid AND m.active=TRUE AND m.role IN ('owner','admin')
                     AND (%s::bigint IS NULL OR m.telegram_id=%s::bigint)''',
                (tenant_id, recipient_id, recipient_id))
    rows = cur.fetchall()
    workflow = _read_tenant_workflow(cur, tenant_id) if rows else {}
    return [{**row, 'tenant_id': str(row['tenant_id']), 'workflow': workflow} for row in rows]


def expand_events() -> int:
    """Bounded batch; recipients are frozen once, not backfilled on opt-in."""
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT e.*, c.state FROM saas_approval_notification_events e
                           JOIN saas_task_commands c USING (tenant_id, actor_id, request_id)
                           WHERE e.expanded=FALSE ORDER BY e.created_at LIMIT 10
                           FOR UPDATE OF e SKIP LOCKED''')
            events = cur.fetchall()
            profiles = {}
            for item in events:
                key = (str(item['tenant_id']), item['actor_id'], str(item['request_id']))
                if item['state'].get('step') == 'waiting_approval':
                    if key[0] not in profiles:
                        profiles[key[0]] = _profiles(cur, key[0])
                    for profile in profiles[key[0]]:
                        if eligible(profile, item['event'], key[0]):
                            cur.execute('''INSERT INTO saas_approval_notification_deliveries
                                           (tenant_id,actor_id,request_id,recipient_id,channel)
                                           VALUES (%s::uuid,%s,%s::uuid,%s,'telegram') ON CONFLICT DO NOTHING''',
                                        (*key, profile['telegram_id']))
                cur.execute('''UPDATE saas_approval_notification_events SET expanded=TRUE
                               WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid''', key)
        conn.commit()
    return len(events)


def claim_delivery() -> dict | None:
    """Commit 'sending' before future I/O. Crashes cannot replay an attempt."""
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_approval_notification_deliveries SET status='unknown',updated_at=now()
                           WHERE status='sending' AND updated_at < now()-interval '5 minutes' ''')
            cur.execute('''SELECT d.*, e.event, c.state, creator.display_name AS creator_name
                           FROM saas_approval_notification_deliveries d
                           JOIN saas_approval_notification_events e USING (tenant_id,actor_id,request_id)
                           JOIN saas_task_commands c USING (tenant_id,actor_id,request_id)
                           LEFT JOIN saas_tenant_members creator
                             ON creator.tenant_id=d.tenant_id AND creator.telegram_id=d.actor_id
                           WHERE d.status='pending' ORDER BY d.updated_at LIMIT 1
                           FOR UPDATE OF d SKIP LOCKED''')
            item = cur.fetchone()
            if not item:
                conn.commit()
                return None
            item['tenant_id'] = str(item['tenant_id'])
            profiles = _profiles(cur, item['tenant_id'], item['recipient_id'])
            allowed = (item['state'].get('step') == 'waiting_approval' and profiles
                       and eligible(profiles[0], item['event'], item['tenant_id']))
            cur.execute('''UPDATE saas_approval_notification_deliveries SET status=%s,updated_at=now()
                           WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid
                             AND recipient_id=%s AND channel=%s''',
                        ('sending' if allowed else 'skipped', *delivery_key(item)))
            item['send'] = bool(allowed)
            if allowed:
                item['tenant_name'] = profiles[0]['tenant_name']
        conn.commit()
    return item


def delivery_key(item: dict) -> tuple:
    return (item['tenant_id'], item['actor_id'], str(item['request_id']), item['recipient_id'], item['channel'])


def finish_delivery(item: dict, *, status: str, message_id: int | None = None) -> None:
    if status not in {'delivered', 'unknown'}:
        raise ValueError('Invalid delivery result')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_approval_notification_deliveries
                           SET status=%s,message_id=%s,updated_at=now()
                           WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid
                             AND recipient_id=%s AND channel=%s AND status='sending' ''',
                        (status, message_id, *delivery_key(item)))
        conn.commit()
