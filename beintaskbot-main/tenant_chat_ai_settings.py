"""Separate company chat key; never read the global or news AI credential."""
import json
import re
from tenant_platform import _connect, _fernet
from tenant_chat_ai_schema import ensure_schema
from tenant_chat_ai_policy import configuration
from tenant_chat_ai_provider import list_models as provider_models, probe_model as provider_probe_model
from tenant_ai_agent_access import tenant_status_allows_management
from tenant_linear_policy import TenantLinearError, identifier


def authorize(cur, session):
    """Allow active company owners and admins to manage company Chat AI."""
    if not isinstance(session, dict):
        raise TenantLinearError('İcazə yoxdur.', 403)
    tenant = identifier(session.get('tenant_id'))
    try:
        user = int(session.get('telegram_id'))
    except (TypeError, ValueError):
        raise TenantLinearError('İcazə yoxdur.', 403) from None
    cur.execute('''SELECT m.role,m.active,t.status FROM saas_tenant_members m
      JOIN saas_tenants t ON t.id=m.tenant_id
      WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s FOR SHARE OF m,t''', (tenant, user))
    person = cur.fetchone()
    if not person or person.get('active') is not True:
        raise TenantLinearError('Bu şirkətdə aktiv üzvlük tələb olunur.', 403)
    if not tenant_status_allows_management(person.get('status')):
        raise TenantLinearError('Bu şirkətdə Chat AI ayarlarını idarə etmək mümkün deyil.', 403)
    if person.get('role') not in {'owner', 'admin'}:
        raise TenantLinearError('Chat AI ayarlarını yalnız şirkət sahibi və ya admin dəyişə bilər.', 403)
    return tenant, user


def public(row, tenant_id=None):
    row = row or {}
    metadata = row.get('metadata') or {}
    result = {'configured': bool(row.get('secrets')), 'updated_at': str(row.get('updated_at') or ''),
            **{k: metadata.get(k, v) for k, v in {
                'enabled': False, 'consent': False, 'audio_enabled': False, 'model': '',
                'transcription_model': '', 'daily_limit': 50, 'media_hosts': []}.items()}}
    result.update({
        'copilot_enabled': metadata.get('copilot_enabled', True),
        'copilot_model': metadata.get('copilot_model') or metadata.get('model') or '',
        'copilot_daily_limit': metadata.get('copilot_daily_limit', 50),
        'copilot_employee_permission': 'chat_ai',
        'provider': 'OpenAI',
        'photo_model': metadata.get('photo_model') or '',
        'video_enabled': metadata.get('video_enabled') is True,
        'video_model': metadata.get('video_model') or '',
    })
    from tenant_ai_photo_model import MODELS, tariff_status
    result['photo_model_tariffs'] = {model: tariff_status(model) for model in MODELS}
    from tenant_ai_video_model import MODELS as VIDEO_MODELS, tariff_status as video_tariff_status
    result['video_model_tariffs'] = ({model: video_tariff_status(model, str(tenant_id))
        for model in VIDEO_MODELS} if tenant_id else {})
    from tenant_ai_asr_model import MODELS as ASR_MODELS, tariff_status as asr_tariff_status
    from tenant_ai_asr_model import effective_model
    try:
        result['effective_transcription_model'] = effective_model(result['transcription_model'])
    except TenantLinearError:
        result['effective_transcription_model'] = ''
        result['transcription_model_blocked_reason'] = 'asr_model_unavailable'
    result['asr_model_tariffs'] = {model: asr_tariff_status(model) for model in ASR_MODELS}
    return result


def read(session):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = authorize(cur, session)
            cur.execute("SELECT secrets,metadata,updated_at FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'", (tenant,))
            return public(cur.fetchone(), tenant)


def _company_key(session):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = authorize(cur, session)
            cur.execute("SELECT secrets,metadata FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'", (tenant,))
            row = cur.fetchone() or {}
    metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    if not row.get('secrets') or metadata.get('enabled') is not True or metadata.get('consent') is not True:
        raise TenantLinearError('Включите Chat AI и подтвердите обработку данных компании.', 409)
    try:
        secret = json.loads(_fernet().decrypt(bytes(row['secrets'])))
        if secret.get('tenant_id') != tenant or secret.get('provider') != 'chat_ai' or not secret.get('api_key'):
            raise ValueError()
    except Exception:
        raise TenantLinearError('Сохранённый ключ Chat AI компании недоступен.', 503) from None
    return tenant, metadata, secret['api_key']


def available_models(session):
    """Explicit owner/admin check; only IDs are returned and no tokens are generated."""
    _tenant, _metadata, key = _company_key(session)
    return {'models': provider_models(key)}


