"""Native PostgreSQL race for two close choices on one current owned episode."""
import pytest
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db
from test_dialog_lifecycle_pglite import episodes, command, payload, state
from test_dialog_lifecycle_postgres import race
from test_dialog_lifecycle_company_completion import STAFF, companies, grant, bindings


@pytest.mark.parametrize('archived',[False,True])
def test_concurrent_close_choices_never_overwrite_the_winner(companies,monkeypatch,archived):
    from test_tenant_support_inbox import A, CA
    database, left, right, _, previous=companies
    command(actor=33)
    grant(database,monkeypatch)
    if archived:
        database.execute("UPDATE saas_crm_native_companies SET archived_at=now() WHERE id=%s::uuid",(previous,))
        database.execute("""INSERT INTO saas_beinsystems_operator_dialog_company_bindings
          (tenant_id,channel_id,conversation_id,company_id,created_by,updated_by)
          VALUES(%s::uuid,%s::uuid,'client',%s::uuid,11,11)""",(A,CA,previous))
        database.commit()
    requests=[payload('close',1,company_id=choice) for choice in (left,right)]
    results=race(database,[(33,p,'mutate') for p in requests])
    assert sum(result[0]=='ok' for result in results)==1
    assert ('denied',409) in results
    result=next(result[1] for result in results if result[0]=='ok')
    assert result['lifecycle']['state']=='closed' and state(database)['revision']==2
    company=bindings(database)[0]['company_id']
    assert company in (left,right)
    from tenant_dialog_lifecycle import mutate
    assert mutate(STAFF,requests[0] if company==left else requests[1])==result
    audit=database.execute("SELECT payload->'company_bound_on_close' AS change FROM saas_tenant_audit_events WHERE action='dialog_close'")
    assert audit==[{'change':{'previous_company_id':previous if archived else None,'company_id':company}}]
    database.commit()



@pytest.mark.parametrize('required', [False, True])
def test_explicit_no_company_is_scoped_fingerprinted_and_needs_no_crm_access(companies, monkeypatch, required):
    from test_tenant_support_inbox import A, CA, PROFILE
    import tenant_dialog_company_completion as completion
    import tenant_dialog_lifecycle as lifecycle
    from tenant_linear_policy import TenantLinearError

    db, company, *_ = companies
    monkeypatch.setenv(completion.ENV, str(required).lower())
    grant(db, monkeypatch, view='none', update='none')
    command(actor=33)
    route = {'provider':'connect', 'channel_id':CA, 'conversation_id':'client', 'lead_id':41, 'can_send':True}
    with db.cursor() as cur:
        preview = lifecycle.decorate(cur, A, 33, route, _can_update=True)
    db.commit()
    assert preview['lifecycle']['can_close'] is True
    assert preview['lifecycle'].get('company_completion') == ({
        'status':'missing', 'required':True, 'can_choose':False, 'can_skip':True,
    } if required else None)

    bad_payloads = (
        {'company_choice':None}, {'company_choice':'None'},
        {'company_choice':'none', 'company_id':company},
    )
    for extra in bad_payloads:
        with pytest.raises(TenantLinearError) as denied:
            lifecycle.mutate(STAFF, payload('close', 1, **extra))
        assert denied.value.status == 422
    request = payload('close', 1, company_choice='none')
    with pytest.raises(TenantLinearError) as denied:
        lifecycle.mutate(PROFILE, request)
    assert denied.value.status in {403, 404}
    assert state(db)['state'] == 'accepted'
    result = lifecycle.mutate(STAFF, request)
    assert result['lifecycle']['state'] == 'closed'
    assert lifecycle.mutate(STAFF, request) == result
    with pytest.raises(TenantLinearError) as replay:
        lifecycle.mutate(STAFF, {**request, 'company_choice':'different'})
    assert replay.value.status == 409
    assert bindings(db) == []
    assert db.execute("SELECT payload->>'company_choice' AS choice FROM saas_tenant_audit_events WHERE action='dialog_close'") == [{'choice':'none'}]
    db.commit()


