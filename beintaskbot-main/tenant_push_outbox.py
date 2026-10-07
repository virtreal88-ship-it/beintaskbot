"""Per-device push checkpoints, independent of Telegram outcomes."""
from tenant_platform import _connect, _ensure_schema
from tenant_notification_outbox import _profiles, eligible


def key(item: dict) -> tuple:
    return (str(item['tenant_id']), item['actor_id'], str(item['request_id']), item['recipient_id'], item['endpoint_hash'])


def claim_push() -> dict | None:
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""UPDATE saas_approval_push_deliveries SET status='unknown',updated_at=now()
                           WHERE status='sending' AND updated_at<now()-interval '5 minutes'""")
            cur.execute('''SELECT d.*,e.event,c.state,p.subscription,b.active AS device_active,p.owner_telegram_id
                FROM saas_approval_push_deliveries d
                JOIN saas_approval_notification_events e USING (tenant_id,actor_id,request_id)
                JOIN saas_task_commands c USING (tenant_id,actor_id,request_id)
                JOIN saas_push_devices p ON p.endpoint_hash=d.endpoint_hash
                LEFT JOIN saas_member_push_devices b ON b.tenant_id=d.tenant_id
                  AND b.telegram_id=d.recipient_id AND b.endpoint_hash=d.endpoint_hash
                WHERE d.status='pending' ORDER BY d.updated_at LIMIT 1 FOR UPDATE OF d SKIP LOCKED''')
            item = cur.fetchone()
            if item is None:
                conn.commit()
                return None
            profiles = _profiles(cur,str(item['tenant_id']),item['recipient_id'])
            allowed = (item['device_active'] is True and item['owner_telegram_id']==item['recipient_id']
                       and item['state'].get('step')=='waiting_approval' and profiles
                       and eligible(profiles[0],item['event'],str(item['tenant_id']),'push'))
            cur.execute('''UPDATE saas_approval_push_deliveries SET status=%s,updated_at=now()
                WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid
                  AND recipient_id=%s AND endpoint_hash=%s''',('sending' if allowed else 'skipped',*key(item)))
            item['send']=bool(allowed)
        conn.commit()
    return item


def finish_push(item: dict, status: str) -> None:
    if status not in {'delivered','unknown','expired'}:
        raise ValueError('Invalid push result')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_approval_push_deliveries SET status=%s,updated_at=now()
                WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid
                  AND recipient_id=%s AND endpoint_hash=%s AND status='sending' ''',(status,*key(item)))
            if status=='expired':
                cur.execute('''UPDATE saas_member_push_devices SET active=FALSE,updated_at=now()
                    WHERE endpoint_hash=%s AND telegram_id=%s''',(item['endpoint_hash'],item['recipient_id']))
        conn.commit()
