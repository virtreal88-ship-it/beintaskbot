"""Read-only admission checks for one exact owner-consented media source."""
import hashlib
import json
import math

from tenant_linear_policy import TenantLinearError, identifier
from tenant_ai_agent_key_policy import platform_model, platform_model_allowed, choose_api_credential, platform_key_ready
from tenant_ai_spend_budget import limit_microusd, price, media_price

NAME='administrator_knowledge_extraction_preflight'
DOMAIN='api.openai.com'
MEDIA_KINDS={'image','audio','voice','video','document'}


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _source(cur,cfg,args):
    allowed={'message_id','channel_id','lead_id','consent_id'}
    if not isinstance(args,dict) or set(args)-allowed or not {'message_id','channel_id','lead_id'}<=set(args):
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
    cur.execute('''SELECT id,message_type,external_id,raw,body,COALESCE(happened_at,created_at) AS occurred_at
      FROM saas_crm_messages WHERE tenant_id=%s::uuid AND id=%s::uuid
      AND kommo_lead_id=%s AND channel=%s''',(tenant,message,lead,'connect:'+channel))
    row=cur.fetchone()
    if not row: raise TenantLinearError('Source message is unavailable in the permitted channel.',404)
    kind=str(row.get('message_type') or '')
    if kind not in MEDIA_KINDS: raise TenantLinearError('Source message has no supported attachment.',409)
    raw=row.get('raw') if isinstance(row.get('raw'),dict) else {}
    # Match ``store.snapshot``'s media_version exactly.  Message ID, kind,
    # channel and lead are independent consent-bound columns, so changing any
    # of those still invalidates the consent without placing content in a hash.
    version=_digest({key:raw.get(key) for key in
      ('mediaId','media_id','file_uuid','fileUuid','fileLength','file_size','size_bytes','mediaKeyTimestamp','timestamp')})
    return {'message_id':message,'channel_id':channel,'lead_id':lead,
      'conversation_id':str(conversations[0]['conversation_id']),'kind':kind,
      'version':version,'body_hash':_digest(str(row.get('body') or '')),
      'external_id':str(row.get('external_id') or ''),'occurred_at':str(row['occurred_at'])}


def _model(cur,cfg):
    """Mirror execution model selection without decrypting a credential."""
    tenant=cfg['AI_LEARNING_MCP_TENANT_ID'];agent=cfg['AI_LEARNING_MCP_AGENT_ID']
    cur.execute('''SELECT a.model,a.enabled,s.enabled AS settings_enabled,s.emergency_stop,
      i.metadata,(i.secrets IS NOT NULL) AS tenant_credential_present
      FROM saas_ai_agents a JOIN saas_ai_agent_settings s ON s.tenant_id=a.tenant_id
      LEFT JOIN saas_tenant_integrations i ON i.tenant_id=a.tenant_id AND i.provider='chat_ai'
      WHERE a.tenant_id=%s::uuid AND a.id=%s::uuid''',(tenant,agent))
    row=cur.fetchone()
    if not row or not row.get('enabled') or not row.get('settings_enabled') or row.get('emergency_stop'):
        return None,None,False,'agent_unavailable'
    meta=row.get('metadata') or {}; model=str(row.get('model') or meta.get('model') or platform_model()).strip()
    tenant_key=bool(row.get('tenant_credential_present'))
    # ``choose_api_credential`` is the execution selector.  Feed it only a
    # non-secret presence sentinel; if a stored tenant key is malformed the
    # executor still rejects it before dispatch, while preflight never reads it.
    source=choose_api_credential(tenant,agent,'connect','configured' if tenant_key else '',model)[1]
    tenant_ready=tenant_key and meta.get('enabled') is True and meta.get('consent') is True and model in list(meta.get('models') or [])+[meta.get('model')]
    platform_ready=source=='platform' and platform_key_ready(tenant,agent,'connect') and platform_model_allowed(model)
    ready=platform_ready if source=='platform' else tenant_ready
    if not model or not ready:
        return model,source,False,('credential_unavailable' if model else 'model_not_allowlisted')
    return model,source,True,''


