"""Prepare, check source and authority, checkpoint, send once, record receipt."""
import asyncio
from tenant_chat_send_policy import command, resolve_route
from tenant_chat_send_store import reserve, mark_sending, finish
from tenant_chat_send_provider import send
from tenant_policy import TenantPolicy
from tenant_linear_policy import TenantLinearError


async def run(session, data, request, wait_slot, logger):
    item = await asyncio.to_thread(reserve, session, command(data))
    if 'cached_result' in item:
        return item['cached_result']
    external_started = False
    try:
        tenant, _, _ = item['key']
        lead_id = item['command']['lead_id']
        async with asyncio.timeout(45):
            route = await resolve_route(tenant, lead_id, request)
            live = await request(tenant, 'GET', f'leads/{lead_id}')
        profile, credentials = await asyncio.to_thread(mark_sending, item, route)
        if int(live.get('id') or 0) != lead_id or not TenantPolicy(profile).can_access_deal({
            **live, 'tenant_id': tenant, 'kommo_lead_id': lead_id}):
            raise TenantLinearError('Sövdələşmənin icraçısı və ya mərhələsi dəyişib.', 403)
        external_started = True
        message_id = await asyncio.to_thread(send, credentials, route, item['command']['text'], wait_slot)
        await asyncio.to_thread(finish, item, 'accepted', message_id)
        return {'state': 'accepted', 'message_id': message_id, 'origin': route['origin']}
    except TenantLinearError:
        await asyncio.to_thread(finish, item, 'blocked')
        raise
    except Exception:
        try:
            await asyncio.to_thread(finish, item, 'unknown' if external_started else 'blocked')
        except Exception:
            logger.error('Tenant chat receipt persistence failed; no retry')
        logger.warning('Tenant chat send unavailable; no automatic retry')
        error = TenantLinearError('Nəticə məlum deyil. Yenidən göndərməyin; Kommo-da yoxlayın.' if external_started else 'Mesaj göndərilmədi. Çat mənbəyini və Kommo bağlantısını yoxlayın.', 503)
        error.keep_request = external_started
        raise error from None
