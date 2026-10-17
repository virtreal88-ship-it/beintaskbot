"""Allowlisted and idempotent CRM actions for configured agent triggers."""
import asyncio
import re
import uuid
from tenant_ai_dialog_scope import job_scope
from datetime import datetime, timedelta, timezone
from tenant_ai_agent_actions import enabled_triggers, matching_triggers, handoff
from tenant_ai_agents_schema import ensure_schema
from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect, _json
from tenant_ai_agent_test_mode import assert_job_allowed
from tenant_ai_agent_runtime_flags import enabled as runtime_flag

PHONE_RE=re.compile(r"(?<![\w])\+[1-9]\d{7,14}(?!\d)")
CRM_ACTIONS={'create_task','update_phone','move_stage'}


def phone_candidate(text):
    values=list(dict.fromkeys(PHONE_RE.findall(str(text or '')[:6000])))
    return values[0] if len(values)==1 else ''


def _first_inquiry(tenant,chat,message_id):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT first_inbound_message_id FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='kommo' AND conversation_id=%s",(tenant,chat))
            row=cur.fetchone()
    return bool(row and str(row.get('first_inbound_message_id') or '')==str(message_id))


def _reserve_action(job,key,action_type,payload):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT j.state,j.revision,j.lease_token,a.enabled,a.mode,s.enabled AS agents_enabled,
              s.crm_actions_enabled,s.operating_mode,s.emergency_stop,cs.state AS conversation_state,t.status AS tenant_status,
              EXISTS (SELECT 1 FROM saas_ai_agent_test_mode b JOIN saas_tenant_integrations ki
                ON ki.tenant_id=b.tenant_id AND ki.provider='kommo' AND ki.status='connected'
                JOIN saas_tenant_webhook_events e ON e.tenant_id=j.tenant_id AND e.fingerprint=j.event_fingerprint
                WHERE j.provider='kommo' AND (b.agent_id IS NULL OR b.agent_id=j.agent_id) AND b.tenant_id=j.tenant_id AND b.enabled=TRUE AND b.verified=TRUE AND b.emergency_stop=FALSE
                  AND b.account_domain=ki.account_domain AND b.lead_id=j.lead_id AND b.contact_id=CASE WHEN e.payload->>'contact_id' ~ '^[0-9]{1,18}$' THEN (e.payload->>'contact_id')::bigint END
                  AND b.talk_id=j.talk_id AND b.chat_id=j.chat_id AND b.channel_id=j.channel_id) AS test_mode_allowed
              FROM saas_ai_agent_jobs j JOIN saas_ai_agents a ON a.tenant_id=j.tenant_id AND a.id=j.agent_id
              JOIN saas_ai_agent_settings s ON s.tenant_id=j.tenant_id JOIN saas_tenants t ON t.id=j.tenant_id
              LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=j.tenant_id AND cs.provider=j.provider AND cs.conversation_id=j.conversation_id AND cs.channel_id=j.scope_channel_id
              WHERE j.tenant_id=%s::uuid AND j.agent_id=%s::uuid AND j.provider=%s AND j.conversation_id=%s AND j.scope_channel_id=%s
              FOR UPDATE OF j,a,s,t''',(job['tenant_id'],job['agent_id'],job['provider'],job['conversation_id'],job_scope(job)))
            current=cur.fetchone()
            if (not current or current['state']!='processing' or current['revision']!=job['revision']
                    or current['lease_token']!=job['lease_token'] or current['enabled'] is not True
                    or current['mode']!='automatic' or current['agents_enabled'] is not True
                    or current['crm_actions_enabled'] is not True or current['conversation_state'] in {'human','paused'}
                    or current['tenant_status']!='active' or current.get('emergency_stop') is True
                    or (current.get('operating_mode')!='active' and current.get('test_mode_allowed') is not True)
                    or not runtime_flag('AI_AGENTS_CRM_ENABLED')):
                raise TenantLinearError('Agent CRM əməliyyatı üçün icazə yoxdur.',409)
            action_id=uuid.uuid4()
            cur.execute('''INSERT INTO saas_ai_agent_action_runs(tenant_id,id,agent_id,event_fingerprint,action_key,action_type,state,payload)
              VALUES(%s::uuid,%s,%s::uuid,%s,%s,%s,'running',%s::jsonb) ON CONFLICT(tenant_id,event_fingerprint,action_key) DO NOTHING
              RETURNING id''',(job['tenant_id'],action_id,job['agent_id'],job['event_fingerprint'],key,action_type,_json(payload)))
            row=cur.fetchone()
            if not row:
                cur.execute('SELECT state FROM saas_ai_agent_action_runs WHERE tenant_id=%s::uuid AND event_fingerprint=%s AND action_key=%s',(job['tenant_id'],job['event_fingerprint'],key))
                state=(cur.fetchone() or {}).get('state','unknown')
                if state=='running':
                    # A prior worker may have stopped after Kommo accepted the
                    # mutation. Treat the outcome as unknown and never replay it.
                    cur.execute('''UPDATE saas_ai_agent_action_runs SET state='unknown',
                      result=%s::jsonb,updated_at=now() WHERE tenant_id=%s::uuid
                      AND event_fingerprint=%s AND action_key=%s AND state='running' ''',
                      (_json({'code':'worker_interrupted_after_reservation'}),job['tenant_id'],job['event_fingerprint'],key))
                    state='unknown'
                conn.commit()
                return None,state
        conn.commit()
    return str(action_id),'running'


def _action_still_allowed(job):
    if not runtime_flag('AI_AGENTS_CRM_ENABLED'):return False
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT 1 FROM saas_ai_agent_jobs j JOIN saas_ai_agents a ON a.tenant_id=j.tenant_id AND a.id=j.agent_id
              JOIN saas_ai_agent_settings s ON s.tenant_id=j.tenant_id JOIN saas_tenants t ON t.id=j.tenant_id
              LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=j.tenant_id AND cs.provider=j.provider AND cs.conversation_id=j.conversation_id AND cs.channel_id=j.scope_channel_id
              WHERE j.tenant_id=%s::uuid AND j.agent_id=%s::uuid AND j.provider=%s AND j.conversation_id=%s AND j.scope_channel_id=%s
                AND j.state='processing' AND j.revision=%s AND j.lease_token=%s AND a.enabled=TRUE AND a.mode='automatic'
                AND s.enabled=TRUE AND s.crm_actions_enabled=TRUE AND s.emergency_stop=FALSE AND t.status='active'
                AND COALESCE(cs.state,'active') NOT IN ('human','paused')
                AND (s.operating_mode='active' OR EXISTS (SELECT 1 FROM saas_ai_agent_test_mode b JOIN saas_tenant_integrations ki
                  ON ki.tenant_id=b.tenant_id AND ki.provider='kommo' AND ki.status='connected'
                  JOIN saas_tenant_webhook_events e ON e.tenant_id=j.tenant_id AND e.fingerprint=j.event_fingerprint
                  WHERE j.provider='kommo' AND (b.agent_id IS NULL OR b.agent_id=j.agent_id) AND b.tenant_id=j.tenant_id AND b.enabled=TRUE AND b.verified=TRUE AND b.emergency_stop=FALSE
                    AND b.account_domain=ki.account_domain AND b.lead_id=j.lead_id AND b.contact_id=CASE WHEN e.payload->>'contact_id' ~ '^[0-9]{1,18}$' THEN (e.payload->>'contact_id')::bigint END
                    AND b.talk_id=j.talk_id AND b.chat_id=j.chat_id AND b.channel_id=j.channel_id))''',
                (job['tenant_id'],job['agent_id'],job['provider'],job['conversation_id'],job_scope(job),job['revision'],job['lease_token']))
            return bool(cur.fetchone())


