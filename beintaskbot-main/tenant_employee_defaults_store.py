"""Owner-authorized, tenant-bounded one-time transition away from legacy grants.

No owner, source binding, assigned-task field or explicit access scope is changed.
A snapshot digest prevents applying a preview after a concurrent permissions edit.
"""
import hashlib
import json
import uuid

from tenant_employee_defaults import POLICY_KEY
from tenant_platform_core import _connect, _json
from tenant_member_store import MemberCardError
from tenant_scoped_access import scoped_access_enabled

RETIRED_SETTINGS = ('native_crm_actions', 'ai_agents_manage', 'ai_enabled')


def _snapshot(cur, tenant_id, owner_id):
    cur.execute('SELECT id,status,owner_telegram_id FROM saas_tenants WHERE id=%s::uuid FOR UPDATE', (tenant_id,))
    company = cur.fetchone()
    if not company or company['status'] != 'active' or int(company['owner_telegram_id']) != owner_id:
        raise MemberCardError('member_access_revoked', 403)
    cur.execute('SELECT policy_key,value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid ORDER BY policy_key FOR UPDATE', (tenant_id,))
    policies = {r['policy_key']: r['value'] for r in cur.fetchall()}
    cur.execute('SELECT telegram_id,role,active,permissions,updated_at FROM saas_tenant_members WHERE tenant_id=%s::uuid ORDER BY telegram_id FOR UPDATE', (tenant_id,))
    members = [dict(row) for row in cur.fetchall()]
    owners = [r for r in members if r['role'] == 'owner' and r['active'] is True]
    if len(owners) != 1 or int(owners[0]['telegram_id']) != owner_id:
        raise MemberCardError('member_access_revoked', 403)
    cur.execute('SELECT id,role,permissions FROM saas_tenant_invites WHERE tenant_id=%s::uuid AND accepted_at IS NULL ORDER BY id FOR UPDATE', (tenant_id,))
    invites = [dict(row) for row in cur.fetchall()]
    settings = policies.get('members', {})
    if not isinstance(settings, dict) or any(not isinstance(v, dict) for v in settings.values()):
        raise MemberCardError('member_access_settings_invalid', 409)
    material = {'tenant_id': tenant_id, 'owner_id': owner_id, 'members': members,
                'invites': invites, 'settings': settings, 'defaults': policies.get(POLICY_KEY)}
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()
    return members, invites, settings, policies.get(POLICY_KEY), digest


def transition(*, tenant_id, owner_id, expected_digest=None):
    tenant_id, owner_id = str(uuid.UUID(str(tenant_id))), int(owner_id)
    if not scoped_access_enabled():
        raise MemberCardError('member_access_scopes_not_enabled', 409)
    with _connect() as conn:
        with conn.cursor() as cur:
            members, invites, settings, defaults, digest = _snapshot(cur, tenant_id, owner_id)
            targets = [row for row in members if row['role'] != 'owner']
            result = {'tenant_id': tenant_id, 'digest': digest, 'employee_count': len(targets),
                      'pending_invite_count': len(invites), 'applied': False,
                      'already_applied': isinstance(defaults, dict) and defaults.get('deny_unset') is True}
            if expected_digest is None or result['already_applied']:
                return result
            if not isinstance(expected_digest, str) or expected_digest != digest:
                raise MemberCardError('member_card_conflict', 409)
            updated = {k: dict(v) for k, v in settings.items()}
            before = []
            for row in targets:
                actor = int(row['telegram_id'])
                current = updated.setdefault(str(actor), {})
                # New matrix scopes remain byte-for-byte unchanged. Legacy
                # booleans/role/module grants no longer provide implicit access.
                retired = {key: current.pop(key) for key in RETIRED_SETTINGS if key in current}
                before.append({'telegram_id': actor, 'permissions': row['permissions'], 'retired_settings': retired})
                cur.execute("UPDATE saas_tenant_members SET permissions='[]'::jsonb,updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond') WHERE tenant_id=%s::uuid AND telegram_id=%s AND role<>'owner'", (tenant_id, actor))
            cur.execute("UPDATE saas_tenant_invites SET permissions='[]'::jsonb WHERE tenant_id=%s::uuid AND accepted_at IS NULL", (tenant_id,))
            for key, value in [('members', updated), (POLICY_KEY, {'deny_unset': True, 'version': 1})]:
                cur.execute('''INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by)
                  VALUES(%s::uuid,%s,%s::jsonb,%s) ON CONFLICT(tenant_id,policy_key)
                  DO UPDATE SET value=EXCLUDED.value,updated_by=EXCLUDED.updated_by,updated_at=now()''',
                            (tenant_id, key, _json(value), owner_id))
            audit_id = str(uuid.uuid4())
            cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
              VALUES(%s::uuid,%s::uuid,%s,'employee.default_deny.enabled','tenant',%s,%s::jsonb)''',
                        (audit_id, tenant_id, owner_id, tenant_id, _json({'preview_digest': digest,
                         'previous_members': before, 'previous_invites': [{**r, 'id': str(r['id'])} for r in invites],
                         'explicit_scopes_preserved': True})))
            result.update(applied=True, audit_id=audit_id)
        conn.commit()
    return result