def test_model(session, model):
    """Explicit owner/admin, context-free minimal test of the selected Copilot model."""
    model = str(model or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', model):
        raise TenantLinearError('Укажите корректный ID модели Copilot.')
    _tenant, metadata, key = _company_key(session)
    if metadata.get('copilot_enabled', True) is not True:
        raise TenantLinearError('Сначала включите AI Copilot компании.', 409)
    provider_probe_model(key, model)
    return {'model': model, 'available': True}


def save(session, data):
    preserve_models = data.get('preserve_models') is True
    model_fields = ('model','transcription_model','copilot_model','photo_model','video_model','video_enabled')
    if preserve_models and any(field in data for field in model_fields):
        raise TenantLinearError('Модели задаются у агента.',400)
    config = configuration(data, models_required=not preserve_models)
    key = data.get('api_key', '')
    if not isinstance(key, str) or (key and (not 20 <= len(key) <= 500 or any(c.isspace() for c in key))):
        raise TenantLinearError('API açarı düzgün deyil.')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = authorize(cur, session)
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tenant-chat-ai:' + tenant,))
            cur.execute("SELECT secrets,metadata,updated_at FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai' FOR UPDATE", (tenant,))
            previous = cur.fetchone() or {}
            if str(previous.get('updated_at') or '') != str(data.get('expected_updated_at') or ''):
                raise TenantLinearError('AI ayarları dəyişib. Yeniləyin.', 409)
            # Older settings clients do not submit the Copilot subsection.
            # Preserve its values so legacy Chat AI edits cannot couple models.
            old_metadata = previous.get('metadata') if isinstance(previous.get('metadata'), dict) else {}
            if preserve_models:
                for field in model_fields:
                    config[field] = old_metadata.get(field, False if field=='video_enabled' else '')
            # Photo selection is a separate owner setting, never the text-model
            # allowlist. Old clients preserve it and cannot add model grants.
            if 'photo_model' not in data and 'photo_model' in old_metadata:
                config['photo_model'] = old_metadata['photo_model']
            # Older settings clients never opt in to video; preserve the
            # separate owner-only selection without coupling it to photo/text.
            for field in ('video_model', 'video_enabled'):
                if field not in data and field in old_metadata:
                    config[field] = old_metadata[field]
            if 'models' in old_metadata:
                config['models'] = old_metadata['models']
            video_changed = (config.get('video_model', '') != old_metadata.get('video_model', '')
                or config.get('video_enabled') is not (old_metadata.get('video_enabled') is True))
            if video_changed:
                cur.execute('SELECT role FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s FOR SHARE',
                            (tenant, int(session['telegram_id'])))
                if (cur.fetchone() or {}).get('role') != 'owner':
                    raise TenantLinearError('Only the company owner can configure video analysis.', 403)
                if config.get('video_enabled') or config.get('video_model'):
                    if not previous.get('secrets') or not config['enabled'] or not config['consent']:
                        raise TenantLinearError('video_model_company_configuration_required', 409)
                    from tenant_ai_video_model import quote as video_quote
                    video_quote(config['video_model'], tenant)
            if config.get('photo_model', '') != old_metadata.get('photo_model', ''):
                cur.execute('SELECT role FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s FOR SHARE',
                            (tenant, int(session['telegram_id'])))
                if (cur.fetchone() or {}).get('role') != 'owner':
                    raise TenantLinearError('Company owner access required for photo model selection.', 403)
                if config.get('photo_model'):
                    from tenant_ai_photo_model import quote
                    quote(config['photo_model'])
                    if not previous.get('secrets') or not config['enabled'] or not config['consent']:
                        raise TenantLinearError('photo_model_company_configuration_required', 409)
            if 'copilot_model' not in data and not preserve_models:
                config['copilot_model'] = old_metadata.get('copilot_model') or old_metadata.get('model') or config['model']
            if 'copilot_enabled' not in data:
                config['copilot_enabled'] = old_metadata.get('copilot_enabled', True)
            if 'copilot_daily_limit' not in data:
                config['copilot_daily_limit'] = old_metadata.get('copilot_daily_limit', 50)
            encrypted = previous.get('secrets')
            if data.get('remove_key') is True:
                encrypted = None
            elif key:
                encrypted = _fernet().encrypt(json.dumps({'tenant_id': tenant, 'provider': 'chat_ai', 'api_key': key}).encode())
            if config['enabled'] and not encrypted:
                raise TenantLinearError('Şirkətin API açarını daxil edin.')
            cur.execute('''INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets,metadata)
              VALUES(%s::uuid,'chat_ai','configured',%s,%s::jsonb) ON CONFLICT(tenant_id,provider) DO UPDATE
              SET secrets=EXCLUDED.secrets,metadata=EXCLUDED.metadata,status='configured',updated_at=now()
              RETURNING secrets,metadata,updated_at''', (tenant, encrypted, json.dumps(config)))
            result = public(cur.fetchone(), tenant)
            if config.get('photo_model', '') != old_metadata.get('photo_model', ''):
                from tenant_ai_agents_store import _audit
                _audit(cur,tenant,int(session['telegram_id']),'chat_ai_photo_model_changed','chat_ai',tenant,
                       {'previous_photo_model':old_metadata.get('photo_model') or '',
                        'photo_model':config.get('photo_model') or ''})
            if video_changed:
                from tenant_ai_agents_store import _audit
                _audit(cur,tenant,int(session['telegram_id']),'chat_ai_video_model_changed','chat_ai',tenant,
                       {'previous_video_model':old_metadata.get('video_model') or '',
                        'video_model':config.get('video_model') or '',
                        'video_enabled':config.get('video_enabled') is True})
        conn.commit()
    return result
