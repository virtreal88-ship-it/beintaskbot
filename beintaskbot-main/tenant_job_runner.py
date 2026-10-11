"""Small orchestration boundary; one failed queue must not stop the next one."""
from collections.abc import Awaitable, Callable, Iterable
from importlib import import_module


async def deliver_from(module_name: str, *args: object) -> None:
    """Trusted module names supplied by scheduler, never by an HTTP request."""
    await import_module(module_name).deliver(*args)


async def run_jobs(jobs: Iterable[tuple[str, Callable[[], Awaitable[None]]]], logger) -> None:
    for name, job in jobs:
        try:
            await job()
        except Exception:
            # Do not log provider exceptions containing private data or tokens.
            logger.error('Tenant background queue unavailable: %s', name)
