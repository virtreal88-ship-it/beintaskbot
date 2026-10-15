"""Lease, budget and draft receipts for autonomous manual-mode preparation."""
import json
import uuid

from tenant_ai_agents_schema import ensure_schema
from tenant_ai_agent_access import tenant_status_allows_ai
from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect, _fernet, _json


def claim_next():
    from tenant_ai_agent_runtime_flags import enabled as runtime_flag
    if not runtime_flag('AI_AGENTS_ENABLED'):
        return None
    conn = _connect()
    try:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT j.tenant_id,j.agent_id,j.provider,j.conversation_id,j.revision,j.event_fingerprint
              FROM saas_ai_agent_jobs j WHERE j.state='processing' AND j.lease_until<=now()
              AND NOT EXISTS (SELECT 1 FROM saas_ai_agent_runs r WHERE r.tenant_id=j.tenant_id
                AND r.agent_id=j.agent_id AND r.event_fingerprint=j.event_fingerprint AND r.revision=j.revision)
              FOR UPDATE OF j SKIP LOCKED''')
            abandoned = cur.fetchall()
            for row in abandoned:
                cur.execute('''UPDATE saas_ai_agent_jobs SET state=CASE WHEN attempts>=5 THEN 'failed' ELSE 'queued' END,
                  lease_token=NULL,lease_until=NULL,due_at=now(),error_code='worker_interrupted',updated_at=now()
                  WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider=%s AND conversation_id=%s''',
                  (row['tenant_id'],row['agent_id'],row['provider'],row['conversation_id']))
                cur.execute("UPDATE saas_tenant_webhook_events SET state='queued',error_code='worker_interrupted' WHERE tenant_id=%s::uuid AND fingerprint=%s",
                            (row['tenant_id'],row['event_fingerprint']))
            cur.execute('''UPDATE saas_ai_agent_runs r SET state='unknown',updated_at=now()
              WHERE r.state='generating' AND r.created_at<now()-interval '3 minutes'
              AND NOT EXISTS (SELECT 1 FROM saas_ai_agent_jobs j WHERE j.tenant_id=r.tenant_id
                AND j.agent_id=r.agent_id AND j.event_fingerprint=r.event_fingerprint
                AND j.revision=r.revision AND j.state='processing' AND j.lease_until>now())''')
            cur.execute('''UPDATE saas_ai_agent_jobs j SET state='unknown',lease_token=NULL,lease_until=NULL,
              error_code='provider_result_unknown',updated_at=now()
              WHERE j.state='processing' AND j.lease_until<=now() AND EXISTS (
                SELECT 1 FROM saas_ai_agent_runs r WHERE r.tenant_id=j.tenant_id AND r.agent_id=j.agent_id
                AND r.event_fingerprint=j.event_fingerprint AND r.revision=j.revision
                AND r.state IN ('generating','unknown'))''')
            cur.execute('''UPDATE saas_tenant_webhook_events e SET state='unknown',error_code='provider_result_unknown'
              WHERE e.state='processing' AND EXISTS (SELECT 1 FROM saas_ai_agent_jobs j
                WHERE j.tenant_id=e.tenant_id AND j.event_fingerprint=e.fingerprint AND j.state='unknown')''')
            cur.execute('''SELECT j.* FROM saas_ai_agent_jobs j
              JOIN saas_ai_agents a ON a.tenant_id=j.tenant_id AND a.id=j.agent_id AND a.enabled=TRUE
              JOIN saas_ai_agent_settings cfg ON cfg.tenant_id=j.tenant_id AND cfg.enabled=TRUE AND cfg.emergency_stop=FALSE
              JOIN saas_tenants t ON t.id=j.tenant_id
              WHERE j.state='queued' AND j.due_at<=now()
              AND (t.status='active' OR (j.provider='connect' AND t.status IN ('onboarding','ready_for_integration')))
              AND (cfg.operating_mode='active' OR (j.provider='connect' AND EXISTS (SELECT 1 FROM saas_ai_agent_connect_test_mode cb
                JOIN saas_tenant_webhook_events ce ON ce.tenant_id=j.tenant_id AND ce.fingerprint=j.event_fingerprint
                WHERE cb.tenant_id=j.tenant_id AND cb.enabled=TRUE AND cb.verified=TRUE AND cb.emergency_stop=FALSE
                  AND cb.channel_id=j.channel_id AND cb.conversation_id=j.conversation_id AND ce.payload->>'test_mode'='true')) OR EXISTS (SELECT 1 FROM saas_ai_agent_test_mode b
                JOIN saas_tenant_integrations ki ON ki.tenant_id=b.tenant_id AND ki.provider='kommo' AND ki.status='connected'
                JOIN saas_tenant_webhook_events e ON e.tenant_id=j.tenant_id AND e.fingerprint=j.event_fingerprint
                WHERE b.tenant_id=j.tenant_id AND b.enabled=TRUE AND b.verified=TRUE AND b.emergency_stop=FALSE
                  AND b.account_domain=ki.account_domain AND b.lead_id=j.lead_id AND b.talk_id=j.talk_id
                  AND b.chat_id=j.chat_id AND b.channel_id=j.channel_id
                  AND b.contact_id=CASE WHEN e.payload->>'contact_id' ~ '^[0-9]{1,18}$' THEN (e.payload->>'contact_id')::bigint END))
              ORDER BY j.due_at,j.updated_at LIMIT 1 FOR UPDATE OF j SKIP LOCKED''')
            row = cur.fetchone()
            if not row:
                conn.commit()
                return None
            token = uuid.uuid4()
            cur.execute('''UPDATE saas_ai_agent_jobs SET state='processing',lease_token=%s,lease_until=now()+interval '3 minutes',
              attempts=attempts+1,updated_at=now() WHERE tenant_id=%s::uuid AND agent_id=%s::uuid
              AND provider=%s AND conversation_id=%s RETURNING *''',
              (token,row['tenant_id'],row['agent_id'],row['provider'],row['conversation_id']))
            claimed = dict(cur.fetchone())
            cur.execute('''SELECT payload FROM saas_tenant_webhook_events
              WHERE tenant_id=%s::uuid AND fingerprint=%s FOR UPDATE''',
              (claimed['tenant_id'],claimed['event_fingerprint']))
            event = cur.fetchone()
            if not event:
                cur.execute('''UPDATE saas_ai_agent_jobs SET state='failed',error_code='event_missing',updated_at=now()
                  WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider=%s AND conversation_id=%s''',
                  (claimed['tenant_id'],claimed['agent_id'],claimed['provider'],claimed['conversation_id']))
                conn.commit()
                return None
            claimed['payload'] = event['payload'] if isinstance(event['payload'], dict) else {}
        conn.commit()
        return claimed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def retry_or_fail(job, error_code):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_ai_agent_jobs SET state=CASE WHEN attempts>=5 THEN 'failed' ELSE 'queued' END,
              due_at=now()+(LEAST(60,2^LEAST(attempts,5))*interval '1 second'),
              lease_token=NULL,lease_until=NULL,error_code=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider=%s AND conversation_id=%s
                AND state='processing' AND lease_token=%s AND revision=%s''',
              (str(error_code)[:80],job['tenant_id'],job['agent_id'],job['provider'],job['conversation_id'],job['lease_token'],job['revision']))
            cur.execute('''UPDATE saas_tenant_webhook_events SET state=CASE WHEN %s THEN 'failed' ELSE 'queued' END,
              error_code=%s WHERE tenant_id=%s::uuid AND fingerprint=%s''',
              (int(job.get('attempts') or 0)>=5,str(error_code)[:80],job['tenant_id'],job['event_fingerprint']))
        conn.commit()


