"""Owner-only, read-only entry point for the Business Analyst source layer.

The tool registry deliberately starts small. It reuses the existing owner risk
reader and reports source-schema coverage without reading customer content.
No caller-controlled SQL, table names, URLs, credentials, or write tools exist.
"""
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access


_SOURCE_CATALOG = (
    {
        'id': 'connect_conversations',
        'relations': ('saas_connect_channels', 'saas_connect_conversations', 'saas_crm_messages'),
        'existing_read_path': 'Learning MCP: channels_read, conversations_read, messages_read',
        'scope': 'tenant and configured Connect channel; message content needs a separate content grant',
    },
    {
        'id': 'sales_conversations',
        'relations': ('saas_crm_deals', 'saas_crm_messages', 'saas_crm_tasks'),
        'existing_read_path': 'selected-dialog Copilot and CRM pages; no company-wide Kommo timeline reader',
        'scope': 'selected dialog or existing CRM route',
    },
    {
        'id': 'developer_tasks',
        'relations': ('saas_linear_snapshots', 'saas_linear_sync', 'saas_crm_tasks', 'saas_crm_native_tasks'),
        'existing_read_path': 'Linear task page and owner risk reader; no shared analytics search',
        'scope': 'existing member/team or tenant CRM scope',
    },
    {
        'id': 'support_cases',
        'relations': ('saas_support_tickets', 'saas_support_ticket_messages', 'saas_knowledge_candidates', 'saas_knowledge_extractions'),
        'existing_read_path': 'Learning MCP ticket metadata and verified case/evidence readers',
        'scope': 'tenant-scoped tickets; case content remains source- and grant-bound',
    },
    {
        'id': 'knowledge',
        'relations': ('saas_ai_knowledge_sources', 'saas_instruction_lessons'),
        'existing_read_path': 'Learning MCP knowledge/instruction readers and agent-bound internal lookup',
        'scope': 'tenant and current source/agent publication state',
    },
    {
        'id': 'customer_identity',
        'relations': ('saas_crm_entity_links', 'saas_connect_direct_identities', 'saas_crm_native_contact_identities'),
        'existing_read_path': 'source-bound identity and merge services; no analytics timeline reader',
        'scope': 'exact verified links only; conflicts remain unresolved',
    },
    {
        'id': 'company_risks',
        'relations': ('saas_proactive_risk_findings', 'saas_proactive_risk_scope_checks'),
        'existing_read_path': 'owner-only proactive risk reader reused by this tool',
        'scope': 'current configured Linear and cached CRM-task sources',
    },
)

_TOOLS = frozenset({'list_analytics_sources', 'get_company_risks'})


def _owner(cur, profile):
    tenant, actor, policy = access(cur, profile, 'dialogs')
    if policy.profile.get('role') != 'owner':
        raise TenantLinearError('Company owner access required.', 403)
    return tenant, actor


def list_analytics_sources(profile):
    """Return schema presence only; data coverage and freshness stay unknown."""
    from tenant_platform import _connect

    with _connect() as conn:
        with conn.cursor() as cur:
            _owner(cur, profile)
            relations = sorted({relation for source in _SOURCE_CATALOG for relation in source['relations']})
            present = {}
            for relation in relations:
                cur.execute('SELECT to_regclass(%s) AS relation', (relation,))
                present[relation] = bool((cur.fetchone() or {}).get('relation'))

    sources = []
    for source in _SOURCE_CATALOG:
        required = list(source['relations'])
        ready = all(present.get(relation, False) for relation in required)
        sources.append({
            'id': source['id'],
            'schema_status': 'present' if ready else 'partial_or_missing',
            'existing_read_path': source['existing_read_path'],
            'scope': source['scope'],
            'freshness': 'not_checked',
            'record_coverage': 'not_checked',
            'relations': [{'name': relation, 'present': present.get(relation, False)} for relation in required],
        })
    return {'read_only': True, 'assistant_connected': False, 'checked_at': None, 'sources': sources,
            'warning': 'Schema presence does not prove current data, provider connectivity, or complete history.'}


def get_company_risks(profile, *, limit=10):
    """Reuse the existing source-revalidated owner risk reader."""
    if type(limit) is not int or not 1 <= limit <= 20:
        raise TenantLinearError('Invalid risk page size.', 400)
    from tenant_proactive_risk_store import list_findings

    result = list_findings(profile, limit=limit)
    # Identity is used for server-side scoping, not needed by the model or UI.
    result.pop('tenant_id', None)
    result.pop('user_id', None)
    result.pop('alert_opt_in_channels', None)
    result['read_only'] = True
    return result


def execute(profile, tool, arguments=None):
    """Execute one allowlisted owner tool; arguments are strict and bounded."""
    if not isinstance(tool, str) or tool not in _TOOLS:
        raise TenantLinearError('Unknown analytics tool.', 404)
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise TenantLinearError('Invalid analytics arguments.', 400)

    if tool == 'list_analytics_sources':
        if arguments:
            raise TenantLinearError('This analytics tool takes no arguments.', 400)
        tenant, actor = _authorized_owner(profile)
        result = list_analytics_sources(profile)
    else:
        if set(arguments) - {'limit'}:
            raise TenantLinearError('Invalid analytics arguments.', 400)
        tenant, actor = _authorized_owner(profile)
        result = get_company_risks(profile, limit=arguments.get('limit', 10))

    from tenant_platform import append_audit_event
    append_audit_event(tenant_id=tenant, actor_telegram_id=actor,
        action='ai_business_analytics_tool_read', entity_type='analytics_tool', entity_id=tool,
        payload={'tool': tool, 'read_only': True})
    return result


def _authorized_owner(profile):
    from tenant_platform import _connect

    with _connect() as conn:
        with conn.cursor() as cur:
            return _owner(cur, profile)


def definitions():
    """Tool schemas for the existing assistant runtime; both operations are read-only."""
    return [
        {'name': 'list_analytics_sources',
         'description': 'Read schema presence and known coverage limits for company analytics sources. Does not read customer content or prove freshness.',
         'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
         'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
        {'name': 'get_company_risks',
         'description': 'Read current owner-only Linear and cached CRM-task risk findings from the existing source-revalidated risk reader.',
         'inputSchema': {'type': 'object', 'properties': {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20}}, 'additionalProperties': False},
         'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
    ]
