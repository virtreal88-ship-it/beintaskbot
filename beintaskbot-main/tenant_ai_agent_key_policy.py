"""Platform-owned AI credentials and centrally managed model catalogue.

The catalogue lives in Railway environment variables, not the browser.  That
keeps platform credentials and model admission under platform control while
allowing a catalogue change without an application release.
"""
import json
import os
import re


def retired_text_model(model):
    """Text model retirement only; transcription models and history are untouched."""
    return bool(re.fullmatch(r"gpt-4o-mini(?:-\d{4}-\d{2}-\d{2})?", str(model or "").strip(), re.I))


def require_text_model(model):
    value = str(model or "").strip()
    if not value or retired_text_model(value):
        from tenant_linear_policy import TenantLinearError
        raise TenantLinearError("configuration_required: select a supported text model; no automatic replacement.", 409)
    return value


def platform_agent_pairs():
    """Return only exact tenant/agent allowlist pairs from Railway env."""
    result = set()
    for item in str(os.environ.get('AI_AGENTS_PLATFORM_KEY_AGENTS') or '').split(','):
        tenant, separator, agent = item.strip().partition(':')
        if separator and tenant.strip() and agent.strip():
            result.add((tenant.strip().lower(), agent.strip().lower()))
    return result


def platform_model():
    """Backward-compatible name for the platform default model."""
    requested = str(os.environ.get('AI_AGENTS_PLATFORM_DEFAULT_MODEL') or '').strip()
    configured = _configured_platform_models()
    # Keep a stale explicit default visible to admission guards, never silently
    # select a different model with a different price or credential.
    if retired_text_model(requested):
        return requested
    if requested and requested in configured:
        return requested
    legacy = str(os.environ.get('OPENAI_HISTORY_REPLY_MODEL') or '').strip()
    if legacy:
        return legacy
    return configured[0] if configured else ''


def _enabled(value):
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _configured_platform_models():
    """Return safe API model identifiers configured by a platform admin.

    ``AI_AGENTS_PLATFORM_MODELS`` accepts either a JSON array of identifiers
    or objects with an ``id`` field.  Invalid entries are ignored rather than
    being made selectable by a tenant browser.
    """
    raw = str(os.environ.get('AI_AGENTS_PLATFORM_MODELS') or '').strip()
    values = []
    if raw:
        try:
            decoded = json.loads(raw)
        except ValueError:
            decoded = []
        if isinstance(decoded, list):
            for item in decoded:
                value = item.get('id') if isinstance(item, dict) else item
                if isinstance(value, str):
                    value = value.strip()
                    if value and len(value) <= 100 and all(char.isalnum() or char in '._-' for char in value):
                        values.append(value)
    legacy = str(os.environ.get('OPENAI_HISTORY_REPLY_MODEL') or '').strip()
    if legacy and legacy not in values:
        values.append(legacy)
    return list(dict.fromkeys(values))


def platform_models():
    return [value for value in _configured_platform_models() if not retired_text_model(value)]


def platform_model_choices():
    """Public, non-secret labels for the tenant editor."""
    raw = str(os.environ.get('AI_AGENTS_PLATFORM_MODELS') or '').strip()
    labels = {}
    if raw:
        try:
            decoded = json.loads(raw)
        except ValueError:
            decoded = []
        if isinstance(decoded, list):
            for item in decoded:
                if isinstance(item, dict) and isinstance(item.get('id'), str) and isinstance(item.get('label'), str):
                    labels[item['id'].strip()] = item['label'].strip()[:120]
    default = platform_model()
    choices = [{'id': '', 'label': 'Default (recommended)'}]
    for value in platform_models():
        choices.append({'id': value, 'label': labels.get(value) or value,
                        'recommended': value == default})
    return choices


def platform_service_ready():
    """Whether the shared key is deliberately enabled for AI Agents."""
    return (_enabled(os.environ.get('AI_AGENTS_PLATFORM_ENABLED'))
            and bool(os.environ.get('OPENAI_API_KEY'))
            and bool(platform_model()))


