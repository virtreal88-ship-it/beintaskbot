"""Direct password assignment with isolated synthetic PostgreSQL accounts."""
from test_member_email_assign_postgres import fixture, query, command, snapshot, A, B
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
import tenant_member_password_assign as assign
import tenant_account_store as accounts
import tenant_email_auth as auth
from tenant_member_store import MemberCardError
from tenant_platform_core import TenantPlatformError

NEW='Synthetic-replacement-password-42!'

def password_command(f):
    cmd=command(f);cmd.pop('email');cmd.update(password=NEW,confirm_password=NEW);return cmd

def prepare(f,monkeypatch):
    monkeypatch.setattr(assign,'_connect',f)
    monkeypatch.setattr(accounts,'_connect',f);monkeypatch.setattr(accounts,'_ensure_schema',lambda c:None)
    monkeypatch.setattr(auth,'_connect',f);monkeypatch.setattr(auth,'_ensure_schema',lambda c:None)

@pytest.mark.parametrize('email',['employee@isolated.example',None])
def test_password_assignment_and_account_isolation(fixture,monkeypatch,email):
    f=fixture;prepare(f,monkeypatch)
    query(f,'UPDATE saas_auth_users SET email=%s,password_hash=%s WHERE actor_id=41',(email,auth._hash_password('Synthetic-previous-password-41!')))
    foreign=query(f,'SELECT * FROM saas_auth_users WHERE actor_id=42')
    cmd=password_command(f);result=assign.assign_member_password(**cmd)
    assert result['password_changed'] is True and result['card_version']!=cmd['expected_version']
    assert auth.login(email='employee41',password=NEW,request_ip='127.0.0.1')['actor_id']==41
    assert auth.login(email='employee41',password='Synthetic-previous-password-41!',request_ip='127.0.0.1') is None
    assert query(f,'SELECT * FROM saas_auth_users WHERE actor_id=42')==foreign
    assert query(f,'SELECT force_password_change,legacy_sessions_revoked FROM saas_auth_users WHERE actor_id=41')==[{'force_password_change':False,'legacy_sessions_revoked':True}]
    assert not accounts.legacy_session_is_allowed(actor_id=41)
    assert all(bool(x['revoked_at'])==(x['actor_id']==41) for x in query(f,'SELECT actor_id,revoked_at FROM saas_tenant_browser_sessions'))
    assert all(x['used_at'] for x in query(f,'SELECT used_at FROM saas_auth_tokens'))
    assert query(f,"SELECT payload FROM saas_tenant_audit_events WHERE action='employee.password.assigned'")==[{'payload':{}}]
    before=snapshot(f)
    with pytest.raises(MemberCardError):assign.assign_member_password(**cmd)
    assert snapshot(f)==before

@pytest.mark.parametrize('mutation',[
 "DELETE FROM saas_tenant_audit_events WHERE entity_id='41'",
 "INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,active) VALUES('"+B+"',41,'worker',FALSE)",
 "UPDATE saas_auth_users SET tenant_id='"+B+"' WHERE actor_id=41",
 "UPDATE saas_tenants SET owner_telegram_id=41 WHERE id='"+B+"'",
 "UPDATE saas_tenant_members SET active=FALSE WHERE telegram_id=41",
 "UPDATE saas_auth_users SET disabled_at=now() WHERE actor_id=41",
 "UPDATE saas_tenant_members SET role='admin' WHERE telegram_id=11",
])
def test_password_scope_atomic(fixture,monkeypatch,mutation):
    f=fixture;prepare(f,monkeypatch);query(f,mutation);before=snapshot(f)
    with pytest.raises(MemberCardError):assign.assign_member_password(**password_command(f))
    assert snapshot(f)==before

@pytest.mark.parametrize('extra',[{'password':'short'},{'confirm_password':'different'},{'expected_version':'stale'},{'source_session':None},{'telegram_id':11}])
def test_invalid_command_no_effect(fixture,monkeypatch,extra):
    f=fixture;prepare(f,monkeypatch);cmd={**password_command(f),**extra};before=snapshot(f)
    with pytest.raises(MemberCardError):assign.assign_member_password(**cmd)
    assert snapshot(f)==before

def test_duplicate_command_single_commit(fixture,monkeypatch):
    f=fixture;prepare(f,monkeypatch);cmd=password_command(f)
    def run(_):
        try:return assign.assign_member_password(**cmd)['password_changed']
        except MemberCardError as e:return e.code
    with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(run,[1,2]))
    assert sorted(map(str,result))==['True','member_card_conflict']

def test_stale_login_proof_cannot_create_session_after_password_or_email(fixture,monkeypatch):
    f=fixture;prepare(f,monkeypatch)
    query(f,'UPDATE saas_auth_users SET password_hash=%s WHERE actor_id=41',(auth._hash_password(NEW),))
    credentials=auth.login(email='employee41',password=NEW,request_ip='127.0.0.1')
    from tenant_member_email_assign import assign_member_email
    assign_member_email(**command(f))
    with pytest.raises(TenantPlatformError):
        accounts.create_session(session_id=str(uuid4()),tenant_id=A,actor_id=41,expires_at=9999999999,credential_fingerprint=credentials['_credential_fingerprint'])
    credentials=auth.login(email='employee41',password=NEW,request_ip='127.0.0.1')
    cmd=password_command(f);cmd.update(password='Other-synthetic-password-42!',confirm_password='Other-synthetic-password-42!')
    assign.assign_member_password(**cmd)
    with pytest.raises(TenantPlatformError):
        accounts.create_session(session_id=str(uuid4()),tenant_id=A,actor_id=41,expires_at=9999999999,credential_fingerprint=credentials['_credential_fingerprint'])


@pytest.mark.parametrize('managed', [True, False])
def test_managed_login_cannot_use_assigned_email_as_foreign_company_invitation_proof(fixture,monkeypatch,managed):
    f=fixture;prepare(f,monkeypatch)
    import tenant_invite_store as invites
    import hashlib
    monkeypatch.setattr(invites,'_connect',f)
    monkeypatch.setattr(invites,'_ensure_schema',lambda c:None)
    query(f,'UPDATE saas_auth_users SET password_hash=%s WHERE actor_id=41',(auth._hash_password(NEW),))
    if not managed:query(f,"DELETE FROM saas_tenant_audit_events WHERE entity_id='41'")
    token='synthetic-invitation-'+str(uuid4())
    query(f,"""INSERT INTO saas_tenant_invites(id,tenant_id,token_hash,display_name,role,permissions,created_by,expires_at,invited_email)
       VALUES(%s,%s,%s,'Synthetic','worker','[]',22,now()+interval '1 hour','user41@isolated.example')""",(uuid4(),B,hashlib.sha256(token.encode()).hexdigest()))
    credentials=auth.login(email='employee41',password=NEW,request_ip='127.0.0.1')
    args=dict(token=token,actor_id=41,email='user41@isolated.example',credential_fingerprint=credentials['_credential_fingerprint'])
    if managed:
        before=snapshot(f)
        with pytest.raises(TenantPlatformError):invites.accept_email_invite(**args)
        assert snapshot(f)==before
        assert not query(f,'SELECT accepted_at FROM saas_tenant_invites')[0]['accepted_at']
    else:
        assert invites.accept_email_invite(**args)['tenant_id']==B
