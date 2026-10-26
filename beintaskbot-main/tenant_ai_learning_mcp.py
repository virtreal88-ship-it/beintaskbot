"""Read-only remote MCP view of a deliberately narrow AI Learning Loop scope.

This module never calls a model or exposes CRM/Connect credentials.  It is
disabled unless the Railway owner supplies all MCP_* variables.  OAuth access
tokens are opaque, hashed at rest, short lived and may be revoked.
"""
import asyncio
import base64
import hashlib
import hmac
import html
import json
import logging
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
import tenant_ai_owner_review_component as owner_review_component
import tenant_ai_dialogue_merge as dialogue_merge
import tenant_ai_direct_reconcile_mcp as direct_reconcile
import tenant_ai_knowledge_extraction_preflight as knowledge_extraction_preflight

TOOL_SCOPE = 'ai_learning.read'
DRAFT_SCOPE = administrator_drafts.DRAFT_SCOPE
KNOWLEDGE_DEDUP_SCOPE = 'ai_learning.knowledge_dedup'
APPLY_SCOPE = 'ai_learning.apply'
PUBLISH_SCOPE = administrator_approvals.PUBLISH_SCOPE
DIALOGUE_MERGE_SCOPE = dialogue_merge.MERGE_SCOPE
OFFLINE_SCOPE = 'offline_access'
MAX_PAGE = 25
CHATGPT_CLIENT_ID = 'https://chatgpt.com/oauth/client.json'
CHATGPT_REDIRECT_URI = 'https://chatgpt.com/connector_platform_oauth_redirect'
log = logging.getLogger(__name__)


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
    owner_review_component.ensure_schema(conn)
    dialogue_merge.ensure_schema(conn)
    direct_reconcile.reconcile.ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_learning_mcp_tokens(
          id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          token_hash TEXT NOT NULL UNIQUE, agent_id UUID NOT NULL, channel_id UUID NOT NULL,
          client_id TEXT NOT NULL, expires_at TIMESTAMPTZ NOT NULL, revoked_at TIMESTAMPTZ NULL,
          refresh_token_hash TEXT NULL UNIQUE, refresh_expires_at TIMESTAMPTZ NULL,
          scope TEXT NOT NULL DEFAULT 'ai_learning.read',
          oauth_connection_id UUID NULL,
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
        cur.execute('ALTER TABLE saas_ai_learning_mcp_tokens ADD COLUMN IF NOT EXISTS oauth_connection_id UUID NULL')
        # A legacy token without a connection id remains usable for its old
        # scopes, but cannot prepare a component review (fail closed).
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
    text=value.get('text') if 'text' in value else value.get('body')
    receipt=str(value.get('media_evidence_context') or '')
    return {'role':author_type,'at':_iso(value.get('at') or value.get('happened_at') or value.get('created_at')),
            'text':_redact((str(text or '')+'\n'+receipt).strip()),
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
                cur.execute('''SELECT external_id,channel,direction,author_name,body,message_type,happened_at,created_at,raw FROM saas_crm_messages
                  WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s
                    AND ((COALESCE(happened_at,created_at),created_at) < (
                      SELECT COALESCE(happened_at,created_at),created_at FROM saas_crm_messages
                      WHERE tenant_id=%s::uuid AND id=%s::uuid) OR id=%s::uuid)
                  ORDER BY COALESCE(happened_at,created_at) DESC,created_at DESC,id DESC LIMIT 4''',
                  (cfg['AI_LEARNING_MCP_TENANT_ID'],row['lead_id'],'connect:'+cfg['AI_LEARNING_MCP_CHANNEL_ID'],
                   cfg['AI_LEARNING_MCP_TENANT_ID'],row['source_message_id'],row['source_message_id']))
                context_rows=list(reversed(cur.fetchall()))
                from tenant_media_evidence import annotate_rows
                annotate_rows(cur,cfg['AI_LEARNING_MCP_TENANT_ID'],context_rows)
                item['context']=[_public_context_line(value,conversation_id=row['conversation_id'])
                                 for value in context_rows]
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
            cfg['oauth_connection_id']=str(row['oauth_connection_id'])
            cfg['oauth_token_id']=str(row['id'])
            if TOOL_SCOPE in cfg['granted_scopes'] or DRAFT_SCOPE in cfg['granted_scopes'] or KNOWLEDGE_DEDUP_SCOPE in cfg['granted_scopes'] or APPLY_SCOPE in cfg['granted_scopes'] or DIALOGUE_MERGE_SCOPE in cfg['granted_scopes'] or administrator_read.READ_SCOPE in cfg['granted_scopes']:
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
    if name == knowledge_extraction_preflight.NAME:
        if TOOL_SCOPE not in set(cfg.get('granted_scopes') or []):
            raise TenantLinearError('Additional OAuth scope required.',403)
        return knowledge_extraction_preflight.preflight(cur,cfg,args)
    if name == 'administrator_owner_review_component':
        return owner_review_component.prepare(cur,cfg,args)
    if name == 'administrator_owner_review_component_complete':
        if not isinstance(args,dict) or set(args)!={'capability','action','approval_id'}:
            raise TenantLinearError('Invalid owner component decision.',400)
        return owner_review_component.decide(cur,cfg,capability=args['capability'],action=args['action'],approval_id=args['approval_id'])
    if name == 'administrator_owner_publish_review_component':
        if not isinstance(args,dict) or set(args)!={'draft_id','source_id','kind','agent_id','channel_id'}:
            raise TenantLinearError('Invalid owner publication review.',400)
        return owner_review_component.prepare_publish(cur,cfg,**args)
    if name == 'administrator_owner_publish_component_complete':
        if not isinstance(args,dict) or set(args)!={'capability'}:
            raise TenantLinearError('Invalid owner publication consent.',400)
        return owner_review_component.complete_publish(cur,cfg,capability=args['capability'])
    if name.startswith(direct_reconcile.PREFIX):
        scope=TOOL_SCOPE if name in {direct_reconcile.PREFIX+'preview',direct_reconcile.PREFIX+'rollback_preflight'} else direct_reconcile.WRITE_SCOPE
        if scope not in set(cfg.get('granted_scopes') or []): raise TenantLinearError('Additional OAuth scope required.',403)
        return direct_reconcile.execute(cur,cfg,name,args)
    if name=='whatsapp_dialogue_duplicate_candidates':
        if TOOL_SCOPE not in set(cfg.get('granted_scopes') or []): raise TenantLinearError('Additional OAuth scope required.',403)
        result=dialogue_merge.preview(cur,cfg,args)
        _audit(cur,cfg['AI_LEARNING_MCP_TENANT_ID'],cfg['actor_id'],'mcp_whatsapp_dialogue_duplicate_preview','mcp_tool',name,{'count':len(result['items'])})
        return result
    if name=='whatsapp_dialogue_merge':
        if DIALOGUE_MERGE_SCOPE not in set(cfg.get('granted_scopes') or []): raise TenantLinearError('Additional OAuth scope required.',403)
        return dialogue_merge.execute(cur,cfg,args)
    if name=='administrator_training_sources':
        return administrator_approvals.training_sources(cur,cfg,args)
    if name in {'administrator_training_create','administrator_training_update','administrator_training_publish','administrator_training_rollback'}:
        return administrator_approvals.training_operation(cur,cfg,name.removeprefix('administrator_training_'),args)
    if name=='administrator_training_policies':
        if args or PUBLISH_SCOPE not in set(cfg.get('granted_scopes') or []): raise TenantLinearError('Additional OAuth scope required or invalid arguments.',403)
        return administrator_approvals.training_policy_list(cur,{'tenant_id':cfg['AI_LEARNING_MCP_TENANT_ID'],'telegram_id':cfg['actor_id']},cfg)
    if name in {'administrator_publish_reviewed','administrator_rollback_publication'}:
        if not isinstance(args,dict) or set(args)!={'grant_id'}: raise TenantLinearError('Invalid publication request.',400)
        return administrator_approvals.publication_execute(cur,cfg,args['grant_id'],rollback=name=='administrator_rollback_publication')
    if name=='administrator_publication_grants':
        if args: raise TenantLinearError('Invalid grant list request.',400)
        if PUBLISH_SCOPE not in set(cfg.get('granted_scopes') or []): raise TenantLinearError('Additional OAuth scope required.',403)
        result=administrator_approvals.publication_list(cur,{'tenant_id':cfg['AI_LEARNING_MCP_TENANT_ID'],'telegram_id':cfg['actor_id']})
        result['items']=[v for v in result['items'] if v['agent_id']==cfg['AI_LEARNING_MCP_AGENT_ID'] and v['channel_id']==cfg['AI_LEARNING_MCP_CHANNEL_ID']]
        return result
    if name == 'administrator_testing_compare':
        result=administrator_read.testing_compare(cur,cfg,args)
        _audit(cur,cfg['AI_LEARNING_MCP_TENANT_ID'],cfg['actor_id'],'mcp_administrator_read','mcp_tool',name)
        return result
    if name in administrator_read.SPECS:
        result=administrator_read.read(cur,cfg,name,args)
        _audit(cur,cfg['AI_LEARNING_MCP_TENANT_ID'],cfg['actor_id'],'mcp_administrator_read','mcp_tool',name)
        return result
    if name == 'administrator_draft_trigger_create': return administrator_drafts.typed_create(cur,cfg,'trigger',args)
    if name in {'administrator_apply_trigger_draft','administrator_rollback_trigger_draft'}:
        if APPLY_SCOPE not in set(cfg.get('granted_scopes') or []): raise TenantLinearError('Additional OAuth scope required.',403)
        expected={'approval_id','draft_id'} if name=='administrator_apply_trigger_draft' else {'approval_id'}
        if set(args)!=expected: raise TenantLinearError('Invalid trigger request.',400)
        if 'draft_id' in args: return administrator_apply.apply_trigger_draft(cur,{'tenant_id':cfg['AI_LEARNING_MCP_TENANT_ID'],'telegram_id':cfg['actor_id']},args['approval_id'],args['draft_id'],cfg)
        return administrator_apply.rollback_trigger_draft(cur,{'tenant_id':cfg['AI_LEARNING_MCP_TENANT_ID'],'telegram_id':cfg['actor_id']},args['approval_id'],cfg)
    if name == 'administrator_draft_agent_config_create': return administrator_drafts.typed_create(cur,cfg,'agent_config',args)
    if name in {'administrator_apply_agent_config','administrator_rollback_agent_config'}:
        if APPLY_SCOPE not in set(cfg.get('granted_scopes') or []): raise TenantLinearError('Additional OAuth scope required.',403)
        expected={'approval_id','draft_id'} if name=='administrator_apply_agent_config' else {'approval_id'}
        if set(args)!=expected: raise TenantLinearError('Invalid configuration request.',400)
        principal={'tenant_id':cfg['AI_LEARNING_MCP_TENANT_ID'],'telegram_id':cfg['actor_id']}
        if 'draft_id' in args: return administrator_apply.apply_agent_config(cur,principal,args['approval_id'],args['draft_id'],cfg)
        return administrator_apply.rollback_agent_config(cur,principal,args['approval_id'],cfg)
    if name == 'administrator_draft_instruction_create': return administrator_drafts.typed_create(cur,cfg,'instruction',args)
    if name in {'administrator_historical_replay_preflight','administrator_historical_replay_candidates'}:
        if TOOL_SCOPE not in set(cfg.get('granted_scopes') or []): raise TenantLinearError('Additional OAuth scope required.',403)
        if not isinstance(args,dict) or (set(args)!={'message_ids'} if name=='administrator_historical_replay_preflight' else bool(set(args)-{'message_ids'})): raise TenantLinearError('Invalid replay preflight.',400)
        import tenant_ai_historical_replay as replay
        if (cfg['AI_LEARNING_MCP_TENANT_ID'],cfg['AI_LEARNING_MCP_AGENT_ID'],cfg['AI_LEARNING_MCP_CHANNEL_ID'])!=(replay.PILOT_TENANT,replay.PILOT_AGENT,replay.PILOT_CHANNEL): raise TenantLinearError('Replay outside OAuth scope.',403)
        owner={'tenant_id':cfg['AI_LEARNING_MCP_TENANT_ID'],'telegram_id':cfg['actor_id']}
        if name=='administrator_historical_replay_candidates': return replay.eligible_examples(owner,args.get('message_ids'))
        return replay.preflight(owner,args['message_ids'])
    if name == 'administrator_draft_create': return administrator_drafts.create(cur,cfg,args)
    if name == 'administrator_draft_list': return administrator_drafts.listing(cur,cfg,args)
    if name == 'administrator_draft_update': return administrator_drafts.update(cur,cfg,args)
    if name == 'administrator_draft_versions': return administrator_drafts.versions(cur,cfg,args)
    if name == 'administrator_draft_restore': return administrator_drafts.restore(cur,cfg,args)
    if name == 'administrator_draft_agent_prompt_create': return administrator_drafts.typed_create(cur,cfg,'agent_prompt',args)
    if name == 'administrator_draft_knowledge_create': return administrator_drafts.typed_create(cur,cfg,'knowledge',args)
    if name == 'administrator_draft_owner_knowledge_create': return administrator_drafts.typed_create(cur,cfg,'owner_knowledge',args)
    if name == 'administrator_draft_knowledge_separation_create': return administrator_drafts.typed_create(cur,cfg,'knowledge_separation',args)
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
    if name == 'administrator_apply_agent_prompt':
        if APPLY_SCOPE not in set(cfg.get('granted_scopes') or []): raise TenantLinearError('Additional OAuth scope required.',403)
        if set(args)!={'approval_id','draft_id'}: raise TenantLinearError('Invalid administrator apply request.',400)
        return administrator_apply.apply_agent_prompt(cur,profile,args['approval_id'],args['draft_id'])
    if name == 'administrator_rollback_agent_prompt':
        if APPLY_SCOPE not in set(cfg.get('granted_scopes') or []): raise TenantLinearError('Additional OAuth scope required.',403)
        if set(args)!={'approval_id'}: raise TenantLinearError('Invalid administrator rollback request.',400)
        return administrator_apply.rollback_agent_prompt(cur,profile,args['approval_id'])
    if name == 'administrator_apply_knowledge_draft':
        if APPLY_SCOPE not in set(cfg.get('granted_scopes') or []): raise TenantLinearError('Additional OAuth scope required.',403)
        if set(args)!={'approval_id','draft_id'}: raise TenantLinearError('Invalid administrator apply request.',400)
        return administrator_apply.apply_knowledge_draft(cur,profile,args['approval_id'],args['draft_id'])
    if name == 'administrator_rollback_knowledge_draft':
        if APPLY_SCOPE not in set(cfg.get('granted_scopes') or []): raise TenantLinearError('Additional OAuth scope required.',403)
        if set(args)!={'approval_id'}: raise TenantLinearError('Invalid administrator rollback request.',400)
        return administrator_apply.rollback_knowledge_draft(cur,profile,args['approval_id'])
    if name == 'administrator_apply_instruction_draft':
        if APPLY_SCOPE not in set(cfg.get('granted_scopes') or []): raise TenantLinearError('Additional OAuth scope required.',403)
        if set(args)!={'approval_id','draft_id'}: raise TenantLinearError('Invalid administrator apply request.',400)
        return administrator_apply.apply_instruction_draft(cur,profile,args['approval_id'],args['draft_id'])
    if name == 'administrator_rollback_instruction_draft':
        if APPLY_SCOPE not in set(cfg.get('granted_scopes') or []): raise TenantLinearError('Additional OAuth scope required.',403)
        if set(args)!={'approval_id'}: raise TenantLinearError('Invalid administrator rollback request.',400)
        return administrator_apply.rollback_instruction_draft(cur,profile,args['approval_id'])
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


def _tool_response(value, request_id):
    """Serialize before commit so a response encoding failure cannot hide a write."""
    # Component-only metadata is intentionally absent from text and structured
    # content.  This is where a short-lived capability may travel; logging or
    # echoing it would turn a display token into a chat approval token.
    if isinstance(value,dict) and '_component_meta' in value:
        public={key:item for key,item in value.items() if key!='_component_meta'}
        return _jsonrpc({'content':[{'type':'text','text':json.dumps(public,ensure_ascii=False)}],
                         'structuredContent':public,'_meta':value['_component_meta']},request_id=request_id)
    return _jsonrpc({'content':[{'type':'text','text':json.dumps(value,ensure_ascii=False)}],
                     'structuredContent':value},request_id=request_id)


def _tool_error(message, *, status, request_id, retry_safety='not_committed', incident_id=None):
    """MCP tool failures are results, not a transport/session failure.

    OAuth authentication remains an HTTP challenge.  Once a bearer token was
    accepted, validation and business errors must keep the MCP session alive so
    the client can inspect an idempotency receipt instead of blindly retrying.
    """
    error={'message':str(message)[:500],'status':int(status),'retry_safety':retry_safety}
    if incident_id: error['incident_id']=incident_id
    return _jsonrpc({'content':[{'type':'text','text':json.dumps({'error':error},ensure_ascii=False)}],
                     'isError':True},request_id=request_id)


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


def _administrator_apply_definitions():
    security=[{'type':'oauth2','scopes':[APPLY_SCOPE]}]
    return _replay_preflight_definitions()+_config_definitions()+_trigger_definitions()+[
      {'name':'administrator_apply_agent_prompt','description':'Apply one exact owner-approved and isolated-test-verified prompt draft. The server requires a live owner grant, enabled application feature flag, matching agent version, and a one-time approval receipt.',
       'inputSchema':{'type':'object','properties':{'approval_id':{'type':'string'},'draft_id':{'type':'string'}},'required':['approval_id','draft_id'],'additionalProperties':False},
       'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':security},
      {'name':'administrator_rollback_agent_prompt','description':'Restore the exact prompt snapshot created by a prior approved application. It fails if the agent changed after application and can only be used once.',
       'inputSchema':{'type':'object','properties':{'approval_id':{'type':'string'}},'required':['approval_id'],'additionalProperties':False},
       'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':security},
      {'name':'administrator_apply_knowledge_draft','description':'Store one owner-approved knowledge draft only as inactive review material. It cannot publish or bind it to an agent.',
       'inputSchema':{'type':'object','properties':{'approval_id':{'type':'string'},'draft_id':{'type':'string'}},'required':['approval_id','draft_id'],'additionalProperties':False},
       'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':security},
      {'name':'administrator_rollback_knowledge_draft','description':'Withdraw an applied inactive knowledge review source without deleting evidence.',
       'inputSchema':{'type':'object','properties':{'approval_id':{'type':'string'}},'required':['approval_id'],'additionalProperties':False},
       'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':security},
      {'name':'administrator_apply_instruction_draft','description':'Create one owner-approved Instruction Library draft. It cannot submit, approve, publish, share, or bind knowledge.',
       'inputSchema':{'type':'object','properties':{'approval_id':{'type':'string'},'draft_id':{'type':'string'}},'required':['approval_id','draft_id'],'additionalProperties':False},
       'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':security},
      {'name':'administrator_rollback_instruction_draft','description':'Archive one untouched applied Instruction Library draft. It fails closed after any review or edit.',
       'inputSchema':{'type':'object','properties':{'approval_id':{'type':'string'}},'required':['approval_id'],'additionalProperties':False},
       'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':security},
    ]


def _owner_component_definition():
    """App-only review preparation; it is never an approval tool."""
    return {'name':'administrator_owner_review_component',
      'description':'Prepare a disabled, exact-version owner review component for a knowledge, instruction, or support-case draft. It creates only a pending review; chat words and this tool cannot approve, publish, bind, or apply anything.',
      'inputSchema':{'type':'object','properties':{'draft_id':{'type':'string'},'version':{'type':'integer','minimum':1}},'required':['draft_id','version'],'additionalProperties':False},
      'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},
      'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}],
      '_meta':{'ui':{'visibility':['model','app'],'resourceUri':owner_review_component.RESOURCE_URI},
               'openai/outputTemplate':owner_review_component.RESOURCE_URI}}


def _owner_component_completion_definition():
    return {'name':'administrator_owner_review_component_complete',
      'description':'TEST-ONLY app completion for one exact owner-review capability. It is hidden from the model, derives tenant/owner/OAuth connection from the validated bearer, and never applies or publishes a draft.',
      'inputSchema':{'type':'object','properties':{'action':{'type':'string','enum':['approve','reject']},'approval_id':{'type':'string'},
          'capability':{'type':'string','minLength':32,'maxLength':200}},'required':['action','capability','approval_id'],'additionalProperties':False},
      'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},
      'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}],
      '_meta':{'ui':{'visibility':['app'],'resourceUri':owner_review_component.RESOURCE_URI},
               'openai/outputTemplate':owner_review_component.RESOURCE_URI}}


def _owner_publish_component_definitions():
    """A model may open review; only the app can complete exact consent."""
    security=[{'type':'oauth2','scopes':[DRAFT_SCOPE,PUBLISH_SCOPE]}]
    meta={'ui':{'visibility':['app'],'resourceUri':owner_review_component.RESOURCE_URI},
          'openai/outputTemplate':owner_review_component.RESOURCE_URI}
    return [
      {'name':'administrator_owner_publish_review_component',
       'description':'Prepare an exact post-apply publication review for an already applied knowledge or instruction material. It cannot grant, publish, bind, or change the material; the one-use completion capability is component-only.',
       'inputSchema':{'type':'object','properties':{'draft_id':{'type':'string'},'source_id':{'type':'string'},'kind':{'type':'string','enum':['knowledge','instruction']},'agent_id':{'type':'string'},'channel_id':{'type':'string'}},'required':['draft_id','source_id','kind','agent_id','channel_id'],'additionalProperties':False},
       'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':security,
       '_meta':{'ui':{'visibility':['model','app'],'resourceUri':owner_review_component.RESOURCE_URI},'openai/outputTemplate':owner_review_component.RESOURCE_URI}},
      {'name':'administrator_owner_publish_component_complete',
       'description':'TEST-ONLY app completion of one exact post-apply publication consent. It rechecks owner, OAuth connection, scope, source hash, agent and channel before creating the existing one-use grant and publishing.',
       'inputSchema':{'type':'object','properties':{'capability':{'type':'string','minLength':32,'maxLength':200}},'required':['capability'],'additionalProperties':False},
       'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':security,'_meta':meta},
    ]


async def mcp(request):
    method=None; request_id=None; tool_name=''; commit_attempted=False
    try:
        data=await request.json(); method=data.get('method'); request_id=data.get('id')
        if method=='initialize': return _jsonrpc({'protocolVersion':'2025-06-18','serverInfo':{'name':'beintaskbot-learning-loop','version':direct_reconcile.SCHEMA_VERSION},'capabilities':{'tools':{},'resources':{}}},request_id=request_id)
        if method=='resources/list':
            if not administrator_drafts.enabled():
                return _jsonrpc({'resources':[]},request_id=request_id)
            return _jsonrpc({'resources':[{'uri':owner_review_component.RESOURCE_URI,'name':'Exact owner review (disabled)',
              'mimeType':'text/html;profile=mcp-app','_meta':{'ui':{'prefersBorder':True,
                'csp':{'connectDomains':[],'resourceDomains':[]}},'openai/ui':{'availableDisplayModes':['inline']}}}]},request_id=request_id)
        if method=='resources/read':
            if not administrator_drafts.enabled():
                return _jsonrpc(error={'code':-32602,'message':'Resource not found'},request_id=request_id)
            uri=str(data.get('params',{}).get('uri',''))
            if uri != owner_review_component.RESOURCE_URI:
                return _jsonrpc(error={'code':-32602,'message':'Resource not found'},request_id=request_id)
            return _jsonrpc({'contents':[{'uri':owner_review_component.RESOURCE_URI,'mimeType':'text/html;profile=mcp-app',
                                           'text':owner_review_component.resource_html(),
                                           '_meta':{'ui':{'prefersBorder':True,
                                             'csp':{'connectDomains':[],'resourceDomains':[]}},
                                             'openai/ui':{'availableDisplayModes':['inline']}}}]},request_id=request_id)
        if method=='tools/list':
            auth=[{'type':'oauth2','scopes':[TOOL_SCOPE]}]
            component_tools=[]
            if administrator_drafts.enabled():
                component_tools.append(_owner_component_definition())
                if owner_review_component.enabled_for_manual_bridge():
                    component_tools.append(_owner_component_completion_definition())
                    component_tools.extend(_owner_publish_component_definitions())
            return _jsonrpc({'tools':[{'name':'simulation_runs','description':'Read new scoped simulation runs. Context is optional and redacted.','inputSchema':{'type':'object','properties':{'cursor':{'type':'string'},'limit':{'type':'integer'},'include_context':{'type':'boolean'}}},'annotations':{'readOnlyHint':True},'securitySchemes':auth}, {'name':'learning_issues','description':'Read evidence-based Learning Loop issues for the scoped agent and channel.','inputSchema':{'type':'object','properties':{'cursor':{'type':'string'}}},'annotations':{'readOnlyHint':True},'securitySchemes':auth}, {'name':'usage_summary','description':'Read scoped Simulation token usage.','inputSchema':{'type':'object'},'annotations':{'readOnlyHint':True},'securitySchemes':auth}]+knowledge_extraction_preflight.definitions(TOOL_SCOPE)+direct_reconcile.definitions()+dialogue_merge.definitions()+_knowledge_dedup_definitions()+administrator_read.definitions()+administrator_drafts.definitions()+administrator_approvals.definitions()+component_tools+_administrator_apply_definitions()+administrator_approvals.publication_definitions()+administrator_approvals.training_definitions()},request_id=request_id)
        if method=='tools/call':
            cfg=_authorize_token(request)
            tool_name=str(data.get('params',{}).get('name',''))
            arguments=data.get('params',{}).get('arguments') or {}
            # `_authorize_token` has already completed and committed the MCP
            # schema setup in its own connection.  Historical replay opens its
            # own read-only connection, so dispatching it while this handler
            # holds another transaction (and a migration DDL lock) can wait on
            # itself.  Keep this narrow read path outside the write dispatcher
            # and off the aiohttp event loop; all approval/apply writes below
            # retain their original single-transaction behavior.
            if tool_name in {'administrator_historical_replay_candidates','administrator_historical_replay_preflight'}:
                output=await asyncio.to_thread(_tools,None,cfg,tool_name,arguments)
                return _tool_response(output,request_id)
            with _connect() as conn:
                with conn.cursor() as cur: output=_tools(cur,cfg,tool_name,arguments)
                # Serialize the whole response before any write is committed.
                # An encoder exception therefore remains rollback-safe.
                response=_tool_response(output,request_id)
                if tool_name.startswith(direct_reconcile.PREFIX):
                    commit_attempted=True
                    conn.commit()
                if data.get('params',{}).get('name','').startswith('administrator_training_') or data.get('params',{}).get('name','').startswith('administrator_publish') or data.get('params',{}).get('name','').startswith('administrator_draft_') or data.get('params',{}).get('name','').startswith('administrator_approval_') or data.get('params',{}).get('name','').startswith('administrator_apply_') or data.get('params',{}).get('name','').startswith('administrator_rollback_') or data.get('params',{}).get('name','').startswith('administrator_owner_review_component') or data.get('params',{}).get('name','').startswith('knowledge_duplicate_') or data.get('params',{}).get('name','')=='whatsapp_dialogue_merge':
                    commit_attempted=True
                    conn.commit()
            return response
        return _jsonrpc(error={'code':-32601,'message':'Method not found'},request_id=request_id)
    except TenantLinearError as exc:
        headers={'WWW-Authenticate':'Bearer'} if exc.status==401 else {}
        # A missing OAuth scope remains a real authorization response so a
        # client can request the additional scope. Every other authenticated
        # tool error is encoded as a normal MCP result and never tears down the
        # session. Its transaction was not committed by this handler.
        if method=='tools/call' and tool_name.startswith('administrator_training_') and exc.status!=401 and not (exc.status==403 and 'OAuth scope required' in str(exc)):
            return _tool_error(exc,status=exc.status,request_id=request_id)
        return web.json_response({'error':str(exc),**({'details':exc.details} if hasattr(exc,'details') else {})},status=exc.status,headers=headers)
    except Exception:
        incident_id=secrets.token_hex(8)
        log.exception('AI Learning MCP tool failure incident=%s method=%s',incident_id,method)
        if method=='tools/call' and tool_name.startswith('administrator_training_'):
            # A failed commit can have an unknown network outcome. Make that
            # explicit and require the caller to inspect the policy/request
            # receipt before retrying the same idempotency key.
            safety='outcome_unknown' if commit_attempted else 'not_committed'
            return _tool_error('AI Learning MCP tool failed.',status=500,request_id=request_id,retry_safety=safety,incident_id=incident_id)
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
    if not requested or TOOL_SCOPE not in requested or requested-{TOOL_SCOPE,DRAFT_SCOPE,KNOWLEDGE_DEDUP_SCOPE,APPLY_SCOPE,PUBLISH_SCOPE,DIALOGUE_MERGE_SCOPE,direct_reconcile.WRITE_SCOPE,OFFLINE_SCOPE}:
        raise TenantLinearError('Unsupported OAuth scope.',400)
    return requested


def _consent_text(scopes):
    """Describe the exact OAuth grant without treating draft access as read-only.

    A draft is deliberately a non-public proposal, but creating or editing one
    is still a write operation.  The consent page must make that distinction so
    an existing read token is not mistaken for a token that can manage drafts.
    """
    if direct_reconcile.WRITE_SCOPE in scopes:
        return ('Owner-delegated PHYSICAL reconciliation of provider-proven direct WhatsApp duplicates in the configured company/channel. Moves CRM messages and supported dependencies with a backup, preserves media/history and stopped AI states. Each exact pair requires fresh preview, explicit confirmation and an idempotent request. Includes guarded rollback. No customer sends or WhatsApp session changes.'), 'Allow physical dialogue reconciliation'
    if DIALOGUE_MERGE_SCOPE in scopes:
        return (
            'Explicit owner-delegated dialogue merge access for one configured company and WhatsApp channel. '
            'ChatGPT may only merge or roll back a freshly previewed provider-proven direct LID/JID pair. '
            'It cannot use phone/name matching, groups, ambiguous identities, send messages, alter CRM rows, '
            'or modify WhatsApp. Each operation is idempotent and audited.'
        ), 'Allow reversible dialogue merge'
    if PUBLISH_SCOPE in scopes:
        text='Publication access for the configured company, agent and channel. An explicit owner-session grant for the exact reviewed material OR an owner-created time-bounded verified-training policy is required. Policies bind material types, rights, agent/channel and operation quotas; they are revocable. Per-material grants remain single-use. No self-approval, public links, messages or mode changes.'
        if APPLY_SCOPE in scopes: text+=' Also allow existing owner-approved applications: tested prompts, restricted agent settings, disabled triggers, inactive knowledge and library drafts.'
        if DRAFT_SCOPE in scopes: text+=' Also allow creating and editing non-public administrator proposals.'
        if KNOWLEDGE_DEDUP_SCOPE in scopes: text+=' Also allow existing reversible knowledge duplicate maintenance.'
        return text, 'Allow delegated reviewed publication'
    if APPLY_SCOPE in scopes:
        return (
            'Apply access for one configured company agent and channel. ChatGPT may use only an exact, '
            'owner-approved changes: tested prompts, restricted agent settings, disabled triggers, inactive knowledge and Instruction Library drafts. It cannot send messages, '
            'enable Active mode, change budgets, publish knowledge, or modify CRM data.'
        ), 'Allow approved administrator application'
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
                    token_id=uuid.uuid4(); connection_id=uuid.uuid4()
                    refresh=secrets.token_urlsafe(48) if OFFLINE_SCOPE in str(row['scope']).split() else None
                    cur.execute('''INSERT INTO saas_ai_learning_mcp_tokens(id,tenant_id,token_hash,agent_id,channel_id,client_id,expires_at,refresh_token_hash,refresh_expires_at,scope,oauth_connection_id,created_by)
                      VALUES(%s,%s::uuid,%s,%s::uuid,%s::uuid,%s,now()+interval '8 hours',%s,CASE WHEN %s::text IS NULL THEN NULL ELSE now()+interval '30 days' END,%s,%s,%s)''',(token_id,row['tenant_id'],_hash(secrets.token_urlsafe(48)),row['agent_id'],row['channel_id'],client_id,_hash(refresh) if refresh else None,refresh,row['scope'],connection_id,row['actor_id']))
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


async def revoke(request):
    """RFC 7009-style revocation for a delegated ChatGPT connection.

    The caller proves control of the token; the response deliberately does not
    reveal whether it existed.  Matching access *or* refresh token revokes the
    whole grant, so a previously issued refresh credential cannot recreate it.
    """
    try:
        cfg=_require_cfg(); data=await request.post()
        client_id=str(data.get('client_id','')); _chatgpt_client(client_id)
        value=str(data.get('token',''))
        if not value or len(value)>500: raise TenantLinearError('Invalid token.',400)
        hint=str(data.get('token_type_hint',''))
        if hint not in {'','access_token','refresh_token'}: raise TenantLinearError('Invalid token type.',400)
        digest=_hash(value)
        with _connect() as conn:
            ensure_mcp_schema(conn)
            with conn.cursor() as cur:
                where="tenant_id=%s::uuid AND agent_id=%s::uuid AND channel_id=%s::uuid AND client_id=%s AND revoked_at IS NULL"
                params=[cfg['AI_LEARNING_MCP_TENANT_ID'],cfg['AI_LEARNING_MCP_AGENT_ID'],cfg['AI_LEARNING_MCP_CHANNEL_ID'],client_id]
                if hint=='access_token': where+=' AND token_hash=%s';params.append(digest)
                elif hint=='refresh_token': where+=' AND refresh_token_hash=%s';params.append(digest)
                else: where+=' AND (token_hash=%s OR refresh_token_hash=%s)';params.extend([digest,digest])
                cur.execute('UPDATE saas_ai_learning_mcp_tokens SET revoked_at=now() WHERE '+where+' RETURNING id,tenant_id,created_by',tuple(params))
                row=cur.fetchone()
                if row: _audit(cur,row['tenant_id'],row['created_by'],'ai_learning_mcp_token_revoked','mcp_token',row['id'],{'client_id':client_id[:120]})
            conn.commit()
        return web.Response(status=200,headers={'Cache-Control':'no-store'})
    except TenantLinearError as exc:
        return web.json_response({'error':'invalid_request','error_description':str(exc)},status=exc.status,headers={'Cache-Control':'no-store'})


def routes(member_from_request):
    async def protected(request):
        try:
            cfg=_require_cfg()
            return web.json_response({'resource':cfg['issuer']+'/mcp/ai-learning','authorization_servers':[cfg['issuer']], 'scopes_supported':[TOOL_SCOPE,DRAFT_SCOPE,KNOWLEDGE_DEDUP_SCOPE,APPLY_SCOPE,PUBLISH_SCOPE,DIALOGUE_MERGE_SCOPE,direct_reconcile.WRITE_SCOPE,OFFLINE_SCOPE]})
        except TenantLinearError as exc: return web.json_response({'error':str(exc)},status=exc.status)
    async def metadata(request):
        try:
            cfg=_require_cfg(); base=cfg['issuer']
            return web.json_response({'issuer':base,'authorization_endpoint':base+'/oauth/authorize','token_endpoint':base+'/oauth/token','revocation_endpoint':base+'/oauth/revoke','code_challenge_methods_supported':['S256'],'scopes_supported':[TOOL_SCOPE,DRAFT_SCOPE,KNOWLEDGE_DEDUP_SCOPE,APPLY_SCOPE,PUBLISH_SCOPE,DIALOGUE_MERGE_SCOPE,direct_reconcile.WRITE_SCOPE,OFFLINE_SCOPE],'response_types_supported':['code'],'grant_types_supported':['authorization_code','refresh_token'],'token_endpoint_auth_methods_supported':['none'],'client_id_metadata_document_supported':True})
        except TenantLinearError as exc: return web.json_response({'error':str(exc)},status=exc.status)
    return [('POST','/mcp/ai-learning',mcp),('GET','/mcp/ai-learning',mcp),('GET','/.well-known/oauth-protected-resource/mcp/ai-learning',protected),('GET','/.well-known/oauth-authorization-server',metadata),('GET','/oauth/authorize',lambda req:authorize(req,member_from_request)),('POST','/oauth/authorize',lambda req:authorize(req,member_from_request)),('POST','/oauth/token',token),('POST','/oauth/revoke',revoke)]


def handler(member_from_request):
    async def route(request):
        if request.path.endswith('/mcp'): return await mcp(request)
        if request.path.endswith('/oauth-protected-resource'):
            cfg=_require_cfg(); return web.json_response({'resource':cfg['issuer']+'/mcp','authorization_servers':[cfg['issuer']]})
        return web.json_response({'error':'Not implemented'},status=404)
    return route


def _trigger_definitions():
    props={'agent_id':{'type':'string'},'channel_id':{'type':'string'},'expected_agent_version':{'type':'integer','minimum':1},'trigger':{'type':'object'}}
    rows=[('administrator_draft_trigger_create',props,DRAFT_SCOPE)]
    rows += [(name,{'approval_id':{'type':'string'},**({'draft_id':{'type':'string'}} if 'apply_' in name else {})},APPLY_SCOPE) for name in ('administrator_apply_trigger_draft','administrator_rollback_trigger_draft')]
    return [{'name':name,'description':'Owner-scoped disabled trigger proposal/application. No activation or CRM actions. Rollback preserves a disabled historical trigger.','inputSchema':{'type':'object','properties':properties,'required':list(properties),'additionalProperties':False},'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[scope]}]} for name,properties,scope in rows]


def _config_definitions():
    props={'agent_id':{'type':'string'},'channel_id':{'type':'string'},'expected_agent_version':{'type':'integer','minimum':1},'patch':{'type':'object','properties':{'language':{'type':'string'},'style':{'type':'string'},'response_policy':{'type':'object'},'expert_policy':{'type':'object'}},'additionalProperties':False}}
    rows=[('administrator_draft_agent_config_create',props,DRAFT_SCOPE)]
    rows += [(name,{'approval_id':{'type':'string'},**({'draft_id':{'type':'string'}} if 'apply_' in name else {})},APPLY_SCOPE) for name in ('administrator_apply_agent_config','administrator_rollback_agent_config')]
    return [{'name':name,'description':'Owner-approved allowlisted agent configuration. Cannot change modes, scope, budgets, CRM permissions or expand Expert permissions.','inputSchema':{'type':'object','properties':properties,'required':list(properties),'additionalProperties':False},'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[scope]}]} for name,properties,scope in rows]


def _replay_preflight_definitions():
    return [{'name':'administrator_historical_replay_candidates','description':'Owner-only bounded eligible direct customer text selection, or per-message rejection diagnosis. Returns message UUIDs and reason codes only; no texts, phone numbers, names or paid calls. Suggested five IDs still require fresh preflight and separate owner approval.',
      'inputSchema':{'type':'object','properties':{'message_ids':{'type':'array','minItems':1,'maxItems':25,'uniqueItems':True,'items':{'type':'string','format':'uuid'}}},'additionalProperties':False},
      'annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[TOOL_SCOPE]}]},{'name':'administrator_historical_replay_preflight','description':'Owner-only read-only preflight for exactly five existing pilot message IDs. Checks model, scope and the shared $5 ledger; never calls a model or executes replay. Request separate owner confirmation before paid execution through the existing replay endpoint.',
      'inputSchema':{'type':'object','properties':{'message_ids':{'type':'array','minItems':5,'maxItems':5,'uniqueItems':True,'items':{'type':'string','format':'uuid'}}},'required':['message_ids'],'additionalProperties':False},
      'annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[TOOL_SCOPE]}]}]
