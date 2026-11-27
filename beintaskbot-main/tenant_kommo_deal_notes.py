"""Bounded read-only plain-text notes for a currently authorized Kommo lead."""
import asyncio
from aiohttp import web
from tenant_task_flow import TaskFlowError, _positive, _profiles, query
from tenant_platform_core import _connect
from tenant_policy import TenantPolicy
from tenant_crm_relation_identity import current_binding
from tenant_kommo_board_provenance import require as require_source


def sanitize_notes(payload, lead_id, *, include_events=False):
    rows = (payload.get('_embedded') or {}).get('notes') or []
    if not isinstance(rows, list):
        raise TaskFlowError('kommo_notes_unavailable', 503)
    result, calls = [], []
    for row in rows[:250]:
        if not isinstance(row, dict) or str(row.get('entity_id')) != str(lead_id):
            raise TaskFlowError('kommo_notes_identity_mismatch', 409)
        if include_events and row.get('note_type') in ('call_in', 'call_out'):
            calls.append({'id': str(row.get('id') or ''), 'type': row['note_type'],
                          'created_at': row.get('created_at')})
        if row.get('note_type') != 'common':
            continue  # calls, files and service events are not editable text notes
        params = row.get('params') or {}
        if not isinstance(params, dict) or not isinstance(params.get('text'), str):
            continue
        result.append({'id': str(row['id']), 'text': params['text'][:10000],
                       'created_at': row.get('created_at'), 'updated_at': row.get('updated_at')})
    response = {'notes': result, 'has_more': bool((payload.get('_links') or {}).get('next')) or len(rows)>250}
    if include_events:
        response['calls'] = calls
    return response


def sanitize_stage_events(payload, lead_id):
    rows = (payload.get('_embedded') or {}).get('events') or []
    if not isinstance(rows, list):
        raise TaskFlowError('kommo_deal_activity_unavailable', 503)
    result = []
    for row in rows[:100]:
        if (not isinstance(row, dict) or row.get('entity_type') != 'lead'
                or str(row.get('entity_id')) != lead_id or row.get('type') != 'lead_status_changed'):
            raise TaskFlowError('kommo_deal_activity_identity_mismatch', 409)
        changes = row.get('value_after') or []
        change = changes[0].get('lead_status') if isinstance(changes, list) and changes and isinstance(changes[0], dict) else None
        if isinstance(change, dict):
            result.append({'id': str(row.get('id') or ''), 'created_at': row.get('created_at'),
                           'stage_id': str(change.get('id') or ''), 'pipeline_id': str(change.get('pipeline_id') or '')})
    return result, bool((payload.get('_links') or {}).get('next'))


def read_sync(profile, lead_id, request, *, include_events=False):
    tenant, actor = str(profile['tenant_id']), int(profile['telegram_id'])
    with _connect() as conn:
        with conn.cursor() as cur:
            binding = current_binding(cur, tenant)
            if not binding:
                raise TaskFlowError('board_connection_unavailable', 409)
            current, _ = _profiles(cur, tenant, actor)
            from tenant_member_access import page_capabilities
            if not page_capabilities(current).get('deals', False):
                raise TaskFlowError('task_forbidden', 403)
            policy = TenantPolicy(current)
            cur.execute('SELECT * FROM saas_crm_deals WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND deleted_at IS NULL', (tenant, int(lead_id)))
            cached = cur.fetchone()
            if not policy.can_read_kommo_chat(cached):
                raise TaskFlowError('task_forbidden', 403)
            require_source(cur, binding, 'leads', lead_id, None, allow_latest=True)
            remote = request(binding, 'GET', 'leads/' + lead_id)
            if (str(remote.get('id')) != lead_id or remote.get('is_deleted') or not
                    policy.can_read_kommo_chat({**remote, 'tenant_id': tenant, 'raw': remote})):
                raise TaskFlowError('task_forbidden', 403)
            result = sanitize_notes(request(binding, 'GET', 'leads/' + lead_id + '/notes'), lead_id,
                                    include_events=include_events)
            if include_events:
                try:
                    payload = request(binding, 'GET', 'events', params={
                        'filter[entity]': 'lead', 'filter[entity_id]': lead_id,
                        'filter[type]': 'lead_status_changed', 'limit': 100})
                    result['stages'], result['stages_has_more'] = sanitize_stage_events(payload, lead_id)
                except Exception:
                    result['stages_unavailable'] = True
            from tenant_kommo_note_create import can_create
            result['can_create'] = can_create(current, cached) and can_create(current, {**remote, 'tenant_id': tenant, 'raw': remote})
            return result


def handler(*, member, bound_request, logger, origin=None):
    async def handle(request):
        if request.method == 'POST' or 'request_id' in request.rel_url.query:
            from tenant_kommo_note_create import handler as create_handler
            if not origin:
                raise web.HTTPForbidden()
            return await create_handler(member=member, bound_request=bound_request, logger=logger, origin=origin)(request)
        try:
            profile = member(request)
            if not profile:
                raise TaskFlowError('task_forbidden', 403)
            query(request, profile, required=True)
            optional = {'include_events'} if request.rel_url.query.getall('include_events', []) == ['1'] else set()
            if set(request.rel_url.query) != {'lead_id', 'expected_tenant_id', 'expected_user_id'} | optional or len(request.rel_url.query.getall('lead_id', [])) != 1:
                raise TaskFlowError('task_invalid_query')
            lead_id = _positive(request.rel_url.query['lead_id'])
            loop = asyncio.get_running_loop()
            def provider(binding, method, path, **kwargs):
                return asyncio.run_coroutine_threadsafe(bound_request(binding, method, path, **kwargs), loop).result()
            result = await asyncio.to_thread(read_sync, profile, lead_id, provider, include_events=bool(optional))
            return web.json_response({'success': True, **result}, headers={'Cache-Control': 'no-store'})
        except TaskFlowError as exc:
            return web.json_response({'success': False, 'code': exc.code, 'error': exc.code}, status=exc.status, headers={'Cache-Control': 'no-store'})
        except Exception:
            logger.exception('Kommo notes read unavailable')
            return web.json_response({'success': False, 'code': 'kommo_notes_unavailable'}, status=503, headers={'Cache-Control': 'no-store'})
    return handle
