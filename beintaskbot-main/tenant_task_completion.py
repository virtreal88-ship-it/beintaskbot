"""Tenant-scoped task completion, independent of deal stages and finances."""
import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from tenant_policy import TenantPolicy, positive_id
from tenant_platform import TenantPlatformError, get_crm_task, get_crm_deal, upsert_crm_tasks, append_audit_event
from tenant_task_commands import TaskCommandStore, TaskCommandPending
from tenant_tasks import in_scope, KommoRequest


def normalize_completion_input(data: dict) -> dict:
    try:
        request_id = str(uuid.UUID(str(data.get('request_id', ''))))
    except (ValueError, TypeError) as exc:
        raise TenantPlatformError('Sorğu kodu düzgün deyil.') from exc
    task_id = positive_id(data.get('task_id'))
    result = str(data.get('result_text') or '').strip()
    if not 0 < task_id < 2**63 or not result or len(result) > 3500:
        raise TenantPlatformError('Tapşırıq və nəticə mətni tələb olunur (maksimum 3500 simvol).')
    return {'operation': 'complete', 'request_id': request_id, 'task_id': task_id, 'result_text': result}


async def authorize_task(profile: dict, task: dict, request: KommoRequest, *, live: bool) -> None:
    """Never use the shared Kommo responsible user as employee authority."""
    policy = TenantPolicy(profile)
    if not policy.allows('tasks'):
        raise TenantPlatformError('Tapşırıqlar üçün icazəniz yoxdur.')
    scope = policy.task_scope()
    if scope['kind'] == 'all':
        return
    if scope['kind'] == 'marker' and scope['marker'] in str(task.get('text') or ''):
        return
    if scope['kind'] == 'pipelines':
        lead_id = (positive_id(task.get('entity_id')) if task.get('entity_type') in ('leads', 2, '2') else 0) if live else positive_id(task.get('kommo_lead_id'))
        if lead_id:
            lead = (await request(str(profile['tenant_id']), 'GET', f'leads/{lead_id}') if live else
                    await asyncio.to_thread(get_crm_deal, tenant_id=str(profile['tenant_id']), kommo_lead_id=lead_id))
            if lead and in_scope(policy, lead) and (not live or positive_id(lead.get('id')) == lead_id):
                return
    raise TenantPlatformError('Bu tapşırıq üçün icazəniz yoxdur.')


