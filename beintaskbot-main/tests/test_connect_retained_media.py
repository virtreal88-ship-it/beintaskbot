"""Native PostgreSQL retained-route media regression; HTTP and AI gates mocked."""
import asyncio
import json
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet

from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A, B, CA, CB
from test_connect_dialog_equivalence import prepare, apply, candidate, command, JID, LID, LEADS
from test_connect_media_preview import transport as media_transport, MID
import tenant_connect_documents as documents
import tenant_connect_images as images
import tenant_connect_videos as videos
import tenant_connect_media as media
import tenant_connect_transport as transport
from tenant_linear_policy import TenantLinearError


@pytest.mark.parametrize('module,kind,mime', [
    (images, 'image', 'image/png'),
    (documents, 'document', 'application/pdf'),
    (videos, 'video', 'video/mp4'),
])
def test_post_merge_media_keeps_provider_route_and_exact_message_scope(db, monkeypatch, module, kind, mime):
    prepare(db, monkeypatch)
    apply(db, command(candidate(db)))
    cipher = Fernet(Fernet.generate_key())
    monkeypatch.setattr(transport, '_fernet', lambda: cipher)
    db.execute("""INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets)
      VALUES(%s::uuid,'connect','connected',%s)""",
      (A, cipher.encrypt(b'{"api_key":"synthetic-test-key"}')))
    # This is the persisted shape produced by ingestion after canonical routing.
    raw = {'conversationId': JID, 'originalConversationId': LID,
           '_crm_provider_conversation_id': LID, 'channelId': CA}
    message_id = str(uuid.uuid4())
    db.execute("""INSERT INTO saas_crm_messages
      (id,tenant_id,kommo_lead_id,external_id,channel,direction,message_type,raw)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s,'incoming',%s,%s::jsonb)""",
      (message_id, A, LEADS[0], 'connect:' + MID, 'connect:' + CA, kind, json.dumps(raw)))
    db.commit()
    monkeypatch.setattr(module, '_connect', lambda: db)
    monkeypatch.setattr(module, '_active' if module is videos else 'live', lambda *_: True)
    resolve = module._resolve if module is videos else module.resolve
    job = {'tenant_id': A, 'channel_id': CA, 'conversation_id': JID, 'lead_id': LEADS[0]}
    item = resolve(job, {}, MID)
    assert item['row']['raw'] == raw
    assert item['route']['conversation_id'] == JID
    _, session = media_transport(monkeypatch, headers={'Content-Type': mime}, chunks=[b'synthetic-file'])
    assert asyncio.run(media.download_media(item)) == (b'synthetic-file', mime)
    query = parse_qs(urlsplit(session.get.call_args.args[0]).query)
    assert query == {'conversationId': [LID]}
    assert item['route']['conversation_id'] == JID
    assert db.execute('SELECT raw FROM saas_crm_messages WHERE id=%s::uuid', (message_id,))[0]['raw'] == raw
    db.commit()

    # A retained source stays fenced, and caller-supplied scope cannot select a
    # different tenant, channel, lead, conversation or missing message.
    for changes, external_id in [
        ({'lead_id': LEADS[1], 'conversation_id': LID}, MID),
        ({'tenant_id': B}, MID),
        ({'channel_id': CB}, MID),
        ({'lead_id': 41}, MID),
        ({'conversation_id': 'unrelated'}, MID),
        ({}, str(uuid.uuid4())),
    ]:
        with pytest.raises((ValueError, TenantLinearError)):
            resolve({**job, **changes}, {}, external_id)

    # Even valid server provenance never relaxes persisted message ownership,
    # direction or media kind. Keep the route valid to exercise the SQL filter.
    for column, wrong, original in [
        ('tenant_id', B, A),
        ('channel', 'connect:' + CB, 'connect:' + CA),
        ('kommo_lead_id', LEADS[1], LEADS[0]),
        ('direction', 'outgoing', 'incoming'),
        ('message_type', 'text', kind),
    ]:
        db.execute('UPDATE saas_crm_messages SET ' + column + '=%s WHERE id=%s::uuid', (wrong, message_id))
        db.commit()
        with pytest.raises((ValueError, TenantLinearError)):
            resolve(job, {}, MID)
        db.execute('UPDATE saas_crm_messages SET ' + column + '=%s WHERE id=%s::uuid', (original, message_id))
        db.commit()
    assert session.get.call_count == 1
