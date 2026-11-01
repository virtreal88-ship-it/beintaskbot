"""Claim current recipients, checkpoint before provider I/O, no resend."""
from datetime import datetime, timezone
from urllib.parse import urlencode
from tenant_platform import _connect
from tenant_crm_notice_schema import ensure_schema
from tenant_crm_notice_store import profiles, resource, connection_id, connect_dialog_route
from tenant_crm_notice_policy import eligible, task_route, timestamp
from tenant_policy import TenantPolicy


def key(item: dict) -> tuple:
    return (str(item['tenant_id']), str(item['event_id']), item['recipient_id'], item['channel'], item['endpoint_hash'])


def fresh(event: str, row: dict | None, payload: dict) -> bool:
    if not row or row.get('deleted_at'): return False
    if event.startswith('task_'):
        if row.get('completed') is not False or task_route(row) != payload.get('route'): return False
        if event == 'task_overdue':
            return (timestamp(row.get('due_at')) == timestamp(payload.get('due_at'))
                    and 0 < timestamp(row.get('due_at')) <= datetime.now(timezone.utc).timestamp())
    return True


def claim(channel: str) -> dict | None:
    if channel not in {'telegram', 'push'}: raise ValueError('Invalid channel')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_crm_notice_deliveries SET status='unknown',updated_at=now() WHERE channel=%s AND status='sending' AND updated_at<now()-interval '5 minutes'", (channel,))
            cur.execute('''SELECT d.*,e.event,e.kind,e.source_key,e.payload,p.subscription,
              p.owner_telegram_id,b.active AS device_active FROM saas_crm_notice_deliveries d
              JOIN saas_crm_notice_events e ON e.tenant_id=d.tenant_id AND e.id=d.event_id
              LEFT JOIN saas_push_devices p ON p.endpoint_hash=d.endpoint_hash AND d.channel='push'
              LEFT JOIN saas_member_push_devices b ON b.tenant_id=d.tenant_id AND b.telegram_id=d.recipient_id
                AND b.endpoint_hash=d.endpoint_hash AND d.channel='push'
              WHERE d.channel=%s AND d.status='pending' ORDER BY d.updated_at,d.event_id
              LIMIT 1 FOR UPDATE OF d SKIP LOCKED''', (channel,))
            item = cur.fetchone()
            if not item:
                conn.commit(); return None
            tenant = str(item['tenant_id'])
            member = next((m for m in profiles(cur, tenant) if m['telegram_id'] == item['recipient_id']), None)
            row = resource(cur, tenant, item['kind'], item['source_key'])
            target_url=''
            connect_route=None
            connect_route_ok=True
            push_context=None
            if item['event']=='incoming_message' and item['kind']=='message' and str((row or {}).get('channel') or '').startswith('connect:'):
                connect_route=connect_dialog_route(row or {})
                stored_route=(item.get('payload') or {}).get('connect_dialog')
                connect_route_ok=bool(connect_route and (not stored_route or stored_route==connect_route))
                if connect_route_ok:
                    cur.execute('''SELECT c.conversation_kind,c.display_name,c.manual_display_name,c.contact_name,c.phone FROM saas_connect_conversations c
                      JOIN saas_connect_channels ch ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id AND ch.active=TRUE
                      JOIN saas_tenant_integrations i ON i.tenant_id=c.tenant_id AND i.provider='connect' AND i.status='connected'
                      WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid
                        AND c.conversation_id=%s AND c.lead_id=%s''',
                      (tenant,connect_route['channel_id'],connect_route['conversation_id'],connect_route['lead_id']))
                    conversation=cur.fetchone()
                    connect_route_ok=bool(conversation)
                if connect_route_ok:
                    target_url='/app?'+urlencode({'view':'dialogs','open_dialog':'1',**connect_route})
                    # Select only the latest persisted *incoming* message from this exact
                    # tenant/channel/conversation. Outgoing echoes, internal notes and
                    # Copilot drafts never become notification content.
                    cur.execute('''SELECT author_name,body,message_type FROM saas_crm_messages
                      WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s
                        AND direction='incoming' AND raw->>'conversationId'=%s
                      ORDER BY happened_at DESC NULLS LAST,created_at DESC,id DESC LIMIT 1''',
                      (tenant,connect_route['lead_id'],'connect:'+connect_route['channel_id'],connect_route['conversation_id']))
                    latest=cur.fetchone()
                    if not latest and (row or {}).get('direction')=='incoming':
                        latest=row
                    if latest and latest.get('direction','incoming')=='incoming':
                        display_name=(conversation or {}).get('manual_display_name') or (conversation or {}).get('contact_name') or (conversation or {}).get('display_name') or ''
                        push_context={'conversation_kind':(conversation or {}).get('conversation_kind') or '',
                          'display_name':display_name,'phone':(conversation or {}).get('phone') or '',
                          'author':latest.get('author_name') or '',
                          'text':latest.get('body') or '', 'message_type':latest.get('message_type') or ''}
            allowed = bool(member and item['payload'].get('connection') == connection_id(cur, tenant)
                           and fresh(item['event'], row, item['payload']) and connect_route_ok
                           and eligible(member, item['event'], row, channel))
            if channel == 'push':
                allowed = bool(allowed and item['device_active'] is True and item['subscription'] and item['owner_telegram_id'] == item['recipient_id'])
            cur.execute('''UPDATE saas_crm_notice_deliveries SET status=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND event_id=%s::uuid AND recipient_id=%s AND channel=%s AND endpoint_hash=%s''',
              ('sending' if allowed else 'skipped', *key(item)))
            if row and member and item['event'].startswith('task_') and not TenantPolicy(member).can_access_deal(row.get('deal')):
                row = {**row, 'deal': None}  # Task-only users do not gain client contact access.
            item.update(send=allowed, resource=row, tenant_name=(member or {}).get('tenant_name', ''),target_url=target_url)
            if allowed and channel=='push' and push_context:
                item['notification_context']=push_context
        conn.commit()
    return item


def finish(item: dict, status: str, message_id: int | None = None) -> None:
    if status not in {'delivered', 'unknown', 'expired'}: raise ValueError('Invalid result')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_crm_notice_deliveries SET status=%s,message_id=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND event_id=%s::uuid AND recipient_id=%s AND channel=%s
                AND endpoint_hash=%s AND status='sending' ''', (status, message_id, *key(item)))
            if status == 'expired' and item['channel'] == 'push':
                cur.execute('''UPDATE saas_member_push_devices SET active=FALSE,updated_at=now()
                  WHERE tenant_id=%s::uuid AND telegram_id=%s AND endpoint_hash=%s''',
                  (str(item['tenant_id']), item['recipient_id'], item['endpoint_hash']))
        conn.commit()
