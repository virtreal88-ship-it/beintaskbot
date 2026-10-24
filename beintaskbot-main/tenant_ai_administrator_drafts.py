"""Feature-gated, tenant-owned drafts for the Autonomous Administrator.

Drafts are proposals only.  This module never invokes a model or applies a
proposal to an agent, knowledge source, case, channel, or customer dialogue.
"""
import json
import os
import uuid

from tenant_ai_agents_store import _audit, _authorize
from tenant_linear_policy import TenantLinearError

DRAFT_SCOPE = 'ai_learning.drafts'
KINDS = {'agent_improvement', 'knowledge', 'support_case', 'testing_scenario'}


def enabled():
    return str(os.environ.get('AI_LEARNING_MCP_DRAFTS_ENABLED') or '').lower() == 'true'


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_administrator_drafts(
          id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          kind TEXT NOT NULL, title TEXT NOT NULL, body JSONB NOT NULL, target JSONB NOT NULL DEFAULT '{}'::jsonb,
          state TEXT NOT NULL DEFAULT 'draft', version INTEGER NOT NULL DEFAULT 1,
          created_by BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK(kind IN ('agent_improvement','knowledge','support_case','testing_scenario')),
          CHECK(state='draft'))''')
        cur.execute('CREATE INDEX IF NOT EXISTS saas_ai_administrator_drafts_tenant_idx ON saas_ai_administrator_drafts(tenant_id,updated_at DESC)')


def definitions():
    schema={'type':'object','properties':{'id':{'type':'string'},'kind':{'type':'string','enum':sorted(KINDS)},'title':{'type':'string','maxLength':240},'body':{'type':'object'},'target':{'type':'object'},'expected_version':{'type':'integer','minimum':1},'limit':{'type':'integer','minimum':1,'maximum':25}},'additionalProperties':False}
    return [
      {'name':'administrator_draft_create','description':'Create a non-public proposal. It cannot apply or publish changes.','inputSchema':schema,'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}]},
      {'name':'administrator_draft_list','description':'List non-public proposals for this company.','inputSchema':{'type':'object','properties':{'limit':schema['properties']['limit']},'additionalProperties':False},'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}]},
      {'name':'administrator_draft_update','description':'Edit a non-public proposal with optimistic versioning. It cannot apply or publish changes.','inputSchema':schema,'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}]},
    ]


def _guard(cfg):
    if DRAFT_SCOPE not in set(cfg.get('granted_scopes') or []): raise TenantLinearError('Additional OAuth scope required.',403)
    if not enabled(): raise TenantLinearError('Administrator drafts are disabled.',403)


def _valid(data, creating):
    allowed={'kind','title','body','target'} if creating else {'id','title','body','target','expected_version'}
    if not isinstance(data,dict) or set(data)-allowed: raise TenantLinearError('Invalid draft.',400)
    if creating and data.get('kind') not in KINDS: raise TenantLinearError('Invalid draft kind.',400)
    if not creating:
        try: uuid.UUID(str(data.get('id')))
        except (TypeError,ValueError): raise TenantLinearError('Invalid draft.',400) from None
        if type(data.get('expected_version')) is not int: raise TenantLinearError('Draft version is required.',409)
    if 'title' in data and (not isinstance(data['title'],str) or not data['title'].strip() or len(data['title'])>240): raise TenantLinearError('Invalid draft title.',400)
    for key in ('body','target'):
        if key in data and (not isinstance(data[key],dict) or len(json.dumps(data[key],ensure_ascii=False))>24000): raise TenantLinearError('Invalid draft body.',400)


def create(cur,cfg,data):
    _guard(cfg);_valid(data,True); tenant,actor=_authorize(cur,{'tenant_id':cfg['AI_LEARNING_MCP_TENANT_ID'],'telegram_id':cfg['actor_id']})
    ident=uuid.uuid4(); cur.execute('''INSERT INTO saas_ai_administrator_drafts(id,tenant_id,kind,title,body,target,created_by)
      VALUES(%s,%s::uuid,%s,%s,%s::jsonb,%s::jsonb,%s) RETURNING *''',(ident,tenant,data['kind'],data['title'].strip(),json.dumps(data.get('body') or {}),json.dumps(data.get('target') or {}),actor))
    row=dict(cur.fetchone()); _audit(cur,tenant,actor,'ai_administrator_draft_created','ai_administrator_draft',ident,{'kind':row['kind']}); return public(row)


def listing(cur,cfg,data):
    _guard(cfg)
    if set(data)-{'limit'} or ('limit' in data and (type(data['limit']) is not int or not 1<=data['limit']<=25)): raise TenantLinearError('Invalid draft list.',400)
    cur.execute('SELECT * FROM saas_ai_administrator_drafts WHERE tenant_id=%s::uuid ORDER BY updated_at DESC,id DESC LIMIT %s',(cfg['AI_LEARNING_MCP_TENANT_ID'],data.get('limit',10)))
    return {'items':[public(dict(row)) for row in cur.fetchall()]}


def update(cur,cfg,data):
    _guard(cfg);_valid(data,False); tenant,actor=_authorize(cur,{'tenant_id':cfg['AI_LEARNING_MCP_TENANT_ID'],'telegram_id':cfg['actor_id']})
    cur.execute('SELECT * FROM saas_ai_administrator_drafts WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,data['id'])); row=cur.fetchone()
    if not row: raise TenantLinearError('Draft not found.',404)
    row=dict(row)
    if row['version']!=data['expected_version']: raise TenantLinearError('Draft changed; reload before saving.',409)
    title=data.get('title',row['title']).strip() if isinstance(data.get('title',row['title']),str) else row['title']; body=data.get('body',row['body']); target=data.get('target',row['target'])
    cur.execute('''UPDATE saas_ai_administrator_drafts SET title=%s,body=%s::jsonb,target=%s::jsonb,version=version+1,updated_at=now()
      WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING *''',(title,json.dumps(body),json.dumps(target),tenant,data['id']))
    result=public(dict(cur.fetchone())); _audit(cur,tenant,actor,'ai_administrator_draft_updated','ai_administrator_draft',data['id'],{}); return result


def public(row):
    return {key:(str(value) if isinstance(value,uuid.UUID) else value.isoformat() if hasattr(value,'isoformat') else value) for key,value in row.items() if key in {'id','kind','title','body','target','state','version','created_at','updated_at'}}
