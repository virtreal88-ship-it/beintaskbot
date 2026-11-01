"""Current personal ledger visibility and reads on isolated PostgreSQL/WASM."""
import asyncio
import json
import uuid
from urllib.parse import urlencode
from unittest.mock import Mock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from test_tenant_support_inbox import database, db, A, B, CA, CB, PROFILE
from test_dialog_consultations import ready, prepared, reply
import tenant_dialog_consultations as consultations
import tenant_notification_center as center
from tenant_notification_center_api import handler
from tenant_linear_policy import TenantLinearError


@pytest.fixture
def ledger(db, monkeypatch):
    monkeypatch.setattr(center, '_connect', lambda: db)
    return db


def notice(db, *, tenant=A, actor=11, event='task:assigned', target='/app?view=tasks', read=False):
    key = str(uuid.uuid4())
    db.execute('''INSERT INTO saas_tenant_notifications(id,tenant_id,telegram_id,event_key,title,body,target_url,read_at)
      VALUES(%s::uuid,%s::uuid,%s,%s,'Saved notification','Review this item',%s,CASE WHEN %s THEN now() ELSE NULL END)''',
      (key, tenant, actor, event, target, read))
    db.commit()
    return key


def test_empty_and_two_notifications_use_the_same_ledger(ledger):
    assert center.listing(PROFILE) == {'notifications': [], 'unread_count': 0, 'next_before': None, 'status': 'unread'}
    ids = {notice(ledger), notice(ledger, event='ai_consultation:synthetic', target='/app?view=support')}
    result = center.listing(PROFILE)
    assert result['unread_count'] == 2
    assert {n['id'] for n in result['notifications']} == ids
    key = next(iter(ids))
    assert center.mark_read(PROFILE, key)['unread_count'] == 1
    assert center.mark_read(PROFILE, key)['unread_count'] == 1
    assert len(center.listing(PROFILE)['notifications']) == 1
    assert len(center.listing(PROFILE, 'all')['notifications']) == 2


def test_personal_reads_cannot_cross_actor_or_tenant(ledger):
    ours = notice(ledger)
    other = notice(ledger, tenant=B, actor=22)
    with pytest.raises(TenantLinearError) as denied:
        center.mark_read(PROFILE, other)
    assert denied.value.status == 404
    assert center.listing(PROFILE)['unread_count'] == 1
    assert center.listing({'tenant_id': B, 'telegram_id': 22})['unread_count'] == 1
    center.mark_read(PROFILE, ours)
    assert center.listing({'tenant_id': B, 'telegram_id': 22})['unread_count'] == 1
    ledger.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid', (A,)); ledger.commit()
    with pytest.raises(TenantLinearError):
        center.listing(PROFILE)


def test_current_module_access_and_invalid_targets_filter_list_and_count(ledger):
    ledger.execute("UPDATE saas_tenants SET modules='{}'::jsonb WHERE id=%s::uuid", (A,))
    ledger.execute('''INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions)
      VALUES(%s::uuid,33,'operator','Employee','["dialogs"]'::jsonb)''', (A,)); ledger.commit()
    notice(ledger, actor=33)
    notice(ledger, actor=33, event='personal:saved', target='')
    notice(ledger, actor=33, event='personal:remote', target='https://outside.invalid/app?view=dialogs')
    notice(ledger, actor=33, event='personal:missing', target='/app?view=dialogs&lead_id=999&channel_id='+CA+'&conversation_id=missing')
    result = center.listing({'tenant_id': A, 'telegram_id': 33})
    assert result['unread_count'] == 1
    assert [n['event_key'] for n in result['notifications']] == ['personal:saved']


