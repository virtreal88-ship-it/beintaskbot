"""Validated company-owned Linear/news configuration; no legacy identities."""
import uuid


class TenantLinearError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def owner(profile):
    if not profile or profile.get('active') is False or profile.get('role') != 'owner' or profile.get('tenant_status', 'active') != 'active':
        raise TenantLinearError('Yalnız şirkət sahibi üçün.', 403)
    return str(profile['tenant_id']), int(profile['telegram_id'])


def identifier(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise TenantLinearError('Linear ID-si düzgün deyil.') from None


def settings(value):
    if not isinstance(value, dict) or set(value) - {'team_id', 'done_state_ids', 'members', 'news'}:
        raise TenantLinearError('Linear ayarları düzgün deyil.')
    team = identifier(value['team_id']) if value.get('team_id') else ''
    states = value.get('done_state_ids', [])
    if not isinstance(states, list) or len(states) > 50:
        raise TenantLinearError('Status siyahısı düzgün deyil.')
    members = value.get('members', {})
    if not isinstance(members, dict) or len(members) > 500:
        raise TenantLinearError('Əməkdaş siyahısı düzgün deyil.')
    normalized = {}
    for key, binding in members.items():
        if not isinstance(key, str) or not key.isascii() or not key.isdecimal() or int(key) <= 0 or str(int(key)) != key:
            raise TenantLinearError('Əməkdaş ID-si düzgün deyil.')
        if not isinstance(binding, dict) or set(binding) - {'account', 'operator', 'can_create', 'can_edit', 'can_review_news'}:
            raise TenantLinearError('Əməkdaş ayarları düzgün deyil.')
        row = {}
        for field in ('account', 'operator'):
            text = binding.get(field, '')
            if not isinstance(text, str) or len(text) > 160 or any(ord(c) < 32 for c in text):
                raise TenantLinearError('Account/Operator düzgün deyil.')
            row[field] = text.strip()
        for field in ('can_create', 'can_edit', 'can_review_news'):
            flag = binding.get(field, False)
            if not isinstance(flag, bool):
                raise TenantLinearError('İcazə boolean olmalıdır.')
            row[field] = flag
        normalized[key] = row
    news = value.get('news', {})
    if not isinstance(news, dict) or set(news) - {'channel', 'projects', 'manual_approval', 'main_issues_only', 'retention_days'}:
        raise TenantLinearError('Xəbər ayarları düzgün deyil.')
    for field in ('manual_approval', 'main_issues_only'):
        if field in news and news[field] is not True:
            raise TenantLinearError('Xəbərlər üçün əl ilə təsdiq və əsas tapşırıq məcburidir.')
    if 'retention_days' in news and (type(news['retention_days']) is not int or news['retention_days'] != 90):
        raise TenantLinearError('Xəbərlərin saxlama müddəti 90 gündür.')
    channel = news.get('channel', '')
    import re
    if not isinstance(channel, str) or (channel and not re.fullmatch(r'@[A-Za-z][A-Za-z0-9_]{4,31}|-100[0-9]{5,16}', channel)):
        raise TenantLinearError('Telegram kanalını @kanal və ya -100… olaraq daxil edin.')
    projects = news.get('projects', [])
    if not isinstance(projects, list) or len(projects) > 50:
        raise TenantLinearError('Layihə siyahısı düzgün deyil.')
    # Safety invariants are server-owned, never configurable auto-publication flags.
    return {'team_id': team, 'done_state_ids': list(dict.fromkeys(identifier(s) for s in states)),
            'members': normalized, 'news': {'channel': channel,
            'projects': list(dict.fromkeys(identifier(p) for p in projects)),
            'manual_approval': True, 'main_issues_only': True, 'retention_days': 90}}