def load_agent_config(job, *, with_block_reason=False):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT a.enabled AS agent_enabled,a.scopes,a.model AS agent_model,a.safety,a.schedule,a.mode,
              s.enabled AS settings_enabled,s.operating_mode,s.emergency_stop,s.crm_actions_enabled,i.metadata,t.status AS tenant_status,cs.state AS conversation_state,
              (j.provider='connect' AND EXISTS (SELECT 1 FROM saas_ai_agent_connect_test_mode cb JOIN saas_tenant_webhook_events ce
                ON ce.tenant_id=j.tenant_id AND ce.fingerprint=j.event_fingerprint WHERE cb.tenant_id=j.tenant_id AND cb.enabled=TRUE
                AND cb.verified=TRUE AND cb.emergency_stop=FALSE AND cb.channel_id=j.channel_id AND cb.conversation_id=j.conversation_id
                AND ce.payload->>'test_mode'='true')) OR EXISTS (SELECT 1 FROM saas_ai_agent_test_mode b JOIN saas_tenant_integrations ki
                ON ki.tenant_id=b.tenant_id AND ki.provider='kommo' AND ki.status='connected'
                JOIN saas_tenant_webhook_events e ON e.tenant_id=j.tenant_id AND e.fingerprint=j.event_fingerprint
                WHERE b.tenant_id=j.tenant_id AND b.enabled=TRUE AND b.verified=TRUE AND b.emergency_stop=FALSE
                  AND b.account_domain=ki.account_domain AND b.lead_id=j.lead_id AND b.talk_id=j.talk_id
                  AND b.chat_id=j.chat_id AND b.channel_id=j.channel_id
                  AND b.contact_id=CASE WHEN e.payload->>'contact_id' ~ '^[0-9]{1,18}$' THEN (e.payload->>'contact_id')::bigint END) AS test_mode_allowed
              FROM saas_ai_agent_jobs j JOIN saas_ai_agents a ON a.tenant_id=j.tenant_id AND a.id=j.agent_id
              JOIN saas_ai_agent_settings s ON s.tenant_id=j.tenant_id
              JOIN saas_tenant_integrations i ON i.tenant_id=j.tenant_id AND i.provider='chat_ai'
              JOIN saas_tenants t ON t.id=j.tenant_id
              LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=j.tenant_id AND cs.provider=j.provider AND cs.conversation_id=j.conversation_id
              WHERE j.tenant_id=%s::uuid AND j.agent_id=%s::uuid AND j.provider=%s AND j.conversation_id=%s
                AND j.state='processing' AND j.lease_token=%s AND j.revision=%s''',
              (job['tenant_id'],job['agent_id'],job['provider'],job['conversation_id'],job['lease_token'],job['revision']))
            row = cur.fetchone()
    reason = None
    if not row:
        reason = 'config_row_missing'
    elif not tenant_status_allows_ai(row['tenant_status'], job.get('provider')):
        reason = 'tenant_inactive'
    elif row['agent_enabled'] is not True:
        reason = 'agent_disabled'
    elif row['settings_enabled'] is not True:
        reason = 'company_ai_disabled'
    elif row.get('conversation_state') in {'human', 'paused'}:
        reason = 'conversation_paused_or_human'
    elif row.get('emergency_stop') is True:
        reason = 'emergency_stopped'
    elif row.get('operating_mode') != 'active' and row.get('test_mode_allowed') is not True:
        reason = 'test_binding_mismatch'
    if reason:
        return (None, reason) if with_block_reason else None
    config = row['metadata'] if isinstance(row['metadata'],dict) else {}
    if config.get('enabled') is not True:
        reason = 'chat_ai_disabled'
    elif config.get('consent') is not True:
        reason = 'chat_ai_consent_missing'
    if reason:
        return (None, reason) if with_block_reason else None
    result = {'scopes':row['scopes'],'agent_model':str(row['agent_model'] or ''),'safety':row.get('safety') or {},
      'chat_model':str(config.get('model') or ''),'schedule':row['schedule'],'mode':row['mode'],
      'crm_actions_enabled':row['crm_actions_enabled'] is True}
    return (result, None) if with_block_reason else result


def defer_job(job, due_at):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_ai_agent_jobs SET state='queued',due_at=%s,lease_token=NULL,lease_until=NULL,
              attempts=GREATEST(attempts-1,0),updated_at=now() WHERE tenant_id=%s::uuid AND agent_id=%s::uuid
              AND provider=%s AND conversation_id=%s AND state='processing' AND lease_token=%s AND revision=%s''',
              (due_at,job['tenant_id'],job['agent_id'],job['provider'],job['conversation_id'],job['lease_token'],job['revision']))
            cur.execute("UPDATE saas_tenant_webhook_events SET state='queued' WHERE tenant_id=%s::uuid AND fingerprint=%s",
                        (job['tenant_id'],job['event_fingerprint']))
        conn.commit()


