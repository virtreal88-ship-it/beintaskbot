"""Read-only Connect night report over existing messages, runs, tickets and audit events."""
import uuid
from datetime import datetime, timezone
from tenant_ai_agents_schema import ensure_schema
from tenant_linear_policy import TenantLinearError, identifier
from tenant_platform import _connect
from tenant_chat_send_schema import ensure_schema as ensure_send_schema
from tenant_night_report_access import authorize
from tenant_night_report_period import interval


def public(row):
    return {key:value.isoformat() if hasattr(value,'isoformat') else str(value) if isinstance(value,uuid.UUID) else value for key,value in dict(row).items()}


def configuration(cur,tenant,company):
    onboarding=company.get('onboarding') or {}
    zone=onboarding.get('timezone') or (onboarding.get('company') or {}).get('timezone')
    cur.execute("SELECT schedule FROM saas_ai_agents WHERE tenant_id=%s::uuid AND enabled=TRUE AND schedule<>'{}'::jsonb",(tenant,))
    schedules=[r['schedule'] for r in cur.fetchall()]
    zones={s.get('timezone','Asia/Baku') for s in schedules}
    hours={(s.get('start'),s.get('end')) for s in schedules if s.get('start','')>s.get('end','')}
    source='company' if zone else 'agent_schedule' if len(zones)==1 else 'default'
    zone=zone or (next(iter(zones)) if len(zones)==1 else 'Asia/Baku')
    start,end=next(iter(hours)) if len(hours)==1 else ('19:00','09:00')
    return {'timezone':zone,'start':start,'end':end,'timezone_source':source,
            'hours_source':'agent_schedule' if len(hours)==1 else 'default','multiple_schedules':len(zones)>1 or len(hours)>1}


