"""Chromium -> local HTTP/store/signed webhook; all Connect and AI calls synthetic."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import socket
import subprocess
import threading
import uuid
from pathlib import Path
from unittest.mock import Mock
from cryptography.fernet import Fernet

import pytest
from aiohttp import web
from test_tenant_support_inbox import database as wasm_database, db, A, CA, AGENT, PROFILE, seed_job
from test_connect_channel_multiprocess import database as postgres_database
from test_tenant_consultation_group import setup_case, GROUP
from test_dialog_consultations import REAL_QUEUE_SEND
import tenant_consultation_group as legacy
import tenant_internal_consultations as internal
import tenant_dialog_consultations as consultations
import tenant_dialog_consultations_worker as worker
import tenant_public_case as public
import tenant_support_inbox_store as tickets
import tenant_connect_transport as transport
import tenant_operator_copilot as copilot
import tenant_episode_media as episode_media
import tenant_media_evidence as evidence
from tenant_support_inbox_api import handler
from tenant_dialog_consultations_api import handler as consultation_handler

ROOT = Path(__file__).resolve().parents[1]
ACTUAL_CONNECT_ROUTE=transport.connect_route


@pytest.fixture(scope='module')
def database():
    # CI supplies its disposable localhost PostgreSQL service. Keep the same
    # isolated WASM fallback for developers without that optional service.
    fixture = postgres_database if os.environ.get('BEINTASKBOT_TEST_POSTGRES_DSN') else wasm_database
    yield from fixture.__wrapped__()


def test_chromium_real_local_backend_signed_reply_and_unsent_draft(db, monkeypatch):
    if not (ROOT/'.support-test-deps/node_modules/playwright').exists() or not Path(os.environ.get('CHROMIUM_EXECUTABLE_PATH', '/usr/bin/chromium')).exists():
        pytest.skip('Install Playwright and Chromium to run browser integration')
    old_consult, case = setup_case(db, monkeypatch)
    seed_job(db)
    consultations.schema(db)
    db.execute("UPDATE saas_ai_agents SET mode='manual' WHERE tenant_id=%s::uuid AND id=%s::uuid", (A, AGENT))
    image, image2 = str(uuid.uuid4()), str(uuid.uuid4())
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,message_type) VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','image')", (image, A, 'connect:'+str(uuid.uuid4()), 'connect:'+CA))
    with db.cursor() as cur:
        tickets.record_case_selected_message_ids(cur, A, case, [image], 'local-browser-selected-photo')
    cipher=Fernet(Fernet.generate_key())
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets) VALUES(%s::uuid,'connect','connected',%s)",
      (A,cipher.encrypt(json.dumps({'api_key':'synthetic','webhook_secret':'synthetic-secret'}).encode())))
    # Reuse complete exact-version screenshot evidence in the private draft;
    # no browser step may download or analyze an attachment eagerly.
    with db.cursor() as cur:
        cur.execute('SELECT external_id,body,message_type,raw,direction FROM saas_crm_messages WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,image))
        ref=episode_media.reference(cur.fetchone(),CA,41)
        evidence.persist(cur,A,[ref],[{'id':ref['id'],'status':'processed','coverage':{'kind':'image','complete':True},
          'observation':'Synthetic screenshot shows the reported amount.'}],processor='isolated_fixture')
        cur.execute('SELECT count(*) AS n FROM saas_media_evidence_receipts WHERE tenant_id=%s::uuid AND external_id=%s',(A,ref['external_id']))
        assert cur.fetchone()['n']==1
    db.commit()
    if getattr(db, 'dsn', None):
        import psycopg
        from psycopg.rows import dict_row
        factory = lambda: psycopg.connect(db.dsn, row_factory=dict_row)
    else:
        factory = lambda: db
    for module in (legacy, internal, consultations, worker, public, tickets, transport,copilot,episode_media,evidence):
        monkeypatch.setattr(module, '_connect', factory)
    # setup_case has a legacy route stub; this browser test exercises the real
    # current route/credential checks against the synthetic encrypted connector.
    monkeypatch.setattr(transport,'connect_route',ACTUAL_CONNECT_ROUTE)
    monkeypatch.setattr(transport, '_fernet', lambda:cipher)
    monkeypatch.setattr(copilot, '_fernet', lambda:cipher)
    monkeypatch.setattr(transport, 'send_consultation_with_receipt', REAL_QUEUE_SEND)
    # The real transport helper validates a synthetic Connect queue receipt.
    # No actual HTTP provider, media download or model is available in this test.
    sends, unexpected = [], []
    def provider_request(_key, method, path, payload):
        if method != 'POST' or not path.endswith('/v1/channels/'+CA+'/group-messages') or payload.get('conversationId') != GROUP:
            unexpected.append((method, path)); raise AssertionError('Unexpected provider operation')
        sends.append({'request_id': payload['clientMessageId'], 'body': payload['text'], 'target': payload['conversationId'], 'queue_id': 'synthetic-queue-'+str(len(sends)+1)})
        return {'id': sends[-1]['queue_id'], 'status': 'pending'}
    monkeypatch.setattr(transport, '_request', provider_request)
    provider_http = Mock(side_effect=AssertionError('Live provider HTTP is forbidden'))
    media_download = Mock(side_effect=AssertionError('No eager media download'))
    monkeypatch.setattr(transport.requests, 'request', provider_http)
    monkeypatch.setattr(transport, 'download_connect_message_media', media_download)
    from test_pr465_workflow_integration import _enable_private_copilot
    _enable_private_copilot(db,cipher)
    draft_generation = Mock(return_value=('We are checking.',{'total_tokens':2}))
    monkeypatch.setattr(copilot,'generate',draft_generation)
    consultations.developer_marker(PROFILE, {'channel_id': CA, 'conversation_id': GROUP, 'enabled': True})
    tickets.update(PROFILE,case,{'assigned_developer_channel_id':CA,'assigned_developer_conversation_id':GROUP})
    db.execute("INSERT INTO saas_ai_group_staff(tenant_id,channel_id,jid,member_id,verified_by) VALUES(%s::uuid,%s::uuid,'15550001111@s.whatsapp.net',11,11)",(A,CA));db.commit()
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sock.listen(16)
    origin = 'http://127.0.0.1:'+str(sock.getsockname()[1])
    loop, ready, state = asyncio.new_event_loop(), threading.Event(), {}

    def signed(event):
        raw = json.dumps(event).encode()
        signature = 'sha256='+hmac.new(b'synthetic-secret', raw, hashlib.sha256).hexdigest()
        return transport.ingest(raw, signature, event)

    def latest_delivery():
        with factory() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT consultation_id::text,state FROM saas_consultation_group_deliveries WHERE tenant_id=%s::uuid AND request_id=%s::uuid', (A, sends[-1]['request_id']))
                return cur.fetchone()

    async def start():
        app = web.Application(); member = lambda _: PROFILE
        async def index(_):
            return web.Response(text='<section id="view-support"><div id="operatorSupport"></div></section><div id="modalContent"></div><script>window.tenantOperator={hasRight:()=>true}</script><script src="/i18n.js"></script><script src="/consultations.js"></script><script src="/support.js"></script>', content_type='text/html')
        async def asset(request):
            file = {'/i18n.js': 'tenant-operator-i18n.js', '/consultations.js': 'tenant-support-consultations.js', '/support.js': 'tenant-support-inbox.js'}[request.path]
            return web.Response(text=(ROOT/'docs'/file).read_text(), content_type='application/javascript')
        async def profile(_):
            return web.json_response({'member': {**PROFILE, 'role': 'owner'}})
        async def developer_reply(_):
            delivery = latest_delivery(); index = str(len(sends))
            event = {'id': 'e2e-inbound-'+index, 'type': 'message.inbound.received', 'data': {
                'id': 'e2e-reply-'+index, 'channelId': CA, 'conversationId': GROUP, 'isGroup': True, 'fromMe': False,
                'type': 'text', 'quotedExternalMessageId': 'PROVIDER_'+index, 'participantName': 'Developer',
                'participantJid':'15550001111@s.whatsapp.net',
                'text': 'CONSULT:'+delivery['consultation_id']+' We are investigating'}}
            accepted = await asyncio.to_thread(signed, event)
            thread = await asyncio.to_thread(consultations.inbox, PROFILE, case_id=case)
            return web.json_response({'accepted': accepted, 'linked_replies': len(thread['replies'])})
        async def provider_receipt(_):
            delivery = latest_delivery(); item = sends[-1]; index = str(len(sends))
            event = {'id': 'e2e-outbound-'+index, 'type': 'message.outbound.observed', 'data': {
                'id': item['queue_id'], 'channelId': CA, 'conversationId': GROUP, 'isGroup': True, 'fromMe': True,
                'type': 'text', 'externalMessageId': 'PROVIDER_'+index, 'originClientMessageId': item['request_id'], 'text': item['body']}}
            assert await asyncio.to_thread(signed, event)
            linked = await asyncio.to_thread(worker.reconcile)
            assert await asyncio.to_thread(worker.reconcile) == 0
            return web.json_response({'state': latest_delivery()['state'], 'reconciled': linked, 'consultation_id': delivery['consultation_id']})
        async def client_attachment(_):
            with factory() as conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,message_type) VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','image')", (image2, A, 'connect:'+str(uuid.uuid4()), 'connect:'+CA))
                conn.commit()
            return web.json_response({'accepted': True})
        app.router.add_get('/app', index)
        for path in ('/i18n.js', '/consultations.js', '/support.js'):
            app.router.add_get(path, asset)
        app.router.add_get('/api/platform/me', profile)
        app.router.add_post('/simulate/reply', developer_reply)
        app.router.add_post('/simulate/receipt', provider_receipt)
        app.router.add_post('/simulate/client-attachment', client_attachment)
        for suffix, resource, methods in [('support-tickets', 'tickets', ('GET',)), ('support-tickets/{ticket_id}', 'ticket', ('GET',)), ('support-tickets/{ticket_id}/messages', 'ticket_messages', ('GET', 'POST'))]:
            for method in methods:
                app.router.add_route(method, '/api/platform/ai/'+suffix, handler(member, origin, logging.getLogger(__name__), resource))
        for suffix, resource, methods in [('preview', 'preview', ('POST',)), ('destinations', 'destinations', ('GET',)), ('replies', 'replies', ('GET',)), ('', 'prepare', ('POST',)), ('{id}/dispatch', 'dispatch', ('POST',)), ('replies/{id}/draft', 'draft', ('POST',))]:
            for method in methods:
                app.router.add_route(method, '/api/platform/ai/dialog-consultations'+('/'+suffix if suffix else ''), consultation_handler(member, origin, logging.getLogger(__name__), resource))
        runner = web.AppRunner(app); await runner.setup(); await web.SockSite(runner, sock).start()
        state['runner'] = runner; ready.set()

    def run():
        asyncio.set_event_loop(loop); loop.run_until_complete(start()); loop.run_forever()
    thread = threading.Thread(target=run, daemon=True); thread.start(); assert ready.wait(10)
    try:
        env = {**os.environ, 'GROUP_E2E_ORIGIN': origin, 'GROUP_E2E_CASE': case, 'GROUP_E2E_OLD_CONSULT': old_consult, 'GROUP_E2E_IMAGE': image, 'GROUP_E2E_IMAGE2': image2}
        completed = subprocess.run(['node', str(ROOT/'tests/support_group_local_e2e.cjs')], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        assert completed.returncode == 0, completed.stdout+'\n'+completed.stderr
        assert len(sends) == 2 and all(item['target'] == GROUP for item in sends)
        assert len({item['request_id'] for item in sends}) == 2
        with factory() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT state,provider_message_id FROM saas_consultation_group_deliveries WHERE tenant_id=%s::uuid ORDER BY created_at', (A,))
                assert [(row['state'], row['provider_message_id']) for row in cur.fetchall()] == [('sent', 'PROVIDER_1'), ('sent', 'PROVIDER_2')]
                cur.execute('SELECT selected_media_ids FROM saas_public_case_shares WHERE tenant_id=%s::uuid ORDER BY created_at,id', (A,))
                assert [row['selected_media_ids'] for row in cur.fetchall()] == [[image], [image2]]
        first = consultations.inbox(PROFILE, case_id=case)['replies'][-1]
        assert first['draft'] == 'We are checking.' and first['body'].endswith('We are investigating')
        assert draft_generation.call_count == 1
        assert len(consultations.inbox(PROFILE, case_id=case)['replies']) == 2
        # The first reply was explicitly drafted; the second fresh Manual
        # reply remains in its private queue and has sent nothing to a client.
        replies=consultations.inbox(PROFILE,case_id=case)['replies']
        assert {item['id'] for item in worker.pending()}=={replies[0]['id']}
        assert replies[0]['draft_state']=='pending' and replies[-1]['draft_state']=='completed'
        assert not provider_http.called and not media_download.called and not unexpected
        for item in sends:
            token = re.search(r'/public/case/([A-Za-z0-9_-]+)', item['body']).group(1)
            snapshot = public.read(token)
            assert len(snapshot['selected_media_ids']) == 1
            assert [message['id'] for message in snapshot['public_messages']] == snapshot['selected_media_ids']
    finally:
        asyncio.run_coroutine_threadsafe(state['runner'].cleanup(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop); thread.join(timeout=10); loop.close()
