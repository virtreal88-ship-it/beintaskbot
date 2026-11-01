"""Exact-route ASR metadata for the existing owner read grant; no media/content."""
import hashlib
import hmac
import json
import re
import uuid
from tenant_linear_policy import TenantLinearError,identifier
from tenant_operator_access import access,assert_route

NAME='audio_attempts_diagnostic_read'
CODES={'','provider_outcome_unknown','pre_dispatch_stopped','context_changed','publication_check_pending',
       'publication_check_unavailable','accounting_settlement_pending'}
STATES={'reserved','sending','completed','failed_safe','unknown'}


def schema():
    return {'type':'object','properties':{
      'channel_id':{'type':'string','minLength':1,'maxLength':36},
      'lead_id':{'type':'string','minLength':1,'maxLength':19,'pattern':'^[1-9][0-9]{0,18}$'},
      'limit':{'type':'integer','minimum':1,'maximum':20},
      'cursor':{'type':'string','maxLength':2048}},
      'required':['channel_id','lead_id'],'additionalProperties':False}


def _code(value):
    from tenant_chat_ai_provider import is_safe_failure_code
    return value if isinstance(value,str) and (value in CODES or is_safe_failure_code(value)) else None


def _model(value):
    if (not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',value)
        or re.search(r'(?i)^(sk-|eyJ)|password|secret|token|bearer|authorization|api.?key',value)):
        return None
    return value


