"""Text-only task edits. Ownership markers and all relations remain immutable."""
import re
from tenant_task_flow import TaskFlowError, _uuid, _positive
from tenant_kommo_task_reschedule import authorize_cached
from tenant_kommo_task_reschedule import prepare as authorize_live

OPERATION = 'kommo_task_text_update'
MAX_TEXT = 4000
MARKER = re.compile(r'\[CRM:[^\]]*\]')


def normalize(data):
    fields = {'request_id', 'task_id', 'source_revision', 'expected_text', 'text'}
    if not isinstance(data, dict) or set(data) != fields:
        raise TaskFlowError('board_invalid_request')
    revision = data['source_revision']
    if not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{64}', revision):
        raise TaskFlowError('board_source_sync_required', 409)
    for key in ('expected_text', 'text'):
        if not isinstance(data[key], str) or len(data[key]) > MAX_TEXT or '\x00' in data[key]:
            raise TaskFlowError('task_invalid_text')
    # Markers are authorization metadata embedded in provider text. Never allow
    # an editor to grant another actor visibility or remove an existing owner.
    if '[CRM:' in data['text']:
        raise TaskFlowError('task_marker_immutable', 409)
    markers = MARKER.findall(data['expected_text'])
    full_text = data['text'] + (('\n' if data['text'] else '') + '\n'.join(markers) if markers else '')
    if len(full_text) > MAX_TEXT:
        raise TaskFlowError('task_invalid_text')
    return {**data, 'visible_text': data['text'], 'text': full_text, 'request_id': _uuid(data['request_id']), 'task_id': _positive(data['task_id'])}


def prepare(cur, profile, binding, remote, payload, unknown, request):
    # Reuse current cached + live provider scope checks, NOT native CRUD rights.
    # unknown=True bypasses only deadline CAS in the deadline helper.
    authorize_live(cur, profile, binding, remote,
                   {'task_id': payload['task_id'], 'due_at': 1}, True, request)
    if not unknown:
        if remote.get('is_completed'):
            raise TaskFlowError('board_task_completed', 409)
        if str(remote.get('text') or '') != payload['expected_text']:
            raise TaskFlowError('board_conflict', 409)
    return {'text': payload['text']}


def verified(remote, payload):
    if str(remote.get('id')) != payload['task_id'] or str(remote.get('text') or '') != payload['text']:
        raise TaskFlowError('board_outcome_unknown', 409)
    return {'task_id': payload['task_id'], 'source': 'kommo', 'text': payload['visible_text'], 'task_text': payload['text']}


def cache_confirmed(cur, profile, payload, result):
    cur.execute("""UPDATE saas_crm_tasks SET text=%s,synced_at=clock_timestamp(),
      raw=jsonb_set(COALESCE(raw,'{}'::jsonb),'{text}',to_jsonb(%s::text))
      WHERE tenant_id=%s::uuid AND kommo_task_id=%s
      AND COALESCE(raw->>'text','') IN (%s,%s)""",
      (result['task_text'], result['task_text'], profile['tenant_id'], int(payload['task_id']),
       payload['expected_text'], payload['text']))


def handlers(*, member, bound_request, origin, logger):
    import asyncio
    from aiohttp import web
    from tenant_task_flow import query
    from tenant_kommo_board_commands import execute, read_payload
    async def handler(request):
        try:
            profile = member(request)
            if not profile:
                raise TaskFlowError('task_forbidden', 403)
            params = query(request, profile, required=True)
            checking = request.method == 'GET'
            if checking:
                if not params.get('request_id'):
                    raise TaskFlowError('board_invalid_request')
                payload = await asyncio.to_thread(read_payload, profile, params['request_id'], OPERATION)
            else:
                if request.content_type != 'application/json' or request.headers.get('Origin', '').rstrip('/') != origin.rstrip('/'):
                    raise TaskFlowError('task_forbidden', 403)
                payload = normalize(await request.json())
            result = await execute(profile, payload, bound_request, operation=OPERATION,
                path='tasks/' + payload['task_id'], prepare=prepare, verified=verified,
                verify_only=checking, authorize_cached=authorize_cached, cache_confirmed=cache_confirmed)
            return web.json_response({'success': True, **result}, headers={'Cache-Control': 'no-store'})
        except TaskFlowError as exc:
            return web.json_response({'success': False, 'code': exc.code, 'error': exc.code,
                'keep_request': getattr(exc, 'keep_request', False),
                'retry_same_request': getattr(exc, 'keep_request', False)}, status=exc.status,
                headers={'Cache-Control': 'no-store'})
        except Exception:
            logger.exception('Kommo text command unavailable')
            return web.json_response({'success': False, 'code': 'board_outcome_unknown',
                'error': 'board_outcome_unknown', 'keep_request': True, 'retry_same_request': True},
                status=503, headers={'Cache-Control': 'no-store'})
    return handler
