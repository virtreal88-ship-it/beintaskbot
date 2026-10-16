"""Sandbox orchestration: one real generation; never imports an action executor or transport."""
import asyncio
from tenant_chat_ai_provider import generate
from tenant_ai_testing_store import reserve,finish,read

async def turn(profile,session_id,data):
    item=await asyncio.to_thread(reserve,profile,session_id,data)
    if item.get('cached'):return await asyncio.to_thread(read,profile,session_id)
    try:
        response,usage=await asyncio.to_thread(generate,item['api_key'],item['model'],'reply',item['lines'],'',instructions=item['instructions'],extra_context=item['extra'],images=[],with_usage=True)
    except asyncio.CancelledError:
        await asyncio.shield(asyncio.to_thread(finish,item));raise
    except Exception:
        await asyncio.to_thread(finish,item)
        # The failed attempt is visible in its private history and never replayed automatically.
        return await asyncio.to_thread(read,profile,session_id)
    await asyncio.to_thread(finish,item,response,usage)
    return await asyncio.to_thread(read,profile,session_id)
