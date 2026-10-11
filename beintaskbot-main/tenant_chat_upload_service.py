"""Reserve file hash, upload, re-route/re-authorize, checkpoint and send once."""
import asyncio
from tenant_chat_send_store import reserve, upload_credentials, mark_sending, finish
from tenant_chat_send_policy import resolve_route
from tenant_chat_upload_provider import upload
from tenant_chat_send_provider import send
from tenant_linear_policy import TenantLinearError
from tenant_policy import TenantPolicy


async def run(session, command, content, request, wait_slot, logger):
    item = await asyncio.to_thread(reserve, session, command)
    if 'cached_result' in item:
        return item['cached_result']
    started = False
    try:
        tenant = item['key'][0]
        async with asyncio.timeout(45):
            await resolve_route(tenant, command['lead_id'], request)
            account = await request(tenant, 'GET', 'account', params={'with': 'drive_url'})
        credentials = await asyncio.to_thread(upload_credentials, item)
        attachment = await asyncio.to_thread(upload, account.get('drive_url'), credentials['access_token'], command['file'], content)
        async with asyncio.timeout(45):
            route = await resolve_route(tenant, command['lead_id'], request)
            live = await request(tenant, 'GET', f"leads/{command['lead_id']}")
        profile, credentials = await asyncio.to_thread(mark_sending, item, {**route, 'attachment': attachment})
        if int(live.get('id') or 0) != command['lead_id'] or not TenantPolicy(profile).can_access_deal({**live,'tenant_id':tenant}):
            raise TenantLinearError('Sövdələşməyə giriş dəyişib.', 403)
        started = True
        message_id = await asyncio.to_thread(send, credentials, route, command['text'], wait_slot, attachment=attachment)
        await asyncio.to_thread(finish, item, 'accepted', message_id)
        return {'state': 'accepted', 'message_id': message_id, 'origin': route['origin']}
    except Exception as error:
        try:
            await asyncio.to_thread(finish, item, 'unknown' if started else 'blocked')
        except Exception:
            logger.error('Tenant file receipt persistence unavailable')
        if isinstance(error, TenantLinearError):
            error.keep_request = started
            raise
        logger.warning('Tenant file send unavailable; no automatic retry')
        failure = TenantLinearError('Nəticə məlum deyil. Təkrar göndərməyin.' if started else 'Fayl göndərilmədi. Kommo fayl icazələrini yoxlayın.', 503)
        failure.keep_request = started
        raise failure from None
