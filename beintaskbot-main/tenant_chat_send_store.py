"""Durable, tenant/actor-specific receipts reserved before external writes."""
import json
import uuid
import hashlib
from tenant_platform import _connect, _read_tenant_workflow, _fernet
from tenant_chat_send_schema import ensure_schema
from tenant_chat_send_policy import fingerprint
from tenant_policy import TenantPolicy
from tenant_linear_policy import TenantLinearError
from tenant_ai_agent_runtime_flags import enabled as runtime_flag


def agent_send_reservation_block_reason(current, job, route, integration, *, runtime_enabled):
    """Return a stable, non-sensitive code for the first failed send gate."""
    if not runtime_enabled:
        return 'global_auto_send_disabled'
    if not current:
        return 'job_missing'
    if current.get('state') != 'processing':
        return 'job_not_processing'
    if current.get('revision') != job.get('revision'):
        return 'job_revision_changed'
    if current.get('lease_token') != job.get('lease_token'):
        return 'job_lease_changed'
    if current.get('agent_enabled') is not True:
        return 'agent_disabled'
    if current.get('mode') != 'automatic':
        return 'agent_not_automatic'
    if current.get('settings_enabled') is not True:
        return 'company_ai_disabled'
    if current.get('auto_send_enabled') is not True:
        return 'company_auto_send_disabled'
    if current.get('tenant_status') != 'active':
        return 'tenant_inactive'
    if current.get('conversation_state') in {'human', 'paused'}:
        return 'conversation_paused_for_manager'
    if current.get('test_mode_allowed') is not True:
        return 'test_mode_binding_mismatch'
    if current.get('conversation_revision') != route.get('conversation_revision'):
        return 'conversation_revision_changed'
    if not integration or integration.get('status') != 'connected':
        return 'kommo_disconnected'
    # The app has one explicitly linked legacy Kommo credential source. It is
    # safe for an AI write only when the exact verified Test Mode binding is
    # active; tenant OAuth continues to use encrypted per-tenant secrets.
    if (not integration.get('secrets') and not integration.get('credentials_available')):
        return 'kommo_credentials_missing'
    if (integration.get('credential_source') == 'legacy_environment'
            and current.get('test_mode_allowed') is not True):
        return 'legacy_credentials_test_mode_only'
    return ''


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
                    if row.get('resolution'):
                        return {'cached_result': {'state': 'accepted', 'message_id': row['message_id'], 'manual_review': True}}
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


def upload_credentials(item):
    """Fresh authority before upload, without claiming message sending."""
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            profile, connection, integration = authority(cur,
                {'tenant_id': item['key'][0], 'telegram_id': item['key'][1]}, item['command']['lead_id'])
            if connection != item['connection']:
                raise TenantLinearError('Kommo bağlantısı dəyişib.', 409)
            tokens = json.loads(_fernet().decrypt(bytes(integration['secrets'])))
            if not isinstance(tokens, dict) or not isinstance(tokens.get('access_token'), str) or not tokens['access_token']:
                raise TenantLinearError('Kommo bağlantısını yeniləyin.', 409)
        conn.commit()
    return {'account_domain': integration['account_domain'], 'access_token': tokens['access_token']}


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