def _finish_action(job,action_id,state,result=None):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('UPDATE saas_ai_agent_action_runs SET state=%s,result=%s::jsonb,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND state=\'running\'',
                (state,_json(result or {}),job['tenant_id'],action_id))
        conn.commit()


async def _execute_one(job,trigger,index,action,text,phone,request,generated_task_text=''):
    condition=trigger.get('condition') or {};key=f"{trigger['id']}:{index}"
    payload={'lead_id':job['lead_id'],'talk_id':job['talk_id'],'chat_id':job['chat_id']}
    if action=='update_phone':
        if not phone:return 'blocked'
        payload['phone']=phone
    elif action=='move_stage':
        payload['target_stage_id']=str(condition.get('target_stage_id') or '')
        if not payload['target_stage_id']:return 'blocked'
    elif action=='create_task':
        configured_text=str(condition.get('task_text') or '')
        payload['text']=(str(generated_task_text).strip() if configured_text=='<AI_GENERATED_TASK>'
                         else configured_text)[:500]
        if not payload['text']:return 'blocked'
    else:return 'blocked'
    action_id,state=await asyncio.to_thread(_reserve_action,job,key,action,payload)
    if state!='running':return state
    try:
        lead=await request(str(job['tenant_id']),'GET',f"leads/{int(job['lead_id'])}")
        if int(lead.get('id') or 0)!=int(job['lead_id']):raise TenantLinearError('Deal identity changed.',409)
        if action=='update_phone':
            embedded=lead.get('_embedded') if isinstance(lead.get('_embedded'),dict) else {}
            contacts=embedded.get('contacts') if isinstance(embedded.get('contacts'),list) else []
            if len(contacts)!=1 or not contacts[0].get('id'):raise TenantLinearError('Contact is ambiguous.',409)
            contact_id=int(contacts[0]['id']);contact=await request(str(job['tenant_id']),'GET',f'contacts/{contact_id}')
            fields=contact.get('custom_fields_values') or (contact.get('_embedded') or {}).get('custom_fields_values') or []
            existing=[]
            for field in fields if isinstance(fields,list) else []:
                if str(field.get('field_code') or '').upper()=='PHONE':
                    existing.extend(str(v.get('value') or '') for v in field.get('values',[]) if isinstance(v,dict))
            if any(value and value!=phone for value in existing):raise TenantLinearError('Existing phone conflicts.',409)
            if not existing:
                if not await asyncio.to_thread(_action_still_allowed,job):raise TenantLinearError('CRM action disabled before commit.',409)
                await request(str(job['tenant_id']),'PATCH',f'contacts/{contact_id}',json_body=[{'id':contact_id,'custom_fields_values':[{'field_code':'PHONE','values':[{'value':phone,'enum_code':'WORK'}]}]}])
                verified=await request(str(job['tenant_id']),'GET',f'contacts/{contact_id}')
                verify=verified.get('custom_fields_values') or (verified.get('_embedded') or {}).get('custom_fields_values') or []
                if not any(str(v.get('value') or '')==phone for f in verify if isinstance(f,dict) and str(f.get('field_code') or '').upper()=='PHONE' for v in f.get('values',[]) if isinstance(v,dict)):
                    raise RuntimeError('Phone update result unknown')
            result={'contact_id':contact_id,'phone':phone}
        elif action=='move_stage':
            target=int(payload['target_stage_id'])
            with _connect() as conn:
                ensure_schema(conn)
                with conn.cursor() as cur:
                    cur.execute('''SELECT 1 FROM saas_tenant_pipeline_stages s JOIN saas_tenant_pipelines p
                      ON p.tenant_id=s.tenant_id AND p.pipeline_id=s.pipeline_id
                      WHERE s.tenant_id=%s::uuid AND s.stage_id=%s AND p.pipeline_id=%s AND p.active=TRUE''',
                      (job['tenant_id'],target,int(lead.get('pipeline_id') or 0)))
                    allowed=bool(cur.fetchone())
            if not allowed:raise TenantLinearError('Target stage is outside tenant pipelines.',403)
            if not await asyncio.to_thread(_action_still_allowed,job):raise TenantLinearError('CRM action disabled before commit.',409)
            await request(str(job['tenant_id']),'PATCH',f"leads/{int(job['lead_id'])}",json_body=[{'id':int(job['lead_id']),'status_id':target}])
            verified=await request(str(job['tenant_id']),'GET',f"leads/{int(job['lead_id'])}")
            if str(verified.get('status_id') or '')!=str(target):raise RuntimeError('Stage update result unknown')
            result={'stage_id':str(target)}
        else:
            deadline=int((datetime.now(timezone.utc)+timedelta(days=1)).timestamp())
            responsible=int(lead.get('responsible_user_id') or 0)
            if not responsible:raise TenantLinearError('Deal has no responsible manager.',409)
            task_id=str(uuid.uuid5(uuid.NAMESPACE_URL,f"ai-task:{job['tenant_id']}:{job['event_fingerprint']}:{key}"))
            if not await asyncio.to_thread(_action_still_allowed,job):raise TenantLinearError('CRM action disabled before commit.',409)
            created=await request(str(job['tenant_id']),'POST','tasks',json_body=[{'request_id':task_id,'text':payload['text'],'complete_till':deadline,
              'entity_id':int(job['lead_id']),'entity_type':'leads','responsible_user_id':responsible,'task_type_id':1}])
            embedded=created.get('_embedded') if isinstance(created,dict) else None
            tasks=embedded.get('tasks') if isinstance(embedded,dict) else None
            if not isinstance(tasks,list) or len(tasks)!=1 or not tasks[0].get('id'):
                raise RuntimeError('Task acceptance could not be verified')
            result={'request_id':task_id,'responsible_user_id':responsible}
        await asyncio.to_thread(_finish_action,job,action_id,'accepted',result)
        return 'accepted'
    except TenantLinearError as error:
        await asyncio.to_thread(_finish_action,job,action_id,'blocked',{'code':error.status})
        return 'blocked'
    except Exception:
        await asyncio.to_thread(_finish_action,job,action_id,'unknown',{'code':'provider_result_unknown'})
        return 'unknown'


