"""Exact owner consent for one existing attachment, routed through Campaigns.

The browser-visible launcher only creates a pending consent.  Its component
completion is tied to the validated OAuth owner connection and can create one
bounded Campaign fragment; it never calls a provider itself.  The existing
Campaign worker remains the sole extractor and repeats all route, lease,
budget, credential and media fences before dispatch.
"""
import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from tenant_linear_policy import TenantLinearError, identifier
from tenant_ai_administrator_drafts import DRAFT_SCOPE
from tenant_ai_owner_review_component import RESOURCE_URI, _connection_profile, enabled_for_manual_bridge
from tenant_ai_agents_store import _audit
import tenant_ai_knowledge_extraction_preflight as preflight

PREPARE_NAME='administrator_single_message_extraction_review'
COMPLETE_NAME='administrator_single_message_extraction_component_complete'


def _hash(value): return hashlib.sha256(str(value).encode()).hexdigest()


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_single_message_extraction_consents(
          id UUID PRIMARY KEY,tenant_id UUID NOT NULL,owner_id BIGINT NOT NULL,oauth_connection_id UUID NOT NULL,
          message_id UUID NOT NULL,channel_id UUID NOT NULL,lead_id BIGINT NOT NULL,conversation_id TEXT NOT NULL,
          source_version TEXT NOT NULL,message_kind TEXT NOT NULL,body_hash TEXT NOT NULL,external_id TEXT NOT NULL,
          agent_id UUID NOT NULL,model_id TEXT NOT NULL,asr_model_id TEXT NOT NULL DEFAULT '',config_hash TEXT NOT NULL,media_policy JSONB NOT NULL,
          capability_hash TEXT NOT NULL UNIQUE,action TEXT NOT NULL CHECK(action IN ('approve','reject')),
          state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','approved','rejected','revoked','expired')),
          expires_at TIMESTAMPTZ NOT NULL,consumed_at TIMESTAMPTZ NULL,
          campaign_id UUID NULL,fragment_id UUID NULL,route JSONB NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        cur.execute('CREATE INDEX IF NOT EXISTS saas_ai_single_message_consent_live_idx ON saas_ai_single_message_extraction_consents(tenant_id,owner_id,expires_at) WHERE state=\'pending\'')
        for column,definition in (('message_kind',"TEXT NOT NULL DEFAULT ''"),('body_hash',"TEXT NOT NULL DEFAULT ''"),
                                  ('external_id',"TEXT NOT NULL DEFAULT ''"),('model_id',"TEXT NOT NULL DEFAULT ''"),
                                  ('asr_model_id',"TEXT NOT NULL DEFAULT ''"),('config_hash',"TEXT NOT NULL DEFAULT ''"),
                                  ('retry_of','UUID'),('retry_request_id','UUID'),('retry_token_id','UUID'),
                                  ('photo_model',"TEXT NOT NULL DEFAULT ''"),
                                  ('retry_budget',"JSONB NOT NULL DEFAULT '{}'"),('retry_cache',"JSONB NOT NULL DEFAULT '{}'")):
            cur.execute('ALTER TABLE saas_ai_single_message_extraction_consents ADD COLUMN IF NOT EXISTS '+column+' '+definition)
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS single_photo_retry_once ON saas_ai_single_message_extraction_consents(tenant_id,retry_of) WHERE action='approve' AND fragment_id IS NOT NULL AND retry_of IS NOT NULL")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS single_photo_retry_request ON saas_ai_single_message_extraction_consents(tenant_id,retry_request_id,action) WHERE retry_request_id IS NOT NULL")


def _parse_args(data):
    required={'message_id','channel_id','lead_id'}
    if not isinstance(data,dict) or set(data)-{'photo_model'} not in (required,required|{'retry_of','source_version','request_id'}):
        raise TenantLinearError('Invalid single-message extraction review.',400)
    if 'photo_model' in data and data['photo_model'] not in ('gpt-4.1','gpt-4.1-2025-04-14'):
        raise TenantLinearError('Unsupported photo model.',400)
    return data


def _selection(cur,cfg,photo_model=''):
    if photo_model:
        from tenant_ai_photo_model import select
        return select(cur,cfg,photo_model)
    return preflight._model(cur,cfg)


