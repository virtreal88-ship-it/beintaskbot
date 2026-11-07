"""Durable asynchronous private follow-ups; no pilot expiry or customer transport."""
import asyncio
import uuid
from tenant_platform import _connect
from tenant_linear_policy import TenantLinearError
import tenant_dialog_consultations as flow


def pending(limit=10):
    with _connect() as conn:
        flow.require_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('saas_dialog_consultation_replies') AS ready")
            if not (cur.fetchone() or {}).get('ready'):return []
            cur.execute('''SELECT r.id::text,r.tenant_id::text,c.created_by FROM saas_dialog_consultation_replies r
              JOIN saas_internal_consultations c ON c.tenant_id=r.tenant_id AND c.id=r.consultation_id
              LEFT JOIN saas_support_tickets t ON t.tenant_id=c.tenant_id AND t.id=c.case_id
              LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=t.tenant_id AND cs.provider='connect'
                AND cs.channel_id=t.channel_id::text AND cs.conversation_id=t.conversation_id
              LEFT JOIN saas_ai_agents a ON a.tenant_id=cs.tenant_id AND a.id=cs.agent_id
              WHERE r.draft_state='pending' AND r.draft_request_id IS NULL AND r.handled_at IS NULL AND c.created_by IS NOT NULL
                AND (c.case_id IS NULL OR (t.status<>'resolved' AND a.mode='manual'
                  AND r.source_kind='provider' AND r.message_type='text'
                  AND EXISTS(SELECT 1 FROM saas_crm_messages m WHERE m.tenant_id=r.tenant_id
                    AND m.external_id=r.source_external_id AND m.channel='connect:'||r.channel_id::text
                    AND m.direction='incoming' AND m.raw->>'quotedExternalMessageId' IS NOT NULL
                    AND EXISTS(SELECT 1 FROM saas_consultation_group_deliveries d
                      WHERE d.tenant_id=c.tenant_id AND d.consultation_id=c.id AND d.channel_id=r.channel_id
                        AND d.conversation_id=r.conversation_id AND d.state='sent'
                        AND d.provider_message_id=m.raw->>'quotedExternalMessageId'))))
                AND (r.draft_retry_after IS NULL OR r.draft_retry_after<=now())
              ORDER BY r.created_at LIMIT %s''',(limit,))
            return [dict(r) for r in cur.fetchall()]


def auto_dispatch_queue(limit=10):
    """Read only freshly queued automatic CASE dispatch intents."""
    with _connect() as conn:
        flow.require_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('saas_ai_case_developer_dispatches') AS ready")
            if not (cur.fetchone() or {}).get('ready'):return []
            cur.execute("SELECT id::text FROM saas_ai_case_developer_dispatches WHERE state='queued' ORDER BY created_at,id LIMIT %s",(limit,))
            return [row['id'] for row in cur.fetchall()]


def reconcile_auto_dispatches():
    """Project the existing consultation receipt ledger; never resubmit a provider POST."""
    with _connect() as conn:
        flow.require_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('saas_ai_case_developer_dispatches') AS ready")
            if not (cur.fetchone() or {}).get('ready'):return
            cur.execute('''UPDATE saas_ai_case_developer_dispatches x SET
                consultation_id=d.consultation_id,
                state=CASE d.state WHEN 'ready' THEN 'queued' WHEN 'sent' THEN 'sent' WHEN 'pending' THEN 'pending'
                    WHEN 'unknown' THEN 'unknown' WHEN 'blocked' THEN 'blocked' ELSE x.state END,
                reason=CASE WHEN d.state='blocked' THEN 'developer_route_blocked' ELSE x.reason END,updated_at=now()
              FROM saas_consultation_group_deliveries d
              WHERE d.tenant_id=x.tenant_id AND d.request_id=x.request_id
                AND x.state IN ('preparing','queued','pending','unknown')''')
            cur.execute("UPDATE saas_ai_case_developer_dispatches SET state='unknown',reason='dispatch_outcome_requires_receipt_reconciliation',updated_at=now() WHERE state='preparing' AND updated_at<now()-interval '5 minutes'")
        conn.commit()