async def execute_matching_actions(job,source,request,*,automatic,crm_enabled,crm_kill_switch,
                                   generated_task_text=''):
    await asyncio.to_thread(assert_job_allowed,job)
    triggers=await asyncio.to_thread(enabled_triggers,job['tenant_id'],job['agent_id'])
    phone=phone_candidate(source.get('body'))
    first=await asyncio.to_thread(_first_inquiry,job['tenant_id'],job['chat_id'],source.get('external_id'))
    matching=matching_triggers(triggers,source.get('body'),first_inquiry=first,phone=phone)
    if any('handoff' in (row.get('actions') or []) for row in matching):
        await asyncio.to_thread(handoff,job,'agent_trigger_handoff')
        return {'handoff':True,'blocked':False,'unknown':False}
    if not automatic or not crm_enabled or not crm_kill_switch:
        return {'handoff':False,'blocked':False,'unknown':False,'actions':[{'trigger':row['name'],'action':action,'state':'proposed',
          'dynamic_task_text':action=='create_task' and (row.get('condition') or {}).get('task_text')=='<AI_GENERATED_TASK>'}
          for row in matching for action in (row.get('actions') or []) if action in CRM_ACTIONS]}
    outcomes=[]
    for trigger in matching:
        for index,action in enumerate(trigger.get('actions') or []):
            if action in {'handoff','call_expert'}:continue
            status=await _execute_one(job,trigger,index,action,source.get('body'),phone,request,
                                      generated_task_text)
            outcomes.append({'trigger':trigger['name'],'action':action,'state':status})
            if status=='unknown':
                await asyncio.to_thread(handoff,job,'crm_action_result_unknown')
                return {'handoff':True,'blocked':False,'unknown':True,'actions':outcomes}
            if status=='blocked':
                await asyncio.to_thread(handoff,job,'crm_action_requires_manager')
                return {'handoff':True,'blocked':True,'unknown':False,'actions':outcomes}
    return {'handoff':False,'blocked':False,'unknown':False,'actions':outcomes}
