"""Allowlisted Native CRM trigger actions for existing Connect AI jobs.

Writes are doubly gated by a default-off process flag and a default-off
tenant owner setting. Values come only from validated trigger configuration
and verified CRM/Connect records; this module exposes no generic SQL/API tool.
"""
from __future__ import annotations
import asyncio, uuid
from tenant_ai_agent_runtime_flags import enabled as runtime_flag
from tenant_ai_agents_schema import ensure_schema
from tenant_ai_agent_connect import scope_matches
from tenant_ai_dialog_scope import job_scope
from tenant_platform import _connect,_json

NATIVE_ACTIONS={'native_find_create_contact','native_find_create_company','native_find_create_deal','native_move_stage',
                'native_assign_responsible','native_create_task','native_get_deal_status','native_get_task_status'}


def _allowed(cur,job,*,for_write=True):
    if (job.get('provider')!='connect' or job.get('payload',{}).get('test_mode') is True
            or '@g.us' in str(job.get('conversation_id') or '') or not runtime_flag('AI_NATIVE_CRM_ACTIONS_ENABLED')):
        return False
    cur.execute('''SELECT j.state,j.revision,j.lease_token,a.enabled,a.mode,a.scopes,s.enabled AS settings_enabled,
      s.native_crm_actions_enabled,s.operating_mode,s.emergency_stop,t.status AS tenant_status,cs.state AS conversation_state,
      EXISTS(SELECT 1 FROM saas_ai_agent_connect_test_mode tm WHERE tm.tenant_id=j.tenant_id AND tm.enabled=TRUE
        AND tm.verified=TRUE AND tm.channel_id=j.scope_channel_id AND tm.conversation_id=j.conversation_id
        AND (tm.agent_id IS NULL OR tm.agent_id=j.agent_id)) AS test_route
      FROM saas_ai_agent_jobs j JOIN saas_ai_agents a ON a.tenant_id=j.tenant_id AND a.id=j.agent_id
      JOIN saas_ai_agent_settings s ON s.tenant_id=j.tenant_id JOIN saas_tenants t ON t.id=j.tenant_id
      LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=j.tenant_id AND cs.provider='connect'
        AND cs.channel_id=j.scope_channel_id AND cs.conversation_id=j.conversation_id
      WHERE j.tenant_id=%s::uuid AND j.agent_id=%s::uuid AND j.provider='connect'
        AND j.channel_id=%s AND j.conversation_id=%s AND j.scope_channel_id=%s
        AND j.state='processing' AND j.revision=%s AND j.lease_token=%s FOR UPDATE OF j,a,s,t''',
      (job['tenant_id'],job['agent_id'],job['channel_id'],job['conversation_id'],job_scope(job),job['revision'],job['lease_token']))
    row=cur.fetchone()
    if not row:return False
    if (row['enabled'] is not True or row['mode']!='automatic' or row['settings_enabled'] is not True
        or row['native_crm_actions_enabled'] is not True or row['operating_mode']!='active'
        or row['emergency_stop'] is True or row['tenant_status']!='active'
        or row['conversation_state'] in {'human','paused'} or row['test_route'] is True
        or not scope_matches(row.get('scopes'),str(job['channel_id']))):return False
    cur.execute("""SELECT 1 FROM saas_tenant_integrations i JOIN saas_connect_channels c
      ON c.tenant_id=i.tenant_id AND c.connect_channel_id=%s::uuid AND c.active=TRUE
      JOIN saas_connect_conversations r ON r.tenant_id=c.tenant_id AND r.connect_channel_id=c.connect_channel_id
        AND r.conversation_id=%s AND (%s::bigint IS NULL OR r.lead_id=%s)
      WHERE i.tenant_id=%s::uuid AND i.provider='connect' AND i.status='connected'""",
      (job['channel_id'],job['conversation_id'],job.get('lead_id'),job.get('lead_id'),job['tenant_id']))
    return bool(cur.fetchone())