def dispatch_auto_case(item_id):
    """Prepare and send one owner-authorized auto CASE request through the existing receipt path."""
    import uuid
    with _connect() as conn:
        flow.require_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_ai_case_developer_dispatches SET state='preparing',updated_at=now()
              WHERE id=%s::uuid AND state='queued' RETURNING *''',(item_id,))
            item=cur.fetchone()
            if not item:return
        conn.commit()
    tenant=str(item['tenant_id']);owner=item.get('owner_id');profile={'tenant_id':tenant,'telegram_id':owner}
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT a.enabled,a.mode,a.scopes,a.response_policy,s.enabled AS company_enabled,
                  s.auto_send_enabled,s.emergency_stop,t.status AS tenant_status,t.owner_telegram_id,m.role,m.active
                  FROM saas_ai_agents a JOIN saas_ai_agent_settings s ON s.tenant_id=a.tenant_id
                  JOIN saas_tenants t ON t.id=a.tenant_id LEFT JOIN saas_tenant_members m
                    ON m.tenant_id=a.tenant_id AND m.telegram_id=%s
                  WHERE a.tenant_id=%s::uuid AND a.id=%s::uuid''',(owner,tenant,item['agent_id']))
                cfg=cur.fetchone() or {};policy=cfg.get('response_policy') or {}
                destinations=policy.get('developer_destinations') if isinstance(policy.get('developer_destinations'),list) else []
                recipient_current=any(str(row.get('channel_id') or '')==str(item.get('channel_id') or '')
                    and row.get('conversation_id')==item.get('conversation_id') for row in destinations if isinstance(row,dict))
                if (not owner or cfg.get('role')!='owner' or cfg.get('active') is not True
                    or cfg.get('owner_telegram_id')!=owner
                    or cfg.get('enabled') is not True or cfg.get('mode')!='automatic'
                    or policy.get('auto_case_dispatch_enabled') is not True
                    or int(policy.get('auto_case_dispatch_owner_id') or 0)!=int(owner)
                    or not recipient_current
                    or cfg.get('company_enabled') is not True or cfg.get('auto_send_enabled') is not True
                    or cfg.get('emergency_stop') is True or cfg.get('tenant_status') not in {'active','trial'}):
                    raise TenantLinearError('automatic_dispatch_policy_or_owner_changed',409)
                from tenant_ai_agent_runtime_flags import enabled as runtime_flag
                from tenant_ai_agent_connect import scope_matches
                if not runtime_flag('AI_AGENTS_ENABLED') or not runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED'):
                    raise TenantLinearError('automatic_dispatch_runtime_or_agent_scope_changed',409)
                if not item.get('channel_id') or not item.get('conversation_id'):
                    raise TenantLinearError('developer_destination_not_configured',409)
                cur.execute('''SELECT t.channel_id::text AS channel_id,t.conversation_id,t.description,t.status
                  FROM saas_support_tickets t WHERE t.tenant_id=%s::uuid AND t.id=%s::uuid''',(tenant,item['case_id']))
                case=cur.fetchone()
                if not case or case['status']=='resolved':raise TenantLinearError('case_no_longer_open',409)
                if not scope_matches(cfg.get('scopes') or [],str(case['channel_id'])):
                    raise TenantLinearError('automatic_dispatch_agent_scope_changed',409)
                if not flow.auto_case_dispatch_source_current(cur,tenant,case['channel_id'],case['conversation_id'],
                    str(item['agent_id']),str(item['event_key'])):
                    raise TenantLinearError('source_dialog_state_or_assignment_changed',409)
                cur.execute('''SELECT state,revision,agent_id::text AS agent_id,agent_assignment_id::text AS assignment_id
                  FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect'
                    AND channel_id=%s AND conversation_id=%s FOR SHARE''',(tenant,case['channel_id'],case['conversation_id']))
                source_state=cur.fetchone() or {}
                if (source_state.get('state')!='active' or str(source_state.get('agent_id') or '')!=str(item['agent_id'])
                    or item.get('source_revision') is None or int(source_state.get('revision') or 0)!=int(item['source_revision'])
                    or str(source_state.get('assignment_id') or '')!=str(item.get('source_assignment_id') or '')):
                    raise TenantLinearError('source_dialog_state_or_assignment_changed',409)
                from tenant_dialog_consultations import access,developer_target
                tenant_check,_actor,dialogs_policy=access(cur,profile,'dialogs')
                if str(tenant_check)!=tenant:raise TenantLinearError('developer_recipient_company_changed',403)
                developer_target(cur,tenant,dialogs_policy,item['channel_id'],item['conversation_id'])
        from tenant_dialog_consultations import preview,prepare,dispatch
        source={'case_id':str(item['case_id']),'channel_id':case['channel_id'],'conversation_id':case['conversation_id']}
        material=preview(profile,source)
        messages=[row for row in material.get('messages',[]) if row.get('direction') in {'incoming','outgoing'}][-30:]
        if not messages:raise TenantLinearError('case_has_no_publishable_messages',409)
        from tenant_consultation_summary import for_case
        with _connect() as conn:
            with conn.cursor() as cur:
                ai_summary=for_case(cur,tenant,str(item['case_id']),channel_id=case['channel_id'],
                  conversation_id=case['conversation_id'],selected_ids=[str(row['id']) for row in messages])
        request=str(item['request_id']);target={'channel_id':str(item['channel_id']),'conversation_id':item['conversation_id']}
        consultation=prepare(profile,{'request_id':request,'case_id':str(item['case_id']),
          'channel_id':case['channel_id'],'conversation_id':case['conversation_id'],
          'destination_channel_id':target['channel_id'],'destination_conversation_id':target['conversation_id'],
          'message_ids':[str(row['id']) for row in messages],'media_ids':[],
          'summary':ai_summary['text'],
          'confirm':True,'include_customer_identity':False},'https://crm.pro.az',ai_summary=ai_summary,auto_dispatch_id=item_id)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE saas_ai_case_developer_dispatches SET consultation_id=%s::uuid,updated_at=now() WHERE id=%s::uuid AND state='preparing'",(consultation['id'],item_id))
            conn.commit()
        if consultation.get('reused'):
            # Never convert an employee's prepared request into an automatic
            # send, or repeat an already attempted external operation.
            state='blocked'
            reason='existing_consultation_requires_review'
        else:
            result=dispatch(profile,consultation['id'],request,auto_dispatch_id=item_id)
            state=result.get('state') if result.get('state') in {'sent','pending','unknown','blocked'} else 'unknown'
            reason=str(result.get('error') or '')[:240]
    except TenantLinearError as error:
        state='blocked';reason=str(error)[:240]
    except Exception:
        state='unknown';reason='dispatch_result_requires_receipt_reconciliation'
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_ai_case_developer_dispatches SET state=%s,reason=%s,updated_at=now()
              WHERE id=%s::uuid AND state='preparing' ''',(state,reason,item_id))
            if state=='blocked' and item.get('owner_id'):
                cur.execute('''INSERT INTO saas_tenant_notifications(id,tenant_id,telegram_id,event_key,title,body,target_url)
                  VALUES(%s::uuid,%s::uuid,%s,%s,'Автоотправка обращения заблокирована',%s,%s) ON CONFLICT DO NOTHING''',
                  (uuid.uuid4(),tenant,item['owner_id'],'auto_case_dispatch:'+str(item_id),reason,'/app?view=support&support_ticket_id='+str(item['case_id'])))
        conn.commit()


def reconcile():
    """Recover correlation after webhook-before-receipt or a transient DB error.

    Only already stored inbound evidence in exact receipted destinations is read.
    The receipt and actual quote/CONSULT correlation are independently rechecked.
    """
    with _connect() as conn:
        flow.require_schema(conn)
        with conn.cursor() as cur:
            # A crashed provider attempt cannot remain an optimistic spinner or
            # become eligible for another POST. A later signed echo can recover it.
            cur.execute("UPDATE saas_consultation_group_deliveries SET state='unknown',updated_at=now() WHERE state='sending' AND updated_at<now()-interval '5 minutes'")
            # Recover pending/unknown sends only from server-marked signed
            # outbound evidence. Never POST again and never infer from body.
            cur.execute('''SELECT DISTINCT d.tenant_id::text,d.channel_id::text,d.conversation_id,m.raw,m.body AS observed_body
              FROM saas_consultation_group_deliveries d JOIN saas_internal_consultations c
                ON c.tenant_id=d.tenant_id AND c.id=d.consultation_id
              JOIN saas_connect_conversations v ON v.tenant_id=d.tenant_id AND v.connect_channel_id=d.channel_id AND v.conversation_id=d.conversation_id
              JOIN saas_crm_messages m ON m.tenant_id=d.tenant_id AND m.kommo_lead_id=v.lead_id AND m.channel='connect:'||d.channel_id::text
              WHERE d.state IN ('sending','pending','unknown') AND m.direction='outgoing'
                AND m.raw->>'_crm_signed_outbound_receipt'='true'
                AND (m.raw->>'id'=d.connect_message_id OR lower(m.raw->>'originClientMessageId')=d.request_id::text OR lower(m.raw->>'clientMessageId')=d.request_id::text)
              LIMIT 100''')
            for row in cur.fetchall():
                flow.observe_outbound_receipt(cur,row['tenant_id'],row['channel_id'],row['conversation_id'],{**row['raw'],'text':row['raw'].get('text') or row['observed_body']})
            # Release delivery locks before derived replies lock consultations.
            # Dispatch locks consultation -> delivery; never invert that order.
            conn.commit()
            cur.execute('''SELECT DISTINCT d.tenant_id::text,d.channel_id::text,d.conversation_id,
              m.external_id,m.body,m.message_type,m.raw,m.author_name,COALESCE(m.happened_at,m.created_at) AS happened_at
              FROM saas_consultation_group_deliveries d JOIN saas_internal_consultations c
              ON c.tenant_id=d.tenant_id AND c.id=d.consultation_id
              JOIN saas_connect_conversations v ON v.tenant_id=d.tenant_id AND v.connect_channel_id=d.channel_id AND v.conversation_id=d.conversation_id
              JOIN saas_crm_messages m ON m.tenant_id=d.tenant_id AND m.kommo_lead_id=v.lead_id AND m.channel='connect:'||d.channel_id::text
              WHERE d.state='sent' AND m.direction='incoming' AND m.message_type IN ('text','image','video','audio','voice','document','ptt','voice_message','photo','file') AND m.created_at>=d.created_at
                AND (m.raw->>'quotedExternalMessageId'=d.provider_message_id OR m.body ILIKE '%%CONSULT:'||d.consultation_id::text||'%%' OR m.body ILIKE '%%CASE:'||d.consultation_id::text||'%%'
                  OR m.raw->>'caption' ILIKE '%%CASE:'||d.consultation_id::text||'%%' OR m.raw->>'caption' ILIKE '%%CONSULT:'||d.consultation_id::text||'%%')
                AND NOT EXISTS(SELECT 1 FROM saas_dialog_consultation_replies r WHERE r.tenant_id=d.tenant_id
                  AND r.channel_id=d.channel_id AND r.conversation_id=d.conversation_id AND (r.message_id=CASE WHEN m.external_id ~ '^connect:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' THEN substring(m.external_id from 9)
                    WHEN jsonb_typeof(m.raw->'id')='string' AND m.raw->>'id'<>'' THEN m.raw->>'id'
                    WHEN m.external_id LIKE 'connect:%%' THEN substring(m.external_id from 9) ELSE m.external_id END
                    OR r.message_id=m.external_id OR r.source_external_id=m.external_id
                    OR (m.external_id LIKE 'connect:%%' AND r.message_id=substring(m.external_id from 9))))
              LIMIT 100''')
            count=0
            for row in cur.fetchall():
                raw=row.get('raw') if isinstance(row.get('raw'),dict) else {}
                if raw.get('ai_origin') is True or raw.get('aiOrigin') is True:continue
                linked=flow.record_reply(cur,row['tenant_id'],row['channel_id'],row['conversation_id'],
                    flow.message_identity(row['external_id'],raw),row['body'] or str(raw.get('caption') or ''),raw.get('quotedExternalMessageId'),
                    row.get('author_name') or '',row['happened_at'],message_type=row['message_type'],source_external_id=row['external_id'])
                count+=bool(linked)
                if linked and raw.get('quotedExternalMessageId'):
                    from tenant_ai_agent_connect import enqueue_case_developer_followup
                    enqueue_case_developer_followup(cur,row['tenant_id'],linked,source_external_id=row['external_id'],
                        quoted_message_id=str(raw['quotedExternalMessageId']))
            # Recover a saved, authoritative reply if the webhook path committed
            # the receipt but its follow-up enqueue failed. The event fingerprint
            # makes this reconciliation idempotent; it never sends provider I/O.
            cur.execute('''SELECT r.id::text AS reply_id,r.tenant_id::text,r.source_external_id,
                r.channel_id::text,r.conversation_id,m.raw
              FROM saas_dialog_consultation_replies r
              JOIN saas_internal_consultations c ON c.tenant_id=r.tenant_id AND c.id=r.consultation_id
              JOIN saas_support_tickets t ON t.tenant_id=c.tenant_id AND t.id=c.case_id
              JOIN saas_crm_messages m ON m.tenant_id=r.tenant_id AND m.external_id=r.source_external_id
                AND m.channel='connect:'||r.channel_id::text AND m.direction='incoming'
              WHERE r.source_kind='provider' AND r.message_type='text' AND t.status<>'resolved'
                AND r.followup_requested_at IS NOT NULL
                AND r.source_external_id<>'' AND m.raw->>'quotedExternalMessageId' IS NOT NULL
              ORDER BY r.created_at DESC LIMIT 100''')
            for saved in cur.fetchall():
                try:
                    from tenant_ai_agent_connect import enqueue_case_developer_followup
                    count+=bool(enqueue_case_developer_followup(cur,saved['tenant_id'],saved['reply_id'],
                        source_external_id=saved['source_external_id'],
                        quoted_message_id=str((saved.get('raw') or {}).get('quotedExternalMessageId') or '')))
                except Exception:
                    # Fail closed; a later scheduled reconciliation can retry.
                    continue
        # Historical CASE replies already linked by the previous handler remain
        # visible even if their original quoted webhook metadata was not retained.
        with conn.cursor() as cur:
            cur.execute('''SELECT g.*,COALESCE(m.author_name,'') AS author,
                COALESCE(m.happened_at,m.created_at,g.created_at) AS happened_at,m.message_identity,m.external_id AS source_external_id,m.message_type AS original_type
              FROM saas_consultation_group_replies g
              JOIN saas_internal_consultations c ON c.tenant_id=g.tenant_id AND c.id=g.consultation_id
              JOIN saas_connect_conversations v ON v.tenant_id=g.tenant_id AND v.connect_channel_id=g.channel_id AND v.conversation_id=g.conversation_id
              LEFT JOIN LATERAL (SELECT m0.author_name,m0.happened_at,m0.created_at,m0.external_id,m0.message_type,
                    CASE WHEN m0.external_id ~ '^connect:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' THEN substring(m0.external_id from 9)
                    WHEN jsonb_typeof(m0.raw->'id')='string' AND m0.raw->>'id'<>'' THEN m0.raw->>'id'
                      WHEN m0.external_id LIKE 'connect:%%' THEN substring(m0.external_id from 9) ELSE m0.external_id END AS message_identity
                  FROM saas_crm_messages m0
                WHERE m0.tenant_id=g.tenant_id AND m0.kommo_lead_id=v.lead_id AND m0.channel='connect:'||g.channel_id::text
                  AND (m0.external_id=g.message_id OR m0.external_id='connect:'||g.message_id OR m0.raw->>'id'=g.message_id)
                ORDER BY m0.created_at LIMIT 1) m ON TRUE
              WHERE EXISTS(SELECT 1 FROM saas_consultation_group_deliveries d
                WHERE d.tenant_id=g.tenant_id AND d.consultation_id=g.consultation_id AND d.channel_id=g.channel_id
                  AND d.conversation_id=g.conversation_id AND d.state='sent')
                AND NOT EXISTS(SELECT 1 FROM saas_dialog_consultation_replies r WHERE r.tenant_id=g.tenant_id
                  AND r.channel_id=g.channel_id AND r.conversation_id=g.conversation_id
                    AND (r.message_id=COALESCE(m.message_identity,g.message_id) OR r.message_id=m.external_id OR r.source_external_id=m.external_id
                    OR (m.external_id LIKE 'connect:%%' AND r.message_id=substring(m.external_id from 9))))
              ORDER BY g.created_at LIMIT 100''')
            for row in cur.fetchall():
                reply_id=flow._store_reply(cur,str(row['tenant_id']),str(row['consultation_id']),str(row['channel_id']),
                    row['conversation_id'],row.get('message_identity') or row['message_id'],row['body'],row['author'],row['happened_at'],source_kind='legacy',
                    message_type=flow._reply_kind(row.get('original_type') or 'text'),source_external_id=row.get('source_external_id') or '')
                if reply_id:
                    from tenant_ai_agents_store import _audit
                    _audit(cur,str(row['tenant_id']),None,'consultation_legacy_reply_reconciled','consultation',str(row['consultation_id']),
                        {'reply_id':reply_id,'source_message_id':row['message_id'],'source':'existing_group_reply'})
                    count+=1
        conn.commit()
        return count


async def deliver(logger):
    await asyncio.to_thread(reconcile)
    await asyncio.to_thread(reconcile_auto_dispatches)
    for item_id in await asyncio.to_thread(auto_dispatch_queue):
        await asyncio.to_thread(dispatch_auto_case,item_id)
    for item in await asyncio.to_thread(pending):
        try:
            # Normal Copilot revalidates membership, employee AI permission,
            # current Manual assignment, language, knowledge and company budget.
            # Its durable claim rejects concurrent/inflight/unknown requests.
            await asyncio.to_thread(flow.draft,{'tenant_id':item['tenant_id'],'telegram_id':item['created_by']},
                item['id'],{'request_id':str(uuid.uuid5(uuid.NAMESPACE_URL,'dialog-consultation-draft:'+item['tenant_id']+':'+item['id']))})
        except TenantLinearError:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE saas_dialog_consultation_replies SET draft_retry_after=now()+interval '5 minutes' WHERE tenant_id=%s::uuid AND id=%s::uuid AND draft_state='pending'",(item['tenant_id'],item['id']))
                conn.commit()
            logger.info('Private consultation follow-up deferred by current access or assignment')
        except Exception:
            logger.warning('Private consultation follow-up needs review; no automatic retry of uncertain AI calls')
