"""Validation for tenant-owned AI agent settings and knowledge sources."""
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from tenant_linear_policy import TenantLinearError


MAX_AGENTS = 50
MAX_SOURCES = 500
MAX_TEXT = 200_000
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_EXTRACTED_TEXT = 200_000
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 120
SETUP_KEY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{11,119}')

# These controls are deliberately non-optional.  The editor exposes them so
# an owner can understand the boundary, but a browser request cannot weaken it.
CRITICAL_SAFETY = ('protect_internal', 'verified_facts', 'block_adult',
                   'block_prompt_injection', 'scope_only')


def safety_policy(value=None):
    if value is None:
        return {key: True for key in CRITICAL_SAFETY}
    if not isinstance(value, dict) or set(value) - set(CRITICAL_SAFETY):
        raise TenantLinearError('Təhlükəsizlik siyasəti düzgün deyil.')
    if any(value.get(key) is False for key in CRITICAL_SAFETY):
        raise TenantLinearError('Kritik təhlükəsizlik məhdudiyyətləri söndürülə bilməz.', 403)
    return {key: True for key in CRITICAL_SAFETY}


def _text(value, field, limit, *, required=False):
    if not isinstance(value, str) or len(value) > limit:
        raise TenantLinearError(f'{field} düzgün deyil.')
    value = value.strip()
    if required and not value:
        raise TenantLinearError(f'{field} boş ola bilməz.')
    if any(ord(char) < 32 and char not in '\n\r\t' for char in value):
        raise TenantLinearError(f'{field} düzgün deyil.')
    return value


def setup_key(value):
    """Validate the opaque, browser-generated key used to resume a wizard.

    It is deliberately not a client identifier or a tenant selector.  Store
    methods always combine it with the authorized tenant and employee.
    """
    if not isinstance(value, str) or not SETUP_KEY.fullmatch(value):
        raise TenantLinearError('Yenidən cəhd açarı düzgün deyil.')
    return value


