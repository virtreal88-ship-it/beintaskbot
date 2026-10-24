"""Owner-only review surface for Autonomous Administrator proposals.

This API deliberately exposes approvals, not a generic write proxy.  Applying
is still fail-closed behind AI_AUTONOMOUS_APPLY_ENABLED and only supports the
versioned prompt patch allowlisted by tenant_ai_administrator_apply.
"""
import asyncio

from aiohttp import web

from tenant_ai_dashboard import _connect
from tenant_linear_policy import TenantLinearError
import tenant_ai_administrator_approvals as approvals
import tenant_ai_administrator_apply as application


def _origin(request, canonical_origin):
    if request.method != 'GET' and request.headers.get('Origin','').rstrip('/') != canonical_origin.rstrip('/'):
        raise TenantLinearError('Sorğunun mənbəyi düzgün deyil.',403)


def _with_connection(fn, *args, **kwargs):
    with _connect() as conn:
        try:
            with conn.cursor() as cur:
                result=fn(cur,*args,**kwargs)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise


def handler(member_from_request, canonical_origin, logger):
    async def handle(request):
        try:
            profile=member_from_request(request)
            if not profile: raise TenantLinearError('İcazə yoxdur.',403)
            _origin(request,canonical_origin)
            if request.method=='GET' and set(request.query)=={'review_run_id'}:
                value=await asyncio.to_thread(_with_connection,review_context,profile,request.query['review_run_id'])
            elif request.method=='GET':
                raw=request.query.get('limit','50')
                try: limit=int(raw)
                except ValueError: raise TenantLinearError('Invalid approval list.',400) from None
                value=await asyncio.to_thread(_with_connection,approvals.listing,profile,limit=limit)
            else:
                if request.content_type != 'application/json': raise TenantLinearError('JSON sorğusu tələb olunur.',415)
                data=await request.json()
                if not isinstance(data,dict): raise TenantLinearError('Invalid approval request.',400)
                action=data.get('action')
                if action=='prepare_review_solution':
                    value=await asyncio.to_thread(_with_connection,prepare_review_solution,profile,data)
                elif action=='request':
                    if set(data)!={'action','draft_id','version'}: raise TenantLinearError('Invalid approval request.',400)
                    value=await asyncio.to_thread(_with_connection,approvals.request,profile,data['draft_id'],data['version'])
                elif action in {'approve','reject'}:
                    if set(data)!={'action','approval_id'}: raise TenantLinearError('Invalid approval request.',400)
                    value=await asyncio.to_thread(_with_connection,approvals.approve,profile,data['approval_id'],action)
                elif action=='approve_batch':
                    if set(data)!={'action','approval_ids'}: raise TenantLinearError('Invalid approval batch.',400)
                    value=await asyncio.to_thread(_with_connection,approvals.approve_many,profile,data['approval_ids'])
                elif action in {'apply_knowledge_draft','apply_instruction_draft'}:
                    if set(data)!={'action','approval_id','draft_id'}: raise TenantLinearError('Invalid approval request.',400)
                    fn=application.apply_knowledge_draft if action=='apply_knowledge_draft' else application.apply_instruction_draft
                    value=await asyncio.to_thread(_with_connection,fn,profile,data['approval_id'],data['draft_id'])
                elif action=='apply_agent_prompt':
                    if set(data)!={'action','approval_id','draft_id'}: raise TenantLinearError('Invalid approval request.',400)
                    value=await asyncio.to_thread(_with_connection,application.apply_agent_prompt,profile,data['approval_id'],data['draft_id'])
                elif action=='rollback_agent_prompt':
                    if set(data)!={'action','approval_id'}: raise TenantLinearError('Invalid approval request.',400)
                    value=await asyncio.to_thread(_with_connection,application.rollback_agent_prompt,profile,data['approval_id'])
                else: raise TenantLinearError('Invalid approval action.',400)
            return web.json_response({'success':True,'result':value},headers={'Cache-Control':'no-store'})
        except TenantLinearError as exc:
            return web.json_response({'success':False,'error':str(exc)},status=getattr(exc,'status',400),headers={'Cache-Control':'no-store'})
        except Exception:
            logger.exception('AI administrator approval API failed')
            return web.json_response({'success':False,'error':'Approval service is unavailable.'},status=503,headers={'Cache-Control':'no-store'})
    return handle


