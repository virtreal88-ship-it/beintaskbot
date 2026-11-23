"""Exact route name projection for visible dialogs; no CRM directory grant."""
import json
from pathlib import Path
import subprocess
import uuid

import pytest

import tenant_crm_native_contacts as contacts
import tenant_operator_dialogs as dialogs
from tenant_crm_native_access import CRMAccessError
from tenant_linear_policy import TenantLinearError
from test_tenant_support_inbox import A, B, CA, CB, PROFILE, database, db


EMPLOYEE = {'tenant_id': A, 'telegram_id': 33}
OTHER_OWNER = {'tenant_id': B, 'telegram_id': 22}
JID = '99234567890@lid'


def _link(db, tenant, channel, title, *, provider='connect', conversation=JID, archived=False):
    contact_id = str(uuid.uuid4())
    db.execute("""INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name,phone,email,archived_at)
      VALUES(%s::uuid,%s::uuid,%s,'+990123456789','private@example.test',
        CASE WHEN %s THEN now() END)""", (contact_id, tenant, title, archived))
    db.execute("""INSERT INTO saas_crm_native_contact_conversations(tenant_id,provider,channel_id,conversation_id,contact_id)
      VALUES(%s::uuid,%s,%s::uuid,%s,%s::uuid)""", (tenant, provider, channel, conversation, contact_id))
    return contact_id


