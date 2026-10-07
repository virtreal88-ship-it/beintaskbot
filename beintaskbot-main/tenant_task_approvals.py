"""Tenant creation approvals, backed by the existing durable command journal."""
import asyncio
import hashlib
import json
import uuid

from tenant_platform import TenantPlatformError, member
from tenant_policy import TenantPolicy, positive_id
from tenant_task_commands import approval_command, TaskCommandStore
from tenant_tasks import create_task, normalize_task_input, KommoRequest


async def decide_task_approval(profile: dict, data: dict, request: KommoRequest) -> dict:
    policy = TenantPolicy(profile)
    if not policy.privileged or not policy.allows('tasks'):
        raise TenantPlatformError('Təsdiq üçün icazəniz yoxdur.')
    try:
        request_id = str(uuid.UUID(str(data.get('request_id', ''))))
    except (ValueError, TypeError):
        raise TenantPlatformError('Sorğu kodu düzgün deyil.')
    creator_id = positive_id(data.get('creator_id'))
    action = data.get('action')
    if not creator_id or action not in {'approve', 'reject'}:
        raise TenantPlatformError('Təsdiq əməliyyatı düzgün deyil.')
    state = await asyncio.to_thread(approval_command, profile, creator_id, request_id)
    task_input = state.get('task_input') or {}
    payload = normalize_task_input(task_input, creator_id)
    if payload['request_id'] != request_id:
        raise TenantPlatformError('Təsdiq məlumatları düzgün deyil.')
    if action == 'approve':
        creator = await asyncio.to_thread(member, str(profile['tenant_id']), creator_id)
        if not creator or creator.get('active') is False:
            raise TenantPlatformError('Sorğunu yaradan əməkdaş aktiv deyil.')
        return await create_task(creator, task_input, request, reviewer=profile)
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    store = await asyncio.to_thread(TaskCommandStore, str(profile['tenant_id']), creator_id, request_id, fingerprint)
    try:
        if store.state.get('step') == 'rejected':
            return {'rejected': True}
        if store.state.get('step') != 'waiting_approval':
            raise TenantPlatformError('Əməliyyat artıq başlayıb. Kommo nəticəsini yoxlayın; onu rədd etmək olmaz.')
        await asyncio.to_thread(store.save, {'step': 'rejected',
            'approval': {'status': 'rejected', 'reviewer_id': int(profile['telegram_id'])}})
        return {'rejected': True}
    finally:
        await asyncio.to_thread(store.close)
