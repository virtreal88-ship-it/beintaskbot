"""Small tenant-scoped outbound CRM queue; records stay in Beintaskbot."""
from __future__ import annotations
import asyncio, json, uuid
from datetime import datetime, timezone
from tenant_platform_core import _connect, _ensure_schema, _json
from tenant_crm_identity import record_link, read_link
from tenant_crm_provider import CrmProviderError

def enqueue_change(tenant_id,entity_type,internal_id):
    if entity_type not in {'contact','deal'}:return 0
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT provider,sync_direction,allowed_fields FROM saas_crm_provider_settings
              WHERE tenant_id=%s::uuid AND entity_type=%s''',(tenant_id,entity_type)); policy=cur.fetchone() or {}
            if policy.get('sync_direction') not in {'to_external','bidirectional'} or not policy.get('allowed_fields'):return 0
            table='saas_crm_native_contacts' if entity_type=='contact' else 'saas_crm_native_deals'
            cur.execute(f"SELECT updated_at FROM {table} WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL",(tenant_id,internal_id))
            row=cur.fetchone()
            if not row:return 0
            version=row['updated_at'].isoformat()
            event_key=str(uuid.uuid5(uuid.NAMESPACE_URL,f"crm-sync:{tenant_id}:{policy['provider']}:{entity_type}:{internal_id}:{version}"))
            cur.execute('''INSERT INTO saas_crm_sync_outbox(id,tenant_id,provider,entity_type,internal_id,source_updated_at,event_key)
              VALUES(%s,%s::uuid,%s,%s,%s::uuid,%s,%s) ON CONFLICT(tenant_id,provider,entity_type,event_key) DO NOTHING''',
              (uuid.uuid4(),tenant_id,policy['provider'],entity_type,internal_id,row['updated_at'],event_key))
            queued=cur.rowcount>0
        conn.commit()
    return int(queued)

def claim():
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT * FROM saas_crm_sync_outbox WHERE
              (state='pending' AND next_attempt_at<=now()) OR (state='processing' AND lease_until<now())
              ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1''')
            row=cur.fetchone()
            if not row:conn.commit();return None
            row=dict(row)
            if row['state']=='processing' and row['provider']=='kommo':
                # Kommo does not document idempotency keys for create. A lost
                # response is ambiguous, so never repeat a possible create.
                cur.execute('''SELECT 1 FROM saas_crm_entity_links WHERE tenant_id=%s::uuid AND provider=%s
                  AND entity_type=%s AND internal_id=%s::uuid''',(row['tenant_id'],row['provider'],row['entity_type'],row['internal_id']))
                if not cur.fetchone():
                    cur.execute("UPDATE saas_crm_sync_outbox SET state='unknown',error_code='kommo_create_outcome_unknown',lease_until=NULL,updated_at=now() WHERE id=%s",(row['id'],));conn.commit();return None
            cur.execute("UPDATE saas_crm_sync_outbox SET state='processing',attempts=attempts+1,lease_until=now()+interval '3 minutes',updated_at=now() WHERE id=%s RETURNING *",(row['id'],))
            row=dict(cur.fetchone())
        conn.commit()
    return row

def finish(row,state,error=''):
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            delay=min(3600,30*(2**min(int(row.get('attempts') or 1),7)))
            cur.execute('''UPDATE saas_crm_sync_outbox SET state=%s,error_code=%s,lease_until=NULL,
              next_attempt_at=CASE WHEN %s='pending' THEN now()+(%s*interval '1 second') ELSE next_attempt_at END,
              updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='processing' ''',
              (state,str(error or '')[:100],state,delay,row['tenant_id'],row['id']))
        conn.commit()

def _current_policy(cur,row):
    cur.execute('''SELECT provider,sync_direction,allowed_fields FROM saas_crm_provider_settings
      WHERE tenant_id=%s::uuid AND entity_type=%s''',(row['tenant_id'],row['entity_type']))
    policy=cur.fetchone() or {}
    if policy.get('provider')!=row['provider'] or policy.get('sync_direction') not in {'to_external','bidirectional'} or not policy.get('allowed_fields'):
        return None
    return policy

def _local(cur,row):
    table='saas_crm_native_contacts' if row['entity_type']=='contact' else 'saas_crm_native_deals'
    cur.execute(f"SELECT * FROM {table} WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL",(row['tenant_id'],row['internal_id']))
    return cur.fetchone()

