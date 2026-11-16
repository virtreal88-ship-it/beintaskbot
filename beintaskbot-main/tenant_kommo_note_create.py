"""One-shot tenant-bound common note creation; ambiguous POSTs are never replayed."""
import asyncio
import hashlib
import json
from dataclasses import asdict
from aiohttp import web
from tenant_task_flow import TaskFlowError, _positive, _uuid, _profiles, query
from tenant_task_commands import TaskCommandStore
from tenant_crm_relation_identity import current_binding
from tenant_kommo_board_provenance import require as require_source
from tenant_kommo_deal_move import stage_choices
from tenant_policy import TenantPolicy

OPERATION = 'kommo_deal_note_create'


def normalize(data):
    if not isinstance(data, dict) or set(data) != {'request_id', 'lead_id', 'text'}:
        raise TaskFlowError('board_invalid_request')
    text = data['text']
    if not isinstance(text, str) or not text.strip() or len(text) > 10000 or '\x00' in text:
        raise TaskFlowError('note_invalid_text')
    return {'request_id': _uuid(data['request_id']), 'lead_id': _positive(data['lead_id']), 'text': text.strip()}


def can_create(profile, deal):
    try:
        return bool(deal and TenantPolicy(profile).can_read_kommo_chat(deal)
            and {'pipeline_id': int(deal['pipeline_id']), 'stage_id': int(deal['status_id'])}
            in stage_choices(profile, target=False))
    except (KeyError, TypeError, ValueError):
        return False


def authorize(cur, profile, binding, payload, provider):
    lead = payload['lead_id']
    cur.execute('SELECT * FROM saas_crm_deals WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND deleted_at IS NULL', (profile['tenant_id'], int(lead)))
    cached = cur.fetchone()
    if not can_create(profile, cached) or (cached.get('raw') or {}).get('source') == 'connect':
        raise TaskFlowError('task_forbidden', 403)
    require_source(cur, binding, 'leads', lead, None, allow_latest=True)
    remote = provider(binding, 'GET', 'leads/' + lead)
    if (str(remote.get('id')) != lead or remote.get('is_deleted') or
            not can_create(profile, {**remote, 'tenant_id': profile['tenant_id'], 'raw': remote})):
        raise TaskFlowError('task_forbidden', 403)


def note_receipt(response, payload):
    rows = (response.get('_embedded') or {}).get('notes') or []
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise TaskFlowError('note_outcome_unknown', 409)
    row = rows[0]
    if (str(row.get('entity_id')) != payload['lead_id'] or row.get('note_type') != 'common'
            or (row.get('params') or {}).get('text') != payload['text']):
        raise TaskFlowError('note_outcome_unknown', 409)
    return {'id': _positive(row.get('id')), 'text': payload['text'],
            'created_at': row.get('created_at'), 'updated_at': row.get('updated_at')}


