"""Bounded round-robin observations; plan/observe in one DB transaction."""
import json
import uuid
from datetime import datetime, timezone
from tenant_platform import _connect, _read_tenant_workflow
from tenant_crm_notice_schema import ensure_schema
from tenant_crm_notice_policy import eligible, transitions, task_route

RESOURCES = {
    'lead': ('saas_crm_deals', 'kommo_lead_id'),
    'task': ('saas_crm_tasks', 'kommo_task_id'),
    'message': ('saas_crm_messages', 'external_id'),
}


def profiles(cur, tenant: str) -> list[dict]:
    cur.execute('''SELECT m.*,t.name AS tenant_name,t.status AS tenant_status,t.modules,t.notification_rules,t.onboarding
      FROM saas_tenant_members m JOIN saas_tenants t ON t.id=m.tenant_id
      WHERE m.tenant_id=%s::uuid AND m.active=TRUE''', (tenant,))
    rows = cur.fetchall()
    workflow = _read_tenant_workflow(cur, tenant)
    return [{**row, 'tenant_id': tenant, 'workflow': workflow} for row in rows]


def connection_id(cur, tenant: str) -> str:
    cur.execute("SELECT account_domain,connected_at FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='kommo' AND status='connected'", (tenant,))
    row = cur.fetchone()
    return str(row['account_domain']) + ':' + str(row['connected_at']) if row and row.get('account_domain') else ''


def resource(cur, tenant: str, kind: str, key: str) -> dict | None:
    table, field = RESOURCES[kind]  # fixed server constants, never user SQL
    cur.execute(f'SELECT * FROM {table} WHERE tenant_id=%s::uuid AND {field}::text=%s', (tenant, key))
    row = cur.fetchone()
    return hydrate(cur, tenant, kind, row) if row else None


def hydrate(cur, tenant: str, kind: str, row: dict) -> dict:
    row = {**row, 'tenant_id': tenant}
    if kind in {'task', 'message'} and row.get('kommo_lead_id'):
        cur.execute('SELECT * FROM saas_crm_deals WHERE tenant_id=%s::uuid AND kommo_lead_id=%s',
                    (tenant, row['kommo_lead_id']))
        row['deal'] = cur.fetchone()
    return row


def plan(cur, tenant: str, kind: str, key: str, row: dict, event: str, version: str, members: list[dict], connection: str) -> None:
    # Freeze permitted recipients now, then recheck at claim. No catch-up on opt-in.
    event_id = str(uuid.uuid4())
    cur.execute('''INSERT INTO saas_crm_notice_events(id,tenant_id,event,kind,source_key,version,payload)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s::jsonb)
      ON CONFLICT(tenant_id,event,source_key,version) DO NOTHING RETURNING id''',
      (event_id, tenant, event, kind, key, connection + ':' + version, json.dumps(
          {'connection': connection, 'route': task_route(row), 'due_at': row.get('due_at')}, default=str)))
    if not cur.fetchone(): return
    for member in members:
        recipient = member['telegram_id']
        if eligible(member, event, row, 'telegram'):
            cur.execute('''INSERT INTO saas_crm_notice_deliveries(tenant_id,event_id,recipient_id,channel)
              VALUES(%s::uuid,%s::uuid,%s,'telegram') ON CONFLICT DO NOTHING''', (tenant, event_id, recipient))
        if eligible(member, event, row, 'push'):
            cur.execute('''INSERT INTO saas_crm_notice_deliveries(tenant_id,event_id,recipient_id,channel,endpoint_hash)
              SELECT %s::uuid,%s::uuid,b.telegram_id,'push',b.endpoint_hash
              FROM saas_member_push_devices b JOIN saas_push_devices p USING(endpoint_hash)
              WHERE b.tenant_id=%s::uuid AND b.telegram_id=%s AND b.active=TRUE
                AND p.owner_telegram_id=b.telegram_id ON CONFLICT DO NOTHING''', (tenant, event_id, tenant, recipient))


def observe() -> int:
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO saas_crm_notice_scan(tenant_id) SELECT id FROM saas_tenants WHERE status='active' ON CONFLICT DO NOTHING")
            cur.execute('''SELECT s.* FROM saas_crm_notice_scan s JOIN saas_tenants t ON t.id=s.tenant_id
              WHERE t.status='active' AND s.next_scan<=now()
                AND EXISTS(SELECT 1 FROM saas_tenant_integrations i WHERE i.tenant_id=s.tenant_id AND i.provider='kommo' AND i.status='connected')
              ORDER BY s.next_scan,s.tenant_id
              LIMIT 1 FOR UPDATE OF s SKIP LOCKED''')
            scan = cur.fetchone()
            if not scan:
                conn.commit(); return 0
            tenant, positions = str(scan['tenant_id']), dict(scan['positions'])
            connection = connection_id(cur, tenant)
            if connection != scan['connection_id']:
                positions = {}
                scan['baseline'] = datetime.now(timezone.utc)
                cur.execute('DELETE FROM saas_crm_notice_observations WHERE tenant_id=%s::uuid', (tenant,))
                cur.execute('UPDATE saas_crm_notice_scan SET baseline=%s,connection_id=%s WHERE tenant_id=%s::uuid', (scan['baseline'], connection, tenant))
            members = profiles(cur, tenant)
            count = 0
            for kind, (table, field) in RESOURCES.items():
                cur.execute(f'''SELECT *,{field}::text AS source_key FROM {table}
                  WHERE tenant_id=%s::uuid AND {field}::text>%s ORDER BY {field}::text LIMIT 50''',
                  (tenant, positions.get(kind, '')))
                rows = cur.fetchall()
                cur.execute('''SELECT source_key,state FROM saas_crm_notice_observations
                  WHERE tenant_id=%s::uuid AND kind=%s AND source_key=ANY(%s)''', (tenant, kind, [row['source_key'] for row in rows]))
                previous = {row['source_key']: row['state'] for row in cur.fetchall()}
                leads = {}
                if kind != 'lead':
                    cur.execute('SELECT * FROM saas_crm_deals WHERE tenant_id=%s::uuid AND kommo_lead_id=ANY(%s)', (tenant, [row['kommo_lead_id'] for row in rows if row.get('kommo_lead_id')]))
                    leads = {row['kommo_lead_id']: row for row in cur.fetchall()}
                observations = []
                for row in rows:
                    key = row['source_key']
                    row = {**row, 'tenant_id': tenant}
                    if kind != 'lead': row['deal'] = leads.get(row.get('kommo_lead_id'))
                    state, events = transitions(kind, row, previous.get(key), scan['baseline'], datetime.now(timezone.utc))
                    for event, stamp in events:
                        plan(cur, tenant, kind, key, row, event, stamp, members, connection)
                    observations.append((tenant, kind, key, json.dumps(state)))
                    count += 1
                if observations:
                    cur.executemany('''INSERT INTO saas_crm_notice_observations(tenant_id,kind,source_key,state)
                      VALUES(%s::uuid,%s,%s,%s::jsonb) ON CONFLICT(tenant_id,kind,source_key) DO UPDATE SET state=EXCLUDED.state''', observations)
                positions[kind] = rows[-1]['source_key'] if len(rows) == 50 else ''
            cur.execute("UPDATE saas_crm_notice_scan SET positions=%s::jsonb,next_scan=now()+interval '1 minute' WHERE tenant_id=%s::uuid", (json.dumps(positions), tenant))
        conn.commit()
    return count
