"""Task reads: provider-side coarse filters plus exact server-side authorization."""
from tenant_platform import _connect, _ensure_schema
from tenant_linear_context import context
from tenant_linear_policy import TenantLinearError
from tenant_linear_runtime_policy import visible, metadata, capabilities
from tenant_linear_tasks_provider import issue_page, team_members
from tenant_linear_provider import team_catalog
from tenant_linear_task_inputs import body_text


def provider_filter(profile, config):
    result = {'team': {'id': {'eq': config['team_id']}}}
    binding = config.get('members', {}).get(str(profile['telegram_id']), {})
    if profile['role'] == 'owner' or binding.get('can_view_all') is True:
        return result
    rules = config.get('workflow', {})
    clauses = [{'description': {'containsIgnoreCase': binding[name]}}
        for name in ('account', 'operator') if binding.get(name)]
    if binding.get('assignee_id'):
        clauses.append({'assignee': {'id': {'eq': binding['assignee_id']}}})
    if not clauses:
        return None
    result['and' if rules.get('visibility_mode') == 'all' else 'or'] = clauses
    return result


def public_issue(profile, config, row):
    return {**{key: row.get(key) for key in ('id', 'identifier', 'title', 'description', 'priority', 'url', 'createdAt', 'updatedAt', 'state', 'project', 'assignee')},
            **metadata(row, config.get('workflow', {})), 'body': body_text(row.get('description'), config.get('workflow', {})),
            'capabilities': capabilities(profile, config, row)}


def listing(session, *, after=None, search=''):
    if after is not None and (not isinstance(after, str) or len(after) > 512):
        raise TenantLinearError('Səhifə kursoru düzgün deyil.')
    if not isinstance(search, str) or len(search) > 160:
        raise TenantLinearError('Axtarış düzgün deyil.')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            profile, config, key, version = context(cur, session)
            issue_filter = provider_filter(profile, config)
            if issue_filter is None:
                page = {'nodes': [], 'pageInfo': {'hasNextPage': False, 'endCursor': None}}
            else:
                if search.strip():
                    # Search is ANDed with team/scope, never replaces authorization.
                    issue_filter = {'and': [issue_filter, {'or': [{'title': {'containsIgnoreCase': search.strip()}},
                        {'description': {'containsIgnoreCase': search.strip()}}]}]}
                page = issue_page(key, issue_filter, after)
            rows = [public_issue(profile, config, row) for row in page['nodes']
                if visible(profile, config, row) and str(metadata(row, config.get('workflow', {})).get('account') or '').strip().casefold() not in {'', 'göstərilməyib', 'gosterilmeyib', '—', '-'}]
            statuses = []
            if after is None and issue_filter is not None:
                team = team_catalog(key, config['team_id'])
                state_nodes = ((team or {}).get('states') or {}).get('nodes') or []
                if not isinstance(state_nodes, list):
                    state_nodes = []
                # Linear's workflow position is the source of truth; Python's stable
                # sort preserves API order if a provider response omits position.
                state_nodes = sorted(enumerate(state_nodes), key=lambda pair: (
                    pair[1].get('position') is None,
                    pair[1].get('position') if pair[1].get('position') is not None else pair[0],
                    pair[0]))
                statuses = [{'id': state.get('id'), 'name': state.get('name'),
                    'color': state.get('color'), 'type': state.get('type'), 'position': state.get('position')}
                    for _, state in state_nodes if state.get('id')]
    return {'issues': rows, 'pageInfo': page['pageInfo'], 'statuses': statuses,
        'capabilities': capabilities(profile, config), 'config_version': version,
        'tenant_id': str(profile['tenant_id']), 'user_id': profile['telegram_id']}


def editor_choices(session):
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            profile, config, key, version = context(cur, session)
            rights = capabilities(profile, config)
            if not (rights['can_create'] or rights['can_edit'] or rights['can_change_status']):
                raise TenantLinearError('Redaktəyə icazə yoxdur.', 403)
            team = team_catalog(key, config['team_id'])
            people = team_members(key, config['team_id'])
    if not team or team['states']['pageInfo']['hasNextPage'] or team['projects']['pageInfo']['hasNextPage'] or people['pageInfo']['hasNextPage']:
        raise TenantLinearError('Kataloq tam deyil. Səhifələmə tələb olunur.', 409)
    return {'states': team['states']['nodes'], 'projects': team['projects']['nodes'], 'assignees': people['nodes'],
        'binding': config.get('members', {}).get(str(profile['telegram_id']), {}), 'owner': profile['role'] == 'owner',
        'config_version': version, 'capabilities': rights,
        'required_fields': config.get('workflow', {}).get('required_fields', ['account', 'operator', 'project', 'body'])}
