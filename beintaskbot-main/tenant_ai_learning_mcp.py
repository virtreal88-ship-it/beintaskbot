"""Read-only remote MCP view of a deliberately narrow AI Learning Loop scope.

This module never calls a model or exposes CRM/Connect credentials.  It is
disabled unless the Railway owner supplies all MCP_* variables.  OAuth access
tokens are opaque, hashed at rest, short lived and may be revoked.
"""
import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import uuid

from aiohttp import web

from tenant_ai_agents_store import _audit, _authorize, knowledge_deduplication
from tenant_ai_dashboard import _connect, _iso, _json_value
from tenant_ai_learning import findings, reply_quality
from tenant_ai_simulation_schema import ensure_schema
from tenant_ai_simulation_engine import context_author_type
from tenant_linear_policy import TenantLinearError
import tenant_ai_administrator_read as administrator_read
import tenant_ai_administrator_drafts as administrator_drafts
import tenant_ai_administrator_approvals as administrator_approvals
import tenant_ai_administrator_observations as administrator_observations
import tenant_ai_administrator_apply as administrator_apply

TOOL_SCOPE = 'ai_learning.read'
DRAFT_SCOPE = administrator_drafts.DRAFT_SCOPE
KNOWLEDGE_DEDUP_SCOPE = 'ai_learning.knowledge_dedup'
OFFLINE_SCOPE = 'offline_access'
MAX_PAGE = 25
CHATGPT_CLIENT_ID = 'https://chatgpt.com/oauth/client.json'
CHATGPT_REDIRECT_URI = 'https://chatgpt.com/connector_platform_oauth_redirect'


def _cfg():
    keys = ('AI_LEARNING_MCP_TENANT_ID', 'AI_LEARNING_MCP_AGENT_ID',
            'AI_LEARNING_MCP_CHANNEL_ID', 'AI_LEARNING_MCP_CURSOR_SECRET')
    value = {key: str(os.environ.get(key) or '').strip() for key in keys}
    value['enabled'] = str(os.environ.get('AI_LEARNING_MCP_ENABLED') or '').lower() == 'true'
    value['issuer'] = str(os.environ.get('AI_LEARNING_MCP_ISSUER') or '').rstrip('/')
    return value


def _require_cfg():
    cfg = _cfg()
    if not cfg['enabled'] or not cfg['issuer'] or any(not cfg[key] for key in cfg if key.endswith(('_ID', '_SECRET'))):
        raise TenantLinearError('AI Learning MCP is not enabled.', 503)
    try:
        for key in ('AI_LEARNING_MCP_TENANT_ID', 'AI_LEARNING_MCP_AGENT_ID', 'AI_LEARNING_MCP_CHANNEL_ID'):
            uuid.UUID(cfg[key])
    except ValueError as exc:
        raise TenantLinearError('AI Learning MCP scope is invalid.', 503) from exc
    return cfg


