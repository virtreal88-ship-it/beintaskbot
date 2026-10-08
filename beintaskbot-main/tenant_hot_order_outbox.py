"""Bounded tenant queue; recheck order, membership, service and device rights."""
from tenant_platform import _connect, _read_tenant_workflow
from tenant_hot_order_schema import ensure_hot_order_schema
from tenant_hot_order_policy import HotOrderPolicy
from tenant_policy import TenantPolicy
from tenant_notifications import notification_channels

EVENT = 'hot_order_available'


def eligible(profile: dict, order: dict, channel: str) -> bool:
    policy = TenantPolicy(profile)
    return (profile.get('tenant_status') in {'active','onboarding','ready_for_integration'}
            and HotOrderPolicy(policy).can_claim(order)
            and channel in notification_channels(policy,event=EVENT,tenant_id=str(order['tenant_id'])))


def profiles(cur, tenant: str, *, after: int = 0, recipient: int | None = None) -> list[dict]:
    cur.execute('''SELECT m.*,t.status AS tenant_status,t.modules,t.notification_rules
        FROM saas_tenant_members m JOIN saas_tenants t ON t.id=m.tenant_id
        WHERE m.tenant_id=%s::uuid AND m.active=TRUE AND m.telegram_id>%s
          AND (%s::bigint IS NULL OR m.telegram_id=%s::bigint)
        ORDER BY m.telegram_id LIMIT 100''',(tenant,after,recipient,recipient))
    rows = cur.fetchall()
    workflow = _read_tenant_workflow(cur,tenant) if rows else {}
    return [{**row,'tenant_id':str(row['tenant_id']),'workflow':workflow} for row in rows]


def expand_events() -> int:
    with _connect() as conn:
        ensure_hot_order_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT e.*,o.status,o.service_id,o.updated_at AS current_version
                FROM saas_hot_order_events e JOIN saas_hot_orders o ON o.tenant_id=e.tenant_id AND o.id=e.order_id
                WHERE e.expanded=FALSE ORDER BY e.created_at LIMIT 10 FOR UPDATE OF e SKIP LOCKED''')
            events = cur.fetchall()
            for event in events:
                tenant = str(event['tenant_id'])
                order = {'tenant_id':tenant,'status':event['status'],'service_id':event['service_id']}
                candidates = profiles(cur,tenant,after=event['recipient_cursor']) if (
                    event['status']=='open' and event['current_version']==event['order_version']) else []
                for profile in candidates:
                    user = profile['telegram_id']
                    if eligible(profile,order,'telegram'):
                        cur.execute('''INSERT INTO saas_hot_order_deliveries (tenant_id,event_id,recipient_id,channel)
                            VALUES (%s::uuid,%s::uuid,%s,'telegram') ON CONFLICT DO NOTHING''',(tenant,event['id'],user))
                    if eligible(profile,order,'push'):
                        cur.execute('''INSERT INTO saas_hot_order_deliveries
                            (tenant_id,event_id,recipient_id,channel,endpoint_hash)
                            SELECT %s::uuid,%s::uuid,b.telegram_id,'push',b.endpoint_hash
                            FROM saas_member_push_devices b JOIN saas_push_devices p USING (endpoint_hash)
                            WHERE b.tenant_id=%s::uuid AND b.telegram_id=%s AND b.active=TRUE
                              AND p.owner_telegram_id=b.telegram_id ON CONFLICT DO NOTHING''',
                            (tenant,event['id'],tenant,user))
                cursor = candidates[-1]['telegram_id'] if candidates else event['recipient_cursor']
                cur.execute('''UPDATE saas_hot_order_events SET expanded=%s,recipient_cursor=%s
                    WHERE tenant_id=%s::uuid AND id=%s::uuid''',(len(candidates)<100,cursor,tenant,event['id']))
        conn.commit()
    return len(events)


def delivery_key(item: dict) -> tuple:
    return (str(item['tenant_id']),str(item['event_id']),item['recipient_id'],item['channel'],item['endpoint_hash'])


def claim_delivery(channel: str) -> dict | None:
    if channel not in {'telegram','push'}:
        raise ValueError('Invalid channel')
    with _connect() as conn:
        ensure_hot_order_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_hot_order_deliveries SET status='unknown',updated_at=now()
                WHERE channel=%s AND status='sending' AND updated_at<now()-interval '5 minutes' ''',(channel,))
            cur.execute('''SELECT d.*,e.order_version,o.status AS order_status,o.service_id,o.updated_at AS current_version,
                p.subscription,p.owner_telegram_id,b.active AS device_active
                FROM saas_hot_order_deliveries d
                JOIN saas_hot_order_events e ON e.tenant_id=d.tenant_id AND e.id=d.event_id
                JOIN saas_hot_orders o ON o.tenant_id=e.tenant_id AND o.id=e.order_id
                LEFT JOIN saas_push_devices p ON p.endpoint_hash=d.endpoint_hash AND d.channel='push'
                LEFT JOIN saas_member_push_devices b ON b.tenant_id=d.tenant_id AND b.telegram_id=d.recipient_id
                  AND b.endpoint_hash=d.endpoint_hash AND d.channel='push'
                WHERE d.channel=%s AND d.status='pending' ORDER BY d.updated_at LIMIT 1
                FOR UPDATE OF d SKIP LOCKED''',(channel,))
            item = cur.fetchone()
            if not item:
                conn.commit()
                return None
            tenant = str(item['tenant_id'])
            order = {'tenant_id':tenant,'status':item['order_status'],'service_id':item['service_id']}
            candidates = profiles(cur,tenant,recipient=item['recipient_id'])
            allowed = bool(candidates and item['current_version']==item['order_version'] and eligible(candidates[0],order,channel))
            if channel=='push':
                allowed = bool(allowed and item['device_active'] is True and item['subscription']
                               and item['owner_telegram_id']==item['recipient_id'])
            cur.execute('''UPDATE saas_hot_order_deliveries SET status=%s,updated_at=now()
                WHERE tenant_id=%s::uuid AND event_id=%s::uuid AND recipient_id=%s AND channel=%s AND endpoint_hash=%s''',
                ('sending' if allowed else 'skipped',*delivery_key(item)))
            item['send'],item['event'],item['tenant_id'] = allowed,EVENT,tenant
        conn.commit()
    return item


def finish_delivery(item: dict, status: str, message_id: int | None = None) -> None:
    if status not in {'delivered','unknown','expired'}:
        raise ValueError('Invalid delivery result')
    with _connect() as conn:
        ensure_hot_order_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_hot_order_deliveries SET status=%s,message_id=%s,updated_at=now()
                WHERE tenant_id=%s::uuid AND event_id=%s::uuid AND recipient_id=%s AND channel=%s AND endpoint_hash=%s
                  AND status='sending' ''',(status,message_id,*delivery_key(item)))
            if status=='expired' and item['channel']=='push':
                cur.execute('''UPDATE saas_member_push_devices SET active=FALSE,updated_at=now()
                    WHERE tenant_id=%s::uuid AND telegram_id=%s AND endpoint_hash=%s''',
                    (str(item['tenant_id']),item['recipient_id'],item['endpoint_hash']))
        conn.commit()