def _reserve(job,trigger_id,index,action,payload):
    from tenant_ai_pilot import processing_allowed
    if not processing_allowed(job):return None,'blocked',None
    if not runtime_flag('AI_NATIVE_CRM_ACTIONS_ENABLED'):return None,'disabled',None
    key=f'native:{trigger_id}:{index}'
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            if not _allowed(cur,job):return None,'blocked',None
            ident=uuid.uuid4()
            cur.execute("""INSERT INTO saas_ai_agent_action_runs(tenant_id,id,agent_id,event_fingerprint,action_key,action_type,state,payload)
              VALUES(%s::uuid,%s,%s::uuid,%s,%s,%s,'running',%s::jsonb)
              ON CONFLICT(tenant_id,event_fingerprint,action_key) DO NOTHING RETURNING id""",
              (job['tenant_id'],ident,job['agent_id'],job['event_fingerprint'],key,action,_json(payload)))
            row=cur.fetchone()
            if not row:
                cur.execute("SELECT state,result FROM saas_ai_agent_action_runs WHERE tenant_id=%s::uuid AND event_fingerprint=%s AND action_key=%s",(job['tenant_id'],job['event_fingerprint'],key))
                prior=cur.fetchone() or {}
                state=prior.get('state','unknown')
                if state=='running':
                    cur.execute("UPDATE saas_ai_agent_action_runs SET state='unknown',result=%s::jsonb,updated_at=now() WHERE tenant_id=%s::uuid AND event_fingerprint=%s AND action_key=%s AND state='running'",(_json({'code':'worker_interrupted_after_reservation'}),job['tenant_id'],job['event_fingerprint'],key))
                    state='unknown';prior['result']={'code':'worker_interrupted_after_reservation'}
                conn.commit();return None,state,prior.get('result')
        conn.commit()
    return str(ident),'running',None


def _still_allowed(job):
    from tenant_ai_pilot import processing_allowed
    if not processing_allowed(job):return False
    if not runtime_flag('AI_NATIVE_CRM_ACTIONS_ENABLED'):return False
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:return _allowed(cur,job)


def _finish(job,ident,state,result):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_ai_agent_action_runs SET state=%s,result=%s::jsonb,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='running'",(state,_json(result),job['tenant_id'],ident))
        conn.commit()


def _blocked_before_write(job,ident):
    result={'code':'handoff_or_scope_changed'}
    _finish(job,ident,'blocked',result)
    return result


def _contact(job):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT contact_id FROM saas_crm_native_contact_conversations WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s::uuid AND conversation_id=%s",(job['tenant_id'],job['channel_id'],job['conversation_id']))
            row=cur.fetchone()
    return str(row['contact_id']) if row else ''


def _create_verified_contact(job,source):
    """Create/link only from the direct WhatsApp JID on an inbound event."""
    from tenant_ai_agent_connect import _whatsapp_phone
    if source.get('direction')!='incoming':return ''
    phone=_whatsapp_phone(str(job.get('conversation_id') or ''))
    if not phone:return ''
    try:channel=str(uuid.UUID(str(job.get('channel_id') or '')))
    except (ValueError,TypeError,AttributeError):return ''
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            from tenant_crm_native_contacts import upsert_whatsapp_contact
            ident=upsert_whatsapp_contact(cur,str(job['tenant_id']),channel,str(job['conversation_id']),phone,
                str(source.get('author_name') or '')[:180],str(source.get('external_id') or '')[:240])
        conn.commit()
    return str(ident or '')


