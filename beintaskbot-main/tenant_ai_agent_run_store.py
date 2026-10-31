"""Lease, budget and draft receipts for autonomous manual-mode preparation."""
from tenant_ai_testing_budget import TOKEN_USAGE_SQL
import json
import os
import uuid
from tenant_ai_dialog_scope import job_scope, lock_dialog, assert_resolved

from tenant_ai_agents_schema import ensure_schema
from tenant_ai_agent_access import tenant_status_allows_ai
from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect, _fernet, _json
from tenant_ai_agent_key_policy import (choose_api_credential, platform_key_ready,
    platform_model, platform_model_allowed, platform_service_ready,
    platform_token_limit, skip_pre_activation_jobs)


def claim_next():
    from tenant_ai_agent_runtime_flags import enabled as runtime_flag
    from tenant_ai_pilot import configured_scope,scoped_live_enabled
    if not runtime_flag('AI_AGENTS_ENABLED') or (not runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED')
            and not scoped_live_enabled()):
        return None
    conn = _connect()
    try:
        ensure_schema(conn)
        with conn.cursor() as cur:
            from tenant_ai_reliability import recover_terminal_jobs
            recover_terminal_jobs(cur)
            # Never replay Connect jobs received before an explicit key cutover.
            skip_pre_activation_jobs(cur)
            cur.execute('''SELECT j.tenant_id,j.agent_id,j.provider,j.channel_id,j.conversation_id,j.revision,j.event_fingerprint
              FROM saas_ai_agent_jobs j WHERE j.state='processing' AND j.lease_until<=now()
              AND NOT EXISTS (SELECT 1 FROM saas_ai_agent_runs r WHERE r.tenant_id=j.tenant_id
                AND r.agent_id=j.agent_id AND r.event_fingerprint=j.event_fingerprint AND r.revision=j.revision)
              FOR UPDATE OF j SKIP LOCKED''')
            abandoned = cur.fetchall()
            for row in abandoned:
                cur.execute('''UPDATE saas_ai_agent_jobs SET state=CASE WHEN attempts>=5 THEN 'failed' ELSE 'queued' END,
                  lease_token=NULL,lease_until=NULL,due_at=now(),error_code='worker_interrupted',updated_at=now()
                  WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider=%s AND conversation_id=%s AND scope_channel_id=%s''',
                  (row['tenant_id'],row['agent_id'],row['provider'],row['conversation_id'],job_scope(row)))
                cur.execute("UPDATE saas_tenant_webhook_events SET state=CASE WHEN EXISTS (SELECT 1 FROM saas_ai_agent_jobs j WHERE j.tenant_id=saas_tenant_webhook_events.tenant_id AND j.event_fingerprint=saas_tenant_webhook_events.fingerprint AND j.state='failed') THEN 'failed' ELSE 'queued' END,error_code='worker_interrupted' WHERE tenant_id=%s::uuid AND fingerprint=%s",
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
            if scoped_live_enabled():
                cur.execute("SELECT to_regclass('saas_ai_pilot_followups') IS NOT NULL AS available")
                if (cur.fetchone() or {}).get('available'):
                    from tenant_consultation_group import materialize_pilot_followup
                    materialize_pilot_followup(cur)
            pilot_scope=configured_scope() if scoped_live_enabled() else None
            pilot_tenant=(pilot_scope or {}).get('tenant_id','00000000-0000-0000-0000-000000000000')
            pilot_channel=(pilot_scope or {}).get('channel_id','')
            pilot_conversation=(pilot_scope or {}).get('conversation_id','')
            pilot_enabled=bool(pilot_scope and scoped_live_enabled())
            cur.execute('''SELECT j.* FROM saas_ai_agent_jobs j
              JOIN saas_ai_agents a ON a.tenant_id=j.tenant_id AND a.id=j.agent_id AND a.archived_at IS NULL AND a.enabled=TRUE AND a.role='primary'
              JOIN saas_ai_agent_settings cfg ON cfg.tenant_id=j.tenant_id AND cfg.enabled=TRUE AND cfg.emergency_stop=FALSE
              JOIN saas_tenants t ON t.id=j.tenant_id
              WHERE j.state='queued' AND j.due_at<=now()
              AND ((a.mode='automatic' AND cfg.auto_send_enabled=TRUE)
                OR (%s AND j.provider='connect' AND j.tenant_id=%s::uuid
                  AND j.channel_id=%s AND j.conversation_id=%s AND a.mode='manual'))
              AND (j.provider<>'connect' OR NOT ai_legacy_blocked(j.tenant_id,j.channel_id,j.conversation_id,j.event_fingerprint))
              AND (t.status='active' OR (j.provider='connect' AND t.status IN ('onboarding','ready_for_integration')))
              ORDER BY j.due_at,j.updated_at LIMIT 1 FOR UPDATE OF j SKIP LOCKED''',
              (pilot_enabled,pilot_tenant,pilot_channel,pilot_conversation))
            row = cur.fetchone()
            if not row:
                conn.commit()
                return None
            token = uuid.uuid4()
            cur.execute('''UPDATE saas_ai_agent_jobs SET state='processing',lease_token=%s,lease_until=now()+interval '3 minutes',
              attempts=attempts+1,updated_at=now() WHERE tenant_id=%s::uuid AND agent_id=%s::uuid
              AND provider=%s AND conversation_id=%s AND scope_channel_id=%s RETURNING *''',
              (token,row['tenant_id'],row['agent_id'],row['provider'],row['conversation_id'],job_scope(row)))
            claimed = dict(cur.fetchone())
            cur.execute('''SELECT payload FROM saas_tenant_webhook_events
              WHERE tenant_id=%s::uuid AND fingerprint=%s FOR UPDATE''',
              (claimed['tenant_id'],claimed['event_fingerprint']))
            event = cur.fetchone()
            if not event:
                cur.execute('''UPDATE saas_ai_agent_jobs SET state='failed',error_code='event_missing',updated_at=now()
                  WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider=%s AND conversation_id=%s AND scope_channel_id=%s''',
                  (claimed['tenant_id'],claimed['agent_id'],claimed['provider'],claimed['conversation_id'],job_scope(claimed)))
                conn.commit()
                return None
            claimed['payload'] = event['payload'] if isinstance(event['payload'], dict) else {}
            from tenant_ai_pilot import processing_allowed
            if not processing_allowed(claimed, connection=conn):
                cur.execute("UPDATE saas_ai_agent_jobs SET state='skipped',lease_token=NULL,lease_until=NULL,error_code='pilot_scope_blocked',updated_at=now() WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider=%s AND conversation_id=%s AND scope_channel_id=%s AND lease_token=%s",
                    (claimed['tenant_id'],claimed['agent_id'],claimed['provider'],claimed['conversation_id'],job_scope(claimed),claimed['lease_token']))
                cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',completed_at=now(),error_code='pilot_scope_blocked' WHERE tenant_id=%s::uuid AND fingerprint=%s",
                    (claimed['tenant_id'],claimed['event_fingerprint']))
                conn.commit()
                return None
            cur.execute("UPDATE saas_tenant_webhook_events SET state='processing' WHERE tenant_id=%s::uuid AND fingerprint=%s AND state='queued'", (claimed['tenant_id'],claimed['event_fingerprint']))
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
              WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider=%s AND conversation_id=%s AND scope_channel_id=%s
                AND state='processing' AND lease_token=%s AND revision=%s
                AND NOT EXISTS (SELECT 1 FROM saas_ai_agent_runs r WHERE r.tenant_id=saas_ai_agent_jobs.tenant_id
                  AND r.agent_id=saas_ai_agent_jobs.agent_id AND r.event_fingerprint=saas_ai_agent_jobs.event_fingerprint
                  AND r.revision=saas_ai_agent_jobs.revision) RETURNING state''',
              (str(error_code)[:80],job['tenant_id'],job['agent_id'],job['provider'],job['conversation_id'],job_scope(job),job['lease_token'],job['revision']))
            changed=cur.fetchone()
            if not changed:
                conn.commit()
                return False
            cur.execute('''UPDATE saas_tenant_webhook_events SET state=CASE WHEN %s THEN 'failed' ELSE 'queued' END,
              error_code=%s WHERE tenant_id=%s::uuid AND fingerprint=%s''',
              (changed['state']=='failed',str(error_code)[:80],job['tenant_id'],job['event_fingerprint']))
        conn.commit()


def load_agent_config(job, *, with_block_reason=False):
    from tenant_ai_pilot import manual_live_job
    pilot_live=manual_live_job(job)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT a.role,a.enabled AS agent_enabled,a.scopes,a.model AS agent_model,a.safety,a.response_policy,a.schedule,a.mode,
              s.enabled AS settings_enabled,s.operating_mode,s.auto_send_enabled,s.emergency_stop,s.crm_actions_enabled,s.native_crm_actions_enabled,i.metadata,i.secrets,t.status AS tenant_status,cs.state AS conversation_state
              FROM saas_ai_agent_jobs j JOIN saas_ai_agents a ON a.tenant_id=j.tenant_id AND a.id=j.agent_id
              JOIN saas_ai_agent_settings s ON s.tenant_id=j.tenant_id
              LEFT JOIN saas_tenant_integrations i ON i.tenant_id=j.tenant_id AND i.provider='chat_ai'
              JOIN saas_tenants t ON t.id=j.tenant_id
              LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=j.tenant_id AND cs.provider=j.provider AND cs.conversation_id=j.conversation_id AND cs.channel_id=j.scope_channel_id
              WHERE j.tenant_id=%s::uuid AND j.agent_id=%s::uuid AND j.provider=%s AND j.conversation_id=%s AND j.scope_channel_id=%s
                AND j.state='processing' AND j.lease_token=%s AND j.revision=%s''',
              (job['tenant_id'],job['agent_id'],job['provider'],job['conversation_id'],job_scope(job),job['lease_token'],job['revision']))
            row = cur.fetchone()
    reason = None
    if not row:
        reason = 'config_row_missing'
    elif not tenant_status_allows_ai(row['tenant_status'], job.get('provider')):
        reason = 'tenant_inactive'
    elif row.get('role','primary')!='primary' or row['agent_enabled'] is not True:
        reason = 'agent_disabled'
    elif row['settings_enabled'] is not True:
        reason = 'company_ai_disabled'
    elif not pilot_live and row.get('operating_mode') not in (None,'active'):
        reason = 'company_mode_not_active'
    elif row.get('mode') != 'automatic' and not (pilot_live and row.get('mode')=='manual'):
        reason = 'manual_mode_requires_operator_request'
    elif row.get('auto_send_enabled') is not True and not pilot_live:
        reason = 'auto_send_disabled'
    elif row.get('conversation_state') in {'human', 'paused'}:
        reason = 'conversation_paused_or_human'
    elif row.get('emergency_stop') is True:
        reason = 'emergency_stopped'
    if reason:
        return (None, reason) if with_block_reason else None
    selected_model = str(row['agent_model'] or '')
    config = row['metadata'] if isinstance(row['metadata'],dict) else {}
    if not selected_model and platform_service_ready():
        selected_model = platform_model()
    if not selected_model:
        selected_model = str(config.get('model') or '')
    use_platform = platform_key_ready(job['tenant_id'], job['agent_id'], job.get('provider')) and (
        not row.get('secrets') or platform_model_allowed(selected_model))
    if not use_platform:
        if config.get('enabled') is not True:
            reason = 'chat_ai_disabled'
        elif config.get('consent') is not True:
            reason = 'chat_ai_consent_missing'
        elif not row.get('secrets'):
            reason = 'chat_ai_key_missing'
    if reason:
        return (None, reason) if with_block_reason else None
    result = {'scopes':row['scopes'],'agent_model':selected_model,'safety':row.get('safety') or {},
      'response_policy':row.get('response_policy') or {},
      'chat_model':str(config.get('model') or ''),'schedule':row['schedule'],'mode':row['mode'],
      'crm_actions_enabled':row['crm_actions_enabled'] is True,
      'native_crm_actions_enabled':row.get('native_crm_actions_enabled') is True,
      'mode':'automatic' if pilot_live else row['mode'],
      'auto_send_enabled':True if pilot_live else row.get('auto_send_enabled') is True}
    return (result, None) if with_block_reason else result


