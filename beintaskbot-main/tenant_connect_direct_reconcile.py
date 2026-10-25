"""Owner-approved, reversible reconciliation of verified direct JID/LID routes.

Only pairs proven by signed historical receipts are eligible.  A candidate with
unmigratable references is quarantined instead of being guessed or deleted.
"""
from __future__ import annotations
import hashlib
import json
import re
import uuid
from contextlib import nullcontext

from tenant_platform import _connect, _json
from tenant_ai_agents_store import _audit
from tenant_linear_policy import TenantLinearError
from tenant_connect_transport import _schema
import tenant_connect_reconcile_dependencies as dependencies


def _owner(cur, profile):
    tenant=str(profile.get('tenant_id') or ''); actor=int(profile.get('telegram_id') or 0)
    cur.execute('''SELECT m.role,m.active,t.status FROM saas_tenant_members m JOIN saas_tenants t ON t.id=m.tenant_id
      WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s FOR UPDATE OF m,t''',(tenant,actor))
    row=cur.fetchone() or {}
    if row.get('role')!='owner' or row.get('active') is not True or row.get('status')!='active':
        raise TenantLinearError('Company owner access required.',403)
    return tenant,actor

def _id(tenant,channel,jid,lid): return hashlib.sha256((tenant+'\x1f'+channel+'\x1f'+jid+'\x1f'+lid).encode()).hexdigest()
def _precondition(item): return hashlib.sha256(json.dumps(item,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def ensure_schema(conn):
    _schema(conn)
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_connect_direct_merge_backups(
          id UUID PRIMARY KEY,tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          channel_id UUID NOT NULL,pair_id TEXT NOT NULL,canonical_conversation_id TEXT NOT NULL,split_conversation_id TEXT NOT NULL,
          snapshot JSONB NOT NULL,state TEXT NOT NULL CHECK(state IN ('merged','rolled_back')),created_by BIGINT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),rolled_back_at TIMESTAMPTZ NULL,UNIQUE(tenant_id,pair_id))''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_connect_direct_merge_requests(
          tenant_id UUID NOT NULL,request_id UUID NOT NULL,operation TEXT NOT NULL,pair_id TEXT NOT NULL,result JSONB NOT NULL,
          PRIMARY KEY(tenant_id,request_id))''')

def _candidates(cur, tenant, channel=''):
    from tenant_connect_identity import inventory
    cur.execute('SELECT connect_channel_id::text AS channel FROM saas_connect_channels WHERE tenant_id=%s::uuid AND (%s=\'\' OR connect_channel_id::text=%s) ORDER BY connect_channel_id',(tenant,channel,channel))
    scans=[(row['channel'],inventory(cur,tenant,row['channel'])) for row in cur.fetchall()]
    items=[]
    evidence=[(ch,p) for ch,scan in scans for p in scan['pairs']]
    for ch,proof in evidence:
        jid,lid=proof['canonical_id'],proof['split_id'];receipts=proof['receipts']
        cur.execute('''SELECT conversation_id,lead_id,conversation_kind FROM saas_connect_conversations
          WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=ANY(%s::text[]) FOR SHARE''',(tenant,ch,[jid,lid]))
        routes={str(row['conversation_id']):dict(row) for row in cur.fetchall()}; reasons=list(proof['conflicts'])
        if set(routes)!={jid,lid}: reasons.append('route_missing')
        if any((route.get('conversation_kind') or '').casefold()=='group' for route in routes.values()): reasons.append('group_not_allowed')
        canonical,split=routes.get(jid),routes.get(lid)
        if canonical and split:
            snapshot=dependencies.capture(cur,tenant,ch,[jid,lid],[canonical['lead_id'],split['lead_id']])
            reasons.extend(dependencies.conflicts(cur,tenant,ch,[jid,lid],[canonical['lead_id'],split['lead_id']],snapshot))
        counts={'canonical':0,'split':0}
        for key,route in [('canonical',canonical),('split',split)]:
            if route:
                cur.execute("SELECT count(*) AS n FROM saas_crm_messages WHERE tenant_id=%s::uuid AND channel='connect:'||%s::text AND kommo_lead_id=%s",(tenant,ch,route['lead_id']))
                counts[key]=int(cur.fetchone()['n'])
        item={'pair_id':_id(tenant,ch,jid,lid),'channel_id':ch,'provider_receipts':receipts,'canonical_messages':counts['canonical'],'split_messages':counts['split'],'eligible':not reasons,'conflicts':sorted(set(reasons)),'evidence_sources':proof['sources']}
        if canonical and split:
            item['dependency_counts']={k:len(v) for k,v in snapshot.items() if k not in {'messages','routes','aliases'}}
            item['snapshot_digest']=_precondition(snapshot)
        item['precondition']=_precondition(item);items.append(item)
    return items

def preview(profile, channel_id=''):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,_=_owner(cur,profile); items=_candidates(cur,tenant,str(channel_id or ''))
        conn.commit();return {'items':items,'total':len(items),'eligible':sum(x['eligible'] for x in items)}

def apply(profile, payload, *, connection=None, channel_id=None):
    if not isinstance(payload,dict) or set(payload)!={'operation','pair_id','precondition','request_id','confirm'} or payload['confirm'] is not True:
        raise TenantLinearError('direct_merge_confirmation_required',400)
    operation=str(payload['operation']);pair_id=str(payload['pair_id']);precondition=str(payload['precondition'])
    if operation not in {'merge','rollback'} or not re.fullmatch(r'[a-f0-9]{64}',pair_id) or not re.fullmatch(r'[a-f0-9]{64}',precondition): raise TenantLinearError('direct_merge_request_invalid',400)
    try: request_id=str(uuid.UUID(str(payload['request_id'])))
    except (TypeError,ValueError): raise TenantLinearError('direct_merge_request_invalid',400) from None
    with (nullcontext(connection) if connection is not None else _connect()) as conn:
        if connection is None: ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,actor=_owner(cur,profile)
            dependencies.lock(cur)
            cur.execute('SELECT result FROM saas_connect_direct_merge_requests WHERE tenant_id=%s::uuid AND request_id=%s::uuid FOR UPDATE',(tenant,request_id)); old=cur.fetchone()
            request_digest=_precondition(payload)
            if old:
                if channel_id:
                    cur.execute('SELECT channel_id::text AS channel FROM saas_connect_direct_merge_backups WHERE tenant_id=%s::uuid AND pair_id=%s', (tenant,pair_id))
                    if (cur.fetchone() or {}).get('channel') != channel_id: raise TenantLinearError('direct_merge_channel_forbidden',403)
                if old['result'].get('request_digest')!=request_digest: raise TenantLinearError('direct_merge_request_key_conflict',409)
                return {**dict(old['result']),'idempotent_replay':True}
            candidates=_candidates(cur,tenant); item=next((x for x in candidates if x['pair_id']==pair_id),None)
            cur.execute('SELECT * FROM saas_connect_direct_merge_backups WHERE tenant_id=%s::uuid AND pair_id=%s FOR UPDATE',(tenant,pair_id)); backup=cur.fetchone()
            if channel_id and str((item or backup or {}).get('channel_id') or '') != channel_id:
                raise TenantLinearError('direct_merge_channel_forbidden',403)
            if operation=='merge':
                if not item or item['precondition']!=precondition or not item['eligible']: raise TenantLinearError('direct_merge_preview_stale_or_blocked',409)
                if backup and backup['state']=='merged': raise TenantLinearError('direct_merge_backup_conflict',409)
                # Reconstruct private IDs only after the signed evidence and
                # precondition have been verified in this same transaction.
                from tenant_connect_identity import inventory
                scan=inventory(cur,tenant,item['channel_id'])
                proof=next((p for p in scan['pairs'] if not p['conflicts'] and _id(tenant,item['channel_id'],p['canonical_id'],p['split_id'])==pair_id),None)
                jid=proof['canonical_id'] if proof else None;lid=proof['split_id'] if proof else None
                if not jid or not lid: raise TenantLinearError('direct_merge_preview_stale_or_blocked',409)
                routes=scan['routes']
                canonical,split=routes[jid],routes[lid]
                ids=[jid,lid];leads=[canonical['lead_id'],split['lead_id']];channel=item['channel_id']
                before=dependencies.capture(cur,tenant,channel,ids,leads)
                dependencies.migrate(cur,tenant,channel,ids,leads,before)
                after=dependencies.capture(cur,tenant,channel,ids,leads)
                dependencies.verify_transfer(before,after,ids,leads)
                snapshot={'version':2,'ids':ids,'leads':leads,'before':before,'after':after}
                cur.execute('''INSERT INTO saas_connect_direct_merge_backups(id,tenant_id,channel_id,pair_id,canonical_conversation_id,split_conversation_id,snapshot,state,created_by)
                  VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s::jsonb,'merged',%s) ON CONFLICT(tenant_id,pair_id) DO UPDATE SET snapshot=EXCLUDED.snapshot,state='merged',created_by=EXCLUDED.created_by,created_at=now(),rolled_back_at=NULL''',(str(uuid.uuid4()),tenant,item['channel_id'],pair_id,jid,lid,_json(snapshot),actor))
                result={'state':'merged','pair_id':pair_id,'moved_messages':item['split_messages'],'preserved_message_ids':True,'rollback_available':True,'dependency_counts':item['dependency_counts']}
            else:
                if not backup or backup['state']!='merged': raise TenantLinearError('direct_merge_rollback_unavailable',409)
                snapshot=dict(backup['snapshot'])
                if snapshot.get('version')!=2: raise TenantLinearError('direct_merge_legacy_backup_requires_review',409)
                if item and item['precondition']!=precondition: raise TenantLinearError('direct_merge_preview_stale_or_blocked',409)
                if not item: raise TenantLinearError('direct_merge_preview_stale_or_blocked',409)
                ids=snapshot['ids'];leads=snapshot['leads'];channel=str(backup['channel_id'])
                reasons=dependencies.conflicts(cur,tenant,channel,ids,leads,snapshot['after'])
                if reasons: raise TenantLinearError('direct_merge_rollback_dependency_conflict',409)
                touches=dependencies.restore(cur,tenant,channel,ids,leads,snapshot['before'],snapshot['after'])
                cur.execute("UPDATE saas_connect_direct_merge_backups SET state='rolled_back',rolled_back_at=now() WHERE tenant_id=%s::uuid AND pair_id=%s",(tenant,pair_id))
                result={'state':'rolled_back','pair_id':pair_id,'restored_messages':sum(r['kommo_lead_id']==leads[1] for r in snapshot['before']['messages']),'preserved_message_ids':True,'preserved_timestamp_touches':touches}
            result['request_digest']=request_digest
            cur.execute('INSERT INTO saas_connect_direct_merge_requests(tenant_id,request_id,operation,pair_id,result) VALUES(%s::uuid,%s::uuid,%s,%s,%s::jsonb)',(tenant,request_id,operation,pair_id,_json(result)))
            _audit(cur,tenant,actor,'connect_direct_merge_'+operation,'connect_conversation',pair_id,{'channel_id':(item or backup)['channel_id'],'physical':True,'snapshot_version':2,'before_digest':_precondition(snapshot['before']),'after_digest':_precondition(snapshot['after']),'dependency_counts':result.get('dependency_counts',{}),'preserved_timestamp_touches':result.get('preserved_timestamp_touches',[])})
        if connection is None: conn.commit()
        return result


def rollback_preflight(profile,pair_id, *, connection=None, channel_id=None):
    """Deterministic owner-only dry run under the same locks; never restores data."""
    with (nullcontext(connection) if connection is not None else _connect()) as conn:
        if connection is None: ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,_=_owner(cur,profile);dependencies.lock(cur)
            cur.execute("SELECT * FROM saas_connect_direct_merge_backups WHERE tenant_id=%s::uuid AND pair_id=%s AND state='merged' FOR UPDATE",(tenant,pair_id))
            backup=cur.fetchone()
            if not backup or backup['snapshot'].get('version')!=2: raise TenantLinearError('direct_merge_rollback_unavailable',409)
            if channel_id and str(backup['channel_id']) != channel_id: raise TenantLinearError('direct_merge_channel_forbidden',403)
            s=backup['snapshot'];channel=str(backup['channel_id'])
            current=dependencies.capture(cur,tenant,channel,s['ids'],s['leads'])
            reasons=dependencies.conflicts(cur,tenant,channel,s['ids'],s['leads'],current)
            if reasons: raise TenantLinearError('direct_merge_rollback_dependency_conflict',409)
            _,touches=dependencies.rollback_expected(cur,tenant,s['before'],s['after'],current)
            result={'eligible':True,'pair_id':pair_id,'backup_id':str(backup['id']),'current_digest':_precondition(current),'preserved_timestamp_touches':touches}
        if connection is None: conn.rollback()
        return result
