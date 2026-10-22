"""Read-only tenant diagnostics and conservative recovery of durable receipts.

No message bodies, prompts, keys, raw exceptions or provider responses leave
this module. An uncertain paid call/send is NEVER automatically replayed.
"""
import asyncio
from tenant_ai_dialog_scope import job_scope
import re
from tenant_platform import _connect, _json
from tenant_ai_agents_schema import ensure_schema
from tenant_ai_agent_runtime_flags import enabled

REASONS = {
    'manager_replied': 'AI не ответил, потому что сотрудник ответил вручную',
    'manager_pause': 'AI в диалоге приостановлен сотрудником',
    'human_handoff': 'Диалог передан сотруднику',
    'group_messages_not_allowed': 'Групповые сообщения не включены для этого канала агента',

    "legacy_channel_ambiguous": "Старое состояние диалога не привязано к каналу; требуется безопасная проверка администратором",
    'agent_disabled': 'Агент выключен', 'agent_not_found': 'Подходящий агент не найден',
    'company_ai_disabled': 'AI компании выключен', 'ai_agents_server_disabled': 'Worker выключен на сервере',
    'emergency_stop': 'Включена аварийная остановка', 'ai_emergency_stop': 'Включена аварийная остановка',
    'tenant_ai_unavailable': 'AI недоступен для компании',
    'outside_schedule': 'Вне расписания: задача ждёт следующей смены',
    'conversation_human_or_paused': 'Human Mode или Pause: требуется явный Resume',
    'conversation_human': 'Human Mode: отвечает сотрудник', 'conversation_paused': 'AI приостановлен',
    'connect_human_takeover': 'Ответ отменён после ручного ответа сотрудника',
    'manual_mode': 'Ответ создан как черновик: агент в Manual',
    'auto_send_disabled': 'Автоматическая отправка выключена',
    'ai_limit_reached': 'Достигнут лимит AI', 'voice_token_limit': 'Достигнут лимит AI',
    'connect_admission_blocked': 'Канал или настройки компании не разрешают обработку',
    'connect_policy_blocked': 'Проверка безопасности не разрешила обработку',
    'no_connect_scope': 'Канал не входит в область работы агента',
    'connect_channel_outside_agent_scope': 'Канал не входит в область работы агента',
    'worker_interrupted': 'Worker прерван до вызова AI; безопасное восстановление',
    'worker_stopped': 'Worker остановлен; результат уже начатого вызова неизвестен',
    'connect_worker_failed': 'Ошибка чтения контекста; повтор до вызова AI ограничен',
    'read_or_context_failed': 'Ошибка чтения контекста; повтор до вызова AI ограничен',
    'connect_configuration_unavailable': 'Настройки Connect временно недоступны',
    'ai_configuration_unavailable': 'Настройки AI временно недоступны',
    'ai_configuration_not_ready': 'Настройки AI не готовы',
    'connect_send_unknown': 'Connect: результат отправки неизвестен; повтор запрещён',
    'send_result_unknown': 'Результат отправки неизвестен; повтор запрещён',
    'provider_result_unknown': 'Результат вызова неизвестен; автоматического повтора нет',
    'generation_result_unknown': 'Результат модели неизвестен; автоматического повтора нет',
    'connect_generation_unknown': 'Ошибка модели или сохранения; автоматического повтора нет',
    'connect_result_unknown': 'Результат обработки неизвестен; автоматического повтора нет',
    'connect_send_or_generation_unknown': 'Результат обработки неизвестен; автоматического повтора нет',
    'provider_or_store_unknown': 'Результат модели или сохранения неизвестен',
    'newer_inbound': 'Заменено более новым сообщением',
    'newer_connect_message': 'Получено более новое сообщение',
    'conversation_changed': 'Диалог изменился во время обработки',
    'connect_conversation_advanced': 'Диалог изменился во время обработки',
    'customer_requested_manager': 'Клиент запросил сотрудника',
    'trigger_reply_sent_then_handoff': 'После ответа диалог передан сотруднику',
    'voice_media_unavailable': 'Аудио недоступно через Connect',
    'voice_invalid_audio': 'Аудио повреждено или не декодируется',
    'voice_unsupported': 'Формат аудио не поддерживается',
    'voice_expired': 'Истёк срок получения аудио',
    'voice_size_limit': 'Превышен размер аудио', 'voice_duration_limit': 'Превышена длительность аудио',
    'voice_decoder_unavailable': 'На worker отсутствует ffmpeg',
    'voice_download_timeout': 'Истекло время загрузки аудио', 'voice_decode_timeout': 'Истекло время декодирования',
    'voice_scope_mismatch': 'Медиа не принадлежит этому каналу и диалогу',
    'voice_already_claimed': 'Аудио уже обрабатывалось; повтор запрещён',
    'voice_before_resume': 'Старое аудио до Resume не обрабатывается',
    'voice_conversation_changed': 'Обработка аудио остановлена из-за изменения диалога',
    'voice_transcription_failed': 'Не удалось распознать аудио',
    'recovered_superseded': 'Прерванная задача уже была отменена',
    'recovery_result_unknown': 'Результат прерванной задачи неизвестен; повтор запрещён',
    'send_in_progress': 'Отправка выполняется; повторная отправка запрещена',
    'queue_delay': 'Очередь ожидает свободный worker',
    'received_not_queued': 'Сообщение сохранено без AI-задачи',
    'config_row_missing': 'Настройки изменились или задача уже заменена',
    'tenant_inactive': 'Компания не допущена к работе AI',
    'conversation_paused_or_human': 'Human Mode или Pause: требуется явный Resume',
    'emergency_stopped': 'Включена аварийная остановка',
    'chat_ai_disabled': 'AI-провайдер не включён для компании',
    'chat_ai_consent_missing': 'Не подтверждены условия использования AI',
    'chat_ai_key_missing': 'Недоступны учётные данные AI-провайдера',
    'voice_invalid_reference': 'Некорректный идентификатор аудио',
    'voice_disabled': 'Распознавание аудио выключено',
    'voice_model_unavailable': 'Модель распознавания аудио недоступна',
    'connect_source_not_found': 'Исходное сообщение не найдено в истории Connect',
    'talk_not_found': 'Диалог Kommo не найден',
    'deal_read_failed': 'Не удалось прочитать сделку Kommo; безопасный повтор ограничен',
    'deal_identity_mismatch': 'Сделка Kommo не соответствует задаче',
    'source_message_not_incoming': 'Сообщение не является входящим сообщением клиента',
    'event_missing': 'Не найдено исходное событие задачи',
    'internal_error': 'Внутренняя ошибка; требуется проверка администратора',
}


