"""One conservative episode admission fence for manual and automatic consultations.

Only explicit bounded message membership proves an implicit case association.
Full-history and attachment-picker inclusion is not incident identity.
"""
from tenant_linear_policy import TenantLinearError


def lock_source(cur, tenant, channel, conversation):
    # Same lock as case creation and its automatic-dispatch enqueue transaction.
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                ('support:'+tenant+':'+channel+':'+conversation,))


def implicit_case(cur, tenant, channel, conversation, selected):
    from tenant_support_case_context import _NON_BOUNDARY_EVENTS
    cur.execute("""SELECT t.id::text,t.history_scope,cm.event_key,
        count(DISTINCT cm.message_id) AS matched
      FROM saas_support_tickets t JOIN saas_support_ticket_messages cm
        ON cm.tenant_id=t.tenant_id AND cm.ticket_id=t.id
      WHERE t.tenant_id=%s::uuid AND t.channel_id=%s::uuid AND t.conversation_id=%s
        AND t.status<>'resolved' AND t.completion_outcome<>'test_stopped'
        AND cm.message_id=ANY(%s::uuid[])
      GROUP BY t.id,t.history_scope,cm.event_key""", (tenant,channel,conversation,selected))
    candidates=cur.fetchall()
    if not candidates:return None
    def boundary(row):
        event=row['event_key']
        return (row['history_scope'] in {'selected','legacy'}
            and not any(event.startswith(pattern[:-1]) for pattern in _NON_BOUNDARY_EVENTS)
            and not event.startswith('case-snapshot:'))
    proven=[row for row in candidates if boundary(row) and int(row['matched'])==len(selected)]
    if len(proven)!=1 or any(row['id']!=proven[0]['id'] for row in candidates):
        raise TenantLinearError('Фрагмент пересекается с обращением, но связь неоднозначна. Откройте нужное обращение и выберите его материалы.',409)
    return proven[0]['id']


def existing(cur, tenant, channel, conversation, selected, case_id, destination, dest_conversation, summary, media, include_identity, versions, auto=None, reuse_allowed=False):
    # JSON message IDs are the immutable, explicitly selected source evidence.
    # A shared dialogue alone never merges two questions. Unknown stays fenced
    # even if an unrelated developer message changed the consultation status.
    cur.execute('''SELECT c.id::text,c.case_id::text,c.evidence,c.created_by,c.reason,
        ps.selected_media_ids,ps.public_identity,ps.source_versions,ps.revoked_at,ps.expires_at>now() AS share_current,
        d.request_id::text,d.state,d.channel_id::text AS destination_channel,
        d.conversation_id AS destination_conversation
      FROM saas_internal_consultations c JOIN saas_consultation_group_deliveries d
        ON d.tenant_id=c.tenant_id AND d.consultation_id=c.id
      LEFT JOIN saas_public_case_shares ps ON ps.tenant_id=c.tenant_id AND ps.consultation_id=c.id
      LEFT JOIN saas_support_tickets t ON t.tenant_id=c.tenant_id AND t.id=c.case_id
      WHERE c.tenant_id=%s::uuid AND c.channel_id=%s::uuid AND c.conversation_id=%s
        AND ((c.case_id IS NULL OR (c.case_id=%s::uuid AND t.status<>'resolved'))
          OR d.state IN ('sending','pending','unknown'))
        AND (c.evidence->'message_ids') ?| %s::text[]
        AND (d.state IN ('ready','sending','pending','unknown')
          OR (d.state='sent' AND c.status IN ('waiting_specialist','queued_after_hours')))
      ORDER BY c.created_at,c.id''',(tenant,channel,conversation,case_id,selected))
    rows=cur.fetchall()
    if auto and auto.get('legacy_multiple'):
        # Preserve the old explicitly configured fanout. Only a sibling intent
        # from this exact causal event is exempt, never an employee request.
        cur.execute('''SELECT x.consultation_id::text FROM saas_ai_case_developer_dispatches x
          JOIN saas_consultation_group_deliveries original ON original.tenant_id=x.tenant_id
            AND original.consultation_id=x.consultation_id AND original.request_id=x.request_id
            AND original.channel_id=x.channel_id AND original.conversation_id=x.conversation_id
          WHERE x.tenant_id=%s::uuid AND x.case_id=%s::uuid AND x.event_key=%s
            AND x.request_id<>%s::uuid AND x.consultation_id IS NOT NULL
            AND (x.channel_id<>%s::uuid OR x.conversation_id<>%s)''',
          (tenant,case_id,auto['event_key'],auto['request_id'],destination,dest_conversation))
        siblings={row['consultation_id'] for row in cur.fetchall()}
        rows=[row for row in rows if row['id'] not in siblings]
    if len(rows)>1:
        raise TenantLinearError('Найдено несколько консультаций этого фрагмента. Проверьте существующие запросы; новая отправка отключена.',409)
    if not rows:return None
    row=rows[0]
    if (row.get('evidence') or {}).get('message_ids')!=selected:
        raise TenantLinearError('Фрагмент частично пересекается с существующей консультацией. Проверьте её; новый вопрос не объединён автоматически.',409)
    if row['destination_channel']!=destination or row['destination_conversation']!=dest_conversation:
        raise TenantLinearError('По этому фрагменту уже ожидается другой разработчик. Проверьте существующую консультацию; смена адресата не отправляет второй запрос.',409)
    if row.get('case_id')!=case_id:
        raise TenantLinearError('По этому фрагменту уже есть консультация в другом контексте. Откройте её в исходном диалоге; новая отправка отключена.',409)
    if (row['reason']!=summary or sorted(row.get('selected_media_ids') or [])!=sorted(media)
        or bool(row.get('public_identity'))!=include_identity
        or row.get('source_versions')!=versions or row.get('revoked_at') or not row.get('share_current')):
        raise TenantLinearError('Этот фрагмент уже подготовлен или отправлен с другим вопросом или материалами. Изменённый вопрос не отправлен; проверьте существующую консультацию.',409)
    if not reuse_allowed and not auto:
        raise TenantLinearError('Этот фрагмент уже подготовлен или отправлен. Проверьте существующую консультацию; повтор отключён.',409)
    return row


