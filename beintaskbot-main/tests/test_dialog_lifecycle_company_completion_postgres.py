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
