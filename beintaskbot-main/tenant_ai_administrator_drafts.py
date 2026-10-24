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
          CHECK(state IN ('draft','applied','rolled_back')))''')
        cur.execute('ALTER TABLE saas_ai_administrator_drafts DROP CONSTRAINT IF EXISTS saas_ai_administrator_drafts_state_check')
        cur.execute("ALTER TABLE saas_ai_administrator_drafts ADD CONSTRAINT saas_ai_administrator_drafts_state_check CHECK(state IN ('draft','applied','rolled_back'))")
        cur.execute('CREATE INDEX IF NOT EXISTS saas_ai_administrator_drafts_tenant_idx ON saas_ai_administrator_drafts(tenant_id,updated_at DESC)')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_administrator_draft_versions(
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          draft_id UUID NOT NULL REFERENCES saas_ai_administrator_drafts(id) ON DELETE CASCADE,
          version INTEGER NOT NULL, title TEXT NOT NULL, body JSONB NOT NULL, target JSONB NOT NULL,
          created_by BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,draft_id,version))''')
        # Additive backfill preserves the current version of records created
        # before immutable snapshots existed. It never overwrites history.
        cur.execute('''INSERT INTO saas_ai_administrator_draft_versions(tenant_id,draft_id,version,title,body,target,created_by,created_at)
          SELECT tenant_id,id,version,title,body,target,created_by,updated_at FROM saas_ai_administrator_drafts
          ON CONFLICT(tenant_id,draft_id,version) DO NOTHING''')


