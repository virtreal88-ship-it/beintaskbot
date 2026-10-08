"""Bounded background reads. Never mutate Kommo or use legacy credentials."""
import asyncio
from collections.abc import Awaitable, Callable

from tenant_crm_sync_data import PAGE_SIZE, page_items, normalize_page
from tenant_crm_sync_store import SyncPageStore
from tenant_policy import positive_id
from tenant_crm_sync_windows import window_params

KommoRead = Callable[..., Awaitable[dict]]


async def store_call(function, *args, **kwargs):
    """Do not close the connection while a cancelled thread still commits."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def sync_one_page(store: SyncPageStore, request: KommoRead) -> None:
    row = store.row
    tenant, resource = str(row['tenant_id']), row['resource']
    if not await store_call(store.connection_is_current):
        await store_call(store.fail, permanent=True)
        return
    stage_names = {}
    if resource == 'leads':
        catalog = await request(tenant, 'GET', 'leads/pipelines')
        pipelines = page_catalog(catalog)
        for pipeline in pipelines:
            for stage in (pipeline.get('_embedded') or {}).get('statuses') or []:
                stage_names[int(stage['id'])] = str(stage.get('name') or '')
    params = {'limit': PAGE_SIZE, 'page': int(row['next_page']), 'order[id]': 'asc'}
    params.update(window_params(row))
    if resource == 'leads':
        params['with'] = 'contacts'
    payload = await request(tenant, 'GET', resource, params=params)
    items = page_items(payload, resource)
    ids = [positive_id(item.get('id')) for item in items]
    if ids and (min(ids) <= int(row.get('last_record_id') or 0) or ids != sorted(set(ids))):
        raise ValueError('CRM page did not advance in ID order')
    snapshots = normalize_page(resource, items, stage_names)
    # A full page always probes another page, regardless of HAL links. Never
    # follow a provider-supplied URL (host and credentials stay server-owned).
    await store_call(store.save_page, snapshots, done=len(items) < PAGE_SIZE)


def page_catalog(payload: dict) -> list[dict]:
    pipelines = (payload.get('_embedded') or {}).get('pipelines')
    if not isinstance(pipelines, list):
        raise ValueError('Invalid pipeline catalog')
    return pipelines


async def run_sync_batch(request: KommoRead, logger) -> None:
    for _ in range(4):
        claim = asyncio.create_task(asyncio.to_thread(SyncPageStore.claim))
        try:
            store = await asyncio.shield(claim)
        except asyncio.CancelledError:
            store = await claim
            if store is not None:
                await store_call(store.close)
            raise
        if store is None:
            break
        try:
            await sync_one_page(store, request)
        except Exception:
            # Do not persist/log provider bodies, tokens or customer details.
            logger.warning('Tenant CRM sync page failed: tenant=%s resource=%s page=%s',
                           store.row['tenant_id'], store.row['resource'], store.row['next_page'])
            await store_call(store.fail)
        finally:
            await store_call(store.close)
