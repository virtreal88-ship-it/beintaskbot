"""Read the existing native and cached CRM tasks without syncing or repairing DBs."""
from __future__ import annotations

from datetime import date, datetime
import json
import re
from uuid import UUID

from psycopg.errors import SerializationFailure

from tenant_platform_core import _connect
from tenant_policy import TenantPolicy
from tenant_workflow_store import _read_tenant_workflow
from tenant_crm_queries import task_filters


class TaskFlowError(ValueError):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


def public(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: public(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [public(item) for item in value]
    return value


def _uuid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise TaskFlowError('task_invalid_query') from None


def _positive(value):
    if not re.fullmatch(r'[1-9][0-9]{0,18}', str(value)) or int(value) > 9223372036854775807:
        raise TaskFlowError('task_invalid_query')
    return str(value)


def query(request, profile, *, required=False):
    """Browser intent is a deny-only check; the cookie remains authority."""
    result = {}
    params = request.query if hasattr(request, 'query') else request.rel_url.query
    for key in ('flow', 'source', 'task_id', 'request_id', 'expected_tenant_id', 'expected_user_id'):
        values = params.getall(key, []) if hasattr(params, 'getall') else (
            [params[key]] if key in params else [])
        if len(values) > 1 or (values and not str(values[0])):
            raise TaskFlowError('task_invalid_query')
        if values:
            result[key] = values[0]
    expected = ('expected_tenant_id', 'expected_user_id')
    if required or any(key in result for key in expected):
        if not profile:
            raise TaskFlowError('task_forbidden', 403)
        if not all(key in result for key in expected):
            raise TaskFlowError('task_invalid_query')
        tenant, actor = _uuid(result[expected[0]]), _positive(result[expected[1]])
        if tenant != _uuid(profile.get('tenant_id')) or int(actor) != int(profile.get('telegram_id') or 0):
            raise TaskFlowError('task_context_changed', 409)
    if result.get('flow') not in (None, '1') or result.get('source') not in (None, 'native', 'kommo'):
        raise TaskFlowError('task_invalid_query')
    if result.get('task_id') and result.get('request_id'):
        raise TaskFlowError('task_invalid_query')
    if 'request_id' in result:
        result['request_id'] = _uuid(result['request_id'])
    return result


def _require_schema(conn):
    with conn.cursor() as cur:
        cur.execute('''SELECT bool_and(to_regclass(name) IS NOT NULL) AS ready
          FROM unnest(%s::text[]) AS required(name)''', ([
            'saas_tenants', 'saas_tenant_members', 'saas_tenant_workflow_policies',
            'saas_tenant_pipelines', 'saas_tenant_pipeline_stages',
            'saas_crm_native_tasks', 'saas_crm_native_deals', 'saas_crm_native_contacts',
            'saas_crm_tasks', 'saas_crm_deals', 'saas_task_commands'],))
        if not (cur.fetchone() or {}).get('ready'):
            raise TaskFlowError('task_unavailable', 503)


def _profiles(cur, tenant, actor, *, everyone=False):
    # Match current task/member writers; do not lock pipelines after memberships.
    cur.execute('SELECT id,modules,onboarding FROM saas_tenants WHERE id=%s::uuid FOR SHARE', (tenant,))
    company = cur.fetchone()
    if not company:
        raise TaskFlowError('task_forbidden', 403)
    cur.execute('''SELECT policy_key FROM saas_tenant_workflow_policies
      WHERE tenant_id=%s::uuid AND policy_key=ANY(%s::text[])
      ORDER BY policy_key FOR SHARE''', (tenant, ['members', 'modules']))
    cur.fetchall()
    cur.execute('''SELECT tenant_id,telegram_id,display_name,role,permissions,active
      FROM saas_tenant_members WHERE tenant_id=%s::uuid'''+('' if everyone else ' AND telegram_id=%s')+
      ' ORDER BY telegram_id FOR SHARE', (tenant,) if everyone else (tenant, actor))
    members = cur.fetchall()
    workflow = _read_tenant_workflow(cur, tenant)
    profiles = {int(row['telegram_id']): {**dict(row), 'tenant_id': tenant,
        'permissions': row['permissions'] if isinstance(row['permissions'], list) else [],
        'modules': company.get('modules') or {}, 'onboarding': company.get('onboarding') or {},
        'workflow': workflow} for row in members}
    profile = profiles.get(actor)
    if not profile or profile.get('active') is not True:
        raise TaskFlowError('task_forbidden', 403)
    return profile, profiles


def _attempt(profile, operation, *, everyone=False):
    tenant, actor = _uuid(profile.get('tenant_id')), int(_positive(profile.get('telegram_id')))
    for attempt in range(2):
        try:
            with _connect() as conn:
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                _require_schema(conn)
                with conn.cursor() as cur:
                    current, profiles = _profiles(cur, tenant, actor, everyone=everyone)
                    return operation(cur, current, profiles)
        except SerializationFailure:
            if attempt:
                raise TaskFlowError('task_unavailable', 503) from None


def current_profile(profile):
    return _attempt(profile, lambda _cur, current, _profiles: current)


def execution_profiles(profile, executor_id=None):
    """Capture only the actor/executor authority used by one external command."""
    def operation(_cur, current, profiles):
        policy = TenantPolicy(current)
        if not policy.allows('tasks'):
            raise TaskFlowError('task_forbidden', 403)
        executor = int(_positive(executor_id or current['telegram_id']))
        selected = profiles.get(executor)
        if (not selected or not TenantPolicy(selected).allows('tasks') or
                (not policy.privileged and executor != int(current['telegram_id']))):
            raise TaskFlowError('task_forbidden', 403)
        return current, selected
    return _attempt(profile, operation, everyone=True)


def authority_key(profile):
    return json.dumps({key: profile.get(key) for key in (
        'tenant_id', 'telegram_id', 'role', 'permissions', 'active', 'modules', 'onboarding', 'workflow')},
        sort_keys=True, default=str)


def _sources(policy):
    native = policy.allows_action('tasks', 'view')
    external = policy.allows('tasks')
    return [dict(source='native', view=native,
        create=policy.allows_action('tasks', 'create'), update=policy.allows_action('tasks', 'update'),
        complete=policy.allows_action('tasks', 'update'), archive=policy.allows_action('tasks', 'delete'), sync=False),
        dict(source='kommo', view=external, create=external, update=False,
            complete=external, archive=False, sync=external and policy.privileged)]


def _native_where(profile):
    policy = TenantPolicy(profile)
    clause = 't.tenant_id=%s::uuid AND t.archived_at IS NULL'
    args = [profile['tenant_id']]
    if not policy.privileged:
        clause += ' AND (t.responsible_id=%s OR t.created_by=%s)'
        args.extend([profile['telegram_id'], profile['telegram_id']])
        if profile.get('role') == 'manager':
            parts = []
            for item in policy.pipeline_scope():
                part = 'd.pipeline_id=%s'; args.append(str(item['pipeline_id']))
                if item['status_ids']:
                    part += ' AND d.stage_id=ANY(%s::text[])'
                    args.append([str(value) for value in item['status_ids']])
                parts.append('('+part+')')
            clause += ' AND ('+' OR '.join(parts)+')' if parts else ' AND FALSE'
    return clause, args


def _rows(cur, profile, source, *, task_id=None, request_id=None, limit=None):
    if source == 'native':
        where, args = _native_where(profile)
        if task_id:
            where += ' AND t.id=%s::uuid'; args.append(_uuid(task_id))
        if request_id:
            where += ' AND t.idempotency_key=%s AND t.created_by=%s'
            args.extend(['manual:'+request_id, profile['telegram_id']])
        sql = '''SELECT t.id,t.deal_id,d.title AS deal_title,t.contact_id,c.display_name AS contact_name,
          t.channel_id,t.conversation_id,t.text,t.due_at,t.responsible_id,m.display_name AS responsible_name,
          t.status,t.completed_at,t.created_by,t.created_at,t.updated_at,d.pipeline_id,d.stage_id
          FROM saas_crm_native_tasks t
          JOIN saas_crm_native_deals d ON d.tenant_id=t.tenant_id AND d.id=t.deal_id
          LEFT JOIN saas_crm_native_contacts c ON c.tenant_id=t.tenant_id AND c.id=t.contact_id
          LEFT JOIN saas_tenant_members m ON m.tenant_id=t.tenant_id AND m.telegram_id=t.responsible_id
          WHERE '''+where+' ORDER BY (t.status=\'open\') DESC,t.due_at ASC NULLS LAST,t.created_at DESC,t.id'
    else:
        where, args = task_filters(tenant_id=profile['tenant_id'], scope=TenantPolicy(profile).task_scope())
        if task_id:
            where += ' AND kommo_task_id=%s'; args.append(_positive(task_id))
        sql = '''SELECT kommo_task_id,kommo_lead_id,text,due_at,responsible_id,completed,synced_at
          FROM saas_crm_tasks WHERE '''+where+' ORDER BY completed,due_at ASC NULLS LAST,synced_at DESC,kommo_task_id'
    if limit is not None:
        sql += ' LIMIT %s'; args.append(limit)
    cur.execute(sql, args)
    result = []
    for row in cur.fetchall():
        row = dict(row)
        if source == 'kommo':
            row.update(id=str(row['kommo_task_id']), status='completed' if row['completed'] else 'open')
            row['kommo_task_id'] = str(row['kommo_task_id'])
            row['kommo_lead_id'] = str(row['kommo_lead_id']) if row.get('kommo_lead_id') else None
        row['source'] = source
        result.append(public(row))
    return result


def _command(cur, profile, source, request_id):
    result = dict(request_id=request_id, source=source, state='not_confirmed', task_id=None,
                  task=None, cache_state='not_applicable')
    if source == 'native':
        tasks = _rows(cur, profile, source, request_id=request_id)
        if tasks:
            result.update(state='created', task_id=tasks[0]['id'], task=tasks[0], cache_state='available')
        return result
    cur.execute('''SELECT state FROM saas_task_commands
      WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid''',
      (profile['tenant_id'], profile['telegram_id'], request_id))
    row = cur.fetchone()
    if not row:
        return result
    state = row.get('state') or {}
    # Completion commands are a separate existing operation, not task creation.
    if not isinstance(state, dict) or state.get('completion_input'):
        return result
    step = state.get('step')
    if step == 'rejected':
        result['state'] = 'rejected'
    elif step == 'waiting_approval':
        result['state'] = 'approval_pending'
    elif step == 'done':
        saved = state.get('result') or {}
        try:
            task_id = _positive(saved.get('task_id'))
        except TaskFlowError:
            result['state'] = 'unknown'
            return result
        tasks = _rows(cur, profile, source, task_id=task_id)
        result.update(state='created', task_id=task_id, task=tasks[0] if tasks else None,
                      cache_state='available' if tasks else 'unavailable')
    else:
        result['state'] = 'unknown'
    return result


def read(profile, *, source=None, task_id=None, request_id=None):
    if source not in (None, 'native', 'kommo') or (task_id and request_id):
        raise TaskFlowError('task_invalid_query')
    if request_id:
        request_id = _uuid(request_id)
    def operation(cur, current, _profiles):
        policy = TenantPolicy(current)
        sources = _sources(policy)
        selected = source or ('native' if sources[0]['view'] else 'kommo')
        if not next(item for item in sources if item['source'] == selected)['view']:
            raise TaskFlowError('task_forbidden', 403)
        scope = ('tenant' if policy.privileged else 'personal') if selected == 'native' else (
            'tenant' if policy.task_scope()['kind'] == 'all' else policy.task_scope()['kind'])
        result = dict(source=selected, sources=sources, scope=scope, limit=100 if selected == 'native' else 200)
        if request_id:
            result['command'] = _command(cur, current, selected, request_id)
        elif task_id:
            rows = _rows(cur, current, selected, task_id=task_id)
            if not rows:
                raise TaskFlowError('task_not_found', 404)
            result['task'] = rows[0]
        else:
            rows = _rows(cur, current, selected, limit=result['limit']+1)
            result.update(tasks=rows[:result['limit']], shown=min(len(rows), result['limit']),
                          has_more=len(rows)>result['limit'], state='ready' if rows else 'empty')
        return result
    return _attempt(profile, operation)


def external_options(profile):
    def operation(cur, current, profiles):
        policy = TenantPolicy(current)
        if not policy.allows('tasks'):
            raise TaskFlowError('task_forbidden', 403)
        people = profiles.values() if policy.privileged else [current]
        executors = []
        for person in people:
            if not TenantPolicy(person).allows('tasks'):
                continue
            personal = TenantPolicy({**person, 'role': 'manager'}).pipeline_scope()
            names = {str(item['pipeline_id']): item.get('name') or ''
                     for item in current['workflow'].get('pipelines', [])}
            executors.append(dict(id=str(person['telegram_id']), name=person.get('display_name') or 'Employee',
                role=person['role'], pipelines=[{**item, 'name': names.get(str(item['pipeline_id']), '')}
                  for item in personal] if person['role'] in {'manager', 'owner', 'admin'} else []))
        parts, args = [], [current['tenant_id']]
        for item in policy.pipeline_scope():
            part = 'pipeline_id=%s'; args.append(item['pipeline_id'])
            if item['status_ids']:
                part += ' AND status_id=ANY(%s::bigint[])'; args.append(item['status_ids'])
            parts.append('('+part+')')
        cur.execute('''SELECT kommo_lead_id::text AS id,name,pipeline_id
          FROM saas_crm_deals WHERE tenant_id=%s::uuid AND deleted_at IS NULL AND ('''+
          (' OR '.join(parts) or 'FALSE')+') ORDER BY synced_at DESC,kommo_lead_id LIMIT 200', args)
        return dict(executors=public(executors), deals=public(cur.fetchall()))
    return _attempt(profile, operation, everyone=True)
