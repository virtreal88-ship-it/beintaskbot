"""Live authority, immutable request receipt and conservative daily paid-call budget."""
import json
from tenant_platform import _connect, _fernet, _read_tenant_workflow
from tenant_policy import TenantPolicy
from tenant_linear_policy import TenantLinearError, identifier
from tenant_chat_ai_schema import ensure_schema
from tenant_chat_ai_policy import fingerprint, audio, media_identity


def access(cur, session, lead_id):
    tenant, user = identifier(session['tenant_id']), int(session['telegram_id'])
    cur.execute('''SELECT m.*,t.status AS tenant_status,t.modules FROM saas_tenant_members m
      JOIN saas_tenants t ON t.id=m.tenant_id WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s FOR SHARE OF m,t''', (tenant, user))
    person = cur.fetchone()
    if not person or person['active'] is not True or person['tenant_status'] != 'active':
        raise TenantLinearError('İcazə yoxdur.', 403)
    policy = TenantPolicy({**person, 'tenant_id': tenant, 'workflow': _read_tenant_workflow(cur, tenant)})
    cur.execute('SELECT * FROM saas_crm_deals WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND deleted_at IS NULL FOR SHARE', (tenant, lead_id))
    if not policy.can_use_chat_ai() or not policy.can_access_deal(cur.fetchone()):
        raise TenantLinearError('Bu çatın AI funksiyalarına icazə yoxdur.', 403)
    cur.execute("SELECT secrets,metadata,updated_at FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai' FOR SHARE", (tenant,))
    connection = cur.fetchone()
    if not connection or not connection['secrets'] or connection['metadata'].get('enabled') is not True or connection['metadata'].get('consent') is not True:
        raise TenantLinearError('Şirkətin çat AI ayarlarını aktiv edin.', 409)
    return tenant, user, connection


def reserve(session, command):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant = identifier(session['tenant_id'])
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tenant-chat-ai:' + tenant,))
            tenant, user, connection = access(cur, session, command['lead_id'])
            cur.execute('SELECT * FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND request_id=%s::uuid FOR UPDATE', (tenant, command['request_id']))
            existing = cur.fetchone()
            if existing:
                if existing['requested_by'] != user or existing['input_hash'] != fingerprint(command):
                    raise TenantLinearError('Sorğu kodu başqa əməliyyata aiddir.', 409)
                if existing['status'] == 'completed' and existing['result'] and existing['config_version'] == str(connection['updated_at']):
                    return {'cached_result': existing['result']}
                error = TenantLinearError('Əvvəlki sorğunun nəticəsi hazır deyil və ya naməlumdur. Avtomatik təkrarlanmır.', 409)
                error.keep_request = True
                raise error
            config = connection['metadata']
            if command['mode'] == 'transcribe':
                if config.get('audio_enabled') is not True:
                    raise TenantLinearError('Səs transkripsiyası aktiv deyil.', 409)
                cur.execute('''SELECT external_id,direction,body,message_type,media_url,happened_at FROM saas_crm_messages
                  WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND external_id=%s''', (tenant, command['lead_id'], command['message_id']))
                row = cur.fetchone()
                if not row or not audio(row):
                    raise TenantLinearError('Səs mesajı tapılmadı.', 404)
                rows = [row]
            else:
                cur.execute('''SELECT external_id,direction,body,message_type,media_url,happened_at FROM saas_crm_messages
                  WHERE tenant_id=%s::uuid AND kommo_lead_id=%s
                  ORDER BY COALESCE(happened_at,created_at) DESC,external_id DESC LIMIT 30''', (tenant, command['lead_id']))
                rows = list(reversed(cur.fetchall()))
                if not rows:
                    raise TenantLinearError('Çat tarixçəsi boşdur.', 409)
            transcripts, pending = {}, []
            for row in rows:
                if not audio(row):
                    continue
                media_hash = media_identity(row, config['transcription_model'])
                cur.execute('''SELECT transcript FROM saas_chat_transcripts WHERE tenant_id=%s::uuid AND lead_id=%s
                  AND media_hash=%s AND created_at>=now()-interval '90 days' ''', (tenant, command['lead_id'], media_hash))
                cached = cur.fetchone()
                if cached:
                    transcripts[str(row['external_id'])] = cached['transcript']
                elif config.get('audio_enabled') is True:
                    pending.append({**row, 'media_hash': media_hash})
            calls = len(pending) + (command['mode'] != 'transcribe')
            cur.execute("SELECT COALESCE(sum(reserved_calls),0) AS used FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND usage_day=(now() AT TIME ZONE 'UTC')::date", (tenant,))
            if cur.fetchone()['used'] + calls > config['daily_limit']:
                raise TenantLinearError('Şirkətin gündəlik AI limiti dolub.', 429)
            try:
                secret = json.loads(_fernet().decrypt(bytes(connection['secrets'])))
                if secret['tenant_id'] != tenant or secret['provider'] != 'chat_ai' or not isinstance(secret['api_key'], str) or not secret['api_key']:
                    raise ValueError()
            except Exception:
                raise TenantLinearError('AI təhlükəsizlik ayarlarını yoxlayın.', 503) from None
            cur.execute('''INSERT INTO saas_chat_ai_runs(tenant_id,request_id,requested_by,lead_id,input_hash,config_version,status,reserved_calls)
              VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,'sending',%s)''', (tenant, command['request_id'], user,
                command['lead_id'], fingerprint(command), str(connection['updated_at']), calls))
            # Retain dedup receipts, clear sensitive old result/cache in bounded batches.
            cur.execute("WITH old AS (SELECT tenant_id,request_id FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND created_at<now()-interval '90 days' AND result IS NOT NULL LIMIT 100) UPDATE saas_chat_ai_runs r SET result=NULL FROM old WHERE r.tenant_id=old.tenant_id AND r.request_id=old.request_id", (tenant,))
            cur.execute("DELETE FROM saas_chat_transcripts WHERE (tenant_id,lead_id,media_hash) IN (SELECT tenant_id,lead_id,media_hash FROM saas_chat_transcripts WHERE tenant_id=%s::uuid AND created_at<now()-interval '90 days' LIMIT 100)", (tenant,))
        conn.commit()
    return {'tenant_id': tenant, 'user_id': user, 'config_version': str(connection['updated_at']),
            'command': command, 'config': config, 'api_key': secret['api_key'], 'rows': rows,
            'pending_audio': pending, 'transcripts': transcripts}


