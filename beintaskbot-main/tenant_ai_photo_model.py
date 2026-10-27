"""Exact owner-selected photo observation; no agent/default model mutation.

Only the existing single-message consent may enable this route. Pricing uses
the shared token ledger, not an unrelated model's fixed media tariff.
"""
import math
from tenant_linear_policy import TenantLinearError

MODELS=('gpt-4.1','gpt-4.1-2025-04-14')
INPUT_UPPER=2048  # fixed prompt + empty conversation envelope + 85 low-detail image tokens
OUTPUT_UPPER=600
PRICES=(2_000_000,8_000_000)  # microUSD / million tokens, standard Responses
POLICY={'allow_external_media':True,'allow_paid_asr':False,
        'max_main_calls':0,'max_asr_calls':0,'max_vision_calls':1}


def quote(model):
    from tenant_ai_spend_budget import price
    if model not in MODELS or price(model)!=PRICES:
        raise TenantLinearError('photo_model_tariff_required',409)
    return {'model':model,'text_input_per_million':PRICES[0],
      'text_output_per_million':PRICES[1],'input_upper':INPUT_UPPER,'output_upper':OUTPUT_UPPER,
      'max_microusd':math.ceil(INPUT_UPPER*PRICES[0]/1_000_000)+math.ceil(OUTPUT_UPPER*PRICES[1]/1_000_000),
      'max_main_calls':0,'max_vision_calls':1,'max_asr_calls':0,'photo_only':True}


def tariff_status(model):
    """Non-secret, read-only quote; missing/mismatched prices never get defaults."""
    try:
        return {'ready': True, **quote(model)}
    except TenantLinearError:
        return {'ready': False, 'model': model, 'reason_code': 'photo_model_tariff_required'}


def budget(cur,tenant,model):
    from tenant_ai_spend_budget import limit_microusd
    amount=quote(model);cap=limit_microusd(tenant)
    if not cap:raise TenantLinearError('photo_model_capped_budget_required',409)
    cur.execute("""SELECT COALESCE(sum(CASE WHEN state IN ('reserved','unknown') THEN reserved_microusd
      ELSE COALESCE(actual_microusd,reserved_microusd) END),0) AS used
      FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid""",(tenant,))
    if int((cur.fetchone() or {}).get('used') or 0)+amount['max_microusd']>cap:
        raise TenantLinearError('photo_model_budget_exhausted',429)
    return amount


def select(cur,cfg,model):
    if model not in MODELS:raise TenantLinearError('Unsupported photo model.',400)
    cur.execute('''SELECT a.enabled,s.enabled AS settings_enabled,s.emergency_stop,i.metadata,
      (i.secrets IS NOT NULL) AS credential_present FROM saas_ai_agents a
      JOIN saas_ai_agent_settings s ON s.tenant_id=a.tenant_id
      LEFT JOIN saas_tenant_integrations i ON i.tenant_id=a.tenant_id AND i.provider='chat_ai'
      WHERE a.tenant_id=%s::uuid AND a.id=%s::uuid''',
      (cfg['AI_LEARNING_MCP_TENANT_ID'],cfg['AI_LEARNING_MCP_AGENT_ID']))
    row=cur.fetchone() or {};meta=row.get('metadata') or {}
    if (not row.get('enabled') or not row.get('settings_enabled') or row.get('emergency_stop')
        or not row.get('credential_present') or meta.get('enabled') is not True or meta.get('consent') is not True
        or model not in list(meta.get('models') or [])+[meta.get('model'),meta.get('photo_model')]):
        raise TenantLinearError('photo_model_company_configuration_required',409)
    from tenant_connect_images import supports_images
    if not supports_images(model):raise TenantLinearError('photo_model_vision_not_allowed',409)
    quote(model)
    return model,'company',True,''


def fence(cur,row,source):
    if not row.get('photo_model'):return
    from tenant_ai_owner_review_component import _connection_profile
    from tenant_ai_administrator_drafts import DRAFT_SCOPE
    cfg={'AI_LEARNING_MCP_TENANT_ID':str(row['tenant_id']),
      'AI_LEARNING_MCP_AGENT_ID':str(row['agent_id']),'AI_LEARNING_MCP_CHANNEL_ID':str(row['channel_id']),
      'actor_id':row['owner_id'],'oauth_connection_id':str(row['oauth_connection_id']),
      'oauth_token_id':str(row['retry_token_id'])}
    _connection_profile(cur,cfg,required_scopes=(DRAFT_SCOPE,))
    select(cur,cfg,row['photo_model'])
    from tenant_ai_spend_budget import limit_microusd
    if not limit_microusd(str(row['tenant_id'])):raise TenantLinearError('photo_model_capped_budget_required',409)
    if source['kind']!='image' or row['model_id']!=row['photo_model'] or row['media_policy']!=POLICY or row['retry_budget']!=quote(row['photo_model']):
        raise TenantLinearError('Owner photo selection changed.',409)
    cur.execute('SELECT expires_at>now() AS live FROM saas_ai_single_message_extraction_consents WHERE tenant_id=%s::uuid AND id=%s::uuid',(row['tenant_id'],row['id']))
    if not (cur.fetchone() or {}).get('live'):raise TenantLinearError('Owner photo selection expired.',409)


def authority(cur,tenant,fragment_id,model):
    import tenant_ai_single_message_extraction as consent
    cur.execute('''SELECT x.* FROM saas_ai_single_message_extraction_consents x
      JOIN saas_knowledge_campaign_fragments f ON f.tenant_id=x.tenant_id AND f.id=x.fragment_id
      JOIN saas_knowledge_campaigns c ON c.tenant_id=f.tenant_id AND c.id=f.campaign_id
      WHERE x.tenant_id=%s::uuid AND x.fragment_id=%s::uuid AND x.photo_model=%s
        AND x.state='approved' AND x.action='approve' AND x.consumed_at IS NOT NULL
        AND f.route=x.route AND c.progress->>'mode'='single_message'
        AND f.status='running' AND f.lease_until>now() AND c.status='running'
        AND c.created_by=x.owner_id AND c.approved_by=x.owner_id FOR SHARE OF x,f,c''',(tenant,fragment_id,model))
    row=cur.fetchone()
    if not row:raise TenantLinearError('Exact owner photo selection required.',403)
    consent.dispatch_fence(cur,tenant,fragment_id)
    return row
