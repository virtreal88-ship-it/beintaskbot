"""Tenant-scoped AI dashboard projections over the existing AI journals.

This is a read model, not a second execution log. Simulation step events come
from immutable simulation runs; Active facts are projected from existing
webhook events, jobs, runs and token usage.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from tenant_ai_agents_store import _authorize
from tenant_ai_simulation_schema import ensure_schema
from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect, _json


def _json_value(value, fallback):
    if isinstance(value, str):
        try: value=json.loads(value)
        except (TypeError, ValueError): return fallback
    return value if isinstance(value, type(fallback)) else fallback


def _iso(value):
    return value.isoformat() if hasattr(value, 'isoformat') else str(value or '')


def dashboard(profile):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _actor = _authorize(cur, profile)
            cur.execute('''SELECT a.id,a.name,a.role,a.mode,a.enabled,a.scopes,a.schedule,
                COALESCE(ar.run_count,0) AS active_runs,COALESCE(ar.error_count,0) AS active_errors,
                ar.last_active,COALESCE(au.tokens,0) AS active_tokens,
                COALESCE(sr.run_count,0) AS simulation_runs,COALESCE(sr.error_count,0) AS simulation_errors,
                sr.last_simulation,COALESCE(su.tokens,0) AS simulation_tokens
              FROM saas_ai_agents a
              LEFT JOIN LATERAL (
                SELECT count(*) AS run_count,count(*) FILTER(WHERE r.state IN ('failed','unknown')) AS error_count,
                       max(created_at) AS last_active
                FROM saas_ai_agent_runs r JOIN saas_ai_agent_jobs j
                  ON j.tenant_id=r.tenant_id AND j.agent_id=r.agent_id AND j.event_fingerprint=r.event_fingerprint
                WHERE r.tenant_id=a.tenant_id AND r.agent_id=a.id
                  AND r.created_at>=now()-interval '30 days'
              ) ar ON TRUE
              LEFT JOIN LATERAL (
                SELECT COALESCE(sum(total_tokens),0) AS tokens FROM saas_ai_agent_token_usage u
                JOIN saas_ai_agent_runs r ON r.tenant_id=u.tenant_id AND r.id=u.run_id
                JOIN saas_ai_agent_jobs j ON j.tenant_id=r.tenant_id AND j.agent_id=r.agent_id AND j.event_fingerprint=r.event_fingerprint
                WHERE u.tenant_id=a.tenant_id AND u.agent_id=a.id AND u.created_at>=now()-interval '30 days'
              ) au ON TRUE
              LEFT JOIN LATERAL (
                SELECT count(*) AS run_count,count(*) FILTER(WHERE outcome IN ('error','unknown','blocked','unsupported')) AS error_count,
                       max(created_at) AS last_simulation
                FROM saas_ai_simulation_runs r WHERE r.tenant_id=a.tenant_id AND r.primary_agent_id=a.id
                  AND r.created_at>=now()-interval '30 days'
              ) sr ON TRUE
              LEFT JOIN LATERAL (
                SELECT COALESCE(sum(u.actual_tokens),0) AS tokens FROM saas_ai_simulation_usage u
                JOIN saas_ai_simulation_runs r ON r.tenant_id=u.tenant_id AND r.queue_id=u.queue_id
                WHERE u.tenant_id=a.tenant_id AND u.agent_id=a.id AND u.state='succeeded'
                  AND u.created_at>=now()-interval '30 days'
              ) su ON TRUE
              WHERE a.tenant_id=%s::uuid ORDER BY CASE a.role WHEN 'primary' THEN 0 ELSE 1 END,a.name,a.id''',(tenant,))
            agents=[]
            for row in cur.fetchall():
                item=dict(row)
                item['id']=str(item['id']); item['scopes']=_json_value(item.get('scopes'),[])
                item['active_runs']=int(item.get('active_runs') or 0); item['active_errors']=int(item.get('active_errors') or 0)
                item['active_tokens']=int(item.get('active_tokens') or 0)
                item['simulation_runs']=int(item.get('simulation_runs') or 0); item['simulation_errors']=int(item.get('simulation_errors') or 0)
                item['simulation_tokens']=int(item.get('simulation_tokens') or 0)
                item['last_active_at']=_iso(item.pop('last_active',None)) or None
                item['last_simulation_at']=_iso(item.pop('last_simulation',None)) or None
                agents.append(item)
            cur.execute('''SELECT count(*) AS runs,
                count(*) FILTER(WHERE state IN ('queued','processing')) AS pending,
                count(*) FILTER(WHERE state IN ('failed','unknown')) AS errors,
                count(*) FILTER(WHERE state='sent') AS sent_jobs
              FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND updated_at>=now()-interval '30 days' ''',(tenant,))
            active=dict(cur.fetchone() or {})
            cur.execute('''SELECT count(*) AS runs,
                count(*) FILTER(WHERE outcome IN ('virtual_reply','reply')) AS replies,
                count(*) FILTER(WHERE outcome IN ('error','unknown','blocked','unsupported')) AS errors,
                count(*) FILTER(WHERE outcome='virtual_stop') AS stops,
                COALESCE(sum(total_tokens),0) AS tokens
              FROM saas_ai_simulation_runs WHERE tenant_id=%s::uuid AND created_at>=now()-interval '30 days' ''',(tenant,))
            simulation=dict(cur.fetchone() or {})
            cur.execute("SELECT count(*) AS calls FROM saas_ai_simulation_usage WHERE tenant_id=%s::uuid AND call_kind='expert' AND state='succeeded' AND created_at>=now()-interval '30 days'",(tenant,))
            simulation['expert_calls']=int((cur.fetchone() or {}).get('calls') or 0)
            cur.execute("SELECT count(*) AS matches FROM saas_ai_simulation_runs r CROSS JOIN LATERAL jsonb_array_elements(r.events) e WHERE r.tenant_id=%s::uuid AND r.created_at>=now()-interval '30 days' AND e->>'type'='trigger_matched'",(tenant,))
            simulation['trigger_matches']=int((cur.fetchone() or {}).get('matches') or 0)
            cur.execute('''SELECT c.enabled,c.state,c.emergency_stop,c.agent_id,c.channel_ids,c.conversation_ids,
                c.starts_at,c.ends_at,c.token_budget,c.message_limit,c.max_parallel,c.max_calls_per_event,
                (SELECT COALESCE(sum(CASE WHEN state IN ('reserved','unknown') THEN reserved_tokens ELSE actual_tokens END),0)
                  FROM saas_ai_simulation_usage u WHERE u.tenant_id=c.tenant_id AND u.created_at>=COALESCE(c.starts_at,'-infinity')) AS used_tokens,
                (SELECT count(*) FROM saas_ai_simulation_queue q WHERE q.tenant_id=c.tenant_id
                  AND q.state IN ('queued','processing')) AS pending
              FROM saas_ai_simulation_configs c WHERE c.tenant_id=%s::uuid''',(tenant,))
            config=cur.fetchone() or {'enabled':False,'state':'stopped','emergency_stop':False,'agent_id':None,'channel_ids':[],'conversation_ids':[],
                'starts_at':None,'ends_at':None,'token_budget':0,'message_limit':0,'max_parallel':1,'max_calls_per_event':3,'used_tokens':0,'pending':0}
            for key in ('channel_ids','conversation_ids'):
                config[key]=_json_value(config.get(key),[])
            config['agent_id']=str(config['agent_id']) if config.get('agent_id') else None
            for key in ('starts_at','ends_at'):
                config[key]=_iso(config.get(key)) or None
            cur.execute('''SELECT connect_channel_id AS id,name,active,status FROM saas_connect_channels
                WHERE tenant_id=%s::uuid ORDER BY name,connect_channel_id LIMIT 201''',(tenant,))
            channels=[{**dict(row),'id':str(row['id']),'active':bool(row.get('active'))} for row in cur.fetchall()]
            cur.execute('''SELECT connect_channel_id AS channel_id,conversation_id,contact_name,last_message_at
                FROM saas_connect_conversations WHERE tenant_id=%s::uuid
                ORDER BY last_message_at DESC NULLS LAST,conversation_id LIMIT 200''',(tenant,))
            conversations=[{'channel_id':str(row['channel_id']),'conversation_id':str(row['conversation_id']),
                'label':str(row.get('contact_name') or 'WhatsApp conversation')[:120],
                'last_message_at':_iso(row.get('last_message_at')) or None} for row in cur.fetchall()]
            cur.execute("SELECT enabled,emergency_stop,auto_send_enabled FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid",(tenant,))
            settings=cur.fetchone() or {}
            from tenant_ai_agent_runtime_flags import enabled as runtime_enabled
            runtime=runtime_enabled('AI_SIMULATION_ENABLED')
            active['runs']=int(active.get('runs') or 0);active['errors']=int(active.get('errors') or 0)
            active['pending']=int(active.get('pending') or 0);active['accepted_sends']=int(active.get('sent_jobs') or 0)
            simulation={key:int(value or 0) for key,value in simulation.items()}
            config['used_tokens']=int(config.get('used_tokens') or 0);config['remaining_tokens']=max(0,int(config.get('token_budget') or 0)-config['used_tokens']) if config.get('token_budget') else None
            config['pending']=int(config.get('pending') or 0);config['runtime_enabled']=runtime
            config['company_ai_enabled']=bool(settings.get('enabled'));config['company_emergency_stop']=bool(settings.get('emergency_stop'))
            config['auto_send_enabled']=bool(settings.get('auto_send_enabled'))
            return {'agents':agents,'channels':channels,'conversations':conversations,'active':active,'simulation':simulation,
                    'simulation_config':dict(config),'window_days':30,
                    'active_detail_telemetry':'run_level_only',
                    'note':'Active event details are limited to facts already recorded by existing webhook, job, run and usage journals.'}


def events(profile, *, limit=100, mode='all', event_type=''):
    safe=max(1,min(200,int(limit or 100)))
    if mode not in {'all','active','simulation'}: raise TenantLinearError('AI event mode is invalid.')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,_=_authorize(cur,profile)
            result=[]
            if mode in {'all','simulation'}:
                cur.execute('''SELECT r.id AS run_id,r.primary_agent_id AS agent_id,r.channel_id,r.conversation_id,
                    r.outcome,r.events,r.created_at,a.name AS agent_name
                  FROM saas_ai_simulation_runs r LEFT JOIN saas_ai_agents a
                    ON a.tenant_id=r.tenant_id AND a.id=r.primary_agent_id
                  WHERE r.tenant_id=%s::uuid ORDER BY r.created_at DESC LIMIT %s''',(tenant,safe))
                for row in cur.fetchall():
                    for item in _json_value(row.get('events'),[]):
                        if not isinstance(item,dict): continue
                        kind=str(item.get('type') or 'simulation_event')[:80]
                        if event_type and event_type!=kind: continue
                        meta={key:item[key] for key in ('reason','actions','tokens','sent','source_ids','trigger_id','executed','message_type',
                            'enabled_count','matched','matched_trigger_ids','possible_topic') if key in item}
                        result.append({'tenant_id':tenant,'run_id':str(row['run_id']),'agent_id':str(row['agent_id']) if row.get('agent_id') else None,
                          'agent_name':row.get('agent_name') or '', 'conversation_id':str(row['conversation_id']),'channel_id':str(row['channel_id']),
                          'event_type':kind,'timestamp':_iso(row.get('created_at')),'status':str(row.get('outcome') or ''),
                          'reason_code':str(item.get('reason') or ''),'metadata':meta,'mode':'simulation','source':'simulation_run'})
            if mode in {'all','active'}:
                cur.execute('''SELECT e.fingerprint,e.external_id,e.provider,e.payload->>'channel_id' AS channel_id,
                    COALESCE(e.payload->>'conversation_id',e.payload->>'chat_id','') AS conversation_id,
                    e.received_at,e.state AS event_state,e.error_code AS event_error,
                    j.agent_id,j.state AS job_state,j.error_code AS job_error,r.id AS run_id,r.state AS run_state,
                    r.error_code AS run_error,r.result,a.name AS agent_name
                  FROM saas_tenant_webhook_events e
                  LEFT JOIN LATERAL (SELECT * FROM saas_ai_agent_jobs x WHERE x.tenant_id=e.tenant_id AND x.event_fingerprint=e.fingerprint
                    ORDER BY x.updated_at DESC LIMIT 1) j ON TRUE
                  LEFT JOIN LATERAL (SELECT * FROM saas_ai_agent_runs x WHERE x.tenant_id=e.tenant_id AND x.event_fingerprint=e.fingerprint
                    AND (j.agent_id IS NULL OR x.agent_id=j.agent_id) ORDER BY x.created_at DESC LIMIT 1) r ON TRUE
                  LEFT JOIN saas_ai_agents a ON a.tenant_id=e.tenant_id AND a.id=COALESCE(j.agent_id,r.agent_id)
                  WHERE e.tenant_id=%s::uuid AND e.event_type='incoming_message'
                  ORDER BY e.received_at DESC LIMIT %s''',(tenant,safe))
                for row in cur.fetchall():
                    state=str(row.get('run_state') or row.get('job_state') or row.get('event_state') or 'unknown')
                    reason=str(row.get('run_error') or row.get('job_error') or row.get('event_error') or '')
                    run_id=str(row['run_id']) if row.get('run_id') else str(row['fingerprint'])
                    accepted=bool(_json_value(row.get('result'),{}).get('send_state')=='accepted') if row.get('result') else False
                    base={'tenant_id':tenant,'run_id':run_id,'agent_id':str(row['agent_id']) if row.get('agent_id') else None,
                      'agent_name':row.get('agent_name') or '', 'conversation_id':str(row.get('conversation_id') or ''),
                      'channel_id':str(row.get('channel_id') or ''),'timestamp':_iso(row.get('received_at')),'status':state,
                      'reason_code':reason,'mode':'active','source':'webhook_job_run'}
                    for kind,meta in [('inbound_received',{}),('processing_state',{'job_state':row.get('job_state'),'run_state':row.get('run_state'),'accepted_by_provider':accepted})]:
                        if event_type and event_type!=kind: continue
                        result.append({**base,'event_type':kind,'metadata':meta})
            result.sort(key=lambda item:item.get('timestamp') or '',reverse=True)
            return result[:safe]


def simulation_run(profile, run_id):
    try: run_id=str(uuid.UUID(str(run_id)))
    except (ValueError,TypeError): raise TenantLinearError('Simulation qeydi tapılmadı.',404) from None
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,_=_authorize(cur,profile)
            cur.execute('''SELECT r.*,q.state AS queue_state,q.error_code AS queue_error,q.recheck_of_run_id,
                a.name AS agent_name,ch.name AS channel_name,d.lead_id
              FROM saas_ai_simulation_runs r JOIN saas_ai_simulation_queue q ON q.tenant_id=r.tenant_id AND q.id=r.queue_id
              LEFT JOIN saas_ai_agents a ON a.tenant_id=r.tenant_id AND a.id=r.primary_agent_id
              LEFT JOIN saas_connect_channels ch ON ch.tenant_id=r.tenant_id AND ch.connect_channel_id=r.channel_id
              LEFT JOIN saas_connect_conversations d ON d.tenant_id=r.tenant_id AND d.connect_channel_id=r.channel_id
                AND d.conversation_id=r.conversation_id
              WHERE r.tenant_id=%s::uuid AND r.id=%s::uuid''',(tenant,run_id))
            row=cur.fetchone()
            if not row: raise TenantLinearError('Simulation qeydi tapılmadı.',404)
            item=dict(row)
            # Tenant identity is authorization context, never part of the public run detail.
            item.pop('tenant_id',None)
            cur.execute('''SELECT id,direction,author_name,body,message_type,happened_at,created_at
              FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s
                AND COALESCE(happened_at,created_at)<=COALESCE((SELECT happened_at FROM saas_crm_messages
                  WHERE tenant_id=%s::uuid AND id=%s::uuid),(SELECT created_at FROM saas_crm_messages
                  WHERE tenant_id=%s::uuid AND id=%s::uuid))
              ORDER BY COALESCE(happened_at,created_at) DESC,created_at DESC LIMIT 24''',
              (tenant,item.get('lead_id'), 'connect:'+str(item['channel_id']),tenant,item['source_message_id'],tenant,item['source_message_id']))
            messages=list(reversed(cur.fetchall()))
            cur.execute('''SELECT agent_id,call_kind,reserved_tokens,actual_tokens,state,created_at,completed_at
              FROM saas_ai_simulation_usage WHERE tenant_id=%s::uuid AND queue_id=%s::uuid ORDER BY call_no''',(tenant,item['queue_id']))
            usage=[dict(value) for value in cur.fetchall()]
            cur.execute('''SELECT r.id,r.outcome,r.virtual_stop_reason,r.virtual_reply,r.virtual_triggers,
                r.knowledge_refs,r.expert_results,r.total_tokens,r.created_at,q.state
              FROM saas_ai_simulation_queue q JOIN saas_ai_simulation_runs r
                ON r.tenant_id=q.tenant_id AND r.queue_id=q.id
              WHERE q.tenant_id=%s::uuid AND q.recheck_of_run_id=%s::uuid ORDER BY r.created_at DESC LIMIT 20''',(tenant,run_id))
            rechecks=[]
            for value in cur.fetchall():
                child=dict(value)
                child['id']=str(child['id']);child['created_at']=_iso(child.get('created_at'))
                child['virtual_triggers']=_json_value(child.get('virtual_triggers'),[])
                child['knowledge_refs']=_json_value(child.get('knowledge_refs'),[])
                child['expert_results']=_json_value(child.get('expert_results'),[])
                rechecks.append(child)
            for value in usage:
                value['agent_id']=str(value['agent_id']);value['created_at']=_iso(value.get('created_at'));value['completed_at']=_iso(value.get('completed_at')) or None
            for key in ('id','queue_id','channel_id','source_message_id','primary_agent_id','recheck_of_run_id'):
                item[key]=str(item[key]) if item.get(key) else None
            for key in ('created_at',):item[key]=_iso(item.get(key))
            for key in ('virtual_triggers','knowledge_refs','expert_results','events'):item[key]=_json_value(item.get(key),[])
            item['sent']=False
            item['messages']=[{'id':str(message['id']),'direction':message.get('direction'),'author':message.get('author_name') or '',
                'text':str(message.get('body') or ''),'type':message.get('message_type') or 'text','timestamp':_iso(message.get('happened_at') or message.get('created_at'))}
                for message in messages]
            item['usage']=usage
            item['rechecks']=rechecks
            return item


def issues(profile, *, limit=100):
    safe=max(1,min(200,int(limit or 100)))
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,_=_authorize(cur,profile)
            cur.execute('''SELECT r.id,r.primary_agent_id,r.channel_id,r.conversation_id,r.outcome,r.virtual_stop_reason,
                r.events,r.knowledge_refs,r.created_at,a.name AS agent_name,ch.name AS channel_name
              FROM saas_ai_simulation_runs r LEFT JOIN saas_ai_agents a ON a.tenant_id=r.tenant_id AND a.id=r.primary_agent_id
              LEFT JOIN saas_connect_channels ch ON ch.tenant_id=r.tenant_id AND ch.connect_channel_id=r.channel_id
              WHERE r.tenant_id=%s::uuid ORDER BY r.created_at DESC LIMIT %s''',(tenant,safe))
            found=[]
            for row in cur.fetchall():
                run=str(row['id']);events_list=_json_value(row.get('events'),[]);refs=_json_value(row.get('knowledge_refs'),[])
                candidates=[]
                outcome=str(row.get('outcome') or '')
                if outcome in {'error','blocked','unsupported'}:
                    candidates.append(('processing_error' if outcome=='error' else 'blocked_or_unsupported',
                        'high' if outcome=='error' else 'medium',outcome,str(row.get('virtual_stop_reason') or outcome),
                        'Open the simulation run and inspect recorded diagnostics.'))
                for event in events_list:
                    if not isinstance(event,dict):continue
                    kind=str(event.get('type') or '')
                    if kind=='expert_unavailable':
                        candidates.append(('expert_unavailable','medium',kind,str(event.get('reason') or 'Expert call was unavailable.'),'Review the Primary/Expert link and the recorded policy result.'))
                    elif kind=='simulation_unsupported':
                        candidates.append(('unsupported_tool_or_media','medium',kind,str(event.get('reason') or 'An operation was not simulated.'),'Review the unsupported capability before using this scenario to assess readiness.'))
                    elif kind=='provider_result_unknown':
                        candidates.append(('provider_result_unknown','high',kind,'Provider result is unknown; the call will not be repeated automatically.','Check provider usage before deciding whether to rerun.'))
                    elif kind=='trigger_checked' and event.get('matched') is False and event.get('possible_topic'):
                        candidates.append(('possible_trigger_miss','low','No enabled trigger matched the recorded scenario.',
                            'A possible '+str(event.get('possible_topic'))+' topic was noted for this recorded check; review manually.',
                            'Compare the trigger configuration with the original scenario. This is a review prompt, not proof of a missed trigger.'))
                if outcome=='virtual_reply' and not refs:
                    candidates.append(('no_knowledge_references','low','No knowledge source references were recorded by this run.','No knowledge source was attached to the recorded result.','Review whether the reply needs a relevant source; this is a review prompt, not proof of a bad answer.'))
                for key,priority,fact,reason,action in candidates:
                    cur.execute('SELECT status,updated_at FROM saas_ai_dashboard_issue_reviews WHERE tenant_id=%s::uuid AND run_id=%s::uuid AND issue_key=%s',(tenant,run,key))
                    review=cur.fetchone() or {}
                    found.append({'id':run+':'+key,'run_id':run,'issue_key':key,'date':_iso(row.get('created_at')),
                      'agent_id':str(row['primary_agent_id']) if row.get('primary_agent_id') else None,
                      'agent_name':row.get('agent_name') or '', 'channel_id':str(row['channel_id']),
                      'channel_name':row.get('channel_name') or '', 'conversation_id':str(row['conversation_id']),
                      'category':key,'priority':priority,'fact':fact,'possible_reason':reason,'suggested_action':action,
                      'status':review.get('status') or 'new','reviewed_at':_iso(review.get('updated_at')) or None})
            return found[:safe]


def review_issue(profile, run_id, issue_key, status):
    try: run_id=str(uuid.UUID(str(run_id)))
    except (ValueError,TypeError): raise TenantLinearError('Issue is invalid.',404) from None
    if status not in {'new','in_review','confirmed','dismissed','fixed'}: raise TenantLinearError('Issue status is invalid.')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,actor=_authorize(cur,profile)
            cur.execute('''SELECT r.outcome,r.events,r.knowledge_refs FROM saas_ai_simulation_runs r
              WHERE r.tenant_id=%s::uuid AND r.id=%s::uuid FOR SHARE''',(tenant,run_id))
            run=cur.fetchone()
            if not run: raise TenantLinearError('Issue is invalid.',404)
            events_value=_json_value(run.get('events'),[]);knowledge_value=_json_value(run.get('knowledge_refs'),[])
            valid_keys={'processing_error':str(run.get('outcome'))=='error',
                'blocked_or_unsupported':str(run.get('outcome')) in {'blocked','unsupported'},
                'expert_unavailable':any(isinstance(item,dict) and item.get('type')=='expert_unavailable' for item in events_value),
                'unsupported_tool_or_media':any(isinstance(item,dict) and item.get('type')=='simulation_unsupported' for item in events_value),
                'provider_result_unknown':any(isinstance(item,dict) and item.get('type')=='provider_result_unknown' for item in events_value),
                'possible_trigger_miss':any(isinstance(item,dict) and item.get('type')=='trigger_checked'
                    and item.get('matched') is False and item.get('possible_topic') for item in events_value),
                'no_knowledge_references':str(run.get('outcome'))=='virtual_reply' and not knowledge_value}
            if not valid_keys.get(str(issue_key),False): raise TenantLinearError('Issue is invalid.',404)
            cur.execute('''INSERT INTO saas_ai_dashboard_issue_reviews(tenant_id,run_id,issue_key,status,updated_by,updated_at)
              VALUES(%s::uuid,%s::uuid,%s,%s,%s,now()) ON CONFLICT(tenant_id,run_id,issue_key)
              DO UPDATE SET status=EXCLUDED.status,updated_by=EXCLUDED.updated_by,updated_at=now()
              RETURNING status,updated_at''',(tenant,run_id,str(issue_key)[:100],status,actor))
            result=dict(cur.fetchone());result['updated_at']=_iso(result.get('updated_at'))
        conn.commit()
        return result


def enqueue_recheck(profile, run_id, request_key):
    if not isinstance(request_key,str) or len(request_key)>100 or not request_key.strip():
        raise TenantLinearError('Recheck idempotency key is required.')
    try: run_id=str(uuid.UUID(str(run_id)))
    except (ValueError,TypeError): raise TenantLinearError('Simulation run not found.',404) from None
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,_actor=_authorize(cur,profile)
            cur.execute('''SELECT r.id,r.queue_id,r.channel_id,r.conversation_id,r.source_message_id,
                q.source_event_id,q.lead_id,c.enabled,c.emergency_stop,c.token_budget,c.message_limit
              FROM saas_ai_simulation_runs r JOIN saas_ai_simulation_queue q ON q.tenant_id=r.tenant_id AND q.id=r.queue_id
              JOIN saas_ai_simulation_configs c ON c.tenant_id=r.tenant_id
              WHERE r.tenant_id=%s::uuid AND r.id=%s::uuid FOR UPDATE OF c''',(tenant,run_id))
            old=cur.fetchone()
            if not old: raise TenantLinearError('Simulation run not found.',404)
            from tenant_ai_agent_runtime_flags import enabled as runtime_enabled
            if not runtime_enabled('AI_SIMULATION_ENABLED'): raise TenantLinearError('Simulation server flag is disabled.',409)
            if old.get('enabled') is not True or old.get('emergency_stop') is True: raise TenantLinearError('Start Simulation before requesting a recheck.',409)
            cur.execute('''INSERT INTO saas_ai_simulation_queue(id,tenant_id,channel_id,conversation_id,lead_id,
                source_message_id,source_event_id,event_fingerprint,recheck_of_run_id,recheck_request_key,state)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s::uuid,%s,%s,%s::uuid,%s,'queued')
              ON CONFLICT(tenant_id,recheck_request_key) WHERE recheck_of_run_id IS NOT NULL AND recheck_request_key IS NOT NULL
              DO NOTHING RETURNING id''',(str(uuid.uuid4()),tenant,old['channel_id'],old['conversation_id'],old['lead_id'],
                old['source_message_id'],old['source_event_id'],'recheck:'+str(uuid.uuid4()),run_id,request_key.strip()))
            inserted=cur.fetchone()
            was_inserted=bool(inserted)
            if not inserted:
                cur.execute('SELECT id FROM saas_ai_simulation_queue WHERE tenant_id=%s::uuid AND recheck_request_key=%s',(tenant,request_key.strip()))
                inserted=cur.fetchone()
            if not inserted: raise TenantLinearError('Recheck could not be queued.',409)
            conn.commit()
            return {'queue_id':str(inserted['id']),'recheck_of_run_id':run_id,'queued':True,'idempotent':not was_inserted}