def defer_job(job, due_at, reservation=None):
    """Return a leased job to the queue, optionally invalidating its AI run.

    A schedule may close after ``reserve_run`` has already created a
    ``generating`` receipt.  In that case the receipt must be terminalized in
    the same transaction as the requeue; otherwise the next worker can see an
    in-flight run and incorrectly treat the event as already consumed.
    """
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            if reservation is not None:
                run_id = str((reservation or {}).get('run_id') or '')
                if not run_id:
                    raise ValueError('A reserved AI run requires run_id.')
                cur.execute('''UPDATE saas_ai_agent_runs SET state='superseded',updated_at=now()
                  WHERE tenant_id=%s::uuid AND id=%s::uuid AND agent_id=%s::uuid
                    AND event_fingerprint=%s AND revision=%s AND state='generating'
                  RETURNING id''',
                  (job['tenant_id'], run_id, job['agent_id'], job['event_fingerprint'], job['revision']))
                # A stale or already-finalized reservation must never reopen a
                # job that another worker has taken over.
                if not cur.fetchone():
                    conn.commit()
                    return False
            # A run receipt is unique per revision. Once a reservation has
            # been superseded, retry it as a new revision rather than colliding
            # with that terminal receipt on the next reserve_run call.
            revision_bump = ',revision=revision+1' if reservation is not None else ''
            requeue_sql = '''UPDATE saas_ai_agent_jobs SET state='queued',due_at=%s,lease_token=NULL,lease_until=NULL,
              attempts=GREATEST(attempts-1,0),error_code='outside_schedule',updated_at=now()''' + revision_bump + ''' WHERE tenant_id=%s::uuid AND agent_id=%s::uuid
              AND provider=%s AND conversation_id=%s AND scope_channel_id=%s AND state='processing' AND lease_token=%s AND revision=%s'''
            cur.execute(requeue_sql + ' RETURNING 1',
              (due_at,job['tenant_id'],job['agent_id'],job['provider'],job['conversation_id'],job_scope(job),job['lease_token'],job['revision']))
            if not cur.fetchone():
                # The run is already terminalized above, but a newer lease owns
                # the job/event.  Do not reopen that newer worker's event.
                conn.commit()
                return False
            cur.execute("UPDATE saas_tenant_webhook_events SET state='queued',error_code='outside_schedule' WHERE tenant_id=%s::uuid AND fingerprint=%s",
                        (job['tenant_id'],job['event_fingerprint']))
        conn.commit()
    return True


