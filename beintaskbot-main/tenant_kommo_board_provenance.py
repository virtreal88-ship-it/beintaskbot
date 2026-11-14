"""Origin proof for cached Kommo board rows. Only verified sync observations write it."""
import hashlib
import json
from dataclasses import asdict
from tenant_crm_relation_identity import current_binding


def migrate(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_crm_board_provenance (
      tenant_id UUID NOT NULL, resource TEXT NOT NULL CHECK(resource IN ('tasks','leads')),
      external_id BIGINT NOT NULL CHECK(external_id>0), account_id TEXT NOT NULL,
      connection_generation TEXT NOT NULL, raw_digest TEXT NOT NULL,
      source_revision TEXT NOT NULL, observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,resource,external_id))''')


def digest(raw):
    return hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def ready(cur):
    cur.execute("SELECT to_regclass('saas_crm_board_provenance') IS NOT NULL AS ready")
    return (cur.fetchone() or {}).get('ready') is True


def record(cur,tenant,resource,snapshots,observation):
    if resource not in ('leads','tasks') or not observation or not snapshots:return
    from tenant_crm_import_provenance import validate_observation, board_observation_digest
    validate_observation(cur,tenant,resource,[row['raw'] for row in snapshots],observation)
    if not ready(cur):return  # Old server keeps sync available; new mutations fail closed.
    observed = dict(observation.raw_digests)
    from tenant_crm_native_access import CRMAccessError
    if any(observed.get(str(row['raw']['id'])) != board_observation_digest(resource,row['raw']) for row in snapshots):
        raise CRMAccessError('board_observation_payload_mismatch',409)
    binding=observation.binding
    values=[]
    for row in snapshots:
        raw=row['raw']; raw_hash=digest(raw)
        revision=digest({'binding':asdict(binding),'resource':resource,'id':str(raw['id']),'raw':raw_hash})
        values.append((tenant,resource,int(raw['id']),binding.account_id,binding.connection_generation,raw_hash,revision))
    cur.executemany('''INSERT INTO saas_crm_board_provenance
      (tenant_id,resource,external_id,account_id,connection_generation,raw_digest,source_revision)
      VALUES(%s::uuid,%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,resource,external_id)
      DO UPDATE SET account_id=EXCLUDED.account_id,connection_generation=EXCLUDED.connection_generation,
      raw_digest=EXCLUDED.raw_digest,source_revision=EXCLUDED.source_revision,observed_at=now()''',values)


def revisions(cur,tenant,resource,ids,binding=None,expected_raw=None,lock_proof=False):
    if not ids or not ready(cur):return {}
    binding=binding or current_binding(cur,tenant,lock=False)
    if not binding:return {}
    table,ident=('saas_crm_tasks','kommo_task_id') if resource=='tasks' else ('saas_crm_deals','kommo_lead_id')
    cur.execute(f'''SELECT p.external_id,p.raw_digest,p.source_revision,c.raw FROM saas_crm_board_provenance p
      JOIN {table} c ON c.tenant_id=p.tenant_id AND c.{ident}=p.external_id
      WHERE p.tenant_id=%s::uuid AND p.resource=%s AND p.external_id=ANY(%s::bigint[])
      AND p.account_id=%s AND p.connection_generation=%s'''+(' FOR SHARE OF p' if lock_proof else ''),
      (tenant,resource,[int(i) for i in ids],binding.account_id,binding.connection_generation))
    return {str(row['external_id']):row['source_revision'] for row in cur.fetchall()
        if row['raw_digest']==digest(row['raw']) and (expected_raw is None or
            str(row['external_id']) in expected_raw and row['raw_digest']==digest(expected_raw[str(row['external_id'])]))}


def require(cur,binding,resource,ident,revision,*,allow_latest=False):
    from tenant_task_flow import TaskFlowError
    # Match synchronization order: cached row first, then provenance. Holding
    # this row through provider I/O prevents an unrelated writer from changing
    # raw between source validation and the command's proof refresh.
    table,key=('saas_crm_tasks','kommo_task_id') if resource=='tasks' else ('saas_crm_deals','kommo_lead_id')
    cur.execute(f'SELECT raw FROM {table} WHERE tenant_id=%s::uuid AND {key}=%s FOR UPDATE',(binding.tenant_id,int(ident)))
    row=cur.fetchone()
    if not row:raise TaskFlowError('board_source_sync_required',409)
    actual=revisions(cur,binding.tenant_id,resource,[ident],binding,expected_raw={str(ident):row['raw']},lock_proof=True).get(str(ident))
    if not actual or (not allow_latest and actual!=revision):raise TaskFlowError('board_source_sync_required',409)
    return actual


def refresh_after_command(cur,binding,resource,ident):
    """Same locked transaction already proved old source and verified provider write."""
    table,key=('saas_crm_tasks','kommo_task_id') if resource=='tasks' else ('saas_crm_deals','kommo_lead_id')
    cur.execute(f'SELECT raw FROM {table} WHERE tenant_id=%s::uuid AND {key}=%s',(binding.tenant_id,int(ident)))
    row=cur.fetchone()
    if not row:return None
    raw_hash=digest(row['raw'])
    revision=digest({'binding':asdict(binding),'resource':resource,'id':str(ident),'raw':raw_hash})
    cur.execute('''UPDATE saas_crm_board_provenance SET raw_digest=%s,source_revision=%s
      WHERE tenant_id=%s::uuid AND resource=%s AND external_id=%s
      AND account_id=%s AND connection_generation=%s''',(raw_hash,revision,binding.tenant_id,resource,int(ident),binding.account_id,binding.connection_generation))
    return revision
