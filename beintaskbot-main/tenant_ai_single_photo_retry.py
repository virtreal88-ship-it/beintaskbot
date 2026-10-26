"""One owner-consented child of an exact unknown image extraction.

This module only validates existing single-message consent records. It never
resets an extraction/cache/hold or calls a provider. Ordinary worker fences and
the shared spend ledger remain mandatory for the child.
"""
import math

from tenant_linear_policy import TenantLinearError, identifier
import tenant_ai_knowledge_extraction_preflight as preflight


def quote(model, policy):
    from tenant_ai_spend_budget import price, media_price
    text=price(model);vision=media_price(model,'vision_low')
    if text is None or vision is None:
        raise TenantLinearError('Retry tariff unavailable.',409)
    total=math.ceil(16000*text[0]/1_000_000)+math.ceil(1800*text[1]/1_000_000)+vision*policy['max_vision_calls']
    return {'model':model,'text_input_per_million':text[0],'text_output_per_million':text[1],
      'vision_low_per_call':vision,'max_microusd':int(total),'max_main_calls':1,
      'max_vision_calls':policy['max_vision_calls'],'max_asr_calls':0}


def parent(cur,tenant,owner,source,prior_id,agent,model):
    """The parent must itself have been approved through the exact owner flow."""
    cur.execute('''SELECT e.source_refs,e.media_results,e.route,e.status,c.id AS campaign_id,
      x.message_id,x.channel_id,x.source_version,x.body_hash,x.external_id,x.lead_id,
      x.conversation_id,x.agent_id,x.owner_id,x.retry_of,r.model
      FROM saas_knowledge_extractions e
      JOIN saas_ai_single_message_extraction_consents x ON x.tenant_id=e.tenant_id AND x.fragment_id=e.id
      JOIN saas_knowledge_campaign_fragments f ON f.tenant_id=e.tenant_id AND f.id=e.id
      JOIN saas_knowledge_campaigns c ON c.tenant_id=f.tenant_id AND c.id=f.campaign_id
      JOIN saas_ai_agent_runs r ON r.tenant_id=e.tenant_id AND r.id=e.id
      WHERE e.tenant_id=%s::uuid AND e.id=%s::uuid AND e.status='unknown'
        AND e.completed_at IS NOT NULL AND e.deadline<now() AND f.status='unknown'
        AND x.state='approved' AND x.action='approve' AND x.message_kind='image'
        AND c.status IN ('blocked','paused','stopped') FOR SHARE OF e,x,f,c,r''',(tenant,identifier(prior_id)))
    old=cur.fetchone()
    if (not old or old['retry_of'] or old['owner_id']!=owner or source['kind']!='image'
        or str(old['agent_id'])!=str(agent) or old['model']!=model
        or any(str(old[key])!=str(source[name]) for key,name in (
          ('message_id','message_id'),('channel_id','channel_id'),('lead_id','lead_id'),
          ('source_version','version'),('body_hash','body_hash'),('external_id','external_id'),
          ('conversation_id','conversation_id')))):
        raise TenantLinearError('Exact unknown photo parent is unavailable or changed.',409)
    refs=old['source_refs'] or []
    if len(refs)!=1 or any(str(refs[0].get(key,''))!=str(source[name]) for key,name in (
        ('id','message_id'),('channel_id','channel_id'),('lead_id','lead_id'),
        ('media_version','version'),('external_id','external_id'),('media_kind','kind'))):
        raise TenantLinearError('Exact parent evidence changed.',409)
    # Legacy consent and extraction hashes use different JSON Unicode settings.
    # Validate each against its own existing serializer, never rewrite receipts.
    from tenant_knowledge_candidates_store import digest
    cur.execute('SELECT body FROM saas_crm_messages WHERE tenant_id=%s::uuid AND id=%s::uuid',
      (tenant,source['message_id']))
    current=cur.fetchone()
    if not current or refs[0].get('body_hash')!=digest(str(current.get('body') or '')):
        raise TenantLinearError('Exact parent evidence changed.',409)
    results=old['media_results'] or []
    if (len(results)!=1 or str(results[0].get('id'))!=source['message_id']
        or results[0].get('status')!='unknown' or results[0].get('failure_stage')!='visual_analysis'
        or not isinstance(results[0].get('sha256'),str) or len(results[0]['sha256'])!=64):
        raise TenantLinearError('Unknown visual attempt identity is unavailable.',409)
    cur.execute('''SELECT fingerprint,token,metadata FROM saas_knowledge_media_cache
      WHERE tenant_id=%s::uuid AND state IN ('unknown','running')
        AND metadata->>'kind'='vision' AND metadata->>'model'=%s
        AND metadata->>'channel_id'=%s AND metadata->>'sha256'=%s
        AND NOT (metadata ? 'owner_retry')
        AND metadata->'origin'='{}'::jsonb LIMIT 2 FOR SHARE''',
      (tenant,model,source['channel_id'],results[0]['sha256']))
    caches=cur.fetchall()
    if len(caches)!=1:
        raise TenantLinearError('Exact unknown visual cache is unavailable or ambiguous.',409)
    cache=caches[0]
    return {'campaign_id':str(old['campaign_id']),
      'cache':{'fingerprint':cache['fingerprint'],'token':str(cache['token']),'sha256':results[0]['sha256']}}


