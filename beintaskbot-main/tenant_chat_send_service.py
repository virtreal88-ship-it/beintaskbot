"""Prepare, check source and authority, checkpoint, send once, record receipt."""
import asyncio
from tenant_chat_send_policy import command, resolve_route
from tenant_chat_send_store import reserve, mark_sending, finish
from tenant_chat_send_provider import send
from tenant_chat_send_store import reserve_agent, mark_agent_sending, finish_agent
from tenant_chat_reader import read_talks, read_messages
from tenant_policy import TenantPolicy
from tenant_linear_policy import TenantLinearError
from tenant_ai_agent_test_mode import assert_expected_route, assert_job_allowed, assert_live_job_allowed


async def run(session, data, request, wait_slot, logger, *, expected_route=None):
    item = await asyncio.to_thread(reserve, session, command(data))
    if 'cached_result' in item:
        return item['cached_result']
    external_started = False
    try:
        tenant, _, _ = item['key']
        lead_id = item['command']['lead_id']
        if expected_route is not None:
            # Manual review of an AI draft is still tied to the exact message
            # it answered. Wait for the account limiter before checking the
            # source so the queue delay cannot make that review stale.
            await asyncio.to_thread(wait_slot, item['connection'][0])
        async with asyncio.timeout(45):
            route = await resolve_route(tenant, lead_id, request)
            if expected_route is not None and any(
                    str(route.get(key) or '') != str(expected_route.get(key) or '')
                    for key in ('talk_id', 'chat_id', 'message_id')):
                raise TenantLinearError('Qaralama başqa söhbətə və ya köhnə mesaja aiddir; göndərilmədi.', 409)
            if expected_route is not None:
                talks = await read_talks(tenant, lead_id, request)
                talk = next((row for row in talks if str(row.get('talk_id') or row.get('id') or '') == str(expected_route['talk_id'])
                    and str(row.get('chat_id') or '') == str(expected_route['chat_id'])), None)
                if not talk:
                    raise TenantLinearError('Qaralamanın söhbəti artıq əlçatan deyil; göndərilmədi.', 409)
                messages = await read_messages(tenant, talk, request)
                source = None
                source_stamp = 0
                for row in messages:
                    nested = row.get('message') if isinstance(row.get('message'), dict) else {}
                    message_id = str(nested.get('id') or nested.get('msgid') or row.get('id') or row.get('msgid') or '')
                    direction = str(nested.get('direction') or row.get('direction') or nested.get('type') or row.get('type') or '').casefold()
                    created = nested.get('sec_created_at') or row.get('sec_created_at')
                    try:
                        stamp = int(created) if created else int(nested.get('created_at') or row.get('created_at') or 0) * 1000
                    except (TypeError, ValueError):
                        stamp = 0
                    if message_id == str(expected_route['message_id']):
                        source, source_stamp = direction, stamp
                if source not in {'incoming', 'in', 'inbound'} or not source_stamp:
                    raise TenantLinearError('Исходное входящее сообщение больше не подтверждено; отправка отменена.', 409)
                for row in messages:
                    nested = row.get('message') if isinstance(row.get('message'), dict) else {}
                    message_id = str(nested.get('id') or nested.get('msgid') or row.get('id') or row.get('msgid') or '')
                    created = nested.get('sec_created_at') or row.get('sec_created_at')
                    try:
                        stamp = int(created) if created else int(nested.get('created_at') or row.get('created_at') or 0) * 1000
                    except (TypeError, ValueError):
                        stamp = 0
                    if message_id and message_id != str(expected_route['message_id']) and stamp >= source_stamp:
                        raise TenantLinearError('Диалог изменился после создания черновика; отправка отменена.', 409)
            live = await request(tenant, 'GET', f'leads/{lead_id}')
        if expected_route is not None:
            await assert_expected_route(tenant,lead_id,expected_route,request)
        profile, credentials = await asyncio.to_thread(mark_sending, item, route)
        if int(live.get('id') or 0) != lead_id or not TenantPolicy(profile).can_access_deal({
            **live, 'tenant_id': tenant, 'kommo_lead_id': lead_id}):
            raise TenantLinearError('Sövdələşmənin icraçısı və ya mərhələsi dəyişib.', 403)
        if expected_route is not None:
            # This endpoint is used for AI draft review; the separate guard
            # leaves ordinary /app and /webapp sends unchanged.
            await assert_expected_route(tenant,lead_id,expected_route,request)
        external_started = True
        send_wait_slot = (lambda _domain: None) if expected_route is not None else wait_slot
        message_id = await asyncio.to_thread(send, credentials, route, item['command']['text'], send_wait_slot)
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


