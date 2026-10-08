"""Pure configurable visibility and actions. No employee names or status names."""
import re
from tenant_linear_policy import identifier, TenantLinearError


def workflow_settings(value):
    if not isinstance(value, dict) or set(value) - {'enabled', 'visibility_mode', 'account_prefix', 'operator_prefix', 'creation_state_id', 'buttons', 'required_fields'}:
        raise TenantLinearError('Linear iş qaydaları düzgün deyil.')
    enabled = value.get('enabled', False)
    if not isinstance(enabled, bool) or value.get('visibility_mode', 'any') not in {'any', 'all'}:
        raise TenantLinearError('Görünüş qaydası düzgün deyil.')
    result = {'enabled': enabled, 'visibility_mode': value.get('visibility_mode', 'any')}
    required = value.get('required_fields', ['account', 'operator', 'project', 'body'])
    if not isinstance(required, list) or any(not isinstance(f, str) or f not in {'account', 'operator', 'project', 'body'} for f in required):
        raise TenantLinearError('Məcburi sahələr düzgün deyil.')
    result['required_fields'] = list(dict.fromkeys(required))
    for name, default in (('account_prefix', 'Account'), ('operator_prefix', 'Operator')):
        prefix = value.get(name, default)
        if not isinstance(prefix, str) or not re.fullmatch(r'[\w .-]{1,40}', prefix, re.UNICODE):
            raise TenantLinearError('Metadata başlığı düzgün deyil.')
        if not prefix.strip():
            raise TenantLinearError('Metadata başlığı boş ola bilməz.')
        result[name] = prefix.strip()
    if result['account_prefix'].casefold() == result['operator_prefix'].casefold():
        raise TenantLinearError('Account və Operator başlıqları fərqli olmalıdır.')
    result['creation_state_id'] = identifier(value['creation_state_id']) if value.get('creation_state_id') else ''
    buttons = value.get('buttons', [])
    if not isinstance(buttons, list) or len(buttons) > 20:
        raise TenantLinearError('Keçid düymələri düzgün deyil.')
    result['buttons'] = []
    for button in buttons:
        if not isinstance(button, dict) or set(button) - {'id', 'label', 'from_state_ids', 'to_state_id', 'require_reason'}:
            raise TenantLinearError('Düymə qaydası düzgün deyil.')
        key = button.get('id', '')
        label = button.get('label', '')
        sources = button.get('from_state_ids')
        reason = button.get('require_reason', False)
        if not isinstance(key, str) or not re.fullmatch(r'[a-z0-9_-]{1,40}', key) or key in {b['id'] for b in result['buttons']}:
            raise TenantLinearError('Düymə ID-si unikal olmalıdır.')
        if not isinstance(label, str) or not label.strip() or len(label) > 80 or not isinstance(reason, bool):
            raise TenantLinearError('Düymə adı və səbəb qaydası düzgün deyil.')
        if not isinstance(sources, list) or not 1 <= len(sources) <= 50:
            raise TenantLinearError('Düymənin mənbə statuslarını seçin.')
        result['buttons'].append({'id': key, 'label': label.strip(), 'from_state_ids': list(dict.fromkeys(identifier(s) for s in sources)),
            'to_state_id': identifier(button.get('to_state_id')), 'require_reason': reason})
    return result


def metadata(issue, config):
    result = {}
    for name in ('account', 'operator'):
        prefix = config.get(name + '_prefix', name.title())
        values = re.findall(r'^[ \t]*' + re.escape(prefix) + r':[ \t]*([^\n\r]+)', str(issue.get('description') or ''), re.I | re.M)
        # Ambiguous duplicate metadata never grants access.
        result[name] = values[0].strip() if len(values) == 1 else ''
    return result


def visible(profile, config, issue):
    if not config.get('team_id') or str((issue.get('team') or {}).get('id')) != config['team_id']:
        return False
    if profile['role'] == 'owner':
        return True
    binding = config.get('members', {}).get(str(profile['telegram_id']), {})
    if binding.get('can_view_all') is True:
        return True
    rules = config.get('workflow', {})
    values = metadata(issue, rules)
    comparisons = [values[name].casefold() == str(binding[name]).strip().casefold()
                   for name in ('account', 'operator') if binding.get(name)]
    if binding.get('assignee_id'):
        comparisons.append(str((issue.get('assignee') or {}).get('id')) == binding['assignee_id'])
    if not comparisons:
        return False
    return all(comparisons) if rules.get('visibility_mode') == 'all' else any(comparisons)


def capabilities(profile, config, issue=None):
    privileged = profile['role'] == 'owner'
    binding = config.get('members', {}).get(str(profile['telegram_id']), {})
    can_create = privileged or binding.get('can_create') is True
    required = config.get('workflow', {}).get('required_fields', ['account', 'operator', 'project', 'body'])
    if not privileged and any(not binding.get(name) for name in ('account', 'operator') if name in required):
        can_create = False
    buttons = config.get('workflow', {}).get('buttons', [])
    allowed = [{'id': b['id'], 'label': b['label'], 'require_reason': b['require_reason']}
        for b in buttons if issue and (issue.get('state') or {}).get('id') in b['from_state_ids']
        and (privileged or b['id'] in binding.get('button_ids', []))]
    return {'can_create': can_create and bool(config.get('workflow', {}).get('creation_state_id')),
        'can_edit': privileged or binding.get('can_edit') is True,
        'can_change_status': privileged or binding.get('can_change_status') is True, 'buttons': allowed}
