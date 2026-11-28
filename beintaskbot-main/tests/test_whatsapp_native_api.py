"""Real local HTTP, synthetic source credentials; no provider or production DB."""
import asyncio
from unittest.mock import Mock
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from test_browser_calls import gateway
from test_call_recordings import pilot
from test_call_sources import TOKEN, managed
from test_tenant_support_inbox import A, CA, database, db
from test_whatsapp_native_consumer import activate, ready
from test_whatsapp_native_inbox import capture, delivery, store
from test_whatsapp_native_projection import message
import tenant_whatsapp_native_api as api
import tenant_whatsapp_native_consumer as consumer
from tenant_linear_policy import TenantLinearError


def test_projection_finishes_before_receipt_and_failure_is_retried(monkeypatch):
    async def run():
        monkeypatch.setenv('BEINTASKBOT_UNIFIED_WHATSAPP_INBOX', '1')
        monkeypatch.setenv('BEINTASKBOT_UNIFIED_WHATSAPP_PROJECTION', '1')
        monkeypatch.setattr(api, 'SLOTS', asyncio.Semaphore(2))
        monkeypatch.setattr(api.tenant_browser_calls, 'gateway_auth', lambda _: {'managed':True})
        receipt = {'state':'stored', 'sequence':7,'scope':{'tenant':A}}
        order = []
        def store(*args):
            order.append('store_committed')
            return receipt
        def project(value):
            assert value == receipt and order[-1] == 'store_committed'
            order.append('crm_committed')
            return True
        updated=Mock()
        updated.publish.side_effect=lambda *_:order.append('browser_woken')
        monkeypatch.setattr(api.inbox, 'store_delivery', store)
        monkeypatch.setattr(consumer, 'project_stored', project)
        app = web.Application()
        app.router.add_post('/messages', api.handler(Mock(), 'https://example.invalid', Mock(),message_updates=updated))
        async with TestClient(TestServer(app)) as client:
            response = await client.post('/messages', json={})
            assert response.status == 200
            assert order == ['store_committed', 'crm_committed', 'browser_woken']
            updated.publish.assert_called_once_with({'tenant_id':A},'')
            monkeypatch.setattr(consumer, 'project_stored', Mock(side_effect=RuntimeError('PRIVATE')))
            response = await client.post('/messages', json={})
            assert response.status == 503 and 'PRIVATE' not in await response.text()
            assert updated.publish.call_count == 1
    asyncio.run(run())


def test_private_delivery_gate_and_post_commit_response(monkeypatch):
    async def run():
        logger,member=Mock(),Mock()
        authenticate=Mock(return_value={'managed':True})
        store=Mock(return_value={'state':'stored','sequence':7})
        monkeypatch.setattr(api.tenant_browser_calls,'gateway_auth',authenticate)
        monkeypatch.setattr(api.inbox,'store_delivery',store)
        monkeypatch.setattr(api,'SLOTS',asyncio.Semaphore(2))
        app=web.Application(client_max_size=api.inbox.MAX_DELIVERY)
        app.router.add_post('/messages',api.handler(member,'https://example.invalid',logger))
        async with TestClient(TestServer(app)) as client:
            monkeypatch.delenv('BEINTASKBOT_UNIFIED_WHATSAPP_INBOX',raising=False)
            response=await client.post('/messages',json={})
            assert response.status==404
            authenticate.assert_not_called();store.assert_not_called()
            monkeypatch.setenv('BEINTASKBOT_UNIFIED_WHATSAPP_INBOX','1')
            for headers in ({'Origin':'https://example.invalid'},{'Cookie':'session=test'}):
                response=await client.post('/messages',json={},headers=headers)
                assert response.status==403
            authenticate.assert_not_called()
            for managed in (False,None):
                authenticate.return_value={'managed':managed}
                assert (await client.post('/messages',json={})).status==403
            authenticate.return_value={'managed':True}
            assert (await client.post('/messages',data='{}')).status==400
            assert (await client.post('/messages',data=b'x'*(api.inbox.MAX_DELIVERY+1),headers={'Content-Type':'application/json'})).status==413
            store.assert_not_called()
            response=await client.post('/messages',json={},headers={'Authorization':'Bearer synthetic'})
            assert response.status==200 and await response.json()=={'state':'stored','sequence':7}
            assert response.headers['Cache-Control']=='no-store'
            assert store.call_args.args==('Bearer synthetic',b'{}')
            store.side_effect=TenantLinearError('native_source_changed',409)
            assert (await client.post('/messages',json={})).status==409
            store.side_effect=RuntimeError('SYNTHETIC_PRIVATE_PAYLOAD')
            response=await client.post('/messages',json={})
            assert response.status==503 and 'SYNTHETIC_PRIVATE_PAYLOAD' not in await response.text()
            assert logger.warning.call_args.args==('native_whatsapp_delivery_failed stage=store code=internal',)
            member.assert_not_called()
    asyncio.run(run())