def test_dialog_targets_match_the_exact_decoded_conversation_for_list_count_and_read(ledger):
    conversation = '120363411822115409@g.us'
    ledger.execute('''INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id)
      VALUES(%s::uuid,%s::uuid,%s,99)''', (A, CA, conversation))
    ledger.commit()
    route = {'view': 'dialogs', 'open_dialog': '1', 'provider': 'connect',
             'lead_id': 99, 'channel_id': CA, 'conversation_id': conversation}
    encoded = notice(ledger, event='dialog:encoded', target='/app?' + urlencode(route))
    plain = notice(ledger, event='dialog:plain', target='/app?view=dialogs&lead_id=99&channel_id='+CA+'&conversation_id='+conversation)
    rejected = [
        notice(ledger, event='dialog:wrong', target='/app?' + urlencode({**route, 'conversation_id': 'another@g.us'})),
        notice(ledger, event='dialog:channel', target='/app?' + urlencode({**route, 'channel_id': CB})),
        notice(ledger, event='dialog:double', target='/app?' + urlencode({**route, 'conversation_id': conversation.replace('@', '%40')})),
        notice(ledger, event='dialog:invalid-utf8', target='/app?view=dialogs&lead_id=99&channel_id='+CA+'&conversation_id=%FF'),
        notice(ledger, event='dialog:invalid-escape', target='/app?view=dialogs&lead_id=99&channel_id='+CA+'&conversation_id=%GG'),
    ]
    result = center.listing(PROFILE)
    assert result['unread_count'] == 2
    assert {row['id'] for row in result['notifications']} == {encoded, plain}
    for key in rejected:
        with pytest.raises(TenantLinearError) as denied:
            center.mark_read(PROFILE, key)
        assert denied.value.status == 404
    assert center.mark_read(PROFILE, encoded)['unread_count'] == 1


def test_dialog_query_encoding_preserves_literal_plus_spaces_and_unicode(ledger):
    conversation = 'Тест +@g.us'
    ledger.execute('''INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id)
      VALUES(%s::uuid,%s::uuid,%s,99)''', (A, CA, conversation))
    ledger.commit()
    route = {'view': 'dialogs', 'lead_id': 99, 'channel_id': CA, 'conversation_id': conversation}
    selected = notice(ledger, event='dialog:unicode', target='/app?' + urlencode(route))
    notice(ledger, event='dialog:raw-plus', target='/app?view=dialogs&lead_id=99&channel_id='+CA+'&conversation_id=Тест++@g.us')
    result = center.listing(PROFILE)
    assert result['unread_count'] == 1 and [row['id'] for row in result['notifications']] == [selected]


def test_pagination_keeps_all_notifications_reachable_without_duplicate_rows(ledger):
    keys = {notice(ledger) for _ in range(23)}
    page = center.listing(PROFILE, limit=5)
    found = []
    while True:
        assert page['unread_count'] == 23
        assert len(page['notifications']) <= 5
        found.extend(n['id'] for n in page['notifications'])
        if not page['next_before']:
            break
        page = center.listing(PROFILE, limit=5, before=page['next_before'])
    assert len(found) == 23 and set(found) == keys
    with pytest.raises(TenantLinearError):
        center.listing(PROFILE, before='invalid cursor')
    cursor = center.listing(PROFILE, limit=5)['next_before']
    with pytest.raises(TenantLinearError):
        center.listing(PROFILE, status='all', before=cursor)


def test_handled_unread_consultation_stays_in_menu_and_read_is_personal(ready, monkeypatch):
    local, data, _ = ready
    monkeypatch.setattr(center, '_connect', lambda: local)
    result, _ = prepared(ready)
    consultations.dispatch(PROFILE, result['id'], data['request_id'])
    rid = reply(local, result)
    consultations.action(PROFILE, rid, {'action': 'handled'})
    inbox = center.listing(PROFILE)
    assert inbox['unread_count'] == 1 and len(inbox['notifications']) == 1
    item = inbox['notifications'][0]
    assert item['reply_id'] == rid and 'consultation_reply_id='+rid in item['target_url']
    assert center.mark_read(PROFILE, item['id'])['unread_count'] == 0
    assert consultations.inbox(PROFILE)['unread'] == 0
    assert consultations.inbox({'tenant_id': A, 'telegram_id': 33})['unread'] == 1
    assert center.listing(PROFILE)['notifications'] == []
    assert len(center.listing(PROFILE, 'all')['notifications']) == 1


