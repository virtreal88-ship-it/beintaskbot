"""Current tenant/resource authorization before each reserved notice attempt."""
from tenant_platform import _connect
from tenant_deal_completion_store import ensure_schema
from tenant_notification_outbox import _profiles, eligible
from tenant_policy import TenantPolicy

EVENT = 'deal_completion_requested'


def allowed(profile, deal, state, tenant, channel):
    return bool(profile and deal and not deal.get('deleted_at')
                and state.get('step') == 'waiting_approval'
                and TenantPolicy(profile).privileged
                and TenantPolicy(profile).can_access_deal(deal)
                and eligible(profile, EVENT, tenant, channel))


def expand():
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT e.*,c.lead_id,c.state FROM saas_deal_notice_events e
              JOIN saas_deal_completion_commands c USING(tenant_id,actor_id,request_id)
              WHERE e.expanded=FALSE ORDER BY e.created_at LIMIT 10 FOR UPDATE OF e SKIP LOCKED''')
            events = cur.fetchall()
            for item in events:
                tenant = str(item['tenant_id'])
                key = (tenant, item['actor_id'], str(item['request_id']))
                cur.execute('SELECT * FROM saas_crm_deals WHERE tenant_id=%s::uuid AND kommo_lead_id=%s', (tenant, item['lead_id']))
                deal = cur.fetchone()
                for profile in _profiles(cur, tenant):
                    for channel in ('telegram', 'push'):
                        if not allowed(profile, deal, item['state'], tenant, channel):
                            continue
                        if channel == 'telegram':
                            cur.execute('''INSERT INTO saas_deal_notice_deliveries
                              (tenant_id,actor_id,request_id,recipient_id,channel)
                              VALUES(%s::uuid,%s,%s::uuid,%s,'telegram') ON CONFLICT DO NOTHING''',
                                        (*key, profile['telegram_id']))
                        else:
                            cur.execute('''INSERT INTO saas_deal_notice_deliveries
                              (tenant_id,actor_id,request_id,recipient_id,channel,endpoint_hash)
                              SELECT %s::uuid,%s,%s::uuid,b.telegram_id,'push',b.endpoint_hash
                              FROM saas_member_push_devices b JOIN saas_push_devices p USING(endpoint_hash)
                              WHERE b.tenant_id=%s::uuid AND b.telegram_id=%s AND b.active=TRUE
                              AND p.owner_telegram_id=b.telegram_id ON CONFLICT DO NOTHING''',
                                        (*key, tenant, profile['telegram_id']))
                cur.execute('''UPDATE saas_deal_notice_events SET expanded=TRUE
                  WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid''', key)
        conn.commit()
    return len(events)


def delivery_key(item):
    return (str(item['tenant_id']), item['actor_id'], str(item['request_id']),
            item['recipient_id'], item['channel'], item['endpoint_hash'])


def claim(channel):
    if channel not in {'telegram', 'push'}:
        raise ValueError('Invalid channel')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_deal_notice_deliveries SET status='unknown',updated_at=now() WHERE status='sending' AND updated_at<now()-interval '5 minutes'")
            cur.execute('''SELECT n.*,c.lead_id,c.state,m.display_name AS creator_name,
              p.subscription,p.owner_telegram_id,b.active AS device_active
              FROM saas_deal_notice_deliveries n
              JOIN saas_deal_completion_commands c USING(tenant_id,actor_id,request_id)
              LEFT JOIN saas_tenant_members m ON m.tenant_id=n.tenant_id AND m.telegram_id=n.actor_id
              LEFT JOIN saas_push_devices p ON p.endpoint_hash=n.endpoint_hash AND n.channel='push'
              LEFT JOIN saas_member_push_devices b ON b.tenant_id=n.tenant_id AND b.telegram_id=n.recipient_id
                AND b.endpoint_hash=n.endpoint_hash AND n.channel='push'
              WHERE n.channel=%s AND n.status='pending' ORDER BY n.updated_at
              LIMIT 1 FOR UPDATE OF n SKIP LOCKED''', (channel,))
            item = cur.fetchone()
            if not item:
                conn.commit()
                return None
            tenant = str(item['tenant_id'])
            profiles = _profiles(cur, tenant, item['recipient_id'])
            profile = profiles[0] if profiles else None
            cur.execute('SELECT * FROM saas_crm_deals WHERE tenant_id=%s::uuid AND kommo_lead_id=%s', (tenant, item['lead_id']))
            deal = cur.fetchone()
            send = allowed(profile, deal, item['state'], tenant, channel)
            if channel == 'push':
                send = bool(send and item['device_active'] is True and item['subscription']
                            and item['owner_telegram_id'] == item['recipient_id'])
            cur.execute('''UPDATE saas_deal_notice_deliveries SET status=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid
              AND recipient_id=%s AND channel=%s AND endpoint_hash=%s''',
                        ('sending' if send else 'skipped', *delivery_key(item)))
            item.update(send=send, tenant_id=tenant, event=EVENT, deal=deal,
                        tenant_name=(profile or {}).get('tenant_name', ''))
        conn.commit()
    return item


def finish(item, status, message_id=None):
    if status not in {'delivered', 'unknown', 'expired'}:
        raise ValueError('Invalid result')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_deal_notice_deliveries SET status=%s,message_id=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid AND recipient_id=%s
              AND channel=%s AND endpoint_hash=%s AND status='sending' ''', (status, message_id, *delivery_key(item)))
            if status == 'expired' and item['channel'] == 'push':
                cur.execute('''UPDATE saas_member_push_devices SET active=FALSE,updated_at=now()
                  WHERE tenant_id=%s::uuid AND telegram_id=%s AND endpoint_hash=%s''',
                            (str(item['tenant_id']), item['recipient_id'], item['endpoint_hash']))
        conn.commit()