def test_inbox_saturation_does_not_use_call_slots(monkeypatch):
    async def run():
        monkeypatch.setenv('BEINTASKBOT_UNIFIED_WHATSAPP_INBOX','1')
        monkeypatch.setattr(api,'SLOTS',asyncio.Semaphore(0))
        auth=Mock();monkeypatch.setattr(api.tenant_browser_calls,'gateway_auth',auth)
        app=web.Application();app.router.add_post('/messages',api.handler(Mock(),'https://example.invalid',Mock()))
        async with TestClient(TestServer(app)) as client:
            assert (await client.post('/messages',json={})).status==429
            auth.assert_not_called()
    asyncio.run(run())


def test_cancelled_requests_keep_capacity_until_transactions_finish(monkeypatch):
    import threading
    async def run():
        started, finish = threading.Event(), threading.Event()
        slots = asyncio.Semaphore(1)
        def slow_commit():
            started.set()
            assert finish.wait(4)
            raise RuntimeError('SYNTHETIC_PRIVATE_COMMIT_ERROR')
        async def request():
            async with slots:
                await api._finish_thread(slow_commit)
        job = asyncio.create_task(request())
        try:
            assert await asyncio.to_thread(started.wait, 2)
            job.cancel()
            await asyncio.sleep(0)
            job.cancel()
            await asyncio.sleep(0)
            assert slots.locked() and not job.done()
        finally:
            finish.set()
        with pytest.raises(asyncio.CancelledError):
            await job
        assert not slots.locked()
        assert await api._finish_thread(lambda: 'committed') == 'committed'
    asyncio.run(run())


def deliver_over_http(monkeypatch, *events):
    async def run():
        monkeypatch.setattr(api, 'SLOTS', asyncio.Semaphore(2))
        app = web.Application()
        app.router.add_post('/messages', api.handler(Mock(), 'https://example.invalid', Mock()))
        async with TestClient(TestServer(app)) as client:
            results = []
            for event in events:
                response = await client.post('/messages', json=event, headers={'Authorization':'Bearer '+TOKEN})
                results.append((response.status, await response.json()))
            return results
    return asyncio.run(run())


