"""Completion, review and snapshotted rewards in one local transaction."""
import hashlib
import json
import uuid
from datetime import datetime

from tenant_platform import _connect
from tenant_hot_orders import authorize,identity,public_row,TenantHotOrderError
from tenant_hot_order_schema import ensure_hot_order_schema
from tenant_hot_order_completion_schema import enqueue_completion_notice
from tenant_hot_order_rewards import accrue_reward


def completion_command(profile: dict, data: dict) -> dict:
    policy = authorize(profile)
    action = data.get('action')
    if action not in {'complete','approve','reject'}:
        raise TenantHotOrderError('Əməliyyat düzgün deyil.')
    if action!='complete' and not policy.policy.privileged:
        raise TenantHotOrderError('Təsdiq üçün icazəniz yoxdur.',403)
    order_id,request_id = identity(data.get('order_id')),identity(data.get('request_id'))
    text = data.get('result_text' if action=='complete' else 'reason','')
    if not isinstance(text,str) or len(text.strip())>8000 or (action in {'complete','reject'} and not text.strip()):
        raise TenantHotOrderError('Nəticə və ya imtina səbəbini yazın.')
    text = text.strip()
    try:
        version = datetime.fromisoformat(str(data.get('expected_updated_at') or ''))
        if version.tzinfo is None:
            raise ValueError()
    except ValueError:
        raise TenantHotOrderError('Sifarişin versiyası düzgün deyil.') from None
    payload = {'order_id':order_id,'action':action,'text':text,'version':version.isoformat()}
    fingerprint = hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    tenant,user = str(profile['tenant_id']),int(profile['telegram_id'])
    with _connect() as conn:
        ensure_hot_order_schema(conn)
        with conn.cursor() as cur:
            # Actor/request lock prevents reuse across two different orders.
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('hot-command:'+tenant+':'+str(user)+':'+request_id,))
            cur.execute('SELECT * FROM saas_hot_orders WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,order_id))
            row = cur.fetchone()
            if not row or not policy.can_view(row):
                raise TenantHotOrderError('Sifariş tapılmadı.',404)
            if action=='complete' and row['claimed_by']!=user:
                raise TenantHotOrderError('Yalnız sifarişi qəbul edən əməkdaş tamamlaya bilər.',403)
            cur.execute('''SELECT fingerprint,result FROM saas_hot_order_commands
                WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid''',(tenant,user,request_id))
            previous = cur.fetchone()
            if previous:
                if previous['fingerprint']!=fingerprint:
                    raise TenantHotOrderError('Bu sorğu ID-si başqa əməliyyat üçün istifadə edilib.',409)
                return previous['result']
            current = row['updated_at']
            if isinstance(current,str):
                current = datetime.fromisoformat(current)
            allowed = policy.can_complete(row) if action=='complete' else policy.can_review(row)
            if not allowed or current!=version:
                raise TenantHotOrderError('Sifariş dəyişib. Siyahını yeniləyin.',409)
            target = ('submitted' if policy.requires_completion_approval() else 'completed') if action=='complete' else ('completed' if action=='approve' else 'claimed')
            cur.execute('''UPDATE saas_hot_orders SET status=%s,result_text=%s,review_reason=%s,
                completed_at=CASE WHEN %s='completed' THEN now() ELSE NULL END,updated_at=now()
                WHERE tenant_id=%s::uuid AND id=%s::uuid AND status=%s RETURNING *''',
                (target,text if action=='complete' else row['result_text'],text if action=='reject' else '',target,tenant,order_id,row['status']))
            changed = cur.fetchone()
            if not changed:
                raise TenantHotOrderError('Sifariş artıq dəyişib.',409)
            reward_entry = accrue_reward(cur, profile, changed)
            if reward_entry:
                changed['reward_entry_id'] = reward_entry
            result = public_row(changed,policy)
            cur.execute('''INSERT INTO saas_hot_order_commands (tenant_id,actor_id,request_id,order_id,fingerprint,result)
                VALUES (%s::uuid,%s,%s::uuid,%s::uuid,%s,%s::jsonb)''',(tenant,user,request_id,order_id,fingerprint,json.dumps(result)))
            event_id = str(uuid.uuid4())
            cur.execute('''INSERT INTO saas_tenant_audit_events (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                VALUES (%s::uuid,%s::uuid,%s,%s,'hot_order',%s,%s::jsonb)''',
                (event_id,tenant,user,'hot_order_'+action,order_id,json.dumps({'result':text,'status':target})))
            event = 'hot_order_completion_requested' if target=='submitted' else 'hot_order_completion_decided'
            enqueue_completion_notice(cur,tenant,order_id,event_id,event,None if target=='submitted' else int(row['claimed_by']))
        conn.commit()
    return result
