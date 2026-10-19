"""Incremental, allowlisted Kommo -> native CRM import; no deletions or replay."""
from __future__ import annotations
import uuid
from datetime import datetime,timezone
from tenant_crm_identity import record_link,CRMIdentityConflict
from tenant_crm_sync_data import phone as kommo_phone

def _when(value):
    try:
        if isinstance(value,(int,float)) or str(value or '').isdigit():return datetime.fromtimestamp(float(value),timezone.utc)
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError,ValueError,OverflowError,OSError):return None

def _email(row):
    for field in row.get('custom_fields_values') or []:
        if isinstance(field,dict) and str(field.get('field_code') or '').upper()=='EMAIL':
            values=field.get('values') or []
            if values and isinstance(values[0],dict):return str(values[0].get('value') or '')[:254]
    return ''

def _policy(cur,tenant,entity):
    cur.execute('''SELECT allowed_fields,enabled_since FROM saas_crm_provider_settings WHERE tenant_id=%s::uuid
      AND entity_type=%s AND provider='kommo' AND sync_direction IN ('from_external','bidirectional')''',(tenant,entity))
    return cur.fetchone()

def _record_conflict(cur,tenant,entity,external,error,internal=None):
    cur.execute('''INSERT INTO saas_crm_sync_conflicts(tenant_id,provider,entity_type,external_id,internal_id,error_code)
      VALUES(%s::uuid,'kommo',%s,%s,%s::uuid,%s) ON CONFLICT(tenant_id,provider,entity_type,external_id)
      DO UPDATE SET internal_id=EXCLUDED.internal_id,error_code=EXCLUDED.error_code,seen_at=now(),resolved_at=NULL''',
      (tenant,entity,str(external),internal,error))

def _link_by_external(cur,tenant,entity,external):
    cur.execute('''SELECT internal_id,last_synced_at FROM saas_crm_entity_links WHERE tenant_id=%s::uuid
      AND provider='kommo' AND entity_type=%s AND external_id=%s FOR UPDATE''',(tenant,entity,str(external)))
    return cur.fetchone()

def _mapped_id(cur,tenant,entity,external):
    if not external:return ''
    row=_link_by_external(cur,tenant,entity,str(external))
    return str(row['internal_id']) if row else ''

def _link_by_internal(cur,tenant,entity,internal):
    cur.execute('''SELECT external_id,last_synced_at FROM saas_crm_entity_links WHERE tenant_id=%s::uuid
      AND provider='kommo' AND entity_type=%s AND internal_id=%s::uuid FOR UPDATE''',(tenant,entity,internal))
    return cur.fetchone()