def _preview(source,policy,model,retry=None):
    # No body, contact identity, raw metadata, provider ID or hidden dialogue
    # key leaks into the model-visible result.
    from tenant_ai_single_photo_retry import preview as retry_preview
    return {'message_id':source['message_id'],'attachment_kind':source['kind'],
      'channel_id':source['channel_id'],'agent_id':model,'media_policy':policy,
      'execution':'existing_guarded_campaign_worker',**({'source_version':source['version']} if retry else {}),**retry_preview(retry),
      **({'photo_model':retry['photo_model'],'photo_only':True,'maximum_additional_microusd':retry['retry_budget']['max_microusd'],'maximum_additional_usd':format(retry['retry_budget']['max_microusd']/1_000_000,'.6f')} if retry and retry.get('photo_model') else {})}


def prepare(cur,cfg,data):
    """Create one expiring component capability; this is never an approval."""
    if DRAFT_SCOPE not in set(cfg.get('granted_scopes') or []):
        raise TenantLinearError('Additional OAuth scope required.',403)
    if not enabled_for_manual_bridge():
        raise TenantLinearError('Owner component approval is not enabled.',403)
    data=_parse_args(data);source=preflight._source(cur,cfg,{k:data[k] for k in ('message_id','channel_id','lead_id')})
    photo_model=data.get('photo_model','')
    if photo_model and source['kind']!='image':raise TenantLinearError('Photo model requires one image.',400)
    model,credential_source,ready,reason=_selection(cur,cfg,photo_model)
    if not ready: raise TenantLinearError('Extraction configuration is unavailable: '+reason,409)
    tenant,owner,connection=_connection_profile(cur,cfg,required_scopes=(DRAFT_SCOPE,))
    from tenant_ai_photo_model import POLICY as PHOTO_POLICY,budget as photo_budget
    policy=dict(PHOTO_POLICY) if photo_model else preflight.policy_for(source['kind'])
    voice_model=preflight.asr_model(cur,cfg,credential_source)
    if policy['max_asr_calls'] and not voice_model:
        raise TenantLinearError('Extraction configuration is unavailable: asr_model_unavailable',409)
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
      ('single-message-extraction:'+tenant+':'+source['message_id']+':'+source['version'],))
    from tenant_ai_single_photo_retry import prepare as prepare_retry
    retry=prepare_retry(cur,cfg,data,source,model,policy) if 'retry_of' in data else {}
    if photo_model:
        retry.update(photo_model=photo_model,retry_budget=photo_budget(cur,tenant,model),retry_token_id=identifier(cfg['oauth_token_id']))
    if retry.get('retry_of'):
        cur.execute('SELECT retry_of,message_id,source_version FROM saas_ai_single_message_extraction_consents WHERE tenant_id=%s::uuid AND retry_request_id=%s::uuid LIMIT 1',(tenant,retry['retry_request_id']))
        prior_request=cur.fetchone()
        if prior_request and (str(prior_request['retry_of'])!=retry['retry_of'] or str(prior_request['message_id'])!=source['message_id'] or prior_request['source_version']!=source['version']):
            raise TenantLinearError('Retry request_id already used for another source.',409)
    # The same source/version gets a single pending consent.  A model cannot
    # obtain a second capability to race a live owner review.
    cur.execute("UPDATE saas_ai_single_message_extraction_consents SET state='expired',updated_at=now() WHERE tenant_id=%s::uuid AND message_id=%s::uuid AND source_version=%s AND state='pending' AND expires_at<=now()",
      (tenant,source['message_id'],source['version']))
    cur.execute('''SELECT * FROM saas_ai_single_message_extraction_consents
      WHERE tenant_id=%s::uuid AND message_id=%s::uuid AND source_version=%s
        AND retry_of IS NOT DISTINCT FROM %s::uuid
        AND ((retry_of IS NOT NULL AND fragment_id IS NOT NULL) OR state='approved' OR (state='pending' AND consumed_at IS NULL))
      ORDER BY CASE WHEN state='approved' THEN 0 ELSE 1 END,created_at DESC LIMIT 1 FOR UPDATE''',(tenant,source['message_id'],source['version'],retry.get('retry_of')))
    old=cur.fetchone()
    # SQL is the authority on TTL at completion; do not parse adapter-specific
    # timestamp wrappers merely to decide whether an already exact receipt may
    # be reopened.
    if old and (old['state'] in {'approved','pending'} or (old.get('retry_of') and old.get('fragment_id'))):
        if old.get('photo_model','')!=photo_model:
            raise TenantLinearError('Existing consent has a different photo selection; use its exact receipt.',409)
        if old.get('retry_of') and old.get('fragment_id'):
            return _retry_receipt(old)
        if old['state']=='approved':
            return {'consent_id':str(old['id']),'state':'approved','exact_preview':_preview(source,old['media_policy'],cfg['AI_LEARNING_MCP_AGENT_ID']),
                    'execution':'existing_guarded_campaign_worker'}
        raise TenantLinearError('An exact owner consent is already pending.',409)
    # Reject/revoke/expiry never silently reactivates an old capability.  A
    # fresh record is deliberately required after an explicit new launch.
    secret=secrets.token_urlsafe(32); key=str(uuid.uuid4())
    cur.execute('''INSERT INTO saas_ai_single_message_extraction_consents
      (id,tenant_id,owner_id,oauth_connection_id,message_id,channel_id,lead_id,conversation_id,
       source_version,message_kind,body_hash,external_id,agent_id,model_id,asr_model_id,config_hash,media_policy,capability_hash,action,expires_at,retry_of,retry_request_id,retry_token_id,retry_budget,retry_cache)
      VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s::uuid,%s,%s,%s,%s::jsonb,%s,\'approve\',now()+interval \'10 minutes\',%s::uuid,%s::uuid,%s::uuid,%s::jsonb,%s::jsonb)''',
      (key,tenant,owner,connection,source['message_id'],source['channel_id'],source['lead_id'],source['conversation_id'],
       source['version'],source['kind'],source['body_hash'],source['external_id'],cfg['AI_LEARNING_MCP_AGENT_ID'],model,voice_model,preflight.config_hash(cur,cfg,credential_source,voice_model),json.dumps(policy),_hash(secret),retry.get('retry_of'),retry.get('retry_request_id'),retry.get('retry_token_id'),json.dumps(retry.get('retry_budget',{})),json.dumps(retry.get('retry_cache',{}))))
    # A separate reject capability prevents action substitution while sharing
    # the exact source and connection fence.
    reject=secrets.token_urlsafe(32); reject_key=str(uuid.uuid4())
    cur.execute('''INSERT INTO saas_ai_single_message_extraction_consents
      (id,tenant_id,owner_id,oauth_connection_id,message_id,channel_id,lead_id,conversation_id,
       source_version,message_kind,body_hash,external_id,agent_id,model_id,asr_model_id,config_hash,media_policy,capability_hash,action,expires_at,retry_of,retry_request_id,retry_token_id,retry_budget,retry_cache)
      VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s::uuid,%s,%s,%s,%s::jsonb,%s,\'reject\',now()+interval \'10 minutes\',%s::uuid,%s::uuid,%s::uuid,%s::jsonb,%s::jsonb)''',
      (reject_key,tenant,owner,connection,source['message_id'],source['channel_id'],source['lead_id'],source['conversation_id'],
       source['version'],source['kind'],source['body_hash'],source['external_id'],cfg['AI_LEARNING_MCP_AGENT_ID'],model,voice_model,preflight.config_hash(cur,cfg,credential_source,voice_model),json.dumps(policy),_hash(reject),retry.get('retry_of'),retry.get('retry_request_id'),retry.get('retry_token_id'),json.dumps(retry.get('retry_budget',{})),json.dumps(retry.get('retry_cache',{}))))
    if photo_model:
        cur.execute('UPDATE saas_ai_single_message_extraction_consents SET photo_model=%s WHERE tenant_id=%s::uuid AND id IN (%s::uuid,%s::uuid)',(photo_model,tenant,key,reject_key))
    _audit(cur,tenant,owner,'single_message_extraction_consent_prepared','crm_message',source['message_id'],{'channel_id':source['channel_id'],'kind':source['kind'],**({'retry_of':retry['retry_of'],'max_microusd':retry['retry_budget']['max_microusd']} if retry.get('retry_of') else {}),**({'photo_model':photo_model,'photo_only':True,'max_microusd':retry['retry_budget']['max_microusd']} if photo_model else {})})
    return {'consent_id':key,'state':'pending','owner_consent_required':True,'exact_preview':_preview(source,policy,cfg['AI_LEARNING_MCP_AGENT_ID'],retry),
      '_component_meta':{'ui':{'resourceUri':RESOURCE_URI},'owner_single_extraction':{
        'consent_ids':{'approve':key,'reject':reject_key},'capabilities':{'approve':secret,'reject':reject},'exact_preview':_preview(source,policy,cfg['AI_LEARNING_MCP_AGENT_ID'],retry)}}}


