"""Durable, tenant/actor-specific receipts reserved before external writes."""
import json
import uuid
from tenant_platform import _connect, _read_tenant_workflow, _fernet
from tenant_chat_send_schema import ensure_schema
from tenant_chat_send_policy import fingerprint
from tenant_policy import TenantPolicy
from tenant_linear_policy import TenantLinearError


def authority(cur, session, lead_id):
    tenant, actor = str(uuid.UUID(str(session['tenant_id']))), int(session['telegram_id'])
    cur.execute('''SELECT m.*,t.status AS tenant_status,t.modules FROM saas_tenant_members m
      JOIN saas_tenants t ON t.id=m.tenant_id WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s FOR SHARE OF m,t''', (tenant, actor))
    profile = cur.fetchone()
    if not profile or profile['active'] is not True or profile['tenant_status'] != 'active':
        raise TenantLinearError('İcazə yoxdur.', 403)
    profile = {**profile, 'tenant_id': tenant, 'workflow': _read_tenant_workflow(cur, tenant)}
    policy = TenantPolicy(profile)
    cur.execute('SELECT * FROM saas_crm_deals WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND deleted_at IS NULL FOR SHARE', (tenant, lead_id))
    if not policy.allows('deals') or not policy.can_access_deal(cur.fetchone()):
        raise TenantLinearError('Mesaj göndərmək üçün icazəniz yoxdur.', 403)
    cur.execute("SELECT account_domain,connected_at,status,secrets FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='kommo' FOR SHARE", (tenant,))
    integration = cur.fetchone()
    if not integration or integration['status'] != 'connected' or not integration['secrets']:
        raise TenantLinearError('Kommo bağlantısını yoxlayın.', 409)
    return profile, [integration['account_domain'], str(integration['connected_at'])], integration


def reserve(session, command):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            profile, connection, _ = authority(cur, session, command['lead_id'])
            key = (profile['tenant_id'], int(profile['telegram_id']), command['request_id'])
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f"chat-send:{key[0]}:{command['lead_id']}",))
            cur.execute("UPDATE saas_chat_send_receipts SET state=CASE WHEN state='preparing' THEN 'blocked' ELSE 'unknown' END,updated_at=now() WHERE tenant_id=%s::uuid AND lead_id=%s AND state IN ('preparing','sending') AND updated_at<now()-interval '10 minutes'", (key[0], command['lead_id']))
            cur.execute('SELECT * FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid FOR UPDATE', key)
            row = cur.fetchone()
            if row:
                if row['input_hash'] != fingerprint(command):
                    raise TenantLinearError('Eyni sorğu kodu ilə fərqli mesaj göndərilib.', 409)
                if row['state'] == 'accepted':
                    return {'cached_result': {'state': 'accepted', 'message_id': row['message_id']}}
                error = TenantLinearError('Bu sorğu təkrarlanmır. Göndərilmə vəziyyətini Kommo-da yoxlayın.', 409)
                error.keep_request = row['state'] != 'blocked'
                conn.commit()  # Persist stale receipt transitions before returning the refusal.
                raise error
            cur.execute("SELECT request_id FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid AND lead_id=%s AND state IN ('preparing','sending','unknown') LIMIT 1", (key[0], command['lead_id']))
            if cur.fetchone():
                conn.commit()
                raise TenantLinearError('Bu çatda başqa göndərmə yoxlanılır. Kommo-da vəziyyəti yoxlayın.', 409)
            cur.execute("INSERT INTO saas_chat_send_receipts(tenant_id,actor_id,request_id,lead_id,input_hash,state) VALUES(%s::uuid,%s,%s::uuid,%s,%s,'preparing')", (*key, command['lead_id'], fingerprint(command)))
        conn.commit()
    return {'key': key, 'command': command, 'connection': connection, 'profile': profile}


def mark_sending(item, route):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            profile, connection, integration = authority(cur, {'tenant_id': item['key'][0], 'telegram_id': item['key'][1]}, item['command']['lead_id'])
            if connection != item['connection']:
                raise TenantLinearError('Kommo bağlantısı dəyişib.', 409)
            tokens = json.loads(_fernet().decrypt(bytes(integration['secrets'])).decode('utf-8'))
            if not isinstance(tokens, dict) or not tokens.get('access_token'):
                raise TenantLinearError('Kommo bağlantısını yeniləyin.', 409)
            credentials = {'account_domain': integration['account_domain'], 'access_token': tokens['access_token']}
            cur.execute("UPDATE saas_chat_send_receipts SET state='sending',route=%s::jsonb,updated_at=now() WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid AND state='preparing' RETURNING request_id", (json.dumps(route), *item['key']))
            if not cur.fetchone():
                raise TenantLinearError('Göndərmə artıq işlənir.', 409)
        conn.commit()
    return profile, credentials


def finish(item, state, message_id=None):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_chat_send_receipts SET state=%s,message_id=%s,updated_at=now() WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid AND state IN ('preparing','sending') RETURNING lead_id", (state, message_id, *item['key']))
            row = cur.fetchone()
            if row and state == 'accepted':
                cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                  VALUES(%s,%s::uuid,%s,'chat_message_accepted','lead',%s,%s::jsonb)''',
                  (uuid.uuid4(), item['key'][0], item['key'][1], str(row['lead_id']), json.dumps({'message_id': message_id})))
        conn.commit()