def skip_job(job, error_code):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_ai_agent_jobs SET state='skipped',lease_token=NULL,lease_until=NULL,error_code=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider=%s AND conversation_id=%s
                AND state='processing' AND lease_token=%s AND revision=%s''',
              (str(error_code)[:80],job['tenant_id'],job['agent_id'],job['provider'],job['conversation_id'],job['lease_token'],job['revision']))
            cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',error_code=%s,completed_at=now() WHERE tenant_id=%s::uuid AND fingerprint=%s",
                        (str(error_code)[:80],job['tenant_id'],job['event_fingerprint']))
        conn.commit()


def knowledge_context(tenant_id, query, *, limit=6):
    query = str(query or '').strip()[:1000]
    if not query:
        return []
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT s.id AS source_id,s.title,c.content,
              ts_rank(c.search_vector,plainto_tsquery('simple',%s)) AS rank
              FROM saas_ai_knowledge_chunks c JOIN saas_ai_knowledge_sources s
                ON s.tenant_id=c.tenant_id AND s.id=c.source_id
              WHERE c.tenant_id=%s::uuid AND s.active=TRUE
                AND (c.search_vector @@ plainto_tsquery('simple',%s) OR c.content ILIKE %s)
              ORDER BY rank DESC,s.updated_at DESC,c.chunk_no LIMIT %s''',
              (query,str(tenant_id),query,'%'+query[:120]+'%',max(1,min(int(limit),8))))
            return [{'source_id':str(row['source_id']),'title':str(row['title']),
                     'content':str(row['content'])[:1800]} for row in cur.fetchall()]