def skip_job(job, error_code):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_ai_agent_jobs SET state='skipped',lease_token=NULL,lease_until=NULL,error_code=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider=%s AND conversation_id=%s AND scope_channel_id=%s
                AND state='processing' AND lease_token=%s AND revision=%s RETURNING state''',
              (str(error_code)[:80],job['tenant_id'],job['agent_id'],job['provider'],job['conversation_id'],job_scope(job),job['lease_token'],job['revision']))
            if not cur.fetchone():
                conn.commit()
                return False
            cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',error_code=%s,completed_at=now() WHERE tenant_id=%s::uuid AND fingerprint=%s",
                        (str(error_code)[:80],job['tenant_id'],job['event_fingerprint']))
        conn.commit()


def knowledge_context(tenant_id, query, *, limit=6, agent_id=None):
    query = str(query or '').strip()[:1000]
    if not query:
        return []
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            from tenant_knowledge_search import tsquery, patterns
            semantic_query=tsquery(query)
            semantic_patterns=patterns(query)
            cur.execute("SELECT to_regclass('saas_instruction_lessons') IS NOT NULL AS available")
            instruction_library_available=bool((cur.fetchone() or {}).get('available'))
            instruction_guard=(" AND (NOT (s.provenance ? 'instruction_id') OR EXISTS(SELECT 1 FROM saas_instruction_lessons l WHERE l.tenant_id=s.tenant_id AND l.id::text=s.provenance->>'instruction_id' AND l.state='published' AND l.published_doc->>'review_status'='approved' AND s.provenance->>'review_status'='approved' AND s.provenance->>'approved_by' IS NOT NULL))" if instruction_library_available else " AND NOT (s.provenance ? 'instruction_id')")
            cur.execute('''SELECT s.id AS source_id,s.title,c.content,
              ts_rank(c.search_vector,to_tsquery('simple',%s)) AS rank
              FROM saas_ai_knowledge_chunks c JOIN saas_ai_knowledge_sources s
                ON s.tenant_id=c.tenant_id AND s.id=c.source_id
               WHERE c.tenant_id=%s::uuid AND s.active=TRUE AND (s.agent_id IS NULL OR s.agent_id=%s::uuid)
                AND (NOT (s.provenance ? 'candidate_id') OR EXISTS(SELECT 1 FROM saas_knowledge_candidates k
                  JOIN saas_knowledge_candidate_sources ks ON ks.tenant_id=k.tenant_id AND ks.candidate_id=k.id
                    AND ks.source_id=s.id AND (ks.agent_id=s.agent_id OR s.agent_id IS NULL)
                  WHERE k.tenant_id=s.tenant_id AND k.id::text=s.provenance->>'candidate_id'
                    AND k.reviewed_by IS NOT NULL
                    AND k.reviewed_by::text=s.provenance->>'approved_by'
                    AND (k.status='approved' OR (k.status='knowledge_published'
                      AND s.provenance->>'review_status'='owner_reviewed_guidance'))))
              ''' + instruction_guard + '''
              AND (c.search_vector @@ to_tsquery('simple',%s) OR c.content ILIKE ANY(%s))
              ORDER BY rank DESC,s.updated_at DESC,c.chunk_no LIMIT %s''',
              (semantic_query,str(tenant_id),agent_id,semantic_query,semantic_patterns,max(1,min(int(limit),8))))
            return [{'source_id':str(row['source_id']),'title':str(row['title']),
                     'content':str(row['content'])[:1800]} for row in cur.fetchall()]


def video_reserved_calls(base_calls, *, response_policy_enabled, audio_enabled,
                         transcription_model, asr_tariff_available):
    """Bound a video run's daily reservation to its maximum call path."""
    if type(base_calls) is not int or not 1 <= base_calls <= 4:
        raise ValueError('Invalid AI call reservation')
    if response_policy_enabled:
        # The policy may issue a second reply generation; ASR is skipped for
        # this path so main + two frames + fallback remain capped at four.
        return max(base_calls, 4)
    calls=max(base_calls,3)  # main reply plus at most two sampled frames
    if audio_enabled and transcription_model=='whisper-1' and asr_tariff_available:
        calls=max(calls,4)
    return calls


def reserve_run(job, *, pipeline_id, stage_id, model, reserved_calls=1, video_requested=False):
    """Reserve shared Chat AI calls before contacting the provider."""
    if type(reserved_calls) is not int or not 1 <= reserved_calls <= 4:
        raise ValueError("Invalid AI call reservation")
    from tenant_ai_agent_admission import assert_job_allowed
    assert_job_allowed(job)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            lock_dialog(cur, job["tenant_id"], job["provider"], job.get("channel_id"), job["conversation_id"])
            if job["provider"] == "connect":
                assert_resolved(cur, job["tenant_id"], job["channel_id"], job["conversation_id"], job.get("event_fingerprint") or "")
            tenant = str(job['tenant_id'])
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tenant-chat-ai:' + tenant,))
            cur.execute('''SELECT j.*,a.role,a.name,a.instructions,a.enabled AS agent_enabled,a.mode,a.version AS agent_version,a.scopes,a.language,a.style,
              a.model AS configured_model,a.safety,a.response_policy,s.company_instructions,s.enabled AS settings_enabled,s.operating_mode,s.auto_send_enabled,s.emergency_stop,
              s.crm_actions_enabled,s.max_replies_per_chat,s.updated_at AS settings_version,
              cs.state AS conversation_state,cs.revision AS conversation_revision,
              i.secrets,i.metadata,i.updated_at AS config_version,t.status AS tenant_status
              FROM saas_ai_agent_jobs j JOIN saas_ai_agents a ON a.tenant_id=j.tenant_id AND a.id=j.agent_id
              JOIN saas_ai_agent_settings s ON s.tenant_id=j.tenant_id
              LEFT JOIN saas_tenant_integrations i ON i.tenant_id=j.tenant_id AND i.provider='chat_ai'
              JOIN saas_tenants t ON t.id=j.tenant_id
              LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=j.tenant_id AND cs.provider=j.provider AND cs.conversation_id=j.conversation_id AND cs.channel_id=j.scope_channel_id
              WHERE j.tenant_id=%s::uuid AND j.agent_id=%s::uuid AND j.provider=%s AND j.conversation_id=%s AND j.scope_channel_id=%s
              AND j.state='processing' AND j.lease_token=%s AND j.revision=%s
              -- `i` is nullable because Chat AI can be provided by the platform.
              -- PostgreSQL cannot lock that nullable relation.
              -- The tenant advisory lock above serializes Chat AI setting changes;
              -- the mutable job, agent and company-setting rows still need locks.
              FOR UPDATE OF j,a,s''', (tenant,job['agent_id'],job['provider'],job['conversation_id'],job_scope(job),job['lease_token'],job['revision']))
            row = cur.fetchone()
            if not row:
                raise TenantLinearError('Agent işi yenilənib.', 409)
            config = row['metadata'] if isinstance(row.get('metadata'), dict) else {}
            from tenant_ai_pilot import manual_live_job
            pilot_live=manual_live_job(job)
            configured_model = str(row.get('configured_model') or '')
            selected_model = str(model or configured_model or '')
            if not selected_model and platform_service_ready():
                selected_model = platform_model()
            if not selected_model:
                selected_model = str(config.get('model') or '')
            company_key = ''
            if row.get('secrets'):
                try:
                    secret = json.loads(_fernet().decrypt(bytes(row['secrets'])))
                    if secret.get('tenant_id') != tenant or secret.get('provider') != 'chat_ai' or not secret.get('api_key'):
                        raise ValueError()
                    company_key = str(secret['api_key'])
                except Exception:
                    raise TenantLinearError('AI təhlükəsizlik ayarlarını yoxlayın.', 503) from None
            api_key, credential_source = choose_api_credential(tenant, job['agent_id'], job.get('provider'), company_key, selected_model)
            use_platform_key = credential_source == 'platform'
            video_model='';video_api_key=''
            if (video_requested is True and job.get('provider')=='connect'
                    and config.get('video_enabled') is True and config.get('video_model')
                    and config.get('enabled') is True and config.get('consent') is True
                    and company_key and row.get('settings_enabled') is True):
                from tenant_ai_video_model import quote as video_quote
                from tenant_connect_images import supports_images
                candidate_video_model=str(config.get('video_model') or '')
                try:
                    video_quote(candidate_video_model,tenant)
                    if supports_images(candidate_video_model):
                        video_model=candidate_video_model
                        video_api_key=company_key
                        from tenant_ai_spend_budget import media_price
                        reserved_calls=video_reserved_calls(reserved_calls,
                            response_policy_enabled=(row.get('response_policy') or {}).get('enabled') is True,
                            audio_enabled=config.get('audio_enabled') is True,
                            transcription_model=config.get('transcription_model'),
                            asr_tariff_available=(media_price('whisper-1','asr') is not None))
                except TenantLinearError:
                    video_model='';video_api_key=''
            if (not tenant_status_allows_ai(row['tenant_status'],job.get('provider')) or row['settings_enabled'] is not True or (not pilot_live and row.get('operating_mode') not in (None,'active')) or row.get('emergency_stop') is True or row['agent_enabled'] is not True or row.get('role','primary')!='primary'
                    or (row.get('mode')=='manual' and not pilot_live)
                    or row['conversation_state'] in {'human','paused'}
                    or (not use_platform_key and (config.get('enabled') is not True or config.get('consent') is not True or not company_key))):
                raise TenantLinearError('AI Agents və mövcud Chat AI ayarları aktiv deyil.', 409)
            allowed_models = [str(item).strip() for item in config.get('models', []) if isinstance(item, str) and item.strip()]
            chat_model = str(config.get('model') or '').strip()
            if chat_model and chat_model not in allowed_models:
                allowed_models.append(chat_model)
            if use_platform_key:
                if not platform_model_allowed(selected_model):
                    raise TenantLinearError('Seçilmiş model platforma üçün təsdiqlənməyib.', 409)
            elif selected_model not in allowed_models or (configured_model and configured_model != selected_model):
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
            platform_cap = platform_token_limit(tenant) if use_platform_key else 0
            if platform_cap:
                cur.execute(TOKEN_USAGE_SQL, (tenant,tenant))
                if int((cur.fetchone() or {}).get('tokens') or 0) >= platform_cap:
                    raise TenantLinearError('Şirkətin platform AI token limiti dolub.', 429)
            limit = int(config.get('daily_limit') or 50)
            cur.execute('''SELECT COALESCE(sum(reserved_calls),0) AS calls FROM saas_chat_ai_runs
              WHERE tenant_id=%s::uuid AND usage_day=(now() AT TIME ZONE 'UTC')::date''', (tenant,))
            existing = int((cur.fetchone() or {}).get('calls') or 0)
            cur.execute('''SELECT COALESCE(sum(reserved_calls),0) AS calls FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid
              AND created_at>=(now() AT TIME ZONE 'UTC')::date AT TIME ZONE 'UTC'
              AND (state IN ('generating','completed','unknown') OR reserved_calls>1 OR generated_at IS NOT NULL)''', (tenant,))
            existing += int((cur.fetchone() or {}).get('calls') or 0)
            if existing + reserved_calls > limit:
                raise TenantLinearError('Şirkətin gündəlik Chat AI limiti dolub.', 429)
            run_id = uuid.uuid4()
            # The prompt is bounded by the worker contracts. Reserve against a
            # deliberately conservative upper input bound before crossing the
            # provider boundary; tenants without a server-owned monetary cap
            # preserve the established behaviour.
            from tenant_ai_spend_budget import reserve as reserve_spend
            spend_key='active:'+str(run_id)
            reserve_spend(cur, tenant, spend_key, 'active_primary', selected_model, 32768, 1800)
            cur.execute('''INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state,reserved_calls)
              VALUES(%s::uuid,%s,%s::uuid,%s,%s,%s,'generating',%s)''',
              (tenant,run_id,job['agent_id'],job['event_fingerprint'],job['revision'],selected_model,reserved_calls))
            cur.execute("UPDATE saas_tenant_webhook_events SET state='processing',attempts=attempts+1 WHERE tenant_id=%s::uuid AND fingerprint=%s",
                        (tenant,job['event_fingerprint']))
            cur.execute('''INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,channel_id,agent_id,state,first_inbound_message_id,last_inbound_message_id)
              VALUES(%s::uuid,%s,%s,%s,%s::uuid,'active',%s,%s)
              ON CONFLICT(tenant_id,provider,channel_id,conversation_id) DO UPDATE SET agent_id=EXCLUDED.agent_id,
                last_inbound_message_id=EXCLUDED.last_inbound_message_id,updated_at=now()
              WHERE saas_ai_conversation_state.state='active' RETURNING revision''',
              (tenant,job['provider'],job['conversation_id'],job_scope(job),job['agent_id'],str((job.get('payload') or {}).get('message_id') or ''),str((job.get('payload') or {}).get('message_id') or '')))
            conversation = cur.fetchone()
            if reserved_calls > 1:
                cur.execute("UPDATE saas_ai_agent_jobs SET lease_until=now()+(%s * interval '3 minutes') WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider=%s AND conversation_id=%s AND scope_channel_id=%s AND lease_token=%s AND revision=%s",
                            (reserved_calls,tenant,job['agent_id'],job['provider'],job['conversation_id'],job_scope(job),job['lease_token'],job['revision']))
            result = {'run_id':str(run_id),'api_key':api_key,'config':config,
              'config_version':str(row['config_version']),'company_instructions':row['company_instructions'],
              'settings_version':str(row['settings_version']),
              'agent_name':row['name'],'agent_instructions':row['instructions'],'agent_language':row['language'],
              'response_policy':row.get('response_policy') or {},
              'agent_style':row['style'],'agent_safety':row.get('safety') or {},'scope_instructions':str(scope.get('instructions') or ''),
              'agent_version':row['agent_version'],'conversation_revision':conversation.get('revision') if conversation else row.get('conversation_revision'),
              'mode':'automatic' if pilot_live else row['mode'],
              'auto_send_enabled':True if pilot_live else row['auto_send_enabled'],'crm_actions_enabled':row['crm_actions_enabled'],
              'max_replies_per_chat':row['max_replies_per_chat'],
              'source_message_id':str((job.get('payload') or {}).get('message_id') or ''),
              'model':selected_model,'credential_source':credential_source,'pipeline_id':str(pipeline_id),'stage_id':str(stage_id),
              'video_model':video_model,'video_api_key':video_api_key}
            result['spend_call_key']=spend_key
        conn.commit()
    return result


def record_token_usage(job, reservation, usage, *, cumulative=False, generated=False):
    """Persist provider-reported usage against the already reserved run.

    The browser never supplies these values.  A unique run receipt makes a
    retry idempotent and retains the tenant/agent/model boundary for audits.
    """
    values = usage if isinstance(usage, dict) else {}
    try:
        input_tokens = max(0, int(values.get('input_tokens') or 0))
        output_tokens = max(0, int(values.get('output_tokens') or 0))
        total_tokens = max(input_tokens + output_tokens, int(values.get('total_tokens') or 0))
    except (TypeError, ValueError):
        input_tokens = output_tokens = total_tokens = 0
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            conflict = 'DO NOTHING'
            if cumulative:
                # Audio is recorded first; the later cumulative receipt includes
                # audio + generation. Maxima make repeated writes idempotent.
                conflict = '''DO UPDATE SET
                  input_tokens=GREATEST(saas_ai_agent_token_usage.input_tokens,EXCLUDED.input_tokens),
                  output_tokens=GREATEST(saas_ai_agent_token_usage.output_tokens,EXCLUDED.output_tokens),
                  total_tokens=GREATEST(saas_ai_agent_token_usage.total_tokens,EXCLUDED.total_tokens)'''
            cur.execute('''INSERT INTO saas_ai_agent_token_usage
              (tenant_id,id,run_id,agent_id,model,input_tokens,output_tokens,total_tokens)
              VALUES(%s::uuid,%s,%s::uuid,%s::uuid,%s,%s,%s,%s)
              ON CONFLICT(tenant_id,run_id) ''' + conflict,
              (job['tenant_id'],uuid.uuid4(),reservation['run_id'],job['agent_id'],reservation['model'],
               input_tokens,output_tokens,total_tokens))
            if generated:
                cur.execute("UPDATE saas_ai_agent_runs SET generated_at=COALESCE(generated_at,now()) WHERE tenant_id=%s::uuid AND id=%s::uuid AND agent_id=%s::uuid",
                            (job['tenant_id'],reservation['run_id'],job['agent_id']))
        conn.commit()
    if generated and reservation.get('spend_call_key'):
        from tenant_ai_spend_budget import settle
        settle(job['tenant_id'],reservation['spend_call_key'],usage)




def finish_unknown(job, reservation, error_code='provider_result_unknown'):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_ai_agent_runs SET state='unknown',error_code=%s,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='generating'",
                        (str(error_code)[:80],job['tenant_id'],reservation['run_id']))
            cur.execute('''UPDATE saas_ai_agent_jobs SET state='unknown',lease_token=NULL,lease_until=NULL,error_code=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider=%s AND conversation_id=%s AND scope_channel_id=%s
                AND lease_token=%s AND revision=%s RETURNING state''',
              (str(error_code)[:80],job['tenant_id'],job['agent_id'],job['provider'],job['conversation_id'],job_scope(job),job['lease_token'],job['revision']))
            if not cur.fetchone():
                conn.commit()
                return False
            cur.execute("UPDATE saas_tenant_webhook_events SET state='unknown',error_code=%s WHERE tenant_id=%s::uuid AND fingerprint=%s",
                        (str(error_code)[:80],job['tenant_id'],job['event_fingerprint']))
        conn.commit()
    if reservation.get('spend_call_key'):
        from tenant_ai_spend_budget import settle
        settle(job['tenant_id'],reservation['spend_call_key'],{},unknown=True)


def record_pilot_initial_rejection(job, reservation, reason_code):
    """Persist an allowlisted parser diagnostic without changing send state/errors."""
    from tenant_ai_pilot_initial import PARSER_REJECTIONS
    code = str(reason_code or '')
    if code not in PARSER_REJECTIONS:
        code = 'invalid_schema'
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_ai_agent_runs SET result=
                COALESCE(result,'{}'::jsonb) || jsonb_build_object('pilot_initial_triage_rejection',%s::text),
                updated_at=now()
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='generating'
                AND EXISTS (SELECT 1 FROM saas_ai_agent_jobs j WHERE j.tenant_id=saas_ai_agent_runs.tenant_id
                  AND j.agent_id=saas_ai_agent_runs.agent_id AND j.provider=%s AND j.conversation_id=%s
                  AND j.scope_channel_id=%s AND j.lease_token=%s AND j.revision=%s
                  AND j.lease_until>now() AND j.state='processing') RETURNING id''',
              (code,job['tenant_id'],reservation['run_id'],job['provider'],job['conversation_id'],
               job_scope(job),job['lease_token'],job['revision']))
            saved = bool(cur.fetchone())
        conn.commit()
    return saved


