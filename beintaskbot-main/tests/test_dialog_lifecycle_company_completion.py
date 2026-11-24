"""Completion-only company gate in disposable SQL fixtures. No provider traffic."""
import json
import uuid

import pytest

import tenant_dialog_company_completion as completion
import tenant_dialog_lifecycle as lifecycle
from tenant_linear_policy import TenantLinearError
from test_dialog_lifecycle_pglite import episodes, command, payload, state
from test_tenant_support_inbox import A, B, CA, CB, PROFILE, db

pytest_plugins=('test_tenant_support_inbox',)
STAFF={'tenant_id':A,'telegram_id':33}


@pytest.fixture
def companies(episodes,monkeypatch):
    from tenant_beinsystems_operator_schema import migrate_operator_identity_links
    from tenant_connect_transport import _schema
    from tenant_crm_native_schema import migrate_native_contacts
    _schema(episodes)
    with episodes.cursor() as cur:
        migrate_native_contacts(cur)
        migrate_operator_identity_links(cur)
    ids=[str(uuid.uuid4()) for _ in range(4)]
    for ident, tenant, actor in zip(ids,(A,A,B,A),(33,33,22,11)):
        episodes.execute("""INSERT INTO saas_crm_native_companies
          (id,tenant_id,name,access_responsible_id) VALUES(%s::uuid,%s::uuid,'Synthetic company',%s)""", (ident,tenant,actor))
    episodes.commit()
    monkeypatch.setenv(completion.ENV,'true')
    return episodes,*ids


def grant(episodes,monkeypatch,view='all',update='self'):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    row={'33':{'access_scopes':{'companies':{'view':view,'update':update}}}}
    episodes.execute("""INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value)
      VALUES(%s::uuid,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key)
      DO UPDATE SET value=EXCLUDED.value""", (A,json.dumps(row)))
    episodes.commit()


def bindings(database):
    rows=database.execute('SELECT tenant_id::text,channel_id::text,conversation_id,company_id::text,created_by FROM saas_beinsystems_operator_dialog_company_bindings ORDER BY channel_id')
    database.commit()
    return rows


@pytest.mark.parametrize('existing',[False,True])
def test_individual_finish_keeps_company_and_returns_controls(companies,existing):
    database, company, *_=companies
    accepted=command(actor=33)
    assert accepted['controls']['lifecycle']['can_close'] is True
    if existing:
        database.execute("""INSERT INTO saas_beinsystems_operator_dialog_company_bindings
          (tenant_id,channel_id,conversation_id,company_id,created_by,updated_by)
          VALUES(%s::uuid,%s::uuid,'client',%s::uuid,11,11)""", (A,CA,company))
        database.commit()
    before=bindings(database)
    request=payload('close',1,company_choice='unchanged')
    with pytest.raises(TenantLinearError):
        lifecycle.mutate(PROFILE,request)
    result=lifecycle.mutate(STAFF,request)
    assert result['lifecycle']['state']=='closed'
    controls=result['controls']
    assert controls['can_send'] is False and controls['lifecycle']['can_close'] is False
    assert controls['lifecycle']['can_submit_case'] is True
    assert controls['closed_episode']['id']==accepted['lifecycle']['id']
    assert lifecycle.mutate(STAFF,request)==result
    assert bindings(database)==before


def test_accept_send_and_receipt_replay_and_atomic_completion(companies,monkeypatch):
    database, company, second, _, _=companies
    # A regular employee accepts with no company. Existing send guard stays unchanged.
    accept=payload()
    assert lifecycle.mutate(STAFF,accept)['lifecycle']['state']=='accepted'
    assert not bindings(database)
    with database.cursor() as cur:
        lifecycle.require_access(cur,A,33,'connect',CA,'client','update')
    database.commit()
    with pytest.raises(TenantLinearError) as missing:
        command('close',1,actor=33)
    assert missing.value.status==422 and state(database)['state']=='accepted'
    assert not bindings(database)
    grant(database,monkeypatch)
    request=payload('close',1,company_id=company)
    result=lifecycle.mutate(STAFF,request)
    assert result['lifecycle']['state']=='closed'
    assert lifecycle.mutate(STAFF,request)==result
    with pytest.raises(TenantLinearError) as collision:
        lifecycle.mutate(STAFF,{**request,'company_id':second})
    assert collision.value.status==409
    assert bindings(database)==[{'tenant_id':A,'channel_id':CA,'conversation_id':'client','company_id':company,'created_by':33}]
    assert lifecycle.mutate(STAFF,accept)['lifecycle']['operator_id']==33