def test_unavailable_consultation_source_is_not_counted_or_exposed(ready, monkeypatch):
    local, data, _ = ready
    monkeypatch.setattr(center, '_connect', lambda: local)
    result, _ = prepared(ready)
    consultations.dispatch(PROFILE, result['id'], data['request_id'])
    reply(local, result)
    local.execute('UPDATE saas_connect_channels SET active=FALSE WHERE tenant_id=%s::uuid', (A,)); local.commit()
    assert center.listing(PROFILE)['unread_count'] == 0
    assert center.listing(PROFILE, 'all')['notifications'] == []
    assert local.execute('SELECT count(*) AS n FROM saas_tenant_notifications')[0]['n'] == 1


def test_case_reply_requires_current_support_right_and_keeps_personal_duplicates_consistent(ready, monkeypatch):
    local, data, _ = ready
    monkeypatch.setattr(center, '_connect', lambda: local)
    result, _ = prepared(ready)
    consultations.dispatch(PROFILE, result['id'], data['request_id'])
    rid = reply(local, result)
    notice(local, event='dialog_consultation:'+rid, target='/app?view=dialogs')
    assert center.listing(PROFILE)['unread_count'] == 2
    center.mark_read(PROFILE, center.listing(PROFILE)['notifications'][0]['id'])
    assert center.listing(PROFILE)['unread_count'] == 0
    employee_notice = notice(local, actor=33, event='dialog_consultation:'+rid, target='/app?view=dialogs')
    assert center.listing({'tenant_id': A, 'telegram_id': 33})['unread_count'] == 1
    import tenant_support_inbox_store as tickets
    with local.cursor() as cur:
        ticket = tickets.create_in_transaction(cur, A, CA, 'client', 'Synthetic case', 'case-permission-test')
    local.execute('UPDATE saas_internal_consultations SET case_id=%s::uuid WHERE tenant_id=%s::uuid AND id=%s::uuid',
      (ticket['id'], A, result['id'])); local.commit()
    assert center.listing({'tenant_id': A, 'telegram_id': 33})['unread_count'] == 0
    with pytest.raises(TenantLinearError) as denied:
        center.mark_read({'tenant_id': A, 'telegram_id': 33}, employee_notice)
    assert denied.value.status == 404
    assert local.execute('SELECT read_at FROM saas_tenant_notifications WHERE id=%s::uuid', (employee_notice,))[0]['read_at'] is None


def test_http_boundary_binds_identity_and_only_explicit_read_is_a_write(monkeypatch):
    listing = Mock(return_value={'notifications': [], 'unread_count': 0, 'next_before': None})
    reading = Mock(return_value={'id': str(uuid.uuid4()), 'read_at': 'now', 'unread_count': 0})
    monkeypatch.setattr(center, 'listing', listing); monkeypatch.setattr(center, 'mark_read', reading)
    async def scenario():
        app = web.Application()
        app.router.add_get('/notifications', handler(lambda req: PROFILE, 'https://crm.test', Mock()))
        app.router.add_post('/notifications/{notification_id}/read', handler(lambda req: PROFILE, 'https://crm.test', Mock(), 'read'))
        async with TestClient(TestServer(app)) as client:
            response = await client.get('/notifications?status=all&limit=5')
            assert response.status == 200 and response.headers['Cache-Control'] == 'no-store'
            assert not reading.called
            response = await client.get('/notifications', headers={'X-Tenant-Id': B, 'X-User-Id': '11'})
            assert response.status == 409
            key = str(uuid.uuid4())
            response = await client.post('/notifications/'+key+'/read', json={'expected_tenant_id': A, 'expected_user_id': 11}, headers={'Origin': 'https://crm.test'})
            assert response.status == 200 and reading.call_args.args == (PROFILE, key)
            response = await client.post('/notifications/'+key+'/read', json={'expected_tenant_id': A, 'expected_user_id': 11, 'action': 'handled'}, headers={'Origin': 'https://crm.test'})
            assert response.status == 400
            response = await client.post('/notifications/'+key+'/read', json={'expected_tenant_id': A, 'expected_user_id': 11}, headers={'Origin': 'https://wrong.invalid'})
            assert response.status == 403
    asyncio.run(scenario())
