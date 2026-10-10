"""Pure PostgreSQL filters for tenant CRM snapshots; no DB or provider calls.

Values always remain separate from SQL. Explicit empty/unknown scopes deny
access; a missing scope preserves the existing trusted caller contract.
"""
from uuid import UUID

from crm_stage_labels import stage_display_name


def public_crm_row(row: dict) -> dict:
    """Serialize a snapshot without modifying the original database row."""
    result = dict(row)
    for key, value in result.items():
        if isinstance(value, UUID):
            result[key] = str(value)
    if 'stage_name' in result:
        result['stage_name'] = stage_display_name(result.get('status_id'), result['stage_name'])
    for key in ('last_message_at', 'source_updated_at', 'synced_at', 'due_at', 'happened_at', 'created_at'):
        if result.get(key):
            result[key] = result[key].isoformat()
    if not isinstance(result.get('raw'), dict):
        result['raw'] = {}
    return result


def deal_filters(*, tenant_id: str, search: str = '', pipeline_ids: list[int] | None = None,
                 status_ids: list[int] | None = None, scope: list[dict] | None = None) -> tuple[str, list]:
    clauses = ['tenant_id = %s::uuid', 'deleted_at IS NULL']
    values: list = [tenant_id]
    if scope is not None:
        scope_clauses = []
        for rule in scope:
            clause = 'pipeline_id = %s'
            values.append(int(rule['pipeline_id']))
            if rule.get('status_ids'):
                clause += ' AND status_id = ANY(%s)'
                values.append([int(sid) for sid in rule['status_ids']])
            scope_clauses.append('(' + clause + ')')
        clauses.append('(' + ' OR '.join(scope_clauses) + ')' if scope_clauses else 'FALSE')
    needle = str(search or '').strip()[:160]
    if needle:
        clauses.append('(name ILIKE %s OR contact_name ILIKE %s OR phone ILIKE %s OR stage_name ILIKE %s)')
        values.extend([f'%{needle}%'] * 4)
    normalized_pipelines = [int(value) for value in (pipeline_ids or []) if str(value).isdigit()]
    normalized_statuses = [int(value) for value in (status_ids or []) if str(value).isdigit()]
    if normalized_pipelines:
        clauses.append('pipeline_id = ANY(%s)')
        values.append(normalized_pipelines)
    if normalized_statuses:
        clauses.append('status_id = ANY(%s)')
        values.append(normalized_statuses)
    return ' AND '.join(clauses), values


def task_filters(*, tenant_id: str, responsible_id: int | None = None,
                 scope: dict | None = None) -> tuple[str, list]:
    clauses = ['tenant_id = %s::uuid']
    clauses.append('NOT EXISTS (SELECT 1 FROM saas_crm_deals deleted WHERE deleted.tenant_id=saas_crm_tasks.tenant_id '
                   'AND deleted.kommo_lead_id=saas_crm_tasks.kommo_lead_id AND deleted.deleted_at IS NOT NULL)')
    values: list = [tenant_id]
    if scope is not None:
        kind = scope.get('kind')
        if kind == 'pipelines':
            pipeline_clauses = []
            for item in scope.get('pipelines', []):
                pipeline_clauses.append('(d.pipeline_id = %s' + (' AND d.status_id = ANY(%s)' if item.get('status_ids') else '') + ')')
                values.append(int(item['pipeline_id']))
                if item.get('status_ids'):
                    values.append(item['status_ids'])
            clauses.append('EXISTS (SELECT 1 FROM saas_crm_deals d WHERE d.tenant_id = saas_crm_tasks.tenant_id '
                           'AND d.kommo_lead_id = saas_crm_tasks.kommo_lead_id AND ('
                           + (' OR '.join(pipeline_clauses) or 'FALSE') + '))')
        elif kind == 'marker' and scope.get('marker'):
            clauses.append('strpos(text, %s) > 0')
            values.append(str(scope['marker']))
        elif kind != 'all':
            clauses.append('FALSE')
    if responsible_id is not None:
        clauses.append('responsible_id = %s')
        values.append(int(responsible_id))
    return ' AND '.join(clauses), values
