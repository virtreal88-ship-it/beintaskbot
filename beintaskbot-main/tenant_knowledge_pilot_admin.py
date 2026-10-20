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


def snapshot(conn):
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT tenant_id::text,channel_id::text,conversation_id,start_after,updated_at FROM saas_support_case_pilot_plan WHERE id=1")
        row=cur.fetchone()
        result={'capability':'available','global_switch':'not_confirmed','global_switch_note':'Railway worker konfiqurasiyası bu interfeysdən təsdiqlənmir.',
                'pilot_scope':None,'scope_verified':False,'queue':{'pending':None,'failed':None},
                'ai_requests':None,'tokens':None,'cost':None,'pilot_ready':False,'launch_enabled':False,'manual_approval_required':True}
        if not row:
            return result
        scope={'tenant_id':str(row['tenant_id']),'channel_id':str(row['channel_id']),
               'conversation_id':row['conversation_id'],'start_after':row['start_after'].isoformat()}
        result['start_after_future']=row['start_after']>datetime.now(timezone.utc)
        cur.execute("""SELECT 1 FROM saas_connect_conversations c JOIN saas_connect_channels ch
          ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
          WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid AND c.conversation_id=%s AND ch.active=TRUE""",
          (scope['tenant_id'],scope['channel_id'],scope['conversation_id']))
        result['scope_verified']=bool(cur.fetchone())
        if result['scope_verified']:
            cur.execute("SELECT to_regclass('saas_knowledge_live_queue') AS table_name")
            if cur.fetchone()['table_name']:
                cur.execute("""SELECT count(*) FILTER(WHERE state='pending') AS pending,
                  count(*) FILTER(WHERE state='blocked') AS failed FROM saas_knowledge_live_queue
                  WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s""",
                  (scope['tenant_id'],scope['channel_id'],scope['conversation_id']))
                counts=cur.fetchone() or {}
                result['queue']={'pending':int(counts.get('pending') or 0),'failed':int(counts.get('failed') or 0)}
            cur.execute("SELECT to_regclass('saas_knowledge_extractions') AS table_name")
            if cur.fetchone()['table_name']:
                cur.execute("""SELECT count(DISTINCT e.id) AS n,COALESCE(sum(u.total_tokens),0) AS tokens
                  FROM saas_knowledge_extractions e LEFT JOIN saas_ai_agent_token_usage u
                    ON u.tenant_id=e.tenant_id AND u.run_id=e.id
                  WHERE e.tenant_id=%s::uuid AND e.route->>'channel_id'=%s AND e.route->>'conversation_id'=%s""",
                  (scope['tenant_id'],scope['channel_id'],scope['conversation_id']))
                usage=cur.fetchone() or {}
                result['ai_requests']=int(usage.get('n') or 0)
                result['tokens']=int(usage.get('tokens') or 0)
        result['pilot_scope']=scope
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