def _linked(cur,row):
    return read_link(cur,tenant_id=str(row['tenant_id']),provider=row['provider'],entity_type=row['entity_type'],internal_id=str(row['internal_id']))

def _external_id(cur,tenant,provider,entity,value):
    if not value:return ''
    try:uuid.UUID(str(value))
    except (ValueError,TypeError):return str(value) if provider=='kommo' and str(value).isdigit() else ''
    link=read_link(cur,tenant_id=tenant,provider=provider,entity_type=entity,internal_id=str(value))
    return str(link.get('external_id') or '') if link else ''

def _kommo_body(cur,row,local,policy):
    fields=set(policy['allowed_fields']); entity=row['entity_type']
    if entity=='contact':
        result={}
        if 'name' in fields:result['name']=local['display_name']
        values=[]
        if 'phone' in fields and local.get('phone'):values.append({'field_code':'PHONE','values':[{'value':local['phone']}]})
        if 'email' in fields and local.get('email'):values.append({'field_code':'EMAIL','values':[{'value':local['email']}]})
        if values:result['custom_fields_values']=values
        return result
    result={}
    if 'title' in fields:result['name']=local['title']
    if 'pipeline_id' in fields:
        ext=_external_id(cur,str(row['tenant_id']),'kommo','pipeline',local['pipeline_id'])
        if not ext:raise ValueError('pipeline_mapping_missing')
        result['pipeline_id']=int(ext)
    if 'stage_id' in fields:
        ext=_external_id(cur,str(row['tenant_id']),'kommo','stage',local['stage_id'])
        if not ext:raise ValueError('stage_mapping_missing')
        result['status_id']=int(ext)
    if 'contact_id' in fields and local.get('contact_id'):
        contact=read_link(cur,tenant_id=str(row['tenant_id']),provider='kommo',entity_type='contact',internal_id=str(local['contact_id']))
        if not contact:raise ValueError('contact_mapping_missing')
        result['_embedded']={'contacts':[{'id':int(contact['external_id'])}]}
    return result

def _bein_body(cur,row,local,policy,metadata):
    fields=set(policy['allowed_fields']);result={}
    if row['entity_type']!='deal':raise ValueError('provider_resource_unsupported')
    if 'title' in fields:result['name']=local['title']
    for field,entity in (('pipeline_id','pipeline'),('stage_id','stage')):
        if field in fields:
            ext=_external_id(cur,str(row['tenant_id']),'beinsystems',entity,local[field])
            if not ext:raise ValueError(f'{entity}_mapping_missing')
            result[field]=ext
    if 'contact_id' in fields and local.get('contact_id'):
        contact=read_link(cur,tenant_id=str(row['tenant_id']),provider='beinsystems',entity_type='contact',internal_id=str(local['contact_id']))
        if not contact:raise ValueError('contact_mapping_missing')
        result['partner_id']=contact['external_id']
    if 'description' in fields:result['description']=local.get('description') or ''
    branch=str((metadata or {}).get('branch_id') or '')
    if branch:result['branch_id']=branch
    return result

def _remote_id(payload,entity,provider):
    if provider=='kommo':
        key='contacts' if entity=='contact' else 'leads'
        rows=(payload.get('_embedded') or {}).get(key) or []
        return str(rows[0].get('id') or '') if rows and isinstance(rows[0],dict) else ''
    data=payload.get('data') if isinstance(payload.get('data'),dict) else {}
    return str(data.get('id') or '')

def _time(value):
    try:
        if isinstance(value,(int,float)) or str(value or '').isdigit():return datetime.fromtimestamp(float(value),timezone.utc)
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError,ValueError,OverflowError,OSError):return None

def _remote_record(payload,entity,provider):
    if provider=='kommo':
        key='contacts' if entity=='contact' else 'leads'; rows=(payload.get('_embedded') or {}).get(key) or []
        return rows[0] if rows and isinstance(rows[0],dict) else {}
    data=payload.get('data')
    return data if isinstance(data,dict) else {}