# All joins repeat the tenant key. Routes include the Connect channel: identical JIDs on
# two channels are two conversations. UNION deduplicates activity, never message counts.
BASE='''WITH b AS (SELECT %s::uuid AS tenant,%s::timestamptz AS starts,%s::timestamptz AS ends),
 routes AS (SELECT c.*,ch.name AS channel_name FROM saas_connect_conversations c
   JOIN b ON b.tenant=c.tenant_id JOIN saas_connect_channels ch
     ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id),
 events AS (SELECT e.*,r.connect_channel_id,r.conversation_id,r.lead_id FROM saas_tenant_webhook_events e
   JOIN b ON b.tenant=e.tenant_id JOIN routes r ON r.tenant_id=e.tenant_id
    AND r.connect_channel_id::text=e.payload->>'channel_id' AND r.conversation_id=e.payload->>'conversation_id'
   WHERE e.provider='connect'),
 runs AS (SELECT a.*,e.connect_channel_id,e.conversation_id FROM saas_ai_agent_runs a
   JOIN events e ON e.tenant_id=a.tenant_id AND e.fingerprint=a.event_fingerprint),
 voices AS (SELECT v.*,r.conversation_id,COALESCE(a.event_fingerprint,'voice:'||v.external_id) AS event_key
   FROM saas_connect_voice_receipts v JOIN b ON b.tenant=v.tenant_id
   JOIN saas_crm_messages m ON m.tenant_id=v.tenant_id AND m.external_id=v.external_id AND m.channel='connect:'||v.channel_id::text
   JOIN routes r ON r.tenant_id=m.tenant_id AND r.lead_id=m.kommo_lead_id AND r.connect_channel_id=v.channel_id
   LEFT JOIN runs a ON a.tenant_id=v.tenant_id AND a.id=v.run_id),
 ai_activity AS (SELECT DISTINCT a.connect_channel_id,a.conversation_id FROM runs a,b
   WHERE a.created_at>=b.starts AND a.created_at<b.ends AND a.state<>'skipped'
   UNION SELECT v.channel_id,v.conversation_id FROM voices v,b WHERE v.created_at>=b.starts AND v.created_at<b.ends),
 handoffs AS (
   SELECT t.channel_id,t.conversation_id FROM saas_support_events s JOIN b ON b.tenant=s.tenant_id
     JOIN saas_support_tickets t ON t.tenant_id=s.tenant_id AND t.id=s.ticket_id
     JOIN events e ON e.tenant_id=s.tenant_id AND s.event_key='handoff:'||e.fingerprint AND t.channel_id=e.connect_channel_id AND t.conversation_id=e.conversation_id
     WHERE COALESCE(e.completed_at,e.received_at)>=b.starts AND COALESCE(e.completed_at,e.received_at)<b.ends
   UNION
   SELECT r.connect_channel_id,r.conversation_id FROM saas_tenant_audit_events a
     JOIN b ON b.tenant=a.tenant_id JOIN routes r ON r.tenant_id=a.tenant_id
       AND r.conversation_id=a.entity_id AND r.connect_channel_id::text=a.payload->>'channel_id'
     WHERE a.action='ai_conversation_human' AND a.entity_type='conversation' AND a.payload->>'provider'='connect'
       AND a.created_at>=b.starts AND a.created_at<b.ends),
 activity AS (
   SELECT r.connect_channel_id,r.conversation_id FROM saas_crm_messages m JOIN routes r
     ON r.tenant_id=m.tenant_id AND r.lead_id=m.kommo_lead_id AND m.channel='connect:'||r.connect_channel_id::text,b
     WHERE COALESCE(m.happened_at,m.created_at)>=b.starts AND COALESCE(m.happened_at,m.created_at)<b.ends
   UNION SELECT * FROM ai_activity UNION SELECT * FROM handoffs),
 ai_failure_candidates AS (SELECT a.event_fingerprint AS event_key,a.connect_channel_id AS channel_id,a.conversation_id,
   'ai'::text AS kind,'ai_'||a.state AS code,a.updated_at FROM runs a,b
   WHERE a.state IN ('failed','unknown') AND a.error_code NOT IN ('connect_send_unknown','send_result_unknown') AND a.updated_at>=b.starts AND a.updated_at<b.ends
   UNION ALL SELECT v.event_key,v.channel_id,v.conversation_id,'ai','voice_failed',v.updated_at FROM voices v,b
     WHERE v.state='failed' AND v.updated_at>=b.starts AND v.updated_at<b.ends
   UNION ALL SELECT j.event_fingerprint,e.connect_channel_id,e.conversation_id,'ai','ai_'||j.state,j.updated_at
     FROM saas_ai_agent_jobs j JOIN events e ON e.tenant_id=j.tenant_id AND e.fingerprint=j.event_fingerprint,b
     WHERE j.state IN ('failed','unknown') AND j.updated_at>=b.starts AND j.updated_at<b.ends
       AND (j.error_code LIKE 'chat_ai_%%' OR j.error_code IN ('connect_worker_failed','worker_stopped'))),
 ai_event_failures AS (SELECT e.fingerprint AS event_key,e.connect_channel_id AS channel_id,e.conversation_id,
   'ai'::text AS kind,'ai_'||e.state AS code,COALESCE(e.completed_at,e.received_at) AS updated_at FROM events e,b
   WHERE e.state IN ('failed','unknown') AND COALESCE(e.completed_at,e.received_at)>=b.starts AND COALESCE(e.completed_at,e.received_at)<b.ends
     AND (e.error_code LIKE 'chat_ai_%%' OR e.error_code LIKE 'voice_%%' OR e.error_code IN
       ('connect_worker_failed','worker_stopped','worker_interrupted','read_or_context_failed','connect_generation_unknown','connect_configuration_unavailable','ai_configuration_unavailable'))),
 ai_failures AS (SELECT DISTINCT ON (event_key) event_key,channel_id,conversation_id,kind,code
   FROM (SELECT * FROM ai_failure_candidates UNION ALL SELECT * FROM ai_event_failures) candidates ORDER BY event_key,updated_at DESC,code),
 connect_failures AS (
   SELECT 'webhook:'||a.event_fingerprint AS event_key,a.connect_channel_id AS channel_id,a.conversation_id,
     'connect'::text AS kind,'send_unknown'::text AS code FROM runs a,b
     WHERE a.state IN ('failed','unknown') AND a.error_code IN ('connect_send_unknown','send_result_unknown')
       AND a.updated_at>=b.starts AND a.updated_at<b.ends
   UNION
   SELECT 'draft:'||d.id::text,e.connect_channel_id,e.conversation_id,'connect','send_unknown'
   FROM saas_ai_agent_drafts d JOIN events e ON e.tenant_id=d.tenant_id AND e.fingerprint=d.source_fingerprint,b
   WHERE d.state='unknown' AND COALESCE(d.send_started_at,d.created_at)>=b.starts
     AND COALESCE(d.send_started_at,d.created_at)<b.ends
   UNION
   SELECT 'send:'||s.actor_id::text||':'||s.request_id::text AS event_key,r.connect_channel_id AS channel_id,r.conversation_id,
     'connect'::text AS kind,'send_'||s.state AS code
   FROM saas_chat_send_receipts s JOIN routes r ON r.tenant_id=s.tenant_id AND r.lead_id=s.lead_id,b
   WHERE s.state IN ('blocked','unknown') AND s.updated_at>=b.starts AND s.updated_at<b.ends
   UNION
   SELECT 'webhook:'||e.fingerprint,e.connect_channel_id,e.conversation_id,'connect',COALESCE(NULLIF(e.error_code,''),e.state)
   FROM events e LEFT JOIN LATERAL (SELECT max(j.updated_at) AS updated_at FROM saas_ai_agent_jobs j
     WHERE j.tenant_id=e.tenant_id AND j.event_fingerprint=e.fingerprint AND j.state IN ('failed','unknown')) job_time ON TRUE,b
   WHERE e.state IN ('failed','unknown') AND COALESCE(job_time.updated_at,e.completed_at,e.received_at)>=b.starts
     AND COALESCE(job_time.updated_at,e.completed_at,e.received_at)<b.ends
     AND NOT EXISTS(SELECT 1 FROM ai_failures a WHERE a.event_key=e.fingerprint)
     AND NOT EXISTS(SELECT 1 FROM runs a WHERE a.event_fingerprint=e.fingerprint
       AND a.state IN ('failed','unknown') AND a.error_code IN ('connect_send_unknown','send_result_unknown'))),
 failures AS (SELECT * FROM ai_failures UNION SELECT * FROM connect_failures),
 tickets AS (SELECT t.*,r.lead_id,r.contact_name,r.phone,r.channel_name,m.display_name AS assignee_name,
   CASE WHEN history.status IN ('new','waiting','in_progress','resolved') THEN history.status
     WHEN t.status='resolved' AND t.resolved_at<b.ends THEN 'resolved'
     ELSE CASE WHEN t.source='ai_handoff' THEN 'waiting' ELSE 'new' END END AS status_at_end
   FROM saas_support_tickets t JOIN b ON b.tenant=t.tenant_id JOIN routes r
     ON r.tenant_id=t.tenant_id AND r.connect_channel_id=t.channel_id AND r.conversation_id=t.conversation_id
   LEFT JOIN saas_tenant_members m ON m.tenant_id=t.tenant_id AND m.telegram_id=t.assigned_to
   LEFT JOIN LATERAL (SELECT a.payload->>'status' AS status FROM saas_tenant_audit_events a
     WHERE a.tenant_id=t.tenant_id AND a.entity_type='support_ticket' AND a.entity_id=t.id::text
       AND a.action='support_update' AND a.payload ? 'status' AND a.created_at<b.ends
     ORDER BY a.created_at DESC,a.id DESC LIMIT 1) history ON TRUE WHERE t.created_at<b.ends),
 claims AS (SELECT DISTINCT t.id FROM saas_tenant_audit_events a JOIN b ON b.tenant=a.tenant_id
   JOIN tickets t ON t.tenant_id=a.tenant_id AND a.entity_id=t.id::text
   LEFT JOIN LATERAL (SELECT prior.payload->>'assigned_to' AS actor FROM saas_tenant_audit_events prior
     WHERE prior.tenant_id=a.tenant_id AND prior.entity_id=a.entity_id AND prior.entity_type='support_ticket'
       AND prior.action='support_update' AND prior.payload ? 'assigned_to' AND prior.created_at<=a.created_at
     ORDER BY prior.created_at DESC,prior.id DESC LIMIT 1) assigned ON TRUE
   WHERE a.action='support_update' AND a.entity_type='support_ticket' AND a.payload->>'status'='in_progress'
     AND a.created_at>=b.starts AND a.created_at<b.ends
     AND (CASE WHEN a.payload ? 'assigned_to' THEN a.payload->>'assigned_to' ELSE assigned.actor END) IS NOT NULL)
 '''


