"""Transactional, deliberately narrow application of owner-approved drafts."""
import os
import uuid

from tenant_ai_agents_store import _audit, _authorize
from tenant_ai_agents_policy import agent as validate_agent
from tenant_linear_policy import TenantLinearError
from tenant_ai_administrator_approvals import consume


def enabled(): return str(os.environ.get('AI_AUTONOMOUS_APPLY_ENABLED') or '').lower() == 'true'


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_administrator_rollbacks(
          id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          draft_id UUID NOT NULL, approval_id UUID NOT NULL UNIQUE, agent_id UUID NOT NULL,
          before_instructions TEXT NOT NULL, before_version INTEGER NOT NULL, applied_version INTEGER NOT NULL,
          rolled_back_at TIMESTAMPTZ NULL, created_by BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')


def _guard(cur, profile):
    if not enabled(): raise TenantLinearError('Autonomous application is disabled.',403)
    tenant, actor = _authorize(cur,profile)
    # Application changes a live agent.  A receipt is not sufficient unless
    # the same authenticated company owner is performing the action.
    cur.execute('''SELECT role,active FROM saas_tenant_members
      WHERE tenant_id=%s::uuid AND telegram_id=%s FOR SHARE''', (tenant, actor))
    member = cur.fetchone()
    if not member or member['role'] != 'owner' or member.get('active') is False:
        raise TenantLinearError('Only the company owner can apply or roll back administrator changes.',403)
    return tenant,actor


def apply_agent_prompt(cur, profile, approval_id, draft_id):
    """Apply only an instruction-string patch guarded by receipt and versions."""
    tenant,actor=_guard(cur,profile)
    try: draft_id=str(uuid.UUID(str(draft_id))); approval_id=str(uuid.UUID(str(approval_id)))
    except (ValueError,TypeError): raise TenantLinearError('Invalid approval request.',400) from None
    cur.execute('SELECT * FROM saas_ai_administrator_drafts WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,draft_id)); draft=cur.fetchone()
    if not draft or draft['kind']!='agent_improvement' or draft['state']!='draft': raise TenantLinearError('Agent improvement draft is unavailable.',409)
    body=draft['body'] or {}; allowed={'agent_id','expected_version','instructions'}
    if not isinstance(body,dict) or set(body)!=allowed or type(body.get('expected_version')) is not int or not isinstance(body.get('instructions'),str): raise TenantLinearError('Invalid agent improvement draft.',400)
    patch=validate_agent({'instructions':body['instructions']},creating=False)
    try: agent_id=str(uuid.UUID(str(body['agent_id'])))
    except (ValueError,TypeError): raise TenantLinearError('Invalid agent improvement draft.',400) from None
    cur.execute('SELECT id,instructions,version FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR UPDATE',(tenant,agent_id)); agent=cur.fetchone()
    if not agent or agent['version']!=body['expected_version']: raise TenantLinearError('Agent changed; review the draft again.',409)
    consume(cur,tenant,approval_id,draft_id,draft['version'],'agent_prompt_apply')
    cur.execute('''UPDATE saas_ai_agents SET instructions=%s,version=version+1,updated_at=now()
      WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING version''',(patch['instructions'],tenant,agent_id)); version=cur.fetchone()['version']
    cur.execute('''INSERT INTO saas_ai_administrator_rollbacks(id,tenant_id,draft_id,approval_id,agent_id,before_instructions,before_version,applied_version,created_by)
      VALUES(%s,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s)''',(uuid.uuid4(),tenant,draft_id,approval_id,agent_id,agent['instructions'],agent['version'],version,actor))
    cur.execute("UPDATE saas_ai_administrator_drafts SET state='applied',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,draft_id))
    _audit(cur,tenant,actor,'ai_administrator_agent_prompt_applied','ai_agent',agent_id,{'draft_id':draft_id,'approval_id':approval_id,'version':version})
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