def test_explicit_none_preserves_concurrently_added_company_without_disclosure(companies, monkeypatch):
    from test_tenant_support_inbox import A, CA
    import tenant_dialog_company_completion as completion
    import tenant_dialog_lifecycle as lifecycle

    db, company, *_ = companies
    monkeypatch.delenv(completion.ENV)
    grant(db, monkeypatch, view='none', update='none')
    command(actor=33)
    db.execute("""INSERT INTO saas_beinsystems_operator_dialog_company_bindings
       (tenant_id,channel_id,conversation_id,company_id,created_by,updated_by)
       VALUES(%s::uuid,%s::uuid,'client',%s::uuid,11,11)""", (A,CA,company))
    db.commit()
    request = payload('close', 1, company_choice='none')
    result = lifecycle.mutate(STAFF, request)
    assert result['lifecycle']['state'] == 'closed'
    assert company not in str(result)
    assert bindings(db)[0]['company_id'] == company
    audit = db.execute("SELECT payload FROM saas_tenant_audit_events WHERE action='dialog_close'")
    assert len(audit) == 1 and company not in str(audit)
    db.commit()


def test_explicit_none_rejects_archived_or_unrelated_tenant_without_writes(companies, monkeypatch):
    from test_tenant_support_inbox import A, B, CA
    import tenant_dialog_company_completion as completion
    import tenant_dialog_lifecycle as lifecycle
    from tenant_beinsystems_operator_access import WORKSPACE_ENV
    from tenant_linear_policy import TenantLinearError

    db, company, *_ = companies
    monkeypatch.delenv(completion.ENV)
    grant(db, monkeypatch)
    command(actor=33)
    request = payload('close', 1, company_choice='none')
    monkeypatch.setenv(WORKSPACE_ENV, B)
    with pytest.raises(TenantLinearError) as out_of_scope:
        lifecycle.mutate(STAFF, request)
    assert out_of_scope.value.status == 422
    assert state(db)['state'] == 'accepted'
    assert not bindings(db)
    monkeypatch.setenv(WORKSPACE_ENV, A)
    db.execute("UPDATE saas_crm_native_companies SET archived_at=now() WHERE id=%s::uuid", (company,))
    db.execute("""INSERT INTO saas_beinsystems_operator_dialog_company_bindings
       (tenant_id,channel_id,conversation_id,company_id,created_by,updated_by)
       VALUES(%s::uuid,%s::uuid,'client',%s::uuid,11,11)""", (A,CA,company))
    db.commit()
    with pytest.raises(TenantLinearError) as archived:
        lifecycle.mutate(STAFF, request)
    assert archived.value.status == 422
    assert state(db)['state'] == 'accepted'
    assert bindings(db)[0]['company_id'] == company
    assert db.execute("SELECT id FROM saas_tenant_audit_events WHERE action='dialog_close'") == []
    db.commit()


@pytest.mark.parametrize('bound',[False,True])
def test_optional_readiness_is_neutral_for_row_scoped_viewer(companies,monkeypatch,bound):
    from test_tenant_support_inbox import A, CA
    from tenant_scoped_access_store import read_access_profile
    from tenant_policy import TenantPolicy
    import tenant_dialog_company_completion as completion
    import tenant_dialog_lifecycle as lifecycle

    db,company,*_ = companies
    monkeypatch.delenv(completion.ENV)
    grant(db,monkeypatch,view='self',update='self')
    command(actor=33)
    if bound:
        db.execute("""INSERT INTO saas_beinsystems_operator_dialog_company_bindings
          (tenant_id,channel_id,conversation_id,company_id,created_by,updated_by)
          VALUES(%s::uuid,%s::uuid,'client',%s::uuid,11,11)""",(A,CA,company))
        db.commit()
    route={'provider':'connect','channel_id':CA,'conversation_id':'client','lead_id':41,'can_send':True}
    with db.cursor() as cur:
        policy=TenantPolicy(read_access_profile(cur,A,33,lock=True))
        preview=lifecycle.decorate(cur,A,33,route,_can_update=True,_policy=policy)
    db.commit()
    assert preview['lifecycle']['can_close'] is True
    assert 'company_completion' not in preview['lifecycle']
    assert lifecycle.mutate(STAFF,payload('close',1,company_choice='none'))['lifecycle']['state']=='closed'
    assert len(bindings(db))==bound
    db.commit()
