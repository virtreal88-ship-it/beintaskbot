"""Atomic scoped scalar events, never a whole-record relationship exporter."""
import hashlib
import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from tenant_policy import TenantPolicy
from tenant_crm_native_access import CRMAccessError, current_access, locked_resource
from tenant_crm_relation_contract import ProviderBinding, RecordRef
from tenant_crm_relation_identity import current_binding, read_identity

TABLES = {'contact': ('customers','saas_crm_native_contacts'), 'deal': ('deals','saas_crm_native_deals')}
FIELDS = {'contact': {'name':'display_name','phone':'phone','email':'email'},
          'deal': {'title':'title','pipeline_id':'pipeline_id','stage_id':'stage_id','description':'description'}}
PROVIDER_FIELDS = {('kommo','contact'): {'name','phone','email'},
                   ('kommo','deal'): {'title','pipeline_id','stage_id'},
                   ('beinsystems','deal'): {'title','pipeline_id','stage_id','description'}}
HASH_FIELDS = ('id','tenant_id','provider','entity_type','internal_id','actor_id','source_action',
               'local_revision','field_mask','before_values','values','binding','external_id')


def _stamp(value):
    if not isinstance(value, datetime): value = datetime.fromisoformat(str(value).replace('Z','+00:00'))
    return value.astimezone(timezone.utc).isoformat()


def fingerprint(row):
    data = {key: row[key] for key in HASH_FIELDS}
    for key in ('id','tenant_id','internal_id'): data[key] = str(data[key])
    data['local_revision'] = _stamp(data['local_revision'])
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def managed(profile, entity):
    return profile is not None and TenantPolicy(profile).has_scoped_access(TABLES[entity][0])


def lock_admission(cur, tenant, actor):
    # Acquire before current_access's SHARE lock, avoiding lock-upgrade races.
    from tenant_scoped_access import scoped_access_enabled
    if actor is not None and scoped_access_enabled():
        cur.execute('SELECT id FROM saas_tenants WHERE id=%s::uuid FOR UPDATE', (tenant,))
        if not cur.fetchone(): raise CRMAccessError()


def queue_edit(cur, tenant, actor, profile, entity, row, previous, *, action='update'):
    """Return public sync status or None for the unchanged legacy path.

    A scoped record head is written even when no export is configured. It stops
    an older actorless legacy event from exporting this new scoped revision.
    Only actually changed, currently allowed scalar fields enter the event.
    """
    if not managed(profile, entity): return None
    module, table = TABLES[entity]
    ident = str(row['id'])
    cur.execute(f'SELECT * FROM {table} WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE', (tenant, ident))
    local = cur.fetchone()
    revision = local['updated_at']
    cur.execute('''INSERT INTO saas_crm_scalar_heads(tenant_id,entity_type,internal_id,local_revision)
      VALUES(%s::uuid,%s,%s::uuid,%s) ON CONFLICT(tenant_id,entity_type,internal_id)
      DO UPDATE SET local_revision=EXCLUDED.local_revision''', (tenant, entity, ident, revision))
    cur.execute('''SELECT provider,sync_direction,allowed_fields FROM saas_crm_provider_settings
      WHERE tenant_id=%s::uuid AND entity_type=%s FOR SHARE''', (tenant,entity))
    policy = cur.fetchone() or {}
    cur.execute('''UPDATE saas_crm_scalar_outbox SET state='superseded',
      error_code='scalar_superseded_before_send',updated_at=clock_timestamp()
      WHERE tenant_id=%s::uuid AND entity_type=%s AND internal_id=%s::uuid
      AND local_revision<%s AND state IN ('pending','needs_verification')
      AND attempt_outcome='not_started' RETURNING id''',(tenant,entity,ident,revision))
    superseded=[str(item['id']) for item in cur.fetchall()]
    status = {'mode':'scoped_scalar','state':'off','local_saved':True,
              'review_required':bool(superseded),'superseded_unsent_event_ids':superseded}
    if policy.get('sync_direction') not in ('to_external','bidirectional') or not policy.get('allowed_fields'):
        return status
    provider = policy['provider']
    previous = previous or {}
    mask = sorted(field for field in set(policy['allowed_fields']) & PROVIDER_FIELDS.get((provider,entity),set())
                  if action == 'create' or str(local.get(FIELDS[entity][field]) or '') != str(previous.get(FIELDS[entity][field]) or ''))
    if not mask or action == 'create':
        cur.execute('''SELECT id,state,error_code FROM saas_crm_scalar_outbox
          WHERE tenant_id=%s::uuid AND entity_type=%s AND internal_id=%s::uuid
          AND local_revision=%s ORDER BY created_at DESC,id DESC LIMIT 1''', (tenant,entity,ident,revision))
        existing=cur.fetchone()
        if existing:
            return {**status,'event_id':str(existing['id']),'state':existing['state'],'error_code':existing['error_code']}
        if not mask:
            return {**status,'state':'unchanged'}
    event = {'id':str(uuid.uuid4()),'tenant_id':str(tenant),'provider':provider,'entity_type':entity,
        'internal_id':ident,'actor_id':actor,'source_action':action,'local_revision':revision,'field_mask':mask,
        'before_values':{f:str(previous.get(FIELDS[entity][f]) or '') for f in mask},
        'values':{f:str(local.get(FIELDS[entity][f]) or '') for f in mask},'binding':None,'external_id':None}
    state, error = 'pending', ''
    if provider != 'kommo':
        state,error = 'needs_verification','scalar_provider_binding_unverified'
    else:
        binding = current_binding(cur, tenant)
        if not binding:
            state,error = 'needs_verification','scalar_connection_unverified'
        else:
            event['binding'] = asdict(binding)
            try:
                event['external_id'] = read_identity(cur,binding,RecordRef(entity,ident)).external_id
            except CRMAccessError:
                state,error = 'needs_verification','scalar_mapping_unverified'
    # A created record has no verified provider identity. No implicit POST or
    # reinterpretation of create-only permission as source-card U is allowed.
    if set(mask) & {'pipeline_id','stage_id'}:
        state,error = 'needs_verification','scalar_catalog_mapping_unverified'
    if entity == 'contact' and any(not event['values'][f] for f in set(mask) & {'phone','email'}):
        state,error = 'needs_verification','scalar_clear_contract_unverified'
    if action == 'create': state,error = 'needs_verification','scalar_create_identity_unverified'
    from tenant_crm_writer_fence import legacy_uncertain
    if legacy_uncertain(cur, tenant, entity, ident):
        state,error = 'needs_verification','scalar_legacy_outcome_unknown'
    event['content_hash'] = fingerprint(event)
    cur.execute('''INSERT INTO saas_crm_scalar_outbox(id,tenant_id,provider,entity_type,internal_id,
      actor_id,source_action,local_revision,field_mask,before_values,values,binding,external_id,content_hash,state,error_code)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s::uuid,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s)''',
      (event['id'],tenant,provider,entity,ident,actor,action,revision,json.dumps(mask),
       json.dumps(event['before_values']),json.dumps(event['values']),json.dumps(event['binding']),
       event['external_id'],event['content_hash'],state,error))
    return {**status,'event_id':event['id'],'state':state,'error_code':error}


