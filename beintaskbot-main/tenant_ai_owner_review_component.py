"""Disabled component bridge for exact owner review of safe draft kinds.

The MCP result may contain a short-lived bearer capability only in component
metadata.  It is *not* an approval: the decision still has to arrive through
the existing authenticated owner API, with its normal Origin protection.  The
bridge is disabled unless a future, separately approved host integration turns
it on.  Keeping the verifier here makes that integration fail closed rather
than treating chat text or an ``owner=true`` value as an attestation.
"""
import hashlib
import json
import os
import secrets
import uuid

from tenant_ai_agents_store import _audit
from tenant_ai_administrator_approvals import _owner, approve, request
from tenant_ai_administrator_drafts import DRAFT_SCOPE, enabled
from tenant_linear_policy import TenantLinearError


RESOURCE_URI = 'ui://beintaskbot/owner-review-v1.html'
ALLOWED_KINDS = {'knowledge', 'instruction', 'support_case'}


def _hash(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def _draft_hash(row):
    # All fields that could materially alter a later application are bound.
    value = {'id': str(row['id']), 'version': int(row['version']),
             'kind': row['kind'], 'title': row['title'],
             'body': row['body'], 'target': row['target']}
    return _hash(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def enabled_for_manual_bridge():
    return str(os.environ.get('AI_OWNER_COMPONENT_APPROVAL_ENABLED') or '').lower() == 'true'


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_owner_review_capabilities(
          id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          owner_id BIGINT NOT NULL, oauth_connection_id UUID NOT NULL,
          capability_hash TEXT NOT NULL UNIQUE, approval_id UUID NOT NULL,
          draft_id UUID NOT NULL, draft_version INTEGER NOT NULL, draft_hash TEXT NOT NULL,
          action TEXT NOT NULL, target JSONB NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
          consumed_at TIMESTAMPTZ NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK(action IN ('approve','reject'))
        )''')
        cur.execute('CREATE INDEX IF NOT EXISTS saas_ai_owner_review_capability_live_idx '
                    'ON saas_ai_owner_review_capabilities(tenant_id,owner_id,expires_at) '
                    'WHERE consumed_at IS NULL')


def prepare(cur, cfg, data):
    """Create an *unapproved* exact-version review capability.

    The raw secret is returned to the component layer only; it is never put in
    tool content, structured content, a URL, or an audit event.
    """
    if DRAFT_SCOPE not in set(cfg.get('granted_scopes') or []) or not enabled():
        raise TenantLinearError('Administrator drafts are disabled.', 403)
    if not isinstance(data, dict) or set(data) != {'draft_id', 'version'} or type(data.get('version')) is not int:
        raise TenantLinearError('Invalid owner review request.', 400)
    try:
        draft_id = str(uuid.UUID(str(data['draft_id'])))
        connection_id = str(uuid.UUID(str(cfg.get('oauth_connection_id') or '')))
    except (TypeError, ValueError):
        raise TenantLinearError('Owner review connection is unavailable.', 409) from None
    profile = {'tenant_id': cfg['AI_LEARNING_MCP_TENANT_ID'], 'telegram_id': cfg['actor_id']}
    tenant, owner = _owner(cur, profile)
    cur.execute('''SELECT * FROM saas_ai_administrator_drafts
      WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE''', (tenant, draft_id))
    draft = cur.fetchone()
    if not draft or draft['state'] != 'draft' or draft['version'] != data['version']:
        raise TenantLinearError('Draft version is not available for owner review.', 409)
    if draft['kind'] not in ALLOWED_KINDS:
        # Prompts, triggers and configuration never get a policy-bypass path.
        raise TenantLinearError('This draft kind requires the existing owner review flow.', 409)
    approval = request(cur, profile, draft_id, data['version'])
    secret = secrets.token_urlsafe(32)
    expires_minutes = 10
    target = {'draft_id': draft_id, 'draft_version': data['version'],
              'draft_hash': _draft_hash(draft), 'kind': draft['kind']}
    cur.execute('''INSERT INTO saas_ai_owner_review_capabilities
      (id,tenant_id,owner_id,oauth_connection_id,capability_hash,approval_id,draft_id,draft_version,draft_hash,action,target,expires_at)
      VALUES(%s,%s::uuid,%s,%s::uuid,%s,%s::uuid,%s::uuid,%s,%s,'approve',%s::jsonb,now()+(%s||' minutes')::interval)''',
      (uuid.uuid4(), tenant, owner, connection_id, _hash(secret), approval['id'], draft_id,
       data['version'], target['draft_hash'], json.dumps(target), expires_minutes))
    _audit(cur, tenant, owner, 'ai_owner_component_review_prepared', 'ai_administrator_draft', draft_id,
           {'version': data['version'], 'kind': draft['kind']})
    return {
        'draft_id': draft_id, 'version': data['version'], 'kind': draft['kind'],
        'approval_id': approval['id'], 'approval_state': 'pending',
        'manual_owner_review_required': True,
        'component_transport': 'disabled_pending_trusted_host_validation',
        '_component_meta': {'ui': {'resourceUri': RESOURCE_URI},
                            'owner_review': {'capability': secret, 'expires_at': approval['expires_at']}}
    }


def decide(cur, profile, *, oauth_connection_id, capability, decision):
    """Consume one capability and approve/reject through the existing owner path.

    The caller is responsible for deriving ``profile`` from the normal browser
    session and enforcing Origin before it reaches this function.
    """
    if not enabled_for_manual_bridge():
        raise TenantLinearError('Owner component approval is not enabled.', 403)
    if decision not in {'approve', 'reject'} or not isinstance(capability, str) or not 32 <= len(capability) <= 200:
        raise TenantLinearError('Invalid owner component decision.', 400)
    try:
        connection_id = str(uuid.UUID(str(oauth_connection_id)))
    except (TypeError, ValueError):
        raise TenantLinearError('Owner review connection is unavailable.', 403) from None
    tenant, owner = _owner(cur, profile)
    cur.execute('''SELECT * FROM saas_ai_owner_review_capabilities
      WHERE tenant_id=%s::uuid AND owner_id=%s AND oauth_connection_id=%s::uuid
        AND capability_hash=%s AND expires_at>now() AND consumed_at IS NULL
      FOR UPDATE''', (tenant, owner, connection_id, _hash(capability)))
    row = cur.fetchone()
    if not row:
        raise TenantLinearError('Owner review is unavailable, expired, or already used.', 409)
    cur.execute('''SELECT * FROM saas_ai_administrator_drafts
      WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE''', (tenant, row['draft_id']))
    draft = cur.fetchone()
    if (not draft or draft['state'] != 'draft' or draft['kind'] not in ALLOWED_KINDS
            or draft['version'] != row['draft_version'] or _draft_hash(draft) != row['draft_hash']):
        raise TenantLinearError('Draft changed; reload and review it again.', 409)
    # Conditional write is a second line of defense if a database adapter does
    # not provide row locks exactly like PostgreSQL.
    cur.execute('''UPDATE saas_ai_owner_review_capabilities SET consumed_at=now()
      WHERE id=%s::uuid AND consumed_at IS NULL RETURNING id''', (row['id'],))
    if not cur.fetchone():
        raise TenantLinearError('Owner review was already used.', 409)
    result = approve(cur, profile, str(row['approval_id']), decision)
    _audit(cur, tenant, owner, 'ai_owner_component_review_decided', 'ai_administrator_draft', row['draft_id'],
           {'version': row['draft_version'], 'decision': decision})
    return result


def resource_html():
    # No customer-provided content or secret is interpolated into this asset.
    return '''<!doctype html><html><body><section aria-label="Owner review">
<h1>Owner review</h1><p>This component is disabled until the host is verified.</p>
<button type="button" disabled>Approve</button><button type="button" disabled>Reject</button>
<p>Use the authenticated company owner review screen. Chat text cannot approve a draft.</p>
</section></body></html>'''