def test_staff_need_both_company_permissions_and_exact_active_scope(companies,monkeypatch):
    database, company, second, foreign, outside_scope=companies
    command(actor=33)
    grant(database,monkeypatch,view='all',update='none')
    for candidate in (company, second):
        with pytest.raises(TenantLinearError) as denied:
            command('close',1,actor=33,company_id=candidate)
        assert denied.value.status==422
    grant(database,monkeypatch,view='none',update='self')
    with pytest.raises(TenantLinearError) as read_denied:
        command('close',1,actor=33,company_id=company)
    assert read_denied.value.status==422
    grant(database,monkeypatch)
    database.execute('UPDATE saas_crm_native_companies SET archived_at=now() WHERE id=%s::uuid',(second,))
    database.commit()
    for candidate in (foreign,outside_scope,second):
        with pytest.raises(TenantLinearError) as denied:
            command('close',1,actor=33,company_id=candidate)
        assert denied.value.status==422
    assert not bindings(database)
    assert state(database)['state']=='accepted'
    assert command('close',1,actor=33,company_id=company)['lifecycle']['state']=='closed'


def test_existing_binding_can_be_used_without_exposing_company_or_replacing_it(companies):
    database, company, second, _, _=companies
    command(actor=33)
    database.execute("""INSERT INTO saas_beinsystems_operator_dialog_company_bindings
      (tenant_id,channel_id,conversation_id,company_id,created_by,updated_by)
      VALUES(%s::uuid,%s::uuid,'client',%s::uuid,11,11)""", (A,CA,company))
    database.commit()
    for attempted in (second,company):
        with pytest.raises(TenantLinearError) as mismatch:
            command('close',1,actor=33,company_id=attempted)
        assert mismatch.value.status==422
    with database.cursor() as cur:
        projection=lifecycle.decorate(cur,A,33,{'provider':'connect','channel_id':CA,'conversation_id':'client','lead_id':41,'can_send':True},_can_update=True)
        assert projection['lifecycle']['company_completion']=={
            'status':'ready','required':True,'can_choose':False,'can_skip':False}
        assert company not in str(projection)
        assert completion.bulk_prelinked_routes(cur,A,33)=={(CA,'client')}
        assert completion.bulk_prelinked_routes(cur,A,11)==set()
    database.commit()
    database.execute('UPDATE saas_crm_native_companies SET archived_at=now() WHERE id=%s::uuid',(company,))
    database.commit()
    with pytest.raises(TenantLinearError) as stale:
        command('close',1,actor=33)
    assert stale.value.status==422 and state(database)['state']=='accepted'
    database.execute('UPDATE saas_crm_native_companies SET archived_at=NULL WHERE id=%s::uuid',(company,))
    database.commit()
    assert command('close',1,actor=33)['lifecycle']['state']=='closed'


def test_missing_tables_no_reply_and_manual_start_unchanged(companies,monkeypatch):
    database, _, _, _, _=companies
    inbound=database.execute("SELECT id::text AS id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND channel=%s AND direction='incoming'",(A,'connect:'+CA))[0]['id']
    database.execute('DROP TABLE saas_beinsystems_operator_dialog_company_bindings')
    database.commit()
    with pytest.raises(TenantLinearError) as old_client:
        command('dismiss',0,message_id=inbound)
    assert old_client.value.status==422
    assert state(database) is None
    assert command('accept',0)['lifecycle']['state']=='accepted'
    request=payload('dismiss',1,message_id=inbound,individual_no_reply=True)
    result=lifecycle.mutate(PROFILE,request)
    assert result['lifecycle']['state']=='accepted' and result['lifecycle']['no_reply_needed'] and lifecycle.mutate(PROFILE,request)==result
    with pytest.raises(TenantLinearError) as unavailable:
        command('close',2)
    assert unavailable.value.status==422 and state(database)['state']=='accepted'
    monkeypatch.delenv(completion.ENV)
    with monkeypatch.context() as patch:
        def unexpected(*_args):
            pytest.fail('stock completion must not inspect company schema')
        patch.setattr(completion,'_ready',unexpected)
        assert command('close',2)['lifecycle']['state']=='closed'


def test_gate_does_not_cover_other_company_workspace(companies,monkeypatch):
    from tenant_dialog_rollout import transition
    database, _, _, _, _=companies
    with database.cursor() as cur:
        transition(cur,B,mode='active',admission_enabled=True,expected_generation=0)
    database.commit()
    def unexpected(*_args):
        pytest.fail('another workspace must not inspect company schema')
    monkeypatch.setattr(completion,'_ready',unexpected)
    actor={'tenant_id':B,'telegram_id':22}
    assert lifecycle.mutate(actor,{**payload(),'channel_id':CB,'lead_id':42})['lifecycle']['state']=='accepted'
    assert lifecycle.mutate(actor,{**payload('close',1),'channel_id':CB,'lead_id':42})['lifecycle']['state']=='closed'


