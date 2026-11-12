"""Resource-aware AI agent documents, separate from shared AI administration.

C/R/U and unused-agent archive D are configurable. Scoped actors use local source-bound options and
strict document projections. Shared tools/workflows and owner-only permanent deletion do
not inherit authority from agent document grants. The rollout gate stays off.
"""
from tenant_linear_policy import TenantLinearError, identifier
from tenant_policy import TenantPolicy
from tenant_scoped_access import SCOPES, allows_owned_resource, profile_actor, scoped_access_enabled
from tenant_scoped_access_store import read_access_profile, owned_resource_metadata


def context(cur, profile, action=None):
    if not scoped_access_enabled():
        return None
    current = read_access_profile(cur, profile['tenant_id'], profile['telegram_id'], lock=True)
    if not current:
        raise TenantLinearError('İcazə yoxdur.', 403)
    if current.get('role') == 'owner':
        return None
    policy = TenantPolicy(current)
    from tenant_employee_defaults import default_deny
    if default_deny(policy):
        if action is None:
            raise TenantLinearError('ai_shared_operation_requires_unscoped_management', 403)
        return current, policy.action_scope('ai_agents', action) or 'none'
    settings = policy.member_settings
    scopes = settings.get('access_scopes', {})
    if isinstance(scopes, dict) and 'ai_agents' not in scopes:
        return None
    # A malformed matrix is never a fallback to legacy management authority.
    actions = scopes.get('ai_agents') if isinstance(scopes, dict) else None
    if action is None:
        raise TenantLinearError('ai_shared_operation_requires_unscoped_management', 403)
    if not isinstance(actions, dict):
        return current, 'none'
    if action not in actions:
        return None
    value = actions[action]
    return current, value if isinstance(value, str) and value in SCOPES else 'none'


def allowed(cur, state, row):
    current, scope = state
    metadata = owned_resource_metadata(cur, current['tenant_id'], row, lock=True)
    return allows_owned_resource(profile_actor(current), metadata, scope)


def authorize(cur, profile, action, agent_id=None):
    state = context(cur, profile, action)
    if state is None:
        return None
    current, scope = state
    if agent_id is not None:
        # Match save_agent's configuration order: membership/policy fences,
        # tenant configuration mutex, then agent row. Never hold a row while
        # waiting for a creator/recovery holding the configuration mutex.
        mutating = action in {'update', 'delete'}
        if mutating:
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                        ('ai-agent-config:' + str(current['tenant_id']),))
        cur.execute('''SELECT tenant_id,id,access_responsible_id FROM saas_ai_agents
          WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL''' + (' FOR UPDATE' if mutating else ' FOR SHARE'),
                    (str(current['tenant_id']), identifier(agent_id)))
        row = cur.fetchone()
        if not row or not allowed(cur, state, row):
            raise TenantLinearError('Agent tapılmadı.', 404)
    elif action == 'create':
        row = {'tenant_id': current['tenant_id'], 'access_responsible_id': current['telegram_id']}
        if not allowed(cur, state, row):
            raise TenantLinearError('AI Agents idarəetmə icazəsi yoxdur.', 403)
    elif scope == 'none':
        raise TenantLinearError('AI Agents idarəetmə icazəsi yoxdur.', 403)
    return str(current['tenant_id']), int(current['telegram_id'])


def require_references(cur, profile, config):
    """References cannot grant read/use access to another employee's agent."""
    if all(context(cur, profile, action) is None for action in ('view', 'create', 'update', 'delete')):
        return
    if config.get('id'):
        cur.execute('SELECT actions,condition FROM saas_ai_agent_triggers WHERE tenant_id=%s::uuid AND agent_id=%s::uuid', (str(profile['tenant_id']),str(config['id'])))
        if any(set(row.get('actions') or []) - {'handoff','handoff_after_reply'} or any(value for key,value in (row.get('condition') or {}).items() if key != 'phrase') for row in cur.fetchall()):
            raise TenantLinearError('ai_trigger_shared_action_requires_management', 403)
    policy = config.get('expert_policy') or {}
    ids = set(policy.get('expert_agent_ids') or []) | set(policy.get('allowed_primary_ids') or [])
    if policy.get('expert_agent_id'):
        ids.add(policy['expert_agent_id'])
    # The executor and diagnostic tools are not partitioned by responsible
    # employee yet. Even access to both rows cannot grant those shared tools.
    if ids or policy.get('tools'):
        raise TenantLinearError('ai_cross_agent_scope_not_ready', 403)
    response = config.get('response_policy') or {}
    if (response.get('auto_case_dispatch_enabled') or response.get('developer_routing') == 'primary_reserve'
            or (response.get('enabled') and response.get('action') not in {'stop', 'clarify'})):
        raise TenantLinearError('ai_shared_dispatch_scope_not_ready', 403)


def require_runtime_source_boundary(cur, profile, config):
    current = document_profile(cur, profile)
    if current is not None:
        require_sources(cur, current, config.get('scopes') or [], write=True)


def capabilities(policy):
    if policy.profile.get('role') == 'owner' or not policy.has_scoped_access('ai_agents'):
        return None
    return {'scoped': True, **{action: (policy.action_scope('ai_agents', action) != 'none'
            if policy.action_scope('ai_agents', action) is not None else (action != 'delete' and policy.legacy_allows('ai_agents')))
            for action in ('view', 'create', 'update', 'delete')}}


def document_profile(cur, profile):
    for action in ('view', 'create', 'update', 'delete'):
        state = context(cur, profile, action)
        if state is not None:
            return state[0]
    return None


