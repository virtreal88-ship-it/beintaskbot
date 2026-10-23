"""Isolated, fail-closed simulation queue and virtual Active-mode executor.

This module intentionally does not import a transport sender, CRM mutator,
handoff service, production worker, or Active run-store. Its only provider
boundary is a bounded text generation call whose output is persisted as a
virtual result and is never dispatched.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import uuid

from tenant_ai_simulation_schema import ensure_schema
from tenant_ai_agent_runtime_flags import enabled as runtime_flag
from tenant_ai_agent_access import tenant_status_allows_ai
from tenant_ai_agents_store import _authorize
from tenant_linear_policy import TenantLinearError, identifier
from tenant_platform import _connect, _fernet, _json


MAX_CONTEXT_MESSAGES = 24
MAX_CONTEXT_CHARS = 18000
MAX_QUEUE_DEPTH = 5000


def _iso(value):
    return value.isoformat() if hasattr(value, 'isoformat') else str(value or '')


def _as_datetime(value):
    if isinstance(value, datetime):
        result=value
    else:
        try: result=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        except (TypeError,ValueError): return None
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def _event_fingerprint(message_id):
    return hashlib.sha256(('connect:' + str(message_id)).encode('utf-8')).hexdigest()


def enqueue_inbound(cur, *, tenant_id, channel_id, conversation_id, lead_id,
                    source_message_id, source_event_id, occurred_at):
    """Enqueue only a newly accepted, post-start inbound; caller owns txn."""
    if not runtime_flag('AI_SIMULATION_ENABLED'):
        return False
    from tenant_connect_identity import conflicted
    if conflicted(cur, tenant_id, channel_id, conversation_id):
        return False
    try:
        occurred = datetime.fromisoformat(str(occurred_at).replace('Z', '+00:00'))
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        occurred = datetime.now(timezone.utc)
    cur.execute('''SELECT c.tenant_id,c.channel_ids,c.conversation_ids,c.starts_at,c.ends_at,c.message_limit
      FROM saas_ai_simulation_configs c JOIN saas_ai_agent_settings s ON s.tenant_id=c.tenant_id
      WHERE c.tenant_id=%s::uuid AND c.enabled=TRUE
        AND (s.operating_mode IS NULL OR s.operating_mode='simulation')
        AND c.emergency_stop=FALSE FOR UPDATE OF c,s''', (tenant_id,))
    config = cur.fetchone()
    if not config or not config.get('starts_at'):
        return False
    channels = config.get('channel_ids') if isinstance(config.get('channel_ids'), list) else []
    conversations = config.get('conversation_ids') if isinstance(config.get('conversation_ids'), list) else []
    if str(channel_id) not in {str(x) for x in channels}:
        return False
    if conversations and str(conversation_id) not in {str(x) for x in conversations}:
        return False
    start = _as_datetime(config['starts_at'])
    end = _as_datetime(config.get('ends_at'))
    if not start or occurred < start or (end and occurred > end):
        return False
    if int(config.get('message_limit') or 0)>0:
        cur.execute('SELECT count(*) AS n FROM saas_ai_simulation_queue WHERE tenant_id=%s::uuid AND created_at>=%s',
                    (tenant_id,start))
        if int((cur.fetchone() or {}).get('n') or 0)>=int(config['message_limit']):
            return False
    cur.execute("SELECT count(*) AS n FROM saas_ai_simulation_queue WHERE tenant_id=%s::uuid AND state IN ('queued','processing')",
                (tenant_id,))
    if int((cur.fetchone() or {}).get('n') or 0) >= MAX_QUEUE_DEPTH:
        return False
    cur.execute('''INSERT INTO saas_ai_simulation_queue
      (id,tenant_id,channel_id,conversation_id,lead_id,source_message_id,source_event_id,event_fingerprint)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s::uuid,%s,%s)
      ON CONFLICT(tenant_id,channel_id,conversation_id,source_message_id)
        WHERE recheck_of_run_id IS NULL DO NOTHING RETURNING id''',
      (str(uuid.uuid4()),tenant_id,channel_id,conversation_id,int(lead_id),str(source_message_id),
       str(source_event_id)[:500],_event_fingerprint(source_event_id)))
    return bool(cur.fetchone())


def _public_config(row):
    if not row:
        return {'enabled': False, 'emergency_stop': False, 'agent_id': None,
                'state':'stopped',
                'channel_ids': [], 'conversation_ids': [], 'token_budget': 0,
                'message_limit': 0, 'max_parallel': 1, 'max_calls_per_event': 3,
                'max_output_tokens': 800, 'starts_at': None, 'ends_at': None,
                'runtime_enabled': runtime_flag('AI_SIMULATION_ENABLED')}
    result = dict(row)
    # Tenant identity is already bound by the signed session and must not
    # leak into this UI settings payload. In particular, psycopg UUID values
    # are not JSON-serializable by aiohttp's default encoder.
    result.pop('tenant_id', None)
    if result.get('agent_id') is not None:
        result['agent_id'] = str(result['agent_id'])
    for key in ('starts_at', 'ends_at', 'updated_at'):
        result[key] = _iso(result.get(key)) if result.get(key) else None
    result['runtime_enabled'] = runtime_flag('AI_SIMULATION_ENABLED')
    return result


def read_config(profile):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _actor = _authorize(cur, profile)
            cur.execute('SELECT * FROM saas_ai_simulation_configs WHERE tenant_id=%s::uuid', (tenant,))
            return _public_config(cur.fetchone())


def save_config(profile, data):
    allowed = {'enabled','emergency_stop','state','agent_id','channel_ids','conversation_ids','confirm_start',
               'ends_at','token_budget','message_limit','max_parallel','max_calls_per_event','max_output_tokens'}
    if not isinstance(data, dict) or set(data) - allowed:
        raise TenantLinearError('Simulation ayarları düzgün deyil.')
    if any(key in data and type(data[key]) is not bool for key in ('enabled','emergency_stop','confirm_start')):
        raise TenantLinearError('Simulation statusu düzgün deyil.')
    if not runtime_flag('AI_SIMULATION_ENABLED') and data.get('enabled') is True:
        raise TenantLinearError('Simulation serverdə söndürülüb.', 409)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor = _authorize(cur, profile)
            cur.execute('''SELECT c.*,t.status AS tenant_status,s.enabled AS ai_enabled,s.operating_mode AS company_mode,s.emergency_stop AS ai_emergency
              FROM saas_ai_simulation_configs c JOIN saas_tenants t ON t.id=c.tenant_id
              LEFT JOIN saas_ai_agent_settings s ON s.tenant_id=c.tenant_id
              WHERE c.tenant_id=%s::uuid FOR UPDATE OF c,t''', (tenant,))
            current = cur.fetchone()
            if not current:
                cur.execute('''SELECT t.status AS tenant_status,s.enabled AS ai_enabled,s.operating_mode AS company_mode,s.emergency_stop AS ai_emergency
                  FROM saas_tenants t LEFT JOIN saas_ai_agent_settings s ON s.tenant_id=t.id
                  WHERE t.id=%s::uuid FOR SHARE OF t''', (tenant,))
                current = cur.fetchone() or {}
                cur.execute('INSERT INTO saas_ai_simulation_configs(tenant_id) VALUES(%s::uuid) ON CONFLICT DO NOTHING', (tenant,))
                cur.execute('SELECT * FROM saas_ai_simulation_configs WHERE tenant_id=%s::uuid FOR UPDATE', (tenant,))
                current.update(dict(cur.fetchone() or {}))
            values = {key: current.get(key) for key in allowed}
            values.update(data)
            if type(values.get('enabled')) is not bool or type(values.get('emergency_stop')) is not bool:
                raise TenantLinearError('Simulation statusu düzgün deyil.')
            enabling = values.get('enabled') is True and current.get('enabled') is not True
            if enabling and data.get('confirm_start') is not True:
                raise TenantLinearError('Simulation başlatmaq üçün təsdiq tələb olunur.', 409)
            if values.get('enabled') is True and current.get('company_mode') not in (None,'simulation'):
                raise TenantLinearError('Əvvəlcə şirkət AI rejimini Simulation seçin.',409)
            requested_state=data.get('state')
            if requested_state not in (None,'running','paused','stopped'):
                raise TenantLinearError('Simulation statusu düzgün deyil.')
            if enabling:
                state='running'
            elif values.get('enabled'):
                state='running'
            else:
                state=requested_state if requested_state in {'paused','stopped'} else 'stopped'
            for key, low, high in (('token_budget',0,2_000_000_000),('message_limit',0,1_000_000),
                                   ('max_parallel',1,10),('max_calls_per_event',1,8),('max_output_tokens',32,1800)):
                value = values.get(key)
                if type(value) is not int or not low <= value <= high:
                    raise TenantLinearError('Simulation limitləri düzgün deyil.')
            if values.get('enabled') and (values['token_budget'] <= 0 or values['message_limit'] <= 0):
                raise TenantLinearError('Simulation üçün token və mesaj limitləri təyin edin.')
            channels = values.get('channel_ids')
            conversations = values.get('conversation_ids')
            if not isinstance(channels, list) or not channels or len(channels) > 100:
                if values.get('enabled'):
                    raise TenantLinearError('Ən azı bir WhatsApp kanalı seçin.')
                channels = channels if isinstance(channels, list) else []
            try:
                channels = sorted(set(str(uuid.UUID(str(item))) for item in channels))
            except (TypeError, ValueError):
                raise TenantLinearError('WhatsApp kanalı düzgün deyil.') from None
            if not isinstance(conversations, list) or len(conversations) > 500:
                raise TenantLinearError('Dialoq siyahısı düzgün deyil.')
            conversations = sorted(set(str(item).strip()[:240] for item in conversations if str(item).strip()))
            ends_at=values.get('ends_at')
            if ends_at in ('',None):
                ends_at=None
            elif not isinstance(ends_at,datetime):
                try: ends_at=datetime.fromisoformat(str(ends_at).replace('Z','+00:00'))
                except (TypeError,ValueError): raise TenantLinearError('Simulation bitmə vaxtı düzgün deyil.') from None
            if ends_at and ends_at.tzinfo is None:
                ends_at=ends_at.replace(tzinfo=timezone.utc)
            if values.get('enabled') and ends_at and ends_at<=datetime.now(timezone.utc):
                raise TenantLinearError('Simulation bitmə vaxtı gələcək tarix olmalıdır.')
            agent_id = values.get('agent_id')
            if values.get('enabled'):
                if not runtime_flag('AI_AGENTS_ENABLED'):
                    raise TenantLinearError('AI Agents serverdə söndürülüb.',409)
                if not tenant_status_allows_ai(current.get('tenant_status'), 'connect'):
                    raise TenantLinearError('Şirkət AI emalı üçün aktiv deyil.',409)
                if current.get('ai_enabled') is not True or current.get('ai_emergency') is True:
                    raise TenantLinearError('Əvvəlcə şirkət AI ayarlarını və emergency stop vəziyyətini yoxlayın.',409)
                try: agent_id = str(uuid.UUID(str(agent_id)))
                except (ValueError, TypeError): raise TenantLinearError('Primary agent seçin.') from None
                cur.execute("SELECT id,enabled,role,mode,scopes,schedule FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE",
                            (tenant,agent_id))
                agent = cur.fetchone()
                from tenant_ai_agent_connect import scope_matches
                # Simulation has its own isolated executor.  A Manual primary
                # may therefore be evaluated virtually without changing the
                # production agent's mode or admitting an Active send.
                if (not agent or agent.get('enabled') is not True or agent.get('role') != 'primary'
                        or agent.get('mode') not in {'automatic','manual'}):
                    raise TenantLinearError('Simulation üçün aktiv Primary tələb olunur.',409)
                if any(not scope_matches(agent.get('scopes'), channel) for channel in channels):
                    raise TenantLinearError('Seçilmiş Primary bütün kanallar üçün icazəli deyil.',409)
                # Verify every selected channel, not merely at least one.
                cur.execute('''SELECT count(DISTINCT c.connect_channel_id) AS n FROM saas_tenant_integrations i
                  JOIN saas_connect_channels c ON c.tenant_id=i.tenant_id AND c.active=TRUE
                  WHERE i.tenant_id=%s::uuid AND i.provider='connect' AND i.status='connected'
                    AND c.connect_channel_id=ANY(%s::uuid[])''', (tenant,channels))
                if int((cur.fetchone() or {}).get('n') or 0) != len(channels):
                    raise TenantLinearError('Bütün seçilmiş kanallar aktiv Connect bağlantısına aid olmalıdır.',409)
            else:
                agent_id = str(agent_id) if agent_id else None
            starts_at = current.get('starts_at')
            if values.get('enabled') and current.get('enabled') is not True:
                starts_at = datetime.now(timezone.utc)
            if values.get('enabled') is not True:
                # Turning Simulation off also cancels queued work immediately.
                cur.execute("UPDATE saas_ai_simulation_queue SET state='skipped',error_code='simulation_disabled',completed_at=now() WHERE tenant_id=%s::uuid AND state='queued'", (tenant,))
            if values.get('emergency_stop') is True:
                cur.execute("UPDATE saas_ai_simulation_queue SET state='skipped',error_code='emergency_stop',completed_at=now(),lease_token=NULL,lease_until=NULL WHERE tenant_id=%s::uuid AND state='queued'", (tenant,))
                cur.execute("UPDATE saas_ai_simulation_queue SET state='skipped',error_code='emergency_stop',completed_at=now(),lease_token=NULL,lease_until=NULL WHERE tenant_id=%s::uuid AND state='processing' AND provider_started_at IS NULL", (tenant,))
            cur.execute('''UPDATE saas_ai_simulation_configs SET enabled=%s,state=%s,emergency_stop=%s,agent_id=%s::uuid,
              channel_ids=%s::jsonb,conversation_ids=%s::jsonb,starts_at=%s,ends_at=%s,token_budget=%s,
              message_limit=%s,max_parallel=%s,max_calls_per_event=%s,max_output_tokens=%s,
              version=version+1,updated_by=%s,updated_at=now() WHERE tenant_id=%s::uuid RETURNING *''',
              (bool(values.get('enabled')),state,bool(values.get('emergency_stop')),agent_id,
               _json(channels),_json(conversations),starts_at,ends_at,values['token_budget'],
               values['message_limit'],values['max_parallel'],values['max_calls_per_event'],
               values['max_output_tokens'],actor,tenant))
            result = _public_config(cur.fetchone())
        conn.commit()
        return result


def claim_next():
    if not runtime_flag('AI_SIMULATION_ENABLED'):
        return None
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            # A crashed provider call is never replayed: the charge/result may
            # be unknown. Work interrupted before reservation can be retried.
            cur.execute("UPDATE saas_ai_simulation_usage u SET state='unknown',completed_at=now() FROM saas_ai_simulation_queue q WHERE u.tenant_id=q.tenant_id AND u.queue_id=q.id AND q.state='processing' AND q.lease_until<now() AND q.provider_started_at IS NOT NULL AND u.state='reserved'")
            cur.execute("UPDATE saas_ai_simulation_usage u SET state='released',actual_tokens=0,completed_at=now() FROM saas_ai_simulation_queue q WHERE u.tenant_id=q.tenant_id AND u.queue_id=q.id AND q.state='processing' AND q.lease_until<now() AND q.provider_started_at IS NULL AND u.state='reserved'")
            cur.execute("UPDATE saas_ai_simulation_queue SET state='unknown',lease_token=NULL,lease_until=NULL,error_code='provider_result_unknown',completed_at=now() WHERE state='processing' AND lease_until<now() AND provider_started_at IS NOT NULL")
            cur.execute("UPDATE saas_ai_simulation_queue SET state='queued',lease_token=NULL,lease_until=NULL,error_code='worker_interrupted' WHERE state='processing' AND lease_until<now() AND provider_started_at IS NULL AND attempts<5")
            cur.execute("UPDATE saas_ai_simulation_queue SET state='failed',error_code='worker_interrupted',completed_at=now() WHERE state='processing' AND lease_until<now() AND provider_started_at IS NULL AND attempts>=5")
            cur.execute('''SELECT q.tenant_id
              FROM saas_ai_simulation_queue q JOIN saas_ai_simulation_configs c ON c.tenant_id=q.tenant_id
              JOIN saas_ai_agent_settings s ON s.tenant_id=q.tenant_id
              WHERE q.state='queued' AND q.available_at<=now() AND c.enabled=TRUE AND c.emergency_stop=FALSE
                AND (s.operating_mode IS NULL OR s.operating_mode='simulation')
                AND c.starts_at<=now() AND (c.ends_at IS NULL OR c.ends_at>now())
                AND (c.channel_ids @> jsonb_build_array(q.channel_id::text))
              ORDER BY q.created_at LIMIT 1''')
            tenant_candidate=cur.fetchone()
            if not tenant_candidate:
                return None
            cur.execute('SELECT max_parallel,message_limit,starts_at,channel_ids,conversation_ids FROM saas_ai_simulation_configs WHERE tenant_id=%s::uuid FOR UPDATE',(tenant_candidate['tenant_id'],))
            current_config=cur.fetchone() or {}
            # Locking the config before a queue row matches the config-save
            # lock order. It also serializes dialog ordering across claimers.
            cur.execute('''SELECT q.*,c.agent_id,c.starts_at AS simulation_started_at,c.token_budget,c.message_limit,c.max_parallel,c.max_calls_per_event,c.max_output_tokens
              FROM saas_ai_simulation_queue q JOIN saas_ai_simulation_configs c ON c.tenant_id=q.tenant_id
              JOIN saas_ai_agent_settings s ON s.tenant_id=q.tenant_id
              WHERE q.tenant_id=%s::uuid AND q.state='queued' AND q.available_at<=now()
                AND c.enabled=TRUE AND c.emergency_stop=FALSE AND c.starts_at<=now()
                AND (s.operating_mode IS NULL OR s.operating_mode='simulation')
                AND (c.ends_at IS NULL OR c.ends_at>now())
                AND c.channel_ids @> jsonb_build_array(q.channel_id::text)
                AND (jsonb_array_length(c.conversation_ids)=0 OR c.conversation_ids @> jsonb_build_array(q.conversation_id))
                AND NOT EXISTS(SELECT 1 FROM saas_ai_simulation_queue p WHERE p.tenant_id=q.tenant_id
                  AND p.channel_id=q.channel_id AND p.conversation_id=q.conversation_id
                  AND p.state='processing' AND p.lease_until>now())
              ORDER BY q.created_at FOR UPDATE OF q SKIP LOCKED LIMIT 1''',(tenant_candidate['tenant_id'],))
            row=cur.fetchone()
            if not row:
                return None
            max_parallel=int(current_config.get('max_parallel') or row['max_parallel'])
            if int(current_config.get('message_limit') or row['message_limit'] or 0)>0:
                cur.execute("SELECT count(*) AS n FROM saas_ai_simulation_queue WHERE tenant_id=%s::uuid AND created_at>=%s AND state<>'queued'",(row['tenant_id'],current_config['starts_at']))
                if int((cur.fetchone() or {}).get('n') or 0)>=int(current_config['message_limit']):
                    return None
            cur.execute('''SELECT count(*) AS n FROM saas_ai_simulation_queue
              WHERE tenant_id=%s::uuid AND state='processing' AND lease_until>now()''', (row['tenant_id'],))
            if int((cur.fetchone() or {}).get('n') or 0) >= max_parallel:
                return None
            token = str(uuid.uuid4())
            cur.execute('''UPDATE saas_ai_simulation_queue SET state='processing',attempts=attempts+1,
              lease_token=%s::uuid,lease_until=now()+interval '3 minutes'
              WHERE id=%s::uuid AND tenant_id=%s::uuid AND state='queued' RETURNING *''',
              (token,row['id'],row['tenant_id']))
            claimed = dict(cur.fetchone())
            claimed.update({key:row[key] for key in ('agent_id','token_budget','message_limit','max_parallel','max_calls_per_event','max_output_tokens')})
        conn.commit()
        return claimed


def _load_context(cur, job):
    cur.execute('''SELECT id,external_id,direction,author_name,body,message_type,happened_at,raw
      FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=%s
        AND channel=%s AND COALESCE(happened_at,created_at) <= (
          SELECT COALESCE(happened_at,created_at) FROM saas_crm_messages WHERE tenant_id=%s::uuid AND id=%s::uuid)
      ORDER BY COALESCE(happened_at,created_at) DESC,created_at DESC LIMIT %s''',
      (job['tenant_id'],job['lead_id'],'connect:'+str(job['channel_id']),job['tenant_id'],job['source_message_id'],MAX_CONTEXT_MESSAGES))
    rows=list(reversed(cur.fetchall()))
    if not rows or str(rows[-1]['id']) != str(job['source_message_id']):
        raise TenantLinearError('simulation_source_message_unavailable',409)
    if rows[-1].get('direction') != 'incoming':
        raise TenantLinearError('simulation_source_not_inbound',409)
    out=[];size=0
    for row in rows:
        body=str(row.get('body') or '')[:3000]
        raw=row.get('raw') if isinstance(row.get('raw'),dict) else {}
        employee=raw.get('verified_employee_id') is not None
        line={'role':'client' if row['direction']=='incoming' and not employee else 'sales_manager',
              'author':str(row.get('author_name') or '')[:100],
              'text':body,'at':_iso(row.get('happened_at'))}
        length=len(json.dumps(line,ensure_ascii=False).encode('utf-8'))
        if size+length>MAX_CONTEXT_CHARS:
            if not out: out.append({**line,'text':body[:max(0,MAX_CONTEXT_CHARS//3)]})
            break
        out.append(line);size+=length
    return rows,out


def _group_active_binding(cur,tenant,channel,conversation,agent_id):
    """Mirror the live group coordinator's single-primary speaking authority."""
    cur.execute("SELECT state,assignments FROM saas_ai_group_coordination WHERE tenant_id=%s::uuid AND provider='connect' AND group_jid=%s",
                (tenant,conversation))
    row=cur.fetchone()
    if not row or row.get('state')!='active': return False
    assignments=row.get('assignments') or []
    if isinstance(assignments,str):
        try: assignments=json.loads(assignments)
        except ValueError: return False
    if not isinstance(assignments,list) or len(assignments)!=1: return False
    binding=assignments[0] if isinstance(assignments[0],dict) else {}
    return str(binding.get('agent_id') or '')==str(agent_id) and str(binding.get('channel_id') or '')==str(channel)


