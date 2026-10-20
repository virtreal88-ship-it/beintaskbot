"""Platform-admin-only, inert preparation and diagnostics for the live case pilot.

The prepared scope is deliberately separate from worker runtime configuration:
this API cannot start extraction, change Railway variables, or touch queue rows.
"""
from datetime import datetime, timezone
import uuid

from aiohttp import web
from tenant_platform import _connect
from tenant_linear_policy import TenantLinearError
from tenant_ai_agents_api import _origin
from tenant_knowledge_candidates_policy import MAX_DAILY_EXTRACTIONS, MAX_TOKENS


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS saas_support_case_pilot_plan(
          id SMALLINT PRIMARY KEY CHECK(id=1), tenant_id UUID NOT NULL, channel_id UUID NOT NULL,
          conversation_id TEXT NOT NULL, start_after TIMESTAMPTZ NOT NULL,
          updated_by BIGINT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")


def _validate_scope(data):
    if not isinstance(data, dict) or set(data) != {'tenant_id','channel_id','conversation_id','start_after'}:
        raise TenantLinearError('Göstərilən pilot sahəsi tam olmalıdır.')
    try:
        tenant = str(uuid.UUID(str(data['tenant_id'])))
        channel = str(uuid.UUID(str(data['channel_id'])))
        start = datetime.fromisoformat(str(data['start_after']).replace('Z','+00:00'))
    except (ValueError, TypeError):
        raise TenantLinearError('Tenant, kanal və START_AFTER dəqiq təsdiqlənməlidir.')
    conversation = data['conversation_id']
    if not isinstance(conversation,str) or not conversation.strip() or len(conversation)>200 or any(ord(c)<32 for c in conversation):
        raise TenantLinearError('Dəqiq dialoq ID-si tələb olunur.')
    if start.tzinfo is None or start <= datetime.now(timezone.utc):
        raise TenantLinearError('START_AFTER gələcək tarix və saat olmalıdır; köhnə backlog qəbul edilmir.')
    return tenant,channel,conversation,start.astimezone(timezone.utc)


def readiness(scope, *, route_verified, start_future, queue, queue_verified,
              global_switch='not_confirmed', manual_approval=True, limits=None):
    """Conservative, side-effect-free decision; unknowns are blockers."""
    reasons=[]
    if not scope: reasons.append('Pilot scope saxlanmayıb.')
    elif not route_verified: reasons.append('Tenant, kanal və dialoq uyğunluğu təsdiqlənmədi.')
    if scope and not start_future: reasons.append('START_AFTER gələcək tarix deyil; köhnə tarixçə istisna edilmir.')
    if not queue_verified: reasons.append('Queue vəziyyətini oxumaq mümkün olmadı.')
    elif queue.get('running') or queue.get('eligible_pending') or queue.get('invalid_source') or queue.get('old_pending'):
        reasons.append('Queue-də işləyən, vaxt sərhədinə düşən, köhnə sərhədöncəsi və ya mənbə mesajı təsdiqlənməyən hadisə var.')
    if global_switch!='off':
        if global_switch=='on': reasons.append('Qlobal çıxarma açarı aktivdir; ayrıca icazə olmadan işə salmaq qadağandır.')
        else: reasons.append('Qlobal SUPPORT_CASES_LIVE_EXTRACTION_ENABLED vəziyyəti təsdiqlənməyib; bu, icazə sayılmır.')
    if not manual_approval: reasons.append('Manual təsdiq axını təsdiqlənmədi.')
    limits=limits or {}
    if not limits.get('pilot_caps_enforced'):
        reasons.append('Pilot üçün ayrıca max_calls, max_tokens və pul xərci limiti konfiqurasiya olunmayıb/tətbiq edilmir.')
    if limits.get('cost_cap') is None:
        reasons.append('Real xərc limiti və xərc ölçümü mövcud deyil.')
    return {'status':'ready_to_configure' if not reasons else 'not_ready','reasons':reasons,
            'manual_approval_required':True,'launch_enabled':False}


def snapshot(conn):
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT tenant_id::text,channel_id::text,conversation_id,start_after,updated_at FROM saas_support_case_pilot_plan WHERE id=1")
        row=cur.fetchone()
        result={'capability':'available_in_code','worker_deployment':'not_confirmed','railway_status':'unavailable',
                'global_switch':'not_confirmed','global_switch_note':'Railway worker konfiqurasiyası bu interfeysdən təsdiqlənmir.',
                'pilot_scope':None,'scope_verified':False,'queue':{'pending':None,'failed':None},
                'ai_requests':None,'tokens':None,'cost':None,'limits':{'daily_extraction_max':MAX_DAILY_EXTRACTIONS,'max_tokens_per_request':MAX_TOKENS,
                  'pilot_caps_enforced':False,'cost_cap':None},'preflight':None,'launch_enabled':False,'manual_approval_required':True}
        if not row:
            result['preflight']=readiness(None,route_verified=False,start_future=False,queue={},queue_verified=False,
              manual_approval=True,limits=result['limits'])
            return result
        scope={'tenant_id':str(row['tenant_id']),'channel_id':str(row['channel_id']),
               'conversation_id':row['conversation_id'],'start_after':row['start_after'].isoformat()}
        result['start_after_future']=row['start_after']>datetime.now(timezone.utc)
        cur.execute("""SELECT 1 FROM saas_connect_conversations c JOIN saas_connect_channels ch
          ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
          WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid AND c.conversation_id=%s AND ch.active=TRUE""",
          (scope['tenant_id'],scope['channel_id'],scope['conversation_id']))
        result['scope_verified']=bool(cur.fetchone())
        cur.execute("SELECT metadata FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'",
          (scope['tenant_id'],))
        integration=cur.fetchone() or {}
        metadata=integration.get('metadata') or {}
        try: result['limits']['company_daily_ai_max']=int(metadata.get('daily_limit') or 50)
        except (ValueError,TypeError): result['limits']['company_daily_ai_max']=None
        queue_state={'pending':None,'failed':None,'running':None,'eligible_pending':None,'invalid_source':None,'old_pending':None}
        queue_verified=False
        if result['scope_verified']:
            cur.execute("SELECT to_regclass('saas_knowledge_live_queue') AS table_name")
            if cur.fetchone()['table_name']:
                cur.execute("""SELECT count(*) FILTER(WHERE q.state='pending') AS pending,
                  count(*) FILTER(WHERE q.state='blocked') AS failed,
                  count(*) FILTER(WHERE q.state='running') AS running,
                  count(*) FILTER(WHERE q.state='pending' AND m.happened_at >= %s::timestamptz) AS eligible_pending,
                  count(*) FILTER(WHERE q.state='pending' AND (m.id IS NULL OR m.happened_at IS NULL OR m.direction NOT IN ('incoming','outgoing'))) AS invalid_source,
                  count(*) FILTER(WHERE q.state='pending' AND m.id IS NOT NULL AND m.happened_at < %s::timestamptz) AS old_pending
                  FROM saas_knowledge_live_queue q LEFT JOIN saas_crm_messages m
                    ON m.tenant_id=q.tenant_id AND m.id=q.last_message_id AND m.kommo_lead_id=q.lead_id
                    AND m.channel=('connect:' || q.channel_id::text)
                  WHERE q.tenant_id=%s::uuid AND q.channel_id=%s::uuid AND q.conversation_id=%s""",
                  (scope['start_after'],scope['start_after'],scope['tenant_id'],scope['channel_id'],scope['conversation_id']))
                counts=cur.fetchone() or {}
                queue_state={key:int(counts.get(key) or 0) for key in queue_state}
                result['queue']=queue_state
                queue_verified=True
            cur.execute("SELECT to_regclass('saas_knowledge_extractions') AS table_name")
            if cur.fetchone()['table_name']:
                cur.execute("SELECT to_regclass('saas_ai_agent_token_usage') AS table_name")
                if cur.fetchone()['table_name']:
                    cur.execute("""SELECT count(DISTINCT e.id) AS n,COALESCE(sum(u.total_tokens),0) AS tokens
                      FROM saas_knowledge_extractions e LEFT JOIN saas_ai_agent_token_usage u
                        ON u.tenant_id=e.tenant_id AND u.run_id=e.id
                      WHERE e.tenant_id=%s::uuid AND e.route->>'channel_id'=%s AND e.route->>'conversation_id'=%s""",
                      (scope['tenant_id'],scope['channel_id'],scope['conversation_id']))
                else:
                    cur.execute("""SELECT count(*) AS n FROM saas_knowledge_extractions e
                      WHERE e.tenant_id=%s::uuid AND e.route->>'channel_id'=%s AND e.route->>'conversation_id'=%s""",
                      (scope['tenant_id'],scope['channel_id'],scope['conversation_id']))
                usage=cur.fetchone() or {}
                result['ai_requests']=int(usage.get('n') or 0)
                result['tokens']=int(usage.get('tokens') or 0) if 'tokens' in usage else None
        result['pilot_scope']=scope
        result['preflight']=readiness(scope,route_verified=result['scope_verified'],
          start_future=bool(result.get('start_after_future')),queue=queue_state,queue_verified=queue_verified,
          global_switch=result['global_switch'],manual_approval=result['manual_approval_required'],limits=result['limits'])
        return result


def handler(member_from_request, is_platform_admin, canonical_origin, logger):
    async def handle(request):
        headers={'Cache-Control':'no-store'}
        try:
            profile=member_from_request(request)
            if not profile or not is_platform_admin(profile):
                raise TenantLinearError('Yalnız platform administratoru üçün əlçatandır.',403)
            if request.method=='POST':
                _origin(request,canonical_origin)
                if request.content_type!='application/json': raise TenantLinearError('JSON tələb olunur.')
                scope=_validate_scope(await request.json())
                with _connect() as conn:
                    ensure_schema(conn)
                    with conn.cursor() as cur:
                        cur.execute("""SELECT 1 FROM saas_connect_conversations c JOIN saas_connect_channels ch
                          ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
                          WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid AND c.conversation_id=%s AND ch.active=TRUE""",scope[:3])
                        if not cur.fetchone(): raise TenantLinearError('Tenant/kanal/dialoq əlaqəsi təsdiqlənmədi.')
                        cur.execute("""INSERT INTO saas_support_case_pilot_plan(id,tenant_id,channel_id,conversation_id,start_after,updated_by,updated_at)
                          VALUES(1,%s::uuid,%s::uuid,%s,%s,%s,now()) ON CONFLICT(id) DO UPDATE SET tenant_id=EXCLUDED.tenant_id,
                          channel_id=EXCLUDED.channel_id,conversation_id=EXCLUDED.conversation_id,start_after=EXCLUDED.start_after,
                          updated_by=EXCLUDED.updated_by,updated_at=now()""",(*scope,int(profile['telegram_id'])))
                    conn.commit()
            with _connect() as conn:
                result=snapshot(conn)
            return web.json_response({'success':True,'result':result},headers=headers)
        except TenantLinearError as exc:
            return web.json_response({'success':False,'error':str(exc)},status=exc.status,headers=headers)
        except (ValueError,TypeError,KeyError):
            return web.json_response({'success':False,'error':'Düzgün olmayan parametrlər.'},status=400,headers=headers)
        except Exception:
            logger.exception('Support case pilot diagnostics unavailable')
            return web.json_response({'success':False,'error':'Pilot məlumatları hazırda əlçatan deyil.'},status=503,headers=headers)
    return handle
