"""Owner-approved, reversible reconciliation of verified direct JID/LID routes.

Only pairs proven by signed historical receipts are eligible.  A candidate with
unmigratable references is quarantined instead of being guessed or deleted.
"""
from __future__ import annotations
import hashlib
import json
import re
import uuid

from tenant_platform import _connect, _json
from tenant_ai_agents_store import _audit
from tenant_linear_policy import TenantLinearError
from tenant_connect_transport import _schema

_JID=re.compile(r'^[1-9][0-9]{5,22}@(s\.whatsapp\.net|c\.us)$')
_LID=re.compile(r'^[1-9][0-9]{5,22}@lid$')

def _owner(cur, profile):
    tenant=str(profile.get('tenant_id') or ''); actor=int(profile.get('telegram_id') or 0)
    cur.execute('''SELECT m.role,m.active,t.status FROM saas_tenant_members m JOIN saas_tenants t ON t.id=m.tenant_id
      WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s FOR UPDATE OF m,t''',(tenant,actor))
    row=cur.fetchone() or {}
    if row.get('role')!='owner' or row.get('active') is not True or row.get('status')!='active':
        raise TenantLinearError('Company owner access required.',403)
    return tenant,actor

def _raw(value):
    if isinstance(value,dict): return value
    try: return json.loads(value) if isinstance(value,str) else {}
    except json.JSONDecodeError: return {}

def _pair(raw):
    item=raw.get('directIdentity') if isinstance(raw,dict) else None
    lid,jid=str((item or {}).get('lid') or ''),str((item or {}).get('jid') or '')
    if not (_LID.fullmatch(lid) and _JID.fullmatch(jid) and raw.get('conversationId') in {lid,jid}): return None
    return jid,lid

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
    params=[tenant]; where="m.tenant_id=%s::uuid AND m.channel LIKE 'connect:%%' AND m.raw ? 'directIdentity'"
    if channel: where+=" AND c.connect_channel_id=%s::uuid";params.append(channel)
    cur.execute('''SELECT m.raw,c.connect_channel_id::text AS channel,c.conversation_id,c.lead_id,c.conversation_kind
      FROM saas_crm_messages m JOIN saas_connect_conversations c ON c.tenant_id=m.tenant_id AND c.lead_id=m.kommo_lead_id
      AND m.channel='connect:'||c.connect_channel_id::text WHERE '''+where,params)
    evidence={}; graph={}; conflicts=set()
    for row in cur.fetchall():
        pair=_pair(_raw(row.get('raw')))
        if not pair: continue
        jid,lid=pair; key=(row['channel'],jid,lid); evidence[key]=evidence.get(key,0)+1
        graph.setdefault((row['channel'],jid),set()).add(lid);graph.setdefault((row['channel'],lid),set()).add(jid)
        if _raw(row.get('raw')).get('conversationIdentityConflict') is True: conflicts.update((row['channel'],jid,row['channel'],lid))
    items=[]
    for (ch,jid,lid),receipts in sorted(evidence.items()):
        cur.execute('''SELECT conversation_id,lead_id,conversation_kind FROM saas_connect_conversations
          WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=ANY(%s::text[]) FOR SHARE''',(tenant,ch,[jid,lid]))
        routes={str(row['conversation_id']):dict(row) for row in cur.fetchall()}; reasons=[]
        if set(routes)!={jid,lid}: reasons.append('route_missing')
        if graph.get((ch,jid))!={lid} or graph.get((ch,lid))!={jid}: reasons.append('provider_identity_ambiguous')
        if (ch,jid) in conflicts or (ch,lid) in conflicts: reasons.append('provider_identity_conflict')
        if any((route.get('conversation_kind') or '').casefold()=='group' for route in routes.values()): reasons.append('group_not_allowed')
        canonical,split=routes.get(jid),routes.get(lid)
        if canonical and split:
            # Do not silently invalidate AI/case state. Those pairs are shown
            # for owner review and can be migrated by a dedicated future path.
            for table,condition,args in [
                ('saas_ai_conversation_state',"provider='connect' AND conversation_id=%s",(split['conversation_id'],)),
                ('saas_ai_agent_jobs',"provider='connect' AND channel_id=%s::text AND conversation_id=%s",(ch,split['conversation_id'])),
                ('saas_ai_agent_drafts','lead_id=%s',(split['lead_id'],)),
            ]:
                cur.execute("SELECT to_regclass(%s) AS present",(table,))
                if cur.fetchone()['present']:
                    cur.execute('SELECT 1 FROM '+table+' WHERE tenant_id=%s::uuid AND '+condition+' LIMIT 1',(tenant,*args))
                    if cur.fetchone(): reasons.append('dependent_state_requires_review');break
        counts={'canonical':0,'split':0}
        for key,route in [('canonical',canonical),('split',split)]:
            if route:
                cur.execute("SELECT count(*) AS n FROM saas_crm_messages WHERE tenant_id=%s::uuid AND channel='connect:'||%s::text AND kommo_lead_id=%s",(tenant,ch,route['lead_id']))
                counts[key]=int(cur.fetchone()['n'])
        item={'pair_id':_id(tenant,ch,jid,lid),'channel_id':ch,'provider_receipts':receipts,'canonical_messages':counts['canonical'],'split_messages':counts['split'],'eligible':not reasons,'conflicts':sorted(set(reasons))}
        item['precondition']=_precondition(item);items.append(item)
    return items

