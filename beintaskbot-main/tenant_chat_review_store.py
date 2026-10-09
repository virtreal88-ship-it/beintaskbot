"""Administrative attestations, never provider writes or automatic resend."""
import json
import uuid
from datetime import datetime
from tenant_platform import _connect, _read_tenant_workflow
from tenant_chat_review_schema import ensure_schema
from tenant_policy import TenantPolicy
from tenant_linear_policy import TenantLinearError


def positive(value):
    result = int(str(value))
    if not 0 < result < 2**63:
        raise ValueError('Invalid ID')
    return result


def authorize(cur, session, lead):
    tenant, actor = str(uuid.UUID(str(session['tenant_id']))), positive(session['telegram_id'])
    cur.execute('''SELECT m.*,t.status AS tenant_status,t.modules FROM saas_tenant_members m
      JOIN saas_tenants t ON t.id=m.tenant_id WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s FOR SHARE OF m,t''', (tenant, actor))
    member = cur.fetchone()
    if not member or member['active'] is not True or member['tenant_status'] != 'active':
        raise TenantLinearError('İcazə yoxdur.', 403)
    policy = TenantPolicy({**member, 'tenant_id': tenant, 'workflow': _read_tenant_workflow(cur, tenant)})
    cur.execute('SELECT * FROM saas_crm_deals WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND deleted_at IS NULL FOR SHARE', (tenant, lead))
    if not policy.privileged or not policy.allows('deals') or not policy.can_access_deal(cur.fetchone()):
        raise TenantLinearError('Yalnız icazəli administrator yoxlaya bilər.', 403)
    return tenant, actor


def list_receipts(session, lead_id):
    lead = positive(lead_id)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = authorize(cur, session, lead)
            cur.execute('''SELECT actor_id,request_id,state,route,message_id,created_at,updated_at,resolution
              FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid AND lead_id=%s
              ORDER BY created_at DESC,request_id DESC LIMIT 50''', (tenant, lead))
            rows = cur.fetchall()
        conn.commit()
    return {'receipts': [{**row, 'request_id': str(row['request_id']),
                          'created_at': row['created_at'].isoformat(), 'updated_at': row['updated_at'].isoformat()} for row in rows]}


def resolve(session, data):
    lead, sender = positive(data['lead_id']), positive(data['actor_id'])
    request_id, review_id = str(uuid.UUID(str(data['request_id']))), str(uuid.UUID(str(data['review_id'])))
    decision, reason = data.get('decision'), data.get('reason')
    if decision not in {'found', 'not_sent'} or not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 1000:
        raise TenantLinearError('Nəticəni seçin və yoxlamanın səbəbini yazın (5–1000 simvol).')
    version = datetime.fromisoformat(str(data['expected_updated_at']))
    if version.tzinfo is None:
        raise ValueError('Timestamp must include timezone')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, reviewer = authorize(cur, session, lead)
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f'chat-send:{tenant}:{lead}',))
            key = (tenant, sender, request_id, lead)
            cur.execute('SELECT * FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid AND lead_id=%s FOR UPDATE', key)
            row = cur.fetchone()
            if not row:
                raise TenantLinearError('Göndərmə tapılmadı.', 404)
            previous = row.get('resolution') or {}
            if previous:
                if (previous.get('review_id'), previous.get('reviewer'), previous.get('decision'), previous.get('reason')) == (review_id, reviewer, decision, reason.strip()):
                    return {'state': row['state'], 'resolution': previous}
                raise TenantLinearError('Nəticə artıq qeyd edilib. Siyahını yeniləyin.', 409)
            # Active sends are never administratively interrupted. Stale sending
            # is first converted to unknown by the existing reservation cleanup.
            if row['state'] != 'unknown' or row['updated_at'] != version:
                raise TenantLinearError('Vəziyyət dəyişib və ya göndərmə hələ işlənir. Yeniləyin.', 409)
            resolution = {'review_id': review_id, 'reviewer': reviewer, 'decision': decision,
                          'reason': reason.strip(), 'manual': True}
            state = 'accepted' if decision == 'found' else 'blocked'
            cur.execute('''UPDATE saas_chat_send_receipts SET state=%s,resolution=%s::jsonb,updated_at=now()
              WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid AND lead_id=%s
              AND state='unknown' AND updated_at=%s RETURNING request_id''', (state, json.dumps(resolution), *key, version))
            if not cur.fetchone():
                raise TenantLinearError('Vəziyyət dəyişib. Yeniləyin.', 409)
            cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
              VALUES(%s,%s::uuid,%s,'chat_send_manually_reviewed','lead',%s,%s::jsonb)''',
              (uuid.uuid4(), tenant, reviewer, str(lead), json.dumps({**resolution, 'sender': sender, 'request_id': request_id})))
        conn.commit()
    return {'state': state, 'resolution': resolution}
