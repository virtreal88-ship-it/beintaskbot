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


def receipt_delivery(db,reference,*,status='read',chat='15550000002@s.whatsapp.net',sender=None,from_me=False):
    sequence=db.execute('SELECT stored_sequence FROM saas_whatsapp_capture_generations')[0]['stored_sequence']
    db.commit()
    value=delivery(after=sequence)
    value['event']={'sequence':sequence+1,'id':format(sequence+1,'064x'),'payload':{
      'kind':'receipt','chat':chat,'sender':sender or chat,'provider_id':'SYNTHETIC_MESSAGE_1',
      'from_me':from_me,'timestamp':1700000000000,'status':status}}
    store(value)
    consumer.consume((*reference[:3],value['event']['id']))


def outgoing_fixture(db):
    db.execute("UPDATE saas_crm_messages SET direction='outgoing' WHERE raw->>'externalMessageId'='SYNTHETIC_MESSAGE_1'")
    db.commit()


def test_remote_status_monotonic_and_no_customer_turn(ready):
    db,reference=ready;activate(db);consumer.consume(reference);outgoing_fixture(db)
    before=db.execute('SELECT COUNT(*) AS n FROM saas_crm_messages')[0]['n'];db.commit()
    receipt_delivery(db,reference,status='read')
    receipt_delivery(db,reference,status='')
    row=db.execute("SELECT raw FROM saas_crm_messages WHERE raw->>'externalMessageId'='SYNTHETIC_MESSAGE_1'")[0]
    assert row['raw']['status']=='read'
    assert db.execute('SELECT COUNT(*) AS n FROM saas_crm_messages')[0]['n']==before
    db.commit()


def test_receipt_before_message_reconciles_after_echo(ready):
    db,reference=ready;activate(db)
    receipt_delivery(db,reference)
    consumer.consume(reference);outgoing_fixture(db)
    from tenant_whatsapp_native_status import reconcile
    with db.cursor() as cur:reconcile(cur,A,CA,'SYNTHETIC_MESSAGE_1')
    assert db.execute("SELECT raw FROM saas_crm_messages WHERE raw->>'externalMessageId'='SYNTHETIC_MESSAGE_1'")[0]['raw']['status']=='read'
    db.commit()


@pytest.mark.parametrize('status,from_me',[('read-self',True),('retry',False),('inactive',False),('read',True)])
def test_local_and_control_receipts_do_not_mark_customer_read(ready,status,from_me):
    db,reference=ready;activate(db);consumer.consume(reference);outgoing_fixture(db)
    receipt_delivery(db,reference,status=status,from_me=from_me)
    assert not db.execute('SELECT * FROM saas_whatsapp_native_status')
    db.commit()


def test_other_chat_receipt_cannot_update_same_provider_id(ready):
    db,reference=ready;activate(db);consumer.consume(reference);outgoing_fixture(db)
    receipt_delivery(db,reference,chat='15550000003@s.whatsapp.net')
    row=db.execute("SELECT raw FROM saas_crm_messages WHERE raw->>'externalMessageId'='SYNTHETIC_MESSAGE_1'")[0]
    assert row['raw'].get('status')!='read'
    db.commit()


def test_device_receipt_does_not_block_following_message(ready):
    db,reference=ready;activate(db);consumer.consume(reference);outgoing_fixture(db)
    receipt_delivery(db,reference,sender='15550000002:2@s.whatsapp.net',status='')
    row=db.execute("SELECT raw FROM saas_crm_messages WHERE raw->>'externalMessageId'='SYNTHETIC_MESSAGE_1'")[0]
    assert row['raw']['status']=='delivered'
    sequence=db.execute('SELECT stored_sequence FROM saas_whatsapp_capture_generations')[0]['stored_sequence'];db.commit()
    value=delivery(after=sequence)
    value['event']={'sequence':sequence+1,'id':'d'*64,'payload':message(sender_alt='',provider_id='SYNTHETIC_AFTER_DEVICE')}
    store(value);consumer.consume((*reference[:3],'d'*64))
    assert db.execute("SELECT 1 FROM saas_crm_messages WHERE raw->>'externalMessageId'='SYNTHETIC_AFTER_DEVICE'")
    db.commit()


@pytest.mark.parametrize('chat',['status@broadcast','123456789@newsletter'])
def test_non_crm_receipt_is_retained_without_stalling(ready,chat):
    db,reference=ready;activate(db)
    receipt_delivery(db,reference,chat=chat,sender='15550000002@s.whatsapp.net',status='')
    assert not db.execute('SELECT 1 FROM saas_whatsapp_native_status')
    assert len(db.execute('SELECT 1 FROM saas_whatsapp_native_inbox'))==2
    db.commit()
    consumer.consume(reference)
    assert db.execute("SELECT 1 FROM saas_crm_messages WHERE raw->>'externalMessageId'='SYNTHETIC_MESSAGE_1'")
    db.commit()
