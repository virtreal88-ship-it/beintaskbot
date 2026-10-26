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
          source_version TEXT NOT NULL,agent_id UUID NOT NULL,media_policy JSONB NOT NULL,
          capability_hash TEXT NOT NULL UNIQUE,action TEXT NOT NULL CHECK(action IN ('approve','reject')),
          state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','approved','rejected','revoked','expired')),
          expires_at TIMESTAMPTZ NOT NULL,consumed_at TIMESTAMPTZ NULL,
          campaign_id UUID NULL,fragment_id UUID NULL,route JSONB NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        cur.execute('CREATE INDEX IF NOT EXISTS saas_ai_single_message_consent_live_idx ON saas_ai_single_message_extraction_consents(tenant_id,owner_id,expires_at) WHERE state=\'pending\'')


def _parse_args(data):
    required={'message_id','channel_id','lead_id'}
    if not isinstance(data,dict) or set(data)!=required:
        raise TenantLinearError('Invalid single-message extraction review.',400)
    return data


def _preview(source,policy,model):
    # No body, contact identity, raw metadata, provider ID or hidden dialogue
    # key leaks into the model-visible result.
    return {'message_id':source['message_id'],'attachment_kind':source['kind'],
      'channel_id':source['channel_id'],'agent_id':model,'media_policy':policy,
      'execution':'existing_guarded_campaign_worker'}


def prepare(cur,cfg,data):
    """Create one expiring component capability; this is never an approval."""
    if DRAFT_SCOPE not in set(cfg.get('granted_scopes') or []):
        raise TenantLinearError('Additional OAuth scope required.',403)
    if not enabled_for_manual_bridge():
        raise TenantLinearError('Owner component approval is not enabled.',403)
    data=_parse_args(data);source=preflight._source(cur,cfg,data)
    model,_,ready,reason=preflight._model(cur,cfg)
    if not ready: raise TenantLinearError('Extraction configuration is unavailable: '+reason,409)
    tenant,owner,connection=_connection_profile(cur,cfg,required_scopes=(DRAFT_SCOPE,))
    policy=preflight.policy_for(source['kind'])
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
      ('single-message-extraction:'+tenant+':'+source['message_id']+':'+source['version'],))
    # The same source/version gets a single pending consent.  A model cannot
    # obtain a second capability to race a live owner review.
    cur.execute('''SELECT * FROM saas_ai_single_message_extraction_consents
      WHERE tenant_id=%s::uuid AND message_id=%s::uuid AND source_version=%s
        AND (state='approved' OR (state='pending' AND consumed_at IS NULL))
      ORDER BY CASE WHEN state='approved' THEN 0 ELSE 1 END,created_at DESC LIMIT 1 FOR UPDATE''',(tenant,source['message_id'],source['version']))
    old=cur.fetchone()
    # SQL is the authority on TTL at completion; do not parse adapter-specific
    # timestamp wrappers merely to decide whether an already exact receipt may
    # be reopened.
    if old and old['state'] in {'approved','pending'}:
        if old['state']=='approved':
            return {'consent_id':str(old['id']),'state':'approved','exact_preview':_preview(source,old['media_policy'],cfg['AI_LEARNING_MCP_AGENT_ID']),
                    'execution':'existing_guarded_campaign_worker'}
        raise TenantLinearError('An exact owner consent is already pending.',409)
    # Reject/revoke/expiry never silently reactivates an old capability.  A
    # fresh record is deliberately required after an explicit new launch.
    secret=secrets.token_urlsafe(32); key=str(uuid.uuid4())
    cur.execute('''INSERT INTO saas_ai_single_message_extraction_consents
      (id,tenant_id,owner_id,oauth_connection_id,message_id,channel_id,lead_id,conversation_id,
       source_version,agent_id,media_policy,capability_hash,action,expires_at)
      VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s::uuid,%s::jsonb,%s,\'approve\',now()+interval \'10 minutes\')''',
      (key,tenant,owner,connection,source['message_id'],source['channel_id'],source['lead_id'],source['conversation_id'],
       source['version'],cfg['AI_LEARNING_MCP_AGENT_ID'],json.dumps(policy),_hash(secret)))
    # A separate reject capability prevents action substitution while sharing
    # the exact source and connection fence.
    reject=secrets.token_urlsafe(32); reject_key=str(uuid.uuid4())
    cur.execute('''INSERT INTO saas_ai_single_message_extraction_consents
      (id,tenant_id,owner_id,oauth_connection_id,message_id,channel_id,lead_id,conversation_id,
       source_version,agent_id,media_policy,capability_hash,action,expires_at)
      VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s::uuid,%s::jsonb,%s,\'reject\',now()+interval \'10 minutes\')''',
      (reject_key,tenant,owner,connection,source['message_id'],source['channel_id'],source['lead_id'],source['conversation_id'],
       source['version'],cfg['AI_LEARNING_MCP_AGENT_ID'],json.dumps(policy),_hash(reject)))
    _audit(cur,tenant,owner,'single_message_extraction_consent_prepared','crm_message',source['message_id'],{'channel_id':source['channel_id'],'kind':source['kind']})
    return {'consent_id':key,'state':'pending','owner_consent_required':True,'exact_preview':_preview(source,policy,cfg['AI_LEARNING_MCP_AGENT_ID']),
      '_component_meta':{'ui':{'resourceUri':RESOURCE_URI},'owner_single_extraction':{
        'consent_ids':{'approve':key,'reject':reject_key},'capabilities':{'approve':secret,'reject':reject},'exact_preview':_preview(source,policy,cfg['AI_LEARNING_MCP_AGENT_ID'])}}}


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
    cur.execute("SELECT 1 FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND status<>'stopped' FOR UPDATE",(tenant,))
    if cur.fetchone(): raise TenantLinearError('Another knowledge campaign is active.',409)
    amount=reservation(policy);campaign_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'single-message-extraction:'+str(row['id'])))
    at=_occurred(source['occurred_at']); route={'provider':'connect','channel_id':source['channel_id'],
      'conversation_id':source['conversation_id'],'history_mode':'channel','message_ids':[source['message_id']],
      'source_version':source['version'],'period_start':at.isoformat(),'period_end':(at+timedelta(seconds=1)).isoformat(),
      'analysis_agent_id':str(row['agent_id']),**policy,'allow_vision':bool(policy['max_vision_calls']),
      'approved_channel_ids':[source['channel_id']]}
    cur.execute('''INSERT INTO saas_knowledge_campaigns
      (tenant_id,id,created_by,sources,analysis_agent_id,status,approved_by,approved_at,max_calls,max_tokens,media_policy)
      VALUES(%s::uuid,%s::uuid,%s,%s::jsonb,%s::uuid,\'running\',%s,now(),%s,%s,%s::jsonb)''',
      (tenant,campaign_id,row['owner_id'],json.dumps([source['channel_id']]),row['agent_id'],row['owner_id'],amount['calls'],amount['tokens'],json.dumps(policy)))
    campaign={'tenant_id':tenant,'id':campaign_id,'sources':[source['channel_id']],'media_policy':policy}
    fragment=campaigns.enqueue(cur,profile,campaign,route)
    cur.execute('''UPDATE saas_ai_single_message_extraction_consents SET state=\'approved\',consumed_at=now(),
      campaign_id=%s::uuid,fragment_id=%s::uuid,route=%s::jsonb,updated_at=now()
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND state=\'pending\' RETURNING id''',
      (campaign_id,fragment,json.dumps(route),tenant,row['id']))
    if not cur.fetchone(): raise TenantLinearError('Owner consent was already used.',409)
    return {'consent_id':str(row['id']),'state':'approved','campaign_id':campaign_id,'fragment_id':fragment,
      'execution':'queued_existing_guarded_campaign_worker'}


