"""Durable, one-shot Kommo board writes. Never replay an uncertain PATCH."""
import asyncio
import hashlib
import json
from dataclasses import asdict
from tenant_task_commands import TaskCommandStore
from tenant_task_flow import TaskFlowError, _profiles, _require_schema
from tenant_crm_relation_identity import current_binding


def execute_sync(profile, payload, request, *, operation, path, prepare, verified, verify_only=False, authorize_cached=None, cache_confirmed=None):
    tenant, actor = str(profile['tenant_id']), int(profile['telegram_id'])
    fingerprint = hashlib.sha256(json.dumps({'operation': operation, **payload}, sort_keys=True).encode()).hexdigest()
    store = TaskCommandStore(tenant, actor, payload['request_id'], fingerprint)
    entity = path.split('/')[0]
    ident = path.split('/')[1]
    try:
        if entity == 'tasks': store.lock_task(int(ident))
        else: store.lock_deal(int(ident))
        if store.state.get('step') == 'rejected':
            exc=TaskFlowError(store.state.get('code', 'board_conflict'),409); exc.keep_request=False
            raise exc
        # The durable checkpoint precedes network I/O. An interrupted attempt is
        # read-verified, never sent twice. Read/profile locks are reacquired below.
        unknown = store.state.get('step') in {'board_sending','done'}
        done = store.state.get('step') == 'done'
        if verify_only and not unknown: raise TaskFlowError('board_not_found',404)
        if not unknown:
            with store.conn.cursor() as cur:
                binding = current_binding(cur, tenant)
                if not binding: raise TaskFlowError('board_connection_unavailable',409)
                _profiles(cur, tenant, actor)
            store.conn.commit()
            store.save({'step':'board_sending','board_entity':entity,'board_id':ident,
                'board_operation':operation,'board_payload':payload,'board_binding':asdict(binding)})
        patched = False
        try:
            with store.conn.cursor() as cur:
                _require_schema(store.conn)
                binding = current_binding(cur, tenant)  # tenant lock first
                if not binding: raise TaskFlowError('board_connection_unavailable',409)
                if asdict(binding) != store.state.get('board_binding'): raise TaskFlowError('board_connection_changed',409)
                current, _ = _profiles(cur, tenant, actor)
                if authorize_cached: authorize_cached(cur,current,payload)
                from tenant_kommo_board_provenance import require as require_source
                current_revision=require_source(cur,binding,entity,ident,store.state.get('confirmed_source_revision') if done else payload.get('source_revision'),allow_latest=unknown)
                cur.execute('''SELECT request_id FROM saas_task_commands WHERE tenant_id=%s::uuid
                  AND state->>'board_entity'=%s AND state->>'board_id'=%s
                  AND state->>'step'='board_sending' AND state->'board_binding'=%s::jsonb
                  AND NOT (actor_id=%s AND request_id=%s::uuid) LIMIT 1''',
                  (tenant,entity,ident,json.dumps(asdict(binding)),actor,payload['request_id']))
                if cur.fetchone(): raise TaskFlowError('board_busy',409)
                remote = request(binding, 'GET', path)
                # prepare always reauthorizes; unknown verification may ignore
                # expected scalar, but must never bypass scope/identity checks.
                body = prepare(cur, current, binding, remote, payload, unknown, request)
                if done:
                    try: result = verified(remote,payload)
                    except TaskFlowError: raise TaskFlowError('board_changed_since_confirmation',409) from None
                elif unknown:
                    result = verified(remote, payload)
                else:
                    patched = True
                    request(binding, 'PATCH', path, json_body=body)
                    result = verified(request(binding, 'GET', path), payload)
                result = {**result, 'request_id':payload['request_id'], 'state':'confirmed'}
                if cache_confirmed and not done:
                    cache_confirmed(cur,current,payload,result)
                    from tenant_kommo_board_provenance import refresh_after_command
                    result['source_revision']=refresh_after_command(cur,binding,entity,ident)
                else: result['source_revision']=current_revision
            # Cache, refreshed provenance and receipt commit together.
            store.save({**store.state,'step':'done','result':result,'confirmed_source_revision':result.get('source_revision') or payload.get('source_revision')})
            return result
        except TaskFlowError as exc:
            store.conn.rollback()
            if not patched and not unknown:
                store.save({**store.state,'step':'rejected','code':exc.code})
                exc.keep_request=False
                raise
            if exc.status in (403,404) or exc.code in {'board_connection_changed','board_source_sync_required','board_changed_since_confirmation'}:
                exc.keep_request=not done and exc.code!='board_connection_changed'
                raise
            error=TaskFlowError('board_outcome_unknown',409); error.keep_request=True
            raise error from exc
        except Exception as exc:
            store.conn.rollback()
            error=TaskFlowError('board_outcome_unknown',409); error.keep_request=True
            raise error from exc
    finally:
        store.close()


async def execute(profile, payload, bound_request, **kwargs):
    loop = asyncio.get_running_loop()
    def request(binding, method, path, **options):
        return asyncio.run_coroutine_threadsafe(bound_request(binding,method,path,**options),loop).result()
    return await asyncio.to_thread(execute_sync, profile, payload, request, **kwargs)


def read_payload(profile, request_id, operation):
    from tenant_platform import _connect
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT state FROM saas_task_commands WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid",(profile['tenant_id'],profile['telegram_id'],request_id))
            row=cur.fetchone()
    state=(row or {}).get('state') or {}
    if state.get('board_operation')!=operation or not isinstance(state.get('board_payload'),dict):
        raise TaskFlowError('board_not_found',404)
    return state['board_payload']
