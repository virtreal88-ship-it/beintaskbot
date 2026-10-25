"""Transactional, deliberately narrow application of owner-approved drafts."""
import os
import uuid
import json

from tenant_ai_agents_store import _audit, _authorize, save_knowledge
from tenant_ai_agents_policy import agent as validate_agent
from tenant_linear_policy import TenantLinearError
from tenant_ai_administrator_approvals import consume, publication_assert_detached
from tenant_instruction_library import document as instruction_document, schema as instruction_schema, _assets as instruction_assets, _video as instruction_video


def enabled(): return str(os.environ.get('AI_AUTONOMOUS_APPLY_ENABLED') or '').lower() == 'true'


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_administrator_rollbacks(
          id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          draft_id UUID NOT NULL, approval_id UUID NOT NULL UNIQUE, agent_id UUID NOT NULL,
          before_instructions TEXT NOT NULL, before_version INTEGER NOT NULL, applied_version INTEGER NOT NULL,
          rolled_back_at TIMESTAMPTZ NULL, created_by BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_administrator_material_rollbacks(
          id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          kind TEXT NOT NULL CHECK(kind IN ('knowledge','instruction')), draft_id UUID NOT NULL, approval_id UUID NOT NULL UNIQUE,
          object_id UUID NOT NULL, applied_version INTEGER NULL, rolled_back_at TIMESTAMPTZ NULL, created_by BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')

        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_administrator_trigger_rollbacks(
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id), approval_id UUID PRIMARY KEY, draft_id UUID NOT NULL,
          agent_id UUID NOT NULL, trigger_id UUID NOT NULL, applied_version INTEGER NOT NULL, rolled_back_at TIMESTAMPTZ)''')

        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_administrator_config_rollbacks(
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id), approval_id UUID PRIMARY KEY, draft_id UUID NOT NULL,
          agent_id UUID NOT NULL, channel_id UUID NOT NULL, before_patch JSONB NOT NULL, applied_version INTEGER NOT NULL, rolled_back_at TIMESTAMPTZ)''')


def _guard(cur, profile, *, scoped_training_policy_id=None):
    # A scoped policy is checked and row-locked by training_operation before
    # entering this helper.  It may only bypass the broad legacy apply switch
    # for the two private material preparation paths below; it never covers
    # prompts, triggers, agent configuration, messages, CRM or a public API.
    if scoped_training_policy_id is None and not enabled():
        raise TenantLinearError('Autonomous application is disabled.',403)
    tenant, actor = _authorize(cur,profile)
    # Application changes a live agent.  A receipt is not sufficient unless
    # the same authenticated company owner is performing the action.
    cur.execute('''SELECT role,active FROM saas_tenant_members
      WHERE tenant_id=%s::uuid AND telegram_id=%s FOR SHARE''', (tenant, actor))
    member = cur.fetchone()
    if not member or member['role'] != 'owner' or member.get('active') is False:
        raise TenantLinearError('Only the company owner can apply or roll back administrator changes.',403)
    return tenant,actor


def _scoped_training_receipt(cur, tenant, approval_id, policy_id):
    """Bind the narrow switch bypass to the exact internal policy receipt."""
    if policy_id is None:
        return
    try:
        policy_id=str(uuid.UUID(str(policy_id)))
    except (ValueError,TypeError):
        raise TenantLinearError('Invalid training policy.',400) from None
    cur.execute('''SELECT id FROM saas_ai_administrator_approvals
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND delegation_policy_id=%s::uuid
      FOR SHARE''',(tenant,approval_id,policy_id))
    if not cur.fetchone():
        raise TenantLinearError('Training policy receipt is unavailable.',403)