def review_context(cur,profile,run_id):
    """Owner review only; later conversation is comparison data, never AI input."""
    import uuid
    from tenant_operator_access import access,assert_route
    from tenant_operator_dialogs import _message
    from tenant_ai_learning import findings
    tenant,_=approvals._owner(cur,profile)
    _,_,policy=access(cur,profile,'dialogs')
    run_id=str(uuid.UUID(str(run_id)))
    cur.execute('''SELECT r.*,m.kommo_lead_id AS lead_id,COALESCE(m.happened_at,m.created_at) AS trigger_at
      FROM saas_ai_simulation_runs r JOIN saas_crm_messages m ON m.tenant_id=r.tenant_id AND m.id=r.source_message_id
      WHERE r.tenant_id=%s::uuid AND r.id=%s::uuid AND m.channel='connect:'||r.channel_id::text''',(tenant,run_id))
    run=cur.fetchone()
    if not run: raise TenantLinearError('Review evidence is unavailable.',404)
    route=assert_route(cur,tenant,policy,'connect',str(run['conversation_id']),str(run['channel_id']),run['lead_id'])
    fields='id,external_id,direction,channel,author_name,body,message_type,happened_at,created_at,raw'
    cur.execute('SELECT '+fields+''' FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s
      AND COALESCE(happened_at,created_at)<%s ORDER BY COALESCE(happened_at,created_at) DESC,id DESC LIMIT 24''',(tenant,run['lead_id'],'connect:'+str(run['channel_id']),run['trigger_at']))
    earlier=list(reversed(cur.fetchall()))
    cur.execute('SELECT '+fields+''' FROM saas_crm_messages WHERE tenant_id=%s::uuid AND id=%s::uuid''',(tenant,run['source_message_id']))
    trigger=cur.fetchone()
    cur.execute('SELECT '+fields+''' FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s
      AND COALESCE(happened_at,created_at)>=%s AND id<>%s::uuid ORDER BY COALESCE(happened_at,created_at),id LIMIT 24''',(tenant,run['lead_id'],'connect:'+str(run['channel_id']),run['trigger_at'],run['source_message_id']))
    later=cur.fetchall()
    messages=[]
    for phase,rows in (('before',earlier),('trigger',[trigger]),('later',later)):
        for row in rows:
            message=_message(row);message.update(review_phase=phase,source_lead_id=run['lead_id']);messages.append(message)
    return {'run_id':run_id,'source_message_id':str(run['source_message_id']),
      'route':{'provider':'connect','lead_id':run['lead_id'],'channel_id':str(run['channel_id']),'conversation_id':str(run['conversation_id'])},
      'findings':findings(run),'virtual_reply':str(run.get('virtual_reply') or ''),'outcome':run['outcome'],
      'messages':messages,'context_snapshot_available':isinstance(run.get('context_snapshot'),list),
      'later_messages_are_comparison_only':True,'read_only':True}


def prepare_review_solution(cur,profile,data):
    import uuid
    import tenant_ai_administrator_drafts as drafts
    required={'action','run_id','explanation','kind','request_key'}
    if set(data)!=required or data.get('kind') not in {'knowledge','support_case'} or not isinstance(data.get('explanation'),str) or not 1<=len(data['explanation'].strip())<=12000:
        raise TenantLinearError('Invalid owner explanation.',400)
    tenant,actor=approvals._owner(cur,profile); request_key=str(uuid.UUID(str(data['request_key'])))
    review=review_context(cur,profile,data['run_id'])
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('learning-review:'+tenant+':'+request_key,))
    cur.execute("SELECT * FROM saas_ai_administrator_drafts WHERE tenant_id=%s::uuid AND target->>'review_request_key'=%s",(tenant,request_key))
    old=cur.fetchone()
    evidence=[review['source_message_id']]
    target={'agent_ids':[],'review_run_id':review['run_id'],'review_request_key':request_key}
    body={'content':data['explanation'].strip(),'evidence_ids':evidence} if data['kind']=='knowledge' else {'problem':data['explanation'].strip(),'evidence_message_ids':evidence}
    if old:
        if old['body']!=body or old['kind']!=data['kind'] or old['target']!=target: raise TenantLinearError('Review request changed.',409)
        return drafts.public(old)
    cfg={'AI_LEARNING_MCP_TENANT_ID':tenant,'actor_id':actor,'granted_scopes':[drafts.DRAFT_SCOPE]}
    return drafts.create(cur,cfg,{'kind':data['kind'],'title':'Owner review · '+review['run_id'][:8],'body':body,'target':target})