def _occurred(value):
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError,ValueError): raise TenantLinearError('Source message timestamp is unavailable.',409) from None


def _activate(cur,cfg,row,source):
    """Write one exact campaign and pending fragment; worker owns provider I/O."""
    import tenant_knowledge_campaigns as campaigns
    import tenant_knowledge_campaign_schema as schema
    import tenant_knowledge_candidates_store as store
    from tenant_knowledge_campaign_policy import reservation
    tenant=str(row['tenant_id']);profile={'tenant_id':tenant,'telegram_id':row['owner_id']};policy=row['media_policy']
    schema.create_schema(cur.connection);store.schema(cur.connection)
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('knowledge-campaign:'+tenant,))
    from tenant_ai_single_photo_retry import validate as validate_retry
    prior=validate_retry(cur,row,source,budget_available=True)
    if prior:
        cur.execute("SELECT 1 FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND status IN ('pending','running') LIMIT 1",(tenant,prior['campaign_id']))
        if cur.fetchone():raise TenantLinearError('Prior campaign still has active work.',409)
    excluded=([prior['campaign_id']]+prior.get('ancestor_campaigns',[])) if prior else []
    cur.execute("SELECT 1 FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND campaign_id=ANY(%s::uuid[]) AND status IN ('pending','running') LIMIT 1",(tenant,excluded))
    if cur.fetchone():raise TenantLinearError('Prior lineage still has active work.',409)
    cur.execute("SELECT 1 FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND status<>'stopped' AND NOT(id=ANY(%s::uuid[])) FOR UPDATE",(tenant,excluded))
    if cur.fetchone(): raise TenantLinearError('Another knowledge campaign is active.',409)
    amount=reservation(policy);campaign_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'single-message-extraction:'+str(row['id'])))
    at=_occurred(source['occurred_at']); route={'provider':'connect','channel_id':source['channel_id'],
      'conversation_id':source['conversation_id'],'history_mode':'channel','message_ids':[source['message_id']],
      'source_version':source['version'],'period_start':at.isoformat(),'period_end':(at+timedelta(seconds=1)).isoformat(),
      'analysis_agent_id':str(row['agent_id']),'allow_external_media':policy['allow_external_media'],
      'allow_paid_asr':policy['allow_paid_asr'],'allow_vision':bool(policy['max_vision_calls']),
      'approved_channel_ids':[source['channel_id']]}
    if row.get('retry_of'):route['retry_of']=str(row['retry_of'])
    if row.get('photo_model'):route['photo_model']=row['photo_model']
    cur.execute('''INSERT INTO saas_knowledge_campaigns
      (tenant_id,id,created_by,sources,analysis_agent_id,status,approved_by,approved_at,max_calls,max_tokens,media_policy)
      VALUES(%s::uuid,%s::uuid,%s,%s::jsonb,%s::uuid,\'running\',%s,now(),%s,%s,%s::jsonb)''',
      (tenant,campaign_id,row['owner_id'],json.dumps([source['channel_id']]),row['agent_id'],row['owner_id'],amount['calls'],amount['tokens'],json.dumps(policy)))
    cur.execute("UPDATE saas_knowledge_campaigns SET progress=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",
      (json.dumps({'mode':'single_message','message_id':source['message_id']}),tenant,campaign_id))
    campaign={'tenant_id':tenant,'id':campaign_id,'sources':[source['channel_id']],'media_policy':policy}
    fragment=campaigns.enqueue(cur,profile,campaign,route)
    cur.execute('''UPDATE saas_ai_single_message_extraction_consents SET state=\'approved\',consumed_at=now(),
      campaign_id=%s::uuid,fragment_id=%s::uuid,route=%s::jsonb,updated_at=now()
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND state=\'pending\' RETURNING id''',
      (campaign_id,fragment,json.dumps(route),tenant,row['id']))
    if not cur.fetchone(): raise TenantLinearError('Owner consent was already used.',409)
    return {'consent_id':str(row['id']),'state':'approved','campaign_id':campaign_id,'fragment_id':fragment,
      'execution':'queued_existing_guarded_campaign_worker'}


