"""One-time, version-bound approvals for future administrator applications.

No MCP tool can approve or consume a change.  A trusted owner-facing route may
call ``approve``; an allowlisted backend applicator must consume the receipt in
the same transaction before it can change a business object.
"""
import uuid
from tenant_ai_agents_store import _audit, _authorize
from tenant_ai_administrator_drafts import DRAFT_SCOPE
from tenant_linear_policy import TenantLinearError


def _owner(cur, profile):
    """Approval is a company-owner decision, never an MCP or admin decision."""
    tenant, actor = _authorize(cur, profile)
    cur.execute('''SELECT role,active FROM saas_tenant_members
      WHERE tenant_id=%s::uuid AND telegram_id=%s FOR SHARE''', (tenant, actor))
    member = cur.fetchone()
    if not member or member['role'] != 'owner' or member.get('active') is False:
        raise TenantLinearError('Only the company owner can approve administrator changes.', 403)
    return tenant, actor


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_administrator_approvals(
          id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          draft_id UUID NOT NULL, draft_version INTEGER NOT NULL, action TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending', approved_by BIGINT NULL, approved_at TIMESTAMPTZ NULL,
          expires_at TIMESTAMPTZ NOT NULL, consumed_at TIMESTAMPTZ NULL, consumed_by TEXT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK(action='apply_draft'), CHECK(state IN ('pending','approved','rejected','consumed')),
          UNIQUE(tenant_id,draft_id,draft_version,action))''')


def definitions():
    return [
      {'name':'administrator_approval_request','description':'Request a one-time owner review for one exact non-public draft version. This never approves or applies a change.',
       'inputSchema':{'type':'object','properties':{'draft_id':{'type':'string'},'version':{'type':'integer','minimum':1}},'required':['draft_id','version'],'additionalProperties':False},
       'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}]},
      {'name':'administrator_approval_list','description':'List owner-review receipts for this company. It never applies a change.',
       'inputSchema':{'type':'object','properties':{'limit':{'type':'integer','minimum':1,'maximum':100}},'additionalProperties':False},
       'annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}]},
    ]


def request(cur, profile, draft_id, version, *, ttl_minutes=30):
    if type(version) is not int or not 1 <= ttl_minutes <= 1440: raise TenantLinearError('Invalid approval request.',400)
    tenant,actor=_owner(cur,profile)
    try: draft_id=str(uuid.UUID(str(draft_id)))
    except (ValueError,TypeError): raise TenantLinearError('Invalid approval request.',400) from None
    cur.execute('''SELECT version,state FROM saas_ai_administrator_drafts WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE''',(tenant,draft_id)); draft=cur.fetchone()
    if not draft or draft['state']!='draft' or draft['version']!=version: raise TenantLinearError('Draft version is not available for approval.',409)
    cur.execute('''INSERT INTO saas_ai_administrator_approvals(id,tenant_id,draft_id,draft_version,action,expires_at)
      VALUES(%s,%s::uuid,%s::uuid,%s,'apply_draft',now()+(%s||' minutes')::interval)
      ON CONFLICT(tenant_id,draft_id,draft_version,action) DO UPDATE SET expires_at=EXCLUDED.expires_at,state=CASE WHEN saas_ai_administrator_approvals.state IN ('rejected','consumed') THEN 'pending' ELSE saas_ai_administrator_approvals.state END
      RETURNING id,state,expires_at''',(uuid.uuid4(),tenant,draft_id,version,ttl_minutes)); row=dict(cur.fetchone())
    _audit(cur,tenant,actor,'ai_administrator_approval_requested','ai_administrator_draft',draft_id,{'version':version,'approval_id':str(row['id'])})
    return {'id':str(row['id']),'state':row['state'],'expires_at':row['expires_at'].isoformat()}


def approve(cur, profile, approval_id, decision):
    if decision not in {'approve','reject'}: raise TenantLinearError('Invalid approval decision.',400)
    tenant,actor=_owner(cur,profile)
    try: approval_id=str(uuid.UUID(str(approval_id)))
    except (ValueError,TypeError): raise TenantLinearError('Invalid approval.',400) from None
    cur.execute('SELECT * FROM saas_ai_administrator_approvals WHERE tenant_id=%s::uuid AND id=%s::uuid AND expires_at>now() FOR UPDATE',(tenant,approval_id)); row=cur.fetchone()
    if not row or row['state']!='pending': raise TenantLinearError('Approval is unavailable or expired.',409)
    state='approved' if decision=='approve' else 'rejected'
    cur.execute('UPDATE saas_ai_administrator_approvals SET state=%s,approved_by=%s,approved_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid',(state,actor,tenant,approval_id))
    _audit(cur,tenant,actor,'ai_administrator_approval_'+state,'ai_administrator_approval',approval_id,{})
    return {'id':approval_id,'state':state}


def approve_many(cur, profile, approval_ids):
    """Atomically approve a bounded review batch owned by one tenant."""
    if not isinstance(approval_ids,list) or not 1 <= len(approval_ids) <= 25:
        raise TenantLinearError('Invalid approval batch.',400)
    try:
        ids=sorted({str(uuid.UUID(str(value))) for value in approval_ids})
    except (ValueError,TypeError):
        raise TenantLinearError('Invalid approval batch.',400) from None
    if len(ids)!=len(approval_ids): raise TenantLinearError('Invalid approval batch.',400)
    tenant,actor=_owner(cur,profile)
    cur.execute('''SELECT id,state FROM saas_ai_administrator_approvals
      WHERE tenant_id=%s::uuid AND id=ANY(%s::uuid[]) AND expires_at>now()
      ORDER BY id FOR UPDATE''',(tenant,ids))
    rows=cur.fetchall()
    if len(rows)!=len(ids) or any(row['state']!='pending' for row in rows):
        raise TenantLinearError('Every approval in the batch must still be pending.',409)
    cur.execute('''UPDATE saas_ai_administrator_approvals SET state='approved',approved_by=%s,approved_at=now()
      WHERE tenant_id=%s::uuid AND id=ANY(%s::uuid[])''',(actor,tenant,ids))
    _audit(cur,tenant,actor,'ai_administrator_approval_batch_approved','ai_administrator_approval_batch',','.join(ids),{'count':len(ids)})
    return {'ids':ids,'state':'approved','count':len(ids)}


def listing(cur, profile, *, limit=50):
    """Owner-only approval queue; draft bodies stay tenant-local."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise TenantLinearError('Invalid approval list.',400)
    tenant,_ = _owner(cur,profile)
    cur.execute('''SELECT a.id,a.draft_id,a.draft_version,a.state,a.expires_at,a.created_at,
                         d.kind,d.title,d.body,d.target,d.version,d.state AS draft_state
      FROM saas_ai_administrator_approvals a
      JOIN saas_ai_administrator_drafts d ON d.id=a.draft_id AND d.tenant_id=a.tenant_id
      WHERE a.tenant_id=%s::uuid ORDER BY a.created_at DESC,a.id DESC LIMIT %s''',(tenant,limit))
    rows=[]
    for row in cur.fetchall():
        value=dict(row)
        for key in ('id','draft_id'):
            value[key]=str(value[key])
        for key in ('expires_at','created_at'):
            value[key]=value[key].isoformat()
        rows.append(value)
    return {'items':rows}


def consume(cur, tenant_id, approval_id, draft_id, version, consumer):
    """Atomically single-use an approved receipt; caller applies the draft next."""
    cur.execute('''UPDATE saas_ai_administrator_approvals SET state='consumed',consumed_at=now(),consumed_by=%s
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND draft_id=%s::uuid AND draft_version=%s
        AND action='apply_draft' AND state='approved' AND expires_at>now() AND consumed_at IS NULL RETURNING id''',(str(consumer)[:120],tenant_id,approval_id,draft_id,version))
    if not cur.fetchone(): raise TenantLinearError('Approval is not valid for this exact draft version.',409)