async def execute(job,source,triggers,*,tenant_enabled=False,automatic=False):
    """Execute matched static trigger operations; every write is journaled and reauthorized."""
    matches=[t for t in triggers if any(a in NATIVE_ACTIONS for a in (t.get('actions') or []))]
    if not matches:return {'actions':[],'blocked':False,'unknown':False}
    if '@g.us' in str(job.get('conversation_id') or ''):return {'actions':[],'blocked':True,'unknown':False}
    if not automatic or tenant_enabled is not True or not runtime_flag('AI_NATIVE_CRM_ACTIONS_ENABLED'):
        return {'actions':[{'action':a,'state':'proposed'} for t in matches for a in (t.get('actions') or []) if a in NATIVE_ACTIONS],'blocked':False,'unknown':False}
    from tenant_crm_native_companies import find_or_create_company,link_entity
    from tenant_crm_native_deals import create_deal,find_chat_deal,set_deal_stage,assign_deal_responsible,get_deal
    from tenant_crm_native_tasks import create_task,get_task
    outcomes=[];context={'contact_id':_contact(job),'company_id':'','deal_id':'','task_id':''}
    for trigger in matches:
        cond=trigger.get('condition') or {}
        for index,action in enumerate(trigger.get('actions') or []):
            if action not in NATIVE_ACTIONS:continue
            if not await asyncio.to_thread(_still_allowed,job):return {'actions':outcomes,'blocked':True,'unknown':False}
            if action=='native_find_create_contact':
                action_id,state,previous=_reserve(job,trigger['id'],index,action,{})
                outcome=previous or {'contact_id':context['contact_id'],'found':bool(context['contact_id'])}
                if state=='running':
                    if not context['contact_id']:
                        if not await asyncio.to_thread(_still_allowed,job):
                            outcome=await asyncio.to_thread(_blocked_before_write,job,action_id)
                            return {'actions':outcomes+[{'action':action,'state':'blocked','result':outcome}],'blocked':True,'unknown':False}
                        context['contact_id']=await asyncio.to_thread(_create_verified_contact,job,source)
                    if not context['contact_id']:
                        state='blocked';outcome={'code':'verified_personal_whatsapp_identity_unavailable'}
                        await asyncio.to_thread(_finish,job,action_id,'blocked',outcome)
                    else:
                        outcome={'contact_id':context['contact_id'],'found':True}
                        await asyncio.to_thread(_finish,job,action_id,'accepted',outcome);state='accepted'
            elif action=='native_find_create_company':
                # A company name must be present verbatim in the current customer
                # message; AI-inferred names are never persisted as fact.
                name=str(cond.get('native_company_name') or '').strip()
                if not name or name.casefold() not in str(source.get('body') or '').casefold():state='blocked';outcome={'code':'company_name_not_confirmed'}
                else:
                    action_id,state,previous=_reserve(job,trigger['id'],index,action,{'company_name':name})
                    if state=='running':
                        if not await asyncio.to_thread(_still_allowed,job):
                            outcome=await asyncio.to_thread(_blocked_before_write,job,action_id)
                            return {'actions':outcomes+[{'action':action,'state':'blocked','result':outcome}],'blocked':True,'unknown':False}
                        company=await asyncio.to_thread(find_or_create_company,str(job['tenant_id']),name)
                        context['company_id']=str(company['id'])
                        if context['contact_id']:await asyncio.to_thread(link_entity,str(job['tenant_id']),context['company_id'],'contact',context['contact_id'])
                        outcome={'company_id':context['company_id']};await asyncio.to_thread(_finish,job,action_id,'accepted',outcome);state='accepted'
                    elif state=='accepted':
                        outcome=previous or {'code':'previous_result_missing'}
                        context['company_id']=str(outcome.get('company_id') or '')
                    else:outcome=previous or {'code':state}
            elif action=='native_find_create_deal':
                title=str(cond.get('native_deal_title') or '').strip(); pipeline=str(cond.get('native_pipeline_id') or '').strip();stage=str(cond.get('native_stage_id') or '').strip()
                if not context['contact_id'] or not title or not pipeline or not stage:state='blocked';outcome={'code':'deal_identity_or_scope_missing'}
                else:
                    existing=await asyncio.to_thread(find_chat_deal,str(job['tenant_id']),str(job['channel_id']),str(job['conversation_id']))
                    if existing:
                        if str(existing.get('pipeline_id'))!=pipeline:
                            state='blocked';outcome={'code':'pipeline_scope_mismatch'}
                            outcomes.append({'action':action,'state':state,'result':outcome});return {'actions':outcomes,'blocked':True,'unknown':False}
                        action_id,state,previous=_reserve(job,trigger['id'],index,action,{'title':title,'pipeline_id':pipeline,'stage_id':stage})
                        context['deal_id']=str(existing['id']);outcome={'deal_id':context['deal_id'],'existing':True}
                        if state=='running':
                            state='accepted';await asyncio.to_thread(_finish,job,action_id,'accepted',outcome)
                        elif state=='accepted':outcome=previous or outcome
                    else:
                        action_id,state,previous=_reserve(job,trigger['id'],index,action,{'title':title,'pipeline_id':pipeline,'stage_id':stage})
                        if state=='running':
                            if not await asyncio.to_thread(_still_allowed,job):
                                outcome=await asyncio.to_thread(_blocked_before_write,job,action_id)
                                return {'actions':outcomes+[{'action':action,'state':'blocked','result':outcome}],'blocked':True,'unknown':False}
                            ident=str(uuid.uuid5(uuid.NAMESPACE_URL,f"native-ai-deal:{job['tenant_id']}:{job['event_fingerprint']}:{trigger['id']}:{index}"))
                            deal=await asyncio.to_thread(create_deal,str(job['tenant_id']),title,pipeline,stage,contact_id=context['contact_id'],responsible_id=cond.get('native_responsible_id') or None,channel_id=job['channel_id'],conversation_id=job['conversation_id'],deal_id=ident)
                            context['deal_id']=str(deal['id'])
                            if context['company_id']:await asyncio.to_thread(link_entity,str(job['tenant_id']),context['company_id'],'deal',context['deal_id'])
                            outcome={'deal_id':context['deal_id']};await asyncio.to_thread(_finish,job,action_id,'accepted',outcome)
                            state='accepted'
                        else:outcome=previous or {'code':state}
            else:
                if not context['deal_id']:
                    deal=await asyncio.to_thread(find_chat_deal,str(job['tenant_id']),str(job['channel_id']),str(job['conversation_id']))
                    context['deal_id']=str(deal['id']) if deal else ''
                if action in {'native_move_stage','native_assign_responsible','native_create_task','native_get_deal_status'} and not context['deal_id']:
                    state='blocked';outcome={'code':'deal_not_found'}
                elif action=='native_get_task_status':
                    task_id=str(cond.get('native_task_id') or context['task_id'])
                    action_id,state,previous=_reserve(job,trigger['id'],index,action,{'task_id':task_id})
                    task=await asyncio.to_thread(get_task,str(job['tenant_id']),task_id) if task_id and state=='running' else None
                    if state=='running':
                        scoped=bool(task and str(task.get('channel_id') or '')==str(job['channel_id'])
                            and str(task.get('conversation_id') or '')==str(job['conversation_id']))
                        state='accepted' if scoped else 'blocked'; outcome=({'task_id':task_id,'status':task.get('status')} if scoped else {'code':'task_outside_conversation'})
                        await asyncio.to_thread(_finish,job,action_id,state,outcome)
                    else:outcome=previous or {'code':state}
                elif action=='native_get_deal_status':
                    action_id,state,previous=_reserve(job,trigger['id'],index,action,{'deal_id':context['deal_id']})
                    deal=await asyncio.to_thread(get_deal,str(job['tenant_id']),context['deal_id']) if context['deal_id'] and state=='running' else None
                    if state=='running':
                        scoped=deal and str(deal.get('pipeline_id'))==str(cond.get('native_pipeline_id'))
                        state='accepted' if scoped else 'blocked';outcome={'deal_id':context['deal_id'],'stage_id':deal.get('stage_id') if scoped else None}
                        await asyncio.to_thread(_finish,job,action_id,state,outcome)
                    else:outcome=previous or {'code':state}
                else:
                    if action=='native_move_stage':
                        current_deal=await asyncio.to_thread(get_deal,str(job['tenant_id']),context['deal_id'])
                        if not current_deal or str(current_deal.get('pipeline_id'))!=str(cond.get('native_pipeline_id')):
                            state='blocked';outcome={'code':'pipeline_scope_mismatch'}
                            outcomes.append({'action':action,'state':state,'result':outcome});return {'actions':outcomes,'blocked':True,'unknown':False}
                    action_id,state,previous=_reserve(job,trigger['id'],index,action,cond)
                    if state=='running':
                        if not await asyncio.to_thread(_still_allowed,job):
                            outcome=await asyncio.to_thread(_blocked_before_write,job,action_id)
                            return {'actions':outcomes+[{'action':action,'state':'blocked','result':outcome}],'blocked':True,'unknown':False}
                        if action=='native_move_stage':result=await asyncio.to_thread(set_deal_stage,str(job['tenant_id']),context['deal_id'],cond['native_stage_id'])
                        elif action=='native_assign_responsible':result=await asyncio.to_thread(assign_deal_responsible,str(job['tenant_id']),context['deal_id'],cond['native_responsible_id'])
                        else:
                            text=str(cond.get('native_task_text') or '').strip()
                            responsible=cond.get('native_responsible_id')
                            if not responsible:
                                deal=await asyncio.to_thread(get_deal,str(job['tenant_id']),context['deal_id']);responsible=(deal or {}).get('responsible_id')
                            if not text or not responsible:raise ValueError('task_text_or_responsible_missing')
                            request_id=str(uuid.uuid5(uuid.NAMESPACE_URL,f"native-ai-task:{job['tenant_id']}:{job['event_fingerprint']}:{trigger['id']}:{index}"))
                            result=await asyncio.to_thread(create_task,str(job['tenant_id']),context['deal_id'],text,responsible,idempotency_key=request_id)
                            context['task_id']=str(result['id'])
                        if not result:raise ValueError('native_record_missing')
                        outcome={'id':str(result.get('id') or context.get('task_id') or ''),'state':result.get('status') or result.get('stage_id') or 'updated'}
                        await asyncio.to_thread(_finish,job,action_id,'accepted',outcome)
                        state='accepted'
                    else:outcome=previous or {'code':state}
            if state=='blocked':outcomes.append({'action':action,'state':'blocked','result':outcome});return {'actions':outcomes,'blocked':True,'unknown':False}
            if state=='unknown':outcomes.append({'action':action,'state':'unknown','result':outcome});return {'actions':outcomes,'blocked':False,'unknown':True}
            outcomes.append({'action':action,'state':state,'result':outcome})
    return {'actions':outcomes,'blocked':False,'unknown':False}
