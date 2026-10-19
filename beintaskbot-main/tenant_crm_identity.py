"""Tenant-bound CRM identity links; no provider calls or automatic sync."""
from __future__ import annotations

import json
import uuid


PROVIDERS = {'kommo', 'beinsystems'}
ENTITY_TYPES = {'contact', 'deal', 'task', 'pipeline', 'stage'}
MODES = {'beintaskbot', 'external', 'selective'}


class CRMIdentityConflict(ValueError):
    """An external identifier is already attached to a different local row."""


def _scope(tenant_id, provider, entity_type, internal_id):
    tenant = str(uuid.UUID(str(tenant_id)))
    internal = str(uuid.UUID(str(internal_id)))
    if provider not in PROVIDERS or entity_type not in ENTITY_TYPES:
        raise ValueError('CRM identity scope is invalid.')
    return tenant, provider, entity_type, internal


def record_link(cur, *, tenant_id, provider, entity_type, internal_id, external_id,
                sync_state='pending', origin_id=''):
    """Idempotently retain a mapping; never silently reassign either identity."""
    tenant, provider, entity_type, internal = _scope(tenant_id, provider, entity_type, internal_id)
    external = str(external_id or '').strip()
    if not external or len(external) > 240:
        raise ValueError('External CRM identifier is invalid.')
    if sync_state not in {'pending', 'synced', 'conflict', 'failed', 'disabled'}:
        raise ValueError('CRM sync state is invalid.')
    cur.execute('''SELECT internal_id,external_id FROM saas_crm_entity_links
      WHERE tenant_id=%s::uuid AND provider=%s AND entity_type=%s
        AND (internal_id=%s::uuid OR external_id=%s) FOR UPDATE''',
                (tenant, provider, entity_type, internal, external))
    existing = cur.fetchall()
    if any(str(row['internal_id']) != internal or row['external_id'] != external for row in existing):
        raise CRMIdentityConflict('External CRM identifier is linked to another record.')
    try:
        cur.execute('''INSERT INTO saas_crm_entity_links
      (tenant_id,provider,entity_type,internal_id,external_id,sync_state,last_origin_id)
      VALUES(%s::uuid,%s,%s,%s::uuid,%s,%s,%s)
      ON CONFLICT(tenant_id,provider,entity_type,internal_id) DO UPDATE SET
        sync_state=EXCLUDED.sync_state,last_origin_id=EXCLUDED.last_origin_id,
        updated_at=now(),last_synced_at=CASE WHEN EXCLUDED.sync_state='synced'
          THEN now() ELSE saas_crm_entity_links.last_synced_at END
      WHERE saas_crm_entity_links.external_id=EXCLUDED.external_id
      RETURNING tenant_id,provider,entity_type,internal_id,external_id,sync_state,last_synced_at,last_origin_id''',
                    (tenant, provider, entity_type, internal, external, sync_state, str(origin_id or '')[:240]))
    except Exception as exc:
        if getattr(exc, 'sqlstate', None) == '23505':
            raise CRMIdentityConflict('CRM identity mapping changed concurrently.') from exc
        raise
    row = cur.fetchone()
    if not row:
        raise CRMIdentityConflict('CRM identity mapping changed concurrently.')
    return dict(row)


def read_link(cur, *, tenant_id, provider, entity_type, internal_id):
    tenant, provider, entity_type, internal = _scope(tenant_id, provider, entity_type, internal_id)
    cur.execute('''SELECT tenant_id,provider,entity_type,internal_id,external_id,sync_state,
      last_synced_at,last_origin_id,last_error_code FROM saas_crm_entity_links
      WHERE tenant_id=%s::uuid AND provider=%s AND entity_type=%s AND internal_id=%s::uuid''',
                (tenant, provider, entity_type, internal))
    row = cur.fetchone()
    return dict(row) if row else None


def set_entity_mode(cur, *, tenant_id, entity_type, mode, primary_provider,
                    allowed_fields=(), updated_by=None):
    """Persist explicit future sync policy. This does not activate a sync job."""
    tenant = str(uuid.UUID(str(tenant_id)))
    if entity_type not in ENTITY_TYPES or mode not in MODES or primary_provider not in PROVIDERS | {'beintaskbot'}:
        raise ValueError('CRM provider policy is invalid.')
    if ((mode == 'beintaskbot' and primary_provider != 'beintaskbot') or
            (mode == 'external' and primary_provider not in PROVIDERS)):
        raise ValueError('CRM primary system does not match the selected mode.')
    if not isinstance(allowed_fields, (list, tuple, set, frozenset)):
        raise ValueError('CRM allowed field list is invalid.')
    fields = sorted({str(value).strip() for value in allowed_fields if str(value).strip()})
    if len(fields) > 100 or any(len(value) > 100 for value in fields):
        raise ValueError('CRM allowed field list is invalid.')
    cur.execute('''INSERT INTO saas_crm_provider_settings
      (tenant_id,entity_type,mode,primary_provider,allowed_fields,updated_by)
      VALUES(%s::uuid,%s,%s,%s,%s::jsonb,%s)
      ON CONFLICT(tenant_id,entity_type) DO UPDATE SET mode=EXCLUDED.mode,
        primary_provider=EXCLUDED.primary_provider,allowed_fields=EXCLUDED.allowed_fields,
        updated_by=EXCLUDED.updated_by,updated_at=now()
      RETURNING tenant_id,entity_type,mode,primary_provider,allowed_fields,updated_at''',
                (tenant, entity_type, mode, primary_provider, json.dumps(fields), updated_by))
    return dict(cur.fetchone())