def reauthorize(cur, event):
    from tenant_scoped_access import scoped_access_enabled
    if not scoped_access_enabled(): raise CRMAccessError('scalar_scope_disabled')
    if event['content_hash'] != fingerprint(event): raise CRMAccessError('scalar_event_changed')
    tenant,entity,actor = str(event['tenant_id']),event['entity_type'],event['actor_id']
    if entity not in TABLES or event['source_action'] != 'update' or type(actor) is not int or actor <= 0:
        raise CRMAccessError('scalar_event_invalid')
    mask = event['field_mask']
    if (not isinstance(mask,list) or not mask or len(mask)!=len(set(mask))
        or not set(mask) <= PROVIDER_FIELDS.get((event['provider'],entity),set())
        or set(mask) != set(event['values']) or set(mask) != set(event['before_values'])):
        raise CRMAccessError('scalar_event_invalid')
    lock_admission(cur,tenant,actor)
    module,table = TABLES[entity]
    profile = current_access(cur,tenant,actor,module,'update')
    if not managed(profile,entity): raise CRMAccessError('scalar_scope_changed')
    local = locked_resource(cur,profile,module,'update',table,tenant,str(event['internal_id']))
    if not local: raise CRMAccessError('scalar_source_unavailable')
    if _stamp(local['updated_at']) != _stamp(event['local_revision']):
        raise CRMAccessError('scalar_superseded',409)
    if any(str(local.get(FIELDS[entity][field]) or '') != event['values'][field] for field in mask):
        raise CRMAccessError('scalar_superseded',409)
    cur.execute('''SELECT 1 FROM saas_crm_scalar_outbox WHERE tenant_id=%s::uuid
      AND entity_type=%s AND internal_id=%s::uuid AND id<>%s::uuid
      AND (attempt_outcome='sending' OR state='unknown') LIMIT 1''',
      (tenant,entity,event['internal_id'],event['id']))
    if cur.fetchone(): raise CRMAccessError('scalar_previous_outcome_unknown',409)
    from tenant_crm_writer_fence import legacy_uncertain
    if legacy_uncertain(cur,tenant,entity,event['internal_id']):
        raise CRMAccessError('scalar_legacy_outcome_unknown',409)
    binding = ProviderBinding(**event['binding'])
    if current_binding(cur,tenant) != binding: raise CRMAccessError('scalar_connection_changed',409)
    if read_identity(cur,binding,RecordRef(entity,str(event['internal_id']))).external_id != event['external_id']:
        raise CRMAccessError('scalar_mapping_changed',409)
    cur.execute('''SELECT provider,sync_direction,allowed_fields FROM saas_crm_provider_settings
      WHERE tenant_id=%s::uuid AND entity_type=%s FOR SHARE''',(tenant,entity))
    policy=cur.fetchone() or {}
    if (policy.get('provider')!=event['provider'] or policy.get('sync_direction') not in ('to_external','bidirectional')
        or not set(mask)<=set(policy.get('allowed_fields') or [])):
        raise CRMAccessError('scalar_policy_changed',409)
    return binding


def legacy_superseded(cur, row):
    """Quarantine full-record events for any card with scoped provenance.

    A later actorless revision does not authorize exporting the scoped values
    still present on that card. Revision ordering cannot establish provenance.
    """
    cur.execute('''SELECT 1 FROM saas_crm_scalar_heads WHERE tenant_id=%s::uuid
      AND entity_type=%s AND internal_id=%s::uuid''',
      (row['tenant_id'],row['entity_type'],row['internal_id']))
    return bool(cur.fetchone())