def _contact(cur,tenant,external,raw,fields,base_time):
    name=str(raw.get('name') or '').strip()[:180]; phone=kommo_phone(raw); email=_email(raw)
    link=_link_by_external(cur,tenant,'contact',external)
    internal=str(link['internal_id']) if link else ''
    if internal:
        cur.execute('SELECT id,display_name,phone,email,updated_at,archived_at FROM saas_crm_native_contacts WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,internal)); local=cur.fetchone()
        if not local or local.get('archived_at') is not None:
            _record_conflict(cur,tenant,'contact',external,'local_record_unavailable',internal);return None
    else:
        # Identity is matched only by provider ID, verified phone or exact email;
        # names alone are never used to merge people.
        cur.execute('''SELECT id,display_name,phone,email,updated_at,archived_at FROM saas_crm_native_contacts
          WHERE tenant_id=%s::uuid AND archived_at IS NULL AND
            ((%s<>'' AND lower(email)=lower(%s)) OR (%s<>'' AND regexp_replace(phone,'[^0-9]','','g')=regexp_replace(%s,'[^0-9]','','g')))
          ORDER BY id LIMIT 2 FOR UPDATE''',(tenant,email,email,phone,phone)); rows=cur.fetchall()
        if len(rows)>1:
            _record_conflict(cur,tenant,'contact',external,'ambiguous_local_identity');return None
        local=rows[0] if rows else None
    if local:
        existing=_link_by_internal(cur,tenant,'contact',str(local['id']))
        if existing and str(existing['external_id'])!=str(external):
            _record_conflict(cur,tenant,'contact',external,'identity_mapping_conflict',str(local['id']));return None
    external_time=_when(raw.get('updated_at')); baseline=(link or {}).get('last_synced_at') or base_time
    if local and baseline and external_time and local.get('updated_at') and local['updated_at']>baseline and external_time>baseline:
        _record_conflict(cur,tenant,'contact',external,'both_sides_changed',str(local['id']));
        record_link(cur,tenant_id=tenant,provider='kommo',entity_type='contact',internal_id=str(local['id']),external_id=str(external),sync_state='conflict')
        return None
    if not local:
        if 'name' not in fields or not name:
            _record_conflict(cur,tenant,'contact',external,'name_required_for_new_contact');return None
        internal=str(uuid.uuid4())
        cur.execute('''INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name,phone,email)
          VALUES(%s::uuid,%s::uuid,%s,%s,%s)''',(internal,tenant,name,phone if 'phone' in fields else '',email if 'email' in fields else ''))
    else:
        internal=str(local['id']); updates=[];args=[]
        if 'name' in fields and name:updates.append('display_name=%s');args.append(name)
        if 'phone' in fields:updates.append('phone=%s');args.append(phone)
        if 'email' in fields:updates.append('email=%s');args.append(email)
        if updates:
            args.extend([tenant,internal]);cur.execute(f"UPDATE saas_crm_native_contacts SET {','.join(updates)},updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",args)
    try:record_link(cur,tenant_id=tenant,provider='kommo',entity_type='contact',internal_id=internal,external_id=str(external),sync_state='synced',origin_id=f"kommo:{external}:{raw.get('updated_at','')}")
    except CRMIdentityConflict:
        _record_conflict(cur,tenant,'contact',external,'identity_mapping_conflict',internal);return None
    cur.execute("UPDATE saas_crm_sync_conflicts SET resolved_at=now() WHERE tenant_id=%s::uuid AND provider='kommo' AND entity_type='contact' AND external_id=%s",(tenant,str(external)))
    return internal

def import_contacts(cur,tenant,rows):
    policy=_policy(cur,tenant,'contact')
    if not policy:return 0
    fields=set(policy.get('allowed_fields') or []); base=policy.get('enabled_since'); base=base.replace(microsecond=0) if isinstance(base,datetime) else base; saved=0
    for raw in rows:
        external=str(raw.get('id') or ''); updated=_when(raw.get('updated_at'))
        if not external or not external.isdigit() or not updated or (base and updated<base):continue
        if _contact(cur,tenant,external,raw,fields,base):saved+=1
    return saved

def import_deals(cur,tenant,rows):
    policy=_policy(cur,tenant,'deal')
    if not policy:return 0
    fields=set(policy.get('allowed_fields') or []);base=policy.get('enabled_since');base=base.replace(microsecond=0) if isinstance(base,datetime) else base;saved=0
    for raw in rows:
        external=str(raw.get('id') or '');updated=_when(raw.get('updated_at'))
        if not external or not external.isdigit() or not updated or (base and updated<base):continue
        link=_link_by_external(cur,tenant,'deal',external); internal=str(link['internal_id']) if link else ''
        contact_raw=(raw.get('_embedded') or {}).get('contacts') or []
        contact_raw=next((item for item in contact_raw if isinstance(item,dict) and item.get('is_main')),contact_raw[0] if contact_raw and isinstance(contact_raw[0],dict) else {})
        contact_id=''
        if contact_raw.get('id'):
            c_link=_link_by_external(cur,tenant,'contact',str(contact_raw['id']))
            contact_id=str(c_link['internal_id']) if c_link else ''
        title=str(raw.get('name') or '').strip()[:240]
        external_pipeline=str(raw.get('pipeline_id') or '');external_stage=str(raw.get('status_id') or '')
        pipeline=_mapped_id(cur,tenant,'pipeline',external_pipeline)
        stage=_mapped_id(cur,tenant,'stage',external_stage)
        if not internal:
            if not title or not pipeline or not stage or not {'title','pipeline_id','stage_id'}<=fields:
                _record_conflict(cur,tenant,'deal',external,'required_fields_unavailable');continue
            cur.execute('''SELECT 1 FROM saas_tenant_pipeline_stages s JOIN saas_tenant_pipelines p USING(tenant_id,pipeline_id)
              WHERE s.tenant_id=%s::uuid AND s.pipeline_id=%s AND s.stage_id=%s AND p.active=TRUE''',(tenant,pipeline,stage))
            if not cur.fetchone():_record_conflict(cur,tenant,'deal',external,'pipeline_stage_not_configured');continue
            if contact_raw.get('id') and not contact_id and 'contact_id' in fields:
                _record_conflict(cur,tenant,'deal',external,'contact_mapping_missing');continue
            if contact_id:
                cur.execute("SELECT 1 FROM saas_crm_native_deals WHERE tenant_id=%s::uuid AND contact_id=%s::uuid AND pipeline_id=%s AND title=%s AND archived_at IS NULL LIMIT 1",(tenant,contact_id,pipeline,title))
                if cur.fetchone():
                    _record_conflict(cur,tenant,'deal',external,'possible_local_duplicate');continue
            internal=str(uuid.uuid4())
            cur.execute('''INSERT INTO saas_crm_native_deals(id,tenant_id,title,pipeline_id,stage_id,contact_id)
              VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s::uuid)''',(internal,tenant,title,pipeline,stage,contact_id or None))
        else:
            cur.execute('SELECT id,title,pipeline_id,stage_id,contact_id,updated_at,archived_at FROM saas_crm_native_deals WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,internal));local=cur.fetchone()
            if not local or local.get('archived_at') is not None:
                _record_conflict(cur,tenant,'deal',external,'local_record_unavailable',internal);continue
            baseline=(link or {}).get('last_synced_at') or base
            if baseline and local.get('updated_at') and local['updated_at']>baseline and updated>baseline:
                _record_conflict(cur,tenant,'deal',external,'both_sides_changed',internal)
                record_link(cur,tenant_id=tenant,provider='kommo',entity_type='deal',internal_id=internal,external_id=external,sync_state='conflict');continue
            updates=[];args=[]
            if 'title' in fields and title:updates.append('title=%s');args.append(title)
            if {'pipeline_id','stage_id'}<=fields and pipeline and stage:
                cur.execute('''SELECT 1 FROM saas_tenant_pipeline_stages s JOIN saas_tenant_pipelines p USING(tenant_id,pipeline_id)
                  WHERE s.tenant_id=%s::uuid AND s.pipeline_id=%s AND s.stage_id=%s AND p.active=TRUE''',(tenant,pipeline,stage))
                if cur.fetchone():updates.extend(['pipeline_id=%s','stage_id=%s']);args.extend([pipeline,stage])
                else:_record_conflict(cur,tenant,'deal',external,'pipeline_stage_not_configured',internal);continue
            if 'contact_id' in fields and contact_id:updates.append('contact_id=%s::uuid');args.append(contact_id)
            if updates:
                args.extend([tenant,internal]);cur.execute(f"UPDATE saas_crm_native_deals SET {','.join(updates)},updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",args)
        try:record_link(cur,tenant_id=tenant,provider='kommo',entity_type='deal',internal_id=internal,external_id=external,sync_state='synced',origin_id=f"kommo:{external}:{raw.get('updated_at','')}")
        except CRMIdentityConflict:
            _record_conflict(cur,tenant,'deal',external,'identity_mapping_conflict',internal);continue
        cur.execute("UPDATE saas_crm_sync_conflicts SET resolved_at=now() WHERE tenant_id=%s::uuid AND provider='kommo' AND entity_type='deal' AND external_id=%s",(tenant,external))
        saved+=1
    return saved
