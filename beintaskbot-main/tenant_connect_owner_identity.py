"""Explicit owner identity assertions, separate from signed provider evidence.

An assertion links exactly two existing direct routes in one company/channel.
It never writes provider aliases or modifies customer history. The exact phone,
actor, confirmation reference and server timestamp stay in the private receipt.
"""
import hmac
import json
import re
import uuid

from tenant_linear_policy import TenantLinearError

FIELDS = {'canonical_lead_id', 'source_lead_id', 'phone', 'evidence_ref'}


def schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_connect_owner_identity_assertions(
      id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      channel_id UUID NOT NULL, pair_id TEXT NOT NULL,
      canonical_id TEXT NOT NULL, source_id TEXT NOT NULL,
      canonical_lead BIGINT NOT NULL, source_lead BIGINT NOT NULL,
      phone TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'owner_assertion'
        CHECK(source='owner_assertion'),
      owner_id BIGINT NOT NULL, evidence_ref TEXT NOT NULL,
      merge_snapshot JSONB NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      revoked_by BIGINT NULL, revoked_at TIMESTAMPTZ NULL,
      CHECK(canonical_id<>source_id), CHECK(canonical_lead<>source_lead),
      FOREIGN KEY(tenant_id,owner_id) REFERENCES saas_tenant_members(tenant_id,telegram_id),
      FOREIGN KEY(tenant_id,revoked_by) REFERENCES saas_tenant_members(tenant_id,telegram_id),
      FOREIGN KEY(tenant_id,channel_id,canonical_id)
        REFERENCES saas_connect_conversations(tenant_id,connect_channel_id,conversation_id),
      FOREIGN KEY(tenant_id,channel_id,source_id)
        REFERENCES saas_connect_conversations(tenant_id,connect_channel_id,conversation_id))''')
    cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS saas_connect_owner_identity_active
      ON saas_connect_owner_identity_assertions(tenant_id,channel_id,pair_id) WHERE revoked_at IS NULL''')


