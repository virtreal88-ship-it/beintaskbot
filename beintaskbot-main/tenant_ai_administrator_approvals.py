"""One-time, version-bound approvals for future administrator applications.

No MCP tool can approve or consume a change.  A trusted owner-facing route may
call ``approve``; an allowlisted backend applicator must consume the receipt in
the same transaction before it can change a business object.
"""
import uuid
from tenant_ai_agents_store import _audit, _authorize
from tenant_linear_policy import TenantLinearError


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


def request(cur, profile, draft_id, version, *, ttl_minutes=30):
    if type(version) is not int or not 1 <= ttl_minutes <= 1440: raise TenantLinearError('Invalid approval request.',400)
    tenant,actor=_authorize(cur,profile)
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
    tenant,actor=_authorize(cur,profile)
    try: approval_id=str(uuid.UUID(str(approval_id)))
    except (ValueError,TypeError): raise TenantLinearError('Invalid approval.',400) from None
    cur.execute('SELECT * FROM saas_ai_administrator_approvals WHERE tenant_id=%s::uuid AND id=%s::uuid AND expires_at>now() FOR UPDATE',(tenant,approval_id)); row=cur.fetchone()
    if not row or row['state']!='pending': raise TenantLinearError('Approval is unavailable or expired.',409)
    state='approved' if decision=='approve' else 'rejected'
    cur.execute('UPDATE saas_ai_administrator_approvals SET state=%s,approved_by=%s,approved_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid',(state,actor,tenant,approval_id))
    _audit(cur,tenant,actor,'ai_administrator_approval_'+state,'ai_administrator_approval',approval_id,{})
    return {'id':approval_id,'state':state}


def consume(cur, tenant_id, approval_id, draft_id, version, consumer):
    """Atomically single-use an approved receipt; caller applies the draft next."""
    cur.execute('''UPDATE saas_ai_administrator_approvals SET state='consumed',consumed_at=now(),consumed_by=%s
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND draft_id=%s::uuid AND draft_version=%s
        AND action='apply_draft' AND state='approved' AND expires_at>now() AND consumed_at IS NULL RETURNING id''',(str(consumer)[:120],tenant_id,approval_id,draft_id,version))
    if not cur.fetchone(): raise TenantLinearError('Approval is not valid for this exact draft version.',409)