def definitions():
    schema={'type':'object','properties':{'id':{'type':'string'},'kind':{'type':'string','enum':sorted(KINDS)},'title':{'type':'string','maxLength':240},'body':{'type':'object'},'target':{'type':'object'},'expected_version':{'type':'integer','minimum':1},'limit':{'type':'integer','minimum':1,'maximum':25}},'additionalProperties':False}
    return [
      {'name':'administrator_draft_create','description':'Create a non-public proposal. It cannot apply or publish changes.','inputSchema':schema,'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}]},
      {'name':'administrator_draft_list','description':'List non-public proposals for this company.','inputSchema':{'type':'object','properties':{'limit':schema['properties']['limit']},'additionalProperties':False},'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}]},
      {'name':'administrator_draft_update','description':'Edit a non-public proposal with optimistic versioning. It cannot apply or publish changes.','inputSchema':schema,'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}]},
      {'name':'administrator_draft_versions','description':'Read immutable snapshots of one non-public proposal. It cannot apply or publish changes.','inputSchema':{'type':'object','properties':{'id':{'type':'string'}},'required':['id'],'additionalProperties':False},'annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}]},
      {'name':'administrator_draft_restore','description':'Restore one earlier immutable version as a new non-public draft version. It cannot apply or publish changes.','inputSchema':{'type':'object','properties':{'id':{'type':'string'},'version':{'type':'integer','minimum':1},'expected_version':{'type':'integer','minimum':1}},'required':['id','version','expected_version'],'additionalProperties':False},'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}]},
      {'name':'administrator_draft_agent_prompt_create','description':'Prepare a tested non-public prompt patch for one existing agent. It cannot apply the patch.','inputSchema':{'type':'object','properties':{'title':{'type':'string','maxLength':240},'agent_id':{'type':'string'},'expected_version':{'type':'integer','minimum':1},'instructions':{'type':'string','maxLength':24000},'testing_turn_id':{'type':'string'}},'required':['title','agent_id','expected_version','instructions','testing_turn_id'],'additionalProperties':False},'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}]},
      {'name':'administrator_draft_knowledge_create','description':'Prepare non-public knowledge with explicit evidence references. It cannot publish or bind knowledge.','inputSchema':{'type':'object','properties':{'title':{'type':'string','maxLength':240},'content':{'type':'string','maxLength':24000},'evidence_ids':{'type':'array','items':{'type':'string'},'maxItems':50},'agent_ids':{'type':'array','items':{'type':'string'},'maxItems':50}},'required':['title','content','evidence_ids'],'additionalProperties':False},'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}]},
      {'name':'administrator_draft_support_case_create','description':'Prepare a non-public support-case proposal with evidence IDs. It cannot publish a case.','inputSchema':{'type':'object','properties':{'title':{'type':'string','maxLength':240},'problem':{'type':'string','maxLength':12000},'evidence_message_ids':{'type':'array','items':{'type':'string'},'maxItems':50}},'required':['title','problem','evidence_message_ids'],'additionalProperties':False},'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}]},
      {'name':'administrator_draft_testing_scenario_create','description':'Prepare a synthetic AI Testing scenario. It cannot run a model or send a message.','inputSchema':{'type':'object','properties':{'title':{'type':'string','maxLength':240},'synthetic_input':{'type':'string','maxLength':8000},'expected':{'type':'string','maxLength':8000}},'required':['title','synthetic_input','expected'],'additionalProperties':False},'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}]},
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


def _uuid_list(value, *, limit=50):
    if not isinstance(value,list) or len(value)>limit: raise TenantLinearError('Invalid evidence references.',400)
    try: result=[str(uuid.UUID(str(item))) for item in value]
    except (TypeError,ValueError): raise TenantLinearError('Invalid evidence references.',400) from None
    if len(result)!=len(set(result)): raise TenantLinearError('Duplicate evidence reference.',400)
    return result


def _message_evidence(cur, tenant, value):
    """Accept only existing tenant-owned message evidence, never opaque IDs."""
    evidence=_uuid_list(value)
    if not evidence:
        raise TenantLinearError('At least one evidence message is required.',400)
    cur.execute('''SELECT id FROM saas_crm_messages
      WHERE tenant_id=%s::uuid AND id=ANY(%s::uuid[])''',(tenant,evidence))
    found={str(row['id']) for row in cur.fetchall()}
    if found != set(evidence):
        raise TenantLinearError('Evidence message is unavailable for this company.',404)
    return evidence


def _tested_prompt(cur, tenant, agent_id, expected_version, test_id):
    """A proposal must already be backed by a matching isolated test."""
    cur.execute('''SELECT t.id FROM saas_ai_test_turns t
      JOIN saas_ai_test_sessions s ON s.tenant_id=t.tenant_id AND s.id=t.session_id
      WHERE t.tenant_id=%s::uuid AND t.id=%s::uuid AND t.state='completed'
        AND s.agent_id=%s::uuid AND COALESCE(t.snapshot->>'agent_version','')=%s
      FOR SHARE''',(tenant,test_id,agent_id,str(expected_version)))
    if not cur.fetchone():
        raise TenantLinearError('A completed isolated test for this exact agent version is required.',409)


def typed_create(cur,cfg,name,data):
    """Narrow proposal contracts layered on the common immutable draft store."""
    _guard(cfg)
    tenant,_=_authorize(cur,{'tenant_id':cfg['AI_LEARNING_MCP_TENANT_ID'],'telegram_id':cfg['actor_id']})
    if name=='agent_prompt':
        if set(data)!={'title','agent_id','expected_version','instructions','testing_turn_id'} or type(data.get('expected_version')) is not int or not isinstance(data.get('instructions'),str): raise TenantLinearError('Invalid agent prompt proposal.',400)
        try: agent_id=str(uuid.UUID(str(data['agent_id']))); test_id=str(uuid.UUID(str(data['testing_turn_id'])))
        except (TypeError,ValueError): raise TenantLinearError('Invalid agent prompt proposal.',400) from None
        cur.execute('SELECT 1 FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL',(tenant,agent_id))
        if not cur.fetchone(): raise TenantLinearError('Agent is unavailable for this company.',404)
        _tested_prompt(cur,tenant,agent_id,data['expected_version'],test_id)
        body={'agent_id':agent_id,'expected_version':data['expected_version'],'instructions':data['instructions'],'testing_turn_id':test_id}
        return create(cur,cfg,{'kind':'agent_improvement','title':data['title'],'body':body,'target':{'agent_id':agent_id}})
    if name=='knowledge':
        if set(data)-{'title','content','evidence_ids','agent_ids'} or not isinstance(data.get('content'),str) or not data.get('content').strip(): raise TenantLinearError('Invalid knowledge proposal.',400)
        evidence=_message_evidence(cur,tenant,data.get('evidence_ids')); agents=_uuid_list(data.get('agent_ids',[]))
        if agents:
            cur.execute('SELECT count(*) AS n FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=ANY(%s::uuid[]) AND archived_at IS NULL',(tenant,agents))
            if int(cur.fetchone()['n'])!=len(agents): raise TenantLinearError('Knowledge target is unavailable for this company.',404)
        return create(cur,cfg,{'kind':'knowledge','title':data.get('title'),'body':{'content':data['content'].strip(),'evidence_ids':evidence},'target':{'agent_ids':agents}})
    if name=='support_case':
        if set(data)!={'title','problem','evidence_message_ids'} or not isinstance(data.get('problem'),str) or not data.get('problem').strip(): raise TenantLinearError('Invalid support case proposal.',400)
        return create(cur,cfg,{'kind':'support_case','title':data['title'],'body':{'problem':data['problem'].strip(),'evidence_message_ids':_message_evidence(cur,tenant,data['evidence_message_ids'])},'target':{}})
    if name=='testing_scenario':
        if set(data)!={'title','synthetic_input','expected'} or any(not isinstance(data.get(key),str) or not data[key].strip() for key in ('title','synthetic_input','expected')): raise TenantLinearError('Invalid testing scenario proposal.',400)
        if len(data['synthetic_input'])>8000 or len(data['expected'])>8000: raise TenantLinearError('Invalid testing scenario proposal.',400)
        return create(cur,cfg,{'kind':'testing_scenario','title':data['title'],'body':{'synthetic_input':data['synthetic_input'].strip(),'expected':data['expected'].strip()},'target':{'synthetic':True}})
    raise TenantLinearError('Invalid draft kind.',400)


def create(cur,cfg,data):
    _guard(cfg);_valid(data,True); tenant,actor=_authorize(cur,{'tenant_id':cfg['AI_LEARNING_MCP_TENANT_ID'],'telegram_id':cfg['actor_id']})
    ident=uuid.uuid4(); cur.execute('''INSERT INTO saas_ai_administrator_drafts(id,tenant_id,kind,title,body,target,created_by)
      VALUES(%s,%s::uuid,%s,%s,%s::jsonb,%s::jsonb,%s) RETURNING *''',(ident,tenant,data['kind'],data['title'].strip(),json.dumps(data.get('body') or {}),json.dumps(data.get('target') or {}),actor))
    row=dict(cur.fetchone()); snapshot(cur,tenant,row,actor); _audit(cur,tenant,actor,'ai_administrator_draft_created','ai_administrator_draft',ident,{'kind':row['kind']}); return public(row)


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
    current=dict(cur.fetchone()); snapshot(cur,tenant,current,actor); result=public(current); _audit(cur,tenant,actor,'ai_administrator_draft_updated','ai_administrator_draft',data['id'],{}); return result


def versions(cur,cfg,data):
    _guard(cfg)
    if not isinstance(data,dict) or set(data)!={'id'}:
        raise TenantLinearError('Invalid draft history.',400)
    try: draft_id=str(uuid.UUID(str(data['id'])))
    except (TypeError,ValueError): raise TenantLinearError('Invalid draft history.',400) from None
    cur.execute('''SELECT draft_id AS id,version,title,body,target,created_at FROM saas_ai_administrator_draft_versions
      WHERE tenant_id=%s::uuid AND draft_id=%s::uuid ORDER BY version''',(cfg['AI_LEARNING_MCP_TENANT_ID'],draft_id))
    return {'items':[public(dict(row)) for row in cur.fetchall()]}


def restore(cur,cfg,data):
    """Revert draft content without mutating an applied business object."""
    _guard(cfg)
    if (not isinstance(data,dict) or set(data)!={'id','version','expected_version'}
            or type(data.get('version')) is not int or type(data.get('expected_version')) is not int):
        raise TenantLinearError('Invalid draft restore.',400)
    try: draft_id=str(uuid.UUID(str(data['id'])))
    except (TypeError,ValueError): raise TenantLinearError('Invalid draft restore.',400) from None
    tenant,actor=_authorize(cur,{'tenant_id':cfg['AI_LEARNING_MCP_TENANT_ID'],'telegram_id':cfg['actor_id']})
    cur.execute('SELECT id,state,version FROM saas_ai_administrator_drafts WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,draft_id))
    draft=cur.fetchone()
    if not draft or draft['state']!='draft' or draft['version']!=data['expected_version']:
        raise TenantLinearError('Draft changed; reload before restoring.',409)
    cur.execute('''SELECT title,body,target FROM saas_ai_administrator_draft_versions
      WHERE tenant_id=%s::uuid AND draft_id=%s::uuid AND version=%s FOR SHARE''',(tenant,draft_id,data['version']))
    source=cur.fetchone()
    if not source: raise TenantLinearError('Draft version is unavailable.',404)
    cur.execute('''UPDATE saas_ai_administrator_drafts SET title=%s,body=%s::jsonb,target=%s::jsonb,
      version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING *''',
      (source['title'],json.dumps(source['body']),json.dumps(source['target']),tenant,draft_id))
    restored=dict(cur.fetchone()); snapshot(cur,tenant,restored,actor)
    _audit(cur,tenant,actor,'ai_administrator_draft_restored','ai_administrator_draft',draft_id,{'from_version':data['version'],'to_version':restored['version']})
    return public(restored)


def snapshot(cur,tenant,row,actor):
    cur.execute('''INSERT INTO saas_ai_administrator_draft_versions(tenant_id,draft_id,version,title,body,target,created_by)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s::jsonb,%s::jsonb,%s) ON CONFLICT(tenant_id,draft_id,version) DO NOTHING''',
      (tenant,row['id'],row['version'],row['title'],json.dumps(row['body']),json.dumps(row['target']),actor))


def public(row):
    return {key:(str(value) if isinstance(value,uuid.UUID) else value.isoformat() if hasattr(value,'isoformat') else value) for key,value in row.items() if key in {'id','kind','title','body','target','state','version','created_at','updated_at'}}
