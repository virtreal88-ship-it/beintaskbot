"""Conservative, tenant-scoped logical merges of provider-proven direct chats.

This module deliberately does not rewrite CRM rows.  A merge is an auditable
overlay between two existing direct WhatsApp routes: message IDs, attachments,
timestamps, operator attribution and all historical case references therefore
remain exactly where they were recorded.  Canonical routing and timeline readers use an explicit retained-route mapping;
source messages, media routes and CRM references remain independently addressable.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid

from tenant_ai_agents_store import _audit
from tenant_linear_policy import TenantLinearError


MERGE_SCOPE = 'ai_learning.dialogue_merge'


def ensure_schema(conn):
    from tenant_connect_transport import _schema
    _schema(conn)
    with conn.cursor() as cur:
        from tenant_connect_owner_identity import schema as owner_identity_schema
        owner_identity_schema(cur)
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_dialogue_merges(
          id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          pair_id TEXT NOT NULL, channel_id UUID NOT NULL,
          canonical_conversation_id TEXT NOT NULL, split_conversation_id TEXT NOT NULL,
          canonical_lead_id BIGINT NOT NULL, split_lead_id BIGINT NOT NULL,
          identity_digest TEXT NOT NULL, snapshot JSONB NOT NULL,
          state TEXT NOT NULL CHECK(state IN ('merged','rolled_back')),
          merged_by BIGINT NOT NULL, merged_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          rolled_back_by BIGINT NULL, rolled_back_at TIMESTAMPTZ NULL,
          UNIQUE(tenant_id,pair_id),
          CHECK(canonical_conversation_id<>split_conversation_id),
          FOREIGN KEY(tenant_id,merged_by) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT,
          FOREIGN KEY(tenant_id,rolled_back_by) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_dialogue_merge_requests(
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          request_id UUID NOT NULL, operation TEXT NOT NULL CHECK(operation IN ('merge','rollback')),
          pair_id TEXT NOT NULL, precondition TEXT NOT NULL, result JSONB NOT NULL,
          created_by BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,request_id),
          FOREIGN KEY(tenant_id,created_by) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT
        )''')


def _secret(cfg):
    value=str(cfg.get('AI_LEARNING_MCP_CURSOR_SECRET') or '')
    if not value: raise TenantLinearError('Dialogue merge identity is unavailable.',503)
    return value.encode()


def _token(cfg, *parts):
    value='\x1f'.join(str(part) for part in parts).encode()
    return hmac.new(_secret(cfg),value,hashlib.sha256).hexdigest()


def _owner(cur,cfg):
    tenant=str(uuid.UUID(str(cfg['AI_LEARNING_MCP_TENANT_ID'])))
    actor=int(cfg['actor_id'])
    cur.execute('''SELECT m.role,m.active,t.status FROM saas_tenant_members m
      JOIN saas_tenants t ON t.id=m.tenant_id
      WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s FOR SHARE OF m,t''',(tenant,actor))
    row=cur.fetchone() or {}
    if row.get('role')!='owner' or row.get('active') is not True or row.get('status')!='active':
        raise TenantLinearError('Company owner access required.',403)
    return tenant,actor


def _pairs(cur,cfg):
    """Return only same-channel, non-group pairs proven by signed Connect data."""
    tenant,actor=_owner(cur,cfg); channel=str(uuid.UUID(str(cfg['AI_LEARNING_MCP_CHANNEL_ID'])))
    from tenant_connect_identity import inventory
    scanned=inventory(cur,tenant,channel);routes=scanned['routes']
    from tenant_connect_owner_identity import merge_proofs
    owner_proofs=merge_proofs(cur,tenant,channel)
    owner_keys={(p['canonical_id'],p['split_id']) for p in owner_proofs}
    proofs=[p for p in scanned['pairs'] if (p['canonical_id'],p['split_id']) not in owner_keys]+owner_proofs
    items=[]
    for proof in proofs:
        jid,lid=proof['canonical_id'],proof['split_id'];receipts=proof['receipts']
        left,right=routes.get(jid),routes.get(lid)
        reasons=list(proof['conflicts'])
        if proof.get('reviewable_conflicts')==['historical_route_split']:
            reasons=[r for r in reasons if r!='provider_identity_conflict']
        pair_id=_token(cfg,'dialogue-pair',tenant,channel,jid,lid)
        canonical={'n':0,'incoming':0,'outgoing':0};split={'n':0,'incoming':0,'outgoing':0}
        if left:
            cur.execute('''SELECT count(*) AS n,count(*) FILTER(WHERE direction='incoming') AS incoming,
          count(*) FILTER(WHERE direction='outgoing') AS outgoing
          FROM saas_crm_messages WHERE tenant_id=%s::uuid AND channel='connect:'||%s::text AND kommo_lead_id=%s''',(tenant,channel,left['lead_id']))
            canonical=dict(cur.fetchone() or canonical)
        if right:
            cur.execute('''SELECT count(*) AS n,count(*) FILTER(WHERE direction='incoming') AS incoming,
          count(*) FILTER(WHERE direction='outgoing') AS outgoing
          FROM saas_crm_messages WHERE tenant_id=%s::uuid AND channel='connect:'||%s::text AND kommo_lead_id=%s''',(tenant,channel,right['lead_id']))
            split=dict(cur.fetchone() or split)
        cur.execute('''SELECT state FROM saas_ai_dialogue_merges WHERE tenant_id=%s::uuid AND pair_id=%s FOR SHARE''',(tenant,pair_id))
        mapping=cur.fetchone() or {}
        identity_digest=_token(cfg,'provider-evidence',tenant,channel,jid,lid,receipts,proof['sources'],reasons,proof.get('message_refs',[]))
        dependency_counts={}; snapshot_digest=''
        if left and right and not reasons and mapping.get('state')!='merged':
            from tenant_connect_dialog_equivalence import snapshot,conflicts
            before=snapshot(cur,tenant,channel,[jid,lid],[left['lead_id'],right['lead_id']])
            reasons.extend(conflicts(cur,tenant,channel,[jid,lid],before))
            dependency_counts={k:len(v) for k,v in before.items() if k not in {'messages','routes','aliases','references'}}
            dependency_counts['references']=before['references']
            snapshot_digest=_token(cfg,'retained-dependencies',json.dumps(before,sort_keys=True))
        state=str(mapping.get('state') or 'candidate')
        cur.execute("SELECT to_regclass('saas_connect_direct_merge_backups') AS present")
        if (cur.fetchone() or {}).get('present'):
            from tenant_connect_direct_reconcile import _id
            cur.execute("SELECT state FROM saas_connect_direct_merge_backups WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND pair_id=%s",(tenant,channel,_id(tenant,channel,jid,lid)))
            if (cur.fetchone() or {}).get('state')=='merged':
                state='merged'
                reasons=[]
        item={'pair_id':pair_id,'state':state,'eligible':not reasons and state!='merged','provider_receipts':receipts,
              'canonical':{key:int(canonical.get(key) or 0) for key in ('n','incoming','outgoing')},
              'split':{key:int(split.get(key) or 0) for key in ('n','incoming','outgoing')},
              'conflicts':sorted(set(reasons)), 'identity_digest':identity_digest,'evidence_sources':proof['sources'],
              'canonical_lead_id':int(left['lead_id']) if left else None,
              'split_lead_id':int(right['lead_id']) if right else None,
              'dependency_counts':dependency_counts,'retains_source_rows':True}
        if proof.get('owner_assertion_id'):
            item['identity_source']='owner_assertion'
            item['assertion_id']=proof['owner_assertion_id']
            item['eligible']=False  # Re-merge requires a fresh explicit assertion.
        item['precondition']=_token(cfg,'dialogue-precondition',pair_id,state,identity_digest,
                                    item['canonical']['n'],item['split']['n'],snapshot_digest)
        if state=='merged':
            from tenant_connect_dialog_equivalence import verification
            checked=verification(cur,tenant,channel,pair_id)
            if checked is not None:item['verification']=checked
        items.append(item)
    return tenant,actor,channel,items


def preview(cur,cfg,args):
    if not isinstance(args,dict) or set(args)-{'pair_id','search'}: raise TenantLinearError('Invalid dialogue duplicate request.',400)
    search=args.get('search','')
    if not isinstance(search,str) or len(search)>100:raise TenantLinearError('Invalid dialogue search.',400)
    tenant,_,channel,items=_pairs(cur,cfg)
    if args.get('pair_id'):
        items=[item for item in items if hmac.compare_digest(item['pair_id'],str(args['pair_id']))]
        if not items: raise TenantLinearError('Verified dialogue pair is unavailable.',404)
    # Identity digests are only a server-side freshness input, never a
    # customer/contact identifier.  Do not expose them to MCP.
    from tenant_connect_identity import inventory
    scan=inventory(cur,tenant,channel)
    from tenant_connect_phone_evidence import normalize_phone
    phone_search=normalize_phone(search)
    phone_evidence=scan['phone_evidence']
    routes=[]
    for identity,row in sorted(scan['routes'].items()):
        phones=phone_evidence['phones_by_identity'].get(identity,set())
        stored_match=bool(phone_search and normalize_phone(str(row.get('phone') or ''))==phone_search)
        proven_match=bool(phone_search and phones=={phone_search})
        if search and not (stored_match or (phone_search and phone_search in phones)
                or any(search.casefold() in str(row.get(field) or '').casefold()
                       for field in ('conversation_id','contact_name','display_name','manual_display_name','phone'))):continue
        reasons=sorted({reason for p in scan['pairs'] if identity in {p['canonical_id'],p['split_id']} for reason in p['conflicts']})
        reasons=sorted(set(reasons)|set(phone_evidence['conflicts'].get(identity,[])))
        proof=phone_evidence['evidence_by_identity'].get(identity,{'sources':[],'message_refs':[]})
        cur.execute('''SELECT count(*) AS n FROM saas_crm_messages WHERE tenant_id=%s::uuid
          AND kommo_lead_id=%s AND channel<>%s''',(tenant,row['lead_id'],'connect:'+channel))
        legacy=int(cur.fetchone()['n'])
        routes.append({'route_id':_token(cfg,'dialogue-route',tenant,channel,identity),
          'lead_id':int(row['lead_id']),'channel_id':channel,
          'identity_kind':'lid' if identity.endswith('@lid') else 'phone_jid',
          'messages':int(row['messages']),'incoming':int(row['incoming']),'outgoing':int(row['outgoing']),
          'other_channel_messages':legacy,'conflicts':reasons,
          'stored_phone_present':bool(str(row.get('phone') or '').strip()),
          'provider_phone_proven':len(phones)==1,
          'evidence_sources':proof['sources'],'message_refs':proof['message_refs'],
          **({'normalized_phone_matches_search':stored_match,
              'verified_phone_matches_search':proven_match} if phone_search else {}),
          'resolution':'review_required' if reasons else 'identity_not_compared'})
    return {'items':[ {key:value for key,value in item.items() if key!='identity_digest'} for item in items],
            'routes':routes,'scan_complete':scan['complete'],'scanned_routes':scan['scanned_routes'],
            'excluded_routes':scan['excluded_routes'],'unmapped_messages':scan['unmapped_messages'],
            'original_customer_identity':'unknown_until_provider_verified'}


def _request(cur,tenant,request_id,operation,pair_id,precondition):
    cur.execute('''SELECT operation,pair_id,precondition,result FROM saas_ai_dialogue_merge_requests
      WHERE tenant_id=%s::uuid AND request_id=%s::uuid FOR UPDATE''',(tenant,request_id))
    row=cur.fetchone()
    if not row: return None
    if row['operation']!=operation or row['pair_id']!=pair_id or row['precondition']!=precondition:
        raise TenantLinearError('Request id was already used for different dialogue operation.',409)
    result=dict(row['result']);result['idempotent_replay']=True
    return result


def execute(cur,cfg,args):
    if not isinstance(args,dict) or set(args)!={'operation','pair_id','precondition','request_id'}:
        raise TenantLinearError('Invalid dialogue merge request.',400)
    operation=str(args.get('operation') or '')
    if operation not in {'merge','rollback'}: raise TenantLinearError('Invalid dialogue merge operation.',400)
    pair_id=str(args.get('pair_id') or '');precondition=str(args.get('precondition') or '')
    if not re.fullmatch(r'[0-9a-f]{64}',pair_id) or not re.fullmatch(r'[0-9a-f]{64}',precondition):
        raise TenantLinearError('Invalid dialogue merge request.',400)
    try: request_id=str(uuid.UUID(str(args.get('request_id'))))
    except (ValueError,TypeError): raise TenantLinearError('Invalid dialogue merge request.',400) from None
    tenant,actor=_owner(cur,cfg);channel=str(uuid.UUID(str(cfg['AI_LEARNING_MCP_CHANNEL_ID'])))
    from tenant_connect_identity import inventory
    from tenant_connect_dialog_equivalence import lock as lock_equivalence
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('dialogue-merge:'+tenant+':'+channel,))
    scan=inventory(cur,tenant,channel)
    from tenant_connect_owner_identity import merge_proofs
    proof=next((p for p in scan['pairs']+merge_proofs(cur,tenant,channel) if hmac.compare_digest(_token(cfg,'dialogue-pair',tenant,channel,p['canonical_id'],p['split_id']),pair_id)),None)
    if proof: lock_equivalence(cur,tenant,channel,[proof['canonical_id'],proof['split_id']])
    tenant,actor,channel,items=_pairs(cur,cfg)
    replay=_request(cur,tenant,request_id,operation,pair_id,precondition)
    if replay:return replay
    item=next((value for value in items if hmac.compare_digest(value['pair_id'],pair_id)),None)
    if not item or not hmac.compare_digest(item['precondition'],precondition):
        raise TenantLinearError('Dialogue merge preview is stale or unavailable.',409)
    cur.execute('SELECT * FROM saas_ai_dialogue_merges WHERE tenant_id=%s::uuid AND pair_id=%s FOR UPDATE',(tenant,pair_id))
    existing=cur.fetchone()
    if operation=='merge':
        if not item['eligible']:
            raise TenantLinearError('Dialogue pair is not eligible for merge.',409)
        if existing and existing['state']=='merged': raise TenantLinearError('Dialogue pair is already merged.',409)
        snapshot={'canonical_message_count':item['canonical']['n'],'split_message_count':item['split']['n'],
                  'canonical_incoming':item['canonical']['incoming'],'split_incoming':item['split']['incoming'],
                  'provider_receipts':item['provider_receipts'],'logical_only':True,'route_equivalence_version':1}
        # Identity values are intentionally held only in this private row. MCP
        # receives opaque pair IDs and aggregate counts, not JIDs/LIDs/phones.
        # The item was derived under the same cursor transaction.  Fetch pair
        # values again privately rather than accepting a client conversation ID.
        from tenant_connect_identity import inventory
        fresh=inventory(cur,tenant,channel)
        match=None
        for proof in fresh['pairs']:
            if (not proof['conflicts'] or proof.get('reviewable_conflicts')==['historical_route_split']) and hmac.compare_digest(_token(cfg,'dialogue-pair',tenant,channel,proof['canonical_id'],proof['split_id']),pair_id):
                match=(proof['split_id'],proof['canonical_id']);break
        if not match: raise TenantLinearError('Verified dialogue pair is unavailable.',409)
        lid,jid=match
        cur.execute('''SELECT conversation_id,lead_id FROM saas_connect_conversations WHERE tenant_id=%s::uuid
          AND connect_channel_id=%s::uuid AND conversation_id=ANY(%s::text[]) FOR SHARE''',(tenant,channel,[jid,lid]))
        routes={str(row['conversation_id']):row for row in cur.fetchall()}
        if set(routes)!={jid,lid}: raise TenantLinearError('Dialogue route changed; preview again.',409)
        from tenant_connect_dialog_equivalence import snapshot as capture_retained,activate,conflicts as merge_conflicts
        before=capture_retained(cur,tenant,channel,[jid,lid],[routes[jid]['lead_id'],routes[lid]['lead_id']])
        if merge_conflicts(cur,tenant,channel,[jid,lid],before):
            raise TenantLinearError('Dialogue dependencies changed; preview again.',409)
        activate(cur,tenant,channel,pair_id,[jid,lid],[routes[jid]['lead_id'],routes[lid]['lead_id']],before)
        if existing:
            cur.execute('''UPDATE saas_ai_dialogue_merges SET state='merged',snapshot=%s::jsonb,identity_digest=%s,
              merged_by=%s,merged_at=now(),rolled_back_by=NULL,rolled_back_at=NULL WHERE tenant_id=%s::uuid AND pair_id=%s''',
              (json.dumps(snapshot),item['identity_digest'],actor,tenant,pair_id))
        else:
            cur.execute('''INSERT INTO saas_ai_dialogue_merges(id,tenant_id,pair_id,channel_id,canonical_conversation_id,split_conversation_id,
              canonical_lead_id,split_lead_id,identity_digest,snapshot,state,merged_by)
              VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s,%s::jsonb,'merged',%s)''',
              (str(uuid.uuid4()),tenant,pair_id,channel,jid,lid,routes[jid]['lead_id'],routes[lid]['lead_id'],item['identity_digest'],json.dumps(snapshot),actor))
        result={'pair_id':pair_id,'state':'merged','logical_only':True,'retained_route_merge':True,
                'canonical_lead_id':routes[jid]['lead_id'],'source_lead_id':routes[lid]['lead_id'],
                'combined_messages':item['canonical']['n']+item['split']['n'],
                'preserved_message_ids':True,'preserved_source_rows':True,'rollback_available':True,'idempotent_replay':False}
    else:
        if not existing or existing['state']!='merged': raise TenantLinearError('No active dialogue merge to roll back.',409)
        if existing['snapshot'].get('route_equivalence_version')==1:
            from tenant_connect_dialog_equivalence import rollback
            rollback(cur,tenant,channel,pair_id)
        if existing['snapshot'].get('identity_source')=='owner_assertion':
            cur.execute('''UPDATE saas_connect_owner_identity_assertions SET revoked_by=%s,revoked_at=now()
              WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND id=%s::uuid AND revoked_at IS NULL''',
              (actor,tenant,channel,existing['snapshot']['assertion_id']))
        cur.execute('''UPDATE saas_ai_dialogue_merges SET state='rolled_back',rolled_back_by=%s,rolled_back_at=now()
          WHERE tenant_id=%s::uuid AND pair_id=%s''',(actor,tenant,pair_id))
        result={'pair_id':pair_id,'state':'rolled_back','logical_only':True,'preserved_message_ids':True,'idempotent_replay':False}
    cur.execute('''INSERT INTO saas_ai_dialogue_merge_requests(tenant_id,request_id,operation,pair_id,precondition,result,created_by)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s::jsonb,%s)''',(tenant,request_id,operation,pair_id,precondition,json.dumps(result),actor))
    _audit(cur,tenant,actor,'ai_dialogue_merge_'+operation,'ai_dialogue_merge',pair_id,
           {'channel_id':channel,'logical_only':True,'message_counts':{'canonical':item['canonical']['n'],'split':item['split']['n']}})
    return result


def definitions():
    read={'type':'object','properties':{'pair_id':{'type':'string','pattern':'^[0-9a-f]{64}$'},'search':{'type':'string','maxLength':100}},'additionalProperties':False}
    write={'type':'object','properties':{'operation':{'type':'string','enum':['merge','rollback']},'pair_id':{'type':'string','pattern':'^[0-9a-f]{64}$'},'precondition':{'type':'string','pattern':'^[0-9a-f]{64}$'},'request_id':{'type':'string','format':'uuid'}},'required':['operation','pair_id','precondition','request_id'],'additionalProperties':False}
    from tenant_connect_owner_identity import definitions as owner_definitions
    return [
      {'name':'whatsapp_dialogue_duplicate_candidates','description':'Owner-scoped full-channel direct WhatsApp identity inventory with optional search. Reports provider-confirmed aliases and blocked heuristic candidates, route/lead IDs, aggregate counts and conflicts; never phone/JID/LID, text, names, or media. Only eligible provider-proven pairs may be merged.', 'inputSchema':read,'annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':['ai_learning.read']}]},
      {'name':'whatsapp_dialogue_merge','description':'Owner-delegated reversible logical merge for one freshly previewed, provider-proven direct pair. It never rewrites messages, attachments, timestamps, attribution, cases, CRM or WhatsApp; groups and ambiguous identities fail closed.', 'inputSchema':write,'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[MERGE_SCOPE]}]},
    ]+owner_definitions(MERGE_SCOPE)