def _reserve_call(cur, job, agent_id, call_no, call_kind, reserve_tokens):
    if not runtime_flag('AI_SIMULATION_ENABLED'):
        return False
    cur.execute('''SELECT c.enabled,c.emergency_stop,c.token_budget,c.message_limit,c.max_calls_per_event,c.max_parallel,
        (SELECT count(*) FROM saas_ai_simulation_queue q WHERE q.tenant_id=c.tenant_id
          AND q.created_at>=c.starts_at AND q.state<>'queued') AS messages,
        (SELECT count(*) FROM saas_ai_simulation_queue q WHERE q.tenant_id=c.tenant_id AND q.state='processing'
          AND q.lease_until>now()) AS parallel,
        (SELECT COALESCE(sum(CASE WHEN state IN ('reserved','unknown') THEN reserved_tokens ELSE actual_tokens END),0)
          FROM saas_ai_simulation_usage u WHERE u.tenant_id=c.tenant_id AND u.created_at>=c.starts_at) AS used
      FROM saas_ai_simulation_configs c WHERE c.tenant_id=%s::uuid FOR UPDATE OF c''', (job['tenant_id'],))
    config=cur.fetchone() or {}
    if not config.get('enabled') or config.get('emergency_stop'):
        return False
    cur.execute("SELECT enabled,operating_mode,emergency_stop FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid FOR UPDATE",(job['tenant_id'],))
    company=cur.fetchone() or {}
    if (company.get('enabled') is not True or company.get('emergency_stop') is True
            or company.get('operating_mode') not in (None,'simulation')):
        return False
    if config.get('message_limit') and int(config.get('messages') or 0)>int(config['message_limit']):
        return False
    if int(config.get('parallel') or 0)>int(config['max_parallel']):
        return False
    if int(call_no)>int(config['max_calls_per_event']):
        return False
    if int(config.get('token_budget') or 0) and int(config.get('used') or 0)+reserve_tokens>int(config['token_budget']):
        return False
    cur.execute('''INSERT INTO saas_ai_simulation_usage(tenant_id,queue_id,call_no,agent_id,call_kind,reserved_tokens,state)
      VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,'reserved') ON CONFLICT DO NOTHING RETURNING call_no''',
      (job['tenant_id'],job['id'],call_no,agent_id,call_kind,reserve_tokens))
    return bool(cur.fetchone())


