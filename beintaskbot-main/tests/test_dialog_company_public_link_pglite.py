"""Current route/CRM/Operator authorization via disposable synthetic SQL."""
import uuid

import pytest

import tenant_beinsystems_operator as operator
import tenant_dialog_company_binding as binding
import tenant_dialog_company_public_link as link
from test_dialog_lifecycle_company_completion import companies
from test_dialog_lifecycle_pglite import episodes, command
from test_tenant_support_inbox import A, CA, CB, PROFILE, db

pytest_plugins = ('test_tenant_support_inbox',)


def test_owned_closed_has_exact_access_but_other_operator_and_pending_do_not(companies, monkeypatch):
    database, company, _, _, _ = companies
    monkeypatch.setattr(binding, '_connect', lambda: database)
    monkeypatch.setattr(operator, '_connect', lambda: database)
    monkeypatch.setattr(operator, '_ensure_schema', lambda conn: None)
    # Grant staff Operator-data read; rejection must still be exact lifecycle
    # ownership, not the absence of an unrelated entitlement.
    database.execute("""UPDATE saas_tenant_members SET
        permissions='["dialogs","support","operator_data"]'::jsonb
        WHERE tenant_id=%s::uuid AND telegram_id=33""",(A,))
    database.execute("""INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value)
        VALUES(%s::uuid,'modules','{"operator_data":true}'::jsonb)
        ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value""",(A,))
    database.execute("""INSERT INTO saas_beinsystems_operator_dialog_company_bindings
        (tenant_id,channel_id,conversation_id,company_id,created_by,updated_by)
        VALUES(%s::uuid,%s::uuid,'client',%s::uuid,11,11)""",(A,CA,company))
    database.commit()
    command()
    assert command('close',1)['lifecycle']['state']=='closed'
    route = dict(provider='connect',lead_id=41,channel_id=CA,conversation_id='client',
                 expected_company_id=company)
    # Accounts-only access cannot authorize a task POST, even for the owner.
    assert link.lookup_link(PROFILE, **route) == {'state':'not_connected'}
    instance=str(uuid.uuid4())
    generation='synthetic-generation'
    import json
    metadata=json.dumps({'generation':generation,'connection_instance_id':instance})
    database.execute("""INSERT INTO saas_tenant_integrations
        (tenant_id,provider,status,account_domain,metadata)
        VALUES(%s::uuid,%s,'connected',%s,%s::jsonb)""",
        (A,operator.PROVIDER,operator.OPERATOR_API_BASE_URL,metadata))
    database.commit()
    assert link.lookup_link(PROFILE, **route) == {'state':'not_linked'}
    database.execute("""INSERT INTO saas_beinsystems_operator_company_imports
        (tenant_id,external_account,company_id,created_by,updated_by,
          last_seen_connection_instance_id,last_seen_generation)
        VALUES(%s::uuid,'synthetic-account',%s::uuid,11,11,%s::uuid,%s)""",
        (A,company,instance,generation))
    database.commit()
    credentials={'base_url':operator.OPERATOR_API_BASE_URL,'token':'synthetic',
                 'generation':generation,'connection_instance_id':instance}
    monkeypatch.setattr(operator,'_credentials',lambda tenant: credentials)
    calls=[]
    def post(account, **kw):
        calls.append((account,kw))
        return 'https://1000.az/abc123'
    monkeypatch.setattr(link,'_lookup_public_link',post)
    with pytest.raises(operator.OperatorError) as not_owner:
        link.lookup_link({'tenant_id':A,'telegram_id':33}, **route)
    assert not_owner.value.status in {403,404}
    with pytest.raises(operator.OperatorError):
        link.lookup_link(PROFILE, **{**route,'channel_id':CB})
    assert not calls
    assert link.lookup_link(PROFILE, **route)=={'state':'available','url':'https://1000.az/abc123'}
    assert calls==[('synthetic-account', {'base_url':operator.OPERATOR_API_BASE_URL,'token':'synthetic'})]
    assert database.execute("""SELECT endpoint,request_count FROM saas_beinsystems_operator_rate_limits
        WHERE tenant_id=%s::uuid AND actor_telegram_id=11""",(A,)) == [{'endpoint':'task_list','request_count':1}]
    assert database.execute("""SELECT COUNT(*) AS n FROM saas_tenant_audit_events
        WHERE tenant_id=%s::uuid AND action='operator_read_requested'
          AND entity_id='dialog_company_public_link' AND payload::text NOT LIKE '%%synthetic-account%%'""",(A,)) == [{'n':1}]
    assert database.execute("""SELECT state FROM saas_dialog_workflows
        WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id='client'""",(A,CA)) == [{'state':'closed'}]
