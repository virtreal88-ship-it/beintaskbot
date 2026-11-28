"""Finite admission for blocking CRM operations, including after caller cancellation."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from functools import partial
from threading import BoundedSemaphore

import psycopg


class CapacityUnavailable(RuntimeError):
    pass


db_scope = ContextVar('crm_db_scope', default=None)


class WorkPool:
    def __init__(self, name, workers, capacity, deadline, statement_ms):
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=name)
        self.slots = BoundedSemaphore(capacity)
        self.deadline = deadline
        self.db_settings = (name, statement_ms)

    async def run(self, fn, *args, **kwargs):
        if not self.slots.acquire(blocking=False):
            raise CapacityUnavailable()
        token = db_scope.set(self.db_settings)
        try:
            work = self.executor.submit(copy_context().run, partial(fn, *args, **kwargs))
        except BaseException:
            self.slots.release()
            raise
        finally:
            db_scope.reset(token)
        work.add_done_callback(lambda _: self.slots.release())
        try:
            return await asyncio.wait_for(asyncio.wrap_future(work), self.deadline)
        except (TimeoutError, psycopg.OperationalError) as exc:
            raise CapacityUnavailable() from exc


session_io = WorkPool('crm-session', 6, 12, 8, 5000)
dialog_io = WorkPool('crm-dialog', 6, 12, 12, 9000)
avatar_auth_io = WorkPool('crm-avatar-auth', 2, 2, 4, 2000)
