"""Bounded numeric diagnostics for operator reads; never accept request metadata.

Resource codes: 1=list, 2=history, 3=case resolver. At most one completed
request per resource per minute per process is logged (not a percentile sample).
Authentication remains synchronous and uncached; this module only measures it.
"""
import asyncio
import threading
import time

_last_logged = {}
_log_lock = threading.Lock()


class ReadTiming:
    def __init__(self, resource, logger):
        if resource not in (1, 2, 3):
            raise ValueError('Unknown read timing resource')
        self.resource = resource
        self.logger = logger
        self.started = time.perf_counter()
        self.auth_ms = self.queue_ms = self.work_ms = self.resume_ms = -1
        self.completed = 0

    def authenticate(self, function, request):
        started = time.perf_counter()
        try:
            return function(request)
        finally:
            self.auth_ms = (time.perf_counter() - started) * 1000

    async def run(self, function, *args):
        submitted = time.perf_counter()
        finished = None

        def work():
            nonlocal finished
            started = time.perf_counter()
            self.queue_ms = (started - submitted) * 1000
            try:
                return function(*args)
            finally:
                finished = time.perf_counter()
                self.work_ms = (finished - started) * 1000

        try:
            result = await asyncio.to_thread(work)
            self.completed = 1
            return result
        finally:
            if finished is not None:
                self.resume_ms = (time.perf_counter() - finished) * 1000

    def finish(self):
        now = time.perf_counter()
        with _log_lock:
            previous = _last_logged.get(self.resource)
            if previous is not None and now - previous < 60:
                return
            _last_logged[self.resource] = now
        try:
            self.logger.info(
                'operator_read_timing resource=%d completed=%d auth_ms=%.1f '
                'queue_ms=%.1f work_ms=%.1f resume_ms=%.1f total_ms=%.1f',
                self.resource, self.completed, self.auth_ms, self.queue_ms,
                self.work_ms, self.resume_ms, (now - self.started) * 1000,
            )
        except Exception:
            # Diagnostics must never change the API response.
            pass