def test_same_group_multiple_sources_bind_only_exact_accepted_route(companies):
    database, company, _, _, _=companies
    group='123456789@g.us'
    second=str(uuid.uuid4())
    database.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Synthetic second')",(A,second))
    for channel, lead in ((CA,104),(second,105)):
        database.execute("""INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,conversation_kind)
            VALUES(%s::uuid,%s::uuid,%s,%s,'group')""",(A,channel,group,lead))
    database.commit()
    first={'channel_id':CA,'conversation_id':group,'lead_id':104}
    sibling={'channel_id':second,'conversation_id':group,'lead_id':105}
    lifecycle.mutate(PROFILE,{**payload(),**first})
    lifecycle.mutate(PROFILE,{**payload(),**sibling})
    lifecycle.mutate(PROFILE,{**payload('close',1,company_id=company),**first})
    with pytest.raises(TenantLinearError) as unlinked:
        lifecycle.mutate(PROFILE,{**payload('close',1),**sibling})
    assert unlinked.value.status==422
    assert bindings(database)==[{'tenant_id':A,'channel_id':CA,'conversation_id':group,'company_id':company,'created_by':11}]


def test_archive_repair_is_explicit_audited_and_foreign_is_not_repaired(companies,monkeypatch):
    database, old, selected, foreign, _=companies
    command(actor=33)
    grant(database,monkeypatch)
    database.execute("""INSERT INTO saas_beinsystems_operator_dialog_company_bindings
      (tenant_id,channel_id,conversation_id,company_id,created_by,updated_by)
      VALUES(%s::uuid,%s::uuid,'client',%s::uuid,11,11)""", (A,CA,foreign))
    database.commit()
    with pytest.raises(TenantLinearError) as invalid:
        command('close',1,actor=33,company_id=selected)
    assert invalid.value.status==422
    assert bindings(database)[0]['company_id']==foreign
    database.execute('UPDATE saas_beinsystems_operator_dialog_company_bindings SET company_id=%s::uuid',(old,))
    database.execute('UPDATE saas_crm_native_companies SET archived_at=now() WHERE id=%s::uuid',(old,))
    database.commit()
    with pytest.raises(TenantLinearError) as needs_choice:
        command('close',1,actor=33)
    assert needs_choice.value.status==422 and state(database)['state']=='accepted'
    request=payload('close',1,company_id=selected)
    result=lifecycle.mutate(STAFF,request)
    assert result['lifecycle']['state']=='closed' and lifecycle.mutate(STAFF,request)==result
    assert bindings(database)[0]['company_id']==selected
    events=database.execute("SELECT payload->'company_bound_on_close' AS change FROM saas_tenant_audit_events WHERE action='dialog_close'")
    assert events==[{'change':{'previous_company_id':old,'company_id':selected}}]
    database.commit()


def test_failed_close_rolls_back_new_binding_and_keeps_open_episode(companies,monkeypatch):
    database, choice, _, _, _=companies
    command(actor=33)
    grant(database,monkeypatch)
    require=completion.require_on_close
    def fail_after_bind(*args, **kwargs):
        assert require(*args, **kwargs)['company_id']==choice
        raise TenantLinearError('synthetic close failure',503)
    monkeypatch.setattr(completion,'require_on_close',fail_after_bind)
    with pytest.raises(TenantLinearError) as failure:
        command('close',1,actor=33,company_id=choice)
    assert failure.value.status==503
    assert not bindings(database)
    assert state(database)['state']=='accepted'


def test_choose_close_still_rechecks_source_revision_and_whatsapp_identity(companies,monkeypatch):
    database, choice, _, _, _=companies
    command(actor=33)
    grant(database,monkeypatch)
    scopes={'33':{'access_scopes':{'dialogs':{'view':'all','update':'self'},
                                   'companies':{'view':'all','update':'self'}}}}
    database.execute("UPDATE saas_tenant_workflow_policies SET value=%s::jsonb WHERE tenant_id=%s::uuid AND policy_key='members'",(json.dumps(scopes),A))
    database.commit()
    with pytest.raises(TenantLinearError) as source:
        command('close',1,actor=33,company_id=choice)
    assert source.value.status==403
    database.execute("""INSERT INTO saas_dialog_source_members(tenant_id,provider,source_id,employee_id,updated_by)
      VALUES(%s::uuid,'connect',%s::uuid,33,11)""",(A,CA))
    database.commit()
    with pytest.raises(TenantLinearError) as stale:
        command('close',0,actor=33,company_id=choice)
    assert stale.value.status==409
    database.execute("""UPDATE saas_crm_messages SET raw='{"conversationIdentityConflict":true,"conversationId":"client"}'::jsonb
      WHERE tenant_id=%s::uuid AND channel=%s""",(A,'connect:'+CA))
    database.commit()
    with pytest.raises(TenantLinearError) as unresolved:
        command('close',1,actor=33,company_id=choice)
    assert unresolved.value.status==422
    assert not bindings(database)
    assert state(database)['state']=='accepted'


