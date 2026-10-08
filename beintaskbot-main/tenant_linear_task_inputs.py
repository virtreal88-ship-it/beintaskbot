"""Validate editable fields and prohibit metadata injection or scope escalation."""
import re
from tenant_linear_policy import identifier, TenantLinearError
from tenant_linear_runtime_policy import metadata, visible
from tenant_linear_tasks_provider import team_members
from tenant_linear_provider import team_catalog


def text(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
        raise TenantLinearError('Başlıq və mətn doldurulmalıdır.')
    return value.strip()


def optional_text(value, limit):
    if value is None:
        return ''
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        raise TenantLinearError('Mətn sahəsi düzgün deyil.')
    return value.strip()


def body_text(description, rules):
    prefixes = [rules.get('account_prefix', 'Account'), rules.get('operator_prefix', 'Operator')]
    return '\n'.join(line for line in str(description or '').splitlines()
        if not any(re.match(r'^\s*' + re.escape(prefix) + r':', line, re.I) for prefix in prefixes)).strip()


def edit_fields(profile, config, key, data, existing=None):
    rules = config.get('workflow', {})
    required = rules.get('required_fields', ['account', 'operator', 'project', 'body'])
    title = text(data.get('title'), 255)
    body = text(data.get('body'), 10000) if 'body' in required else optional_text(data.get('body'), 10000)
    if len(body) > 10000 or '\x00' in body:
        raise TenantLinearError('Mətn düzgün deyil.')
    for prefix in (rules.get('account_prefix', 'Account'), rules.get('operator_prefix', 'Operator')):
        if re.search(r'^\s*' + re.escape(prefix) + r':', body, re.I | re.M):
            raise TenantLinearError('Account/Operator mətnin daxilində yazılmamalıdır.')
    priority = data.get('priority')
    if type(priority) is not int or priority not in range(5):
        raise TenantLinearError('Prioritet düzgün deyil.')
    project = identifier(data.get('project_id')) if data.get('project_id') or 'project' in required else None
    binding = config.get('members', {}).get(str(profile['telegram_id']), {})
    default_assignee = (existing.get('assignee') or {}).get('id') if existing else binding.get('assignee_id')
    assignee = identifier(data.get('assignee_id', default_assignee)) if data.get('assignee_id', default_assignee) else None
    team = team_catalog(key, config['team_id'])
    if not team or team['projects']['pageInfo']['hasNextPage']:
        raise TenantLinearError('Layihə kataloqu tam deyil.', 409)
    if project and project not in {p['id'] for p in team['projects']['nodes']}:
        raise TenantLinearError('Layihə bu komandaya aid deyil.')
    if assignee:
        people = team_members(key, config['team_id'])
        if people['pageInfo']['hasNextPage'] or assignee not in {p['id'] for p in people['nodes']}:
            raise TenantLinearError('İcraçı bu komandaya aid deyil.')
    values = {name: (text(data.get(name), 160) if name in required else optional_text(data.get(name), 160)) for name in ('account', 'operator')} if profile['role'] == 'owner' else (
        metadata(existing, rules) if existing else {name: binding.get(name, '') for name in ('account', 'operator')})
    if any((name in required and not value) or len(value) > 160 or any(ord(c) < 32 for c in value) for name, value in values.items()):
        raise TenantLinearError('Account/Operator əlaqəsini administrator təyin etməlidir.')
    description = ('\n'.join(rules.get(name + '_prefix', name.title()) + ': ' + values[name] for name in ('account', 'operator') if values[name]) + '\n\n' + body).strip()
    fields = {'title': title, 'description': description, 'priority': priority, 'projectId': project, 'assigneeId': assignee}
    candidate = {**(existing or {}), 'team': {'id': config['team_id']}, 'description': description, 'assignee': {'id': assignee}}
    if not visible(profile, config, candidate):
        raise TenantLinearError('Dəyişiklik tapşırığı sizin görünüş qaydanızdan çıxarır.', 403)
    return fields