@pytest.fixture
def scoped(db, monkeypatch):
    monkeypatch.setattr(dialogs, '_connect', lambda: db)
    monkeypatch.setattr(contacts, '_connect', lambda: db)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    db.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions)
      VALUES(%s::uuid,33,'operator','Synthetic employee','["dialogs"]'::jsonb)""", (A,))
    policy = {'33': {'access_scopes': {'dialogs': {'view': 'self'}, 'customers': {'view': 'none'}}}}
    db.execute("""INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value)
      VALUES(%s::uuid,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value""",
      (A, json.dumps(policy)))
    db.execute("""INSERT INTO saas_dialog_source_members(tenant_id,provider,source_id,employee_id,updated_by)
      VALUES(%s::uuid,'connect',%s::uuid,33,11)""", (A, CA))
    db.execute("""INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name)
      VALUES(%s::uuid,%s::uuid,'Unassigned source')""", (A, CB))
    for tenant, channel, lead in ((A, CA, 251), (A, CB, 252), (B, CB, 253)):
        db.execute("""INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,
          conversation_kind,contact_name,display_name,last_message_at)
          VALUES(%s::uuid,%s::uuid,%s,%s,'contact',%s,%s,now())""", (tenant, channel, JID, lead, JID, JID))
    db.commit()
    return db


def _listed(profile, channel=CA, q=''):
    return next((row for row in dialogs.listing(profile, search=q)['dialogs']
                 if row['conversation_id'] == JID and row['channel_id'] == channel), None)


def _open(profile, channel=CA, lead=251):
    return dialogs.history(profile, 'connect', lead, channel, JID)['dialog']


def test_employee_sees_display_name_on_authorized_route_but_not_contacts_directory(scoped):
    exact_contact = _link(scoped, A, CA, 'Synthetic exact display name')
    scoped.commit()
    for profile in (PROFILE, EMPLOYEE):
        for dialog in (_listed(profile), _open(profile)):
            assert dialog['customer_name'] == 'Synthetic exact display name'
            assert not dialog.get('phone')
            assert 'private@example.test' not in str(dialog)
            assert exact_contact not in str(dialog)
        assert _listed(profile, q='exact display')['customer_name'] == 'Synthetic exact display name'
    for get_contact in (
        lambda: contacts.get_contact(A, exact_contact, actor_id=33),
        lambda: contacts.list_contacts(A, actor_id=33),
    ):
        with pytest.raises(CRMAccessError) as denied:
            get_contact()
        assert denied.value.status == 403


def test_no_name_from_other_channel_tenant_provider_archived_or_other_dialog(scoped):
    _link(scoped, A, CB, 'Hidden source name')
    _link(scoped, B, CB, 'Other tenant name')
    _link(scoped, B, CA, 'Foreign exact keys')
    _link(scoped, A, CA, 'Wrong provider name', provider='kommo')
    _link(scoped, A, CA, 'Different dialogue name', conversation='different@lid')
    _link(scoped, A, CA, 'Archived contact name', archived=True)
    scoped.commit()
    assert _listed(EMPLOYEE)['customer_name'] == _open(EMPLOYEE)['customer_name'] == JID
    assert _listed(EMPLOYEE, channel=CB) is None
    for wrong in ('Hidden source', 'Other tenant', 'Foreign exact keys', 'Wrong provider', 'Different dialogue', 'Archived contact'):
        assert _listed(EMPLOYEE, q=wrong) is None
    with pytest.raises(TenantLinearError):
        _open(EMPLOYEE, channel=CB, lead=252)
    assert _listed(PROFILE, channel=CB)['customer_name'] == 'Hidden source name'
    assert _listed(OTHER_OWNER, channel=CB)['customer_name'] == 'Other tenant name'


def test_local_override_precedes_link_and_technical_crm_labels_are_not_projected(scoped):
    _link(scoped, A, CA, 'private@example.test')
    scoped.commit()
    assert _listed(EMPLOYEE)['customer_name'] == JID
    scoped.execute("""UPDATE saas_crm_native_contacts SET display_name='+990123456789'
        WHERE tenant_id=%s::uuid""", (A,))
    scoped.commit()
    assert _open(EMPLOYEE)['customer_name'] == JID
    scoped.execute("""UPDATE saas_crm_native_contacts SET display_name='Ordinary linked name'
        WHERE tenant_id=%s::uuid""", (A,))
    scoped.execute("""UPDATE saas_connect_conversations SET manual_display_name='Manual boundary name'
        WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s""", (A, CA, JID))
    scoped.commit()
    assert _listed(EMPLOYEE)['customer_name'] == _open(EMPLOYEE)['customer_name'] == 'Manual boundary name'


@pytest.mark.parametrize('placeholder', ['  \t\n', ' '+JID+'\t', '+99234567890'])
def test_preferred_placeholder_does_not_mask_later_route_local_name(scoped, placeholder):
    scoped.execute("""UPDATE saas_connect_conversations SET manual_display_name=%s,contact_name=%s,
        display_name='Same route known name' WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid
        AND conversation_id=%s""", (placeholder, placeholder, A, CA, JID))
    scoped.commit()
    for profile in (PROFILE, EMPLOYEE):
        assert _listed(profile)['customer_name'] == _open(profile)['customer_name'] == 'Same route known name'


@pytest.mark.parametrize(('rename', 'restore'), [
    ('ALTER TABLE saas_crm_native_contact_conversations RENAME TO synthetic_crm_links_old',
     'ALTER TABLE synthetic_crm_links_old RENAME TO saas_crm_native_contact_conversations'),
    ('ALTER TABLE saas_crm_native_contacts RENAME COLUMN display_name TO synthetic_crm_label_old',
     'ALTER TABLE saas_crm_native_contacts RENAME COLUMN synthetic_crm_label_old TO display_name'),
])
def test_optional_crm_schema_absent_or_partial_does_not_break_dialogues(scoped, rename, restore):
    _link(scoped, A, CA, 'Existing linked contact')
    scoped.commit()
    assert _listed(EMPLOYEE)['customer_name'] == 'Existing linked contact'
    scoped.execute("""UPDATE saas_connect_conversations SET contact_name='   ',display_name='Route fallback name'
        WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s""", (A, CA, JID))
    scoped.execute(rename)
    scoped.commit()
    try:
        for profile in (EMPLOYEE, PROFILE):
            assert _listed(profile)['customer_name'] == _open(profile)['customer_name'] == 'Route fallback name'
    finally:
        scoped.rollback()
        scoped.execute(restore)
        scoped.commit()


def _retained_alias(db, identity, lead, age, names, *, active=True, kind='contact'):
    db.execute("""INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,
      conversation_kind,manual_display_name,contact_name,display_name,created_at)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,now()-(%s*interval '1 day'))""",
      (A, CA, identity, lead, kind, *names, age))
    db.execute("""INSERT INTO saas_connect_dialog_links(tenant_id,channel_id,source_id,canonical_id,source_lead,
      canonical_lead,pair_id,active,snapshot) VALUES(%s::uuid,%s::uuid,%s,%s,%s,251,'synthetic',%s,'{}')""",
      (A, CA, identity, JID, lead, active))


def _application_labels(list_dialog, open_dialog, message):
    source = (Path(__file__).resolve().parents[1] / 'docs/tenant-operator-dialogs.js').read_text()
    declarations = []
    for signature in ('const safeName=', 'const isDialogGroup=', 'function messageAuthor('):
        declarations.append(next(line.strip() for line in source.splitlines() if line.strip().startswith(signature)))
    for signature in ('  const usableDialogName=', '  const hasContactName=', '  const dialogTitle='):
        start = source.index(signature)
        end = source.index('\n  };', start) + len('\n  };')
        declarations.append(source[start:end])
    script = '\n'.join(declarations) + """
        const t=key=>({whatsappContact:'Unknown contact',customer:'Customer'})[key];
        const contactDetails=()=>null;
        const displayDialogPhone=row=>row.phone||'';
        const [row,header,message]=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
        console.log(JSON.stringify([dialogTitle(row),dialogTitle(header),messageAuthor(message)]));
    """
    return json.loads(subprocess.check_output(
        ['node', '-e', script], input=json.dumps([list_dialog, open_dialog, message]), text=True))


@pytest.mark.parametrize('later_name', ['Synthetic retained customer', '99123456785@lid'])
def test_retained_alias_choices_and_incoming_footer(scoped, later_name):
    older = '99123456781@lid'
    newer = '99123456785@lid'
    _retained_alias(scoped, '99123456780@lid', 254, 40, ('Inactive name', '', ''), active=False)
    _retained_alias(scoped, older, 255, 30, ('  \t', older, '+99123456781'))
    _retained_alias(scoped, newer, 256, 20, (newer, '  '+later_name+'  ', '+99123456785'))
    message_id = str(uuid.uuid4())
    scoped.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,author_name)
      VALUES(%s::uuid,%s::uuid,255,'synthetic-alias-message',%s,'incoming','Synthetic customer message',%s)""",
      (message_id, A, 'connect:'+CA, older))
    scoped.commit()
    for profile in (PROFILE, EMPLOYEE):
        page = dialogs.history(profile, 'connect', 251, CA, JID)
        message = next(row for row in page['messages'] if row['id'] == message_id)
        expected_name = later_name if '@lid' not in later_name else ''
        assert _listed(profile)['customer_name'] == page['dialog']['customer_name'] == (expected_name or JID)
        assert message['author_name'] == expected_name
        assert _application_labels(_listed(profile), page['dialog'], message) == (
            [expected_name]*3 if expected_name else ['Unknown contact', 'Unknown contact', 'Customer'])


