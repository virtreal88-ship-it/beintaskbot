"""Read-only Kommo task titles; catalog entries never become editable task text."""
import asyncio
from collections import OrderedDict
import time


def task_title_fields(row):
    raw = row.get('raw')
    if isinstance(raw, dict):
        if 'text' in raw:
            row['text'] = str(raw.get('text') or '')[:4000]
        value = raw.get('task_type_id')
    else:
        value = row.get('task_type_id')
    try:
        row['task_type_id'] = str(int(value)) if int(value) > 0 else None
    except (ValueError, TypeError):
        row['task_type_id'] = None
    row['task_type_name'] = ''
    row['display_title'] = 'Tapşırıq'
    return row


def connection_identity(tenant_id):
    from tenant_platform_core import _connect
    from tenant_crm_sync_store import connection_id
    with _connect() as conn:
        with conn.cursor() as cur:
            return connection_id(cur, tenant_id)


class TaskTypeTitles:
    """Bounded per-handler cache, isolated by tenant and current connection generation.

    Read after task authorization only. Failures are negatively cached briefly;
    metadata failure must not hide tasks or turn a read into a sync/write.
    """
    def __init__(self, request, *, identity=connection_identity, clock=time.monotonic):
        self.request, self.identity, self.clock = request, identity, clock
        self.cache = OrderedDict()
        self.inflight = {}

    async def names(self, tenant):
        try:
            identity = await asyncio.to_thread(self.identity, tenant)
            if not identity:
                return {}
            key = (tenant, identity)
            cached = self.cache.get(key)
            if cached and cached[0] > self.clock():
                self.cache.move_to_end(key)
                return cached[1]
            pending = self.inflight.get(key)
            if pending is None:
                if len(self.inflight) >= 128:
                    return {}
                pending = asyncio.create_task(self._load(tenant, identity, key))
                self.inflight[key] = pending
                pending.add_done_callback(lambda done: self.inflight.pop(key, None))
            return await asyncio.shield(pending)
        except Exception:
            return {}

    async def _load(self, tenant, identity, key):
        names, ttl = {}, 30
        try:
            account = await asyncio.wait_for(self.request(tenant, 'GET', 'account',
                params={'with': 'task_types'}), timeout=3)
            items = (account.get('_embedded') or {}).get('task_types', [])
            if isinstance(items, list):
                for item in items[:1000]:
                    if not isinstance(item, dict):
                        continue
                    try:
                        type_id = int(item.get('id'))
                    except (ValueError, TypeError):
                        continue
                    name = item.get('name')
                    if type_id > 0 and isinstance(name, str) and name.strip():
                        names[str(type_id)] = name.strip()[:240]
                ttl = 300
            if await asyncio.to_thread(self.identity, tenant) != identity:
                return {}
        except Exception:
            names = {}
        self.cache[key] = (self.clock() + ttl, names)
        self.cache.move_to_end(key)
        while len(self.cache) > 128:
            self.cache.popitem(last=False)
        return names

    async def enrich(self, tenant, result):
        if result.get('source') != 'kommo':
            return
        rows = list(result.get('tasks') or [])
        if isinstance(result.get('task'), dict):
            rows.append(result['task'])
        # List/detail reads resolve type independently of description.
        # Command reconciliation must not acquire a provider dependency.
        needs_catalog = any(row.get('task_type_id') for row in rows)
        command = result.get('command') or {}
        if isinstance(command.get('task'), dict):
            rows.append(command['task'])
        if not rows:
            return
        names = await self.names(str(tenant)) if needs_catalog else {}
        for row in rows:
            row['task_type_name'] = names.get(str(row.get('task_type_id')), '')
            row['display_title'] = row['task_type_name'] or 'Tapşırıq'
