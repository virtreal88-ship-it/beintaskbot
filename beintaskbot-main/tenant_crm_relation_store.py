"""Dormant transactional relationship journal. No routes or workers call it.

All primitives use the caller's transaction. No legacy external ID is adopted,
no whole-record sync event is created, and no permission is inferred from a
queued intent. The existing request-time 409 gate intentionally remains.
"""
import json
import uuid

from tenant_crm_native_access import CRMAccessError, current_access, locked_resource
from tenant_crm_relation_contract import RecordRef, RelationIntent
from tenant_crm_relation_identity import current_binding, read_identity

ENTITIES = {
    'deal': ('deals', 'saas_crm_native_deals'),
    'contact': ('customers', 'saas_crm_native_contacts'),
    'company': ('companies', 'saas_crm_native_companies'),
}
RELATIONS = {'deal_contact': ('deal', 'contact_id', 'contact'),
             'contact_company': ('contact', 'company_id', 'company')}


def _lock_resources(cur, tenant, actor, source, records):
    # Tenant key lock also fences insertion of a newly preferred integration
    # (a missing OAuth row cannot be protected by locking existing rows only).
    cur.execute('SELECT id FROM saas_tenants WHERE id=%s::uuid FOR UPDATE', (tenant,))
    if not cur.fetchone():
        raise CRMAccessError('relation_resource_unavailable')
    module, _ = ENTITIES[source.entity_type]
    if type(actor) is not int or actor <= 0:
        raise CRMAccessError('relation_human_actor_required')
    profile = current_access(cur, tenant, actor, module, 'update')
    rows = {}
    # Same ordering for forward and reverse-card operations.
    for ref in sorted(set(records) | {source}, key=lambda ref: (ref.entity_type, ref.internal_id)):
        resource_module, table = ENTITIES[ref.entity_type]
        if ref == source:
            row = locked_resource(cur, profile, resource_module, 'update', table, tenant, ref.internal_id)
        else:
            cur.execute(f'''SELECT * FROM {table} WHERE tenant_id=%s::uuid
              AND id=%s::uuid AND archived_at IS NULL FOR UPDATE''', (tenant, ref.internal_id))
            row = cur.fetchone()
        if not row:
            raise CRMAccessError('relation_resource_unavailable')
        rows[ref] = row
    return rows


def _settings(cur, binding, relation):
    entity, field, _ = RELATIONS[relation]
    cur.execute('''SELECT provider,sync_direction,allowed_fields FROM saas_crm_provider_settings
      WHERE tenant_id=%s::uuid AND entity_type=%s FOR SHARE''', (binding.tenant_id, entity))
    row = cur.fetchone() or {}
    if (row.get('provider') != binding.provider
            or row.get('sync_direction') not in ('to_external', 'bidirectional')
            or field not in (row.get('allowed_fields') or [])):
        raise CRMAccessError('relation_sync_disabled', 409)


def _identities(cur, intent):
    if current_binding(cur, intent.changed.binding.tenant_id) != intent.changed.binding:
        raise CRMAccessError('relation_connection_changed', 409)
    for identity in (intent.changed, intent.before, intent.after):
        if identity and read_identity(cur, identity.binding, identity.record) != identity:
            raise CRMAccessError('relation_identity_changed', 409)
    _settings(cur, intent.changed.binding, intent.relation)


def change_relation(cur, *, tenant, actor, source, changed, relation, before, after,
                    expected_revision, expectation, event_id=None):
    """Atomic local FK change + journal; caller commits or rolls back BOTH.

    Expected provider digest comes from a binding-verified complete observation.
    This is not a public API and does not enable outbound delivery.
    """
    kind, column, target_kind = RELATIONS[relation]
    if changed.entity_type != kind or any(ref and ref.entity_type != target_kind for ref in (before, after)):
        raise CRMAccessError('invalid_relation', 409)
    records = [ref for ref in (changed, before, after) if ref]
    rows = _lock_resources(cur, tenant, actor, source, records)
    previous = rows[changed]
    if (previous['updated_at'] != expected_revision
            or str(previous.get(column) or '') != (before.internal_id if before else '')):
        raise CRMAccessError('relation_local_conflict', 409)
    binding = current_binding(cur, tenant)
    if not binding:
        raise CRMAccessError('relation_connection_unavailable', 409)
    refs = {ref: read_identity(cur, binding, ref) for ref in records}
    # Validate source semantics before any mutation.
    ident = event_id or str(uuid.uuid4())
    intent = RelationIntent(ident, actor, source, relation, refs[changed],
        refs.get(before), refs.get(after), expected_revision, expectation)
    _settings(cur, binding, relation)
    table = ENTITIES[kind][1]
    cur.execute(f'''UPDATE {table} SET {column}=%s::uuid,updated_at=clock_timestamp()
      WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING updated_at''',
      (after.internal_id if after else None, tenant, changed.internal_id))
    revision = cur.fetchone()['updated_at']
    intent = RelationIntent(ident, actor, source, relation, refs[changed],
        refs.get(before), refs.get(after), revision, expectation)
    cur.execute('''INSERT INTO saas_crm_relation_outbox
      (id,tenant_id,changed_type,changed_id,relation,intent,intent_hash)
      VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s,%s::jsonb,%s)''',
      (ident, tenant, kind, changed.internal_id, relation, json.dumps(intent.to_payload()), intent.fingerprint()))
    cur.execute('''INSERT INTO saas_crm_relation_heads(tenant_id,changed_type,changed_id,relation,event_id)
      VALUES(%s::uuid,%s,%s::uuid,%s,%s::uuid)
      ON CONFLICT(tenant_id,changed_type,changed_id,relation)
      DO UPDATE SET event_id=EXCLUDED.event_id''', (tenant, kind, changed.internal_id, relation, ident))
    return intent


def reauthorize(cur, row):
    """Locks source U, graph, identity, connection and policy until I/O ends."""
    intent = RelationIntent.from_payload(row['intent'])
    tenant = intent.changed.binding.tenant_id
    if (intent.fingerprint() != row['intent_hash'] or str(row['id']) != intent.event_id
            or str(row['tenant_id']) != tenant or row['relation'] != intent.relation
            or row['changed_type'] != intent.changed.record.entity_type
            or str(row['changed_id']) != intent.changed.record.internal_id):
        raise CRMAccessError('relation_event_changed', 409)
    records = [ref.record for ref in (intent.changed, intent.before, intent.after) if ref]
    rows = _lock_resources(cur, tenant, intent.actor_id, intent.source, records)
    _identities(cur, intent)
    cur.execute('''SELECT event_id FROM saas_crm_relation_heads WHERE tenant_id=%s::uuid
      AND changed_type=%s AND changed_id=%s::uuid AND relation=%s FOR SHARE''',
      (tenant, intent.changed.record.entity_type, intent.changed.record.internal_id, intent.relation))
    head = cur.fetchone()
    current = rows[intent.changed.record]
    column = RELATIONS[intent.relation][1]
    if (not head or str(head['event_id']) != intent.event_id
            or current['updated_at'] != intent.local_revision
            or str(current.get(column) or '') != (intent.after.record.internal_id if intent.after else '')):
        raise CRMAccessError('relation_superseded', 409)
    return intent
