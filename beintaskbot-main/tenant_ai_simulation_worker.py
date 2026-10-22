"""Independent background worker for the isolated Simulation queue."""
import asyncio

from tenant_ai_agent_runtime_flags import enabled as runtime_flag
from tenant_ai_simulation_engine import claim_next,process


async def run_worker(logger, *, poll_seconds=1.0):
    """No dependency on or invocation of the production Active worker."""
    if not runtime_flag('AI_SIMULATION_ENABLED'):
        logger.info('AI Simulation worker disabled by runtime flag')
        return
    while runtime_flag('AI_SIMULATION_ENABLED'):
        try:
            job=await asyncio.to_thread(claim_next)
            if not job:
                await asyncio.sleep(poll_seconds)
                continue
            try:
                await asyncio.to_thread(process,job)
            except Exception:
                logger.exception('AI Simulation item failed: queue_id=%s tenant=%s',job.get('id'),job.get('tenant_id'))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('AI Simulation queue polling failed')
            await asyncio.sleep(max(2.0,poll_seconds))