def test_message_formatter_preserves_independently_named_authors():
    direct = {'direction': 'incoming', 'channel': 'connect:synthetic', 'author_name': 'Actual message author'}
    assert dialogs._message(direct, contact_name=JID, contact_route=JID)['author_name'] == direct['author_name']
    assert dialogs._message(direct, group=True, contact_name='Unrelated')['author_name'] == direct['author_name']
    employee = {**direct, 'raw': {'verified_employee_id': 99}}
    assert dialogs._message(employee, contact_name='Unrelated')['author_name'] == direct['author_name']
    assert dialogs._message({**direct, 'channel': 'kommo'}, contact_name='Legacy route name')['author_name'] == 'Legacy route name'



def test_legacy_direct_aliases_still_resolve_but_never_use_group_names(scoped):
    # Existing direct rows may not have a populated conversation_kind.
    # The route binds both source and canonical ids and lead numbers.
    scoped.execute("""UPDATE saas_connect_conversations SET conversation_kind=''
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s""", (A, CA, JID))
    _retained_alias(scoped, '12345678901@g.us', 257, 40, ('Wrong legacy group', '', ''), kind='')
    _retained_alias(scoped, 'synthetic-explicit-group', 258, 30, ('Wrong explicit group', '', ''), kind='group')
    _retained_alias(scoped, 'synthetic-legacy-direct', 259, 20, ('', 'Legacy direct display', ''), kind='')
    scoped.commit()
    for profile in (PROFILE, EMPLOYEE):
        assert _listed(profile)['customer_name'] == _open(profile)['customer_name'] == 'Legacy direct display'