def test_bulk_link_preview_is_not_whatsapp_identity_verification(companies,monkeypatch):
    database, company, _, _, _=companies
    command(actor=33)
    database.execute("""INSERT INTO saas_beinsystems_operator_dialog_company_bindings
      (tenant_id,channel_id,conversation_id,company_id,created_by,updated_by)
      VALUES(%s::uuid,%s::uuid,'client',%s::uuid,11,11)""", (A,CA,company))
    database.execute("""UPDATE saas_crm_messages SET raw='{"conversationIdentityConflict":true,"conversationId":"client"}'::jsonb
      WHERE tenant_id=%s::uuid AND channel=%s""",(A,'connect:'+CA))
    with database.cursor() as cur:
        assert completion.bulk_prelinked_routes(cur,A,33)=={(CA,'client')}
    database.commit()
    import tenant_operator_dialogs
    monkeypatch.setattr(tenant_operator_dialogs,'_connect',lambda:database)
    routes=tenant_operator_dialogs.listing(STAFF,completion_preview=True)['dialogs']
    route=next(row for row in routes if row['lead_id']==41)
    assert route['lifecycle']['bulk_company_gate']=='prelinked_unverified'
    with pytest.raises(TenantLinearError) as unresolved:
        command('close',1,actor=33)
    assert unresolved.value.status==422 and state(database)['state']=='accepted'
    assert bindings(database)[0]['company_id']==company


def test_old_dismiss_receipt_replays_after_opt_in(companies, monkeypatch):
    database, *_=companies
    inbound=database.execute("SELECT id::text AS id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND channel=%s AND direction='incoming'",(A,'connect:'+CA))[0]['id']
    database.commit()
    import hashlib
    import json
    from test_dialog_lifecycle_pglite import seed_legacy_dismissed
    old_request=payload('dismiss',0,message_id=inbound)
    result={'lifecycle':seed_legacy_dismissed(database,inbound)}
    digest=hashlib.sha256(json.dumps(old_request,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    database.execute("""INSERT INTO saas_dialog_lifecycle_receipts(tenant_id,request_id,actor_id,input_hash,result)
       VALUES(%s::uuid,%s::uuid,11,%s,%s::jsonb)""",(A,old_request['request_id'],digest,json.dumps(result)))
    database.commit()
    assert lifecycle.mutate(PROFILE,old_request)==result


def test_scoped_current_operator_reuses_existing_exact_history_binding(companies,monkeypatch):
    import tenant_operator_dialogs
    database, _, _, _, owner_company=companies
    command(actor=33)
    grant(database,monkeypatch,view='none',update='none')
    monkeypatch.delenv(completion.ENV)
    monkeypatch.setattr(tenant_operator_dialogs,'_connect',lambda:database)
    database.execute("""INSERT INTO saas_beinsystems_operator_dialog_company_bindings
      (tenant_id,channel_id,conversation_id,company_id,created_by,updated_by)
      VALUES(%s::uuid,%s::uuid,'client',%s::uuid,11,11)""", (A,CA,owner_company))
    database.commit()

    def read():
        return tenant_operator_dialogs.history(STAFF,'connect',41,CA,'client')['dialog']

    accepted=read()
    assert accepted['lifecycle']['operator_id']==33 and accepted['lifecycle']['can_close'] is True
    assert accepted['lifecycle']['company_completion']=={
        'status':'choice_available','required':False,'can_choose':False,'can_skip':True}
    assert accepted['company_binding']['company_id']==owner_company
    assert accepted['channel_id']==CA and accepted['conversation_id']=='client'
    with pytest.raises(TenantLinearError) as sibling:
        tenant_operator_dialogs.history(STAFF,'connect',41,CB,'client')
    assert sibling.value.status in {403,404}

    database.execute('UPDATE saas_crm_native_companies SET archived_at=now() WHERE id=%s::uuid',(owner_company,))
    database.commit()
    assert read()['company_binding']['company_id'] is None
    database.execute('UPDATE saas_crm_native_companies SET archived_at=NULL WHERE id=%s::uuid',(owner_company,))
    database.execute('DELETE FROM saas_beinsystems_operator_dialog_company_bindings WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s',(A,CA,'client'))
    database.commit()
    assert read()['company_binding']['company_id'] is None

    database.execute("""INSERT INTO saas_beinsystems_operator_dialog_company_bindings
      (tenant_id,channel_id,conversation_id,company_id,created_by,updated_by)
      VALUES(%s::uuid,%s::uuid,'client',%s::uuid,11,11)""", (A,CA,owner_company))
    database.commit()
    assert command('close',1,actor=33)['lifecycle']['state']=='closed'
    assert bindings(database)==[{'tenant_id':A,'channel_id':CA,'conversation_id':'client','company_id':owner_company,'created_by':11}]
