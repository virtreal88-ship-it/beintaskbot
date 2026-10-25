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


def _connection_profile(cur, cfg):
    """Re-read the OAuth grant in the decision transaction; never trust UI input."""
    try:
        token_id = str(uuid.UUID(str(cfg.get('oauth_token_id') or '')))
        connection_id = str(uuid.UUID(str(cfg.get('oauth_connection_id') or '')))
    except (TypeError, ValueError):
        raise TenantLinearError('Owner review connection is unavailable.', 403) from None
    tenant = str(cfg.get('AI_LEARNING_MCP_TENANT_ID') or '')
    actor = cfg.get('actor_id')
    try:
        uuid.UUID(tenant)
    except (TypeError, ValueError):
        raise TenantLinearError('Owner review connection is unavailable.', 403) from None
    cur.execute('''SELECT scope FROM saas_ai_learning_mcp_tokens
      WHERE id=%s::uuid AND tenant_id=%s::uuid AND agent_id=%s::uuid AND channel_id=%s::uuid
        AND oauth_connection_id=%s::uuid AND created_by=%s AND revoked_at IS NULL AND expires_at>now()
      FOR SHARE''', (token_id, tenant, cfg.get('AI_LEARNING_MCP_AGENT_ID'),
                     cfg.get('AI_LEARNING_MCP_CHANNEL_ID'), connection_id, actor))
    token = cur.fetchone()
    if not token or DRAFT_SCOPE not in str(token.get('scope') or '').split():
        raise TenantLinearError('Owner review authorization is unavailable.', 403)
    verified_tenant, owner = _owner(cur, {'tenant_id': tenant, 'telegram_id': actor})
    return verified_tenant, owner, connection_id


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
    try: draft_id = str(uuid.UUID(str(data['draft_id'])))
    except (TypeError, ValueError): raise TenantLinearError('Invalid owner review request.', 400) from None
    tenant, owner, connection_id = _connection_profile(cur, cfg)
    profile = {'tenant_id': tenant, 'telegram_id': owner}
    cur.execute('''SELECT * FROM saas_ai_administrator_drafts
      WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE''', (tenant, draft_id))
    draft = cur.fetchone()
    if not draft or draft['state'] != 'draft' or draft['version'] != data['version']:
        raise TenantLinearError('Draft version is not available for owner review.', 409)
    if draft['kind'] not in ALLOWED_KINDS:
        # Prompts, triggers and configuration never get a policy-bypass path.
        raise TenantLinearError('This draft kind requires the existing owner review flow.', 409)
    cur.execute('''SELECT title,body,target FROM saas_ai_administrator_draft_versions
      WHERE tenant_id=%s::uuid AND draft_id=%s::uuid AND version=%s FOR SHARE''',
      (tenant,draft_id,max(1, data['version']-1)))
    prior=cur.fetchone() if data['version'] > 1 else None
    # Reopening a rejected exact version invalidates any older widgets before a
    # fresh request becomes actionable. An already approved version is never
    # reopened through this bridge.
    cur.execute('''UPDATE saas_ai_owner_review_capabilities SET consumed_at=now()
      WHERE tenant_id=%s::uuid AND draft_id=%s::uuid AND draft_version=%s AND consumed_at IS NULL''',
      (tenant, draft_id, data['version']))
    approval = request(cur, profile, draft_id, data['version'])
    if approval['state'] != 'pending':
        raise TenantLinearError('This draft version was already decided; create a new version to review it again.', 409)
    expires_minutes = 10
    target = {'draft_id': draft_id, 'draft_version': data['version'],
              'draft_hash': _draft_hash(draft), 'kind': draft['kind']}
    capabilities={}; capability_expiry=None
    for action in ('approve','reject'):
        secret=secrets.token_urlsafe(32)
        cur.execute('''INSERT INTO saas_ai_owner_review_capabilities
          (id,tenant_id,owner_id,oauth_connection_id,capability_hash,approval_id,draft_id,draft_version,draft_hash,action,target,expires_at)
          VALUES(%s,%s::uuid,%s,%s::uuid,%s,%s::uuid,%s::uuid,%s,%s,%s,%s::jsonb,now()+(%s||' minutes')::interval)
          RETURNING expires_at''',
          (uuid.uuid4(), tenant, owner, connection_id, _hash(secret), approval['id'], draft_id,
           data['version'], target['draft_hash'], action, json.dumps(target), expires_minutes))
        expiry=cur.fetchone()['expires_at']; capability_expiry=expiry
        capabilities[action]=secret
    _audit(cur, tenant, owner, 'ai_owner_component_review_prepared', 'ai_administrator_draft', draft_id,
           {'version': data['version'], 'kind': draft['kind']})
    return {
        'draft_id': draft_id, 'version': data['version'], 'kind': draft['kind'],
        'approval_id': approval['id'], 'approval_state': 'pending',
        'manual_owner_review_required': True,
        'component_transport': 'test_only' if enabled_for_manual_bridge() else 'disabled_pending_trusted_host_validation',
        '_component_meta': {'ui': {'resourceUri': RESOURCE_URI},
                            'owner_review': {'capabilities': capabilities,
                              'expires_at': capability_expiry.isoformat(),
                              # The component renders this with textContent,
                              # not HTML, so supplied instruction text is inert.
                              'exact_diff': {'before': ({'title':prior['title'],'body':prior['body'],'target':prior['target']} if prior else None),
                                             'after': {'title':draft['title'],'body':draft['body'],'target':draft['target']},
                                             'draft_hash':target['draft_hash']}}}
    }


