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


def test_late_signed_legacy_event_keeps_native_crm_identity(ready,monkeypatch):
    db,reference=ready
    activate(db)
    consumer.consume(reference)
    from tenant_whatsapp_native_transport import migrate
    with db.cursor() as cur:
        migrate(cur)
        _,event=consumer.load(cur,reference)
    db.execute('''INSERT INTO saas_whatsapp_native_routes(tenant_id,channel_id,generation,enabled)
      VALUES(%s,%s,%s,TRUE)''', reference[:3])
    db.commit()
    first=db.execute("SELECT id,external_id FROM saas_crm_messages WHERE raw->>'externalMessageId'='SYNTHETIC_MESSAGE_1' AND tenant_id=%s",(A,))
    db.commit()
    # Signature/credential resolution is separate; exercise the common CRM path
    # with a distinct legacy event and message UUID after native ingestion.
    import json,hmac,hashlib
    from cryptography.fernet import Fernet
    cipher=Fernet(Fernet.generate_key())
    monkeypatch.setattr(transport,'_fernet',lambda:cipher)
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets) VALUES(%s,'connect','connected',%s) ON CONFLICT(tenant_id,provider) DO UPDATE SET status=EXCLUDED.status,secrets=EXCLUDED.secrets",(A,cipher.encrypt(json.dumps({'webhook_secret':'synthetic'}).encode())))
    db.commit()
    event=copy.deepcopy(event);event['id']='legacy-event-after-native';event['data']['id']='12345678-1234-4234-8234-123456789012'
    event['data'].pop('nativeEventId',None)
    raw=json.dumps(event).encode();signature='sha256='+hmac.new(b'synthetic',raw,hashlib.sha256).hexdigest()
    try:
        assert transport.ingest(raw,signature,event)
        rows=db.execute("SELECT id,external_id FROM saas_crm_messages WHERE raw->>'externalMessageId'='SYNTHETIC_MESSAGE_1' AND tenant_id=%s",(A,))
        assert rows==first
    finally:
        db.rollback();db.execute('DELETE FROM saas_whatsapp_native_routes');db.commit()


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


def test_receipt_uses_only_verified_source_scoped_pn_lid_pair(ready):
    db,reference=ready;activate(db);consumer.consume(reference);outgoing_fixture(db)
    pn='15550000002@s.whatsapp.net';lid='19990000002@lid'
    receipt_delivery(db,reference,chat=lid)
    assert db.execute("SELECT raw FROM saas_crm_messages WHERE raw->>'externalMessageId'='SYNTHETIC_MESSAGE_1'")[0]['raw'].get('status')!='read'
    # Registering the exact pair in another tenant/source is not evidence here.
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s,%s,'Other source')",(A,CB))
    for tenant,channel,lead in ((A,CB,9001),(B,CB,9002)):
        db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s,%s,%s,%s)",(tenant,channel,pn,lead))
        db.execute("INSERT INTO saas_connect_direct_identities(tenant_id,connect_channel_id,identity_key,canonical_conversation_id) VALUES(%s,%s,%s,%s)",(tenant,channel,lid,pn))
    db.commit()
    receipt_delivery(db,reference,chat=lid)
    assert db.execute("SELECT raw FROM saas_crm_messages WHERE raw->>'externalMessageId'='SYNTHETIC_MESSAGE_1'")[0]['raw'].get('status')!='read'
    from tenant_whatsapp_native_status import reconcile
    db.execute("INSERT INTO saas_connect_direct_identities(tenant_id,connect_channel_id,identity_key,canonical_conversation_id) VALUES(%s,%s,%s,%s)",(A,CA,lid,pn))
    db.commit()
    with db.cursor() as cur:reconcile(cur,A,CA,'SYNTHETIC_MESSAGE_1')
    assert db.execute("SELECT raw FROM saas_crm_messages WHERE raw->>'externalMessageId'='SYNTHETIC_MESSAGE_1'")[0]['raw']['status']=='read'
    db.commit()


def test_receipt_alias_lookup_cannot_cross_scope():
    from unittest.mock import Mock
    from tenant_whatsapp_native_status import verified_receipt_chats
    cur=Mock();cur.fetchall.side_effect=[[],[]]
    chat='15550000002@s.whatsapp.net'
    assert verified_receipt_chats(cur,A,CA,chat)=={chat}
    assert all(call.args[1][:2]==(A,CA) for call in cur.execute.call_args_list)


