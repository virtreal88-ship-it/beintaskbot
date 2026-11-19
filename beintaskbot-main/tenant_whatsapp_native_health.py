"""Read-only native connection sampling feeding the existing Telegram queue.

Only the current enabled source/generation may contribute. No device reconnect,
provider sends or QR generation. Transport errors are unknown, not logout proof.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from tenant_platform import _connect
from tenant_call_sources import remote


def authority(cur, tenant, channel):
    cur.execute("SELECT to_regclass('saas_whatsapp_native_routes') IS NOT NULL AS ready")
    if not cur.fetchone()['ready']:
        return {'provider':'connect'}
    cur.execute('''SELECT r.generation,r.legacy_fallback,r.enabled,s.gateway_id,s.account_jid,
        s.enabled AS gateway_enabled,g.enabled AS capture_enabled
      FROM saas_whatsapp_native_routes r
      LEFT JOIN saas_whatsapp_capture_generations g USING(tenant_id,channel_id,generation)
      LEFT JOIN saas_browser_call_sources s ON s.tenant_id=r.tenant_id AND s.channel_id=r.channel_id
        AND s.gateway_id=g.gateway_id AND s.account_jid=g.account_jid
      WHERE r.tenant_id=%s::uuid AND r.channel_id=%s::uuid''',(tenant,channel))
    row=cur.fetchone()
    if not row or row['legacy_fallback']:
        return {'provider':'connect'}
    return {'provider':'native','generation':row['generation'],'gateway':str(row['gateway_id']),
            'account':row['account_jid'],
            'enabled':bool(row['enabled'] and row['gateway_enabled'] and row['capture_enabled'])}


def current(cur, tenant, channel, state):
    origin=authority(cur,tenant,channel)
    if state.get('transport',{'provider':'connect'}) != origin:
        return False
    if origin['provider']=='native':
        from tenant_connect_health import timestamp
        try:
            return origin['enabled'] and datetime.now(timezone.utc)-timedelta(seconds=150) <= timestamp(state.get('nativeCheckedAt'))
        except (ValueError,TypeError):
            return False
    return True


def reserve():
    from tenant_connect_health import allowed_tenants
    tenants=allowed_tenants()
    if not tenants:return []
    with _connect() as conn,conn.cursor() as cur:
        cur.execute("SELECT to_regclass('saas_whatsapp_native_routes') IS NOT NULL AS ready")
        if not cur.fetchone()['ready']:return []
        cur.execute('''SELECT c.tenant_id,c.connect_channel_id FROM saas_connect_channels c
          JOIN saas_whatsapp_native_routes r ON r.tenant_id=c.tenant_id AND r.channel_id=c.connect_channel_id
          LEFT JOIN saas_connect_health h ON h.tenant_id=c.tenant_id AND h.channel_id=c.connect_channel_id
          WHERE c.active AND r.enabled AND NOT r.legacy_fallback AND c.tenant_id=ANY(%s::uuid[])
            AND COALESCE((h.state->>'nativeCheckedAt')::timestamptz,'epoch') < now()-interval '45 seconds'
            AND COALESCE((h.state->>'nativePollStartedAt')::timestamptz,'epoch') < now()-interval '2 minutes'
          ORDER BY COALESCE(h.updated_at,'epoch') LIMIT 8 FOR UPDATE OF c SKIP LOCKED''',(list(tenants),))
        rows=cur.fetchall();jobs=[]
        for row in rows:
            tenant,channel=str(row['tenant_id']),str(row['connect_channel_id'])
            origin=authority(cur,tenant,channel)
            if origin['provider']!='native' or not origin['enabled']:continue
            token=str(uuid4());started=datetime.now(timezone.utc).isoformat()
            cur.execute('''INSERT INTO saas_connect_health(tenant_id,channel_id) VALUES(%s,%s)
              ON CONFLICT DO NOTHING''',(tenant,channel))
            cur.execute('''UPDATE saas_connect_health SET state=state||%s::jsonb,updated_at=now()
              WHERE tenant_id=%s AND channel_id=%s''',
              (json.dumps({'nativePollToken':token,'nativePollStartedAt':started}),tenant,channel))
            jobs.append((tenant,channel,origin,token,started))
    return jobs


def observation(data, origin):
    if not isinstance(data,dict) or data.get('messaging_generation')!=origin['generation']:
        return 'unknown','unconfirmed'
    if data.get('connected') is True and data.get('account_jid')==origin['account']:
        return 'connected',''
    if data.get('connected') is not False or data.get('account_jid') not in ('',origin['account']):
        return 'unknown','unconfirmed'
    reason='pairing_required' if data.get('connection_state')=='logged_out' else ''
    return 'disconnected',reason


def apply(job, data):
    from tenant_connect_health import record
    tenant,channel,origin,token,started=job
    with _connect() as conn,conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended(%s,0))',
                    ('whatsapp-source-mode:'+tenant+':'+channel,))
        if authority(cur,tenant,channel)!=origin:return False
        cur.execute('SELECT active FROM saas_connect_channels WHERE tenant_id=%s AND connect_channel_id=%s FOR SHARE',(tenant,channel))
        row=cur.fetchone()
        if not row or not row['active']:return False
        cur.execute('SELECT state FROM saas_connect_health WHERE tenant_id=%s AND channel_id=%s FOR UPDATE',(tenant,channel))
        row=cur.fetchone()
        if not row or row['state'].get('nativePollToken')!=token:return False
        status,reason=observation(data,origin)
        now=datetime.now(timezone.utc)
        record(cur,tenant,channel,{'status':status,'observedAt':started},now,origin=origin)
        cur.execute('''UPDATE saas_connect_health SET state=(state-'nativePollStartedAt'-'nativePollToken')||%s::jsonb
          WHERE tenant_id=%s AND channel_id=%s''',
          (json.dumps({'nativeCheckedAt':now.isoformat(),'reason':reason}),tenant,channel))
    return True


def sample(job):
    try:data=remote('/v1/sources/'+job[2]['gateway'])
    except Exception:data=None  # No exception text, identities, QR or credentials in logs.
    return apply(job,data)


def collect():
    jobs=reserve()
    if jobs:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(sample,jobs))