def _provider_token_reservation(mode,lines,draft,instructions,extra_context,max_output_tokens):
    payload={'mode':mode,'conversation':lines,'manager_draft':draft}
    if extra_context is not None: payload['extra_context']=extra_context
    input_bytes=(len(json.dumps(payload,ensure_ascii=False).encode('utf-8'))
                 +len(str(instructions or '').encode('utf-8'))+1024)
    # Two UTF-8 bytes per token is intentionally conservative for AZ/RU/EN.
    return max(1,int(max_output_tokens))+((input_bytes+1)//2)+512


def _adjust_reservation(cur,job,call_no,required_tokens):
    """Atomically grow a pre-call reservation to the fully assembled prompt."""
    if not runtime_flag('AI_SIMULATION_ENABLED'):
        return False
    cur.execute('''SELECT c.enabled,c.emergency_stop,c.token_budget,c.max_calls_per_event,c.starts_at,
        s.enabled AS company_enabled,s.operating_mode AS company_mode,s.emergency_stop AS company_stop
      FROM saas_ai_simulation_configs c JOIN saas_ai_agent_settings s ON s.tenant_id=c.tenant_id
      WHERE c.tenant_id=%s::uuid FOR UPDATE OF c,s''',(job['tenant_id'],))
    config=cur.fetchone() or {}
    if (config.get('enabled') is not True or config.get('emergency_stop') is True
            or config.get('company_enabled') is not True or config.get('company_mode') not in (None,'simulation') or config.get('company_stop') is True
            or int(call_no)>int(config.get('max_calls_per_event') or 0)):
        return False
    cur.execute("SELECT reserved_tokens,state FROM saas_ai_simulation_usage WHERE tenant_id=%s::uuid AND queue_id=%s::uuid AND call_no=%s FOR UPDATE",(job['tenant_id'],job['id'],call_no))
    own=cur.fetchone() or {}
    if own.get('state')!='reserved': return False
    old=max(0,int(own.get('reserved_tokens') or 0))
    cur.execute('''SELECT COALESCE(sum(CASE WHEN u.state IN ('reserved','unknown') THEN u.reserved_tokens ELSE u.actual_tokens END),0) AS used
      FROM saas_ai_simulation_usage u JOIN saas_ai_simulation_configs c ON c.tenant_id=u.tenant_id
      WHERE u.tenant_id=%s::uuid AND u.created_at>=c.starts_at
        AND NOT (u.queue_id=%s::uuid AND u.call_no=%s)''',(job['tenant_id'],job['id'],call_no))
    used=int((cur.fetchone() or {}).get('used') or 0)
    cap=int(config.get('token_budget') or 0)
    if cap and used+max(1,int(required_tokens))>cap: return False
    cur.execute("UPDATE saas_ai_simulation_usage SET reserved_tokens=%s WHERE tenant_id=%s::uuid AND queue_id=%s::uuid AND call_no=%s AND state='reserved'",
                (max(1,int(required_tokens)),job['tenant_id'],job['id'],call_no))
    return True


def _mark_provider_started(cur,job,call_no):
    """Durably fence one paid call immediately before crossing provider boundary."""
    if not runtime_flag('AI_SIMULATION_ENABLED') or not runtime_flag('AI_AGENTS_ENABLED'):
        return False
    from tenant_connect_identity import conflicted
    if conflicted(cur, job['tenant_id'], job['channel_id'], job['conversation_id']):
        return False
    cur.execute('''SELECT c.agent_id AS configured_agent,c.enabled,c.emergency_stop,c.starts_at,c.ends_at,c.channel_ids,c.conversation_ids,
        s.enabled AS company_enabled,s.operating_mode AS company_mode,s.emergency_stop AS company_stop,t.status AS tenant_status,
        q.channel_id,q.conversation_id,q.lead_id,q.source_event_id,q.recheck_of_run_id,u.agent_id AS call_agent,u.call_kind
      FROM saas_ai_simulation_configs c JOIN saas_ai_agent_settings s ON s.tenant_id=c.tenant_id
      JOIN saas_tenants t ON t.id=c.tenant_id
      JOIN saas_ai_simulation_queue q ON q.tenant_id=c.tenant_id AND q.id=%s::uuid
      JOIN saas_ai_simulation_usage u ON u.tenant_id=q.tenant_id AND u.queue_id=q.id AND u.call_no=%s
      WHERE c.tenant_id=%s::uuid AND q.state='processing' AND q.lease_token=%s::uuid
        AND u.state='reserved' FOR UPDATE OF c,s,t,q,u''',
      (job['id'],call_no,job['tenant_id'],job['lease_token']))
    row=cur.fetchone() or {}
    channels=row.get('channel_ids') or [];dialogs=row.get('conversation_ids') or []
    if isinstance(channels,str):
        try: channels=json.loads(channels)
        except ValueError: channels=[]
    if isinstance(dialogs,str):
        try: dialogs=json.loads(dialogs)
        except ValueError: dialogs=[]
    starts=_as_datetime(row.get('starts_at'));ends=_as_datetime(row.get('ends_at'));now=datetime.now(timezone.utc)
    valid=(row.get('enabled') is True and row.get('emergency_stop') is not True
      and row.get('company_enabled') is True and row.get('company_mode') in (None,'simulation') and row.get('company_stop') is not True
      and tenant_status_allows_ai(row.get('tenant_status'),'connect') and starts and starts<=now
      and (not ends or ends>now) and str(job['channel_id']) in {str(x) for x in channels}
      and (not dialogs or str(job['conversation_id']) in {str(x) for x in dialogs})
      and str(row.get('channel_id') or '')==str(job['channel_id'])
      and str(row.get('conversation_id') or '')==str(job['conversation_id']))
    if valid:
        from tenant_ai_agent_connect import unique_connect_candidate
        from tenant_ai_agent_schedule import is_open
        from tenant_ai_group_coordination import group_jid
        is_group=bool(group_jid(job['conversation_id']))
        cur.execute("SELECT id,enabled,role,mode,scopes,schedule,expert_policy FROM saas_ai_agents WHERE tenant_id=%s::uuid AND role='primary' ORDER BY created_at,id FOR SHARE",(job['tenant_id'],))
        primaries=[dict(item) for item in cur.fetchall()]
        primary,_reason=unique_connect_candidate(primaries,str(job['channel_id']),is_group=is_group)
        # This is the last fence before a Simulation provider call.  Manual is
        # safe here because the executor is virtual and has no send/CRM/handoff
        # imports; it must never relax the Active worker's automatic-only gate.
        valid=bool(primary and str(primary['id'])==str(row.get('configured_agent'))
          and primary.get('mode') in {'automatic','manual'} and is_open(primary.get('schedule') or {}))
        if valid and row.get('call_kind')=='primary':
            valid=str(row.get('call_agent'))==str(primary['id'])
        elif valid and row.get('call_kind')=='expert':
            cur.execute("SELECT id,enabled,role,mode,scopes,schedule FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE",
                        (job['tenant_id'],row.get('call_agent')))
            expert=cur.fetchone()
            valid=bool(expert and expert.get('enabled') is True and expert.get('role')=='expert'
              and expert.get('mode')=='manual' and not expert.get('scopes') and is_open(expert.get('schedule') or {}))
            if valid:
                try:
                    from tenant_ai_expert_policy import policy,effective
                    effective(policy(primary.get('expert_policy') or {}),expert,str(primary['id']))
                except Exception:
                    valid=False
        else:
            valid=False
        if valid and is_group:
            valid=_group_active_binding(cur,str(job['tenant_id']),str(job['channel_id']),
                                        str(job['conversation_id']),str(primary['id']))
        if valid and not row.get('recheck_of_run_id'):
            cur.execute("SELECT agent_id,state FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id=%s",
                        (job['tenant_id'],str(job['channel_id']),str(job['conversation_id'])))
            live=cur.fetchone() or {}
            valid=(live.get('state') not in {'human','paused'} and
                   (not live.get('agent_id') or str(live.get('agent_id'))==str(primary['id'])))
        if valid:
            cur.execute('''SELECT 1 FROM saas_connect_conversations d
              JOIN saas_connect_channels ch ON ch.tenant_id=d.tenant_id AND ch.connect_channel_id=d.connect_channel_id AND ch.active=TRUE
              JOIN saas_tenant_integrations i ON i.tenant_id=d.tenant_id AND i.provider='connect' AND i.status='connected'
              WHERE d.tenant_id=%s::uuid AND d.connect_channel_id=%s::uuid AND d.conversation_id=%s AND d.lead_id=%s''',
              (job['tenant_id'],job['channel_id'],job['conversation_id'],row.get('lead_id')))
            valid=bool(cur.fetchone())
        if valid:
            fingerprint=_event_fingerprint(row.get('source_event_id'))
            cur.execute('SELECT ai_legacy_blocked(%s::uuid,%s,%s,%s) AS blocked',
                        (job['tenant_id'],str(job['channel_id']),str(job['conversation_id']),fingerprint))
            valid=not bool((cur.fetchone() or {}).get('blocked'))
        if valid and not row.get('recheck_of_run_id'):
            fingerprint=_event_fingerprint(row.get('source_event_id'))
            cur.execute("SELECT 1 FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect' AND scope_channel_id=%s AND conversation_id=%s AND event_fingerprint=%s AND state IN ('queued','processing','sent','unknown') LIMIT 1",
                        (job['tenant_id'],str(job['channel_id']),str(job['conversation_id']),fingerprint))
            valid=not bool(cur.fetchone())
        if valid and not row.get('recheck_of_run_id'):
            cur.execute("SELECT 1 FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect' AND scope_channel_id=%s AND conversation_id=%s AND state IN ('queued','processing') LIMIT 1",
                        (job['tenant_id'],str(job['channel_id']),str(job['conversation_id'])))
            valid=not bool(cur.fetchone())
    if not valid:
        cur.execute("UPDATE saas_ai_simulation_usage SET state='released',actual_tokens=0,completed_at=now() WHERE tenant_id=%s::uuid AND queue_id=%s::uuid AND call_no=%s AND state='reserved'",(job['tenant_id'],job['id'],call_no))
        return False
    cur.execute("UPDATE saas_ai_simulation_queue SET provider_started_at=now(),lease_until=now()+interval '3 minutes' WHERE tenant_id=%s::uuid AND id=%s::uuid AND lease_token=%s::uuid AND state='processing' RETURNING id",(job['tenant_id'],job['id'],job['lease_token']))
    return bool(cur.fetchone())


def _provider_credentials(cur, tenant_id, agent_id, provider, model):
    from tenant_ai_agent_key_policy import choose_api_credential, platform_key_ready, platform_model, platform_service_ready
    cur.execute("SELECT metadata,secrets FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'",(tenant_id,))
    integration=cur.fetchone() or {};metadata=integration.get('metadata') or {}
    selected=str(model or '')
    if not selected and platform_service_ready(): selected=platform_model()
    if not selected: selected=str(metadata.get('model') or '')
    company_key=''
    if integration.get('secrets'):
        try:
            secret=json.loads(_fernet().decrypt(bytes(integration['secrets'])).decode('utf-8'))
            if secret.get('tenant_id')==str(tenant_id) and secret.get('provider')=='chat_ai':
                company_key=str(secret.get('api_key') or '')
        except Exception:
            company_key=''
    use_platform=platform_key_ready(tenant_id,agent_id,provider) and (
        not company_key or __import__('tenant_ai_agent_key_policy').platform_model_allowed(selected))
    if not use_platform and (metadata.get('enabled') is not True or metadata.get('consent') is not True):
        raise TenantLinearError('simulation_ai_consent_missing',409)
    key,source=choose_api_credential(tenant_id,agent_id,provider,company_key,selected)
    if not selected or not key: raise TenantLinearError('simulation_model_unavailable',409)
    return key,selected,source,metadata


def _safe_expert(cur, job, primary, trigger, problem, lines, primary_advisory, config, call_no):
    from tenant_ai_expert_policy import policy,helper_ids,effective,result as validate_result
    condition=trigger.get('condition') or {}
    requested=condition.get('expert_agent_ids') if isinstance(condition,dict) else None
    selected=helper_ids(policy(primary.get('expert_policy') or {}))
    if requested:
        selected=[str(x) for x in requested if str(x) in set(selected)]
    if len(selected)!=1:
        return None,'expert_selection_ambiguous_or_unbound',call_no
    expert_id=selected[0]
    cur.execute("SELECT id,name,enabled,role,mode,scopes,schedule,model,instructions,language,expert_policy,version FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE",(job['tenant_id'],expert_id))
    expert=cur.fetchone()
    from tenant_ai_agent_schedule import is_open
    if (not expert or not expert['enabled'] or expert['role']!='expert' or expert['mode']!='manual'
            or expert.get('scopes') or not is_open(expert.get('schedule') or {})):
        return None,'expert_unavailable',call_no
    try:
        effective_policy=effective(policy(primary.get('expert_policy') or {}),expert,primary['id'])
    except Exception:
        return None,'expert_not_allowed',call_no
    call_no+=1
    reserve=max(1,int(config['max_output_tokens']))+512
    if not _reserve_call(cur,job,expert_id,call_no,'expert',reserve):
        return None,'simulation_budget_exhausted',call_no
    from tenant_ai_agent_run_store import knowledge_context
    key,model,_source,_metadata=_provider_credentials(cur,job['tenant_id'],expert_id,'connect',expert.get('model'))
    allowed_tools=effective_policy.get('tools') or []
    knowledge=(knowledge_context(job['tenant_id'],problem,agent_id=expert_id)
               if 'instruction_library' in allowed_tools else [])
    instructions=('Internal simulation expert. Do not address the customer. Do not claim external actions. '
      'Return JSON with cause, evidence, confidence, safe_actions, additional_information, requires_human, ticket_summary. '
      'Use only supplied evidence; never invoke tools.\nAgent instructions: '+str(expert.get('instructions') or '')[:5000])
    context={'problem':problem[:2000],'primary_advisory':primary_advisory[:3000],
             'knowledge':knowledge,'allowed_tools':allowed_tools,'simulation':True}
    from tenant_chat_ai_provider import generate
    expert_reservation=_provider_token_reservation('expert',lines,'',instructions,context,min(int(config['max_output_tokens']),800))
    if not _adjust_reservation(cur,job,call_no,expert_reservation):
        cur.execute("UPDATE saas_ai_simulation_usage SET state='released',actual_tokens=0,completed_at=now() WHERE tenant_id=%s::uuid AND queue_id=%s::uuid AND call_no=%s AND state='reserved'",(job['tenant_id'],job['id'],call_no))
        return None,'simulation_budget_exhausted',call_no
    from tenant_ai_spend_budget import reserve as reserve_spend, release as release_spend, settle as settle_spend
    spend_key='simulation:'+str(job['id'])+':'+str(call_no)
    reserve_spend(cur,job['tenant_id'],spend_key,'simulation_expert',model,total_upper=expert_reservation)
    if not _mark_provider_started(cur,job,call_no):
        cur.connection.commit()
        release_spend(job['tenant_id'],spend_key)
        return None,'simulation_stopped_before_provider',call_no
    cur.connection.commit()
    try:
        raw,usage=generate(key,model,'expert',lines,'',instructions=instructions,extra_context=context,
            with_usage=True,max_output_tokens=min(int(config['max_output_tokens']),800))
    except Exception:
        cur.execute("UPDATE saas_ai_simulation_usage SET state='unknown',actual_tokens=reserved_tokens,completed_at=now() WHERE tenant_id=%s::uuid AND queue_id=%s::uuid AND call_no=%s AND state='reserved'",(job['tenant_id'],job['id'],call_no))
        cur.connection.commit()
        settle_spend(job['tenant_id'],spend_key,{},unknown=True)
        raise
    total=max(0,int(usage.get('total_tokens') or 0))
    try: result=validate_result(json.loads(raw))
    except Exception:
        cur.execute("UPDATE saas_ai_simulation_usage SET state='failed',actual_tokens=CASE WHEN %s>0 THEN %s ELSE reserved_tokens END,completed_at=now() WHERE tenant_id=%s::uuid AND queue_id=%s::uuid AND call_no=%s",(total,total,job['tenant_id'],job['id'],call_no))
        cur.connection.commit()
        settle_spend(job['tenant_id'],spend_key,usage or {},unknown=usage is None)
        return None,'expert_invalid_result',call_no
    cur.execute("UPDATE saas_ai_simulation_usage SET actual_tokens=CASE WHEN %s>0 THEN %s ELSE reserved_tokens END,state='succeeded',completed_at=now() WHERE tenant_id=%s::uuid AND queue_id=%s::uuid AND call_no=%s",(total,total,job['tenant_id'],job['id'],call_no))
    cur.connection.commit()
    settle_spend(job['tenant_id'],spend_key,usage or {},unknown=usage is None)
    return {'agent_id':expert_id,'result':result,'knowledge':knowledge,'tokens':total},'',call_no


def _finish_call(job,call_no,*,state,actual=0):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_ai_simulation_usage SET state=%s,
              actual_tokens=CASE WHEN %s>0 THEN %s ELSE reserved_tokens END,completed_at=now()
              WHERE tenant_id=%s::uuid AND queue_id=%s::uuid AND call_no=%s AND state='reserved' ''',
              (state,max(0,int(actual)),max(0,int(actual)),job['tenant_id'],job['id'],call_no))
        conn.commit()


def _complete(job, outcome, *, reply='', stop_reason='', agent_id=None, triggers=(), knowledge=(), experts=(), events=(), total_tokens=0, error=''):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT state FROM saas_ai_simulation_queue WHERE tenant_id=%s::uuid AND id=%s::uuid
              AND state='processing' AND lease_token=%s::uuid FOR UPDATE''',(job['tenant_id'],job['id'],job['lease_token']))
            if not cur.fetchone(): return False
            # A terminal result cannot leave pre-call reservations consuming
            # budget. Calls already started are settled/unknown separately.
            cur.execute("UPDATE saas_ai_simulation_usage SET state='released',actual_tokens=0,completed_at=now() WHERE tenant_id=%s::uuid AND queue_id=%s::uuid AND state='reserved'",(job['tenant_id'],job['id']))
            cur.execute('''INSERT INTO saas_ai_simulation_runs(id,tenant_id,queue_id,channel_id,conversation_id,
              source_message_id,primary_agent_id,outcome,virtual_reply,virtual_stop_reason,virtual_triggers,
              knowledge_refs,expert_results,events,total_tokens)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s::uuid,%s::uuid,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s)
              ON CONFLICT(queue_id) DO NOTHING''',
              (str(uuid.uuid4()),job['tenant_id'],job['id'],job['channel_id'],job['conversation_id'],job['source_message_id'],
               agent_id,outcome,str(reply or '')[:8000],str(stop_reason or '')[:200],_json(list(triggers)),_json(list(knowledge)),
               _json(list(experts)),_json(list(events)),max(0,int(total_tokens))))
            if not job.get('recheck_of_run_id'):
                cur.execute('''INSERT INTO saas_ai_simulation_dialog_state
                  (tenant_id,channel_id,conversation_id,session_started_at,last_queue_id,virtual_state)
                  VALUES(%s::uuid,%s::uuid,%s,%s,%s::uuid,%s::jsonb)
                  ON CONFLICT(tenant_id,channel_id,conversation_id) DO UPDATE SET
                    session_started_at=EXCLUDED.session_started_at,
                    revision=CASE WHEN saas_ai_simulation_dialog_state.session_started_at=EXCLUDED.session_started_at
                      THEN saas_ai_simulation_dialog_state.revision+1 ELSE 1 END,
                    last_queue_id=EXCLUDED.last_queue_id,virtual_state=EXCLUDED.virtual_state,updated_at=now()''',
                  (job['tenant_id'],job['channel_id'],job['conversation_id'],job.get('simulation_started_at') or datetime.now(timezone.utc),
                   job['id'],_json({'outcome':str(outcome or '')[:40],'last_reply':str(reply or '')[:4000],
                     'stop_reason':str(stop_reason or '')[:200],'sent':False,'source_message_id':str(job['source_message_id'])})))
            cur.execute("UPDATE saas_ai_simulation_queue SET state=%s,error_code=%s,lease_token=NULL,lease_until=NULL,completed_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND lease_token=%s::uuid",
              ('unknown' if outcome=='unknown' else ('failed' if outcome=='error' else ('skipped' if outcome in {'skipped','unsupported','blocked'} else 'completed')),str(error or '')[:80],job['tenant_id'],job['id'],job['lease_token']))
        conn.commit()
    return True


