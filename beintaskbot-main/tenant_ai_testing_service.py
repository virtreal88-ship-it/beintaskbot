"""Sandbox orchestration: one real generation; never imports an action executor or transport."""
import asyncio
from tenant_chat_ai_provider import generate
from tenant_ai_testing_store import reserve,finish,read

async def turn(profile,session_id,data,*,campaign_id=None):
    reservation=asyncio.create_task(asyncio.to_thread(reserve,profile,session_id,data,**({"campaign_id":campaign_id} if campaign_id else {})))
    try:
        item=await asyncio.shield(reservation)
    except asyncio.CancelledError:
        # The database thread can commit after its waiter is cancelled. Drain
        # it before releasing a reservation that never reached the provider.
        async def cleanup():
            item=await reservation
            if not item.get('cached'):
                await asyncio.to_thread(finish,item,attempted=False)
        await asyncio.shield(cleanup())
        raise
    if item.get('cached'):return await asyncio.to_thread(read,profile,session_id)
    attempted=False
    try:
        if campaign_id:
            from tenant_knowledge_quality import check_active
            await asyncio.to_thread(check_active,profile,campaign_id)
        attempted=True
        response,usage=await asyncio.to_thread(generate,item['api_key'],item['model'],'reply',item['lines'],'',instructions=item['instructions'],extra_context=item['extra'],images=[],with_usage=True)
    except asyncio.CancelledError:
        await asyncio.shield(asyncio.to_thread(finish,item,attempted=attempted));raise
    except Exception:
        await asyncio.to_thread(finish,item,attempted=attempted)
        # The failed attempt is visible in its private history and never replayed automatically.
        return await asyncio.to_thread(read,profile,session_id)
    await asyncio.to_thread(finish,item,response,usage)
    return await asyncio.to_thread(read,profile,session_id)