def platform_agent_enabled(tenant_id, agent_id, provider='connect'):
    if str(provider or '').lower() != 'connect':
        return False
    pair = (str(tenant_id or '').strip().lower(), str(agent_id or '').strip().lower())
    return bool(pair[0] and pair[1] and pair in platform_agent_pairs())


def platform_key_ready(tenant_id, agent_id, provider='connect'):
    # The old explicit allow-list remains supported for Nizam Ai and any
    # already deployed cutovers.  The global service is opt-in through an
    # explicit Railway flag, so adding this code alone never enables AI.
    legacy = (platform_agent_enabled(tenant_id, agent_id, provider)
              and bool(str(os.environ.get('AI_AGENTS_PLATFORM_KEY_ACTIVATED_AT') or '').strip())
              and bool(os.environ.get('OPENAI_API_KEY'))
              and bool(platform_model()))
    return legacy or platform_service_ready()


def platform_model_allowed(model):
    return str(model or '').strip() in set(platform_models())


def choose_api_credential(tenant_id, agent_id, provider, company_key, model=''):
    """Choose a credential without exposing either secret.

    Existing companies with a configured Chat AI key continue to use it for
    legacy/custom models.  New agents (and any agent selecting a centrally
    admitted model) use the shared key by default.
    """
    if model:
        require_text_model(model)
    use_platform = (platform_key_ready(tenant_id, agent_id, provider)
                    and (platform_agent_enabled(tenant_id, agent_id, provider)
                         or not str(company_key or '')
                         or platform_model_allowed(model)))
    if use_platform:
        return str(os.environ.get('OPENAI_API_KEY') or ''), 'platform'
    return str(company_key or ''), 'company'


def platform_token_limit(tenant_id):
    """Return the platform-enforced daily token cap for one tenant.

    A zero value means no cap.  Overrides are a Railway-only JSON object such
    as {"tenant-uuid": 250000}; tenants never submit this value through an API.
    """
    raw = str(os.environ.get('AI_AGENTS_PLATFORM_TENANT_TOKEN_LIMITS') or '').strip()
    overrides = {}
    if raw:
        try:
            parsed = json.loads(raw)
            overrides = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            overrides = {}
    value = overrides.get(str(tenant_id))
    if value is None:
        value = os.environ.get('AI_AGENTS_PLATFORM_DEFAULT_TOKEN_LIMIT', '0')
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def skip_pre_activation_jobs(cur):
    """Terminally skip only allowlisted Connect jobs received before cutover."""
    pairs = [tenant + ':' + agent for tenant, agent in platform_agent_pairs()]
    activated_at = str(os.environ.get('AI_AGENTS_PLATFORM_KEY_ACTIVATED_AT') or '').strip()
    if not pairs or not activated_at:
        return False
    cur.execute('''UPDATE saas_tenant_webhook_events e SET state='skipped',completed_at=now(),
        error_code='platform_key_activation_cutoff'
      WHERE e.provider='connect' AND e.state IN ('queued','processing')
        AND e.received_at < %s::timestamptz AND EXISTS (
          SELECT 1 FROM saas_ai_agent_jobs j WHERE j.tenant_id=e.tenant_id
            AND j.event_fingerprint=e.fingerprint AND j.provider='connect'
            AND (j.tenant_id::text || ':' || j.agent_id::text)=ANY(%s)
            AND j.state IN ('queued','processing'))''', (activated_at, pairs))
    cur.execute('''UPDATE saas_ai_agent_jobs j SET state='skipped',revision=revision+1,
        lease_token=NULL,lease_until=NULL,error_code='platform_key_activation_cutoff',updated_at=now()
      WHERE j.provider='connect' AND j.state IN ('queued','processing')
        AND (j.tenant_id::text || ':' || j.agent_id::text)=ANY(%s)
        AND EXISTS (SELECT 1 FROM saas_tenant_webhook_events e
          WHERE e.tenant_id=j.tenant_id AND e.fingerprint=j.event_fingerprint
            AND e.received_at < %s::timestamptz)''', (pairs, activated_at))
    return True