def read(cur,cfg,args):
    from tenant_ai_administrator_read import _page_key,_timestamp
    if (not isinstance(args,dict) or not {'channel_id','lead_id'}<=set(args)
        or set(args)-{'channel_id','lead_id','limit','cursor'}):
        raise TenantLinearError('Channel and lead are required for audio diagnostics.',400)
    channel=identifier(args['channel_id'])
    if not isinstance(args['lead_id'],str) or not re.fullmatch(r'[1-9][0-9]{0,18}',args['lead_id']):
        raise TenantLinearError('Invalid diagnostic lead.',400)
    lead=int(args['lead_id']);limit=args.get('limit',20)
    if lead>=2**63 or type(limit) is not int or not 1<=limit<=20:
        raise TenantLinearError('Invalid diagnostic page.',400)
    if 'cursor' in args and (not isinstance(args['cursor'],str) or not args['cursor'] or len(args['cursor'])>2048):
        raise TenantLinearError('Invalid diagnostic cursor.',400)
    profile={'tenant_id':cfg['AI_LEARNING_MCP_TENANT_ID'],'telegram_id':cfg.get('actor_id')}
    tenant,actor,policy=access(cur,profile,'dialogs')
    if policy.profile.get('role')!='owner' or not policy.can_use_chat_ai():
        raise TenantLinearError('Current company owner Chat AI access required.',403)
    if channel!=str(cfg.get('AI_LEARNING_MCP_CHANNEL_ID') or ''):
        raise TenantLinearError('Route is outside the OAuth channel scope.',403)
    chosen=assert_route(cur,tenant,policy,'connect',channel=channel,lead_id=lead)
    from tenant_operator_dialogs import projection
    from tenant_ai_agent_runtime_flags import enabled
    sql,params=projection(policy)
    cur.execute(sql+"SELECT 1 AS visible FROM visible WHERE provider='connect' AND channel_id=%s AND conversation_id=%s AND lead_id=%s AND channel_active=TRUE",
      (tenant,tenant,*params,enabled('AI_AGENTS_ENABLED'),channel,chosen['conversation_id'],lead))
    if not cur.fetchone():raise TenantLinearError('Dialog is unavailable.',404)
    cur.execute('SELECT scopes FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR SHARE',
      (tenant,cfg['AI_LEARNING_MCP_AGENT_ID']))
    agent=cur.fetchone()
    from tenant_ai_agent_connect import scope_matches
    from tenant_ai_group_coordination import group_jid
    if not agent or not scope_matches(agent['scopes'],channel,is_group=bool(group_jid(chosen['conversation_id']))):
        raise TenantLinearError('Route is outside the current agent scope.',403)
    bound={k:v for k,v in args.items() if k not in {'cursor','limit'}}
    bound['_scope']=hashlib.sha256(json.dumps([tenant,actor,cfg['AI_LEARNING_MCP_AGENT_ID'],channel]).encode()).hexdigest()
    offset=0
    if args.get('cursor'):
        try:
            if not isinstance(args['cursor'],str) or len(args['cursor'])>2048:raise ValueError()
            body,_=args['cursor'].rsplit('.',1);offset=json.loads(body)[2]
            if type(offset) is not int or not 0<=offset<=100000 or not hmac.compare_digest(_page_key(cfg,NAME,bound,offset),args['cursor']):raise ValueError()
        except (ValueError,TypeError,IndexError,KeyError):
            raise TenantLinearError('Invalid diagnostic cursor.',400) from None
    cur.execute("SELECT to_regclass('public.saas_dialog_audio_attempts') AS relation")
    available=bool((cur.fetchone() or {}).get('relation'))
    fields=('a.id AS attempt_id,a.state,a.code,a.model,a.published,a.created_at AS attempt_created_at,a.dispatched_at,a.completed_at'
            if available else 'NULL::uuid AS attempt_id,NULL::text AS state,NULL::text AS code,NULL::text AS model,NULL::boolean AS published,NULL::timestamptz AS attempt_created_at,NULL::timestamptz AS dispatched_at,NULL::timestamptz AS completed_at')
    join=(' LEFT JOIN saas_dialog_audio_attempts a ON a.tenant_id=m.tenant_id AND a.message_row_id=m.id'
          ' AND a.channel_id=%s::uuid AND a.external_id=m.external_id' if available else '')
    ordering='COALESCE(a.created_at,m.happened_at,m.created_at) DESC,m.id DESC,a.id DESC' if available else 'COALESCE(m.happened_at,m.created_at) DESC,m.id DESC'
    cur.execute('''SELECT m.id AS message_row_id,m.external_id,m.direction,m.message_type,
      m.happened_at,m.created_at AS source_created_at,'''+fields+''' FROM saas_crm_messages m'''+join+'''
      WHERE m.tenant_id=%s::uuid AND m.channel=%s AND m.kommo_lead_id=%s
        AND m.message_type IN ('audio','voice','voice_message','ptt') ORDER BY '''+ordering+' LIMIT %s OFFSET %s',
      (*((channel,) if available else ()),tenant,'connect:'+channel,lead,limit+1,offset))
    rows=cur.fetchall();items=[]
    for row in rows[:limit]:
        external=row['external_id'];external_id=None
        try:
            if isinstance(external,str) and external.startswith('connect:'):external_id='connect:'+str(uuid.UUID(external[8:]))
        except (ValueError,TypeError,AttributeError):pass
        attempt=None
        if row['attempt_id']:
            attempt={'id':str(row['attempt_id']),'state':row['state'] if row['state'] in STATES else None,
              'code':_code(row['code']),'model':_model(row['model']),'published':row['published'] is True,
              'created_at':_timestamp(row['attempt_created_at']),'dispatched_at':_timestamp(row['dispatched_at']),
              'completed_at':_timestamp(row['completed_at'])}
        items.append({'message_row_id':str(row['message_row_id']),'message_id':external_id,
          'direction':row['direction'] if row['direction'] in {'incoming','outgoing'} else None,
          'message_type':row['message_type'],'happened_at':_timestamp(row['happened_at']),
          'created_at':_timestamp(row['source_created_at']),'attempt_status':'attempts_present' if attempt else 'no_attempt',
          'attempt':attempt,'source_current':None,'source_current_reason':'metadata_only'})
    return {'read_only':True,'metadata_only':True,'attempt_records_available':available,
      'scope':{'provider':'connect','channel_id':channel,'lead_id':lead},'items':items,
      'next_cursor':_page_key(cfg,NAME,bound,offset+limit) if len(rows)>limit else None,
      'coverage':'manual_attempts_only'}