def reserve_agent(job, text, route):
    """Reserve the shared manual/agent send receipt using actor_id=0."""
    tenant, agent = str(job['tenant_id']), str(job['agent_id'])
    request_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
        f"beintaskbot-ai-send:{tenant}:{agent}:{job['chat_id']}:{job['event_fingerprint']}:{job['revision']}"))
    input_hash = hashlib.sha256((str(job['lead_id'])+'\0'+str(job['talk_id'])+'\0'+str(job['chat_id'])+'\0'+str(text)).encode()).hexdigest()
    with _connect() as conn:
        ensure_schema(conn)
        from tenant_ai_agents_schema import ensure_schema as ensure_agents_schema
        ensure_agents_schema(conn)
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f"chat-send:{tenant}:{job['lead_id']}",))
            cur.execute("SELECT status,secrets,account_domain,metadata FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='kommo' FOR SHARE",(tenant,))
            integration=cur.fetchone()
            metadata = integration.get('metadata') if integration and isinstance(integration.get('metadata'), dict) else {}
            legacy_reference = metadata.get('credential_source') == 'legacy_environment'
            if legacy_reference:
                integration = {**integration, 'credential_source': 'legacy_environment',
                               'credentials_available': True}
            cur.execute('''SELECT j.state,j.revision,j.lease_token,a.enabled AS agent_enabled,a.mode,
              s.enabled AS settings_enabled,s.auto_send_enabled,s.max_replies_per_chat,t.status AS tenant_status,
              cs.state AS conversation_state,cs.revision AS conversation_revision,
              EXISTS (SELECT 1 FROM saas_ai_agent_test_mode b JOIN saas_tenant_integrations ki
                ON ki.tenant_id=b.tenant_id AND ki.provider='kommo' AND ki.status='connected'
                JOIN saas_tenant_webhook_events e ON e.tenant_id=j.tenant_id AND e.fingerprint=j.event_fingerprint
                WHERE b.tenant_id=j.tenant_id AND b.enabled=TRUE AND b.verified=TRUE AND b.emergency_stop=FALSE
                  AND b.account_domain=ki.account_domain AND b.lead_id=j.lead_id AND b.contact_id=CASE WHEN e.payload->>'contact_id' ~ '^[0-9]{1,18}$' THEN (e.payload->>'contact_id')::bigint END
                  AND b.talk_id=j.talk_id AND b.chat_id=j.chat_id AND b.channel_id=j.channel_id) AS test_mode_allowed
              FROM saas_ai_agent_jobs j JOIN saas_ai_agents a ON a.tenant_id=j.tenant_id AND a.id=j.agent_id
              JOIN saas_ai_agent_settings s ON s.tenant_id=j.tenant_id JOIN saas_tenants t ON t.id=j.tenant_id
              LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=j.tenant_id AND cs.provider=j.provider AND cs.conversation_id=j.conversation_id
              WHERE j.tenant_id=%s::uuid AND j.agent_id=%s::uuid AND j.provider=%s AND j.conversation_id=%s
              FOR UPDATE OF j,a,s,t''',(tenant,agent,job['provider'],job['conversation_id']))
            current=cur.fetchone()
            block_reason=agent_send_reservation_block_reason(current,job,route,integration,
                runtime_enabled=runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED'))
            if block_reason:
                error=TenantLinearError('AI avtomatik göndərmə üçün aktiv deyil.',409)
                error.ai_agent_send_reason=block_reason
                raise error
            cur.execute('''SELECT count(*) AS sent FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid AND actor_id=0
              AND state='accepted' AND route->>'chat_id'=%s AND created_at >= (now() AT TIME ZONE 'UTC')::date AT TIME ZONE 'UTC' ''',
              (tenant,str(job['chat_id'])) )
            if int((cur.fetchone() or {}).get('sent') or 0)>=int(current['max_replies_per_chat'] or 20):
                error=TenantLinearError('Bu çat üçün gündəlik AI cavab limiti dolub.',429)
                error.ai_agent_send_reason='daily_chat_limit_reached'
                raise error
            expire_unstarted_agent_receipts(cur, tenant, job['lead_id'])
            cur.execute("SELECT request_id,state,input_hash FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid AND lead_id=%s AND state IN ('preparing','sending','unknown') LIMIT 1",(tenant,job['lead_id']))
            active=cur.fetchone()
            if active:
                if str(active['request_id'])==request_id and active['input_hash']==input_hash and active['state']=='preparing':
                    error=TenantLinearError('Göndərmə artıq hazırlanıb.',409)
                    error.ai_agent_send_reason='send_already_preparing'
                    raise error
                error=TenantLinearError('Bu çatda əvvəlki göndərmənin nəticəsi yoxlanılmalıdır.',409)
                error.ai_agent_send_reason='previous_send_unresolved'
                raise error
            cur.execute('SELECT state,input_hash,message_id FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid AND actor_id=0 AND request_id=%s::uuid FOR UPDATE',(tenant,request_id))
            old=cur.fetchone()
            if old:
                if old['input_hash']!=input_hash:
                    error=TenantLinearError('Göndərmə idempotency yoxlamasından keçmədi.',409)
                    error.ai_agent_send_reason='idempotency_conflict'
                    raise error
                error=TenantLinearError('Bu agent sorğusunun göndərmə nəticəsi təkrarlanmır.',409)
                error.ai_agent_send_reason='duplicate_agent_send'
                raise error
            cur.execute('''INSERT INTO saas_chat_send_receipts(tenant_id,actor_id,request_id,lead_id,input_hash,state,route)
              VALUES(%s::uuid,0,%s::uuid,%s,%s,'preparing',%s::jsonb)''',
              (tenant,request_id,job['lead_id'],input_hash,json.dumps(route)))
            if legacy_reference:
                # Resolve the already-linked server credential by tenant and
                # account mapping. Never copy the shared token into DB secrets.
                from tenant_integration_store import kommo_credentials
                try:
                    resolved = kommo_credentials(tenant_id=tenant)
                except Exception:
                    error=TenantLinearError('Mövcud Kommo bağlantısının hesabı təsdiqlənmədi.',409)
                    error.ai_agent_send_reason='kommo_credentials_invalid'
                    raise error from None
                if (resolved.get('credential_source') != 'legacy_environment'
                        or resolved.get('account_domain', '').casefold() != str(integration['account_domain']).casefold()):
                    error=TenantLinearError('Mövcud Kommo bağlantısının hesabı təsdiqlənmədi.',409)
                    error.ai_agent_send_reason='kommo_credentials_invalid'
                    raise error
                credentials={'account_domain':resolved['account_domain'],
                             'access_token':resolved['access_token'],
                             'credential_source':'legacy_environment',
                             'expected_account_id':resolved['expected_account_id']}
            else:
                tokens=json.loads(_fernet().decrypt(bytes(integration['secrets'])).decode('utf-8'))
                if not isinstance(tokens,dict) or not tokens.get('access_token'):
                    error=TenantLinearError('Kommo bağlantısını yeniləyin.',409)
                    error.ai_agent_send_reason='kommo_credentials_invalid'
                    raise error
                credentials={'account_domain':integration['account_domain'],'access_token':tokens['access_token']}
        conn.commit()
    return {'key':(tenant,0,request_id),'job':job,'credentials':credentials,'route':route,'text':text,'input_hash':input_hash}


def mark_agent_sending(item):
    job=item['job']; tenant=str(job['tenant_id'])
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT j.state,j.revision,j.lease_token,a.enabled AS agent_enabled,a.mode,
              s.enabled AS settings_enabled,s.auto_send_enabled,t.status AS tenant_status,
              cs.state AS conversation_state,cs.revision AS conversation_revision,
              EXISTS (SELECT 1 FROM saas_ai_agent_test_mode b JOIN saas_tenant_integrations ki
                ON ki.tenant_id=b.tenant_id AND ki.provider='kommo' AND ki.status='connected'
                JOIN saas_tenant_webhook_events e ON e.tenant_id=j.tenant_id AND e.fingerprint=j.event_fingerprint
                WHERE b.tenant_id=j.tenant_id AND b.enabled=TRUE AND b.verified=TRUE AND b.emergency_stop=FALSE
                  AND b.account_domain=ki.account_domain AND b.lead_id=j.lead_id AND b.contact_id=CASE WHEN e.payload->>'contact_id' ~ '^[0-9]{1,18}$' THEN (e.payload->>'contact_id')::bigint END
                  AND b.talk_id=j.talk_id AND b.chat_id=j.chat_id AND b.channel_id=j.channel_id) AS test_mode_allowed
              FROM saas_ai_agent_jobs j JOIN saas_ai_agents a ON a.tenant_id=j.tenant_id AND a.id=j.agent_id
              JOIN saas_ai_agent_settings s ON s.tenant_id=j.tenant_id JOIN saas_tenants t ON t.id=j.tenant_id
              LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=j.tenant_id AND cs.provider=j.provider AND cs.conversation_id=j.conversation_id
              WHERE j.tenant_id=%s::uuid AND j.agent_id=%s::uuid AND j.provider=%s AND j.conversation_id=%s
              FOR UPDATE OF j,a,s,t''',(tenant,job['agent_id'],job['provider'],job['conversation_id']))
            current=cur.fetchone()
            if (not runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED')
                    or not current or current['state']!='processing' or current['revision']!=job['revision']
                    or current['lease_token']!=job['lease_token'] or current['agent_enabled'] is not True
                    or current['mode']!='automatic' or current['settings_enabled'] is not True
                    or current['auto_send_enabled'] is not True or current['tenant_status']!='active'
                    or current['conversation_state'] in {'human','paused'}
                    or current.get('test_mode_allowed') is not True
                    or current['conversation_revision']!=item['route'].get('conversation_revision')):
                raise TenantLinearError('Agent və ya avtomatik göndərmə icazəsi dəyişib.',409)
            cur.execute("UPDATE saas_chat_send_receipts SET state='sending',updated_at=now() WHERE tenant_id=%s::uuid AND actor_id=0 AND request_id=%s::uuid AND state='preparing' RETURNING request_id",(item['key'][0],item['key'][2]))
            if not cur.fetchone():
                raise TenantLinearError('Göndərmə receipt-i aktiv deyil.',409)
        conn.commit()


def expire_unstarted_agent_receipts(cur, tenant, lead_id):
    """Unblock only stale AI preparations; a provider POST has not started yet."""
    cur.execute("""UPDATE saas_chat_send_receipts SET state='blocked',updated_at=now()
      WHERE tenant_id=%s::uuid AND actor_id=0 AND lead_id=%s AND state='preparing'
        AND updated_at<now()-interval '10 minutes'""", (tenant, lead_id))


def finish_agent(item,state,message_id=None):
    if state not in {'accepted','unknown','blocked'}:
        raise ValueError('Invalid agent send state')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_chat_send_receipts SET state=%s,message_id=%s,updated_at=now() WHERE tenant_id=%s::uuid AND actor_id=0 AND request_id=%s::uuid AND state IN ('preparing','sending') RETURNING lead_id",(state,message_id,item['key'][0],item['key'][2]))
            row=cur.fetchone()
            if not row:
                cur.execute('SELECT state,message_id FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid AND actor_id=0 AND request_id=%s::uuid',(item['key'][0],item['key'][2]))
                current=cur.fetchone() or {}
                conn.commit()
                return current.get('state') or 'unknown'
            if state=='blocked':
                conn.commit()
                return 'blocked'
            if row and state=='accepted':
                cur.execute('''UPDATE saas_ai_conversation_state SET last_agent_message_id=%s,updated_at=now()
                  WHERE tenant_id=%s::uuid AND provider=%s AND conversation_id=%s''',
                  (str(message_id),item['key'][0],item['job']['provider'],item['job']['conversation_id']))
            job=item['job']
            run_state='completed' if state=='accepted' else 'unknown'
            job_state='sent' if state=='accepted' else 'unknown'
            cur.execute('''UPDATE saas_ai_agent_runs SET state=%s,result=%s::jsonb,updated_at=now()
              WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND event_fingerprint=%s AND revision=%s AND state='generating' ''',
              (run_state,json.dumps({'send_state':state,'message_id':message_id}),item['key'][0],job['agent_id'],job['event_fingerprint'],job['revision']))
            cur.execute('''UPDATE saas_ai_agent_jobs SET state=%s,lease_token=NULL,lease_until=NULL,error_code=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider=%s AND conversation_id=%s
                AND state='processing' AND lease_token=%s AND revision=%s''',
              (job_state,'' if state=='accepted' else 'send_'+state,item['key'][0],job['agent_id'],job['provider'],job['conversation_id'],job['lease_token'],job['revision']))
            cur.execute("UPDATE saas_tenant_webhook_events SET state=%s,completed_at=CASE WHEN %s='sent' THEN now() ELSE completed_at END,error_code=%s WHERE tenant_id=%s::uuid AND fingerprint=%s",
              ('completed' if job_state=='sent' else 'unknown',job_state,'' if state=='accepted' else 'send_'+state,item['key'][0],job['event_fingerprint']))
        conn.commit()
    return state