def _arguments(args):
    from tenant_connect_phone_evidence import normalize_phone
    if not isinstance(args,dict) or set(args)!=FIELDS:
        raise TenantLinearError('Exact owner identity confirmation is required.',400)
    for key in ('canonical_lead_id','source_lead_id'):
        if type(args[key]) is not int or args[key]<=0:
            raise TenantLinearError('Invalid dialogue lead.',400)
    if args['canonical_lead_id']==args['source_lead_id']:
        raise TenantLinearError('Two distinct dialogue leads are required.',400)
    phone=normalize_phone(args['phone'])
    evidence=args['evidence_ref']
    if (not phone or not isinstance(evidence,str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_:/ .#\-]{7,249}',evidence)):
        raise TenantLinearError('Valid international phone and owner confirmation reference required.',400)
    return {**args,'phone':phone}


def _candidate(cur,cfg,args):
    from tenant_ai_dialogue_merge import _owner,_token
    from tenant_connect_identity import inventory
    from tenant_connect_dialog_equivalence import snapshot,conflicts
    tenant,actor=_owner(cur,cfg)
    channel=str(uuid.UUID(str(cfg['AI_LEARNING_MCP_CHANNEL_ID'])))
    scan=inventory(cur,tenant,channel)
    by_lead={int(r['lead_id']):identity for identity,r in scan['routes'].items()}
    ids=[by_lead.get(args[key]) for key in ('canonical_lead_id','source_lead_id')]
    if any(i is None for i in ids):
        raise TenantLinearError('Exact direct dialogue pair is unavailable in this company channel.',404)
    leads=[args['canonical_lead_id'],args['source_lead_id']]
    pair_id=_token(cfg,'dialogue-pair',tenant,channel,*ids)
    before=snapshot(cur,tenant,channel,ids,leads)
    reasons=conflicts(cur,tenant,channel,ids,before,owner_phone=args['phone'])
    # One owner confirmation is not authority to absorb other merged identities.
    cur.execute('''SELECT 1 FROM saas_connect_dialog_links WHERE tenant_id=%s::uuid
      AND channel_id=%s::uuid AND active AND
      (canonical_id=ANY(%s::text[]) OR source_id=ANY(%s::text[])) LIMIT 1''',(tenant,channel,ids,ids))
    if cur.fetchone():reasons.append('route_already_merged')
    evidence=scan['phone_evidence']
    phone_sets={i:sorted(evidence['phones_by_identity'].get(i,set())) for i in ids}
    facts={i:evidence['evidence_by_identity'].get(i,{}) for i in ids}
    digest=_token(cfg,'owner-identity-preview',tenant,channel,actor,
                  json.dumps(args,sort_keys=True),json.dumps(before,sort_keys=True),
                  json.dumps(phone_sets,sort_keys=True),json.dumps(facts,sort_keys=True),sorted(set(reasons)))
    counts={key:len([m for m in before['messages'] if int(m['kommo_lead_id'])==lead])
            for key,lead in zip(('canonical','source'),leads)}
    result={'pair_id':pair_id,'precondition':digest,'eligible':not reasons,
            'conflicts':sorted(set(reasons)),'identity_source':'owner_assertion',
            'canonical_lead_id':leads[0],'source_lead_id':leads[1],
            'canonical_messages':counts['canonical'],'source_messages':counts['source'],
            'canonical_provider_phone_proven':len(phone_sets[ids[0]])==1,
            'source_provider_phone_proven':len(phone_sets[ids[1]])==1,
            'retains_source_rows':True,'reference_counts':before['references'],
            'attachments':sum(bool(m.get('media_url')) or m.get('message_type') not in (None,'','text') for m in before['messages'])}
    return result,tenant,actor,channel,ids,leads,before


def preview(cur,cfg,args):
    return _candidate(cur,cfg,_arguments(args))[0]


def execute(cur,cfg,args):
    from tenant_ai_dialogue_merge import _owner,_request,_audit
    from tenant_connect_dialog_equivalence import lock,activate,verification
    if (not isinstance(args,dict) or set(args)!=FIELDS|{'precondition','request_id','confirm'}
            or args['confirm'] is not True):
        raise TenantLinearError('Explicit exact owner identity confirmation is required.',400)
    supplied=_arguments({k:args[k] for k in FIELDS})
    precondition=str(args['precondition'])
    if not re.fullmatch(r'[0-9a-f]{64}',precondition):
        raise TenantLinearError('Invalid owner identity precondition.',400)
    try:request_id=str(uuid.UUID(str(args['request_id'])))
    except (ValueError,TypeError):raise TenantLinearError('Invalid request id.',400) from None
    tenant,actor=_owner(cur,cfg);channel=str(uuid.UUID(str(cfg['AI_LEARNING_MCP_CHANNEL_ID'])))
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('dialogue-merge:'+tenant+':'+channel,))
    # Resolve only the two authorised IDs, then use the shared webhook lock order.
    from tenant_connect_identity import inventory
    routes=inventory(cur,tenant,channel)['routes']
    by_lead={int(r['lead_id']):i for i,r in routes.items()}
    ids=[by_lead.get(supplied[k]) for k in ('canonical_lead_id','source_lead_id')]
    if any(i is None for i in ids):raise TenantLinearError('Exact dialogue pair is unavailable.',404)
    lock(cur,tenant,channel,ids)
    from tenant_ai_dialogue_merge import _token
    pair_id=_token(cfg,'dialogue-pair',tenant,channel,*ids)
    replay=_request(cur,tenant,request_id,'merge',pair_id,precondition)
    if replay:
        # A request replay must contain the exact original assertion, not merely
        # a previously observed precondition string.
        cur.execute('''SELECT owner_id,phone,evidence_ref,canonical_lead,source_lead
          FROM saas_connect_owner_identity_assertions WHERE tenant_id=%s::uuid
            AND id=%s::uuid''',(tenant,replay.get('assertion_id')))
        old=cur.fetchone() or {}
        if (old.get('owner_id')!=actor or old.get('phone')!=supplied['phone']
                or old.get('evidence_ref')!=supplied['evidence_ref']
                or old.get('canonical_lead')!=supplied['canonical_lead_id']
                or old.get('source_lead')!=supplied['source_lead_id']):
            raise TenantLinearError('Request id was already used for a different owner assertion.',409)
        return replay
    item,tenant,actor,channel,ids,leads,before=_candidate(cur,cfg,supplied)
    if not hmac.compare_digest(item['precondition'],precondition):
        raise TenantLinearError('Owner identity preview changed; preview again.',409)
    if not item['eligible']:raise TenantLinearError('Owner identity pair has unresolved conflicts.',409)
    assertion_id=str(uuid.uuid4())
    rollback_mapping=activate(cur,tenant,channel,pair_id,ids,leads,before,owner_assertion_id=assertion_id)
    cur.execute('''INSERT INTO saas_connect_owner_identity_assertions
      (id,tenant_id,channel_id,pair_id,canonical_id,source_id,canonical_lead,source_lead,phone,owner_id,evidence_ref,merge_snapshot)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)''',
      (assertion_id,tenant,channel,pair_id,*ids,*leads,supplied['phone'],actor,supplied['evidence_ref'],json.dumps(rollback_mapping)))
    saved={'route_equivalence_version':1,'identity_source':'owner_assertion','assertion_id':assertion_id,
           'canonical_message_count':item['canonical_messages'],'split_message_count':item['source_messages']}
    cur.execute('''INSERT INTO saas_ai_dialogue_merges
      (id,tenant_id,pair_id,channel_id,canonical_conversation_id,split_conversation_id,
       canonical_lead_id,split_lead_id,identity_digest,snapshot,state,merged_by)
      VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s,%s::jsonb,'merged',%s)
      ON CONFLICT(tenant_id,pair_id) DO UPDATE SET state='merged',snapshot=EXCLUDED.snapshot,
        identity_digest=EXCLUDED.identity_digest,merged_by=EXCLUDED.merged_by,merged_at=now(),
        rolled_back_at=NULL,rolled_back_by=NULL''',
      (str(uuid.uuid4()),tenant,pair_id,channel,*ids,*leads,precondition,json.dumps(saved),actor))
    result={'pair_id':pair_id,'state':'merged','identity_source':'owner_assertion',
            'assertion_id':assertion_id,'canonical_lead_id':leads[0],'source_lead_id':leads[1],
            'source_provider_phone_proven':item['source_provider_phone_proven'],
            'combined_messages':item['canonical_messages']+item['source_messages'],
            'preserved_message_ids':True,'preserved_source_rows':True,'rollback_available':True,
            'idempotent_replay':False,'verification':verification(cur,tenant,channel,pair_id)}
    cur.execute('''INSERT INTO saas_ai_dialogue_merge_requests
      (tenant_id,request_id,operation,pair_id,precondition,result,created_by)
      VALUES(%s::uuid,%s::uuid,'merge',%s,%s,%s::jsonb,%s)''',
      (tenant,request_id,pair_id,precondition,json.dumps(result),actor))
    _audit(cur,tenant,actor,'ai_dialogue_owner_assertion_merge','ai_dialogue_merge',pair_id,
           {'channel_id':channel,'identity_source':'owner_assertion','assertion_id':assertion_id,
            'canonical_lead_id':leads[0],'source_lead_id':leads[1],
            'source_provider_phone_proven':item['source_provider_phone_proven']})
    return result


def merge_proofs(cur,tenant,channel):
    """Keep owner merges inspectable/rollbackable without provider graph edges."""
    cur.execute('''SELECT m.canonical_conversation_id,m.split_conversation_id,m.state,m.snapshot
      FROM saas_ai_dialogue_merges m WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
        AND snapshot->>'identity_source'='owner_assertion' ''',(tenant,channel))
    result=[{'canonical_id':r['canonical_conversation_id'],'split_id':r['split_conversation_id'],
             'sources':['owner_assertion'],'receipts':0,'message_refs':[],'verified':False,
             'owner_assertion_id':r['snapshot']['assertion_id'],
             'conflicts':[] if r['state']=='merged' else ['fresh_owner_confirmation_required']}
            for r in cur.fetchall()]
    for proof in result:
        assertion=active_link(cur,tenant,channel,proof['canonical_id'])
        if assertion and provider_conflicts(cur,tenant,channel,assertion):
            proof['conflicts'].append('provider_identity_conflict')
    return result


def active_link(cur,tenant,channel,identity):
    # The table is initialized by the normal Connect schema, no per-message DDL.
    cur.execute("SELECT to_regclass('saas_connect_owner_identity_assertions') AS present")
    if not (cur.fetchone() or {}).get('present'):return None
    cur.execute('''SELECT a.*,l.source_id AS linked_source FROM saas_connect_dialog_links l
      JOIN saas_connect_owner_identity_assertions a
        ON a.tenant_id=l.tenant_id AND a.channel_id=l.channel_id AND a.pair_id=l.pair_id
          AND a.id::text=l.snapshot->>'owner_assertion_id'
      WHERE l.tenant_id=%s::uuid AND l.channel_id=%s::uuid AND l.active
        AND (l.source_id=%s OR l.canonical_id=%s) AND a.revoked_at IS NULL''',
      (tenant,channel,identity,identity))
    return cur.fetchone()


def provider_conflicts(cur,tenant,channel,assertion):
    from tenant_connect_phone_evidence import collect
    ids=[assertion['canonical_id'],assertion['source_id']]
    proof=collect(cur,tenant,channel,ids)
    phone=assertion['phone']
    return (proof['phones_by_identity'].get(ids[0],set())!={phone}
            or not proof['phones_by_identity'].get(ids[1],set())<={phone}
            or any(proof['conflicts'].get(i) for i in ids))


def route(cur,tenant,channel,provider_route,data,metadata):
    """Apply only the CRM route overlay. Never change provider alias metadata."""
    assertion=active_link(cur,tenant,channel,provider_route)
    if not assertion:return provider_route,False
    if provider_conflicts(cur,tenant,channel,assertion):return provider_route,True
    metadata['owner_assertion_id']=str(assertion['id'])
    metadata['provider_conversation_id']=provider_route
    return assertion['canonical_id'],False


def definitions(scope):
    properties={'canonical_lead_id':{'type':'integer','minimum':1},
                'source_lead_id':{'type':'integer','minimum':1},
                'phone':{'type':'string','maxLength':40},
                'evidence_ref':{'type':'string','minLength':8,'maxLength':250}}
    read={'type':'object','properties':properties,'required':sorted(FIELDS),'additionalProperties':False}
    write={'type':'object','properties':{**properties,'precondition':{'type':'string','pattern':'^[0-9a-f]{64}$'},
          'request_id':{'type':'string','format':'uuid'},'confirm':{'type':'boolean','const':True}},
          'required':sorted(FIELDS|{'precondition','request_id','confirm'}),'additionalProperties':False}
    return [
      {'name':'whatsapp_dialogue_owner_merge_preview','description':'Read-only exact-pair preview after the company owner explicitly confirms a source dialogue belongs to the canonical verified phone. Requires the owner confirmation reference. Reports provider evidence separately and never clears conflicts.',
       'inputSchema':read,'annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False},
       'securitySchemes':[{'type':'oauth2','scopes':['ai_learning.read']}]},
      {'name':'whatsapp_dialogue_owner_merge','description':'Record one exact explicit owner identity assertion and reversibly link its two direct dialogues after a fresh preview. Use only after the owner personally confirms this exact pair and phone. Preserves history, media, CRM, controls and rollback; never grants provider verification or clears provider conflicts. No sends or AI. Existing dialogue merge rollback revokes the assertion.',
       'inputSchema':write,'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},
       'securitySchemes':[{'type':'oauth2','scopes':[scope]}]},
    ]