def queued(cur, tenant, case_id, request, selected, auto=None):
    if not case_id or auto:return False
    cur.execute("SELECT to_regclass('saas_ai_case_developer_dispatches') AS ready")
    if not (cur.fetchone() or {}).get('ready'):return False
    cur.execute('''SELECT 1 FROM saas_ai_case_developer_dispatches x
      WHERE x.tenant_id=%s::uuid AND x.case_id=%s::uuid AND x.request_id<>%s::uuid
        AND x.state IN ('queued','preparing','pending','unknown','sent')
        AND (x.state<>'sent' OR x.consultation_id IS NULL OR EXISTS(
          SELECT 1 FROM saas_internal_consultations c WHERE c.tenant_id=x.tenant_id
            AND c.id=x.consultation_id AND c.status IN ('waiting_specialist','queued_after_hours')))
        AND EXISTS(SELECT 1 FROM saas_support_ticket_messages cm
          WHERE cm.tenant_id=x.tenant_id AND cm.ticket_id=x.case_id
            AND cm.event_key='handoff:'||x.event_key AND cm.message_id=ANY(%s::uuid[])) LIMIT 1''',
                (tenant,case_id,request,selected))
    return bool(cur.fetchone())


def auto_intent(cur,tenant,case_id,request,identifier,destination,conversation):
    if not identifier:return None
    cur.execute("""SELECT x.event_key,x.request_id::text,a.response_policy
      FROM saas_ai_case_developer_dispatches x JOIN saas_ai_agents a
        ON a.tenant_id=x.tenant_id AND a.id=x.agent_id
      WHERE x.tenant_id=%s::uuid AND x.id=%s::uuid AND x.case_id=%s::uuid
        AND x.request_id=%s::uuid AND x.channel_id=%s::uuid AND x.conversation_id=%s
        AND x.state='preparing'""",(tenant,identifier,case_id,request,destination,conversation))
    row=cur.fetchone()
    if not row:raise TenantLinearError('Автоматический запрос изменился. Отправка остановлена.',409)
    policy=row.get('response_policy') or {}
    return {**row,'legacy_multiple':policy.get('developer_routing')!='primary_reserve'
        and len(policy.get('developer_destinations') or [])>1}