def reason(code):
    """Only server-owned codes; never echo arbitrary exception strings."""
    code = str(code or '')
    if not code:
        return '', ''
    if code in REASONS:
        return code, REASONS[code]
    if re.fullmatch(r'(chat_ai|voice)_http_[45][0-9]{2}_(invalid_credentials|project_access|billing_or_quota|model_access|rate_limit|request_rejected|unknown)', code):
        category = code.split('_http_', 1)[1][4:]
        labels = {'invalid_credentials':'ошибка авторизации', 'project_access':'нет доступа к проекту',
                  'billing_or_quota':'исчерпана квота провайдера', 'model_access':'модель недоступна',
                  'rate_limit':'превышена частота запросов', 'request_rejected':'запрос отклонён',
                  'unknown':'причина не уточнена'}
        return code, 'Ошибка AI-провайдера: ' + labels[category]
    return 'internal_error', REASONS['internal_error']


def recover_terminal_jobs(cur):
    """Reconcile only expired leases using durable local proof; never resend."""
    cur.execute("""SELECT d.tenant_id,d.id,m.external_id FROM saas_ai_agent_drafts d
      JOIN saas_tenant_webhook_events e ON e.tenant_id=d.tenant_id AND e.fingerprint=d.source_fingerprint AND e.provider='connect'
      LEFT JOIN LATERAL (SELECT external_id FROM saas_crm_messages m0 WHERE m0.tenant_id=d.tenant_id
        AND m0.kommo_lead_id=d.lead_id AND m0.channel='connect:'||(e.payload->>'channel_id')
        AND m0.direction='outgoing' AND m0.author_name='AI Agent' AND m0.raw->>'run_id'=d.id::text
        AND m0.raw->>'status'='accepted' LIMIT 1) m ON TRUE
      WHERE (d.state='sending' AND d.send_started_at<now()-interval '10 minutes')
        OR (d.state='unknown' AND m.external_id IS NOT NULL)
      LIMIT 100 FOR UPDATE OF d SKIP LOCKED""")
    for draft in cur.fetchall():
        cur.execute("UPDATE saas_ai_agent_drafts SET state=%s WHERE tenant_id=%s::uuid AND id=%s::uuid",
                    ('approved' if draft.get('external_id') else 'unknown',draft['tenant_id'],draft['id']))
    cur.execute('''SELECT j.*,r.id AS run_id,r.state AS run_state,r.result,
        d.id AS draft_id,m.external_id AS accepted_message
      FROM saas_ai_agent_jobs j JOIN saas_ai_agent_runs r ON r.tenant_id=j.tenant_id
        AND r.agent_id=j.agent_id AND r.event_fingerprint=j.event_fingerprint AND r.revision=j.revision
      LEFT JOIN saas_ai_agent_drafts d ON d.tenant_id=r.tenant_id AND d.run_id=r.id
      LEFT JOIN LATERAL (SELECT m0.external_id FROM saas_crm_messages m0
        WHERE j.provider='connect' AND m0.tenant_id=j.tenant_id AND m0.channel='connect:'||j.channel_id
          AND m0.kommo_lead_id=j.lead_id AND m0.direction='outgoing' AND m0.author_name='AI Agent'
          AND m0.raw->>'run_id'=r.id::text AND m0.raw->>'status'='accepted' LIMIT 1) m ON TRUE
      WHERE ((j.state='processing' AND j.lease_until<=now())
        OR (j.state='unknown' AND (m.external_id IS NOT NULL OR r.result->>'send_state'='accepted')))
        AND (r.state IN ('completed','superseded','failed','skipped') OR m.external_id IS NOT NULL)
      ORDER BY j.lease_until LIMIT 100 FOR UPDATE OF j SKIP LOCKED''')
    for row in cur.fetchall():
        result = row.get('result') or {}
        accepted = row.get('accepted_message')
        sent = bool(accepted or result.get('send_state') == 'accepted')
        state = 'sent' if sent else 'drafted' if row.get('draft_id') else 'skipped' if row['run_state'] in {'superseded','skipped'} else 'unknown'
        code = '' if state in {'sent','drafted'} else 'recovered_superseded' if state == 'skipped' else 'recovery_result_unknown'
        if accepted:
            cur.execute("UPDATE saas_ai_agent_runs SET state='completed',result=%s::jsonb,error_code='',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",
                        (_json({'send_state':'accepted','message_id':accepted.removeprefix('connect:')}),row['tenant_id'],row['run_id']))
        cur.execute("UPDATE saas_ai_agent_jobs SET state=%s,error_code=%s,lease_token=NULL,lease_until=NULL,updated_at=now() WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider=%s AND conversation_id=%s AND scope_channel_id=%s",
                    (state,code,row['tenant_id'],row['agent_id'],row['provider'],row['conversation_id'],job_scope(row)))
        event_state = 'completed' if state in {'sent','drafted'} else state
        cur.execute("UPDATE saas_tenant_webhook_events SET state=%s,error_code=%s,completed_at=now() WHERE tenant_id=%s::uuid AND fingerprint=%s",
                    (event_state,code,row['tenant_id'],row['event_fingerprint']))