def ensure_mcp_schema(conn):
    ensure_schema(conn)
    administrator_drafts.ensure_schema(conn)
    administrator_approvals.ensure_schema(conn)
    administrator_observations.ensure_schema(conn)
    administrator_apply.ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_learning_mcp_tokens(
          id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          token_hash TEXT NOT NULL UNIQUE, agent_id UUID NOT NULL, channel_id UUID NOT NULL,
          client_id TEXT NOT NULL, expires_at TIMESTAMPTZ NOT NULL, revoked_at TIMESTAMPTZ NULL,
          refresh_token_hash TEXT NULL UNIQUE, refresh_expires_at TIMESTAMPTZ NULL,
          scope TEXT NOT NULL DEFAULT 'ai_learning.read',
          requests_in_window INTEGER NOT NULL DEFAULT 0, window_started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_learning_mcp_codes(
          code_hash TEXT PRIMARY KEY, tenant_id UUID NOT NULL, agent_id UUID NOT NULL, channel_id UUID NOT NULL,
          client_id TEXT NOT NULL, redirect_uri TEXT NOT NULL, code_challenge TEXT NOT NULL,
          scope TEXT NOT NULL DEFAULT 'ai_learning.read',
          actor_id BIGINT NOT NULL, expires_at TIMESTAMPTZ NOT NULL, used_at TIMESTAMPTZ NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        cur.execute('CREATE INDEX IF NOT EXISTS saas_ai_learning_mcp_active_idx ON saas_ai_learning_mcp_tokens(tenant_id,expires_at) WHERE revoked_at IS NULL')
        # Existing installations created the two tables before refresh support.
        # These additive columns preserve all opaque access tokens and codes.
        cur.execute('ALTER TABLE saas_ai_learning_mcp_tokens ADD COLUMN IF NOT EXISTS refresh_token_hash TEXT NULL UNIQUE')
        cur.execute('ALTER TABLE saas_ai_learning_mcp_tokens ADD COLUMN IF NOT EXISTS refresh_expires_at TIMESTAMPTZ NULL')
        cur.execute("ALTER TABLE saas_ai_learning_mcp_tokens ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'ai_learning.read'")
        cur.execute("ALTER TABLE saas_ai_learning_mcp_codes ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'ai_learning.read'")


def _hash(value): return hashlib.sha256(str(value).encode()).hexdigest()
def _b64(value): return base64.urlsafe_b64encode(value).decode().rstrip('=')


def _cursor(cfg, created_at, run_id):
    body = _b64(json.dumps([_iso(created_at), str(run_id)], separators=(',', ':')).encode())
    sig = _b64(hmac.new(cfg['AI_LEARNING_MCP_CURSOR_SECRET'].encode(), body.encode(), hashlib.sha256).digest())
    return body + '.' + sig


def _parse_cursor(cfg, value):
    if not value: return None
    try:
        body, sig = value.split('.', 1)
        expected = _b64(hmac.new(cfg['AI_LEARNING_MCP_CURSOR_SECRET'].encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected): raise ValueError
        return json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
    except (ValueError, TypeError, json.JSONDecodeError):
        raise TenantLinearError('Invalid cursor.', 400) from None


def _safe_reason(value):
    return value if isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,99}', value) else 'unclassified_stop'


def _redact(value):
    value = str(value or '')[:600]
    value = re.sub(r'(?i)\b(?:api[_ -]?key|token|password|secret)\s*[:=]\s*\S+', '[redacted]', value)
    value = re.sub(r'\+?\d[\d() .-]{7,}\d', '[phone]', value)
    value = re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', '[email]', value)
    return value


def _public_context_line(value, *, conversation_id=''):
    """Return redacted context while preserving its model-time attribution."""
    author_type=str(value.get('author_type') or '')
    if author_type not in {'customer','participant','operator'}:
        author_type=context_author_type(value,conversation_id)
    return {'role':author_type,'at':_iso(value.get('at') or value.get('happened_at') or value.get('created_at')),
            'text':_redact(value.get('text') if 'text' in value else value.get('body')),
            'attachment':value.get('message_type') not in {'','text',None}}


def _runs(cur, cfg, cursor='', limit=10, include_context=False):
    marker = _parse_cursor(cfg, cursor)
    safe = max(1, min(MAX_PAGE, int(limit or 10)))
    where = '''r.tenant_id=%s::uuid AND r.primary_agent_id=%s::uuid AND r.channel_id=%s::uuid'''
    params = [cfg['AI_LEARNING_MCP_TENANT_ID'], cfg['AI_LEARNING_MCP_AGENT_ID'], cfg['AI_LEARNING_MCP_CHANNEL_ID']]
    if marker:
        where += " AND (r.created_at,r.id) < (%s::timestamptz,%s::uuid)"
        params += [marker[0], marker[1]]
    cur.execute('''SELECT r.id,r.created_at,r.outcome,r.virtual_stop_reason,r.virtual_reply,r.virtual_triggers,
        r.knowledge_refs,r.expert_results,r.events,r.context_snapshot,r.total_tokens,q.lead_id,q.conversation_id,r.source_message_id
      FROM saas_ai_simulation_runs r JOIN saas_ai_simulation_queue q ON q.tenant_id=r.tenant_id AND q.id=r.queue_id
      WHERE ''' + where + ' ORDER BY r.created_at DESC,r.id DESC LIMIT %s', (*params, safe + 1))
    rows = list(cur.fetchall()); page = rows[:safe]
    items=[]
    for row in page:
        item={'run_id':str(row['id']),'at':_iso(row['created_at']),'agent_id':cfg['AI_LEARNING_MCP_AGENT_ID'],
              'channel_id':cfg['AI_LEARNING_MCP_CHANNEL_ID'],'outcome':row['outcome'],
              'stop_reason':_safe_reason(row.get('virtual_stop_reason')),
              'virtual_reply':_redact(row.get('virtual_reply')),
              'triggers':_json_value(row.get('virtual_triggers'),[]),
              'knowledge':_json_value(row.get('knowledge_refs'),[]),
              'expert_calls':_json_value(row.get('expert_results'),[]),
              'events':[{'type':str(x.get('type') or '')[:80],'reason':_safe_reason(x.get('reason'))}
                        for x in _json_value(row.get('events'),[]) if isinstance(x,dict)],
              'total_tokens':int(row.get('total_tokens') or 0),
              'quality':reply_quality(row)}
        # Later operator activity is comparison evidence only. It is kept out
        # of ``context`` and never presented as input used by the model.
        cur.execute('''SELECT count(*) AS n FROM saas_crm_messages later
          WHERE later.tenant_id=%s::uuid AND later.kommo_lead_id=%s AND later.channel=%s
            AND (later.direction='outgoing' OR later.raw ? 'verified_employee_id')
            AND (COALESCE(later.happened_at,later.created_at),later.created_at) > (
              SELECT COALESCE(source.happened_at,source.created_at),source.created_at FROM saas_crm_messages source
              WHERE source.tenant_id=%s::uuid AND source.id=%s::uuid)''',
          (cfg['AI_LEARNING_MCP_TENANT_ID'],row['lead_id'],'connect:'+cfg['AI_LEARNING_MCP_CHANNEL_ID'],
           cfg['AI_LEARNING_MCP_TENANT_ID'],row['source_message_id']))
        item['subsequent_employee_actions']={'count':int((cur.fetchone() or {}).get('n') or 0),'comparison_only':True}
        if include_context:
            snapshot=row.get('context_snapshot')
            if isinstance(snapshot,str):
                try: snapshot=json.loads(snapshot)
                except (TypeError,ValueError): snapshot=None
            if isinstance(snapshot,list):
                item['context']=[_public_context_line(value,conversation_id=row['conversation_id'])
                                 for value in snapshot if isinstance(value,dict)]
                item['context_provenance']='captured_model_input'
            else:
                # Historical runs have no immutable model-input snapshot. A
                # bounded reconstruction is useful for diagnosis, but is never
                # presented as the context used by the model.
                cur.execute('''SELECT direction,author_name,body,message_type,happened_at,created_at,raw FROM saas_crm_messages
                  WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s
                    AND ((COALESCE(happened_at,created_at),created_at) < (
                      SELECT COALESCE(happened_at,created_at),created_at FROM saas_crm_messages
                      WHERE tenant_id=%s::uuid AND id=%s::uuid) OR id=%s::uuid)
                  ORDER BY COALESCE(happened_at,created_at) DESC,created_at DESC,id DESC LIMIT 4''',
                  (cfg['AI_LEARNING_MCP_TENANT_ID'],row['lead_id'],'connect:'+cfg['AI_LEARNING_MCP_CHANNEL_ID'],
                   cfg['AI_LEARNING_MCP_TENANT_ID'],row['source_message_id'],row['source_message_id']))
                item['context']=[_public_context_line(value,conversation_id=row['conversation_id'])
                                 for value in reversed(cur.fetchall())]
                item['context_provenance']='legacy_cutoff_reconstruction'
        items.append(item)
    return {'items':items,'next_cursor':_cursor(cfg,page[-1]['created_at'],page[-1]['id']) if len(rows)>safe else None}


def _authorize_token(request):
    header=request.headers.get('Authorization','')
    if not header.startswith('Bearer '): raise TenantLinearError('Authorization required.',401)
    token=header[7:].strip()
    if not token or len(token)>500: raise TenantLinearError('Authorization required.',401)
    cfg=_require_cfg()
    with _connect() as conn:
        ensure_mcp_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT * FROM saas_ai_learning_mcp_tokens WHERE token_hash=%s AND tenant_id=%s::uuid
              AND agent_id=%s::uuid AND channel_id=%s::uuid AND revoked_at IS NULL AND expires_at>now() FOR UPDATE''',
              (_hash(token),cfg['AI_LEARNING_MCP_TENANT_ID'],cfg['AI_LEARNING_MCP_AGENT_ID'],cfg['AI_LEARNING_MCP_CHANNEL_ID']))
            row=cur.fetchone()
            if not row: raise TenantLinearError('Authorization required.',401)
            cfg['granted_scopes']=str(row.get('scope') or TOOL_SCOPE).split()
            cfg['actor_id']=row['created_by']
            if TOOL_SCOPE in cfg['granted_scopes'] or DRAFT_SCOPE in cfg['granted_scopes'] or KNOWLEDGE_DEDUP_SCOPE in cfg['granted_scopes'] or administrator_read.READ_SCOPE in cfg['granted_scopes']:
                _authorize(cur,{'tenant_id':cfg['AI_LEARNING_MCP_TENANT_ID'],'telegram_id':row['created_by']})
                cur.execute('SELECT role FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s',
                            (cfg['AI_LEARNING_MCP_TENANT_ID'],row['created_by']))
                if (cur.fetchone() or {}).get('role')!='owner': raise TenantLinearError('Company owner access required.',403)
                # The owner explicitly approved company-wide read access for
                # the existing connection. No write permission is implied.
                cfg['granted_scopes']=list(set(cfg['granted_scopes'])|{administrator_read.READ_SCOPE,administrator_read.CONTENT_SCOPE})
            cur.execute("UPDATE saas_ai_learning_mcp_tokens SET requests_in_window=CASE WHEN window_started_at<now()-interval '1 hour' THEN 1 ELSE requests_in_window+1 END, window_started_at=CASE WHEN window_started_at<now()-interval '1 hour' THEN now() ELSE window_started_at END WHERE id=%s",(row['id'],))
            cur.execute('SELECT requests_in_window FROM saas_ai_learning_mcp_tokens WHERE id=%s',(row['id'],))
            if cur.fetchone()['requests_in_window']>60: raise TenantLinearError('Rate limit exceeded.',429)
            _audit(cur,cfg['AI_LEARNING_MCP_TENANT_ID'],row['created_by'],'ai_learning_mcp_read','mcp_token',row['id'],{'client_id':row['client_id'][:120]})
        conn.commit()
    return cfg


def _tools(cur,cfg,name,args):
    if name == 'administrator_testing_compare':
        result=administrator_read.testing_compare(cur,cfg,args)
        _audit(cur,cfg['AI_LEARNING_MCP_TENANT_ID'],cfg['actor_id'],'mcp_administrator_read','mcp_tool',name)
        return result
    if name in administrator_read.SPECS:
        result=administrator_read.read(cur,cfg,name,args)
        _audit(cur,cfg['AI_LEARNING_MCP_TENANT_ID'],cfg['actor_id'],'mcp_administrator_read','mcp_tool',name)
        return result
    if name == 'administrator_draft_create': return administrator_drafts.create(cur,cfg,args)
    if name == 'administrator_draft_list': return administrator_drafts.listing(cur,cfg,args)
    if name == 'administrator_draft_update': return administrator_drafts.update(cur,cfg,args)
    if name == 'administrator_draft_versions': return administrator_drafts.versions(cur,cfg,args)
    if name == 'administrator_draft_restore': return administrator_drafts.restore(cur,cfg,args)
    if name == 'administrator_draft_agent_prompt_create': return administrator_drafts.typed_create(cur,cfg,'agent_prompt',args)
    if name == 'administrator_draft_knowledge_create': return administrator_drafts.typed_create(cur,cfg,'knowledge',args)
    if name == 'administrator_draft_support_case_create': return administrator_drafts.typed_create(cur,cfg,'support_case',args)
    if name == 'administrator_draft_testing_scenario_create': return administrator_drafts.typed_create(cur,cfg,'testing_scenario',args)
    profile={'tenant_id':cfg['AI_LEARNING_MCP_TENANT_ID'],'telegram_id':cfg['actor_id']}
    if name in {'knowledge_duplicate_preview','knowledge_duplicate_archive','knowledge_duplicate_rollback'}:
        if KNOWLEDGE_DEDUP_SCOPE not in set(cfg.get('granted_scopes') or []):
            raise TenantLinearError('Additional OAuth scope required.',403)
        actions={'knowledge_duplicate_preview':'preview','knowledge_duplicate_archive':'archive','knowledge_duplicate_rollback':'rollback'}
        if not isinstance(args,dict) or set(args)-{'canonical_id','duplicate_ids','precondition','request_key'}:
            raise TenantLinearError('Invalid knowledge duplicate request.',400)
        required={'canonical_id','duplicate_ids'} if name=='knowledge_duplicate_preview' else {'canonical_id','duplicate_ids','precondition','request_key'}
        if set(args) != required or not isinstance(args.get('duplicate_ids'),list) or not 1<=len(args['duplicate_ids'])<=50:
            raise TenantLinearError('Invalid knowledge duplicate request.',400)
        payload={'action':actions[name],'canonical_id':args['canonical_id'],'duplicate_ids':args['duplicate_ids']}
        if name!='knowledge_duplicate_preview':
            precondition=str(args.get('precondition') or '')
            if not re.fullmatch(r'[0-9a-f]{64}',precondition): raise TenantLinearError('Invalid knowledge duplicate precondition.',400)
            try: payload['request_key']=str(uuid.UUID(str(args.get('request_key'))))
            except (TypeError,ValueError): raise TenantLinearError('Invalid knowledge duplicate request.',400) from None
            payload['expected_precondition']=precondition
        result=knowledge_deduplication(profile,payload)
        _audit(cur,cfg['AI_LEARNING_MCP_TENANT_ID'],cfg['actor_id'],'mcp_knowledge_duplicate_'+actions[name],
               'knowledge_source',result.get('canonical_id'),{'duplicate_ids':result.get('duplicate_ids',[])})
        return result
    if name == 'administrator_approval_request':
        if DRAFT_SCOPE not in set(cfg.get('granted_scopes') or []): raise TenantLinearError('Additional OAuth scope required.',403)
        if set(args)!={'draft_id','version'}: raise TenantLinearError('Invalid approval request.',400)
        return administrator_approvals.request(cur,profile,args['draft_id'],args['version'])
    if name == 'administrator_approval_list':
        if DRAFT_SCOPE not in set(cfg.get('granted_scopes') or []): raise TenantLinearError('Additional OAuth scope required.',403)
        if set(args)-{'limit'}: raise TenantLinearError('Invalid approval list.',400)
        return administrator_approvals.listing(cur,profile,limit=args.get('limit',50))
    if name=='simulation_runs': return _runs(cur,cfg,args.get('cursor',''),args.get('limit',10),bool(args.get('include_context')))
    if name=='learning_issues':
        result=_runs(cur,cfg,args.get('cursor',''),MAX_PAGE,False); groups={}
        for run in result['items']:
            for issue in findings({'outcome':run['outcome'],'virtual_stop_reason':run['stop_reason'],'events':run['events']}):
                record=groups.setdefault(issue['issue_key'],{**issue,'count':0,'runs':[]}); record['count']+=1; record['runs'].append(run['run_id'])
        return {'items':list(groups.values()),'next_cursor':result['next_cursor']}
    if name=='usage_summary':
        cur.execute('''SELECT COALESCE(sum(u.reserved_tokens),0) reserved,COALESCE(sum(u.actual_tokens),0) actual,
              count(*) FILTER(WHERE u.state='unknown') unknown_calls FROM saas_ai_simulation_usage u
              JOIN saas_ai_simulation_runs r ON r.tenant_id=u.tenant_id AND r.queue_id=u.queue_id
              WHERE r.tenant_id=%s::uuid AND r.primary_agent_id=%s::uuid AND r.channel_id=%s::uuid''',
              (cfg['AI_LEARNING_MCP_TENANT_ID'],cfg['AI_LEARNING_MCP_AGENT_ID'],cfg['AI_LEARNING_MCP_CHANNEL_ID']))
        return dict(cur.fetchone())
    raise TenantLinearError('Tool not found.',404)


def _jsonrpc(result=None, error=None, request_id=None):
    return web.json_response({'jsonrpc':'2.0','id':request_id, **({'result':result} if error is None else {'error':error})},headers={'Cache-Control':'no-store'})


def _knowledge_dedup_definitions():
    schema={'type':'object','properties':{'canonical_id':{'type':'string'},
      'duplicate_ids':{'type':'array','minItems':1,'maxItems':50,'items':{'type':'string'}},
      'precondition':{'type':'string','pattern':'^[0-9a-f]{64}$'},'request_key':{'type':'string'}},
      'additionalProperties':False}
    security=[{'type':'oauth2','scopes':[KNOWLEDGE_DEDUP_SCOPE]}]
    safe={'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False}
    return [
      {'name':'knowledge_duplicate_preview','description':'Owner-only preview of exact same-agent TXT duplicates. It exposes no source text and changes nothing.',
       'inputSchema':{**schema,'required':['canonical_id','duplicate_ids']},'annotations':{**safe,'readOnlyHint':True},'securitySchemes':security},
      {'name':'knowledge_duplicate_archive','description':'Owner-only non-destructive archive after a matching preview. It preserves duplicate rows and chunks and requires an idempotency key.',
       'inputSchema':{**schema,'required':['canonical_id','duplicate_ids','precondition','request_key']},'annotations':safe,'securitySchemes':security},
      {'name':'knowledge_duplicate_rollback','description':'Owner-only rollback of a prior duplicate archive after a matching preview. It requires an idempotency key.',
       'inputSchema':{**schema,'required':['canonical_id','duplicate_ids','precondition','request_key']},'annotations':safe,'securitySchemes':security},
    ]


async def mcp(request):
    try:
        data=await request.json(); method=data.get('method'); request_id=data.get('id')
        if method=='initialize': return _jsonrpc({'protocolVersion':'2025-06-18','serverInfo':{'name':'beintaskbot-learning-loop','version':'1.0'},'capabilities':{'tools':{}}},request_id=request_id)
        if method=='tools/list':
            auth=[{'type':'oauth2','scopes':[TOOL_SCOPE]}]
            return _jsonrpc({'tools':[{'name':'simulation_runs','description':'Read new scoped simulation runs. Context is optional and redacted.','inputSchema':{'type':'object','properties':{'cursor':{'type':'string'},'limit':{'type':'integer'},'include_context':{'type':'boolean'}}},'annotations':{'readOnlyHint':True},'securitySchemes':auth}, {'name':'learning_issues','description':'Read evidence-based Learning Loop issues for the scoped agent and channel.','inputSchema':{'type':'object','properties':{'cursor':{'type':'string'}}},'annotations':{'readOnlyHint':True},'securitySchemes':auth}, {'name':'usage_summary','description':'Read scoped Simulation token usage.','inputSchema':{'type':'object'},'annotations':{'readOnlyHint':True},'securitySchemes':auth}]+_knowledge_dedup_definitions()+administrator_read.definitions()+administrator_drafts.definitions()+administrator_approvals.definitions()},request_id=request_id)
        if method=='tools/call':
            cfg=_authorize_token(request)
            with _connect() as conn:
                ensure_mcp_schema(conn)
                with conn.cursor() as cur: output=_tools(cur,cfg,data.get('params',{}).get('name',''),data.get('params',{}).get('arguments') or {})
                if data.get('params',{}).get('name','').startswith('administrator_draft_') or data.get('params',{}).get('name','').startswith('administrator_approval_') or data.get('params',{}).get('name','').startswith('knowledge_duplicate_'):
                    conn.commit()
            return _jsonrpc({'content':[{'type':'text','text':json.dumps(output,ensure_ascii=False)}]},request_id=request_id)
        return _jsonrpc(error={'code':-32601,'message':'Method not found'},request_id=request_id)
    except TenantLinearError as exc:
        headers={'WWW-Authenticate':'Bearer'} if exc.status==401 else {}
        return web.json_response({'error':str(exc)},status=exc.status,headers=headers)
    except Exception:
        return web.json_response({'error':'AI Learning MCP unavailable.'},status=503)


def _chatgpt_client(client_id, redirect_uri=None):
    # Trust only OpenAI's two published HTTPS CIMD URL shapes, not arbitrary
    # client URLs. A callback-specific client must use its *own* exact callback.
    # This is an explicit pinned-client policy; no request-controlled URL is
    # fetched and no prefix/hostname-only redirect matching is permitted.
    client_id=str(client_id or '')
    if client_id == CHATGPT_CLIENT_ID:
        expected_redirect=CHATGPT_REDIRECT_URI
    else:
        match=re.fullmatch(r'https://chatgpt\.com/oauth/([A-Za-z0-9_-]{1,128})/client\.json',client_id)
        if not match: raise TenantLinearError('Untrusted OAuth client.',400)
        expected_redirect='https://chatgpt.com/connector/oauth/'+match.group(1)
    if redirect_uri is not None and str(redirect_uri) != expected_redirect:
        raise TenantLinearError('Untrusted OAuth redirect.',400)
    return expected_redirect


def _oauth_resource(value, cfg):
    if value and value != cfg['issuer']+'/mcp/ai-learning':
        raise TenantLinearError('Untrusted OAuth resource.',400)


def _requested_scopes(value):
    requested={part for part in str(value or TOOL_SCOPE).split() if part}
    if not requested or TOOL_SCOPE not in requested or requested-{TOOL_SCOPE,DRAFT_SCOPE,KNOWLEDGE_DEDUP_SCOPE,OFFLINE_SCOPE}:
        raise TenantLinearError('Unsupported OAuth scope.',400)
    return requested


def _consent_text(scopes):
    """Describe the exact OAuth grant without treating draft access as read-only.

    A draft is deliberately a non-public proposal, but creating or editing one
    is still a write operation.  The consent page must make that distinction so
    an existing read token is not mistaken for a token that can manage drafts.
    """
    if KNOWLEDGE_DEDUP_SCOPE in scopes:
        return (
            'Knowledge duplicate maintenance for the configured company only. '
            'ChatGPT may preview exact duplicate sources and non-destructively '
            'archive or roll back only sources that pass server-side checks. '
            'It cannot publish knowledge, send messages, or change agent settings.'
        ), 'Allow knowledge duplicate maintenance'
    if DRAFT_SCOPE in scopes:
        return (
            'Draft proposal access for one configured AI agent and WhatsApp '
            'channel. ChatGPT may create and edit non-public proposals only. '
            'It cannot send messages, publish knowledge, change settings, or '
            'apply a prompt without a separate owner approval.'
        ), 'Allow draft proposal access'
    return (
        'Read-only access for one configured AI agent and WhatsApp channel. '
        'No message sending or settings changes.'
    ), 'Allow read-only access'


async def authorize(request, member_from_request):
    try:
        cfg=_require_cfg(); profile=member_from_request(request)
        if not profile: return web.Response(text='Sign in to Beintaskbot, then retry this ChatGPT connection.',status=401,content_type='text/plain')
        if request.method=='GET':
            client_id=request.query.get('client_id',''); redirect_uri=request.query.get('redirect_uri','')
            _chatgpt_client(client_id,redirect_uri)
            _oauth_resource(request.query.get('resource',''),cfg)
            if request.query.get('response_type') != 'code': raise TenantLinearError('Only authorization code flow is supported.',400)
            scopes=_requested_scopes(request.query.get('scope',TOOL_SCOPE))
            state=html.escape(request.query.get('state',''),quote=True); challenge=html.escape(request.query.get('code_challenge',''),quote=True)
            if request.query.get('code_challenge_method')!='S256' or not re.fullmatch(r'[A-Za-z0-9_-]{43}',challenge): raise TenantLinearError('PKCE S256 is required.',400)
            form=''.join(f'<input type="hidden" name="{key}" value="{value}">' for key,value in [('client_id',html.escape(client_id,quote=True)),('redirect_uri',html.escape(redirect_uri,quote=True)),('state',state),('code_challenge',challenge),('scope',html.escape(' '.join(sorted(scopes)),quote=True)),('resource',html.escape(request.query.get('resource',''),quote=True))])
            description, button=_consent_text(scopes)
            return web.Response(text='<html><body><h1>Connect ChatGPT</h1><p>'+html.escape(description)+'</p><form method="post">'+form+'<button type="submit">'+html.escape(button)+'</button></form></body></html>',content_type='text/html')
        data=await request.post(); client_id=str(data.get('client_id','')); redirect_uri=str(data.get('redirect_uri','')); _chatgpt_client(client_id,redirect_uri)
        _oauth_resource(str(data.get('resource','')),cfg)
        scopes=_requested_scopes(data.get('scope',TOOL_SCOPE))
        challenge=str(data.get('code_challenge',''))
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}',challenge): raise TenantLinearError('PKCE S256 is required.',400)
        with _connect() as conn:
            ensure_mcp_schema(conn)
            with conn.cursor() as cur:
                tenant,actor=_authorize(cur,profile)
                if tenant!=cfg['AI_LEARNING_MCP_TENANT_ID'] or profile.get('role')!='owner': raise TenantLinearError('Only the configured company owner can authorize this connection.',403)
                code=secrets.token_urlsafe(40)
                cur.execute('''INSERT INTO saas_ai_learning_mcp_codes(code_hash,tenant_id,agent_id,channel_id,client_id,redirect_uri,code_challenge,scope,actor_id,expires_at)
                  VALUES(%s,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,now()+interval '10 minutes')''',(_hash(code),tenant,cfg['AI_LEARNING_MCP_AGENT_ID'],cfg['AI_LEARNING_MCP_CHANNEL_ID'],client_id,redirect_uri,challenge,' '.join(sorted(scopes)),actor))
                _audit(cur,tenant,actor,'ai_learning_mcp_authorized','mcp_oauth_client',client_id,{'agent_id':cfg['AI_LEARNING_MCP_AGENT_ID'],'channel_id':cfg['AI_LEARNING_MCP_CHANNEL_ID']})
            conn.commit()
        separator='&' if '?' in redirect_uri else '?'
        from urllib.parse import urlencode
        return web.HTTPFound(redirect_uri+separator+urlencode({'code':code,'state':str(data.get('state',''))}))
    except TenantLinearError as exc: return web.Response(text=str(exc),status=exc.status,content_type='text/plain')


async def token(request):
    try:
        cfg=_require_cfg(); data=await request.post()
        grant=str(data.get('grant_type','')); client_id=str(data.get('client_id',''))
        _oauth_resource(str(data.get('resource','')),cfg)
        if grant not in {'authorization_code','refresh_token'}: raise TenantLinearError('Unsupported grant.',400)
        if grant=='authorization_code':
            redirect_uri=str(data.get('redirect_uri','')); _chatgpt_client(client_id,redirect_uri)
            verifier=str(data.get('code_verifier',''))
            if not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}',verifier): raise TenantLinearError('Invalid PKCE verifier.',400)
            digest=_b64(hashlib.sha256(verifier.encode()).digest())
        else:
            _chatgpt_client(client_id)
        with _connect() as conn:
            ensure_mcp_schema(conn)
            with conn.cursor() as cur:
                if grant=='authorization_code':
                    code=str(data.get('code',''))
                    cur.execute('SELECT * FROM saas_ai_learning_mcp_codes WHERE code_hash=%s AND expires_at>now() AND tenant_id=%s::uuid AND agent_id=%s::uuid AND channel_id=%s::uuid FOR UPDATE',(_hash(code),cfg['AI_LEARNING_MCP_TENANT_ID'],cfg['AI_LEARNING_MCP_AGENT_ID'],cfg['AI_LEARNING_MCP_CHANNEL_ID'])); row=cur.fetchone()
                    if not row or row.get('used_at') or row['client_id']!=client_id or row['redirect_uri']!=redirect_uri or not hmac.compare_digest(row['code_challenge'],digest): raise TenantLinearError('Invalid authorization code.',400)
                    cur.execute('UPDATE saas_ai_learning_mcp_codes SET used_at=now() WHERE code_hash=%s',(_hash(code),))
                    token_id=uuid.uuid4()
                    refresh=secrets.token_urlsafe(48) if OFFLINE_SCOPE in str(row['scope']).split() else None
                    cur.execute('''INSERT INTO saas_ai_learning_mcp_tokens(id,tenant_id,token_hash,agent_id,channel_id,client_id,expires_at,refresh_token_hash,refresh_expires_at,scope,created_by)
                      VALUES(%s,%s::uuid,%s,%s::uuid,%s::uuid,%s,now()+interval '8 hours',%s,CASE WHEN %s::text IS NULL THEN NULL ELSE now()+interval '30 days' END,%s,%s)''',(token_id,row['tenant_id'],_hash(secrets.token_urlsafe(48)),row['agent_id'],row['channel_id'],client_id,_hash(refresh) if refresh else None,refresh,row['scope'],row['actor_id']))
                else:
                    refresh=str(data.get('refresh_token',''))
                    cur.execute('''SELECT * FROM saas_ai_learning_mcp_tokens WHERE refresh_token_hash=%s AND client_id=%s
                      AND tenant_id=%s::uuid AND agent_id=%s::uuid AND channel_id=%s::uuid
                      AND revoked_at IS NULL AND refresh_expires_at>now() FOR UPDATE''',(_hash(refresh),client_id,cfg['AI_LEARNING_MCP_TENANT_ID'],cfg['AI_LEARNING_MCP_AGENT_ID'],cfg['AI_LEARNING_MCP_CHANNEL_ID'])); row=cur.fetchone()
                    if not row: raise TenantLinearError('Invalid refresh token.',400)
                    token_id=row['id']
                    refresh=secrets.token_urlsafe(48)
                    cur.execute('''UPDATE saas_ai_learning_mcp_tokens SET token_hash=%s,expires_at=now()+interval '8 hours',
                      refresh_token_hash=%s,refresh_expires_at=now()+interval '30 days' WHERE id=%s''',(_hash(secrets.token_urlsafe(48)),_hash(refresh),token_id))
                access=secrets.token_urlsafe(48)
                cur.execute('UPDATE saas_ai_learning_mcp_tokens SET token_hash=%s WHERE id=%s',(_hash(access),token_id))
                _audit(cur,row['tenant_id'],row.get('actor_id',row.get('created_by')),'ai_learning_mcp_token_issued','mcp_token',token_id,{'client_id':client_id[:120],'grant':grant})
            conn.commit()
        response={'access_token':access,'token_type':'Bearer','expires_in':28800,'scope':str(row.get('scope') or TOOL_SCOPE)}
        if refresh: response['refresh_token']=refresh
        return web.json_response(response,headers={'Cache-Control':'no-store'})
    except TenantLinearError as exc: return web.json_response({'error':'invalid_grant','error_description':str(exc)},status=exc.status,headers={'Cache-Control':'no-store'})


def routes(member_from_request):
    async def protected(request):
        try:
            cfg=_require_cfg()
            return web.json_response({'resource':cfg['issuer']+'/mcp/ai-learning','authorization_servers':[cfg['issuer']], 'scopes_supported':[TOOL_SCOPE,DRAFT_SCOPE,KNOWLEDGE_DEDUP_SCOPE,OFFLINE_SCOPE]})
        except TenantLinearError as exc: return web.json_response({'error':str(exc)},status=exc.status)
    async def metadata(request):
        try:
            cfg=_require_cfg(); base=cfg['issuer']
            return web.json_response({'issuer':base,'authorization_endpoint':base+'/oauth/authorize','token_endpoint':base+'/oauth/token','code_challenge_methods_supported':['S256'],'scopes_supported':[TOOL_SCOPE,DRAFT_SCOPE,KNOWLEDGE_DEDUP_SCOPE,OFFLINE_SCOPE],'response_types_supported':['code'],'grant_types_supported':['authorization_code','refresh_token'],'token_endpoint_auth_methods_supported':['none'],'client_id_metadata_document_supported':True})
        except TenantLinearError as exc: return web.json_response({'error':str(exc)},status=exc.status)
    return [('POST','/mcp/ai-learning',mcp),('GET','/mcp/ai-learning',mcp),('GET','/.well-known/oauth-protected-resource/mcp/ai-learning',protected),('GET','/.well-known/oauth-authorization-server',metadata),('GET','/oauth/authorize',lambda req:authorize(req,member_from_request)),('POST','/oauth/authorize',lambda req:authorize(req,member_from_request)),('POST','/oauth/token',token)]


def handler(member_from_request):
    async def route(request):
        if request.path.endswith('/mcp'): return await mcp(request)
        if request.path.endswith('/oauth-protected-resource'):
            cfg=_require_cfg(); return web.json_response({'resource':cfg['issuer']+'/mcp','authorization_servers':[cfg['issuer']]})
        return web.json_response({'error':'Not implemented'},status=404)
    return route