def test_outbound_receipt_first_keeps_media_reference_and_authorship(ready):
    import json
    db,reference=ready;activate(db);consumer.consume(reference);outgoing_fixture(db)
    db.execute("UPDATE saas_crm_messages SET author_name='Known employee',message_type='image',raw=raw||%s::jsonb WHERE raw->>'externalMessageId'='SYNTHETIC_MESSAGE_1'",(json.dumps({'verified_employee_id':11,'status':'read'}),))
    before=db.execute("SELECT id FROM saas_crm_messages WHERE raw->>'externalMessageId'='SYNTHETIC_MESSAGE_1'")
    db.commit()
    value=delivery(after=2)
    value['event']={'sequence':3,'id':'e'*64,'payload':message(from_me=True,sender='123456789@s.whatsapp.net',sender_alt='',media_kind='image',media_mime='image/png',media_size=179,message_type='image')}
    store(value)
    consumer.consume((*reference[:3],'e'*64));consumer.consume((*reference[:3],'e'*64))
    rows=db.execute("SELECT id,author_name,raw FROM saas_crm_messages WHERE raw->>'externalMessageId'='SYNTHETIC_MESSAGE_1'")
    assert len(rows)==1 and rows[0]['id']==before[0]['id']
    assert rows[0]['author_name']=='Known employee' and rows[0]['raw']['verified_employee_id']==11
    assert rows[0]['raw']['status']=='read' and rows[0]['raw']['nativeEventId']=='e'*64
    assert rows[0]['raw']['_crm_provider_conversation_id']==value['event']['payload']['chat']
    assert 'PRIVATE_MEDIA_KEY' not in json.dumps(rows[0]['raw'])
    db.commit()


def test_native_group_metadata_preserves_local_name_and_order_without_messages(ready):
    from tenant_whatsapp_native_groups import apply_metadata
    db,reference=ready;activate(db)
    group='120363123456789@g.us'
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,display_name,manual_display_name) VALUES(%s,%s,%s,'Old name','Local label')",(A,CA,group))
    before=db.execute('SELECT COUNT(*) AS n FROM saas_crm_messages')[0]['n'];db.commit()
    value=delivery(after=2)
    payload={'kind':'group_metadata','chat':group,'snapshot':{'status':'available','observedAt':'2026-11-18T12:00:00Z','group_name':'Provider group','phones':['+15550000002'],'total':2,'unresolved':1}}
    value['event']={'sequence':3,'id':'f'*64,'payload':payload};store(value)
    consumer.consume((*reference[:3],'f'*64))
    row=db.execute('SELECT display_name,manual_display_name FROM saas_connect_conversations WHERE tenant_id=%s AND connect_channel_id=%s AND conversation_id=%s',(A,CA,group))[0]
    assert row=={'display_name':'Provider group','manual_display_name':'Local label'}
    assert db.execute('SELECT COUNT(*) AS n FROM saas_crm_messages')[0]['n']==before
    db.commit()
    with db.cursor() as cur:
        older=copy.deepcopy(payload);older['snapshot'].update(observedAt='2026-11-18T11:00:00Z',group_name='Stale')
        apply_metadata(cur,value['scope'],older)
        apply_metadata(cur,{**value['scope'],'tenant':B},payload)
        apply_metadata(cur,{**value['scope'],'source':CB},payload)
    assert db.execute('SELECT display_name FROM saas_connect_conversations WHERE tenant_id=%s AND connect_channel_id=%s AND conversation_id=%s',(A,CA,group))[0]['display_name']=='Provider group'
    db.commit()


def test_invalid_group_snapshot_cannot_erase_name_or_create_conversation(ready):
    from tenant_whatsapp_native_groups import apply_metadata
    db,reference=ready;activate(db)
    scope=delivery()['scope'];group='120363123456789@g.us'
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,display_name) VALUES(%s,%s,%s,'Keep this name')",(A,CA,group))
    db.commit()
    base={'kind':'group_metadata','chat':group,'snapshot':{'status':'available','observedAt':'2026-11-18T12:00:00Z','group_name':'Provider group','phones':[],'total':1,'unresolved':1}}
    with db.cursor() as cur:
        for changes in ({'status':'unavailable'},{'group_name':''},{'phones':['+123@lid']},{'observedAt':'2099-01-01T00:00:00Z'}):
            assert apply_metadata(cur,scope,{**base,'snapshot':{**base['snapshot'],**changes}}) is False
        apply_metadata(cur,scope,{**base,'chat':'120363999999999@g.us'})
    assert db.execute('SELECT display_name FROM saas_connect_conversations WHERE tenant_id=%s AND connect_channel_id=%s AND conversation_id=%s',(A,CA,group))[0]['display_name']=='Keep this name'
    assert not db.execute('SELECT 1 FROM saas_connect_conversations WHERE conversation_id=%s',('120363999999999@g.us',))
    db.commit()
