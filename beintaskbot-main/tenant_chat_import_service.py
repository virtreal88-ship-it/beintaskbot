"""Manual one-page Kommo import; cache pagination stays independently bounded."""
import asyncio
import base64
import hashlib
import json
from tenant_chat_reader import read_talks, rows, continuation, PAGE_SIZE, fail
from tenant_linear_policy import TenantLinearError
from tenant_policy import TenantPolicy

MAX_PAGE = 1000


def token(scope: list, index: int, number: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({'scope': scope, 'index': index, 'page': number}).encode()).decode().rstrip('=')


def position(value: str, scope: list, total: int) -> tuple[int, int]:
    if not value: return 0, 1
    try:
        if not isinstance(value, str) or len(value) > 2048: raise ValueError()
        data = json.loads(base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True))
        index, number = data['index'], data['page']
        if data['scope'] != scope or type(index) is not int or type(number) is not int or not 0 <= index < total or not 1 <= number <= MAX_PAGE:
            raise ValueError()
        return index, number
    except (ValueError, TypeError, KeyError, UnicodeDecodeError):
        raise TenantLinearError('Tarixçə mənbəyi və ya kabinet dəyişib. Yenidən başlayın.', 409) from None


async def run(session: dict, lead: int, cursor: str, request, authorize, current_member, upsert, normalize, channel) -> dict:
    tenant = str(session['tenant_id'])
    connection = await asyncio.to_thread(authorize, session, lead)
    async with asyncio.timeout(40):
        talks = sorted(await read_talks(tenant, lead, request), key=lambda row: int(row.get('talk_id') or row.get('id')))
        catalog = hashlib.sha256(json.dumps([(row.get('talk_id') or row.get('id'), row['chat_id']) for row in talks]).encode()).hexdigest()
        scope = [tenant, str(session['telegram_id']), lead, connection, catalog]
        if not talks:
            if cursor: raise TenantLinearError('Çat siyahısı dəyişib.', 409)
            if await asyncio.to_thread(authorize, session, lead) != connection:
                raise TenantLinearError('Kommo bağlantısı dəyişib.', 409)
            return {'imported': 0, 'has_more': False, 'next_cursor': None}
        index, number = position(cursor, scope, len(talks))
        talk = talks[index]
        payload = await request(tenant, 'GET', f"talks/{int(talk.get('talk_id') or talk.get('id'))}/messages",
                                params={'page': number, 'limit': PAGE_SIZE})
        page = rows(payload, 'messages')
        if len(page) > PAGE_SIZE: raise fail()
        seen = {}
        for row in page:
            if str(row.get('chat_id') or '') != str(talk['chat_id']) or not row.get('id'): raise fail()
            key = str(row['id'])
            if key in seen and seen[key] != row: raise fail()
            seen[key] = row
        more = continuation(payload) or len(page) == PAGE_SIZE
        if more and (not page or number == MAX_PAGE): raise fail()
        batch = [normalize(row, str(row.get('origin') or channel(talk))) for row in seen.values()]
        live = await request(tenant, 'GET', f'leads/{lead}')
    if await asyncio.to_thread(authorize, session, lead) != connection:
        raise TenantLinearError('Kommo bağlantısı dəyişib.', 409)
    member = await asyncio.to_thread(current_member, tenant, int(session['telegram_id']))
    if not member or member.get('active') is not True or member.get('tenant_status') != 'active' or int(live.get('id') or 0) != lead or not TenantPolicy(member).can_access_deal({**live, 'tenant_id': tenant}):
        raise TenantLinearError('Sövdələşməyə giriş dəyişib.', 403)
    if batch: await asyncio.to_thread(upsert, tenant_id=tenant, kommo_lead_id=lead, messages=batch)
    next_index, next_page = (index, number + 1) if more else (index + 1, 1)
    next_cursor = token(scope, next_index, next_page) if next_index < len(talks) else None
    return {'imported': len(batch), 'has_more': next_cursor is not None, 'next_cursor': next_cursor}