def config_hash(cur,cfg,credential_source,voice_model=''):
    """Non-secret fingerprint of the exact execution configuration."""
    cur.execute('''SELECT a.model,a.enabled,s.enabled AS settings_enabled,s.emergency_stop,i.metadata,
      (i.secrets IS NOT NULL) AS tenant_credential_present FROM saas_ai_agents a
      JOIN saas_ai_agent_settings s ON s.tenant_id=a.tenant_id
      LEFT JOIN saas_tenant_integrations i ON i.tenant_id=a.tenant_id AND i.provider='chat_ai'
      WHERE a.tenant_id=%s::uuid AND a.id=%s::uuid''',(cfg['AI_LEARNING_MCP_TENANT_ID'],cfg['AI_LEARNING_MCP_AGENT_ID']))
    row=cur.fetchone() or {}; meta=row.get('metadata') or {}
    return _digest({'model':row.get('model'),'enabled':row.get('enabled'),'settings_enabled':row.get('settings_enabled'),
      'emergency_stop':row.get('emergency_stop'),'credential_source':credential_source,
      'tenant_credential_present':bool(row.get('tenant_credential_present')),
      'metadata':{'model':meta.get('model'),'models':meta.get('models'),'enabled':meta.get('enabled'),'consent':meta.get('consent'),
        'audio_enabled':meta.get('audio_enabled'),'transcription_model':meta.get('transcription_model')},'asr_model':voice_model})


def asr_model(cur,cfg,credential_source):
    """Use the same non-secret integration metadata passed by extraction."""
    cur.execute("SELECT metadata FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'",(cfg['AI_LEARNING_MCP_TENANT_ID'],))
    meta=(cur.fetchone() or {}).get('metadata') or {}
    from tenant_connect_voice import transcription_model, VoiceError
    try:return transcription_model({'credential_source':credential_source,'config':meta})
    except VoiceError:return ''


def policy_for(kind):
    """Conservative exact policy later bound into one owner consent."""
    return {'allow_external_media':True,
      'allow_paid_asr':kind in {'audio','voice','video'},'max_main_calls':1,
      'max_asr_calls':1 if kind in {'audio','voice','video'} else 0,
      'max_vision_calls':2 if kind in {'image','video','document'} else 0}


def _budget(cur,tenant,model,asr_model,policy):
    """Combined read-only upper bound; the executor separately reserves it."""
    cap=limit_microusd(tenant); text=price(model)
    vision=media_price(model,'vision_low') if policy['max_vision_calls'] else 0
    asr=media_price(asr_model,'asr') if policy['max_asr_calls'] and asr_model else 0
    tariffs={'text':text is not None,'vision_low':not policy['max_vision_calls'] or vision is not None,
      'asr':not policy['max_asr_calls'] or bool(asr_model and asr is not None)}
    if not cap or not all(tariffs.values()): return tariffs,False
    # Matches the extraction reservation: a 16k bounded input plus its 1.8k
    # response ceiling.  Do not collapse them to one tariff because configured
    # input/output prices can differ.
    text_cost=int(math.ceil(16000*text[0]/1_000_000))+int(math.ceil(1800*text[1]/1_000_000))
    total=text_cost+int(vision or 0)*policy['max_vision_calls']+int(asr or 0)*policy['max_asr_calls']
    cur.execute("""SELECT COALESCE(sum(CASE WHEN state IN ('reserved','unknown') THEN reserved_microusd
      ELSE COALESCE(actual_microusd,reserved_microusd) END),0) AS used
      FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid""",(tenant,))
    return tariffs,int((cur.fetchone() or {}).get('used') or 0)+total<=cap


