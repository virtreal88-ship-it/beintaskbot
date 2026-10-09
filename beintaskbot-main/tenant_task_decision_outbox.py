"""Freeze recipients once; recheck own command and device rights before I/O."""
from tenant_platform import _connect, _read_tenant_workflow
from tenant_task_decision_schema import ensure_schema
from tenant_task_decision_policy import eligible, EVENT


def profile(cur, tenant: str, actor: int) -> dict | None:
    cur.execute('''SELECT m.*,t.name AS tenant_name,t.status AS tenant_status,t.modules,t.notification_rules
      FROM saas_tenant_members m JOIN saas_tenants t ON t.id=m.tenant_id
      WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s AND m.active=TRUE''', (tenant,actor))
    row = cur.fetchone()
    return {**row,'tenant_id':tenant,'workflow':_read_tenant_workflow(cur,tenant)} if row else None


def key(item: dict) -> tuple:
    return (str(item['tenant_id']),item['actor_id'],str(item['request_id']),item['recipient_id'],item['channel'],item['endpoint_hash'])


def expand_events() -> int:
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT e.*,c.state FROM saas_task_decision_events e
              JOIN saas_task_commands c USING(tenant_id,actor_id,request_id)
              WHERE e.expanded=FALSE ORDER BY e.created_at LIMIT 10 FOR UPDATE OF e SKIP LOCKED''')
            events = cur.fetchall()
            for item in events:
                tenant = str(item['tenant_id']); member = profile(cur,tenant,item['actor_id'])
                event_key = (tenant,item['actor_id'],str(item['request_id']))
                if member and eligible(member,item,'telegram'):
                    cur.execute('''INSERT INTO saas_task_decision_deliveries(tenant_id,actor_id,request_id,recipient_id,channel)
                      VALUES(%s::uuid,%s,%s::uuid,%s,'telegram') ON CONFLICT DO NOTHING''', (*event_key,item['actor_id']))
                if member and eligible(member,item,'push'):
                    cur.execute('''INSERT INTO saas_task_decision_deliveries(tenant_id,actor_id,request_id,recipient_id,channel,endpoint_hash)
                      SELECT %s::uuid,%s,%s::uuid,b.telegram_id,'push',b.endpoint_hash
                      FROM saas_member_push_devices b JOIN saas_push_devices p USING(endpoint_hash)
                      WHERE b.tenant_id=%s::uuid AND b.telegram_id=%s AND b.active=TRUE
                        AND p.owner_telegram_id=b.telegram_id ON CONFLICT DO NOTHING''', (*event_key,tenant,item['actor_id']))
                cur.execute('''UPDATE saas_task_decision_events SET expanded=TRUE
                  WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid''', event_key)
        conn.commit()
    return len(events)


def claim_delivery(channel: str) -> dict | None:
    if channel not in {'telegram','push'}:
        raise ValueError('Invalid channel')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_task_decision_deliveries SET status='unknown',updated_at=now() WHERE channel=%s AND status='sending' AND updated_at<now()-interval '5 minutes'", (channel,))
            cur.execute('''SELECT d.*,e.outcome,c.state,p.subscription,p.owner_telegram_id,b.active AS device_active
              FROM saas_task_decision_deliveries d JOIN saas_task_decision_events e USING(tenant_id,actor_id,request_id)
              JOIN saas_task_commands c USING(tenant_id,actor_id,request_id)
              LEFT JOIN saas_push_devices p ON p.endpoint_hash=d.endpoint_hash AND d.channel='push'
              LEFT JOIN saas_member_push_devices b ON b.tenant_id=d.tenant_id AND b.telegram_id=d.recipient_id
                AND b.endpoint_hash=d.endpoint_hash AND d.channel='push'
              WHERE d.channel=%s AND d.status='pending' ORDER BY d.updated_at LIMIT 1 FOR UPDATE OF d SKIP LOCKED''', (channel,))
            item = cur.fetchone()
            if not item:
                conn.commit(); return None
            item['tenant_id'] = str(item['tenant_id']); member = profile(cur,item['tenant_id'],item['recipient_id'])
            allowed = bool(member and item['recipient_id']==item['actor_id'] and eligible(member,item,channel))
            if channel=='push':
                allowed = bool(allowed and item['device_active'] is True and item['subscription'] and item['owner_telegram_id']==item['recipient_id'])
            cur.execute('''UPDATE saas_task_decision_deliveries SET status=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid AND recipient_id=%s AND channel=%s AND endpoint_hash=%s''',
              ('sending' if allowed else 'skipped',*key(item)))
            item.update(send=allowed,event=EVENT,tenant_name=member.get('tenant_name','') if member else '')
        conn.commit()
    return item


def finish_delivery(item: dict, status: str, message_id: int | None = None) -> None:
    if status not in {'delivered','unknown','expired'}:
        raise ValueError('Invalid result')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_task_decision_deliveries SET status=%s,message_id=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid AND recipient_id=%s AND channel=%s AND endpoint_hash=%s AND status='sending' ''',
              (status,message_id,*key(item)))
            if status=='expired' and item['channel']=='push':
                cur.execute('''UPDATE saas_member_push_devices SET active=FALSE,updated_at=now()
                  WHERE tenant_id=%s::uuid AND telegram_id=%s AND endpoint_hash=%s''', (str(item['tenant_id']),item['recipient_id'],item['endpoint_hash']))
        conn.commit()