def test_private_voice_reply_to_group_does_not_block_later_messages(ready,monkeypatch):
    db, _ = ready
    activate(db)
    chat = '188888888@lid'
    voice = delivery()
    voice['after'] = 2
    voice['event'].update(id='b'*64, sequence=3, payload=message(
        chat=chat, sender=chat, sender_alt='', text='', provider_id='PRIVATE_VOICE',
        message_type='audio', media_kind='audio', media_mime='audio/ogg', media_size=120,
        quote_id='GROUP_MESSAGE', quote_chat='120363123456789@g.us'))
    following = delivery()
    following['after'] = 3
    following['event'].update(id='c'*64, sequence=4, payload=message(
        chat=chat, sender=chat, sender_alt='', provider_id='FOLLOWING_MESSAGE'))
    receipts = deliver_over_http(monkeypatch,voice,voice,following)
    assert [(status, result['sequence']) for status,result in receipts] == [(200,3),(200,3),(200,4)]
    rows = db.execute("""SELECT m.direction,m.message_type,m.raw,c.conversation_id
      FROM saas_crm_messages m JOIN saas_connect_conversations c
        ON c.tenant_id=m.tenant_id AND c.lead_id=m.kommo_lead_id
      WHERE m.tenant_id=%s AND m.channel=%s
        AND m.raw->>'externalMessageId' IN ('PRIVATE_VOICE','FOLLOWING_MESSAGE')
      ORDER BY m.message_type""", (A,'connect:'+CA))
    assert [(r['direction'],r['message_type'],r['conversation_id']) for r in rows] == [
        ('incoming','audio',chat),('incoming','text',chat)]
    assert rows[0]['raw']['nativeEventId']=='b'*64
    assert 'quotedExternalMessageId' not in rows[0]['raw']
    assert 'GROUP_MESSAGE' not in str(rows[0]['raw'])
    assert db.execute('SELECT stored_sequence FROM saas_whatsapp_capture_generations') == [
        {'stored_sequence':4}]
    db.commit()


def test_invalid_payload_is_retained_and_audited_once_before_continuing(ready,monkeypatch):
    db, _ = ready
    activate(db)
    invalid = delivery()
    invalid['after'] = 2
    invalid['event'].update(id='b'*64, sequence=3, payload=message(
        sender='invalid sender', provider_id='INVALID_MESSAGE'))
    following = delivery()
    following['after'] = 3
    following['event'].update(id='c'*64, sequence=4, payload=message(
        provider_id='AFTER_INVALID_MESSAGE'))
    receipts = deliver_over_http(monkeypatch,invalid,invalid,following)
    assert [(status, result['sequence']) for status,result in receipts] == [(200,3),(200,3),(200,4)]

    assert db.execute("""SELECT entity_id, payload FROM saas_tenant_audit_events
      WHERE tenant_id=%s AND action='whatsapp_native_projection_rejected'""", (A,)) == [{
        'entity_id':'b'*64, 'payload':{'channel_id':CA,'generation':invalid['scope']['generation'],
                                      'sequence':3,'reason':'native_projection_invalid'}}]
    assert db.execute("""SELECT sequence FROM saas_whatsapp_native_inbox
      WHERE tenant_id=%s AND channel_id=%s AND event_id=%s""", (A,CA,'b'*64)) == [{'sequence':3}]
    assert db.execute("""SELECT raw->>'externalMessageId' AS id FROM saas_crm_messages
      WHERE raw->>'externalMessageId' IN ('INVALID_MESSAGE','AFTER_INVALID_MESSAGE')""") == [
        {'id':'AFTER_INVALID_MESSAGE'}]
    db.commit()


def test_projection_does_not_acknowledge_tampered_evidence(ready,monkeypatch):
    db, _ = ready
    activate(db)
    value = delivery()
    value['after'] = 2
    value['event'].update(id='b'*64, sequence=3, payload=message(provider_id='TAMPERED_MESSAGE'))
    store(value)
    db.execute('UPDATE saas_whatsapp_native_inbox SET encrypted_payload=%s WHERE event_id=%s', (b'not authentic','b'*64))
    db.commit()

    assert deliver_over_http(monkeypatch,value) == [(409,{'error':'native_inbox_evidence_invalid'})]
    assert not db.execute("SELECT 1 FROM saas_tenant_audit_events WHERE action='whatsapp_native_projection_rejected'")
    assert not db.execute("SELECT 1 FROM saas_crm_messages WHERE raw->>'externalMessageId'='TAMPERED_MESSAGE'")
    db.commit()