def prepare(cur,cfg,data,source,model,policy):
    prior=identifier(data['retry_of']);request_id=identifier(data['request_id'])
    if data['source_version']!=source['version']:
        raise TenantLinearError('Retry source version changed.',409)
    old=parent(cur,cfg['AI_LEARNING_MCP_TENANT_ID'],cfg['actor_id'],source,prior,cfg['AI_LEARNING_MCP_AGENT_ID'],model)
    amount=quote(model,policy)
    _,ready=preflight._budget(cur,cfg['AI_LEARNING_MCP_TENANT_ID'],model,'',policy)
    if not ready:raise TenantLinearError('Retry budget unavailable; old unknown holds remain charged to the cap.',409)
    return {'retry_of':prior,'retry_request_id':request_id,'retry_budget':amount,
      'retry_cache':old['cache'],'retry_token_id':identifier(cfg['oauth_token_id'])}


def preview(context):
    if not context or not context.get('retry_of'):return {}
    return {'retry_of':str(context['retry_of']),'one_use_retry':True,
      'maximum_additional_microusd':context['retry_budget']['max_microusd'],
      'maximum_additional_usd':format(context['retry_budget']['max_microusd']/1_000_000,'.6f'),
      'prior_charge_unknown':True,'old_reservations_preserved':True,
      'warning':'One retry of this exact photo. The previous provider outcome and charge are unknown. Its money hold remains; this retry may incur an additional charge.'}


def validate(cur,row,source,*,budget_available=False):
    """Fresh owner/token, source, tariff and prior-cache fences, in the mutation tx."""
    if not row.get('retry_of'):return None
    import tenant_ai_single_message_extraction as consent
    cfg={'AI_LEARNING_MCP_TENANT_ID':str(row['tenant_id']),
      'AI_LEARNING_MCP_AGENT_ID':str(row['agent_id']),'AI_LEARNING_MCP_CHANNEL_ID':str(row['channel_id']),
      'actor_id':row['owner_id'],'oauth_connection_id':str(row['oauth_connection_id']),
      'oauth_token_id':str(row['retry_token_id'])}
    consent._connection_profile(cur,cfg,required_scopes=(consent.DRAFT_SCOPE,))
    cur.execute('SELECT expires_at>now() AS live FROM saas_ai_single_message_extraction_consents WHERE tenant_id=%s::uuid AND id=%s::uuid',
      (row['tenant_id'],row['id']))
    if not (cur.fetchone() or {}).get('live'):
        raise TenantLinearError('One-use retry consent expired.',409)
    old=parent(cur,str(row['tenant_id']),row['owner_id'],source,str(row['retry_of']),row['agent_id'],row['model_id'])
    if old['cache']!=row['retry_cache'] or quote(row['model_id'],row['media_policy'])!=row['retry_budget']:
        raise TenantLinearError('Retry attempt or tariff changed; review again.',409)
    if budget_available:
        _,ready=preflight._budget(cur,str(row['tenant_id']),row['model_id'],'',row['media_policy'])
        if not ready:raise TenantLinearError('Retry budget unavailable; old unknown holds remain charged to the cap.',409)
    return old


def authority(cur,tenant,fragment_id):
    """Only an approved, consumed, persisted child may bypass its exact parent."""
    cur.execute('''SELECT x.* FROM saas_ai_single_message_extraction_consents x
      JOIN saas_knowledge_campaign_fragments f ON f.tenant_id=x.tenant_id AND f.id=x.fragment_id
      JOIN saas_knowledge_campaigns c ON c.tenant_id=f.tenant_id AND c.id=f.campaign_id
      WHERE x.tenant_id=%s::uuid AND x.fragment_id=%s::uuid AND x.retry_of IS NOT NULL
        AND x.action='approve' AND x.state='approved' AND x.consumed_at IS NOT NULL
        AND f.status IN ('pending','running') AND c.status='running'
        AND f.route=x.route AND c.progress->>'mode'='single_message'
        AND c.created_by=x.owner_id AND c.approved_by=x.owner_id FOR SHARE OF x,f,c''',(tenant,fragment_id))
    row=cur.fetchone()
    if not row:raise TenantLinearError('Owner-authorized photo retry is unavailable.',409)
    import tenant_ai_single_message_extraction as consent
    consent.dispatch_fence(cur,tenant,fragment_id)
    return row


def cache_namespace(cur,item,base_fingerprint,meta):
    row=authority(cur,item['tenant'],item['id'])
    if (str(row['retry_of'])!=str(item['route'].get('retry_of'))
        or str(row['id'])!=item.get('_owner_retry_id')
        or row['retry_cache']['fingerprint']!=base_fingerprint or row['retry_cache']['sha256']!=meta['sha256']):
        raise TenantLinearError('Retry cache or image bytes changed.',409)
    from tenant_knowledge_candidates_store import digest
    return digest({'parent_cache':base_fingerprint,'owner_retry_consent':str(row['id'])})
