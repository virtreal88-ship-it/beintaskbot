"""Tenant task creation orchestration; no employee names or paid Kommo seats."""
import asyncio
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable

from tenant_policy import TenantPolicy, positive_id
from tenant_platform import (TenantPlatformError, member, get_crm_deal, upsert_crm_deals,
                             upsert_crm_tasks, append_audit_event)
from tenant_task_commands import TaskCommandStore, TaskCommandPending

KommoRequest = Callable[..., Awaitable[dict]]


def normalize_task_input(data: dict, actor_id: int) -> dict:
    try:
        request_id = str(uuid.UUID(str(data.get('request_id', ''))))
        due = datetime.fromisoformat(str(data.get('due_at', '')).replace('Z', '+00:00'))
        if due.tzinfo is None:
            raise ValueError()
        deadline = int(due.timestamp())
    except (ValueError, TypeError, OverflowError):
        raise TenantPlatformError('Sorğu kodunu və tapşırığın tarixini yoxlayın.')
    text = str(data.get('text') or '').strip()
    if not text or len(text) > 3500 or '[CRM:' in text:
        raise TenantPlatformError('Tapşırığın mətnini yazın (maksimum 3500 simvol).')
    return {'request_id': request_id, 'text': text, 'complete_till': deadline,
            'executor_id': positive_id(data.get('executor_id')) or actor_id,
            'lead_id': positive_id(data.get('lead_id')), 'pipeline_id': positive_id(data.get('pipeline_id'))}


def in_scope(policy: TenantPolicy, lead: dict) -> bool:
    return any(positive_id(lead.get('pipeline_id')) == item['pipeline_id']
               and (not item['status_ids'] or positive_id(lead.get('status_id')) in item['status_ids'])
               for item in policy.pipeline_scope())