async def _conflicts(row,local,link,request,client):
    if not link:return False
    if row['provider']=='kommo':
        path='contacts' if row['entity_type']=='contact' else 'leads'
        payload=await request(str(row['tenant_id']),'GET',f"{path}/{link['external_id']}")
    else:
        path='crm/partners' if row['entity_type']=='contact' else 'crm/leads'
        payload=await client.request('GET',f"{path}/{link['external_id']}")
    remote=_remote_record(payload,row['entity_type'],row['provider'])
    synced=_time(link.get('last_synced_at')); external=_time(remote.get('updated_at'))
    # If the provider cannot give a trustworthy version timestamp, never
    # overwrite a linked record: its conflict state needs explicit review.
    return bool(not synced or not external or external>synced)

async def _send(row,policy,request,bein_credential):
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            current=_current_policy(cur,row);local=_local(cur,row);link=_linked(cur,row)
            if not current or not local:return 'disabled','policy_or_record_changed'
            if row['provider']=='kommo':body=_kommo_body(cur,row,local,current);path='contacts' if row['entity_type']=='contact' else 'leads'
            else:
                cur.execute("SELECT metadata FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='beinsystems' AND status='connected'",(row['tenant_id'],))
                integration=cur.fetchone() or {};body=_bein_body(cur,row,local,current,integration.get('metadata'))
                path='deals'
        conn.commit()
    if row['provider']=='kommo':
        if link:
            if await _conflicts(row,local,link,request,None):
                _mark_link_conflict(row)
                return 'conflict','both_sides_changed'
            await request(str(row['tenant_id']),'PATCH',f"{path}/{link['external_id']}",json_body=[body])
            external=str(link['external_id'])
        else:
            # Kommo v4 has no documented Idempotency-Key. Ambiguous create
            # failures become unknown in the caller and are never replayed.
            result=await request(str(row['tenant_id']),'POST',path,json_body=[body])
            external=_remote_id(result,row['entity_type'],'kommo')
    else:
        client=bein_credential(str(row['tenant_id']))
        if link:
            if await _conflicts(row,local,link,request,client):
                _mark_link_conflict(row)
                return 'conflict','both_sides_changed'
            result=await client.update('deals',str(link['external_id']),body,idempotency_key=str(row['event_key']))
            external=str(link['external_id'])
        else:
            result=await client.create('deals',body,idempotency_key=str(row['event_key']))
            external=_remote_id(result,'deal','beinsystems')
    if not external:raise CrmProviderError('CRM cavabında external ID yoxdur.')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            record_link(cur,tenant_id=str(row['tenant_id']),provider=row['provider'],entity_type=row['entity_type'],
                internal_id=str(row['internal_id']),external_id=external,sync_state='synced',origin_id=str(row['event_key']))
            cur.execute("UPDATE saas_crm_sync_outbox SET state='synced',error_code='',lease_until=NULL,updated_at=now() WHERE id=%s::uuid AND state='processing'",(row['id'],))
        conn.commit()
    return 'synced',''

def _mark_link_conflict(row):
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_crm_entity_links SET sync_state='conflict',last_error_code='both_sides_changed',updated_at=now() WHERE tenant_id=%s::uuid AND provider=%s AND entity_type=%s AND internal_id=%s::uuid",(row['tenant_id'],row['provider'],row['entity_type'],row['internal_id']))
        conn.commit()

async def run_batch(request,bein_credential,logger):
    for _ in range(5):
        row=await asyncio.to_thread(claim)
        if not row:break
        try:
            with _connect() as conn:
                _ensure_schema(conn)
                with conn.cursor() as cur:policy=_current_policy(cur,row)
            if not policy:
                await asyncio.to_thread(finish,row,'disabled','policy_disabled');continue
            state,error=await _send(row,policy,request,bein_credential)
            if state!='synced':await asyncio.to_thread(finish,row,state,error)
        except ValueError as exc:
            await asyncio.to_thread(finish,row,'conflict',str(exc))
        except Exception as exc:
            # Kommo create cannot be safely repeated after an uncertain request.
            if row['provider']=='kommo' and row['attempts']==1 and not _link_exists(row):
                await asyncio.to_thread(finish,row,'unknown','provider_create_outcome_unknown')
            elif getattr(exc,'status',0) in {400,401,403,404,409,422}:
                await asyncio.to_thread(finish,row,'failed',f"provider_http_{getattr(exc,'status',0)}")
            else:
                await asyncio.to_thread(finish,row,'pending','provider_unavailable')
            logger.warning('External CRM sync item failed safely: tenant=%s provider=%s entity=%s state=deferred',row['tenant_id'],row['provider'],row['entity_type'])

def _link_exists(row):
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:return bool(_linked(cur,row))