async def run_agent(job, reservation, text, request, wait_slot, logger):
    """Send one agent answer through the existing Kommo transport/receipt ledger."""
    item = await asyncio.to_thread(reserve_agent, job, text, reservation['route'])
    external_started = False
    try:
        tenant=str(job['tenant_id']); lead_id=int(job['lead_id'])
        # Wait for Kommo's existing per-account limiter before the final chat
        # check, so no intentional delay separates validation from the POST.
        await asyncio.to_thread(wait_slot,item['credentials']['account_domain'])
        async with asyncio.timeout(40):
            talks=await read_talks(tenant,lead_id,request)
            talk=next((row for row in talks if str(row.get('talk_id') or row.get('id') or '')==str(job['talk_id'])
                       and str(row.get('chat_id') or '')==str(job['chat_id'])),None)
            if not talk or talk.get('is_in_work') is False or talk.get('is_closed') is True:
                raise TenantLinearError('Söhbət bağlanıb və ya dəyişib; mesaj saxlanıldı.',409)
            current_origin=str(talk.get('origin') or '').strip().casefold()
            if item['route'].get('origin') and current_origin and current_origin!=str(item['route']['origin']).strip().casefold():
                raise TenantLinearError('Söhbət kanalı dəyişib; mesaj saxlanıldı.',409)
            messages=await read_messages(tenant,talk,request)
            normalized=[]
            for row in messages:
                nested=row.get('message') if isinstance(row.get('message'),dict) else {}
                ident=str(row.get('id') or row.get('msgid') or nested.get('id') or nested.get('msgid') or '')
                direction=str(nested.get('direction') or row.get('direction') or nested.get('type') or row.get('type') or '').casefold()
                created=nested.get('created_at') or row.get('created_at') or nested.get('timestamp') or row.get('timestamp')
                try: stamp=int(created)
                except (TypeError,ValueError): stamp=0
                normalized.append((stamp,ident,direction))
            source=next((row for row in normalized if row[1]==str(reservation['source_message_id'])),None)
            if (not source or source[2] not in {'incoming','in','inbound'}
                    or any(row[1]!=source[1] and row[0]>=source[0] for row in normalized)):
                raise TenantLinearError('Müştəri və ya əməkdaş daha yeni mesaj yazıb; avtomatik cavab ləğv edildi.',409)
            live=await request(tenant,'GET',f'leads/{lead_id}',params={'with':'contacts'})
            if int(live.get('id') or 0)!=lead_id or str(live.get('pipeline_id') or '')!=str(reservation['pipeline_id']) or str(live.get('status_id') or '')!=str(reservation['stage_id']):
                raise TenantLinearError('Sövdələşmənin mərhələsi dəyişib; avtomatik cavab ləğv edildi.',409)
            await assert_live_job_allowed(job,talk,live,request)
        await asyncio.to_thread(mark_agent_sending,item)
        await asyncio.to_thread(assert_job_allowed,job)
        external_started=True
        message_id=await asyncio.to_thread(send,item['credentials'],reservation['route'],text,lambda _domain:None)
        await asyncio.to_thread(finish_agent,item,'accepted',message_id)
        return {'state':'accepted','message_id':message_id}
    except TenantLinearError:
        await asyncio.to_thread(finish_agent,item,'blocked')
        raise
    except Exception:
        try:
            settled=await asyncio.to_thread(finish_agent,item,'unknown' if external_started else 'blocked')
        except Exception:
            logger.error('AI agent send receipt persistence failed; automatic retry remains blocked')
            settled='unknown'
        if settled=='accepted':
            return {'state':'accepted','reconciled':True}
        raise TenantLinearError('Göndərmə nəticəsi qeyri-müəyyəndir. Avtomatik təkrar edilmir; Kommo-da yoxlayın.',503) from None