def heartbeat():
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO saas_ai_worker_health(worker,last_seen_at) VALUES('ai_agents',now()) ON CONFLICT(worker) DO UPDATE SET last_seen_at=EXCLUDED.last_seen_at")
        conn.commit()


async def heartbeat_loop(logger):
    while True:
        try:
            await asyncio.to_thread(heartbeat)
        except Exception:
            logger.warning('AI worker heartbeat unavailable')
        await asyncio.sleep(15)


def _message(row):
    """Derive explicit checkpoints from durable receipts, never from UI text."""
    item = dict(row)
    code = item.get('job_error') or item.get('run_error') or item.get('event_error') or ''
    sent = bool(item.get('sent'))
    generated = bool(item.get('generated_at') or item.get('drafted') or sent)
    state = item.get('job_state') or item.get('event_state') or 'received'
    if state in {'unknown','failed'} and not code: code = 'provider_result_unknown'
    if sent:
        status,code = 'sent',''
    elif item.get('draft_state') in {'unknown','sending'}:
        status,code = 'error' if item['draft_state']=='unknown' else 'processing', 'connect_send_unknown' if item['draft_state']=='unknown' else 'send_in_progress'
    elif state in {'unknown','failed'}:
        status = 'error'
    elif state in {'skipped','superseded'}:
        status = 'skipped'
    elif item.get('drafted'):
        status = 'generated'
        code = 'manual_mode' if item.get('agent_mode') != 'automatic' else 'auto_send_disabled'
    elif state == 'queued':
        status = 'queued'
        if not item.get('agent_enabled'): code = 'agent_disabled'
        elif not item.get('company_enabled'): code = 'company_ai_disabled'
        elif item.get('emergency_stop'): code = 'emergency_stop'
        elif item.get('conversation_state') in {'human','paused'}: code = 'conversation_'+item['conversation_state']
        elif not code: code = 'queue_delay'
    elif state == 'processing':
        status = 'generated' if generated else 'processing'
    else:
        status,code = 'received','received_not_queued'
    if item.get('legacy_scope_unresolved') and not sent and state in {'queued','processing','skipped'}:
        code = 'legacy_channel_ambiguous'
    safe_code,label = reason(code)
    return {'message_id':item['message_id'], 'provider':item['provider'], 'channel_id':item.get('channel_id') or '',
            'agent_id':str(item.get('agent_id') or ''), 'status':status,'reason_code':safe_code,'reason':label,
            'received_at':str(item.get('received_at') or ''), 'next_attempt_at':str(item.get('due_at') or ''),
            'attempts':int(item.get('attempts') or 0),
            'checkpoints':{'received':True,'queued':bool(item.get('run_state') or item.get('job_state') or state in {'queued','processing','completed','superseded'}),
                           'processing':bool(item.get('run_state') or state in {'processing','sent','drafted'}),
                           'generated':generated,'sent':sent},
            'automatic_retry':state=='queued' and not item.get('run_state') and code not in {'legacy_channel_ambiguous','agent_disabled','company_ai_disabled','emergency_stop','conversation_human','conversation_paused'}}


