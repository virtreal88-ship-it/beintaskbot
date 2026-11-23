"""Explicit paid text analysis of a closed, durably pinned operator episode.

No public payload can opt into the pinned snapshot capability. No AI call is
made while a workflow/episode transaction is held. A deterministic extraction
ID makes reloads/repeated clicks checks of the same attempt, not new spending.
"""
import uuid
from tenant_linear_policy import TenantLinearError

_EPISODE_CAPABILITY=object()


def episode_id(profile):
    if isinstance(profile,dict) and profile.get('_episode_case_capability') is _EPISODE_CAPABILITY:
        return str(uuid.UUID(str(profile['_episode_case_id'])))
    return None


def scoped(profile, selected):
    from tenant_operator_case_submission import scoped as submission_profile
    return {**submission_profile(profile),'_episode_case_capability':_EPISODE_CAPABILITY,
            '_episode_case_id':str(selected)}


def _closed(cur,profile,channel,conversation,selected):
    from tenant_operator_access import access
    from tenant_dialog_access import require_source
    from tenant_dialog_lifecycle import require_access
    tenant,actor,policy=access(cur,profile,action='update')
    require_source(cur,tenant,policy,'connect',channel,'view')
    require_source(cur,tenant,policy,'connect',channel,'update')
    require_access(cur,tenant,actor,'connect',channel,conversation,owner=policy.profile.get('role')=='owner')
    cur.execute('''SELECT * FROM saas_dialog_episodes WHERE tenant_id=%s::uuid AND id=%s::uuid
      AND channel_id=%s::uuid AND conversation_id=%s AND operator_id=%s AND closed_at IS NOT NULL FOR UPDATE''',
      (tenant,selected,channel,conversation,actor))
    episode=cur.fetchone()
    if not episode:raise TenantLinearError('Завершённое обращение недоступно.',404)
    from tenant_connect_dialog_equivalence import members
    leads=list(members(cur,tenant,channel,conversation).values())
    cur.execute("""SELECT 1 FROM saas_chat_send_receipts r
      WHERE r.tenant_id=%s::uuid AND r.lead_id=ANY(%s::bigint[])
        AND COALESCE(r.route->>'lifecycle_episode_id','')=''
        AND r.state IN ('preparing','sending','unknown') LIMIT 1""",(tenant,leads))
    if cur.fetchone():
        raise TenantLinearError('В этом диалоге осталась старая отправка с неподтверждённым результатом. Сначала проверьте её статус; анализ кейса не запущен.',409)
    cur.execute('''SELECT 1 FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid
      AND route->>'lifecycle_episode_id'=%s AND state IN ('preparing','sending','unknown') LIMIT 1''',(tenant,selected))
    if cur.fetchone():raise TenantLinearError('Отправка сообщения ещё проверяется. Дождитесь результата.',409)
    cur.execute('''SELECT 1 FROM saas_dialog_episode_sends ds WHERE ds.tenant_id=%s::uuid
      AND ds.episode_id=%s::uuid AND NOT EXISTS(SELECT 1 FROM saas_dialog_episode_messages em
        JOIN saas_crm_messages m ON m.tenant_id=em.tenant_id AND m.id=em.message_id
        WHERE em.tenant_id=ds.tenant_id AND em.episode_id=ds.episode_id
          AND m.external_id IN (ds.external_id,'connect:'||ds.external_id)) LIMIT 1''',(tenant,selected))
    if cur.fetchone():raise TenantLinearError('История отправленного сообщения ещё загружается. Повторите позже.',409)
    return tenant,actor,episode


def snapshot_rows(cur,profile,binding,chosen):
    """Return descending, exact episode members or None for the legacy path."""
    selected=episode_id(profile)
    if not selected:return None
    if binding.get('history_mode')!='channel':raise TenantLinearError('Нужен точный источник обращения.',400)
    channel,conversation=binding['channel_id'],binding['conversation_id']
    tenant,_,_=_closed(cur,profile,channel,conversation,selected)
    from tenant_connect_dialog_equivalence import members
    from tenant_knowledge_candidates_policy import MAX_MESSAGES as MAX_SUBMISSION_MESSAGES
    leads=list(members(cur,tenant,channel,conversation).values())
    cur.execute('''SELECT count(*) AS n FROM saas_dialog_episode_messages WHERE tenant_id=%s::uuid AND episode_id=%s::uuid''',(tenant,selected))
    count=int(cur.fetchone()['n'])
    if count>MAX_SUBMISSION_MESSAGES:
        raise TenantLinearError(f'В обращении {count} сообщений; безопасный лимит анализа — {MAX_SUBMISSION_MESSAGES}. Обращение сохранено целиком, анализ не запускался.',413)
    if not count:raise TenantLinearError('В завершённом обращении нет сообщений для анализа.',409)
    cur.execute('''SELECT m.*,m.kommo_lead_id AS source_lead_id,%s AS source_channel_id
      FROM saas_dialog_episode_messages em JOIN saas_crm_messages m ON m.tenant_id=em.tenant_id AND m.id=em.message_id
      WHERE em.tenant_id=%s::uuid AND em.episode_id=%s::uuid AND m.channel=%s
        AND m.kommo_lead_id=ANY(%s::bigint[]) AND m.direction IN ('incoming','outgoing')
      ORDER BY COALESCE(m.happened_at,m.created_at) DESC,m.id DESC''',(channel,tenant,selected,'connect:'+channel,leads))
    rows=cur.fetchall()
    if len(rows)!=count:raise TenantLinearError('Состав обращения изменился или источник недоступен.',409)
    return rows