def create_sync(profile, payload, provider, *, checking=False):
    fingerprint = hashlib.sha256(json.dumps({'operation': OPERATION, **payload}, sort_keys=True).encode()).hexdigest()
    store = TaskCommandStore(str(profile['tenant_id']), int(profile['telegram_id']), payload['request_id'], fingerprint)
    sent = False
    was_sending = store.state.get('step') == 'note_sending'
    try:
        store.lock_deal(int(payload['lead_id']))
        with store.conn.cursor() as cur:
            binding = current_binding(cur, profile['tenant_id'])
            if not binding:
                raise TaskFlowError('board_connection_unavailable', 409)
            current, _ = _profiles(cur, profile['tenant_id'], profile['telegram_id'])
            if store.state.get('board_binding') and store.state['board_binding'] != asdict(binding):
                raise TaskFlowError('board_connection_changed', 409)
            authorize(cur, current, binding, payload, provider)
        if store.state.get('step') == 'done':
            return store.state['result']
        if store.state.get('step') == 'rejected':
            raise TaskFlowError(store.state.get('code', 'note_rejected'), 409)
        if store.state.get('step') == 'note_sending':
            if store.state.get('provider_note_id'):
                remote = provider(binding, 'GET', 'leads/' + payload['lead_id'] + '/notes/' + store.state['provider_note_id'])
                note = note_receipt({'_embedded': {'notes': [remote]}}, payload)
                result = {'state': 'confirmed', 'request_id': payload['request_id'], 'lead_id': payload['lead_id'], 'note': note}
                store.save({**store.state, 'step': 'done', 'result': result})
                return result
            error = TaskFlowError('note_outcome_unknown', 409)
            error.keep_request = True
            raise error
        if checking:
            raise TaskFlowError('board_not_found', 404)
        store.save({'step': 'note_sending', 'board_operation': OPERATION, 'board_payload': payload,
                    'board_binding': asdict(binding)})
        # Reacquire tenant/profile locks after checkpoint commit and reauthorize.
        with store.conn.cursor() as cur:
            fresh = current_binding(cur, profile['tenant_id'])
            if fresh != binding:
                raise TaskFlowError('board_connection_changed', 409)
            current, _ = _profiles(cur, profile['tenant_id'], profile['telegram_id'])
            authorize(cur, current, binding, payload, provider)
            sent = True
            response = provider(binding, 'POST', 'leads/' + payload['lead_id'] + '/notes',
                                json_body=[{'note_type': 'common', 'params': {'text': payload['text']}}])
            rows = (response.get('_embedded') or {}).get('notes') or []
            if len(rows) != 1 or str(rows[0].get('entity_id')) != payload['lead_id']:
                raise TaskFlowError('note_outcome_unknown', 409)
            note_id = _positive(rows[0].get('id'))
            store.save({**store.state, 'provider_note_id': note_id})
            if current_binding(cur, profile['tenant_id']) != binding:
                raise TaskFlowError('board_connection_changed', 409)
            current, _ = _profiles(cur, profile['tenant_id'], profile['telegram_id'])
            authorize(cur, current, binding, payload, provider)
            remote = provider(binding, 'GET', 'leads/' + payload['lead_id'] + '/notes/' + note_id)
            note = note_receipt({'_embedded': {'notes': [remote]}}, payload)
        result = {'state': 'confirmed', 'request_id': payload['request_id'], 'lead_id': payload['lead_id'], 'note': note}
        store.save({**store.state, 'step': 'done', 'result': result})
        return result
    except Exception as exc:
        store.conn.rollback()
        uncertain = sent or was_sending
        if uncertain:
            error = TaskFlowError('note_outcome_unknown', 409)
            error.keep_request = True
            raise error from exc
        if store.state.get('step') not in {'done', 'rejected', 'note_sending'} or not sent and store.state.get('step') == 'note_sending':
            store.save({**store.state, 'step': 'rejected', 'code': getattr(exc, 'code', 'note_preflight_unavailable')})
        raise
    finally:
        store.close()


def handler(*, member, bound_request, origin, logger):
    async def handle(request):
        try:
            profile = member(request)
            if not profile:
                raise TaskFlowError('task_forbidden', 403)
            params = query(request, profile, required=True)
            checking = request.method == 'GET'
            if checking:
                from tenant_kommo_board_commands import read_payload
                if set(request.rel_url.query) != {'request_id', 'expected_tenant_id', 'expected_user_id'}:
                    raise TaskFlowError('board_invalid_request')
                payload = await asyncio.to_thread(read_payload, profile, _uuid(params.get('request_id')), OPERATION)
            else:
                if set(request.rel_url.query) != {'expected_tenant_id', 'expected_user_id'} or request.content_type != 'application/json' or request.headers.get('Origin', '').rstrip('/') != origin.rstrip('/'):
                    raise TaskFlowError('task_forbidden', 403)
                payload = normalize(await request.json())
            loop = asyncio.get_running_loop()
            def provider(binding, method, path, **kwargs):
                return asyncio.run_coroutine_threadsafe(bound_request(binding, method, path, **kwargs), loop).result()
            result = await asyncio.to_thread(create_sync, profile, payload, provider, checking=checking)
            return web.json_response({'success': True, **result}, headers={'Cache-Control': 'no-store'})
        except TaskFlowError as exc:
            return web.json_response({'success': False, 'code': exc.code, 'error': exc.code,
                'keep_request': getattr(exc, 'keep_request', False)}, status=exc.status, headers={'Cache-Control': 'no-store'})
        except Exception:
            logger.exception('Kommo note create unavailable')
            return web.json_response({'success': False, 'code': 'note_outcome_unknown', 'keep_request': True}, status=503, headers={'Cache-Control': 'no-store'})
    return handle