def reserve_run(job, *, pipeline_id, stage_id, model):
    """Reserve one shared Chat AI daily call before contacting the provider."""
    from tenant_ai_agent_test_mode import assert_job_allowed
    assert_job_allowed(job)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant = str(job['tenant_id'])
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tenant-chat-ai:' + tenant,))
            cur.execute('''SELECT j.*,a.name,a.instructions,a.enabled AS agent_enabled,a.mode,a.version AS agent_version,a.scopes,a.language,a.style,
              a.model AS configured_model,a.safety,s.company_instructions,s.enabled AS settings_enabled,s.operating_mode,s.emergency_stop,
              s.auto_send_enabled,s.crm_actions_enabled,s.max_replies_per_chat,s.updated_at AS settings_version,
              cs.state AS conversation_state,cs.revision AS conversation_revision,
              i.secrets,i.metadata,i.updated_at AS config_version,t.status AS tenant_status
              FROM saas_ai_agent_jobs j JOIN saas_ai_agents a ON a.tenant_id=j.tenant_id AND a.id=j.agent_id
              JOIN saas_ai_agent_settings s ON s.tenant_id=j.tenant_id
              JOIN saas_tenant_integrations i ON i.tenant_id=j.tenant_id AND i.provider='chat_ai'
              JOIN saas_tenants t ON t.id=j.tenant_id
              LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=j.tenant_id AND cs.provider=j.provider AND cs.conversation_id=j.conversation_id
              WHERE j.tenant_id=%s::uuid AND j.agent_id=%s::uuid AND j.provider=%s AND j.conversation_id=%s
              AND j.state='processing' AND j.lease_token=%s AND j.revision=%s
              FOR UPDATE OF j,a,s,i''', (tenant,job['agent_id'],job['provider'],job['conversation_id'],job['lease_token'],job['revision']))
            row = cur.fetchone()
            if not row:
                raise TenantLinearError('Agent işi yenilənib.', 409)
            config = row['metadata'] if isinstance(row.get('metadata'), dict) else {}
            if (not tenant_status_allows_ai(row['tenant_status'],job.get('provider')) or row['settings_enabled'] is not True or row.get('emergency_stop') is True or row['agent_enabled'] is not True
                    or row['conversation_state'] in {'human','paused'}
                    or config.get('enabled') is not True or config.get('consent') is not True or not row.get('secrets')):
                raise TenantLinearError('AI Agents və mövcud Chat AI ayarları aktiv deyil.', 409)
            allowed_models = [str(item).strip() for item in config.get('models', []) if isinstance(item, str) and item.strip()]
            configured_model = str(config.get('model') or '').strip()
            if configured_model and configured_model not in allowed_models:
                allowed_models.append(configured_model)
            if str(model) not in allowed_models or (row.get('configured_model') and row['configured_model'] != model):
                raise TenantLinearError('Agent modeli Chat AI ayarları ilə uyğun deyil.', 409)
            scopes = row['scopes'] if isinstance(row['scopes'], list) else []
            provider = str(job.get('provider') or 'kommo')
            scope = next((item for item in scopes if isinstance(item, dict)
                and str(item.get('provider') or 'kommo') == provider
                and (provider == 'connect' or (str(item.get('pipeline_id')) == str(pipeline_id)
                    and str(item.get('stage_id')) == str(stage_id)))
                and (not item.get('channel_id') or str(item['channel_id']).casefold()==str(job.get('channel_id') or '').casefold())), None)
            if not scope:
                raise TenantLinearError('Deal artıq agentin əhatə dairəsində deyil.', 409)
            cur.execute('''SELECT id FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid AND agent_id=%s::uuid
              AND event_fingerprint=%s AND revision=%s''', (tenant,job['agent_id'],job['event_fingerprint'],job['revision']))
            if cur.fetchone():
                raise TenantLinearError('Bu hadisə üçün AI sorğusu artıq başlanıb.', 409)
            try:
                secret = json.loads(_fernet().decrypt(bytes(row['secrets'])))
                if secret.get('tenant_id') != tenant or secret.get('provider') != 'chat_ai' or not secret.get('api_key'):
                    raise ValueError()
            except Exception:
                raise TenantLinearError('AI təhlükəsizlik ayarlarını yoxlayın.', 503) from None
            limit = int(config.get('daily_limit') or 50)
            cur.execute('''SELECT COALESCE(sum(reserved_calls),0) AS calls FROM saas_chat_ai_runs
              WHERE tenant_id=%s::uuid AND usage_day=(now() AT TIME ZONE 'UTC')::date''', (tenant,))
            existing = int((cur.fetchone() or {}).get('calls') or 0)
            cur.execute('''SELECT count(*) AS calls FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid
              AND created_at>=(now() AT TIME ZONE 'UTC')::date AT TIME ZONE 'UTC'
              AND state IN ('generating','completed','unknown')''', (tenant,))
            existing += int((cur.fetchone() or {}).get('calls') or 0)
            if existing + 1 > limit:
                raise TenantLinearError('Şirkətin gündəlik Chat AI limiti dolub.', 429)
            run_id = uuid.uuid4()
            cur.execute('''INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state)
              VALUES(%s::uuid,%s,%s::uuid,%s,%s,%s,'generating')''',
              (tenant,run_id,job['agent_id'],job['event_fingerprint'],job['revision'],model))
            cur.execute("UPDATE saas_tenant_webhook_events SET state='processing',attempts=attempts+1 WHERE tenant_id=%s::uuid AND fingerprint=%s",
                        (tenant,job['event_fingerprint']))
            cur.execute('''INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,agent_id,state,first_inbound_message_id,last_inbound_message_id)
              VALUES(%s::uuid,%s,%s,%s::uuid,'active',%s,%s)
              ON CONFLICT(tenant_id,provider,conversation_id) DO UPDATE SET agent_id=EXCLUDED.agent_id,
                last_inbound_message_id=EXCLUDED.last_inbound_message_id,updated_at=now()
              WHERE saas_ai_conversation_state.state='active' RETURNING revision''',
              (tenant,job['provider'],job['conversation_id'],job['agent_id'],str((job.get('payload') or {}).get('message_id') or ''),str((job.get('payload') or {}).get('message_id') or '')))
            conversation = cur.fetchone()
            result = {'run_id':str(run_id),'api_key':secret['api_key'],'config':config,
              'config_version':str(row['config_version']),'company_instructions':row['company_instructions'],
              'settings_version':str(row['settings_version']),
              'agent_name':row['name'],'agent_instructions':row['instructions'],'agent_language':row['language'],
              'agent_style':row['style'],'agent_safety':row.get('safety') or {},'scope_instructions':str(scope.get('instructions') or ''),
              'agent_version':row['agent_version'],'conversation_revision':conversation.get('revision') if conversation else row.get('conversation_revision'),
              'mode':row['mode'],'auto_send_enabled':row['auto_send_enabled'],'crm_actions_enabled':row['crm_actions_enabled'],
              'max_replies_per_chat':row['max_replies_per_chat'],
              'source_message_id':str((job.get('payload') or {}).get('message_id') or ''),
              'model':model,'pipeline_id':str(pipeline_id),'stage_id':str(stage_id)}
        conn.commit()
    return result


