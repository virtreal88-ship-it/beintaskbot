"""Explicit account/generation mappings. Never infer them from legacy IDs."""
from datetime import datetime, timezone
import re

from tenant_crm_relation_contract import BoundIdentity, ProviderBinding, RecordRef
from tenant_crm_native_access import CRMAccessError


def connection_binding(tenant, row):
    if (not row or row.get('status') != 'connected'
            or row.get('provider') not in ('kommo', 'kommo_manual')
            or not row.get('account_domain') or not row.get('connected_at')):
        return None
    stamp = row['connected_at']
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]*', row['account_domain']):
        return None
    if isinstance(stamp, str):
        try:
            stamp = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        except ValueError:
            return None
    if not isinstance(stamp, datetime) or stamp.tzinfo is None:
        return None
    generation = row['provider'] + ':' + stamp.astimezone(timezone.utc).isoformat()
    return ProviderBinding(str(tenant), 'kommo', row['account_domain'], generation)


def current_binding(cur, tenant, *, lock=True):
    if lock:
        # Also fence a concurrent INSERT of the preferred OAuth integration.
        cur.execute('SELECT id FROM saas_tenants WHERE id=%s::uuid FOR UPDATE', (tenant,))
        if not cur.fetchone():
            return None
    cur.execute('''SELECT provider,status,account_domain,connected_at FROM saas_tenant_integrations
      WHERE tenant_id=%s::uuid AND provider IN ('kommo','kommo_manual')
      ORDER BY provider''' + (' FOR SHARE' if lock else ''), (tenant,))
    rows = {row['provider']: row for row in cur.fetchall()}
    selected = next((rows[p] for p in ('kommo', 'kommo_manual')
                     if rows.get(p, {}).get('status') == 'connected'), None)
    return connection_binding(tenant, selected)


def _args(binding):
    return (binding.tenant_id, binding.provider, binding.account_id, binding.connection_generation)


def bind_identity(cur, identity, *, provenance, evidence_id, verified_by=None):
    """Trusted store primitive, not a browser operation or automatic backfill.

    created/imported callers must have proved this NEW mapping's connection
    throughout the source operation. owner_verified requires an exact owner
    assertion, not merely GETting a coincidentally equal ID in a new account.
    """
    if not isinstance(identity, BoundIdentity) or identity.binding.provider != 'kommo':
        raise ValueError('invalid_relation_identity')
    if provenance not in ('created', 'imported', 'owner_verified') or not isinstance(evidence_id, str) or not evidence_id.strip():
        raise ValueError('relation_identity_evidence_required')
    if provenance == 'owner_verified' and (type(verified_by) is not int or verified_by <= 0):
        raise ValueError('relation_identity_owner_required')
    if not re.fullmatch(r'[1-9][0-9]{0,18}', identity.external_id):
        raise ValueError('invalid_kommo_identity')
    if current_binding(cur, identity.binding.tenant_id) != identity.binding:
        raise CRMAccessError('relation_connection_changed', 409)
    from tenant_crm_relation_store import ENTITIES
    table = ENTITIES[identity.record.entity_type][1]
    cur.execute(f'''SELECT id FROM {table} WHERE tenant_id=%s::uuid
      AND id=%s::uuid AND archived_at IS NULL FOR SHARE''',
      (identity.binding.tenant_id, identity.record.internal_id))
    if not cur.fetchone():
        raise CRMAccessError('relation_resource_unavailable')
    if provenance == 'owner_verified':
        from tenant_scoped_access_store import read_access_profile
        profile = read_access_profile(cur, identity.binding.tenant_id, verified_by, lock=True)
        if not profile or profile.get('role') != 'owner':
            raise CRMAccessError('relation_identity_owner_required')
    cur.execute('''INSERT INTO saas_crm_relation_identities
      (tenant_id,provider,account_id,connection_generation,entity_type,internal_id,external_id,
       provenance,evidence_id,verified_by) VALUES(%s::uuid,%s,%s,%s,%s,%s::uuid,%s,%s,%s,%s)
      ON CONFLICT(tenant_id,provider,account_id,connection_generation,entity_type,internal_id)
      DO UPDATE SET evidence_id=saas_crm_relation_identities.evidence_id
      WHERE saas_crm_relation_identities.external_id=EXCLUDED.external_id RETURNING external_id''',
      (*_args(identity.binding), identity.record.entity_type, identity.record.internal_id,
       identity.external_id, provenance, evidence_id[:240], verified_by))
    if not cur.fetchone():
        raise CRMAccessError('relation_identity_conflict', 409)


def read_identity(cur, binding, record):
    cur.execute('''SELECT external_id FROM saas_crm_relation_identities WHERE tenant_id=%s::uuid
      AND provider=%s AND account_id=%s AND connection_generation=%s
      AND entity_type=%s AND internal_id=%s::uuid FOR SHARE''',
      (*_args(binding), record.entity_type, record.internal_id))
    row = cur.fetchone()
    if not row:
        raise CRMAccessError('relation_mapping_unverified', 409)
    return BoundIdentity(record, row['external_id'], binding)
