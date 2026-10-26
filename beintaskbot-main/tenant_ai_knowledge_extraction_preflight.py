"""Owner-scoped, read-only admission check for one existing media extraction.

This is deliberately not an execution path.  Existing Knowledge Extraction
still owns provider dispatch, campaign leases, monetary reservations and
idempotency.  A missing approved campaign fragment is a hard denial.
"""
from tenant_linear_policy import TenantLinearError, identifier
from tenant_ai_agent_key_policy import platform_model, platform_model_allowed
from tenant_ai_spend_budget import price, media_price


NAME='administrator_knowledge_extraction_preflight'
DOMAIN='api.openai.com'


def _source(cur,cfg,args):
    if not isinstance(args,dict) or set(args)!={'message_id','channel_id','lead_id'}:
        raise TenantLinearError('Invalid knowledge extraction preflight.',400)
    tenant=cfg['AI_LEARNING_MCP_TENANT_ID']; channel=identifier(args['channel_id']); message=identifier(args['message_id'])
    if channel!=cfg['AI_LEARNING_MCP_CHANNEL_ID']:
        raise TenantLinearError('Source is outside OAuth channel scope.',403)
    try: lead=int(args['lead_id'])
    except (TypeError,ValueError): raise TenantLinearError('Invalid source selection.',400) from None
    if lead<1: raise TenantLinearError('Invalid source selection.',400)
    cur.execute('''SELECT conversation_id FROM saas_connect_conversations
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND lead_id=%s LIMIT 2''',(tenant,channel,lead))
    conversations=cur.fetchall()
    if len(conversations)!=1: raise TenantLinearError('Source conversation is unavailable or ambiguous.',404)
    cur.execute('''SELECT id,message_type FROM saas_crm_messages WHERE tenant_id=%s::uuid AND id=%s::uuid
      AND kommo_lead_id=%s AND channel=%s''',(tenant,message,lead,'connect:'+channel))
    row=cur.fetchone()
    if not row: raise TenantLinearError('Source message is unavailable in the permitted channel.',404)
    if row.get('message_type') not in {'image','audio','voice','video','document'}:
        raise TenantLinearError('Source message has no supported attachment.',409)
    return {'message_id':message,'channel_id':channel,'lead_id':lead,'conversation_id':str(conversations[0]['conversation_id']),
            'kind':str(row['message_type'])}


def _model(cur,cfg):
    tenant=cfg['AI_LEARNING_MCP_TENANT_ID'];agent=cfg['AI_LEARNING_MCP_AGENT_ID']
    cur.execute('''SELECT a.model,a.enabled,s.enabled AS settings_enabled,s.emergency_stop,i.metadata
      FROM saas_ai_agents a JOIN saas_ai_agent_settings s ON s.tenant_id=a.tenant_id
      LEFT JOIN saas_tenant_integrations i ON i.tenant_id=a.tenant_id AND i.provider='chat_ai'
      WHERE a.tenant_id=%s::uuid AND a.id=%s::uuid''',(tenant,agent))
    row=cur.fetchone()
    if not row or not row.get('enabled') or not row.get('settings_enabled') or row.get('emergency_stop'):
        return None,'agent_unavailable'
    meta=row.get('metadata') or {}; model=str(row.get('model') or meta.get('model') or platform_model()).strip()
    # A tenant-only key must be rechecked by the existing execution service;
    # this read-only tool intentionally never decrypts it.
    if not model or not platform_model_allowed(model): return None,'model_not_allowlisted'
    return model,''


def preflight(cur,cfg,args):
    """Return only safe readiness facts; no body, URL, JID or secret escapes."""
    source=_source(cur,cfg,args);model,reason=_model(cur,cfg)
    if reason:
        return {'ready':False,'reason_code':reason,'message_id':source['message_id'],'attachment_kind':source['kind']}
    tenant=cfg['AI_LEARNING_MCP_TENANT_ID']
    cur.execute('''SELECT f.status AS fragment_status,c.status AS campaign_status,f.lease_until>now() AS lease_live
      FROM saas_knowledge_campaign_fragments f JOIN saas_knowledge_campaigns c
        ON c.tenant_id=f.tenant_id AND c.id=f.campaign_id
      WHERE f.tenant_id=%s::uuid AND f.route->>'channel_id'=%s AND f.route->>'conversation_id'=%s
      ORDER BY f.updated_at DESC LIMIT 1''',(tenant,source['channel_id'],source['conversation_id']))
    fragment=cur.fetchone()
    campaign_ready=bool(fragment and fragment['fragment_status']=='running' and fragment['campaign_status']=='running' and fragment['lease_live'])
    from tenant_connect_voice import transcription_model
    asr_model=transcription_model({'credential_source':'platform','config':{}})
    tariffs={'text':price(model) is not None,'vision_low':media_price(model,'vision_low') is not None,
             'asr':bool(asr_model and media_price(asr_model,'asr') is not None)}
    ready=bool(campaign_ready and all(tariffs.values()))
    return {'ready':ready,'reason_code':'' if ready else ('approved_campaign_fragment_required' if not campaign_ready else 'tariff_or_asr_model_unavailable'),
            'message_id':source['message_id'],'attachment_kind':source['kind'],'model_id':model,
            'asr_model_id':asr_model if asr_model else None,'provider_domain':DOMAIN,'tariffs':tariffs,'execution':'existing_knowledge_extraction_only'}


def definitions(scope):
    return [{'name':NAME,'description':'Owner-only read-only preflight for one existing attachment. It validates tenant/channel/lead/message scope and reports whether an already approved campaign fragment plus exact non-secret tariff readiness exists. It never reads attachment content, starts analysis, creates a campaign, reserves budget, or calls a provider.',
      'inputSchema':{'type':'object','properties':{'message_id':{'type':'string','format':'uuid'},'channel_id':{'type':'string','format':'uuid'},'lead_id':{'type':'integer','minimum':1}},'required':['message_id','channel_id','lead_id'],'additionalProperties':False},
      'annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[scope]}]}]