def report(profile,day='',zone='',start='',end='',offset=0,*,now=None):
    try: offset=int(offset)
    except (ValueError,TypeError): raise TenantLinearError('Некорректная страница.') from None
    if offset<0 or offset>100000: raise TenantLinearError('Некорректная страница.')
    now=now or datetime.now(timezone.utc)
    with _connect() as conn:
        ensure_schema(conn);ensure_send_schema(conn);conn.commit()
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            tenant,company=authorize(cur,profile)
            defaults=configuration(cur,tenant,company)
            period=interval(day,zone or defaults['timezone'],start or defaults['start'],end or defaults['end'],now=now)
            if period['state']=='not_started':
                return {'tenant_id':tenant,'company_name':company['name'],'scope':'connect','period':period,'defaults':defaults,
                        'data_quality':{'errors_complete':False,'reason':'retained_records_only'},'generated_at':now.isoformat(),'metrics':{key:0 for key in ('dialogs','ai_dialogs','handed_off','unresolved','ai_errors','connect_errors','taken_in_work','attention_count')},
                        'tickets':[],'failures':[],'next_offset':None}
            args=(tenant,period['starts_at_utc'],period['cutoff_at'])
            cur.execute(BASE+'''SELECT (SELECT count(*) FROM activity) AS dialogs,
              (SELECT count(*) FROM ai_activity) AS ai_dialogs,(SELECT count(*) FROM handoffs) AS handed_off,
              (SELECT count(*) FROM tickets WHERE status_at_end<>'resolved') AS unresolved,
              (SELECT count(*) FROM ai_failures) AS ai_errors,(SELECT count(*) FROM connect_failures) AS connect_errors,
              (SELECT count(*) FROM claims) AS taken_in_work,
              (SELECT count(*) FROM tickets WHERE status<>'resolved') AS attention_count''',args)
            metrics={key:int(value) for key,value in cur.fetchone().items()}
            cur.execute(BASE+'''SELECT tickets.*,created_at>=b.starts AS created_in_shift FROM tickets,b WHERE status<>'resolved'
              ORDER BY (source='ai_handoff') DESC,(status='waiting') DESC,(assigned_to IS NULL) DESC,created_at,id
              LIMIT 51 OFFSET %s''',(*args,offset))
            rows=cur.fetchall()
            cur.execute(BASE+'''SELECT f.*,r.contact_name,r.phone,r.lead_id,r.channel_name FROM failures f JOIN routes r
              ON r.connect_channel_id=f.channel_id AND r.conversation_id=f.conversation_id ORDER BY f.kind,f.event_key LIMIT 20''',args)
            failures=[public(r) for r in cur.fetchall()]
    return {'tenant_id':tenant,'company_name':company['name'],'scope':'connect','period':period,'defaults':defaults,
            'data_quality':{'errors_complete':False,'reason':'retained_records_only'},'generated_at':now.isoformat(),'metrics':metrics,'tickets':[public(r) for r in rows[:50]],
            'failures':failures,'next_offset':offset+50 if len(rows)>50 else None}