async def complete_task(profile: dict, data: dict, request: KommoRequest, *, reviewer: dict | None = None) -> dict:
    policy = TenantPolicy(profile)
    if not policy.allows('tasks'):
        raise TenantPlatformError('Tapşırıqlar üçün icazəniz yoxdur.')
    if reviewer is not None:
        authority = TenantPolicy(reviewer)
        if (str(reviewer.get('tenant_id')) != str(profile.get('tenant_id'))
                or not authority.privileged or not authority.allows('tasks')):
            raise TenantPlatformError('Təsdiq üçün icazəniz yoxdur.')
    payload = normalize_completion_input(data)
    tenant, actor_id = str(profile['tenant_id']), int(profile['telegram_id'])
    cached = await asyncio.to_thread(get_crm_task, tenant_id=tenant, kommo_task_id=payload['task_id'])
    if not cached or str(cached.get('tenant_id')) != tenant:
        raise TenantPlatformError('Tapşırıq tapılmadı. Siyahını yeniləyin.')
    await authorize_task(profile, cached, request, live=False)
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    store = await asyncio.to_thread(TaskCommandStore, tenant, actor_id, payload['request_id'], fingerprint)
    try:
        await asyncio.to_thread(store.lock_task, payload['task_id'])
        if store.state.get('step') == 'done':
            return store.state['result']
        if store.state.get('step') == 'rejected':
            raise TenantPlatformError('Tamamlama sorğusu administrator tərəfindən rədd edilib.')
        if store.state.get('step') == 'task_completing':
            raise TaskCommandPending('Kommo nəticəsini administrator yoxlamalıdır. Sorğu: ' + payload['request_id'])
        if reviewer is not None and not store.state.get('completion_input'):
            raise TenantPlatformError('Tamamlama təsdiqi tapılmadı.')
        live_task = await request(tenant, 'GET', f"tasks/{payload['task_id']}")
        if positive_id(live_task.get('id')) != payload['task_id']:
            raise TenantPlatformError('Kommo tapşırığı tapılmadı.')
        if live_task.get('entity_type') in ('leads', 2, '2') and positive_id(live_task.get('entity_id')):
            await asyncio.to_thread(store.lock_deal, positive_id(live_task['entity_id']))
        await authorize_task(profile, live_task, request, live=True)
        if live_task.get('is_completed'):
            result = {'task_id': payload['task_id'], 'completed': True, 'already_completed': True}
        else:
            await asyncio.to_thread(store.check_task_completion, payload['task_id'])
            requires_review = not policy.privileged and policy.requires_task_approval(
                creator_id=actor_id, executor_id=actor_id, completion=True)
            if reviewer is None and (requires_review or store.state.get('step') == 'waiting_approval'):
                if not store.state:
                    await asyncio.to_thread(store.save, {'step': 'waiting_approval',
                        'approval': {'status': 'pending', 'kind': 'completion'},
                        'completion_input': {**payload, 'text': str(cached.get('text') or ''),
                                             'executor_id': actor_id, 'lead_id': positive_id(cached.get('kommo_lead_id'))}})
                return {'approval_pending': True, 'request_id': payload['request_id'],
                        'warning': 'Tamamlama administrator təsdiqinə göndərildi.'}
            state = {'step': 'task_completing', 'completion_input': {**payload,
                     'text': str(cached.get('text') or ''), 'executor_id': actor_id,
                     'lead_id': positive_id(cached.get('kommo_lead_id'))}}
            if reviewer is not None:
                state['approval'] = {'status': 'approved', 'kind': 'completion', 'reviewer_id': int(reviewer['telegram_id'])}
            else:
                # If the provider response is lost, administrators can inspect
                # this checkpoint in the same queue without blindly replaying it.
                state['approval'] = {'status': 'not_required', 'kind': 'completion'}
            await asyncio.to_thread(store.save, state)
            response = await request(tenant, 'PATCH', f"tasks/{payload['task_id']}",
                                     json_body={'is_completed': True, 'result': {'text': payload['result_text']}})
            if positive_id(response.get('id')) != payload['task_id']:
                raise TaskCommandPending('Kommo nəticəsini administrator yoxlamalıdır. Sorğu: ' + payload['request_id'])
            live_task = {**live_task, **response, 'is_completed': True, 'result': {'text': payload['result_text']}}
            result = {'task_id': payload['task_id'], 'completed': True}
        final_state = {'step': 'done', 'result': result}
        if reviewer is not None:
            final_state['approval'] = {'status': 'approved', 'kind': 'completion', 'reviewer_id': int(reviewer['telegram_id'])}
        await asyncio.to_thread(store.save, final_state)
        due = positive_id(live_task.get('complete_till'))
        await asyncio.to_thread(upsert_crm_tasks, tenant_id=tenant, tasks=[{
            'kommo_task_id': payload['task_id'],
            'kommo_lead_id': positive_id(live_task.get('entity_id')) if live_task.get('entity_type') in ('leads', 2, '2') else 0,
            'text': str(live_task.get('text') or ''), 'completed': True,
            'due_at': datetime.fromtimestamp(due, timezone.utc).isoformat() if due else None,
            'responsible_id': positive_id(live_task.get('responsible_user_id')), 'raw': live_task}])
        await asyncio.to_thread(append_audit_event, tenant_id=tenant, actor_telegram_id=actor_id,
            action='task_completed', entity_type='kommo_task', entity_id=str(payload['task_id']),
            payload={**result, 'request_id': payload['request_id'],
                     'reviewer_id': int(reviewer['telegram_id']) if reviewer else None})
        return result
    except Exception as exc:
        if store.state.get('step') == 'done':
            logging.getLogger(__name__).exception('Task completed but cache/audit failed: tenant=%s request=%s', tenant, payload['request_id'])
            return {**store.state['result'], 'warning': 'Tapşırıq tamamlandı. Siyahını yeniləyin.'}
        if store.state.get('step') == 'task_completing':
            raise TaskCommandPending('Kommo nəticəsini yoxlayın. Təkrar tamamlamayın. Sorğu: ' + payload['request_id']) from exc
        raise
    finally:
        await asyncio.to_thread(store.close)