def require_sources(cur, current, scopes, *, write=False):
    from tenant_dialog_access import require_source
    policy = TenantPolicy(current)
    for source in scopes:
        provider = source.get('provider', 'kommo')
        for action in (('view', 'update') if write else ('view',)):
            if policy.action_scope('dialogs', action) is None and not policy.legacy_allows('dialogs'):
                raise TenantLinearError('ai_source_access_denied', 403)
            require_source(cur, str(current['tenant_id']), policy, provider, source.get('channel_id', ''), action)
        if provider == 'kommo':
            # Source-wide AI authority does not replace current pipeline grants.
            pipeline = next((row for row in policy.pipeline_scope() if str(row['pipeline_id']) == str(source.get('pipeline_id'))), None)
            if not pipeline or (pipeline['status_ids'] and str(source.get('stage_id')) not in {str(x) for x in pipeline['status_ids']}):
                raise TenantLinearError('ai_pipeline_access_denied', 403)
            if source.get('channel_id'):
                raise TenantLinearError('ai_kommo_channel_scope_not_ready', 403)


def project_document(cur, profile, row):
    current = document_profile(cur, profile)
    if current is None:
        return row
    # Explicit whitelist: response policies, bindings, diagnostic settings and
    # future nested fields cannot silently expose cross-agent/customer IDs.
    result = {key: row[key] for key in ('id', 'tenant_id', 'name', 'instructions', 'enabled',
              'mode', 'language', 'style', 'delay_seconds', 'schedule', 'model', 'version',
              'created_at', 'updated_at') if key in row}
    schedule = row.get('schedule') or {}
    result['schedule'] = {key: schedule[key] for key in ('timezone','days','start','end') if key in schedule}
    can_update = False
    try:
        state = context(cur, profile, 'update')
        can_update = allowed(cur, state, row) if state is not None else TenantPolicy(current).legacy_allows('ai_agents')
        require_references(cur, profile, row)
        require_sources(cur, current, row.get('scopes') or [], write=True)
    except TenantLinearError:
        can_update = False
    try:
        require_sources(cur, current, row.get('scopes') or [])
        result['scopes'] = [{key: source.get(key) for key in ('provider','pipeline_id','stage_id','channel_id','instructions','allow_group_messages') if key in source}
                            for source in row.get('scopes') or []]
    except TenantLinearError:
        can_update = False
    delete_state = context(cur, profile, 'delete')
    can_delete = delete_state is not None and allowed(cur, delete_state, row)
    result['capabilities'] = {'update': can_update, 'delete': can_delete}
    return result


def mutation_receipt(cur, profile, row):
    if document_profile(cur, profile) is None:
        return row
    return {key: str(row[key]) if key == 'id' else row[key] for key in ('id', 'version') if key in row}


def configuration_options(cur, current):
    """Local, source-bound editor catalog. Never refresh providers or list jobs."""
    import tenant_ai_agents_store as store
    caps = capabilities(TenantPolicy(current)) or {}
    if not any(caps.get(action) for action in ('view', 'create', 'update')):
        raise TenantLinearError('İcazə yoxdur.', 403)
    tenant = str(current['tenant_id'])
    cur.execute('SELECT connect_channel_id::text AS id,name FROM saas_connect_channels WHERE tenant_id=%s::uuid AND active=TRUE ORDER BY name,connect_channel_id', (tenant,))
    channels = []
    for row in cur.fetchall():
        try:
            require_sources(cur, current, [{'provider':'connect','channel_id':row['id']}], write=True)
        except TenantLinearError:
            continue
        channels.append(dict(row))
    cur.execute('''SELECT p.pipeline_id,p.name AS pipeline_name,s.stage_id,s.name
      FROM saas_tenant_pipelines p JOIN saas_tenant_pipeline_stages s
        ON s.tenant_id=p.tenant_id AND s.pipeline_id=p.pipeline_id
      WHERE p.tenant_id=%s::uuid AND p.active=TRUE ORDER BY p.pipeline_id,s.sort_order,s.stage_id''', (tenant,))
    pipelines, stages = {}, []
    for row in cur.fetchall():
        try:
            require_sources(cur, current, [{'provider':'kommo','pipeline_id':row['pipeline_id'],'stage_id':row['stage_id']}], write=True)
        except TenantLinearError:
            continue
        pipelines[row['pipeline_id']] = {'pipeline_id':row['pipeline_id'],'name':row['pipeline_name']}
        stages.append({'pipeline_id':row['pipeline_id'],'stage_id':row['stage_id'],'name':row['name']})
    cur.execute("SELECT metadata,secrets IS NOT NULL AS has_secret FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'", (tenant,))
    provider = cur.fetchone() or {}
    meta = provider.get('metadata') or {}
    models = store.platform_model_choices() if store.platform_service_ready() else []
    if provider.get('has_secret') and meta.get('enabled') is True and meta.get('consent') is True:
        ids = {item['id'] for item in models}
        for name in [meta.get('model'), *(meta.get('models') or [])]:
            if isinstance(name, str) and name.strip() and name not in ids:
                models.append({'id':name,'label':name,'source':'company'})
                ids.add(name)
    return {'scoped':True,'pipelines':list(pipelines.values()),'stages':stages,
            'channels':[],'connect_channels':channels,'models':models,
            'model_catalog':{'state':'ready' if models else 'unavailable'}}


def require_trigger_write(cur, profile, agent_id, config):
    current = document_profile(cur, profile)
    if current is None:
        return
    cur.execute('SELECT scopes,expert_policy,response_policy FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid', (str(current['tenant_id']),agent_id))
    parent = cur.fetchone() or {}
    require_sources(cur, current, parent.get('scopes') or [], write=True)
    require_references(cur, profile, parent)
    if (set(config.get('actions') or []) - {'handoff','handoff_after_reply'}
            or any(value for key,value in (config.get('condition') or {}).items() if key != 'phrase')):
        raise TenantLinearError('ai_trigger_shared_action_requires_management', 403)
