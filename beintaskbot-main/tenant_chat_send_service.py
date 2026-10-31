"""Prepare, check source and authority, checkpoint, send once, record receipt."""
import asyncio
from tenant_chat_send_policy import command, resolve_route
from tenant_chat_send_store import reserve, mark_sending, finish, quoted_status_credentials, record_quoted_delivery
from tenant_chat_send_provider import send
from tenant_policy import TenantPolicy
from tenant_linear_policy import TenantLinearError
from tenant_connect_transport import send as send_connect, record_outbound as record_connect_outbound, quoted_delivery_status


async def run(session, data, request, wait_slot, logger):
    try:
        item=await asyncio.to_thread(reserve,session,command(data))
    except TenantLinearError as error:
        if isinstance(data,dict) and data.get('quote_message_id'):error.keep_request=True
        raise
    if 'cached_result' in item:
        if item.get('quote'):
            return {**item['cached_result'],**await _quoted_status(item,item['cached_result']['message_id'])}
        return item['cached_result']
    external_started = False
    try:
        tenant, _, _ = item['key']
        lead_id = item['command']['lead_id']
        is_connect = item['connection'][0] == 'connect'
        if is_connect:
            route = {'origin': 'connect'}
            profile, credentials = await asyncio.to_thread(mark_sending, item, route)
        else:
            async with asyncio.timeout(45):
                route = await resolve_route(tenant, lead_id, request)
                live = await request(tenant, 'GET', f'leads/{lead_id}')
            profile, credentials = await asyncio.to_thread(mark_sending, item, route)
            if int(live.get('id') or 0) != lead_id or not TenantPolicy(profile).can_access_deal({
                **live, 'tenant_id': tenant, 'kommo_lead_id': lead_id}):
                raise TenantLinearError('Sövdələşmənin icraçısı və ya mərhələsi dəyişib.', 403)
        external_started = True
        message_id = await asyncio.to_thread(send_connect, credentials, item['command']['text'], item['command']['request_id'], **({'quoted_message_id':item['quote']} if item.get('quote') else {})) if is_connect else await asyncio.to_thread(send, credentials, route, item['command']['text'], wait_slot)
        if is_connect:
            await asyncio.to_thread(record_connect_outbound, tenant, lead_id, credentials, item['command']['text'], message_id, actor=profile['telegram_id'], **({'quoted_message_id':item['quote']} if item.get('quote') else {}))
        await asyncio.to_thread(finish, item, 'accepted', message_id)
        return {'state':'accepted','message_id':message_id,'origin':route['origin'],**(await _quoted_status(item,message_id) if item.get('quote') else {})}
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


async def _quoted_status(item,message_id):
    try:
        credentials=await asyncio.to_thread(quoted_status_credentials,item)
        status=await asyncio.to_thread(quoted_delivery_status,credentials,message_id,item['command']['request_id'],item['quote'])
        await asyncio.to_thread(record_quoted_delivery,item,message_id,status)
        return status
    except Exception:
        # Acceptance/unknown outcomes are never replayed to check their status.
        return {'delivery_state':'unknown','provider_accepted':False,'delivered':False,'retry_safe':False}