def submit(profile,payload):
    from tenant_platform import _connect
    from tenant_dialog_lifecycle import enabled,_row
    from tenant_operator_access import access,_resolve_connect_route
    from tenant_support_access import require_create
    from tenant_operator_case_submission import assignment
    import tenant_knowledge_candidates_store as store
    if payload.get('provider')!='connect':raise TenantLinearError('Выберите источник Connect.')
    try:
        uuid.UUID(str(payload.get('request_id')))
        revision=payload['expected_revision']
        if type(revision) is not int or revision<0:raise ValueError()
    except (KeyError,ValueError,TypeError):raise TenantLinearError('Обновите диалог.',409) from None
    with _connect() as conn:
        with conn.cursor() as cur:
            if not enabled(cur, profile['tenant_id']):
                raise TenantLinearError('Новая очередь диалогов ещё не включена.',409)
            tenant,actor,policy=access(cur,profile,action='update')
            require_create(cur,profile)
            route=_resolve_connect_route(cur,tenant,str(payload.get('conversation_id') or ''),str(payload.get('channel_id') or ''),int(payload['lead_id']),allow_merged=True)
            channel,conversation=str(route['connect_channel_id']),route['conversation_id']
            from tenant_dialog_lifecycle import require_access
            require_access(cur,tenant,actor,'connect',channel,conversation,owner=policy.profile.get('role')=='owner')
            current=_row(cur,tenant,channel,conversation)
            if (int(current['revision']) if current else 0)!=revision:raise TenantLinearError('Диалог уже изменился. Обновите его.',409)
            selected=str(uuid.UUID(str(payload.get('episode_id') or (current or {}).get('id'))))
            tenant,actor,episode=_closed(cur,profile,channel,conversation,selected)
            internal=scoped(profile,selected)
            # Preserve existing reviewer/submission restrictions before any paid work.
            store.authorize(cur,internal)
            rows=snapshot_rows(cur,internal,{'channel_id':channel,'conversation_id':conversation,'history_mode':'channel'},route)
            training=assignment(cur,tenant,channel,conversation)
            identifiers=[str(row['id']) for row in reversed(rows)]
            request_id=str(uuid.uuid5(uuid.UUID(tenant),'dialog-episode-case:'+selected))
            data={'request_id':request_id,'provider':'connect','channel_id':channel,'conversation_id':conversation,
                  'history_mode':'channel','message_ids':identifiers,'analysis_agent_id':training['agent_id'],
                  'allow_external_media':False,'allow_paid_asr':False,'allow_vision':False}
        conn.commit()
    from tenant_knowledge_candidates_service import analyze
    result=analyze(internal,data,training=training)
    # Revalidate current access after the paid operation. Never expose a result
    # to an operator whose source or conversation ownership changed meanwhile.
    with _connect() as conn:
        with conn.cursor() as cur:
            _closed(cur,profile,channel,conversation,selected)
            cur.execute('SELECT status,error_code,route FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,request_id))
            extraction=cur.fetchone()
            if not extraction or (extraction['route'].get('training_target') or {}).get('episode_id')!=selected:
                raise TenantLinearError('Связь анализа с обращением не подтверждена.',409)
            cur.execute('SELECT id::text FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND extraction_id=%s::uuid ORDER BY id',(tenant,request_id))
            candidates=cur.fetchall()
            suitability=extraction['route'].get('episode_suitability') or {}
            if extraction['status']=='completed' and (type(suitability.get('suitable')) is not bool
                or len(candidates)!=(1 if suitability.get('suitable') else 0)):
                raise TenantLinearError('Результат проверки пригодности обращения не подтверждён. Новый платный запуск не выполнялся.',409)
            case_id=candidates[0]['id'] if extraction['status']=='completed' and suitability.get('suitable') and len(candidates)==1 else None
            if case_id:
                cur.execute('UPDATE saas_dialog_episodes SET case_id=%s::uuid WHERE tenant_id=%s::uuid AND id=%s::uuid AND (case_id IS NULL OR case_id=%s::uuid)',(case_id,tenant,selected,case_id))
        conn.commit()
    publication=publish_if_proven(profile,tenant,request_id,selected,channel,conversation,case_id) if case_id else None
    return {'publication':publication,'case_id':case_id,'episode_id':selected,'case_state':'not_suitable' if extraction['status']=='completed' and suitability.get('suitable') is False else extraction['status'],'suitability':suitability,
            'request_id':request_id,'candidates':result.get('candidates',[]),'error_code':extraction['error_code']}


def publish_if_proven(profile,tenant,request_id,selected,channel,conversation,case_id):
    """One atomic, original-actor publication attempt; never elevate authority."""
    import json
    from tenant_platform import _connect
    from tenant_operator_case_submission import assignment
    import tenant_knowledge_candidates_store as store
    with _connect() as conn:
        store.schema(conn)  # Bootstrap before locks/savepoint; never inside mutation.
        from tenant_ai_agents_schema import ensure_schema
        ensure_schema(conn)
        with conn.cursor() as cur:
            _closed(cur,profile,channel,conversation,selected)
            cur.execute('SELECT route FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,request_id))
            route=cur.fetchone()['route']
            if route.get('episode_publication'):
                saved=route['episode_publication']
                if saved.get('state')=='published':
                    cur.execute('''SELECT s.id FROM saas_knowledge_candidate_sources cs
                      JOIN saas_ai_knowledge_sources s ON s.tenant_id=cs.tenant_id AND s.id=cs.source_id
                      JOIN saas_knowledge_candidates c ON c.tenant_id=cs.tenant_id AND c.id=cs.candidate_id
                      WHERE cs.tenant_id=%s::uuid AND cs.candidate_id=%s::uuid AND cs.agent_id=%s::uuid
                        AND s.agent_id=cs.agent_id AND s.active AND c.status='approved' ''',
                      (tenant,case_id,saved['agent_id']))
                    if len(cur.fetchall())!=1:
                        return {'state':'draft','reason':'Публикация изменена или отключена. Нужна ручная проверка; повторной публикации не было.'}
                return saved
            cur.execute('SAVEPOINT episode_publication')
            try:
                target=assignment(cur,tenant,channel,conversation,expected=route.get('training_target'))
                # Use the real actor, never the restricted extraction capability.
                store.authorize(cur,profile)
                cur.execute('SELECT version,status,content FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,case_id))
                candidate=cur.fetchone()
                version=candidate['version']
                if candidate['status']!='approved' and (version!=1 or candidate['status']!='pending_review'):
                    raise TenantLinearError('Кейс изменён или уже рассмотрен. Нужна ручная проверка.',409)
                run=store.extraction_row(cur,tenant,request_id)
                run['_candidate_id']=case_id
                run['_candidate_source_ids']=set(candidate['content'].get('source_message_ids',[]))
                evidence=store.evidence(cur,profile,run)
                if any(ref.get('media_kind') for ref in run.get('source_refs',[])):
                    raise TenantLinearError('В обращении есть вложения: перед публикацией нужна проверка полноты их анализа.',409)
                operator_text=[item.get('text','') for item in evidence if item.get('role')=='operator']
                procedures=[*candidate['content'].get('resolution_steps',[]),*candidate['content'].get('operator_actions',[])]
                if any(not step or not any(step in text for text in operator_text) for step in procedures):
                    raise TenantLinearError('Шаги решения не подтверждены точными словами оператора. Нужна ручная проверка.',409)
                result=store.mutate(profile,case_id,{'expected_version':version,'agent_ids':[target['agent_id']]},'approve',connection=conn)
                publication={'state':'published','agent_id':target['agent_id'],'candidate_id':case_id}
                if result['candidate']['status']!='approved':raise TenantLinearError('Публикация не подтверждена.',409)
            except TenantLinearError as exc:
                cur.execute('ROLLBACK TO SAVEPOINT episode_publication')
                publication={'state':'draft','reason':str(exc)}
            cur.execute('RELEASE SAVEPOINT episode_publication')
            cur.execute("UPDATE saas_knowledge_extractions SET route=route||%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(json.dumps({'episode_publication':publication}),tenant,request_id))
            store._audit(cur,tenant,int(profile['telegram_id']),'dialog_episode_publication_checked','knowledge_candidate',case_id,publication)
        conn.commit()
    return publication