def complete(cur,cfg,*,consent_id,capability,action,acknowledge_prior_unknown=False):
    if not enabled_for_manual_bridge(): raise TenantLinearError('Owner component approval is not enabled.',403)
    if action not in {'approve','reject'} or not isinstance(capability,str) or not 32<=len(capability)<=200:
        raise TenantLinearError('Invalid owner extraction decision.',400)
    try: consent_id=identifier(consent_id)
    except TenantLinearError: raise TenantLinearError('Invalid owner extraction decision.',400) from None
    tenant,owner,connection=_connection_profile(cur,cfg,required_scopes=(DRAFT_SCOPE,))
    cur.execute('''SELECT retry_of FROM saas_ai_single_message_extraction_consents
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND owner_id=%s AND oauth_connection_id=%s::uuid
        AND capability_hash=%s AND action=%s''',(tenant,consent_id,owner,connection,_hash(capability),action))
    retry_lock=cur.fetchone()
    if retry_lock and retry_lock.get('retry_of'):
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
          ('single-photo-retry:'+tenant+':'+str(retry_lock['retry_of']),))
    # Durable replay authenticates the original capability but never queues twice.
    cur.execute('''SELECT * FROM saas_ai_single_message_extraction_consents WHERE tenant_id=%s::uuid AND id=%s::uuid
      AND owner_id=%s AND oauth_connection_id=%s::uuid AND capability_hash=%s AND action=%s
      AND retry_of IS NOT NULL AND fragment_id IS NOT NULL''',
      (tenant,consent_id,owner,connection,_hash(capability),action))
    replay=cur.fetchone()
    if replay:return _retry_receipt(replay)
    cur.execute('''SELECT message_id,source_version FROM saas_ai_single_message_extraction_consents
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='pending' AND expires_at<=now()''',(tenant,consent_id))
    expired=cur.fetchone()
    if expired:
        # Approve/reject sibling capabilities belong to one launch; when one
        # expires all become terminal before a fresh exact review is possible.
        cur.execute("UPDATE saas_ai_single_message_extraction_consents SET state='expired',updated_at=now() WHERE tenant_id=%s::uuid AND message_id=%s::uuid AND source_version=%s AND state='pending'",(tenant,expired['message_id'],expired['source_version']))
    cur.execute('''SELECT * FROM saas_ai_single_message_extraction_consents WHERE tenant_id=%s::uuid AND id=%s::uuid
      AND owner_id=%s AND oauth_connection_id=%s::uuid AND capability_hash=%s AND action=%s
      AND state=\'pending\' AND consumed_at IS NULL AND expires_at>now() FOR UPDATE''',
      (tenant,consent_id,owner,connection,_hash(capability),action))
    row=cur.fetchone()
    if not row: raise TenantLinearError('Owner extraction consent is unavailable, expired, or already used.',409)
    if row.get('retry_of') and action=='approve' and acknowledge_prior_unknown is not True:
        raise TenantLinearError('Acknowledge the unknown prior charge and this one additional attempt.',400)
    source=preflight._source(cur,cfg,{'message_id':str(row['message_id']),'channel_id':str(row['channel_id']),'lead_id':row['lead_id']})
    model,credential_source,ready,_=_selection(cur,cfg,row.get('photo_model',''))
    voice_model=preflight.asr_model(cur,cfg,credential_source)
    if (not ready or source['version']!=row['source_version'] or source['conversation_id']!=row['conversation_id']
            or source['kind']!=row['message_kind'] or source['body_hash']!=row['body_hash']
            or source['external_id']!=row['external_id'] or model!=row['model_id'] or voice_model!=row['asr_model_id']
            or preflight.config_hash(cur,cfg,credential_source,voice_model)!=row['config_hash']):
        raise TenantLinearError('Source changed; review it again.',409)
    from tenant_ai_photo_model import fence as photo_fence,budget as photo_budget
    photo_fence(cur,row,source)
    if row.get('photo_model'):photo_budget(cur,tenant,row['photo_model'])
    # Mark every sibling capability terminal before a campaign can exist.
    cur.execute('''UPDATE saas_ai_single_message_extraction_consents SET consumed_at=now(),updated_at=now()
      WHERE tenant_id=%s::uuid AND message_id=%s::uuid AND source_version=%s AND state=\'pending\' AND consumed_at IS NULL''',
      (tenant,row['message_id'],row['source_version']))
    if action=='reject':
        cur.execute("UPDATE saas_ai_single_message_extraction_consents SET state='rejected' WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,row['id']))
        _audit(cur,tenant,owner,'single_message_extraction_consent_rejected','crm_message',row['message_id'],{})
        return {'consent_id':consent_id,'state':'rejected'}
    result=_activate(cur,cfg,row,source)
    _audit(cur,tenant,owner,'single_message_extraction_consent_approved','crm_message',row['message_id'],{'campaign_id':result['campaign_id'],**({'photo_model':row['photo_model'],'photo_only':True,'max_microusd':row['retry_budget']['max_microusd']} if row.get('photo_model') else {}),**({'retry_of':str(row['retry_of']),'prior_charge_unknown_acknowledged':True,'max_microusd':row['retry_budget']['max_microusd']} if row.get('retry_of') else {})})
    return result


def dispatch_fence(cur,tenant,fragment_id):
    """Revalidate a consent-bound fragment at dispatch and before generation.

    This takes no caller data: the fragment owns all source identifiers, and a
    changed attachment/config terminates the already-approved work rather than
    reusing an owner capability for different bytes or a different model.
    """
    cur.execute('''SELECT * FROM saas_ai_single_message_extraction_consents
      WHERE tenant_id=%s::uuid AND fragment_id=%s::uuid AND state='approved' LIMIT 1 FOR SHARE''',(tenant,fragment_id))
    row=cur.fetchone()
    if not row: raise TenantLinearError('Single-message owner consent is unavailable.',409)
    cfg={'AI_LEARNING_MCP_TENANT_ID':str(row['tenant_id']),'AI_LEARNING_MCP_AGENT_ID':str(row['agent_id']),
      'AI_LEARNING_MCP_CHANNEL_ID':str(row['channel_id'])}
    source=_source_for_row(cur,cfg,row)
    model,credential_source,ready,_=_selection(cur,cfg,row.get('photo_model','')); voice_model=preflight.asr_model(cur,cfg,credential_source)
    if (not ready or (row['media_policy'].get('max_asr_calls') and not voice_model)
            or source['version']!=row['source_version'] or source['kind']!=row['message_kind']
            or source['body_hash']!=row['body_hash'] or source['external_id']!=row['external_id']
            or model!=row['model_id'] or voice_model!=row['asr_model_id']
            or preflight.config_hash(cur,cfg,credential_source,voice_model)!=row['config_hash']):
        raise TenantLinearError('Owner-approved source or AI configuration changed.',409)
    from tenant_ai_photo_model import fence as photo_fence
    photo_fence(cur,row,source)
    if row.get('retry_of'):
        from tenant_ai_single_photo_retry import validate
        validate(cur,row,source)
    return True


def _source_for_row(cur,cfg,row):
    return preflight._source(cur,cfg,{'message_id':str(row['message_id']),'channel_id':str(row['channel_id']),'lead_id':row['lead_id']})


def _retry_receipt(row):
    return {'consent_id':str(row['id']),'state':row['state'],'campaign_id':str(row['campaign_id']),
      'fragment_id':str(row['fragment_id']),'retry_of':str(row['retry_of']),'replayed':True,
      'execution':'existing_guarded_campaign_worker'}


def definitions():
    meta={'ui':{'visibility':['app'],'resourceUri':RESOURCE_URI},'openai/outputTemplate':RESOURCE_URI}
    return [
      {'name':PREPARE_NAME,'description':'Prepare a component-only exact owner consent for one existing attachment. It cannot read media, reserve money, start extraction, or approve itself. For one additional attempt of an eligible unknown image, supply retry_of (prior extraction UUID), source_version and a unique request_id together. The owner must acknowledge the unknown prior charge in the component; old holds remain. Optional photo_model selects GPT-4.1 for one image observation only (no text/ASR call, no agent/default changes). A model-access failure may receive one model-change recovery; it cannot be retried again.',
       'inputSchema':{'type':'object','properties':{'message_id':{'type':'string','format':'uuid'},'channel_id':{'type':'string','format':'uuid'},'lead_id':{'type':'integer','minimum':1},'retry_of':{'type':'string','format':'uuid'},'source_version':{'type':'string','pattern':'^[a-f0-9]{64}$'},'request_id':{'type':'string','format':'uuid'},'photo_model':{'type':'string','enum':['gpt-4.1','gpt-4.1-2025-04-14']}},'required':['message_id','channel_id','lead_id'],'additionalProperties':False},
       'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}],
       '_meta':{'ui':{'visibility':['model','app'],'resourceUri':RESOURCE_URI},'openai/outputTemplate':RESOURCE_URI}},
      {'name':COMPLETE_NAME,'description':'TEST-ONLY app completion of one exact owner consent. It rechecks OAuth owner, connection, source version and scope, then queues one bounded existing Campaign fragment. It never calls the provider directly.',
       'inputSchema':{'type':'object','properties':{'consent_id':{'type':'string','format':'uuid'},'capability':{'type':'string','minLength':32,'maxLength':200},'action':{'type':'string','enum':['approve','reject']},'acknowledge_prior_unknown':{'type':'boolean'}},'required':['consent_id','capability','action'],'additionalProperties':False},
       'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}], '_meta':meta}]