def preview(profile, channel_id=''):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,_=_owner(cur,profile); items=_candidates(cur,tenant,str(channel_id or ''))
        conn.commit();return {'items':items,'total':len(items),'eligible':sum(x['eligible'] for x in items)}

def apply(profile, payload):
    if not isinstance(payload,dict) or set(payload)!={'operation','pair_id','precondition','request_id','confirm'} or payload['confirm'] is not True:
        raise TenantLinearError('direct_merge_confirmation_required',400)
    operation=str(payload['operation']);pair_id=str(payload['pair_id']);precondition=str(payload['precondition'])
    if operation not in {'merge','rollback'} or not re.fullmatch(r'[a-f0-9]{64}',pair_id) or not re.fullmatch(r'[a-f0-9]{64}',precondition): raise TenantLinearError('direct_merge_request_invalid',400)
    try: request_id=str(uuid.UUID(str(payload['request_id'])))
    except (TypeError,ValueError): raise TenantLinearError('direct_merge_request_invalid',400) from None
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,actor=_owner(cur,profile)
            cur.execute('SELECT result FROM saas_connect_direct_merge_requests WHERE tenant_id=%s::uuid AND request_id=%s::uuid FOR UPDATE',(tenant,request_id)); old=cur.fetchone()
            if old: return {**dict(old['result']),'idempotent_replay':True}
            candidates=_candidates(cur,tenant); item=next((x for x in candidates if x['pair_id']==pair_id),None)
            cur.execute('SELECT * FROM saas_connect_direct_merge_backups WHERE tenant_id=%s::uuid AND pair_id=%s FOR UPDATE',(tenant,pair_id)); backup=cur.fetchone()
            if operation=='merge':
                if not item or item['precondition']!=precondition or not item['eligible']: raise TenantLinearError('direct_merge_preview_stale_or_blocked',409)
                # Reconstruct private IDs only after the signed evidence and
                # precondition have been verified in this same transaction.
                cur.execute('''SELECT c.conversation_id,c.lead_id,m.raw FROM saas_crm_messages m JOIN saas_connect_conversations c ON c.tenant_id=m.tenant_id AND c.lead_id=m.kommo_lead_id WHERE m.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid AND m.raw ? 'directIdentity' ''',(tenant,item['channel_id']))
                routes={}
                for row in cur.fetchall():
                    pair=_pair(_raw(row['raw']))
                    if pair and _id(tenant,item['channel_id'],pair[0],pair[1])==pair_id: routes[str(row['conversation_id'])]=dict(row)
                jid=next((k for k in routes if _JID.fullmatch(k)),None);lid=next((k for k in routes if _LID.fullmatch(k)),None)
                if not jid or not lid: raise TenantLinearError('direct_merge_preview_stale_or_blocked',409)
                canonical,split=routes[jid],routes[lid]
                cur.execute('SELECT row_to_json(c) AS row FROM saas_connect_conversations c WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s',(tenant,item['channel_id'],lid)); route_snapshot=cur.fetchone()['row']
                cur.execute("SELECT jsonb_agg(row_to_json(m)) AS rows FROM saas_crm_messages m WHERE tenant_id=%s::uuid AND channel='connect:'||%s::text AND kommo_lead_id=%s",(tenant,item['channel_id'],split['lead_id'])); messages=cur.fetchone()['rows'] or []
                snapshot={'route':route_snapshot,'messages':messages}
                cur.execute('UPDATE saas_crm_messages SET kommo_lead_id=%s WHERE tenant_id=%s::uuid AND channel=%s AND kommo_lead_id=%s',(canonical['lead_id'],tenant,'connect:'+item['channel_id'],split['lead_id']))
                cur.execute('DELETE FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s',(tenant,item['channel_id'],lid))
                cur.execute('''INSERT INTO saas_connect_direct_merge_backups(id,tenant_id,channel_id,pair_id,canonical_conversation_id,split_conversation_id,snapshot,state,created_by)
                  VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s::jsonb,'merged',%s) ON CONFLICT(tenant_id,pair_id) DO UPDATE SET snapshot=EXCLUDED.snapshot,state='merged',created_by=EXCLUDED.created_by,created_at=now(),rolled_back_at=NULL''',(str(uuid.uuid4()),tenant,item['channel_id'],pair_id,jid,lid,_json(snapshot),actor))
                result={'state':'merged','pair_id':pair_id,'moved_messages':len(messages),'preserved_message_ids':True,'rollback_available':True}
            else:
                if not backup or backup['state']!='merged': raise TenantLinearError('direct_merge_rollback_unavailable',409)
                snapshot=dict(backup['snapshot']);route=snapshot['route'];messages=snapshot['messages']
                if item and item['precondition']!=precondition: raise TenantLinearError('direct_merge_preview_stale_or_blocked',409)
                columns=('tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,phone,ai_enabled,manual_hold,last_message_at,created_at,updated_at,display_name,conversation_kind,manual_display_name,avatar_status,avatar_checked_at')
                values=[route.get(key) for key in columns.split(',')]
                cur.execute('INSERT INTO saas_connect_conversations('+columns+') VALUES('+','.join(['%s']*len(values))+') ON CONFLICT DO NOTHING',values)
                for row in messages: cur.execute('UPDATE saas_crm_messages SET kommo_lead_id=%s WHERE tenant_id=%s::uuid AND id=%s::uuid',(route['lead_id'],tenant,row['id']))
                cur.execute("UPDATE saas_connect_direct_merge_backups SET state='rolled_back',rolled_back_at=now() WHERE tenant_id=%s::uuid AND pair_id=%s",(tenant,pair_id))
                result={'state':'rolled_back','pair_id':pair_id,'restored_messages':len(messages),'preserved_message_ids':True}
            cur.execute('INSERT INTO saas_connect_direct_merge_requests(tenant_id,request_id,operation,pair_id,result) VALUES(%s::uuid,%s::uuid,%s,%s,%s::jsonb)',(tenant,request_id,operation,pair_id,_json(result)))
            _audit(cur,tenant,actor,'connect_direct_merge_'+operation,'connect_conversation',pair_id,{'channel_id':(item or backup)['channel_id'],'physical':True})
        conn.commit();return result