def snapshot(profile, limit=50):
    from tenant_ai_agents_store import _authorize
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,_ = _authorize(cur,profile)
            return read_snapshot(cur, tenant, limit)


def read_snapshot(cur, tenant, limit=50):
    """Internal projection; caller must authorize and hold membership locks."""
    cur.execute("SELECT last_seen_at, last_seen_at>now()-interval '90 seconds' AS alive FROM saas_ai_worker_health WHERE worker='ai_agents'")
    worker = cur.fetchone() or {}
    cur.execute('''SELECT count(*) FILTER(WHERE state='queued') AS queued,
        count(*) FILTER(WHERE state='processing') AS processing,
        count(*) FILTER(WHERE state='queued' AND due_at<now()-interval '5 minutes') AS overdue,
        count(*) FILTER(WHERE state='processing' AND lease_until<now()) AS expired_leases,
        COALESCE(EXTRACT(EPOCH FROM now()-min(due_at) FILTER(WHERE state='queued' AND due_at<now())),0)::int AS oldest_due_seconds
      FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid''',(tenant,))
    metrics = dict(cur.fetchone())
    cur.execute('''SELECT count(*) FILTER(WHERE state IN ('failed','unknown')) AS ai_errors_24h,
        count(*) FILTER(WHERE error_code IN ('connect_send_unknown','send_result_unknown')) AS unsuccessful_sends_24h
      FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid AND created_at>now()-interval '24 hours' ''',(tenant,))
    metrics.update(cur.fetchone())
    cur.execute('''SELECT count(*) AS count FROM saas_tenant_webhook_events e
      WHERE e.tenant_id=%s::uuid AND e.event_type='incoming_message' AND e.state='failed'
        AND e.received_at>now()-interval '24 hours' AND NOT EXISTS (
          SELECT 1 FROM saas_ai_agent_runs r WHERE r.tenant_id=e.tenant_id AND r.event_fingerprint=e.fingerprint)''',(tenant,))
    metrics['ai_errors_24h'] += cur.fetchone()['count']
    cur.execute("SELECT count(*) AS count FROM saas_ai_agent_drafts WHERE tenant_id=%s::uuid AND state='unknown' AND created_at>now()-interval '24 hours'",(tenant,))
    metrics['unsuccessful_sends_24h'] += cur.fetchone()['count']
    cur.execute("SELECT enabled,emergency_stop,auto_send_enabled FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid",(tenant,))
    settings = cur.fetchone() or {}
    cur.execute("SELECT count(*) AS count FROM saas_ai_agents WHERE tenant_id=%s::uuid AND enabled=TRUE",(tenant,))
    active_agents = cur.fetchone()['count']
    # History remains inspectable when the coalescing job has advanced to
    # another message. No payload/body/result is returned to the browser.
    cur.execute('''SELECT e.external_id AS message_id,e.provider,COALESCE(e.payload->>'channel_id',e.payload->>'channel') AS channel_id,
        e.received_at,e.state AS event_state,e.error_code AS event_error,
        j.state AS job_state,j.error_code AS job_error,j.attempts,j.due_at,
        COALESCE(j.agent_id,r.agent_id) AS agent_id,a.enabled AS agent_enabled,a.mode AS agent_mode,
        s.enabled AS company_enabled,s.emergency_stop,s.auto_send_enabled,cs.state AS conversation_state,
        (e.provider='connect' AND ai_legacy_blocked(e.tenant_id,e.payload->>'channel_id',e.payload->>'conversation_id',e.fingerprint)) AS legacy_scope_unresolved,
        r.state AS run_state,r.error_code AS run_error,r.generated_at,
        (SELECT d.state FROM saas_ai_agent_drafts d WHERE d.tenant_id=e.tenant_id AND d.source_fingerprint=e.fingerprint AND d.agent_id=COALESCE(j.agent_id,r.agent_id) ORDER BY d.created_at DESC LIMIT 1) AS draft_state,
        EXISTS(SELECT 1 FROM saas_ai_agent_drafts d WHERE d.tenant_id=e.tenant_id AND d.source_fingerprint=e.fingerprint AND d.agent_id=COALESCE(j.agent_id,r.agent_id)) AS drafted,
        EXISTS(SELECT 1 FROM saas_ai_agent_runs sr WHERE sr.tenant_id=e.tenant_id AND sr.event_fingerprint=e.fingerprint
            AND sr.agent_id=COALESCE(j.agent_id,r.agent_id) AND sr.result->>'send_state'='accepted') OR EXISTS (
            SELECT 1 FROM saas_ai_agent_drafts d JOIN saas_crm_messages sm ON sm.tenant_id=d.tenant_id
              AND sm.kommo_lead_id=d.lead_id AND sm.channel='connect:'||(e.payload->>'channel_id')
              AND sm.direction='outgoing' AND sm.author_name='AI Agent'
              AND sm.raw->>'run_id'=d.id::text AND sm.raw->>'status'='accepted'
            WHERE d.tenant_id=e.tenant_id AND d.source_fingerprint=e.fingerprint AND d.agent_id=COALESCE(j.agent_id,r.agent_id)) AS sent
      FROM saas_tenant_webhook_events e
      LEFT JOIN LATERAL (SELECT * FROM saas_ai_agent_jobs j0 WHERE j0.tenant_id=e.tenant_id AND j0.event_fingerprint=e.fingerprint ORDER BY j0.updated_at DESC LIMIT 1) j ON TRUE
      LEFT JOIN LATERAL (SELECT * FROM saas_ai_agent_runs r0 WHERE r0.tenant_id=e.tenant_id AND r0.event_fingerprint=e.fingerprint AND (j.agent_id IS NULL OR r0.agent_id=j.agent_id) ORDER BY r0.created_at DESC LIMIT 1) r ON TRUE
      LEFT JOIN saas_ai_agents a ON a.tenant_id=e.tenant_id AND a.id=COALESCE(j.agent_id,r.agent_id)
      LEFT JOIN saas_ai_agent_settings s ON s.tenant_id=e.tenant_id
      LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=e.tenant_id AND cs.provider=e.provider
        AND cs.conversation_id=COALESCE(e.payload->>'conversation_id',e.payload->>'chat_id')
        AND cs.channel_id=CASE WHEN e.provider='connect' THEN e.payload->>'channel_id' ELSE '' END
      WHERE e.tenant_id=%s::uuid AND e.event_type='incoming_message'
      ORDER BY e.received_at DESC,e.fingerprint LIMIT %s''',(tenant,max(1,min(int(limit),100))))
    messages = [_message(row) for row in cur.fetchall()]
    blockers=[]
    if not enabled('AI_AGENTS_ENABLED'): blockers.append('ai_agents_server_disabled')
    if not settings.get('enabled'): blockers.append('company_ai_disabled')
    if settings.get('emergency_stop'): blockers.append('emergency_stop')
    if not active_agents: blockers.append('agent_not_found')
    state = 'disabled' if blockers else 'unavailable' if not worker.get('alive') else 'degraded' if any(metrics.get(k) for k in ('overdue','expired_leases','ai_errors_24h','unsuccessful_sends_24h')) else 'ready'
    labels={'disabled':'AI остановлен настройками','unavailable':'Нет свежего heartbeat worker','degraded':'AI требует внимания: проверьте ошибки и очередь','ready':'Worker доступен; отдельные диалоги могут быть остановлены'}
    return {'health':{'state':state,'label':labels[state],'worker_alive':bool(worker.get('alive')),
                      'worker_last_seen_at':str(worker.get('last_seen_at') or ''),
                      'blockers':[reason(code)[1] for code in blockers],
                      'automatic_send_enabled':bool(settings.get('auto_send_enabled') and enabled('AI_AGENTS_AUTO_SEND_ENABLED'))},
            'metrics':metrics,'messages':messages,'window_hours':24}
