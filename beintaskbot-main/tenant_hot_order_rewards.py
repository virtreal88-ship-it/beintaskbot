"""Accrual inside the completion transaction. No own connection or commit."""
import hashlib
import json
import uuid
from decimal import Decimal


def accrue_reward(cur, profile: dict, order: dict) -> str | None:
    amount = int(order.get('reward_minor') or 0)
    if order.get('status') != 'completed' or amount == 0:
        return None
    tenant = str(profile['tenant_id'])
    if str(order.get('tenant_id')) != tenant or not order.get('claimed_by') or not 0 < amount <= 99999999999:
        raise ValueError('Reward identity/amount mismatch')
    member, actor, order_id = int(order['claimed_by']), int(profile['telegram_id']), str(order['id'])
    # Shared with manual finance commands, so one employee account serializes all writes.
    cur.execute('SELECT active FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s FOR UPDATE', (tenant,member))
    if not cur.fetchone():
        raise ValueError('Reward membership missing')
    # Inactive workers still receive money already earned; their access remains disabled.
    cur.execute('SELECT id FROM saas_finance_entries WHERE tenant_id=%s::uuid AND hot_order_id=%s::uuid', (tenant,order_id))
    if cur.fetchone():
        raise ValueError('Reward already accrued')
    entry = str(uuid.uuid4())
    request = str(uuid.uuid5(uuid.NAMESPACE_URL, 'crm-hot-reward:'+tenant+':'+order_id))
    stamp = hashlib.sha256(json.dumps({'tenant':tenant,'order':order_id,'member':member,'amount':amount},sort_keys=True).encode()).hexdigest()
    cur.execute('''INSERT INTO saas_finance_entries
        (tenant_id,id,member_id,actor_id,request_id,fingerprint,kind,amount_minor,note,hot_order_id)
        VALUES (%s::uuid,%s::uuid,%s,%s,%s::uuid,%s,'credit',%s,%s,%s::uuid)''',
        (tenant,entry,member,actor,request,stamp,amount,'İsti sifariş üzrə xidmət haqqı · '+order_id,order_id))
    cur.execute('''UPDATE saas_hot_orders SET reward_entry_id=%s::uuid
        WHERE tenant_id=%s::uuid AND id=%s::uuid''', (entry,tenant,order_id))
    cur.execute('''INSERT INTO saas_tenant_audit_events
        (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
        VALUES (%s::uuid,%s::uuid,%s,'finance_hot_order_reward','finance_entry',%s,%s::jsonb)''',
        (str(uuid.uuid4()),tenant,actor,entry,json.dumps({'member_id':member,'hot_order_id':order_id,
            'amount':format(Decimal(amount)/100,'.2f')})))
    return entry
