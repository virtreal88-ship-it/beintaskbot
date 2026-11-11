"""One-shot scalar PATCH with immutable provenance and current access fences.

No relation body, implicit create, automatic retry, token refresh, or rebinding.
Provider scalar PATCH is not remote CAS; known divergent scalar values conflict.
"""
import asyncio
from tenant_crm_native_access import CRMAccessError
from tenant_crm_scalar_store import reauthorize, lock_admission


def _remote_values(event, remote):
    if not isinstance(remote,dict) or str(remote.get('id')) != event['external_id']:
        raise CRMAccessError('scalar_remote_identity_mismatch')
    values={}
    for field in event['field_mask']:
        if field in ('title','name'):
            value=remote.get('name')
            if not isinstance(value,str): raise CRMAccessError('scalar_remote_field_unknown')
            values[field]=value
        elif field in ('phone','email'):
            fields=remote.get('custom_fields_values')
            if fields is None: fields=[]
            if not isinstance(fields,list) or any(not isinstance(item,dict) for item in fields):
                raise CRMAccessError('scalar_remote_field_unknown')
            matches=[item for item in fields if item.get('field_code')==field.upper()]
            if len(matches)>1: raise CRMAccessError('scalar_remote_multivalue')
            items=(matches[0].get('values') or []) if matches else []
            if not isinstance(items,list) or len(items)>1 or any(not isinstance(item,dict) or not isinstance(item.get('value'),str) for item in items):
                raise CRMAccessError('scalar_remote_multivalue')
            values[field]=items[0]['value'] if items else ''
        else:
            raise CRMAccessError('scalar_field_contract_unverified')
    return values


def body(event, remote):
    result={}
    for field,value in event['values'].items():
        if field in ('title','name'): result['name']=value
        elif field in ('phone','email') and value:
            item={'value':value}
            # Preserve the existing single value's phone/email category.
            for prior in remote.get('custom_fields_values') or []:
                if prior.get('field_code')==field.upper() and prior.get('values'):
                    item.update({k:prior['values'][0][k] for k in ('enum_id','enum_code') if k in prior['values'][0]})
            result.setdefault('custom_fields_values',[]).append({'field_code':field.upper(),'values':[item]})
        else: raise CRMAccessError('scalar_field_contract_unverified')
    return result


def _finish(cur,event,state,error=''):
    cur.execute('''UPDATE saas_crm_scalar_outbox SET state=%s,error_code=%s,updated_at=clock_timestamp()
      WHERE id=%s::uuid''',(state,error,event))
    return state


def dispatch(factory,event_id,request):
    """request(binding,method,path,json_body=...) must be bound, no-refresh I/O."""
    with factory() as conn:
        with conn.cursor() as cur:
            key='crm-scalar:'+str(event_id)
            cur.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS locked',(key,))
            if not cur.fetchone()['locked']: return 'busy'
            conn.commit()
            try:
                cur.execute('SELECT tenant_id,actor_id FROM saas_crm_scalar_outbox WHERE id=%s::uuid',(event_id,))
                admission=cur.fetchone()
                if not admission: return 'missing'
                lock_admission(cur,str(admission['tenant_id']),admission['actor_id'])
                cur.execute('SELECT * FROM saas_crm_scalar_outbox WHERE id=%s::uuid FOR UPDATE',(event_id,))
                event=cur.fetchone()
                if not event: return 'missing'
                if event['state'] not in ('pending','processing'): return event['state']
                if event['attempt_outcome']=='sending':
                    return _finish(cur,event_id,'unknown','scalar_outcome_unknown')
                write_started=False
                try:
                    binding=reauthorize(cur,event)
                    path=('contacts/' if event['entity_type']=='contact' else 'leads/')+event['external_id']
                    remote=request(binding,'GET',path)
                    observed=_remote_values(event,remote)
                    if observed==event['values']:
                        return _finish(cur,event_id,'synced')
                    if observed!=event['before_values']:
                        return _finish(cur,event_id,'conflict','scalar_remote_changed')
                    payload=body(event,remote)
                    cur.execute("UPDATE saas_crm_scalar_outbox SET state='processing',attempt_outcome='sending' WHERE id=%s::uuid",(event_id,))
                    conn.commit()  # Persist the possible-send barrier before any PATCH.
                    lock_admission(cur,str(event['tenant_id']),event['actor_id'])
                    cur.execute('SELECT * FROM saas_crm_scalar_outbox WHERE id=%s::uuid FOR UPDATE',(event_id,))
                    event=cur.fetchone()
                    binding=reauthorize(cur,event)
                    # Local authorization, mapping, integration and policy stay
                    # locked through PATCH and verification. No refresh may write
                    # those integration rows from another connection while held.
                    try:
                        write_started=True
                        request(binding,'PATCH',path,json_body=payload)
                        cur.execute("UPDATE saas_crm_scalar_outbox SET attempt_outcome='acknowledged' WHERE id=%s::uuid",(event_id,))
                        remote=request(binding,'GET',path)
                        if _remote_values(event,remote)!=event['values']:
                            return _finish(cur,event_id,'conflict','scalar_readback_conflict')
                    except Exception:
                        return _finish(cur,event_id,'unknown','scalar_outcome_unknown')
                    cur.execute("UPDATE saas_crm_scalar_outbox SET attempt_outcome='acknowledged' WHERE id=%s::uuid",(event_id,))
                    return _finish(cur,event_id,'synced')
                except CRMAccessError as error:
                    if not write_started:
                        cur.execute("UPDATE saas_crm_scalar_outbox SET attempt_outcome='not_started' WHERE id=%s::uuid",(event_id,))
                    state=('superseded' if error.code=='scalar_superseded' else
                           'needs_verification' if error.code in ('scalar_previous_outcome_unknown','scalar_legacy_outcome_unknown') else 'disabled')
                    return _finish(cur,event_id,state,error.code)
            finally:
                try: conn.commit()
                finally:
                    conn.rollback()
                    cur.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(key,))
                    conn.commit()


async def run_batch(factory,bound_request,logger):
    from tenant_scoped_access import scoped_access_enabled
    if not scoped_access_enabled() or bound_request is None: return
    def pending():
        with factory() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM saas_crm_scalar_outbox WHERE state IN ('pending','processing') ORDER BY created_at,id LIMIT 5")
                return [row['id'] for row in cur.fetchall()]
    loop=asyncio.get_running_loop()
    def request(binding,*args,**kwargs):
        # Transport has fixed finite HTTP timeout and no detached/background retry.
        return asyncio.run_coroutine_threadsafe(bound_request(binding,*args,**kwargs),loop).result()
    for event in await asyncio.to_thread(pending):
        try: await asyncio.to_thread(dispatch,factory,event,request)
        except Exception:
            logger.warning('Scoped CRM scalar delivery unavailable')