def validate(item):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            _, _, connection = access(cur, {'tenant_id': item['tenant_id'], 'telegram_id': item['user_id']}, item['command']['lead_id'])
            if str(connection['updated_at']) != item['config_version']:
                raise TenantLinearError('AI ayarları dəyişib.', 409)


def cache_transcript(item, row, text):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            _, _, connection = access(cur, {'tenant_id': item['tenant_id'], 'telegram_id': item['user_id']}, item['command']['lead_id'])
            if str(connection['updated_at']) != item['config_version']:
                raise TenantLinearError('AI ayarları dəyişib.', 409)
            cur.execute('''INSERT INTO saas_chat_transcripts(tenant_id,lead_id,media_hash,transcript)
              VALUES(%s::uuid,%s,%s,%s) ON CONFLICT(tenant_id,lead_id,media_hash) DO UPDATE
              SET transcript=EXCLUDED.transcript,created_at=now()''', (item['tenant_id'], item['command']['lead_id'], row['media_hash'], text))
        conn.commit()


def finish(item, result):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            status = 'unknown'
            if result is not None:
                try:
                    _, _, connection = access(cur, {'tenant_id': item['tenant_id'], 'telegram_id': item['user_id']}, item['command']['lead_id'])
                    status = 'completed' if str(connection['updated_at']) == item['config_version'] else 'skipped'
                except TenantLinearError:
                    status = 'skipped'
            cur.execute("UPDATE saas_chat_ai_runs SET status=%s,result=%s::jsonb,updated_at=now() WHERE tenant_id=%s::uuid AND request_id=%s::uuid AND status='sending'",
                        (status, json.dumps(result) if status == 'completed' else None, item['tenant_id'], item['command']['request_id']))
        conn.commit()
    if result is not None and status != 'completed':
        raise TenantLinearError('Giriş və ya ayarlar dəyişib. Nəticə göstərilmir.', 409)
