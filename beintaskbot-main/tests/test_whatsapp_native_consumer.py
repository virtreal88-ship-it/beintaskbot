import copy
import pytest
from test_whatsapp_native_inbox import capture, store, delivery
from test_call_sources import managed
from test_browser_calls import gateway
from test_call_recordings import pilot
from test_tenant_support_inbox import database, db, A, B, CA, CB
from test_whatsapp_native_projection import message
from tenant_linear_policy import TenantLinearError
import tenant_whatsapp_native_consumer as consumer
import tenant_whatsapp_native_projection as projection
import tenant_connect_transport as transport


@pytest.fixture
def ready(capture, monkeypatch):
    db, cipher, cfg = capture
    monkeypatch.setattr(projection, '_fernet', lambda: cipher)
    monkeypatch.setattr(consumer, '_connect', lambda: db)
    monkeypatch.setattr(transport, '_connect', lambda: db)
    monkeypatch.setenv('BEINTASKBOT_UNIFIED_WHATSAPP_PROJECTION', '1')
    with db.cursor() as cur:
        consumer.migrate(cur)
    value = delivery()
    value['event']['payload'] = message(sender_alt='')
    store(value)
    reference = (A, CA, value['scope']['generation'], value['event']['id'])
    yield db, reference


def activate(db):
    db.execute('UPDATE saas_whatsapp_capture_generations SET projection_enabled=TRUE')
    db.commit()


def test_capture_is_not_projection_authority(ready):
    db, reference = ready
    with pytest.raises(TenantLinearError, match='native_projection_source_changed'):
        consumer.consume(reference)
    db.rollback()


def test_same_crm_path_preserves_history_and_duplicate_is_idempotent(ready):
    db, reference = ready
    activate(db)
    consumer.consume(reference)
    first = db.execute("SELECT id,external_id FROM saas_crm_messages WHERE tenant_id=%s AND raw->>'externalMessageId'='SYNTHETIC_MESSAGE_1'", (A,))
    db.commit()
    assert len(first) == 1
    consumer.consume(reference)
    second = db.execute("SELECT id,external_id FROM saas_crm_messages WHERE tenant_id=%s AND raw->>'externalMessageId'='SYNTHETIC_MESSAGE_1'", (A,))
    assert second == first
    assert db.execute("SELECT 1 FROM saas_crm_messages WHERE external_id='m1' AND tenant_id=%s", (A,))
    db.commit()


def test_rechecks_generation_and_encrypted_event_in_commit_transaction(ready):
    db, reference = ready
    activate(db)
    with db.cursor() as cur:
        _, event = consumer.load(cur, reference)
    db.commit()
    changed = copy.deepcopy(event)
    changed['data']['text'] = 'UNTRUSTED'
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError, match='native_projection_evidence_changed'):
            consumer.authorize(cur, reference, changed)
    db.rollback()
    db.execute('UPDATE saas_whatsapp_capture_generations SET projection_enabled=FALSE')
    db.commit()
    with pytest.raises(TenantLinearError):
        transport._ingest_once(b'', '', event, _native_reference=reference)
    db.rollback()


@pytest.mark.parametrize('index,value', [(0,B),(1,CB),(2,'other'),(3,'f'*64)])
def test_reference_cannot_cross_scope(ready,index,value):
    db, reference = ready
    activate(db)
    changed = list(reference)
    changed[index] = value
    with pytest.raises(TenantLinearError):
        consumer.consume(changed)
    db.rollback()


def test_legacy_provider_message_keeps_original_crm_id(ready):
    db, reference = ready
    activate(db)
    db.execute("UPDATE saas_connect_conversations SET conversation_id='15550000002@s.whatsapp.net' WHERE tenant_id=%s AND connect_channel_id=%s", (A,CA))
    db.execute('''UPDATE saas_crm_messages SET external_id='connect:LEGACY_123',
      raw='{"externalMessageId":"SYNTHETIC_MESSAGE_1"}'::jsonb
      WHERE tenant_id=%s AND external_id='m1' ''', (A,))
    original = db.execute("SELECT id FROM saas_crm_messages WHERE tenant_id=%s AND external_id='connect:LEGACY_123'", (A,))
    db.commit()
    consumer.consume(reference)
    rows = db.execute("SELECT id FROM saas_crm_messages WHERE tenant_id=%s AND raw->>'externalMessageId'='SYNTHETIC_MESSAGE_1'", (A,))
    assert rows == original
    db.commit()


def test_disabled_source_cannot_project_captured_message(ready):
    db, reference = ready
    activate(db)
    db.execute('UPDATE saas_connect_channels SET active=FALSE WHERE tenant_id=%s AND connect_channel_id=%s', (A,CA))
    db.commit()
    with pytest.raises(TenantLinearError, match='native_projection_source_changed'):
        consumer.consume(reference)
    db.rollback()


def test_crm_failure_rolls_back_event_marker_for_retry(ready, monkeypatch):
    db, reference = ready
    activate(db)
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic CRM failure')
    with monkeypatch.context() as patch:
        patch.setattr(transport, '_upsert_conversation', fail)
        with pytest.raises(RuntimeError, match='synthetic CRM failure'):
            consumer.consume(reference)
    assert not db.execute('SELECT 1 FROM saas_connect_webhook_events WHERE tenant_id=%s AND event_id=%s', (A,'native:'+reference[3]))
    db.commit()
    consumer.consume(reference)
    assert len(db.execute("SELECT id FROM saas_crm_messages WHERE tenant_id=%s AND raw->>'externalMessageId'='SYNTHETIC_MESSAGE_1'", (A,))) == 1
    db.commit()