def apply_agent_prompt(cur, profile, approval_id, draft_id):
    """Apply only an instruction-string patch guarded by receipt and versions."""
    tenant,actor=_guard(cur,profile)
    try: draft_id=str(uuid.UUID(str(draft_id))); approval_id=str(uuid.UUID(str(approval_id)))
    except (ValueError,TypeError): raise TenantLinearError('Invalid approval request.',400) from None
    cur.execute('SELECT * FROM saas_ai_administrator_drafts WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,draft_id)); draft=cur.fetchone()
    if not draft or draft['kind']!='agent_improvement' or draft['state']!='draft': raise TenantLinearError('Agent improvement draft is unavailable.',409)
    body=draft['body'] or {}; allowed={'agent_id','expected_version','instructions','testing_turn_id'}
    if not isinstance(body,dict) or set(body)!=allowed or type(body.get('expected_version')) is not int or not isinstance(body.get('instructions'),str): raise TenantLinearError('Invalid agent improvement draft.',400)
    patch=validate_agent({'instructions':body['instructions']},creating=False)
    try: agent_id=str(uuid.UUID(str(body['agent_id']))); testing_turn_id=str(uuid.UUID(str(body['testing_turn_id'])))
    except (ValueError,TypeError): raise TenantLinearError('Invalid agent improvement draft.',400) from None
    cur.execute('SELECT id,instructions,version FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR UPDATE',(tenant,agent_id)); agent=cur.fetchone()
    if not agent or agent['version']!=body['expected_version']: raise TenantLinearError('Agent changed; review the draft again.',409)
    # A production prompt patch requires a completed, private AI Testing turn
    # against this exact agent version. This excludes real dialogues and any
    # caller-supplied claim that a test passed.
    cur.execute('''SELECT t.id FROM saas_ai_test_turns t
      JOIN saas_ai_test_sessions s ON s.tenant_id=t.tenant_id AND s.id=t.session_id
      WHERE t.tenant_id=%s::uuid AND t.id=%s::uuid AND t.state='completed'
        AND s.agent_id=%s::uuid AND COALESCE(t.snapshot->>'agent_version','')=%s
      FOR SHARE''',(tenant,testing_turn_id,agent_id,str(body['expected_version'])))
    if not cur.fetchone(): raise TenantLinearError('A completed isolated test for this exact agent version is required.',409)
    consume(cur,tenant,approval_id,draft_id,draft['version'],'agent_prompt_apply')
    cur.execute('''UPDATE saas_ai_agents SET instructions=%s,version=version+1,updated_at=now()
      WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING version''',(patch['instructions'],tenant,agent_id)); version=cur.fetchone()['version']
    cur.execute('''INSERT INTO saas_ai_administrator_rollbacks(id,tenant_id,draft_id,approval_id,agent_id,before_instructions,before_version,applied_version,created_by)
      VALUES(%s,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s)''',(uuid.uuid4(),tenant,draft_id,approval_id,agent_id,agent['instructions'],agent['version'],version,actor))
    cur.execute("UPDATE saas_ai_administrator_drafts SET state='applied',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,draft_id))
    _audit(cur,tenant,actor,'ai_administrator_agent_prompt_applied','ai_agent',agent_id,{'draft_id':draft_id,'approval_id':approval_id,'version':version,'testing_turn_id':testing_turn_id})
    return {'agent_id':agent_id,'version':version,'rollback_available':True}


def rollback_agent_prompt(cur, profile, approval_id):
    tenant,actor=_guard(cur,profile)
    cur.execute('SELECT * FROM saas_ai_administrator_rollbacks WHERE tenant_id=%s::uuid AND approval_id=%s::uuid FOR UPDATE',(tenant,approval_id)); row=cur.fetchone()
    if not row or row['rolled_back_at'] is not None: raise TenantLinearError('Rollback is unavailable.',409)
    cur.execute('''UPDATE saas_ai_agents SET instructions=%s,version=version+1,updated_at=now()
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND version=%s RETURNING version''',(row['before_instructions'],tenant,row['agent_id'],row['applied_version']))
    value=cur.fetchone()
    if not value: raise TenantLinearError('Agent changed after application; rollback requires a new review.',409)
    cur.execute('UPDATE saas_ai_administrator_rollbacks SET rolled_back_at=now() WHERE tenant_id=%s::uuid AND approval_id=%s::uuid',(tenant,approval_id))
    _audit(cur,tenant,actor,'ai_administrator_agent_prompt_rolled_back','ai_agent',row['agent_id'],{'approval_id':str(approval_id)})
    return {'agent_id':str(row['agent_id']),'version':value['version'],'rolled_back':True}


def _ids(approval_id,draft_id):
    try: return str(uuid.UUID(str(approval_id))),str(uuid.UUID(str(draft_id)))
    except (ValueError,TypeError): raise TenantLinearError('Invalid approval request.',400) from None

def apply_knowledge_draft(cur,profile,approval_id,draft_id,*,scoped_training_policy_id=None):
    tenant,actor=_guard(cur,profile,scoped_training_policy_id=scoped_training_policy_id); approval_id,draft_id=_ids(approval_id,draft_id)
    _scoped_training_receipt(cur,tenant,approval_id,scoped_training_policy_id)
    cur.execute('SELECT * FROM saas_ai_administrator_drafts WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,draft_id)); draft=cur.fetchone()
    if not draft or draft['kind']!='knowledge' or draft['state']!='draft': raise TenantLinearError('Knowledge draft is unavailable.',409)
    body=draft['body'] or {}; target=draft['target'] or {}
    evidence_mode=set(body)=={'content','evidence_ids'}
    owner_mode=set(body)=={'content','owner_provided'} and isinstance(body.get('owner_provided'),dict)
    if (not (evidence_mode or owner_mode) or not isinstance(body.get('content'),str) or not body['content'].strip() or not {'agent_ids'}<=set(target) or bool(set(target)-{'agent_ids','review_run_id','review_request_key','source_eligibility'}) or not isinstance(target['agent_ids'],list)):
        raise TenantLinearError('Invalid knowledge draft.',400)
    if owner_mode and (target.get('source_eligibility')!='owner_provided_unverified' or set(body['owner_provided'])!={'note','verified_by'} or body['owner_provided'].get('verified_by')!=actor or not isinstance(body['owner_provided'].get('note'),str) or not body['owner_provided']['note'].strip()):
        raise TenantLinearError('Owner-provided knowledge provenance is invalid.',409)
    consume(cur,tenant,approval_id,draft_id,draft['version'],'knowledge_draft_apply')
    source=save_knowledge({'tenant_id':tenant,'telegram_id':actor},{'title':draft['title'],'kind':'text','content':body['content'].strip(),'active':False},connection=cur.connection)
    sid=str(source['id']); provenance={'administrator_draft_id':draft_id,'approval_id':approval_id,'review_only':True,**({'evidence_ids':body['evidence_ids']} if evidence_mode else {'source_type':'owner_provided','verified':False,'owner_note':body['owner_provided']['note']}),**{k:target[k] for k in ('review_run_id','review_request_key') if k in target}}
    cur.execute("UPDATE saas_ai_knowledge_sources SET provenance=COALESCE(provenance,'{}'::jsonb)||%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(json.dumps(provenance),tenant,sid))
    cur.execute("INSERT INTO saas_ai_administrator_material_rollbacks(id,tenant_id,kind,draft_id,approval_id,object_id,created_by) VALUES(%s,%s::uuid,'knowledge',%s::uuid,%s::uuid,%s::uuid,%s)",(uuid.uuid4(),tenant,draft_id,approval_id,sid,actor))
    cur.execute("UPDATE saas_ai_administrator_drafts SET state='applied',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,draft_id)); _audit(cur,tenant,actor,'ai_administrator_knowledge_applied','knowledge_source',sid,{'draft_id':draft_id,'approval_id':approval_id,'review_only':True})
    return {'source_id':sid,'active':False,'published':False,'agent_bound':False,'rollback_available':True}

def rollback_knowledge_draft(cur,profile,approval_id):
    tenant,actor=_guard(cur,profile); approval_id,_=_ids(approval_id,uuid.uuid4())
    cur.execute("SELECT * FROM saas_ai_administrator_material_rollbacks WHERE tenant_id=%s::uuid AND approval_id=%s::uuid AND kind='knowledge' FOR UPDATE",(tenant,approval_id)); row=cur.fetchone()
    if not row or row.get('rolled_back_at') is not None: raise TenantLinearError('Rollback is unavailable.',409)
    publication_assert_detached(cur,tenant,row['draft_id'])
    cur.execute('''SELECT s.id FROM saas_ai_knowledge_sources s JOIN saas_ai_administrator_drafts d ON d.tenant_id=s.tenant_id AND d.id=%s::uuid
      WHERE s.tenant_id=%s::uuid AND s.id=%s::uuid AND s.active=FALSE AND s.agent_id IS NULL
        AND s.title=d.title AND s.content=d.body->>'content' AND s.provenance->>'approval_id'=%s FOR UPDATE OF s''',(row['draft_id'],tenant,row['object_id'],approval_id))
    if not cur.fetchone(): raise TenantLinearError('Knowledge changed after application; rollback requires a new review.',409)
    cur.execute("UPDATE saas_ai_knowledge_sources SET active=FALSE,provenance=COALESCE(provenance,'{}'::jsonb)||%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(json.dumps({'administrator_rollback':{'approval_id':approval_id}}),tenant,row['object_id']))
    cur.execute('UPDATE saas_ai_administrator_material_rollbacks SET rolled_back_at=now() WHERE id=%s',(row['id'],)); cur.execute("UPDATE saas_ai_administrator_drafts SET state='rolled_back',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,row['draft_id']))
    _audit(cur,tenant,actor,'ai_administrator_knowledge_rolled_back','knowledge_source',row['object_id'],{'approval_id':approval_id}); return {'source_id':str(row['object_id']),'rolled_back':True,'active':False}


def apply_instruction_draft(cur,profile,approval_id,draft_id,*,scoped_training_policy_id=None):
    tenant,actor=_guard(cur,profile,scoped_training_policy_id=scoped_training_policy_id); approval_id,draft_id=_ids(approval_id,draft_id)
    _scoped_training_receipt(cur,tenant,approval_id,scoped_training_policy_id)
    cur.execute('SELECT * FROM saas_ai_administrator_drafts WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,draft_id)); draft=cur.fetchone()
    if not draft or draft['kind']!='instruction' or draft['state']!='draft': raise TenantLinearError('Instruction draft is unavailable.',409)
    body=draft['body'] or {}; target=draft['target'] or {}
    if set(body)!={'doc'} or set(target)!={'expected_version'} or target['expected_version']!=0: raise TenantLinearError('Invalid instruction draft.',400)
    doc=instruction_document(body['doc']); instruction_schema(cur.connection); instruction_assets(cur,tenant,doc); instruction_video(cur,tenant,doc)
    consume(cur,tenant,approval_id,draft_id,draft['version'],'instruction_draft_apply')
    lesson_id=uuid.uuid4()
    cur.execute('SELECT count(*) AS n FROM saas_instruction_lessons WHERE tenant_id=%s::uuid',(tenant,))
    if cur.fetchone()['n']>=200: raise TenantLinearError('Lesson limit',409)
    cur.execute("INSERT INTO saas_instruction_lessons(tenant_id,id,doc) VALUES(%s::uuid,%s::uuid,%s::jsonb)",(tenant,lesson_id,json.dumps(doc)))
    cur.execute("INSERT INTO saas_ai_administrator_material_rollbacks(id,tenant_id,kind,draft_id,approval_id,object_id,applied_version,created_by) VALUES(%s,%s::uuid,'instruction',%s::uuid,%s::uuid,%s::uuid,1,%s)",(uuid.uuid4(),tenant,draft_id,approval_id,lesson_id,actor))
    cur.execute("UPDATE saas_ai_administrator_drafts SET state='applied',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,draft_id)); _audit(cur,tenant,actor,'ai_administrator_instruction_applied','instruction',lesson_id,{'draft_id':draft_id,'approval_id':approval_id,'state':'draft'})
    return {'lesson_id':str(lesson_id),'state':'draft','published':False,'rollback_available':True}

def rollback_instruction_draft(cur,profile,approval_id):
    tenant,actor=_guard(cur,profile); approval_id,_=_ids(approval_id,uuid.uuid4())
    cur.execute("SELECT * FROM saas_ai_administrator_material_rollbacks WHERE tenant_id=%s::uuid AND approval_id=%s::uuid AND kind='instruction' FOR UPDATE",(tenant,approval_id)); row=cur.fetchone()
    if not row or row.get('rolled_back_at') is not None: raise TenantLinearError('Rollback is unavailable.',409)
    publication_assert_detached(cur,tenant,row['draft_id'])
    cur.execute("UPDATE saas_instruction_lessons SET state='archived',archived_at=now(),archived_by=%s,archived_from_state=state,version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='draft' AND version=%s RETURNING version",(actor,tenant,row['object_id'],row['applied_version']))
    if not cur.fetchone(): raise TenantLinearError('Instruction changed after application; rollback requires a new review.',409)
    cur.execute('UPDATE saas_ai_administrator_material_rollbacks SET rolled_back_at=now() WHERE id=%s',(row['id'],)); cur.execute("UPDATE saas_ai_administrator_drafts SET state='rolled_back',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,row['draft_id']))
    _audit(cur,tenant,actor,'ai_administrator_instruction_rolled_back','instruction',row['object_id'],{'approval_id':approval_id}); return {'lesson_id':str(row['object_id']),'rolled_back':True,'state':'archived'}

# Trigger applications share the caller's transaction with the approval receipt.
def validate_trigger_proposal(cur,cfg,data):
    from tenant_ai_agents_policy import trigger as policy
    required={'agent_id','channel_id','expected_agent_version','trigger'}
    if not isinstance(data,dict) or set(data)!=required or type(data.get('expected_agent_version')) is not int:
        raise TenantLinearError('Invalid trigger proposal.',400)
    agent_id=str(uuid.UUID(str(data['agent_id']))); channel_id=str(uuid.UUID(str(data['channel_id'])))
    if agent_id!=cfg['AI_LEARNING_MCP_AGENT_ID'] or channel_id!=cfg['AI_LEARNING_MCP_CHANNEL_ID']:
        raise TenantLinearError('Trigger outside OAuth scope.',403)
    trigger=policy(data['trigger'],creating=True)
    if trigger['enabled'] or set(trigger['actions'])-{'handoff','handoff_after_reply','call_expert'} or 'setup_key' in trigger:
        raise TenantLinearError('Only disabled, non-CRM trigger proposals are permitted.',403)
    cur.execute('SELECT version,scopes,role FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR UPDATE',(cfg['AI_LEARNING_MCP_TENANT_ID'],agent_id)); row=cur.fetchone()
    if not row or row['version']!=data['expected_agent_version'] or row['role']!='primary':
        raise TenantLinearError('Primary changed; review again.',409)
    from tenant_ai_agent_connect import scope_matches
    if not scope_matches(row['scopes'],channel_id,is_group=False): raise TenantLinearError('Channel outside agent scope.',403)
    return {**data,'agent_id':agent_id,'channel_id':channel_id,'trigger':trigger}


def apply_trigger_draft(cur,profile,approval_id,draft_id,cfg):
    from tenant_ai_agents_store import save_trigger
    tenant,actor=_guard(cur,profile); approval_id,draft_id=_ids(approval_id,draft_id)
    cur.execute('SELECT * FROM saas_ai_administrator_drafts WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,draft_id)); draft=cur.fetchone()
    if not draft or draft['kind']!='trigger' or draft['state']!='draft': raise TenantLinearError('Trigger draft unavailable.',409)
    body=validate_trigger_proposal(cur,cfg,draft['body'])
    consume(cur,tenant,approval_id,draft_id,draft['version'],'trigger_apply')
    result=save_trigger(profile,body['agent_id'],body['trigger'],connection=cur.connection)
    cur.execute('INSERT INTO saas_ai_administrator_trigger_rollbacks(tenant_id,approval_id,draft_id,agent_id,trigger_id,applied_version) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s)',(tenant,approval_id,draft_id,body['agent_id'],result['id'],result['version']))
    cur.execute("UPDATE saas_ai_administrator_drafts SET state='applied',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,draft_id))
    _audit(cur,tenant,actor,'ai_administrator_trigger_applied','ai_agent_trigger',result['id'],{'approval_id':approval_id})
    return result


def rollback_trigger_draft(cur,profile,approval_id,cfg):
    tenant,actor=_guard(cur,profile); approval_id,_=_ids(approval_id,uuid.uuid4())
    cur.execute('SELECT * FROM saas_ai_administrator_trigger_rollbacks WHERE tenant_id=%s::uuid AND approval_id=%s::uuid FOR UPDATE',(tenant,approval_id)); row=cur.fetchone()
    if not row or row['rolled_back_at'] is not None or str(row['agent_id'])!=cfg['AI_LEARNING_MCP_AGENT_ID']: raise TenantLinearError('Rollback unavailable.',409)
    cur.execute('UPDATE saas_ai_agent_triggers SET enabled=FALSE,version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND version=%s AND enabled=FALSE RETURNING id',(tenant,row['trigger_id'],row['applied_version']))
    if not cur.fetchone(): raise TenantLinearError('Trigger changed; review again.',409)
    cur.execute('UPDATE saas_ai_administrator_trigger_rollbacks SET rolled_back_at=now() WHERE tenant_id=%s::uuid AND approval_id=%s::uuid',(tenant,approval_id))
    cur.execute("UPDATE saas_ai_administrator_drafts SET state='rolled_back',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,row['draft_id']))
    _audit(cur,tenant,actor,'ai_administrator_trigger_rolled_back','ai_agent_trigger',row['trigger_id'],{'approval_id':approval_id})
    return {'trigger_id':str(row['trigger_id']),'rolled_back':True,'enabled':False}

CONFIG_FIELDS={'language','style','response_policy','expert_policy'}


def validate_config_proposal(cur,cfg,data):
    from tenant_ai_expert_policy import helper_ids
    required={'agent_id','channel_id','expected_agent_version','patch'}
    if not isinstance(data,dict) or set(data)!=required or type(data.get('expected_agent_version')) is not int or not isinstance(data.get('patch'),dict) or not data['patch'] or set(data['patch'])-CONFIG_FIELDS:
        raise TenantLinearError('Invalid agent configuration proposal.',400)
    agent_id=str(uuid.UUID(str(data['agent_id']))); channel_id=str(uuid.UUID(str(data['channel_id'])))
    if agent_id!=cfg['AI_LEARNING_MCP_AGENT_ID'] or channel_id!=cfg['AI_LEARNING_MCP_CHANNEL_ID']: raise TenantLinearError('Configuration outside OAuth scope.',403)
    cur.execute('SELECT * FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR UPDATE',(cfg['AI_LEARNING_MCP_TENANT_ID'],agent_id)); previous=cur.fetchone()
    if not previous or previous['version']!=data['expected_agent_version']: raise TenantLinearError('Agent changed; review again.',409)
    from tenant_ai_agent_connect import scope_matches
    if not scope_matches(previous['scopes'],channel_id,is_group=False): raise TenantLinearError('Channel outside agent scope.',403)
    patch=validate_agent(data['patch'],creating=False)
    if 'expert_policy' in patch:
        old=previous.get('expert_policy') or {}; new=patch['expert_policy']
        if not set(helper_ids(new))<=set(helper_ids(old)) or not set(new.get('tools',[]))<=set(old.get('tools',[])):
            raise TenantLinearError('Expert permissions cannot be expanded.',403)
        for key in ('max_calls','timeout_seconds','max_context_chars','max_tokens'):
            if new.get(key,0)>old.get(key,0): raise TenantLinearError('Expert limits cannot be expanded.',403)
    return {**data,'patch':patch},previous


def apply_agent_config(cur,profile,approval_id,draft_id,cfg):
    from tenant_ai_agents_store import save_agent
    tenant,actor=_guard(cur,profile); approval_id,draft_id=_ids(approval_id,draft_id)
    cur.execute('SELECT * FROM saas_ai_administrator_drafts WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,draft_id)); draft=cur.fetchone()
    if not draft or draft['kind']!='agent_config' or draft['state']!='draft': raise TenantLinearError('Configuration draft unavailable.',409)
    body,previous=validate_config_proposal(cur,cfg,draft['body'])
    consume(cur,tenant,approval_id,draft_id,draft['version'],'agent_config_apply')
    before={key:previous[key] for key in body['patch']}
    result=save_agent(profile,{**body['patch'],'expected_version':previous['version']},agent_id=body['agent_id'],connection=cur.connection)
    cur.execute('INSERT INTO saas_ai_administrator_config_rollbacks(tenant_id,approval_id,draft_id,agent_id,channel_id,before_patch,applied_version) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::jsonb,%s)',(tenant,approval_id,draft_id,body['agent_id'],body['channel_id'],json.dumps(before),result['version']))
    cur.execute("UPDATE saas_ai_administrator_drafts SET state='applied',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,draft_id))
    _audit(cur,tenant,actor,'ai_administrator_config_applied','ai_agent',body['agent_id'],{'approval_id':approval_id,'version':result['version']})
    return {'agent_id':result['id'],'version':result['version'],'rollback_available':True}


def rollback_agent_config(cur,profile,approval_id,cfg):
    from tenant_ai_agents_store import save_agent
    tenant,actor=_guard(cur,profile); approval_id,_=_ids(approval_id,uuid.uuid4())
    cur.execute('SELECT * FROM saas_ai_administrator_config_rollbacks WHERE tenant_id=%s::uuid AND approval_id=%s::uuid FOR UPDATE',(tenant,approval_id)); row=cur.fetchone()
    if not row or row['rolled_back_at'] is not None or str(row['agent_id'])!=cfg['AI_LEARNING_MCP_AGENT_ID'] or str(row['channel_id'])!=cfg['AI_LEARNING_MCP_CHANNEL_ID']: raise TenantLinearError('Rollback unavailable.',409)
    result=save_agent(profile,{**row['before_patch'],'expected_version':row['applied_version']},agent_id=row['agent_id'],connection=cur.connection)
    cur.execute('UPDATE saas_ai_administrator_config_rollbacks SET rolled_back_at=now() WHERE tenant_id=%s::uuid AND approval_id=%s::uuid',(tenant,approval_id))
    cur.execute("UPDATE saas_ai_administrator_drafts SET state='rolled_back',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,row['draft_id']))
    _audit(cur,tenant,actor,'ai_administrator_config_rolled_back','ai_agent',row['agent_id'],{'approval_id':approval_id})
    return {'agent_id':result['id'],'version':result['version'],'rolled_back':True}
