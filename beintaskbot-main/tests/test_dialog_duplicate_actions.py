"""Synthetic browser comparisons and display metadata against the real SQL paths."""
import uuid
import pytest

import tenant_dialog_duplicate_actions as actions
import tenant_operator_dialogs as dialogs
from tenant_linear_policy import TenantLinearError
from test_connect_dependent_reconcile import seed, JID, LID
from test_tenant_support_inbox import db, database, A, B, CA, CB, PROFILE


def route(lead, identity, channel=CA):
    return {'provider': 'connect', 'channel_id': channel, 'lead_id': lead, 'conversation_id': identity}


def setup(db, monkeypatch):
    monkeypatch.setattr(actions, '_connect', lambda: db)
    monkeypatch.setattr(dialogs, '_connect', lambda: db)
    monkeypatch.setenv('TENANT_ENCRYPTION_KEY', 'synthetic-merge-key')
    monkeypatch.delenv('AI_LEARNING_MCP_CURSOR_SECRET', raising=False)
    actions.prepare_schema(db)
    db.commit()
    def forbidden(*_args,**_kwargs):
        pytest.fail('browser request attempted DDL, merge or channel-wide proof')
    import tenant_connect_phone_evidence as evidence
    import tenant_connect_identity as identity
    monkeypatch.setattr(actions,'prepare_schema',forbidden)
    monkeypatch.setattr(identity,'inventory',forbidden)
    monkeypatch.setattr(evidence,'collect',forbidden)


def activate_lifecycle(db):
    from tenant_dialog_lifecycle import migrate
    from tenant_dialog_rollout import transition
    with db.cursor() as cur:
        migrate(cur)
        transition(cur,A,mode='active',admission_enabled=True,expected_generation=0)
    db.commit()


def preview_command():
    return {'left': route(8101, JID), 'right': route(8102, LID)}


def test_browser_comparison_is_read_only_and_does_not_attempt_identity_proof(db,monkeypatch):
    setup(db,monkeypatch)
    seed(db,monkeypatch)
    db.execute("UPDATE saas_connect_conversations SET display_name='Fixture name' WHERE lead_id=8102")
    db.commit()
    activate_lifecycle(db)
    before=db.execute("SELECT count(*) AS n FROM saas_crm_messages WHERE tenant_id=%s",(A,))
    assert actions.preview(PROFILE,preview_command())=={
        'eligible':False,'conflicts':['merge_deferred'],'name_choices':[{'lead_id':8102,'display_name':'Fixture name'}]
    }
    assert db.execute("SELECT count(*) AS n FROM saas_crm_messages WHERE tenant_id=%s",(A,))==before
    assert not db.execute("SELECT 1 FROM saas_connect_dialog_links WHERE tenant_id=%s AND active",(A,))


def test_manual_phone_never_changes_route_or_merge_evidence(db, monkeypatch):
    setup(db, monkeypatch)
    lead = 9901
    lid = '992001234567@lid'
    group = '120363123456789@g.us'
    for tenant, channel, lead_id, identity, kind in ((A, CA, lead, lid, 'contact'), (A, CA, 9902, group, 'group'),
                                                    (B, CB, 9903, lid, 'contact')):
        db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,conversation_kind) VALUES(%s::uuid,%s::uuid,%s,%s,%s)",
                   (tenant, channel, identity, lead_id, kind))
    db.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body)
       VALUES(%s,%s,%s,'synthetic-manual-phone',%s,'incoming','Synthetic')""",
       (str(uuid.uuid4()), A, lead, 'connect:'+CA))
    db.commit()
    activate_lifecycle(db)
    command = route(lead, lid)
    with pytest.raises(TenantLinearError):
        actions.save_phone(PROFILE, {**command, 'phone': '+992001234567'})
    with pytest.raises(TenantLinearError):
        actions.save_phone(PROFILE, {**route(9902, group), 'phone': '+994700000009'})
    with pytest.raises(TenantLinearError):
        actions.save_phone(PROFILE, {**route(9903, lid, CB), 'phone': '+994700000009'})
    assert actions.save_phone(PROFILE, {**command, 'phone': '+994 (70) 000-00-09'}) == {'display_phone': '+994700000009'}
    listed = next(r for r in dialogs.listing(PROFILE)['dialogs'] if r['lead_id'] == lead)
    assert listed['manual_display_phone'] == '+994700000009' and not listed['phone']
    opened = dialogs.history(PROFILE, 'connect', lead, CA, lid)['dialog']
    assert opened['manual_display_phone'] == '+994700000009' and not opened['phone']
    db.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions)
        VALUES(%s,33,'operator','Synthetic operator','["dialogs"]')""",(A,))
    db.commit()
    member = {'tenant_id': A, 'telegram_id': 33}
    staff_dialog = next(r for r in dialogs.listing(member)['dialogs'] if r['lead_id'] == lead)
    assert not staff_dialog.get('manual_display_phone')
    with pytest.raises(TenantLinearError) as error:
        actions.save_phone(member, {**command, 'phone': '+994700000099'})
    assert error.value.status == 403
    assert not db.execute('SELECT 1 FROM saas_connect_direct_identities WHERE tenant_id=%s AND canonical_conversation_id=%s',(A,lid))
    assert actions.save_phone(PROFILE, {**command, 'phone': ''}) == {'display_phone': ''}


    db.execute("""INSERT INTO saas_dialog_workflows(tenant_id,channel_id,conversation_id,id,state,operator_id)
        VALUES(%s,%s,%s,%s,'closed',%s)""",(A,CA,lid,str(uuid.uuid4()),PROFILE['telegram_id']))
    db.commit()
    with pytest.raises(TenantLinearError) as error:
        actions.save_phone(PROFILE, {**command, 'phone': '+994700000009'})
    assert error.value.status == 409


def test_closed_archive_can_be_compared_but_merge_is_never_offered(db,monkeypatch):
    setup(db,monkeypatch)
    seed(db,monkeypatch)
    activate_lifecycle(db)
    db.execute("""INSERT INTO saas_dialog_workflows(tenant_id,channel_id,conversation_id,id,state,operator_id)
        VALUES(%s,%s,%s,%s,'closed',%s)""",(A,CA,LID,str(uuid.uuid4()),PROFILE['telegram_id']))
    db.commit()
    blocked=actions.preview(PROFILE,preview_command())
    assert not blocked['eligible'] and blocked['conflicts']==['merge_deferred']
    assert not db.execute("SELECT 1 FROM saas_connect_dialog_links WHERE tenant_id=%s AND active",(A,))
