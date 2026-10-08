"""Freeze opt-in recipients, then recheck membership, live scope and device binding."""
from tenant_platform import _connect, _read_tenant_workflow
from tenant_linear_observer_schema import ensure_observer_schema
from tenant_linear_context import context
from tenant_linear_tasks_provider import issue
from tenant_linear_runtime_policy import visible
from tenant_policy import TenantPolicy
from tenant_notifications import notification_channels
from tenant_linear_policy import TenantLinearError
from tenant_linear_observer_policy import source_time


def profiles(cur, tenant: str, after: int = 0, recipient: int | None = None) -> list[dict]:
    cur.execute('''SELECT m.*,t.status AS tenant_status,t.modules,t.notification_rules
      FROM saas_tenant_members m JOIN saas_tenants t ON t.id=m.tenant_id
      WHERE m.tenant_id=%s::uuid AND m.active=TRUE AND m.telegram_id>%s
        AND (%s::bigint IS NULL OR m.telegram_id=%s::bigint) ORDER BY m.telegram_id LIMIT 100''', (tenant, after, recipient, recipient))
    rows = cur.fetchall(); workflow = _read_tenant_workflow(cur, tenant) if rows else {}
    return [{**p, 'tenant_id': tenant, 'workflow': workflow} for p in rows]


def eligible(profile: dict, config: dict, row: dict, event: str, channel: str) -> bool:
    return (profile.get('active') is True and profile.get('tenant_status') == 'active'
        and config.get('workflow', {}).get('enabled') is True and config.get('workflow', {}).get('sync_enabled') is True
        and (row.get('state') or {}).get('id') in config.get('workflow', {}).get('notification_state_ids', [])
        and visible(profile, config, row)
        and channel in notification_channels(TenantPolicy(profile), event=event, tenant_id=profile['tenant_id']))


def expand_events() -> int:
    with _connect() as conn:
        ensure_observer_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT e.*,s.snapshot,s.source_version AS current_version,i.metadata,i.status AS connection_status
              FROM saas_linear_notice_events e JOIN saas_linear_snapshots s USING(tenant_id,issue_id)
              JOIN saas_tenant_integrations i ON i.tenant_id=e.tenant_id AND i.provider='linear'
              WHERE e.expanded=FALSE ORDER BY e.created_at LIMIT 5 FOR UPDATE OF e SKIP LOCKED''')
            events = cur.fetchall()
            for event in events:
                tenant = str(event['tenant_id']); config = event['metadata'].get('settings', {})
                candidates = profiles(cur, tenant, event['recipient_cursor']) if event['connection_status'] == 'connected' and event['current_version'] == event['source_version'] else []
                for person in candidates:
                    for channel in ('telegram', 'push'):
                        if not eligible(person, config, event['snapshot'], event['event'], channel):
                            continue
                        args = (tenant, event['id'], person['telegram_id'])
                        if channel == 'telegram':
                            cur.execute('''INSERT INTO saas_linear_notice_deliveries(tenant_id,event_id,recipient_id,channel)
                              VALUES(%s::uuid,%s::uuid,%s,'telegram') ON CONFLICT DO NOTHING''', args)
                        else:
                            cur.execute('''INSERT INTO saas_linear_notice_deliveries(tenant_id,event_id,recipient_id,channel,endpoint_hash)
                              SELECT %s::uuid,%s::uuid,%s,'push',b.endpoint_hash FROM saas_member_push_devices b
                              JOIN saas_push_devices p USING(endpoint_hash) WHERE b.tenant_id=%s::uuid AND b.telegram_id=%s
                              AND b.active=TRUE AND p.owner_telegram_id=b.telegram_id ON CONFLICT DO NOTHING''', (*args, tenant, person['telegram_id']))
                cur.execute('''UPDATE saas_linear_notice_events SET recipient_cursor=%s,expanded=%s
                  WHERE tenant_id=%s::uuid AND id=%s::uuid''',
                  (candidates[-1]['telegram_id'] if candidates else event['recipient_cursor'], len(candidates) < 100, tenant, event['id']))
        conn.commit()
    return len(events)


def delivery_key(item: dict) -> tuple:
    return (str(item['tenant_id']), str(item['event_id']), item['recipient_id'], item['channel'], item['endpoint_hash'])


def claim_delivery(channel: str) -> dict | None:
    if channel not in {'telegram', 'push'}:
        raise ValueError('Invalid channel')
    with _connect() as conn:
        ensure_observer_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_linear_notice_deliveries SET status='unknown',updated_at=now() WHERE status='sending' AND updated_at<now()-interval '5 minutes'")
            cur.execute('''SELECT d.*,e.issue_id,e.event,e.source_version,s.source_version AS current_version,
              p.subscription,p.owner_telegram_id,b.active AS device_active
              FROM saas_linear_notice_deliveries d JOIN saas_linear_notice_events e ON e.tenant_id=d.tenant_id AND e.id=d.event_id
              JOIN saas_linear_snapshots s ON s.tenant_id=e.tenant_id AND s.issue_id=e.issue_id
              LEFT JOIN saas_push_devices p ON p.endpoint_hash=d.endpoint_hash AND d.channel='push'
              LEFT JOIN saas_member_push_devices b ON b.tenant_id=d.tenant_id AND b.telegram_id=d.recipient_id AND b.endpoint_hash=d.endpoint_hash
              WHERE d.channel=%s AND d.status='pending' ORDER BY d.updated_at LIMIT 1 FOR UPDATE OF d SKIP LOCKED''', (channel,))
            item = cur.fetchone()
            if not item:
                return None
            allowed = False
            if item['source_version'] == item['current_version']:
                try:
                    profile, config, key, _ = context(cur, {'tenant_id': str(item['tenant_id']), 'telegram_id': item['recipient_id']})
                    # context omits notification_rules; the live workflow retains event/channel preferences.
                    cur.execute('SELECT notification_rules FROM saas_tenants WHERE id=%s::uuid', (str(item['tenant_id']),))
                    profile['notification_rules'] = cur.fetchone()['notification_rules']
                    row = issue(key, str(item['issue_id']))
                    allowed = bool(row and source_time(row['updatedAt']) == item['source_version'] and eligible(profile, config, row, item['event'], channel))
                except TenantLinearError as error:
                    if error.status in {502, 503}:
                        raise
            if channel == 'push':
                allowed = bool(allowed and item['device_active'] is True and item['subscription'] and item['owner_telegram_id'] == item['recipient_id'])
            cur.execute('''UPDATE saas_linear_notice_deliveries SET status=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND event_id=%s::uuid AND recipient_id=%s AND channel=%s AND endpoint_hash=%s''',
              ('sending' if allowed else 'skipped', *delivery_key(item)))
            item['send'] = allowed; item['tenant_id'] = str(item['tenant_id'])
        conn.commit()
    return item


def finish_delivery(item: dict, status: str, message_id: int | None = None) -> None:
    if status not in {'delivered', 'unknown', 'expired'}:
        raise ValueError('Invalid result')
    with _connect() as conn:
        ensure_observer_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_linear_notice_deliveries SET status=%s,message_id=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND event_id=%s::uuid AND recipient_id=%s AND channel=%s AND endpoint_hash=%s AND status='sending' ''',
              (status, message_id, *delivery_key(item)))
            if status == 'expired' and item['channel'] == 'push':
                cur.execute('''UPDATE saas_member_push_devices SET active=FALSE,updated_at=now()
                  WHERE tenant_id=%s::uuid AND telegram_id=%s AND endpoint_hash=%s''', (item['tenant_id'], item['recipient_id'], item['endpoint_hash']))
        conn.commit()