async def create_task(profile: dict, data: dict, request: KommoRequest, *, reviewer: dict | None = None) -> dict:
    actor = TenantPolicy(profile)
    if reviewer is not None:
        authority = TenantPolicy(reviewer)
        if str(reviewer.get('tenant_id')) != str(profile.get('tenant_id')) or not authority.privileged or not authority.allows('tasks'):
            raise TenantPlatformError('Təsdiq üçün icazəniz yoxdur.')
    if not actor.allows('tasks'):
        raise TenantPlatformError('Tapşırıqlar üçün icazəniz yoxdur.')
    payload = normalize_task_input(data, int(profile['telegram_id']))
    if not actor.privileged and payload['executor_id'] != int(profile['telegram_id']):
        raise TenantPlatformError('Yalnız özünüz üçün tapşırıq yarada bilərsiniz.')
    tenant = str(profile['tenant_id'])
    executor = await asyncio.to_thread(member, tenant, payload['executor_id'])
    if not executor or not TenantPolicy(executor).allows('tasks'):
        raise TenantPlatformError('İcraçı aktiv deyil və ya tapşırıq icazəsi yoxdur.')
    policy = TenantPolicy(executor)
    requires_approval = not actor.privileged and policy.requires_task_approval(creator_id=int(profile['telegram_id']), executor_id=payload['executor_id'])
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    store = await asyncio.to_thread(TaskCommandStore, tenant, int(profile['telegram_id']), payload['request_id'], fingerprint)
    try:
        if store.state.get('step') == 'done':
            return store.state['result']
        if store.state.get('step') == 'rejected':
            raise TenantPlatformError('Tapşırıq sorğusu administrator tərəfindən rədd edilib.')
        if store.state.get('step') in {'lead_creating', 'lead_moving', 'task_creating'}:
            raise TaskCommandPending('Kommo əməliyyatının nəticəsi yoxlanmalıdır. Təkrar yaratmayın; administratorla əlaqə saxlayın. Sorğu: ' + payload['request_id'])
        if payload['complete_till'] <= time.time():
            if store.state and store.state.get('step') != 'waiting_approval':
                raise TaskCommandPending('Əvvəlki əməliyyatı administrator yoxlamalıdır. Sorğu: ' + payload['request_id'])
            raise TenantPlatformError('Gələcək tarix və saat seçin.')
        if reviewer is not None and not store.state.get('approval'):
            raise TenantPlatformError('Təsdiq sorğusu tapılmadı.')
        if reviewer is None and (requires_approval or store.state.get('step') == 'waiting_approval'):
            if not store.state:
                await asyncio.to_thread(store.save, {'step': 'waiting_approval',
                    'approval': {'status': 'pending'}, 'task_input': {
                        'request_id': payload['request_id'], 'text': payload['text'],
                        'due_at': datetime.fromtimestamp(payload['complete_till'], timezone.utc).isoformat(),
                        'executor_id': payload['executor_id'], 'lead_id': payload['lead_id'],
                        'pipeline_id': payload['pipeline_id']}})
            if store.state.get('step') == 'waiting_approval':
                return {'approval_pending': True, 'request_id': payload['request_id'],
                        'warning': 'Tapşırıq administrator təsdiqinə göndərildi.'}
        lead_id = positive_id(store.state.get('lead_id')) or payload['lead_id']
        lead = None
        if lead_id:
            await asyncio.to_thread(store.lock_deal, lead_id)
            # A cache hit is not authorization: verify the live pipeline too.
            cached = await asyncio.to_thread(get_crm_deal, tenant_id=tenant, kommo_lead_id=lead_id)
            if not cached or not in_scope(actor, cached):
                raise TenantPlatformError('Bu sövdələşmə üçün icazəniz yoxdur.')
            lead = await request(tenant, 'GET', f'leads/{lead_id}')
            if positive_id(lead.get('id')) != lead_id or not in_scope(actor, lead):
                raise TenantPlatformError('Sövdələşmə artıq sizin vərəqinizdə deyil.')
        if lead and not actor.privileged and payload['pipeline_id'] and payload['pipeline_id'] != positive_id(lead.get('pipeline_id')):
            raise TenantPlatformError('Seçilmiş vərəq sövdələşmənin vərəqi ilə uyğun deyil.')
        route = policy.task_creation_route(has_deal=bool(lead_id), pipeline_id=payload['pipeline_id'] or (positive_id(lead.get('pipeline_id')) if lead and not actor.privileged else 0))
        if not actor.privileged and lead_id:
            if not in_scope(policy, lead):
                raise TenantPlatformError('Yalnız öz sövdələşmələrinizə tapşırıq yarada bilərsiniz.')
            route['action'] = 'keep_deal'  # Self-created task never resets stage.
        account = await request(tenant, 'GET', 'account')
        responsible = positive_id(account.get('current_user_id'))
        if not responsible:
            raise TenantPlatformError('Kommo administrator hesabı müəyyən edilmədi.')
        if route['action'] in {'create_deal', 'move_deal'}:
            # Validate config against live provider stages before mutating.
            pipelines = await request(tenant, 'GET', 'leads/pipelines')
            provider = next((p for p in (pipelines.get('_embedded') or {}).get('pipelines', []) if positive_id(p.get('id')) == route['pipeline_id']), None)
            stage = next((s for s in ((provider or {}).get('_embedded') or {}).get('statuses', []) if positive_id(s.get('id')) == route['status_id']), None)
            if stage and positive_id(stage.get('type')) == 1:
                # Incoming/unsorted is not a normal lead stage. Older configs
                # imported it as "open"; use the first configured normal one.
                normal = {positive_id(s.get('id')): s for s in (provider.get('_embedded') or {}).get('statuses', [])
                          if positive_id(s.get('type')) != 1 and positive_id(s.get('id')) not in {142, 143}}
                configured = sorted((s for s in policy.config.get('stages', [])
                                     if positive_id(s.get('pipeline_id')) == route['pipeline_id']
                                     and s.get('stage_type', 'open') == 'open'
                                     and (s.get('settings') or {}).get('visible', True)),
                                    key=lambda s: (s.get('sort_order', 0), positive_id(s.get('stage_id'))))
                stage = next((normal[positive_id(s.get('stage_id'))] for s in configured if positive_id(s.get('stage_id')) in normal), None)
                if stage:
                    route['status_id'] = positive_id(stage['id'])
            if not stage:
                raise TenantPlatformError('Vərəq və ya mərhələ Kommo-da tapılmadı. Ayarları yeniləyin.')
            if reviewer is not None and store.state.get('step') == 'waiting_approval':
                await asyncio.to_thread(store.save, {'step': 'approval_accepted',
                    'approval': {'status': 'approved', 'reviewer_id': int(reviewer['telegram_id'])}})
            body = {'pipeline_id': route['pipeline_id'], 'status_id': route['status_id']}
            if route['action'] == 'create_deal':
                await asyncio.to_thread(store.save, {'step': 'lead_creating'})
                response = await request(tenant, 'POST', 'leads', json_body=[{**body, 'name': payload['text'][:200], 'responsible_user_id': responsible, 'request_id': payload['request_id']}])
                lead = next(iter((response.get('_embedded') or {}).get('leads', [])), {})
                lead_id = positive_id(lead.get('id'))
                if not lead_id:
                    raise TenantPlatformError('Kommo sövdələşmə kodunu qaytarmadı.')
                lead = {**lead, **body, 'name': payload['text'][:200]}
            else:
                await asyncio.to_thread(store.save, {'step': 'lead_moving', 'lead_id': lead_id})
                await request(tenant, 'PATCH', f'leads/{lead_id}', json_body=body)
                lead = {**lead, **body}
            await asyncio.to_thread(store.save, {'step': 'lead_ready', 'lead_id': lead_id})
            await asyncio.to_thread(upsert_crm_deals, tenant_id=tenant, deals=[{
                'kommo_lead_id': lead_id, 'pipeline_id': route['pipeline_id'], 'status_id': route['status_id'],
                'stage_name': stage.get('name', ''), 'name': lead.get('name', ''), 'raw': lead,
                'contact_name': (cached or {}).get('contact_name', '') if payload['lead_id'] else '',
                'phone': (cached or {}).get('phone', '') if payload['lead_id'] else ''}])
        if reviewer is not None and store.state.get('step') == 'waiting_approval':
            await asyncio.to_thread(store.save, {'step': 'approval_accepted',
                'approval': {'status': 'approved', 'reviewer_id': int(reviewer['telegram_id'])}})
        task_body = {'text': payload['text'] + ('\n' + route['marker'] if route['marker'] else ''),
                     'task_type_id': 1, 'complete_till': payload['complete_till'],
                     'responsible_user_id': responsible, 'request_id': payload['request_id']}
        if lead_id:
            task_body.update(entity_id=lead_id, entity_type='leads')
        await asyncio.to_thread(store.save, {'step': 'task_creating', 'lead_id': lead_id})
        response = await request(tenant, 'POST', 'tasks', json_body=[task_body])
        task = next(iter((response.get('_embedded') or {}).get('tasks', [])), {})
        task_id = positive_id(task.get('id'))
        if not task_id:
            raise TenantPlatformError('Kommo tapşırıq kodunu qaytarmadı.')
        result = {'task_id': task_id, 'lead_id': lead_id, 'executor_id': payload['executor_id']}
        # Persist success before optional cache/audit. Retrying never reposts.
        await asyncio.to_thread(store.save, {'step': 'done', 'result': result})
        await asyncio.to_thread(upsert_crm_tasks, tenant_id=tenant, tasks=[{
            'kommo_task_id': task_id, 'kommo_lead_id': lead_id, 'text': task_body['text'],
            'due_at': datetime.fromtimestamp(payload['complete_till'], timezone.utc).isoformat(),
            'responsible_id': responsible, 'raw': {**task_body, **task}}])
        await asyncio.to_thread(append_audit_event, tenant_id=tenant, actor_telegram_id=int(profile['telegram_id']),
                               action='task_created', entity_type='kommo_task', entity_id=str(task_id), payload=result)
        return result
    except Exception as exc:
        if store.state.get('step') == 'done':
            # Provider write succeeded; temporary cache failure cannot turn
            # the operation into an invitation to create another task.
            import logging
            logging.getLogger(__name__).exception('Task created but cache/audit failed: tenant=%s request=%s', tenant, payload['request_id'])
            return {**store.state['result'], 'warning': 'Tapşırıq yaradıldı. Siyahını yeniləyin.'}
        if store.state.get('step') in {'lead_creating', 'lead_ready', 'lead_moving', 'task_creating'}:
            raise TaskCommandPending('Kommo nəticəsini yoxlayın. Təkrar yaratmayın. Sorğu: ' + payload['request_id']) from exc
        raise
    finally:
        await asyncio.to_thread(store.close)
