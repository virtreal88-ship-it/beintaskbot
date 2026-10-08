"""Bounded tenant queue operations with transactional authorization and audit."""
import hashlib
import json
import uuid

from tenant_platform import _connect
from tenant_policy import TenantPolicy
from tenant_hot_order_policy import HotOrderPolicy
from tenant_hot_order_schema import ensure_hot_order_schema
from tenant_hot_order_notice_schema import enqueue_hot_order_notice
from tenant_hot_order_reward_policy import service_reward
from decimal import Decimal


class TenantHotOrderError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def identity(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise TenantHotOrderError('Sorğu ID-si düzgün deyil.') from None


def authorize(profile: dict) -> HotOrderPolicy:
    policy = HotOrderPolicy(TenantPolicy(profile))
    if not profile.get('tenant_id') or not profile.get('telegram_id') or not policy.policy.allows('hot_orders'):
        raise TenantHotOrderError('İsti sifarişlər üçün icazəniz yoxdur.', 403)
    return policy


def public_row(row: dict, policy: HotOrderPolicy | None = None) -> dict:
    result = {key: (value.isoformat() if hasattr(value, 'isoformat') else str(value) if isinstance(value, uuid.UUID) else value)
            for key, value in row.items() if key not in {'fingerprint', 'request_id'}}
    if policy:
        result['actions'] = {'claim':policy.can_claim(row), 'cancel':policy.can_edit(row),
            'complete':policy.can_complete(row),'approve':policy.can_review(row),'reject':policy.can_review(row),
            'release':bool(policy.owns_tenant(row) and row.get('status') == 'claimed' and
                           row.get('claimed_by') == policy.policy.profile.get('telegram_id'))}
        result['service_name'] = next((service['name'] for service in policy.settings.get('services', [])
                                       if service['id'] == row.get('service_id')), row.get('service_id',''))
    result['reward_amount'] = format(Decimal(int(result.pop('reward_minor', 0)))/100, '.2f')
    return result


def audit(cur, profile: dict, order_id: str, action: str) -> None:
    event_id = uuid.uuid4()
    cur.execute('''INSERT INTO saas_tenant_audit_events (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
        VALUES (%s,%s::uuid,%s,%s,'hot_order',%s,%s::jsonb)''',
        (event_id, str(profile['tenant_id']), int(profile['telegram_id']), 'hot_order_' + action, order_id, '{}'))
    if action in {'create','release'}:
        enqueue_hot_order_notice(cur,str(profile['tenant_id']),order_id,str(event_id))


def create_order(profile: dict, data: dict) -> dict:
    policy = authorize(profile)
    if not policy.allowed('create'):
        raise TenantHotOrderError('Sifariş yaratmaq üçün icazəniz yoxdur.', 403)
    payload = {}
    for key, maximum, required in [('service_id',80,True),('client_name',120,True),('description',8000,True),
                                   ('phone',80,False),('address',500,False)]:
        value = data.get(key, '')
        if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
            raise TenantHotOrderError('Müştəri, xidmət və mətn sahələrini düzgün doldurun.')
        payload[key] = value.strip()
    payload['priority'] = data.get('priority', 'normal')
    if payload['priority'] not in ('normal', 'urgent'):
        raise TenantHotOrderError('Prioritet düzgün deyil.')
    if payload['service_id'] not in {row['id'] for row in policy.services()}:
        raise TenantHotOrderError('Aktiv xidmət seçin.')
    request_id = identity(data.get('request_id'))
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    tenant, user, order_id = str(profile['tenant_id']), int(profile['telegram_id']), str(uuid.uuid4())
    with _connect() as conn:
        ensure_hot_order_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''INSERT INTO saas_hot_orders
                (tenant_id,id,request_id,fingerprint,status,service_id,client_name,description,phone,address,priority,reward_minor,created_by)
                VALUES (%s::uuid,%s::uuid,%s::uuid,%s,'open',%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_id,created_by,request_id) DO NOTHING RETURNING *''',
                (tenant, order_id, request_id, fingerprint, payload['service_id'], payload['client_name'],
                 payload['description'], payload['phone'], payload['address'], payload['priority'],
                 service_reward(policy.settings, payload['service_id']), user))
            row = cur.fetchone()
            if row:
                audit(cur, profile, order_id, 'create')
            else:
                cur.execute('''SELECT * FROM saas_hot_orders
                    WHERE tenant_id=%s::uuid AND created_by=%s AND request_id=%s::uuid''', (tenant, user, request_id))
                row = cur.fetchone()
                if not row or row['fingerprint'] != fingerprint:
                    raise TenantHotOrderError('Bu sorğu ID-si başqa sifariş üçün istifadə edilib.', 409)
        conn.commit()
    return public_row(row, policy)


def list_orders(profile: dict, *, limit: int = 50, offset: int = 0, approvals_only: bool = False) -> dict:
    policy = authorize(profile)
    limit, offset = max(1, min(int(limit), 100)), max(0, min(int(offset), 100000))
    tenant, user = str(profile['tenant_id']), int(profile['telegram_id'])
    where, values = 'tenant_id=%s::uuid', [tenant]
    if approvals_only:
        if not policy.policy.privileged:
            raise TenantHotOrderError('Təsdiq üçün icazəniz yoxdur.',403)
        where += " AND status='submitted'"
    if not policy.policy.privileged:
        services = [row['id'] for row in policy.services() if policy.allowed('claim') and policy.matches_service(row['id'])]
        where += " AND (created_by=%s OR claimed_by=%s OR (status='open' AND service_id=ANY(%s)))"
        values += [user, user, services]
    with _connect() as conn:
        ensure_hot_order_schema(conn)
        with conn.cursor() as cur:
            cur.execute('SELECT count(*) AS total FROM saas_hot_orders WHERE ' + where, values)
            total = int(cur.fetchone()['total'])
            cur.execute('SELECT * FROM saas_hot_orders WHERE ' + where +
                        ' ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s', [*values,limit,offset])
            rows = cur.fetchall()
    return {'orders':[public_row(row, policy) for row in rows], 'total':total, 'limit':limit, 'offset':offset}


def change_order(profile: dict, *, order_id: str, action: str) -> dict:
    policy = authorize(profile)
    if action not in {'claim','release','cancel'}:
        raise TenantHotOrderError('Əməliyyat düzgün deyil.')
    order_id = identity(order_id)
    tenant, user = str(profile['tenant_id']), int(profile['telegram_id'])
    with _connect() as conn:
        ensure_hot_order_schema(conn)
        with conn.cursor() as cur:
            # Serializes claims. A second employee sees the committed status,
            # not a stale pre-check. All checks/writes remain in this tenant.
            cur.execute('SELECT * FROM saas_hot_orders WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE', (tenant,order_id))
            row = cur.fetchone()
            if not row or not policy.can_view(row):
                raise TenantHotOrderError('Sifariş tapılmadı.', 404)
            if action == 'claim' and row['status'] == 'claimed' and row['claimed_by'] == user:
                return public_row(row, policy)
            if action == 'claim':
                permitted = policy.can_claim(row)
                target, claimant = 'claimed', user
            elif action == 'release':
                # Creator cannot force an accepted order back to the queue.
                permitted = row['status'] == 'claimed' and row['claimed_by'] == user
                target, claimant = 'open', None
            else:
                permitted = policy.can_edit(row)
                target, claimant = 'cancelled', None
            if not permitted:
                raise TenantHotOrderError('Sifariş dəyişib və ya əməliyyat üçün icazəniz yoxdur.', 409)
            cur.execute('''UPDATE saas_hot_orders SET status=%s,claimed_by=%s,
                claimed_at=CASE WHEN %s='claimed' THEN now() ELSE NULL END,updated_at=now()
                WHERE tenant_id=%s::uuid AND id=%s::uuid AND status=%s RETURNING *''',
                (target,claimant,target,tenant,order_id,row['status']))
            result = cur.fetchone()
            if not result:
                raise TenantHotOrderError('Sifariş artıq dəyişib.', 409)
            audit(cur, profile, order_id, action)
        conn.commit()
    return public_row(result, policy)