def finish_superseded(job,reservation,error_code='conversation_changed'):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_ai_agent_runs SET state='superseded',error_code=%s,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='generating'",(str(error_code)[:80],job['tenant_id'],reservation['run_id']))
            cur.execute('''UPDATE saas_ai_agent_jobs SET state='skipped',lease_token=NULL,lease_until=NULL,error_code=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider=%s AND conversation_id=%s AND scope_channel_id=%s AND lease_token=%s AND revision=%s RETURNING state''',
              (error_code,job['tenant_id'],job['agent_id'],job['provider'],job['conversation_id'],job_scope(job),job['lease_token'],job['revision']))
            if not cur.fetchone():
                conn.commit()
                return False
            cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',error_code=%s,completed_at=now() WHERE tenant_id=%s::uuid AND fingerprint=%s",(error_code,job['tenant_id'],job['event_fingerprint']))
        conn.commit()
    if reservation.get('spend_call_key'):
        from tenant_ai_spend_budget import release
        release(job['tenant_id'],reservation['spend_call_key'])


def finish_sent(job, reservation, message_id: str) -> bool:
    """Atomically close the AI run only after Connect accepted the message."""
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_ai_agent_runs SET state='completed',result=COALESCE(result,'{}'::jsonb)||%s::jsonb,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='generating'",
                        (_json({'send_state':'accepted','message_id':str(message_id)}),job['tenant_id'],reservation['run_id']))
            cur.execute("UPDATE saas_ai_agent_jobs SET state='sent',lease_token=NULL,lease_until=NULL,error_code='',updated_at=now() WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider='connect' AND conversation_id=%s AND scope_channel_id=%s AND lease_token=%s AND revision=%s",
                        (job['tenant_id'],job['agent_id'],job['conversation_id'],job_scope(job),job['lease_token'],job['revision']))
            cur.execute("UPDATE saas_tenant_webhook_events SET state='completed',completed_at=now(),error_code='' WHERE tenant_id=%s::uuid AND fingerprint=%s",
                        (job['tenant_id'],job['event_fingerprint']))
        conn.commit()
    return True