def complete_run(job, reservation, draft, source_ids, *, stop_after_reply=False):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT j.revision,j.lease_token,j.state,a.enabled AS agent_enabled,a.version AS agent_version,s.enabled AS settings_enabled,s.operating_mode,s.emergency_stop,s.updated_at AS settings_version,
              i.updated_at AS config_version,i.metadata,t.status AS tenant_status,
              (j.provider='connect' AND EXISTS (SELECT 1 FROM saas_ai_agent_connect_test_mode cb JOIN saas_tenant_webhook_events ce
                ON ce.tenant_id=j.tenant_id AND ce.fingerprint=j.event_fingerprint WHERE cb.tenant_id=j.tenant_id AND cb.enabled=TRUE
                AND cb.verified=TRUE AND cb.emergency_stop=FALSE AND cb.channel_id=j.channel_id AND cb.conversation_id=j.conversation_id
                AND ce.payload->>'test_mode'='true')) OR EXISTS (SELECT 1 FROM saas_ai_agent_test_mode b JOIN saas_tenant_integrations ki
                ON ki.tenant_id=b.tenant_id AND ki.provider='kommo' AND ki.status='connected'
                JOIN saas_tenant_webhook_events e ON e.tenant_id=j.tenant_id AND e.fingerprint=j.event_fingerprint
                WHERE b.tenant_id=j.tenant_id AND b.enabled=TRUE AND b.verified=TRUE AND b.emergency_stop=FALSE
                  AND b.account_domain=ki.account_domain AND b.lead_id=j.lead_id AND b.talk_id=j.talk_id
                  AND b.chat_id=j.chat_id AND b.channel_id=j.channel_id
                  AND b.contact_id=CASE WHEN e.payload->>'contact_id' ~ '^[0-9]{1,18}$' THEN (e.payload->>'contact_id')::bigint END) AS test_mode_allowed,
              cs.state AS conversation_state,cs.revision AS conversation_revision
              FROM saas_ai_agent_jobs j JOIN saas_ai_agents a ON a.tenant_id=j.tenant_id AND a.id=j.agent_id
              JOIN saas_ai_agent_settings s ON s.tenant_id=j.tenant_id
              JOIN saas_tenant_integrations i ON i.tenant_id=j.tenant_id AND i.provider='chat_ai'
              JOIN saas_tenants t ON t.id=j.tenant_id
              LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=j.tenant_id AND cs.provider=j.provider AND cs.conversation_id=j.conversation_id
              WHERE j.tenant_id=%s::uuid AND j.agent_id=%s::uuid AND j.provider=%s AND j.conversation_id=%s
              FOR UPDATE OF j,a,s,i''', (job['tenant_id'],job['agent_id'],job['provider'],job['conversation_id']))
            current = cur.fetchone()
            config = current.get('metadata') if current and isinstance(current.get('metadata'),dict) else {}
            if (not current or current['state']!='processing' or current['lease_token']!=job['lease_token']
                    or current['revision']!=job['revision'] or current['agent_enabled'] is not True
                    or current['agent_version']!=reservation['agent_version'] or current['settings_enabled'] is not True
                    or str(current['settings_version'])!=reservation['settings_version']
                    or current['conversation_state'] in {'human','paused'}
                    or current['conversation_revision']!=reservation.get('conversation_revision')
                    or not tenant_status_allows_ai(current['tenant_status'],job.get('provider')) or current.get('emergency_stop') is True
                    or (current.get('operating_mode')!='active' and current.get('test_mode_allowed') is not True)
                    or str(current['config_version'])!=reservation['config_version']
                    or config.get('consent') is not True):
                cur.execute("UPDATE saas_ai_agent_runs SET state='superseded',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='generating'",
                            (job['tenant_id'],reservation['run_id']))
                conn.commit()
                return False
            cur.execute('''UPDATE saas_ai_agent_runs SET state='completed',result=%s::jsonb,updated_at=now()
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='generating' ''',
              (_json({'source_ids':source_ids}),job['tenant_id'],reservation['run_id']))
            draft_id = uuid.uuid4()
            payload = job.get('payload') or {}
            cur.execute('''INSERT INTO saas_ai_agent_drafts
              (tenant_id,id,agent_id,run_id,lead_id,talk_id,chat_id,source_message_id,source_fingerprint,draft,source_ids,handoff_after_send)
              VALUES(%s::uuid,%s,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)''',
              (job['tenant_id'],draft_id,job['agent_id'],reservation['run_id'],job['lead_id'],job['talk_id'],job['chat_id'],
               str(payload.get('message_id') or ''),job['event_fingerprint'],draft,_json(source_ids),bool(stop_after_reply)))
            cur.execute('''UPDATE saas_ai_agent_jobs SET state='drafted',lease_token=NULL,lease_until=NULL,error_code='',updated_at=now()
              WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider=%s AND conversation_id=%s
                AND lease_token=%s AND revision=%s''',
              (job['tenant_id'],job['agent_id'],job['provider'],job['conversation_id'],job['lease_token'],job['revision']))
            cur.execute("UPDATE saas_tenant_webhook_events SET state='completed',completed_at=now(),error_code='' WHERE tenant_id=%s::uuid AND fingerprint=%s",
                        (job['tenant_id'],job['event_fingerprint']))
        conn.commit()
    return True


def finish_unknown(job, reservation, error_code='provider_result_unknown'):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_ai_agent_runs SET state='unknown',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='generating'",
                        (job['tenant_id'],reservation['run_id']))
            cur.execute('''UPDATE saas_ai_agent_jobs SET state='unknown',lease_token=NULL,lease_until=NULL,error_code=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider=%s AND conversation_id=%s
                AND lease_token=%s AND revision=%s''',
              (str(error_code)[:80],job['tenant_id'],job['agent_id'],job['provider'],job['conversation_id'],job['lease_token'],job['revision']))
            cur.execute("UPDATE saas_tenant_webhook_events SET state='unknown',error_code=%s WHERE tenant_id=%s::uuid AND fingerprint=%s",
                        (str(error_code)[:80],job['tenant_id'],job['event_fingerprint']))
        conn.commit()


def finish_superseded(job,reservation,error_code='conversation_changed'):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_ai_agent_runs SET state='superseded',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='generating'",(job['tenant_id'],reservation['run_id']))
            cur.execute('''UPDATE saas_ai_agent_jobs SET state='skipped',lease_token=NULL,lease_until=NULL,error_code=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider=%s AND conversation_id=%s AND lease_token=%s AND revision=%s''',
              (error_code,job['tenant_id'],job['agent_id'],job['provider'],job['conversation_id'],job['lease_token'],job['revision']))
            cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',error_code=%s,completed_at=now() WHERE tenant_id=%s::uuid AND fingerprint=%s",(error_code,job['tenant_id'],job['event_fingerprint']))
        conn.commit()


def finish_sent(job, reservation, message_id: str) -> bool:
    """Atomically close the AI run only after Connect accepted the message."""
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_ai_agent_runs SET state='completed',result=%s::jsonb,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='generating'",
                        (_json({'send_state':'accepted','message_id':str(message_id)}),job['tenant_id'],reservation['run_id']))
            cur.execute("UPDATE saas_ai_agent_jobs SET state='sent',lease_token=NULL,lease_until=NULL,error_code='',updated_at=now() WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider='connect' AND conversation_id=%s AND lease_token=%s AND revision=%s",
                        (job['tenant_id'],job['agent_id'],job['conversation_id'],job['lease_token'],job['revision']))
            cur.execute("UPDATE saas_tenant_webhook_events SET state='completed',completed_at=now(),error_code='' WHERE tenant_id=%s::uuid AND fingerprint=%s",
                        (job['tenant_id'],job['event_fingerprint']))
        conn.commit()
    return True