def decide(cur, cfg, *, capability, action):
    """Consume one capability through the app-only validated bearer path."""
    if not enabled_for_manual_bridge():
        raise TenantLinearError('Owner component approval is not enabled.', 403)
    if action not in {'approve', 'reject'} or not isinstance(capability, str) or not 32 <= len(capability) <= 200:
        raise TenantLinearError('Invalid owner component decision.', 400)
    tenant, owner, connection_id = _connection_profile(cur, cfg)
    profile={'tenant_id':tenant,'telegram_id':owner}
    cur.execute('''SELECT * FROM saas_ai_owner_review_capabilities
      WHERE tenant_id=%s::uuid AND owner_id=%s AND oauth_connection_id=%s::uuid
        AND capability_hash=%s AND expires_at>now() AND consumed_at IS NULL
      FOR UPDATE''', (tenant, owner, connection_id, _hash(capability)))
    row = cur.fetchone()
    if not row or row['action'] != action:
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
      WHERE id=%s::uuid AND action=%s AND consumed_at IS NULL RETURNING id''', (row['id'], action))
    if not cur.fetchone():
        raise TenantLinearError('Owner review was already used.', 409)
    # Closing one decision invalidates sibling approve/reject widgets too.
    cur.execute('''UPDATE saas_ai_owner_review_capabilities SET consumed_at=now()
      WHERE tenant_id=%s::uuid AND draft_id=%s::uuid AND draft_version=%s
        AND consumed_at IS NULL''',(tenant,row['draft_id'],row['draft_version']))
    result = approve(cur, profile, str(row['approval_id']), action)
    _audit(cur, tenant, owner, 'ai_owner_component_review_decided', 'ai_administrator_draft', row['draft_id'],
           {'version': row['draft_version'], 'decision': action})
    return result


def resource_html():
    # No customer-provided content or secret is interpolated into this asset.
    # The secret remains in in-memory result metadata and is never saved into
    # widget state, location, DOM text, or a follow-up prompt.
    return '''<!doctype html><html><body><section aria-label="Owner review">
<h1>Owner review</h1><p id="status">Loading exact review…</p><pre id="diff" aria-label="Exact draft diff"></pre>
<button type="button" id="approve" disabled>Approve</button>
<button type="button" id="reject" disabled>Reject</button>
<p>Chat text cannot approve a draft. This test bridge uses the connected owner OAuth grant.</p>
<script>
(() => { const api=window.openai; const status=document.querySelector('#status');
 const result=api&&api.toolResponseMetadata; const meta=(result&&((result.mcp_tool_result||result.call_tool_result||result)._meta))||{};
 let caps={}; const hydrate=(candidate)=>{ const review=(candidate&&candidate.owner_review)||{}; caps=review.capabilities||{};
 document.querySelector('#diff').textContent=JSON.stringify(review.exact_diff||{},null,2);
 if (api&&typeof api.callTool==='function'&&caps.approve&&caps.reject) { status.textContent='Review this exact draft version.';
   const a=document.querySelector('#approve'), r=document.querySelector('#reject'); a.disabled=false;r.disabled=false;a.onclick=invoke('approve');r.onclick=invoke('reject'); }
 };
 const invoke=(action)=>async()=>{ try { status.textContent='Submitting…';
   const out=await api.callTool('administrator_owner_review_component_complete',{action,capability:caps[action]});
   const state=out&&out.structuredContent&&out.structuredContent.state;
   if ((state!=='approved'&&state!=='rejected') || (out&&out.isError)) throw new Error('unconfirmed');
   status.textContent=state==='approved'?'Approved.':'Rejected.';
   document.querySelector('#approve').disabled=true; document.querySelector('#reject').disabled=true;
 } catch (_) { status.textContent='Review could not be submitted. Use the authenticated owner screen.'; } };
 hydrate(meta);
 if (!(api&&typeof api.callTool==='function'&&caps.approve&&caps.reject)) status.textContent='This host cannot securely complete the review. Use the authenticated owner screen.';
 window.addEventListener('message',(event)=>{const data=event&&event.data||{};const candidate=data.params&&data.params._meta||data._meta;if(candidate) hydrate(candidate);});
})();</script></section></body></html>'''
