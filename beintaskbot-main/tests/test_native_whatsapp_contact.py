"""Native WhatsApp contact identity tests use the isolated PostgreSQL fixture."""
import uuid
import pytest
from test_tenant_support_inbox import database, db, A, B, CA, CB
import tenant_crm_native_contacts as contacts


def test_direct_contact_idempotent_across_channels_and_keeps_history_link(db):
    other_channel = str(uuid.uuid4())
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Second')", (A, other_channel))
    with db.cursor() as cur:
        first = contacts.upsert_whatsapp_contact(cur, A, CA, '994501234567@s.whatsapp.net',
            '994501234567', 'Aysel', 'msg-1')
        replay = contacts.upsert_whatsapp_contact(cur, A, CA, '994501234567@s.whatsapp.net',
            '994501234567', 'Changed display name', 'msg-1')
        second_channel = contacts.upsert_whatsapp_contact(cur, A, other_channel,
            '994501234567@s.whatsapp.net', '994501234567', '', 'msg-2')
    assert first == replay == second_channel
    assert db.execute("SELECT count(*) AS n FROM saas_crm_native_contacts WHERE tenant_id=%s::uuid", (A,))[0]['n'] == 1
    assert db.execute("SELECT count(*) AS n FROM saas_crm_native_contact_conversations WHERE tenant_id=%s::uuid", (A,))[0]['n'] == 2
    db.commit()


def test_matches_existing_contact_by_number_but_never_by_name_or_other_tenant(db):
    contact_id = str(uuid.uuid4())
    db.execute("INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name,phone) VALUES(%s::uuid,%s::uuid,'Known','+994 50 123 4567')", (contact_id, A))
    db.execute("INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name,phone) VALUES(%s::uuid,%s::uuid,'Aysel','')", (str(uuid.uuid4()), B))
    with db.cursor() as cur:
        got = contacts.upsert_whatsapp_contact(cur, A, CA, '994501234567@s.whatsapp.net', '994501234567', 'Aysel', 'm')
        missing = contacts.upsert_whatsapp_contact(cur, B, CB, '994501234567@s.whatsapp.net', '994501234567', 'Known', 'm')
    assert got == contact_id
    assert missing != contact_id
    assert db.execute("SELECT count(*) AS n FROM saas_crm_native_contacts WHERE tenant_id=%s::uuid", (A,))[0]['n'] == 1
    db.commit()


@pytest.mark.parametrize('phone,conversation', [('', '994501234567@s.whatsapp.net'), ('123', '123@g.us'), ('994501234567', 'unknown')])
def test_rejects_unverified_or_group_identity(db, phone, conversation):
    with db.cursor() as cur:
        assert contacts.upsert_whatsapp_contact(cur, A, CA, conversation, phone, 'Name', 'm') is None
    assert db.execute("SELECT count(*) AS n FROM saas_crm_native_contacts WHERE tenant_id=%s::uuid", (A,))[0]['n'] == 0
    db.commit()