def agent(data, *, creating=False):
    if not isinstance(data, dict):
        raise TenantLinearError('Agent ayarları düzgün deyil.')
    allowed = {'name', 'instructions', 'enabled', 'mode', 'language', 'style',
               'delay_seconds', 'schedule', 'scopes', 'model', 'safety', 'setup_key', 'role', 'expert_policy'}
    if set(data) - allowed:
        raise TenantLinearError('Naməlum agent ayarı var.')
    result = {}
    if creating or 'role' in data:
        role = data.get('role', 'primary')
        if role not in {'primary','expert'}: raise TenantLinearError('Invalid agent role.')
        result['role'] = role
    if creating or 'expert_policy' in data:
        from tenant_ai_expert_policy import policy
        result['expert_policy'] = policy(data.get('expert_policy', {}))
    if creating or 'name' in data:
        result['name'] = _text(data.get('name'), 'Agent adı', 120, required=True)
    if creating or 'instructions' in data:
        result['instructions'] = _text(data.get('instructions', ''), 'Təlimat', 20_000)
    if creating or 'enabled' in data:
        enabled = data.get('enabled', False)
        if not isinstance(enabled, bool):
            raise TenantLinearError('Agent aktivliyi düzgün deyil.')
        result['enabled'] = enabled
    if creating or 'mode' in data:
        mode = data.get('mode', 'manual')
        if mode not in {'manual', 'automatic'}:
            raise TenantLinearError('Agent rejimi düzgün deyil.')
        result['mode'] = mode
    if creating or 'language' in data:
        language = data.get('language', 'auto')
        if language not in {'auto', 'az', 'ru', 'en', 'tr'}:
            raise TenantLinearError('Cavab dili düzgün deyil.')
        result['language'] = language
    if creating or 'style' in data:
        style = data.get('style', 'balanced')
        if style not in {'precise', 'balanced', 'creative'}:
            raise TenantLinearError('Cavab üslubu düzgün deyil.')
        result['style'] = style
    if creating or 'delay_seconds' in data:
        delay = data.get('delay_seconds', 40)
        if type(delay) is not int or not 0 <= delay <= 300:
            raise TenantLinearError('Gecikmə 0–300 saniyə olmalıdır.')
        result['delay_seconds'] = delay
    if creating or 'schedule' in data:
        schedule = data.get('schedule', {})
        if not isinstance(schedule, dict) or set(schedule) - {'timezone', 'days', 'start', 'end'}:
            raise TenantLinearError('İş cədvəli düzgün deyil.')
        normalized_schedule = {}
        if schedule:
            zone = schedule.get('timezone', 'Asia/Baku')
            try:
                ZoneInfo(zone)
            except (ZoneInfoNotFoundError, TypeError):
                raise TenantLinearError('Saat qurşağı düzgün deyil.') from None
            days = schedule.get('days', list(range(7)))
            if not isinstance(days, list) or not days or any(type(day) is not int or not 0 <= day <= 6 for day in days):
                raise TenantLinearError('İş günləri düzgün deyil.')
            start, end = schedule.get('start', '00:00'), schedule.get('end', '23:59')
            if (not isinstance(start, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', start)
                    or not isinstance(end, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', end)):
                raise TenantLinearError('İş saatları düzgün deyil.') from None
            # A shift may legitimately cross midnight (for example
            # 22:00--06:00 for a night-support agent).  Equal endpoints are
            # still ambiguous and therefore rejected rather than silently
            # meaning 24 hours.
            if end == start:
                raise TenantLinearError('İş saatlarının başlanğıcı və sonu eyni ola bilməz.')
            normalized_schedule = {'timezone': zone, 'days': sorted(set(days)), 'start': start, 'end': end}
        result['schedule'] = normalized_schedule
    if creating or 'scopes' in data:
        scopes = data.get('scopes', [])
        if not isinstance(scopes, list) or len(scopes) > 100:
            raise TenantLinearError('Agent əhatə dairəsi düzgün deyil.')
        normalized = []
        seen = set()
        for scope in scopes:
            if not isinstance(scope, dict) or set(scope) - {'provider', 'pipeline_id', 'stage_id', 'channel_id', 'instructions', 'allow_group_messages'}:
                raise TenantLinearError('Agent əhatə dairəsi düzgün deyil.')
            provider = _text(scope.get('provider', 'kommo'), 'provider', 20) or 'kommo'
            if provider not in {'kommo', 'connect'}:
                raise TenantLinearError('Mesaj mənbəyi düzgün deyil.')
            row = {key: _text(scope.get(key, ''), key, 120)
                   for key in ('pipeline_id', 'stage_id', 'channel_id')}
            row['provider'] = provider
            row['instructions'] = _text(scope.get('instructions', ''), 'Mərhələ təlimatı', 5000)
            if 'allow_group_messages' in scope and not isinstance(scope['allow_group_messages'], bool):
                raise TenantLinearError('Qrup mesajları üçün icazə düzgün deyil.')
            # Existing scopes remain safe by default. A group needs an
            # intentional per-channel opt-in from an AI Agent administrator.
            row['allow_group_messages'] = scope.get('allow_group_messages') is True
            if provider == 'kommo' and (not row['pipeline_id'] or not row['stage_id']):
                raise TenantLinearError('Vərəq və mərhələ seçilməlidir.')
            if provider == 'connect' and not row['channel_id']:
                raise TenantLinearError('Connect üçün WhatsApp kanalı seçilməlidir.')
            identity = tuple(row[key] for key in ('provider', 'pipeline_id', 'stage_id', 'channel_id'))
            if identity not in seen:
                seen.add(identity)
                normalized.append(row)
        result['scopes'] = normalized
    if creating or 'model' in data:
        result['model'] = _text(data.get('model', ''), 'Model', 100)
    if creating or 'safety' in data:
        result['safety'] = safety_policy(data.get('safety'))
    if 'setup_key' in data:
        if not creating:
            raise TenantLinearError('Yenidən cəhd açarı dəyişdirilə bilməz.')
        result['setup_key'] = setup_key(data['setup_key'])
    return result


def company_settings(data):
    if not isinstance(data, dict) or set(data) - {'company_instructions', 'enabled', 'auto_send_enabled', 'crm_actions_enabled','max_replies_per_chat','operating_mode','emergency_stop'}:
        raise TenantLinearError('AI Agents ayarları düzgün deyil.')
    result = {}
    if 'company_instructions' in data:
        result['company_instructions'] = _text(data['company_instructions'], 'Şirkət təlimatı', 20_000)
    for key in ('enabled', 'auto_send_enabled', 'crm_actions_enabled'):
        if key in data:
            if not isinstance(data[key], bool):
                raise TenantLinearError('AI Agents ayarları düzgün deyil.')
            result[key] = data[key]
    if 'operating_mode' in data:
        if data['operating_mode'] not in {'test','active'}:
            raise TenantLinearError('AI Agents iş rejimi düzgün deyil.')
        result['operating_mode'] = data['operating_mode']
    if 'emergency_stop' in data:
        if not isinstance(data['emergency_stop'], bool):
            raise TenantLinearError('AI Agents ayarları düzgün deyil.')
        result['emergency_stop'] = data['emergency_stop']
    if 'max_replies_per_chat' in data:
        limit = data['max_replies_per_chat']
        if type(limit) is not int or limit not in {20,60,300,500}:
            raise TenantLinearError('Söhbət limiti 20, 60, 300 və ya 500 olmalıdır.')
        result['max_replies_per_chat'] = limit
    return result


def trigger(data, *, creating=False):
    if not isinstance(data, dict) or set(data) - {'name','trigger_type','condition','actions','enabled','expected_version','setup_key'}:
        raise TenantLinearError('Trigger ayarları düzgün deyil.')
    out = {}
    if creating or 'name' in data:
        out['name'] = _text(data.get('name'), 'Trigger adı', 120, required=True)
    if creating or 'trigger_type' in data:
        kind = data.get('trigger_type')
        if kind not in {'new_inquiry','phone_received','phrase'}:
            raise TenantLinearError('Trigger növü dəstəklənmir.')
        out['trigger_type'] = kind
    if creating or 'condition' in data:
        condition = data.get('condition', {})
        if not isinstance(condition, dict) or set(condition) - {'phrase','target_stage_id','task_text'}:
            raise TenantLinearError('Trigger şərti düzgün deyil.')
        phrase = _text(condition.get('phrase',''), 'Açar ifadə', 200)
        if data.get('trigger_type', '') == 'phrase' and not phrase:
            raise TenantLinearError('Açar ifadə tələb olunur.')
        target_stage=_text(condition.get('target_stage_id',''),'Hədəf mərhələ',120)
        task_text=_text(condition.get('task_text',''),'Tapşırıq mətni',500)
        out['condition'] = {'phrase': phrase,'target_stage_id':target_stage,'task_text':task_text}
    if creating or 'actions' in data:
        actions = data.get('actions', [])
        allowed = {'handoff','handoff_after_reply','call_expert','create_task','update_phone','move_stage'}
        if not isinstance(actions, list) or len(actions) > 4 or any(action not in allowed for action in actions):
            raise TenantLinearError('Trigger əməliyyatları düzgün deyil.')
        out['actions'] = list(dict.fromkeys(actions))
    condition=out.get('condition',data.get('condition',{}))
    actions=out.get('actions',data.get('actions',[]))
    if 'move_stage' in actions and not condition.get('target_stage_id'):
        raise TenantLinearError('Mərhələ dəyişmək üçün hədəf mərhələni seçin.')
    if 'create_task' in actions and not condition.get('task_text'):
        raise TenantLinearError('Tapşırıq yaratmaq üçün tapşırıq mətnini daxil edin.')
    if creating or 'enabled' in data:
        if not isinstance(data.get('enabled',False),bool):
            raise TenantLinearError('Trigger aktivliyi düzgün deyil.')
        out['enabled'] = data.get('enabled',False)
    if 'setup_key' in data:
        if not creating:
            raise TenantLinearError('Yenidən cəhd açarı dəyişdirilə bilməz.')
        out['setup_key'] = setup_key(data['setup_key'])
    return out


def knowledge(data, *, creating=False):
    if not isinstance(data, dict) or set(data) - {'title', 'kind', 'content', 'active'}:
        raise TenantLinearError('Bilik mənbəyi düzgün deyil.')
    result = {}
    if creating or 'title' in data:
        result['title'] = _text(data.get('title'), 'Mənbə adı', 180, required=True)
    if creating or 'kind' in data:
        kind = data.get('kind', 'text')
        if kind not in {'text', 'faq', 'txt', 'pdf', 'docx'}:
            raise TenantLinearError('Mənbə növü düzgün deyil.')
        result['kind'] = kind
    if creating or 'content' in data:
        content = _text(data.get('content', ''), 'Mətn', MAX_TEXT)
        if not content:
            raise TenantLinearError('Mətn boş ola bilməz.')
        result['content'] = content
    if creating or 'active' in data:
        active = data.get('active', True)
        if not isinstance(active, bool):
            raise TenantLinearError('Mənbə aktivliyi düzgün deyil.')
        result['active'] = active
    return result


def chunk_text(content):
    text = re.sub(r'\s+', ' ', str(content or '')).strip()
    if len(text) > MAX_EXTRACTED_TEXT:
        raise TenantLinearError('Çıxarılan mətn həddən artıq böyükdür.')
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + CHUNK_SIZE)
        if end < len(text):
            boundary = text.rfind(' ', start + CHUNK_SIZE // 2, end)
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - CHUNK_OVERLAP)
    return chunks
