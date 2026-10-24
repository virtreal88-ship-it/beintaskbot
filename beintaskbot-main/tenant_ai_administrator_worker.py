import asyncio
from tenant_ai_administrator_observations import enabled,claim_next,process

async def run_worker(logger,*,poll_seconds=2.0):
    if not enabled(): return
    while enabled():
        try:
            item=await asyncio.to_thread(claim_next)
            if item: await asyncio.to_thread(process,item)
            else: await asyncio.sleep(poll_seconds)
        except asyncio.CancelledError: raise
        except Exception: logger.exception('Autonomous administrator observation worker failed'); await asyncio.sleep(max(2,poll_seconds))