def _approved_fragment(cur,tenant,source,agent,model,credential_source,cfg,consent_id):
    if not consent_id: return None,'owner_consent_required'
    try: consent_id=identifier(consent_id)
    except TenantLinearError: return None,'owner_consent_required'
    cur.execute("SELECT to_regclass('saas_ai_single_message_extraction_consents') AS relation")
    if not (cur.fetchone() or {}).get('relation'): return None,'owner_consent_required'
    cur.execute('''SELECT x.*,f.status AS fragment_status,f.lease_until>now() AS lease_live,f.route,
      c.status AS campaign_status,c.sources,c.analysis_agent_id
      FROM saas_ai_single_message_extraction_consents x
      LEFT JOIN saas_knowledge_campaign_fragments f ON f.tenant_id=x.tenant_id AND f.id=x.fragment_id
      LEFT JOIN saas_knowledge_campaigns c ON c.tenant_id=f.tenant_id AND c.id=f.campaign_id
      WHERE x.tenant_id=%s::uuid AND x.id=%s::uuid''',(tenant,consent_id))
    row=cur.fetchone()
    if not row or row.get('state')!='approved': return None,'owner_consent_required'
    if (str(row.get('message_id'))!=source['message_id'] or str(row.get('channel_id'))!=source['channel_id']
      or int(row.get('lead_id') or 0)!=source['lead_id'] or row.get('source_version')!=source['version']
      or row.get('message_kind')!=source['kind'] or row.get('body_hash')!=source['body_hash']
      or row.get('external_id')!=source['external_id'] or str(row.get('agent_id'))!=agent
      or row.get('model_id')!=model or row.get('asr_model_id')!=asr_model(cur,cfg,credential_source)
      or row.get('config_hash')!=config_hash(cur,cfg,credential_source,asr_model(cur,cfg,credential_source))): return None,'source_or_agent_changed'
    route=row.get('route') if isinstance(row.get('route'),dict) else {}
    policy=row.get('media_policy') if isinstance(row.get('media_policy'),dict) else {}
    exact=(route.get('message_ids')==[source['message_id']] and route.get('source_version')==source['version']
      and route.get('channel_id')==source['channel_id'] and route.get('conversation_id')==source['conversation_id']
      and route.get('allow_external_media')==policy.get('allow_external_media')
      and route.get('allow_paid_asr')==policy.get('allow_paid_asr')
      and route.get('allow_vision')==bool(policy.get('max_vision_calls'))
      and row.get('sources')==[source['channel_id']]
      and row.get('fragment_status')=='running' and row.get('campaign_status')=='running' and row.get('lease_live')
      and str(row.get('analysis_agent_id'))==agent)
    return (row if exact else None),('lease_or_exact_fragment_required' if not exact else '')


def preflight(cur,cfg,args):
    """Safe readiness facts only: never attachment bytes, URLs, keys or writes."""
    source=_source(cur,cfg,args); model,credential_source,configured,reason=_model(cur,cfg)
    if reason: return {'ready':False,'reason_code':reason,'message_id':source['message_id'],'attachment_kind':source['kind']}
    voice_model=asr_model(cur,cfg,credential_source)
    policy=policy_for(source['kind']);tariffs,budget_ready=_budget(cur,cfg['AI_LEARNING_MCP_TENANT_ID'],model,voice_model,policy)
    if policy['max_asr_calls'] and not voice_model:
        return {'ready':False,'reason_code':'asr_model_unavailable','message_id':source['message_id'],
          'attachment_kind':source['kind'],'model_id':model,'asr_model_id':None,'provider_domain':DOMAIN,
          'credential_source':credential_source,'credential_configured':configured,'tariffs':tariffs,
          'spend_cap_enforced':bool(limit_microusd(cfg['AI_LEARNING_MCP_TENANT_ID'])),
          'execution':'existing_knowledge_campaign_fragment_only'}
    fragment,fragment_reason=_approved_fragment(cur,cfg['AI_LEARNING_MCP_TENANT_ID'],source,cfg['AI_LEARNING_MCP_AGENT_ID'],model,credential_source,cfg,args.get('consent_id'))
    return {'ready':bool(fragment and budget_ready),'reason_code':'' if fragment and budget_ready else (fragment_reason or 'tariff_or_budget_unavailable'),
      'message_id':source['message_id'],'attachment_kind':source['kind'],'model_id':model,'asr_model_id':voice_model or None,
      'provider_domain':DOMAIN,'credential_source':credential_source,'credential_configured':configured,
      'tariffs':tariffs,'spend_cap_enforced':bool(limit_microusd(cfg['AI_LEARNING_MCP_TENANT_ID'])),
      'execution':'existing_knowledge_campaign_fragment_only'}


def definitions(scope):
    return [{'name':NAME,'description':'Owner-only read-only preflight for one attachment. Ready requires exact owner consent, unchanged media version, matching source/agent/policy, a live campaign fragment lease and combined configured tariff/budget readiness. It never reads content, starts analysis, creates campaigns, reserves budget, or calls a provider.',
      'inputSchema':{'type':'object','properties':{'message_id':{'type':'string','format':'uuid'},'channel_id':{'type':'string','format':'uuid'},'lead_id':{'type':'integer','minimum':1},'consent_id':{'type':'string','format':'uuid'}},'required':['message_id','channel_id','lead_id'],'additionalProperties':False},
      'annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[scope]}]}]
