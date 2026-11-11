"""Real transaction fences on disposable localhost PostgreSQL; no provider I/O."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet
from test_employee_card_consistency_postgres import cards
from test_company_employees_navigation_postgres import A, B, database, db, staff
import tenant_shared_integration_access as access
import tenant_bein_integration as bein
import tenant_integration_store as integrations
import tenant_chat_ai_settings as chat
from tenant_linear_policy import TenantLinearError


@pytest.fixture
def shared(cards,monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    cipher=Fernet(Fernet.generate_key())
    for module in (bein,integrations,chat):
        monkeypatch.setattr(module,'_connect',cards['factory'])
        monkeypatch.setattr(module,'_fernet',lambda:cipher)
    with cards['factory']() as conn:
        conn.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value",(A,json.dumps({'61':{'access_scopes':{'integrations':dict.fromkeys(('view','create','update','delete'),'all')}}})))
    cards['profile']={'tenant_id':A,'telegram_id':61,'role':'owner'}
    cards['data']={'expected_tenant_id':A,'expected_user_id':'61','confirm_company_impact':True}
    return cards


def test_bein_manual_and_chat_mutations_use_current_nonowner_scopes(shared):
    f=shared;p=f['profile'];data=f['data']
    assert bein.save(p,token='bei_int_synthetic_token_never_sent',authorization=data)['status']=='connected'
    integrations.save_kommo_manual_token(tenant_id=A,owner_id=61,account_domain='synthetic.kommo.com',access_token='synthetic-never-sent',authorization=data)
    rows=integrations.list_integrations(tenant_id=A,telegram_id=61)
    assert {row['provider'] for row in rows}>={'kommo','beinsystems','chat_ai','linear','connect'}
    assert all('metadata' not in row and 'secrets' not in row for row in rows)
    chat.save(p,{**data,'preserve_settings':True,'api_key':'synthetic-key-never-sent-123','expected_updated_at':''})
    with f['factory']() as conn:
        old=conn.execute("SELECT metadata,updated_at FROM saas_tenant_integrations WHERE tenant_id=%s AND provider='chat_ai'",(A,)).fetchone()
    chat.save(p,{**data,'preserve_settings':True,'remove_key':True,'expected_updated_at':str(old['updated_at'])})
    with f['factory']() as conn:
        row=conn.execute("SELECT secrets,metadata FROM saas_tenant_integrations WHERE tenant_id=%s AND provider='chat_ai'",(A,)).fetchone()
        assert row['secrets'] is None and row['metadata']['enabled'] is False


def test_revoked_membership_denies_even_with_stale_owner_session(shared):
    f=shared
    with f['factory']() as conn:conn.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=61',(A,))
    with pytest.raises(TenantLinearError):
        bein.save(f['profile'],token='bei_int_synthetic_token_never_sent',authorization=f['data'])
    with f['factory']() as conn:
        assert not conn.execute("SELECT 1 FROM saas_tenant_integrations WHERE tenant_id=%s AND provider='beinsystems'",(A,)).fetchone()


def test_policy_revocation_waits_for_admitted_operation(shared):
    f=shared;entered=threading.Event();release=threading.Event();started=threading.Event()
    def operation():
        with access.guard(f['factory'],f['profile'],'linear','update',data=f['data']):
            entered.set();assert release.wait(5)
    def revoke():
        started.set()
        with f['factory']() as conn:
            conn.execute("SET LOCAL statement_timeout='5s'")
            conn.execute("UPDATE saas_tenant_workflow_policies SET value='{}' WHERE tenant_id=%s AND policy_key='members'",(A,))
    with ThreadPoolExecutor(max_workers=2) as pool:
        working=pool.submit(operation);assert entered.wait(5)
        changing=pool.submit(revoke);assert started.wait(5)
        try:
            from concurrent.futures import TimeoutError
            with pytest.raises(TimeoutError):changing.result(timeout=.1)
        finally:release.set()
        working.result(timeout=5);changing.result(timeout=5)
    with pytest.raises(TenantLinearError):
        with access.guard(f['factory'],f['profile'],'linear','view'):pass


def test_bein_create_only_cannot_replace_existing_primary_crm(shared):
    f=shared
    with f['factory']() as conn:
        conn.execute("UPDATE saas_tenant_workflow_policies SET value=%s::jsonb WHERE tenant_id=%s AND policy_key='members'",(json.dumps({'61':{'access_scopes':{'integrations':{'create':'all','update':'none'}}}}),A))
        conn.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,metadata,secrets) VALUES(%s,'kommo','connected','{\"primary\":true}',%s)",(A,b'synthetic-encrypted-placeholder'))
    with pytest.raises(TenantLinearError,match='integration_action_denied'):
        bein.save(f['profile'],token='bei_int_synthetic_token_never_sent',primary=True,authorization=f['data'])
    with f['factory']() as conn:
        rows=conn.execute("SELECT provider,metadata FROM saas_tenant_integrations WHERE tenant_id=%s",(A,)).fetchall()
        assert rows==[{'provider':'kommo','metadata':{'primary':True}}]