def detail(profile,*,ticket_id='',channel_id='',conversation_id='',before=''):
    with _connect() as conn:
        ensure_schema(conn);conn.commit()
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            tenant,_=authorize(cur,profile)
            ticket=None
            if ticket_id:
                cur.execute('SELECT t.*,m.display_name AS assignee_name FROM saas_support_tickets t LEFT JOIN saas_tenant_members m ON m.tenant_id=t.tenant_id AND m.telegram_id=t.assigned_to WHERE t.tenant_id=%s::uuid AND t.id=%s::uuid',(tenant,identifier(ticket_id)))
                ticket=cur.fetchone()
                if not ticket: raise TenantLinearError('Обращение не найдено.',404)
                channel_id,conversation_id=str(ticket['channel_id']),ticket['conversation_id']
            cur.execute('''SELECT c.*,ch.name AS channel_name FROM saas_connect_conversations c JOIN saas_connect_channels ch
              ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
              WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid AND c.conversation_id=%s''',
              (tenant,identifier(channel_id),str(conversation_id)))
            route=cur.fetchone()
            if not route: raise TenantLinearError('Диалог не найден в этой компании.',404)
            args=[tenant,route['lead_id'],'connect:'+str(route['connect_channel_id'])];extra=''
            if before:
                extra=''' AND (COALESCE(happened_at,created_at),id)<(SELECT COALESCE(happened_at,created_at),id
                  FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s AND id=%s::uuid)'''
                args += [*args,identifier(before)]
            cur.execute('''SELECT id,body,direction,message_type,happened_at,created_at FROM saas_crm_messages
              WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s'''+extra+'''
              ORDER BY COALESCE(happened_at,created_at) DESC,id DESC LIMIT 51''',tuple(args))
            messages=cur.fetchall()
    return {'ticket':public(ticket) if ticket else None,'dialog':public(route),
            'messages':[public(r) for r in reversed(messages[:50])],
            'next_before':str(messages[49]['id']) if len(messages)>50 else None}