def complete(cur,cfg,*,consent_id,capability,action):
    if not enabled_for_manual_bridge(): raise TenantLinearError('Owner component approval is not enabled.',403)
    if action not in {'approve','reject'} or not isinstance(capability,str) or not 32<=len(capability)<=200:
        raise TenantLinearError('Invalid owner extraction decision.',400)
    try: consent_id=identifier(consent_id)
    except TenantLinearError: raise TenantLinearError('Invalid owner extraction decision.',400) from None
    tenant,owner,connection=_connection_profile(cur,cfg,required_scopes=(DRAFT_SCOPE,))
    cur.execute('''SELECT * FROM saas_ai_single_message_extraction_consents WHERE tenant_id=%s::uuid AND id=%s::uuid
      AND owner_id=%s AND oauth_connection_id=%s::uuid AND capability_hash=%s AND action=%s
      AND state=\'pending\' AND consumed_at IS NULL AND expires_at>now() FOR UPDATE''',
      (tenant,consent_id,owner,connection,_hash(capability),action))
    row=cur.fetchone()
    if not row: raise TenantLinearError('Owner extraction consent is unavailable, expired, or already used.',409)
    source=preflight._source(cur,cfg,{'message_id':str(row['message_id']),'channel_id':str(row['channel_id']),'lead_id':row['lead_id']})
    if source['version']!=row['source_version'] or source['conversation_id']!=row['conversation_id']:
        raise TenantLinearError('Source changed; review it again.',409)
    # Mark every sibling capability terminal before a campaign can exist.
    cur.execute('''UPDATE saas_ai_single_message_extraction_consents SET consumed_at=now(),updated_at=now()
      WHERE tenant_id=%s::uuid AND message_id=%s::uuid AND source_version=%s AND state=\'pending\' AND consumed_at IS NULL''',
      (tenant,row['message_id'],row['source_version']))
    if action=='reject':
        cur.execute("UPDATE saas_ai_single_message_extraction_consents SET state='rejected' WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,row['id']))
        _audit(cur,tenant,owner,'single_message_extraction_consent_rejected','crm_message',row['message_id'],{})
        return {'consent_id':consent_id,'state':'rejected'}
    result=_activate(cur,cfg,row,source)
    _audit(cur,tenant,owner,'single_message_extraction_consent_approved','crm_message',row['message_id'],{'campaign_id':result['campaign_id']})
    return result


def definitions():
    meta={'ui':{'visibility':['app'],'resourceUri':RESOURCE_URI},'openai/outputTemplate':RESOURCE_URI}
    return [
      {'name':PREPARE_NAME,'description':'Prepare a component-only exact owner consent for one existing attachment. It cannot read media, reserve money, start extraction, or approve itself.',
       'inputSchema':{'type':'object','properties':{'message_id':{'type':'string','format':'uuid'},'channel_id':{'type':'string','format':'uuid'},'lead_id':{'type':'integer','minimum':1}},'required':['message_id','channel_id','lead_id'],'additionalProperties':False},
       'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}],
       '_meta':{'ui':{'visibility':['model','app'],'resourceUri':RESOURCE_URI},'openai/outputTemplate':RESOURCE_URI}},
      {'name':COMPLETE_NAME,'description':'TEST-ONLY app completion of one exact owner consent. It rechecks OAuth owner, connection, source version and scope, then queues one bounded existing Campaign fragment. It never calls the provider directly.',
       'inputSchema':{'type':'object','properties':{'consent_id':{'type':'string','format':'uuid'},'capability':{'type':'string','minLength':32,'maxLength':200},'action':{'type':'string','enum':['approve','reject']}},'required':['consent_id','capability','action'],'additionalProperties':False},
       'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}], '_meta':meta}]
