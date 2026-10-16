"""One-shot, non-sending OpenAI access probe for a single tenant.

Enable only for an exact tenant UUID with AI_AGENTS_OPENAI_PROBE_TENANT_ID.
The probe never logs credentials, prompts, provider response bodies or drafts.
"""
import json
import os
import requests

from tenant_linear_policy import identifier
from tenant_chat_ai_provider import _provider_error_category


def _probe_key(key, model, *, get=requests.get, post=requests.post):
    result = {'key_present': bool(key), 'models_http': None, 'model_listed': None,
              'generation_http': None, 'generation_category': None, 'generation_ok': False}
    if not key:
        return result
    headers = {'Authorization': 'Bearer ' + key}
    try:
        response = get('https://api.openai.com/v1/models', headers=headers,
                       timeout=(5, 15), allow_redirects=False)
        result['models_http'] = int(response.status_code)
        if response.status_code == 200:
            data = response.json()
            ids = {str(item.get('id') or '') for item in data.get('data', []) if isinstance(item, dict)}
            result['model_listed'] = str(model) in ids
    except Exception:
        pass
    try:
        response = post('https://api.openai.com/v1/responses', headers=headers,
            json={'model': str(model), 'store': False, 'max_output_tokens': 16,
                  'instructions': 'Return only the word OK.', 'input': 'Reply with OK only.'},
            timeout=(5, 30), allow_redirects=False)
        result['generation_http'] = int(response.status_code)
        if response.status_code == 200:
            result['generation_ok'] = response.json().get('status') == 'completed'
        else:
            result['generation_category'] = _provider_error_category(response)
    except Exception:
        result['generation_category'] = 'transport_error'
    return result


def run_probe(tenant_id, logger, *, connect_fn=None, fernet_fn=None, get=requests.get, post=requests.post):
    """Inspect one explicitly selected tenant and make tiny, draft-only probes."""
    tenant = identifier(tenant_id)
    if str(os.environ.get('AI_AGENTS_OPENAI_PROBE_TENANT_ID') or '').strip() != tenant:
        raise ValueError('OpenAI probe tenant is not explicitly enabled')
    if connect_fn is None or fernet_fn is None:
        from tenant_platform_core import _connect, _fernet
        connect_fn = connect_fn or _connect
        fernet_fn = fernet_fn or _fernet
    with connect_fn() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT a.id,a.name,a.model,a.enabled,a.mode,i.metadata,i.secrets
              FROM saas_ai_agents a LEFT JOIN saas_tenant_integrations i
                ON i.tenant_id=a.tenant_id AND i.provider='chat_ai'
              WHERE a.tenant_id=%s::uuid AND lower(a.name)=lower(%s)''', (tenant, 'Nizam Ai'))
            row = cur.fetchone()
    if not row:
        logger.error('AI OpenAI access probe: tenant=%s agent_not_found=true', tenant)
        return
    metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    model = str(row.get('model') or metadata.get('model') or '').strip()
    if not model:
        logger.error('AI OpenAI access probe: tenant=%s agent=%s configured_model_missing=true', tenant, row['id'])
        return
    company_key = ''
    try:
        if row.get('secrets'):
            secret = json.loads(fernet_fn().decrypt(bytes(row['secrets'])))
            if secret.get('tenant_id') == tenant and secret.get('provider') == 'chat_ai':
                company_key = str(secret.get('api_key') or '')
    except Exception:
        company_key = ''
    platform_key = str(os.environ.get('OPENAI_API_KEY') or '')
    platform_model = str(os.environ.get('OPENAI_HISTORY_REPLY_MODEL') or 'gpt-4.1-2025-04-14').strip()
    company = _probe_key(company_key, model, get=get, post=post)
    platform_selected = _probe_key(platform_key, model, get=get, post=post)
    platform_fallback = None
    if platform_key and platform_model and platform_model != model:
        platform_fallback = _probe_key(platform_key, platform_model, get=get, post=post)
    # Keep this record deliberately allowlisted; never log the row, key, prompt or provider body.
    logger.info('AI OpenAI access probe: tenant=%s agent=%s enabled=%s mode=%s selected_model=%s '
        'company_key_present=%s company_models_http=%s company_model_listed=%s company_generation_http=%s '
        'company_generation_category=%s company_generation_ok=%s platform_key_present=%s platform_models_http=%s '
        'platform_selected_model_listed=%s platform_selected_generation_http=%s platform_selected_generation_category=%s '
        'platform_selected_generation_ok=%s platform_reference_model=%s platform_reference_models_http=%s '
        'platform_reference_model_listed=%s platform_reference_generation_http=%s platform_reference_generation_category=%s '
        'platform_reference_generation_ok=%s',
        tenant, row['id'], bool(row.get('enabled')), str(row.get('mode') or ''), model,
        company['key_present'], company['models_http'], company['model_listed'], company['generation_http'],
        company['generation_category'], company['generation_ok'], platform_selected['key_present'],
        platform_selected['models_http'], platform_selected['model_listed'], platform_selected['generation_http'],
        platform_selected['generation_category'], platform_selected['generation_ok'],
        platform_model if platform_key else '',
        platform_fallback['models_http'] if platform_fallback else None,
        platform_fallback['model_listed'] if platform_fallback else None,
        platform_fallback['generation_http'] if platform_fallback else None,
        platform_fallback['generation_category'] if platform_fallback else None,
        platform_fallback['generation_ok'] if platform_fallback else None)