def process(job):
    """Evaluate one queue item and persist only virtual output and usage."""
    from tenant_ai_agent_connect import scope_matches,unique_connect_candidate
    from tenant_ai_group_coordination import group_jid
    from tenant_ai_agent_schedule import is_open
    from tenant_ai_agent_actions import enabled_triggers,matching_triggers,handoff_reason
    tenant=str(job['tenant_id']);channel=str(job['channel_id']);conversation=str(job['conversation_id'])
    events=[{'type':'inbound_received','source_message_id':str(job['source_message_id'])},
            {'type':'scope_checked','channel_id':channel,'conversation_id':conversation}]
    total_tokens=0;call_no=0;precomputed_no_answer=None
    try:
        with _connect() as conn:
            ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute('''SELECT q.id,q.recheck_of_run_id,c.enabled,c.emergency_stop,c.agent_id,c.channel_ids,c.conversation_ids,
                    c.starts_at,c.ends_at,c.token_budget,c.message_limit,c.max_parallel,c.max_calls_per_event,c.max_output_tokens,
                    a.id AS primary_id,a.name,a.instructions,a.enabled AS agent_enabled,a.role,a.mode,a.scopes,a.schedule,a.model,a.language,a.style,a.safety,a.expert_policy,a.response_policy,
                    s.enabled AS company_enabled,s.operating_mode AS company_mode,s.emergency_stop AS company_stop,t.status AS tenant_status,
                    ch.active AS channel_active,EXISTS(SELECT 1 FROM saas_tenant_integrations i WHERE i.tenant_id=q.tenant_id
                      AND i.provider='connect' AND i.status='connected') AS connect_active,
                    EXISTS(SELECT 1 FROM saas_connect_conversations d WHERE d.tenant_id=q.tenant_id
                      AND d.connect_channel_id=q.channel_id AND d.conversation_id=q.conversation_id AND d.lead_id=q.lead_id) AS route_valid,
                    cs.agent_id AS assigned_agent,cs.state AS live_state,ci.metadata AS ai_metadata,ci.secrets AS ai_secrets,
                    (SELECT company_instructions FROM saas_ai_agent_settings WHERE tenant_id=q.tenant_id) AS company_instructions
                    ,(SELECT d.virtual_state FROM saas_ai_simulation_dialog_state d WHERE q.recheck_of_run_id IS NULL AND d.tenant_id=q.tenant_id
                       AND d.channel_id=q.channel_id AND d.conversation_id=q.conversation_id
                       AND d.session_started_at=c.starts_at) AS prior_virtual_state
                  FROM saas_ai_simulation_queue q JOIN saas_ai_simulation_configs c ON c.tenant_id=q.tenant_id
                  JOIN saas_tenants t ON t.id=q.tenant_id LEFT JOIN saas_ai_agent_settings s ON s.tenant_id=q.tenant_id
                  LEFT JOIN saas_connect_channels ch ON ch.tenant_id=q.tenant_id AND ch.connect_channel_id=q.channel_id
                  LEFT JOIN saas_ai_agents a ON a.tenant_id=q.tenant_id AND a.id=c.agent_id
                  LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=q.tenant_id AND cs.provider='connect'
                    AND cs.channel_id=q.channel_id::text AND cs.conversation_id=q.conversation_id
                  LEFT JOIN saas_tenant_integrations ci ON ci.tenant_id=q.tenant_id AND ci.provider='chat_ai'
                  WHERE q.tenant_id=%s::uuid AND q.id=%s::uuid AND q.lease_token=%s::uuid''',
                  (tenant,job['id'],job['lease_token']))
                row=cur.fetchone()
                if not row:
                    return False
                cur.execute('SELECT ai_legacy_blocked(%s::uuid,%s,%s,%s) AS blocked',
                            (tenant,channel,conversation,_event_fingerprint(job['source_event_id'])))
                row['legacy_blocked']=bool((cur.fetchone() or {}).get('blocked'))
                if (not row.get('enabled') or row.get('emergency_stop') or row.get('company_stop')
                        or row.get('company_mode') not in (None,'simulation')):
                    outcome='blocked';reason='emergency_stop' if row.get('emergency_stop') or row.get('company_stop') else 'simulation_disabled'
                    events.append({'type':'simulation_blocked','reason':reason})
                    result=(outcome,reason,None,[],[],[],[],0)
                elif (not row.get('channel_active') or not row.get('connect_active') or not row.get('route_valid')
                      or not tenant_status_allows_ai(row.get('tenant_status'),'connect')):
                    result=('blocked','route_or_tenant_unavailable',None,[],[],[],[],0)
                elif row.get('legacy_blocked'):
                    result=('blocked','legacy_channel_ambiguous',None,[],[],[],[],0)
                else:
                    cur.execute("SELECT id,scopes,enabled,role,mode,schedule FROM saas_ai_agents WHERE tenant_id=%s::uuid AND role='primary' ORDER BY created_at,id",(tenant,))
                    agents=[dict(x) for x in cur.fetchall()]
                    is_group=bool(group_jid(conversation))
                    chosen,_reason=unique_connect_candidate(agents,channel,is_group=is_group)
                    if str(row.get('primary_id') or '')!=(str(chosen['id']) if chosen else ''):
                        result=('blocked','primary_not_unique_or_scope_mismatch',None,[],[],[],[],0)
                    elif not row.get('recheck_of_run_id') and row.get('assigned_agent') and str(row['assigned_agent'])!=str(chosen['id']):
                        result=('blocked','explicit_assignment_preserved',None,[],[],[],[],0)
                    elif (row.get('role')!='primary' or row.get('agent_enabled') is not True
                          or row.get('mode') not in {'automatic','manual'}):
                        result=('blocked','primary_not_simulation_eligible',None,[],[],[],[],0)
                    elif is_group and not _group_active_binding(cur,tenant,channel,conversation,str(chosen['id'])):
                        result=('virtual_stop','group_not_active_or_primary_not_bound',str(chosen['id']),[],[],[],
                                events+[{'type':'virtual_stop','reason':'group_not_active_or_primary_not_bound'}],0)
                    elif not row.get('recheck_of_run_id') and row.get('live_state') in {'human','paused'}:
                        result=('virtual_stop','conversation_human_or_paused',str(chosen['id']),[],[],[],
                                events+[{'type':'virtual_stop','reason':'conversation_human_or_paused'}],0)
                    elif not is_open(row.get('schedule') or {}):
                        result=('skipped','outside_schedule',str(chosen['id']),[],[],[],[],0)
                    else:
                        from tenant_ai_agent_connect import _whatsapp_phone
                        source_rows,lines=_load_context(cur,job)
                        source=source_rows[-1];text=str(source.get('body') or '')
                        media_type=str(source.get('message_type') or 'text').casefold()
                        if media_type not in {'text','chat','conversation'} and not text.strip():
                            result=('unsupported','simulation_media_context_unavailable',str(chosen['id']),[],[],[],
                                    events+[{'type':'simulation_unsupported','reason':'media_context_unavailable','message_type':media_type}],0)
                        else:
                            if not row.get('recheck_of_run_id'):
                                fingerprint=_event_fingerprint(job['source_event_id'])
                                cur.execute("SELECT 1 FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect' AND scope_channel_id=%s AND conversation_id=%s AND event_fingerprint=%s AND state IN ('queued','processing','sent','unknown') LIMIT 1",
                                            (tenant,channel,conversation,fingerprint))
                                active_conflict=bool(cur.fetchone())
                            else:
                                active_conflict=False
                        if media_type not in {'text','chat','conversation'} and not text.strip():
                            pass
                        elif active_conflict:
                            result=('skipped','active_mode_already_processed',str(chosen['id']),[],[],[],[],0)
                        else:
                            triggers=enabled_triggers(tenant,str(chosen['id']))
                            first=sum(1 for x in source_rows if x.get('direction')=='incoming')==1
                            matched=matching_triggers(triggers,text,first_inquiry=first,phone=_whatsapp_phone(conversation))
                            matched_payload=[{'id':str(x.get('id') or ''),'name':str(x.get('name') or '')[:120],
                                              'actions':[str(a) for a in x.get('actions') or []]} for x in matched]
                            events.append({'type':'trigger_checked','enabled_count':len(triggers),
                                'matched':bool(matched),'matched_trigger_ids':[item['id'] for item in matched_payload]})
                            events.extend([{'type':'trigger_matched','trigger_id':x['id'],'actions':x['actions']} for x in matched_payload])
                            handoff=handoff_reason('\n'.join(str(x.get('text') or '') for x in lines if x.get('role')=='client'))
                            action_names={action for item in matched for action in item.get('actions') or []}
                            if handoff or 'handoff' in action_names:
                                stop=handoff or 'configured_handoff_trigger'
                                events.append({'type':'virtual_handoff','reason':stop,'executed':False})
                                result=('virtual_stop',stop,str(chosen['id']),matched_payload,[],[],events,0)
                            else:
                                unsupported=sorted(action_names & {'create_task','update_phone','move_stage','handoff_after_reply'})
                                if unsupported:
                                    events.append({'type':'simulation_unsupported','actions':unsupported,'executed':False})
                                result=('ready','',str(chosen['id']),matched_payload,[],[],events,0)
                conn.commit()
        outcome,reason,primary_id,triggers,knowledge,experts,event_rows,total_tokens=result
        if outcome!='ready':
            _complete(job,outcome,stop_reason=reason,agent_id=primary_id,triggers=triggers,knowledge=knowledge,
                      experts=experts,events=event_rows or events,total_tokens=total_tokens,error=reason)
            return True

        # Content, current settings and scope are revalidated immediately
        # before each paid model call; provider calls occur outside DB locks.
        with _connect() as conn:
            ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute('''SELECT c.enabled,c.emergency_stop,c.max_output_tokens,c.max_calls_per_event,c.token_budget,
                    a.id,a.instructions,a.model,a.language,a.style,a.safety,a.expert_policy,a.response_policy,a.schedule,
                    s.enabled AS company_enabled,s.operating_mode AS company_mode,s.emergency_stop AS company_stop,t.status AS tenant_status,
                    i.metadata,i.secrets FROM saas_ai_simulation_configs c
                  JOIN saas_ai_agents a ON a.tenant_id=c.tenant_id AND a.id=c.agent_id
                  JOIN saas_ai_agent_settings s ON s.tenant_id=c.tenant_id JOIN saas_tenants t ON t.id=c.tenant_id
                  LEFT JOIN saas_tenant_integrations i ON i.tenant_id=c.tenant_id AND i.provider='chat_ai'
                  WHERE c.tenant_id=%s::uuid FOR SHARE OF c,a,s,t''',(tenant,))
                config=cur.fetchone() or {}
                if (not runtime_flag('AI_SIMULATION_ENABLED') or not runtime_flag('AI_AGENTS_ENABLED')
                    or not config.get('enabled') or config.get('emergency_stop') or config.get('company_stop')
                    or config.get('company_mode') not in (None,'simulation')
                    or not config.get('company_enabled') or not tenant_status_allows_ai(config.get('tenant_status'),'connect')
                    or not is_open(config.get('schedule') or {})):
                    _complete(job,'blocked',stop_reason='simulation_revoked_or_schedule_closed',agent_id=primary_id,events=events)
                    return True
                source_rows,lines=_load_context(cur,job);source=source_rows[-1];text=str(source.get('body') or '')
                problem='\n'.join(str(x.get('text') or '') for x in lines if x.get('role')=='client')[-6000:]
                # Existing tenant-scoped retrieval; only source IDs/titles are
                # logged, never model reasoning or secrets.
                from tenant_ai_agent_run_store import knowledge_context
                knowledge=knowledge_context(tenant,problem,agent_id=primary_id)
                response_policy=config.get('response_policy') or {}
                triggers_live=enabled_triggers(tenant,primary_id)
                matching=matching_triggers(triggers_live,text,
                    first_inquiry=sum(1 for x in source_rows if x.get('direction')=='incoming')==1,
                    phone=_whatsapp_phone(conversation))
                if response_policy.get('enabled') is True and response_policy.get('action')=='call_expert':
                    matching=list(matching)+[{'id':'no_answer_policy','name':'Configured no-answer Expert',
                        'condition':{},'actions':['call_expert']}]
                if response_policy.get('enabled') is True and not knowledge and response_policy.get('action')!='call_expert':
                    from tenant_ai_agent_no_answer import selected_action
                    precomputed_no_answer=selected_action(response_policy,'missing_knowledge')
                else:
                    key,model,_source,_meta=_provider_credentials(cur,tenant,primary_id,'connect',config.get('model'))
                    reserve=max(1,min(int(config['max_output_tokens']),1800))+512
                    call_no=1
                    if not _reserve_call(cur,job,primary_id,call_no,'primary',reserve):
                        conn.commit();_complete(job,'blocked',stop_reason='simulation_budget_exhausted',agent_id=primary_id,events=events);return True
                    bound_primary=dict(config)
                conn.commit()
        if precomputed_no_answer:
            action=precomputed_no_answer['action'];reply=''
            events.append({'type':'virtual_no_answer_action','action':action,
                'reason':precomputed_no_answer['reason'],'executed':False})
            if action=='handoff':events.append({'type':'virtual_handoff','reason':'no_answer','executed':False})
            elif action=='create_task':events.append({'type':'virtual_employee_request','reason':'no_answer','executed':False})
            elif action=='clarify':
                reply={'ru':'Уточните, пожалуйста, о каком товаре или заказе идёт речь и что именно нужно проверить.',
                    'en':'Could you share which product or order this concerns and what you need us to check?',
                    'tr':'Lütfen hansı məhsul və ya sifariş haqqında olduğunu və neyi dəqiqləşdirməli olduğumuzu belirtin.',
                    'az':'Zəhmət olmasa, hansı məhsul və ya sifariş barədə olduğunu və nəyi dəqiqləşdirməli olduğumuzu yazın.'}.get(str(config.get('language') or 'auto'),
                    'Zəhmət olmasa, hansı məhsul və ya sifariş barədə olduğunu və nəyi dəqiqləşdirməli olduğumuzu yazın.')
            elif action=='stop':reply=str(response_policy.get('stop_reply') or '')
            _complete(job,'virtual_reply' if reply else 'virtual_stop',reply=reply,
                stop_reason='no_answer_'+action,agent_id=primary_id,triggers=matched_payload,knowledge=knowledge,
                experts=[],events=events,total_tokens=total_tokens)
            return True
        expert_results=[];advisory=[]
        called_experts=set()
        for trigger in matching:
            if 'call_expert' not in (trigger.get('actions') or []): continue
            condition=trigger.get('condition') or {}
            requested=condition.get('expert_agent_ids') if isinstance(condition,dict) else None
            expert_ids=requested or []
            try:
                from tenant_ai_expert_policy import policy as validate_expert_policy,helper_ids
                eligible_experts=helper_ids(validate_expert_policy(bound_primary.get('expert_policy') or {}))
                if expert_ids: eligible_experts=[x for x in eligible_experts if str(x) in {str(v) for v in expert_ids}]
            except Exception:
                eligible_experts=[]
            if len(eligible_experts)==1 and str(eligible_experts[0]) in called_experts: continue
            try:
                with _connect() as conn:
                    ensure_schema(conn)
                    with conn.cursor() as cur:
                        cur.execute('SELECT expert_policy FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,primary_id))
                        primary_cfg=cur.fetchone() or {}
                        value,expert_reason,call_no=_safe_expert(cur,job,{**bound_primary,**primary_cfg},trigger,text,lines,'',bound_primary,call_no)
                        conn.commit()
                if value:
                    called_experts.add(value['agent_id'])
                    expert_results.append({'agent_id':value['agent_id'],'result':value['result'],'tokens':value['tokens']})
                    knowledge.extend(value.get('knowledge') or [])
                    advisory.append(value['result'])
                    total_tokens+=int(value['tokens'])
                    events.append({'type':'expert_completed','agent_id':value['agent_id'],'tokens':value['tokens']})
                else:
                    expert_results.append({'status':'unavailable','reason':expert_reason})
                    events.append({'type':'expert_unavailable','reason':expert_reason})
            except Exception:
                expert_results.append({'status':'unavailable','reason':'expert_adapter_unavailable'})
                events.append({'type':'expert_unavailable','reason':'expert_adapter_unavailable'})
        if response_policy.get('enabled') is True and not knowledge:
            from tenant_ai_agent_no_answer import selected_action
            policy=(dict(response_policy) if response_policy.get('action')!='call_expert' else
                {**response_policy,'action':response_policy.get('fallback_action','stop')})
            decision=selected_action(policy,'expert_failed' if response_policy.get('action')=='call_expert' else 'missing_knowledge')
            action=decision['action'];reply=''
            events.append({'type':'virtual_no_answer_action','action':action,'reason':decision['reason'],'executed':False})
            if action=='handoff': events.append({'type':'virtual_handoff','reason':'no_answer','executed':False})
            elif action=='create_task': events.append({'type':'virtual_employee_request','reason':'no_answer','executed':False})
            elif action=='clarify':
                reply={'ru':'Уточните, пожалуйста, о каком товаре или заказе идёт речь и что именно нужно проверить.',
                    'en':'Could you share which product or order this concerns and what you need us to check?',
                    'tr':'Lütfen hangi ürün veya sipariş hakkında olduğunu ve neyi kontrol etmemizi istediğinizi belirtin.',
                    'az':'Zəhmət olmasa, hansı məhsul və ya sifariş barədə olduğunu və nəyi dəqiqləşdirməli olduğumuzu yazın.'}.get(str(bound_primary.get('language') or 'auto'),
                    'Zəhmət olmasa, hansı məhsul və ya sifariş barədə olduğunu və nəyi dəqiqləşdirməli olduğumuzu yazın.')
            elif action=='stop': reply=str(response_policy.get('stop_reply') or '')
            _complete(job,'virtual_reply' if reply else 'virtual_stop',reply=reply,
                stop_reason='no_answer_'+action,agent_id=primary_id,triggers=triggers,knowledge=knowledge,
                experts=expert_results,events=events,total_tokens=total_tokens)
            return True
        prompt=('You are simulating the configured Primary for one new incoming message. '
          'Return only a concise customer reply draft. Never claim any message was sent, CRM action performed, '
          'or handoff executed. Do not invent facts. If the evidence is insufficient, ask for clarification. '
          'Respect company and agent instructions, channel scope, safety policy, and schedule.\n'
          'Company instructions: '+str(bound_primary.get('company_instructions') or '')[:5000]+'\n'
          'Agent instructions: '+str(bound_primary.get('instructions') or '')[:5000]+'\n'
          'Style: '+str(bound_primary.get('style') or 'balanced')+'; language: '+str(bound_primary.get('language') or 'auto')+'\n'
          'Safety: '+json.dumps(bound_primary.get('safety') or {},ensure_ascii=False)[:3000])
        if response_policy.get('enabled') is True:
            from tenant_ai_agent_no_answer import prompt_instructions
            prompt+='\n\n'+prompt_instructions()
        prior_state=bound_primary.get('prior_virtual_state') or {}
        if isinstance(prior_state,str):
            try: prior_state=json.loads(prior_state)
            except ValueError: prior_state={}
        prior_state={key:prior_state[key] for key in ('outcome','last_reply','stop_reason','sent')
                     if isinstance(prior_state,dict) and key in prior_state}
        extra={'simulation_label':'Предполагаемое поведение AI на основе фактического контекста',
               'knowledge':[{'source_id':x.get('source_id'),'title':x.get('title'),'content':x.get('content')} for x in knowledge[:6]],
               'expert_advisory':advisory,'trigger_actions':[x.get('actions') for x in triggers],
               'prior_simulation_state':prior_state,'virtual_only':True,'sent':False,'external_tools':[]}
        events.append({'type':'knowledge_retrieved','source_ids':[str(x.get('source_id')) for x in knowledge[:8]]})
        events.append({'type':'primary_generation_started','agent_id':primary_id,'call_no':call_no+1})
        call_no+=1
        # Reservation was made above with call_no=1 for Primary. Expert calls
        # use their own numbers; use the final reserved primary call index.
        primary_call=1
        with _connect() as conn:
            ensure_schema(conn)
            with conn.cursor() as cur:
                reservation_tokens=_provider_token_reservation('reply',lines,'',prompt,extra,
                    min(int(bound_primary['max_output_tokens']),1800))
                if not _adjust_reservation(cur,job,primary_call,reservation_tokens):
                    conn.commit()
                    _complete(job,'blocked',stop_reason='simulation_budget_exhausted',agent_id=primary_id,
                              triggers=triggers,knowledge=knowledge,experts=expert_results,events=events)
                    return True
                from tenant_ai_spend_budget import reserve as reserve_spend, release as release_spend, settle as settle_spend
                primary_spend_key='simulation:'+str(job['id'])+':'+str(primary_call)
                reserve_spend(cur,tenant,primary_spend_key,'simulation_primary',model,total_upper=reservation_tokens)
                if not _mark_provider_started(cur,job,primary_call):
                    cur.connection.commit()
                    release_spend(tenant,primary_spend_key)
                    _complete(job,'blocked',stop_reason='simulation_stopped_before_provider',agent_id=primary_id,
                              triggers=triggers,knowledge=knowledge,experts=expert_results,events=events)
                    return True
                cur.connection.commit()
            conn.commit()
        from tenant_chat_ai_provider import generate
        try:
            draft,usage=generate(key,model,'reply',lines,'',instructions=prompt,extra_context=extra,
                with_usage=True,max_output_tokens=min(int(bound_primary['max_output_tokens']),1800),request_timeout=45)
            if response_policy.get('enabled') is True:
                from tenant_ai_agent_no_answer import assess, selected_action
                assessment=assess(draft,knowledge)
                if assessment['status']!='supported':
                    signal='conflicting_evidence' if assessment['status']=='conflicting' else 'unsupported_evidence'
                    policy=dict(response_policy)
                    if policy.get('action')=='call_expert':
                        successful=next((item for item in reversed(expert_results) if item.get('status')=='completed'),None)
                        if not successful or not (successful.get('result') or {}).get('evidence'):
                            policy['action']=policy.get('fallback_action','stop')
                        else:
                            # The expert already ran in this Simulation event.
                            # Never recursively call it after an unsupported Primary result.
                            policy['action']=policy.get('fallback_action','stop')
                    decision=selected_action(policy,signal)
                    action=decision['action'];events.append({'type':'virtual_no_answer_action',
                        'action':action,'reason':decision['reason'],'executed':False})
                    if action=='handoff':events.append({'type':'virtual_handoff','reason':'no_answer','executed':False})
                    elif action=='create_task':events.append({'type':'virtual_employee_request','reason':'no_answer','executed':False})
                    elif action=='clarify':draft=assessment.get('clarification_question') or 'Zəhmət olmasa, əlavə məlumat verin.'
                    elif action=='stop':draft=str(response_policy.get('stop_reply') or '')
                    if action!='clarify' and action!='stop' or not draft:
                        _finish_call(job,primary_call,state='succeeded',actual=max(0,int(usage.get('total_tokens') or 0)))
                        settle_spend(tenant,primary_spend_key,usage or {},unknown=usage is None)
                        total_tokens+=max(0,int(usage.get('total_tokens') or 0))
                        _complete(job,'virtual_stop',stop_reason='no_answer_'+action,agent_id=primary_id,
                            triggers=triggers,knowledge=knowledge,experts=expert_results,events=events,total_tokens=total_tokens)
                        return True
            from tenant_ai_agent_safety import sanitize_draft
            draft=sanitize_draft(draft,bound_primary.get('language') or 'auto')
            used=max(0,int(usage.get('total_tokens') or 0));total_tokens+=used
            _finish_call(job,primary_call,state='succeeded',actual=used)
            settle_spend(tenant,primary_spend_key,usage or {},unknown=usage is None)
            events.append({'type':'primary_reply_prepared','agent_id':primary_id,'tokens':used,'sent':False})
            if any('handoff_after_reply' in (trigger.get('actions') or []) for trigger in matching):
                events.append({'type':'virtual_handoff_after_reply','reason':'configured_trigger','executed':False})
            _complete(job,'virtual_reply',reply=draft,agent_id=primary_id,triggers=triggers,knowledge=knowledge,
                      experts=expert_results,events=events,total_tokens=total_tokens)
        except Exception:
            _finish_call(job,primary_call,state='unknown')
            settle_spend(tenant,primary_spend_key,{},unknown=True)
            _complete(job,'unknown',stop_reason='provider_result_unknown',agent_id=primary_id,triggers=triggers,
                      knowledge=knowledge,experts=expert_results,events=events+[{'type':'provider_result_unknown'}],
                      total_tokens=total_tokens,error='provider_result_unknown')
        return True
    except TenantLinearError as exc:
        code=str(exc).split(':',1)[0][:80] or 'simulation_blocked'
        _complete(job,'blocked',stop_reason=code,agent_id=job.get('agent_id'),events=events+[{'type':'blocked','reason':code}],error=code)
        return True
    except Exception as exc:
        _complete(job,'error',stop_reason='simulation_processing_failed',agent_id=job.get('agent_id'),
                  events=events+[{'type':'processing_failed','error_type':type(exc).__name__}],error='simulation_processing_failed')
        return True


def list_runs(profile, *, limit=50):
    safe_limit=max(1,min(100,int(limit or 50)))
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,_actor=_authorize(cur,profile)
            cur.execute('''SELECT r.id,r.queue_id,r.channel_id,r.conversation_id,r.primary_agent_id,r.outcome,
              r.virtual_reply,r.virtual_stop_reason,r.virtual_triggers,r.knowledge_refs,r.expert_results,
              r.events,r.total_tokens,r.created_at,q.recheck_of_run_id FROM saas_ai_simulation_runs r
              JOIN saas_ai_simulation_queue q ON q.tenant_id=r.tenant_id AND q.id=r.queue_id
              WHERE r.tenant_id=%s::uuid ORDER BY r.created_at DESC LIMIT %s''',(tenant,safe_limit))
            rows=[]
            for row in cur.fetchall():
                rows.append(_public_run(row))
            return rows


def _public_run(row):
    item=dict(row)
    item['created_at']=_iso(item.get('created_at'));item['sent']=False
    for key in ('id','queue_id','channel_id','conversation_id','primary_agent_id','recheck_of_run_id'):
        if item.get(key) is not None: item[key]=str(item[key])
    return item