@pytest.mark.parametrize("name", ["@example_fixture", "Synthetic sender"])
def test_first_list_shows_exact_verified_phone_and_inbound_name_without_opening(scoped,name):
    scoped.execute("""UPDATE saas_connect_conversations SET contact_name=%s
        WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s""",(name,A,CA,JID))
    scoped.execute("""CREATE TABLE IF NOT EXISTS saas_connect_direct_identities
        (tenant_id UUID,connect_channel_id UUID,identity_key TEXT,canonical_conversation_id TEXT)""")
    for tenant,channel,digits in ((A,CB,'441100000111'),(B,CB,'441100000222')):
        scoped.execute("""INSERT INTO saas_connect_direct_identities
          (tenant_id,connect_channel_id,identity_key,canonical_conversation_id) VALUES (%s,%s,%s,%s)""",
          (tenant,channel,digits+'@s.whatsapp.net',JID))
    scoped.commit()
    first=_listed(EMPLOYEE)
    assert first['customer_name']==name and not first.get('phone')
    for jid in ('441100000333@s.whatsapp.net','441100000333@c.us'):
        scoped.execute("""INSERT INTO saas_connect_direct_identities
          (tenant_id,connect_channel_id,identity_key,canonical_conversation_id) VALUES (%s,%s,%s,%s)""",
          (A,CA,jid,JID))
    scoped.commit()
    assert _listed(EMPLOYEE)['phone']=='+441100000333'

    scoped.execute("""INSERT INTO saas_connect_direct_identities
      (tenant_id,connect_channel_id,identity_key,canonical_conversation_id) VALUES (%s,%s,%s,%s)""",
      (A,CA,'441100000444@s.whatsapp.net',JID))
    scoped.commit()
    assert not _listed(EMPLOYEE).get('phone'), 'distinct aliases are ambiguous'
    scoped.execute("""UPDATE saas_connect_conversations SET phone='441100000555'
        WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s""",(A,CA,JID))
    scoped.commit()
    assert _listed(EMPLOYEE)['phone']=='441100000555', 'a stored phone is not overwritten'
    scoped.execute("""INSERT INTO saas_connect_identity_conflicts
      (tenant_id,connect_channel_id,conversation_id,event_id,evidence) VALUES (%s,%s,%s,'fixture','{}')""",
      (A,CA,JID))
    try:
        scoped.commit()
        listed=_listed(EMPLOYEE)
        assert listed['contact_identity_conflict'] and not listed['phone']
        assert listed['customer_name']==name
        assert _open(EMPLOYEE)['phone']=='441100000555', 'history projection remains unchanged'
    finally:
        scoped.rollback()
        scoped.execute("""DELETE FROM saas_connect_identity_conflicts
          WHERE tenant_id=%s AND connect_channel_id=%s AND conversation_id=%s""",(A,CA,JID))
        scoped.commit()
