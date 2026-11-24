import unittest
from datetime import datetime, timezone
import json
from unittest.mock import patch

import pytest
from test_tenant_support_inbox import A, B, db, database

import tenant_member_store


class Cursor:
    def __init__(self, existing=None):
        self.rowcount = 1
        self.queries = []
        self.existing = existing
        self.owner = {'tenant_id':'tenant-a','telegram_id':1,'role':'owner','active':True}

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def __enter__(self): return self
    def __exit__(self, *_args): return False

    def fetchone(self):
        query, params = self.queries[-1]
        if 'AS ready' in query: return {'ready': True}
        if 'SELECT id,modules' in query: return {'id':'tenant-a','modules':{'tasks':True}}
        if 'INSERT INTO saas_tenant_members' in query:
            _, person, name, role, permissions, _, department = params
        elif 'UPDATE saas_tenant_members SET display_name' in query:
            name, role, permissions, _, person = params
        else: raise AssertionError(query)
        return {'tenant_id':'tenant-a','telegram_id':person,'display_name':name,'role':role,
                'permissions':json.loads(permissions),'active':True,
                'updated_at':datetime(2026,11,2,tzinfo=timezone.utc)}

    def fetchall(self):
        query, _ = self.queries[-1]
        if 'SELECT * FROM saas_tenant_members' in query:
            return [self.owner] + ([self.existing] if self.existing else [])
        return []


class Connection:
    def __init__(self, existing=None):
        self.cursor_value = Cursor(existing)
        self.committed = False

    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def cursor(self): return self.cursor_value
    def commit(self): self.committed = True


class MemberStoreTests(unittest.TestCase):
    def test_permission_payload_distinguishes_explicit_effective_and_legacy(self):
        value=tenant_member_store._member_payload({'tenant_id':'tenant-a','telegram_id':22,
            'role':'worker','permissions':['tasks','support','legacy:retired'],'active':True,
            'modules':{'tasks':True}})
        self.assertEqual(value['explicit_permissions'],['tasks','support','legacy:retired'])
        self.assertEqual(value['effective_permissions'],['tasks','support'])
        self.assertEqual(value['legacy_permissions'],['legacy:retired'])

    def test_effective_permissions_uses_member_workflow_policy(self):
        value=tenant_member_store._member_payload({'tenant_id':'tenant-a','telegram_id':22,
            'role':'admin','permissions':['dialogs'],'active':True,'modules':{},
            'workflow':{'policies':[{'policy_key':'members','value':{'22':{'ai_agents_manage':True}}}]}})
        self.assertEqual(value['explicit_permissions'],['dialogs'])
        self.assertEqual(value['effective_permissions'],['dialogs'])

    def test_edit_preserves_hidden_known_and_unknown_grants_and_legacy_role(self):
        existing={'tenant_id':'tenant-a','telegram_id':22,'display_name':'Legacy','role':'legacy_role',
                  'permissions':['tasks','support','employees','finance','legacy:retired'],'active':True}
        # This is the exact state sent by the visible checkbox editor: it only
        # knows its five checkboxes, while historical grants must survive.
        connection = Connection(existing)
        with patch.object(tenant_member_store, '_connect', return_value=connection):
            tenant_member_store.upsert_member(tenant_id='tenant-a', owner_id=1, telegram_id=22,
                display_name='Legacy', role='legacy_role', permissions=['tasks','support'])
        params=next(params for query,params in connection.cursor_value.queries
                    if 'UPDATE saas_tenant_members SET display_name' in query)
        self.assertEqual(params[1], 'legacy_role')
        self.assertEqual(set(json.loads(params[2])), {'tasks','support','employees','finance','legacy:retired'})

    def test_whatsapp_number_normalization_is_exact_and_tenant_input_safe(self):
        normalize=tenant_member_store._whatsapp_digits
        self.assertEqual(normalize('+994 50 123 45 67'),'994501234567')
        self.assertEqual(normalize('994501234567'),'994501234567')
        self.assertEqual(normalize('0501234567'),'994501234567')
        self.assertEqual(normalize('501234567'),'')
        self.assertEqual(normalize('https://wa.me/994501234567'),'')
        self.assertEqual(normalize('99450+1234567'),'')
        self.assertEqual(normalize('++994501234567'),'')

    def test_upsert_creates_member_and_audits_without_overwriting_owner(self):
        connection = Connection()
        with patch.object(tenant_member_store, "_connect", return_value=connection):
            result = tenant_member_store.upsert_member(tenant_id="tenant-a", owner_id=1,
                telegram_id=22, display_name="Operator", role="operator", permissions=["tasks"])
        self.assertEqual(result["telegram_id"], 22)
        self.assertTrue(connection.committed)
        queries=connection.cursor_value.queries
        self.assertTrue(any('employee.created' in params for _,params in queries))
        insert=next(query for query,_ in queries if 'INSERT INTO saas_tenant_members' in query)
        self.assertIn('DO NOTHING',insert)
        self.assertTrue(any('ORDER BY telegram_id FOR UPDATE' in query for query,_ in queries))
        self.assertFalse(any('ALTER ' in query or 'CREATE ' in query for query,_ in queries))


if __name__ == "__main__":
    unittest.main()


# Disposable PGlite exercises the real login telemetry and employee update
# transactions; credential verification and session issuance are test doubles.
@pytest.mark.asyncio
@pytest.mark.parametrize('login_kind', ['email_login', 'login'])
async def test_normal_staff_login_preserves_owner_edit_version(db, monkeypatch, login_kind):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from test_tenant_auth_api import Request, payload
    from tenant_auth_api import AuthApiContext, handlers as auth_handlers
    from tenant_members_api import MemberApiContext, handlers as member_handlers
    import tenant_account_store as account

    actor, owner = 94 if login_kind=='email_login' else 95, 11
    owner_profile = {'tenant_id': A, 'telegram_id': owner, 'role': 'owner', 'active': True}
    for module in (account, tenant_member_store):
        monkeypatch.setattr(module, '_connect', lambda: db)
    for tenant in (A, B):
        db.execute("""INSERT INTO saas_tenant_members
          (tenant_id,telegram_id,role,display_name,updated_at,last_login_at)
          VALUES(%s::uuid,%s,'worker','Synthetic employee','2001-01-01','2001-01-01')""",(tenant,actor))
    db.execute("""INSERT INTO saas_auth_users(id,tenant_id,actor_id,email,first_name,last_name,login,updated_at,last_login_at)
      VALUES(gen_random_uuid(),%s::uuid,%s,%s,'Synthetic','Employee',%s,'2001-01-01','2001-01-01')""",
      (A,actor,f'synthetic{actor}@example.test',f'synthetic{actor}'))
    db.commit()
    member_sql="""SELECT updated_at,last_login_at FROM saas_tenant_members
      WHERE tenant_id=%s::uuid AND telegram_id=%s"""
    initial_member=db.query(member_sql,(A,actor))[0]
    other_membership=db.query(member_sql,(B,actor))[0]
    auth_sql="SELECT updated_at,last_login_at FROM saas_auth_users WHERE actor_id=%s"
    initial_auth=db.query(auth_sql,(actor,))[0]

    def not_used(*args, **kwargs):
        raise AssertionError('Unrelated operation')
    def request(method, body=None):
        item=Request(body)
        item.method=method
        item.query={}
        return item
    employee=member_handlers(MemberApiContext(
        lambda _request: owner_profile, not_used, not_used, not_used, 'https://crm.pro.az',
        tenant_member_store.list_members, tenant_member_store.upsert_member,
        not_used, not_used, not_used, not_used, not_used, tenant_member_store.TenantPlatformError))['members']
    before=await employee(request('GET'))
    assert before.status==200
    card=next(x for x in payload(before)['members'] if x['telegram_id']==actor)

    issue=Mock(return_value='synthetic-session')
    fingerprint='a'*64
    credential={'tenant_id':A,'actor_id':actor,'_credential_fingerprint':fingerprint}
    auth=auth_handlers(AuthApiContext(
        member=not_used,telegram_identity=lambda _telegram:actor,issue_session=issue,
        tenants_for_user=lambda _actor:[{'id':A,'name':'Company','status':'active'},{'id':B,'name':'Other','status':'active'}],
        create_tenant=not_used,list_integrations=not_used,list_members=not_used,capabilities=not_used,
        disable_browser_push=not_used,role_permissions={},platform_error=tenant_member_store.TenantPlatformError,
        origin='https://crm.pro.az',cookie_name='synthetic_cookie',cookie_ttl=3600,
        base_dir='',logger=Mock(),record_login=account.record_login,
        email_auth=SimpleNamespace(login=Mock(return_value=credential))))[login_kind]
    logged_in=await auth(request('POST',{'tenant_id':A,'email':'synthetic@example.test',
                                         'password':'synthetic-only','telegram':{}}))
    assert logged_in.status==200 and payload(logged_in)['redirect']=='/app'
    assert logged_in.cookies['synthetic_cookie']['httponly']
    issue.assert_called_once_with(A,actor,**({'credential_fingerprint':fingerprint} if login_kind=='email_login' else {}))
    signed_in=db.query(member_sql,(A,actor))[0]
    assert signed_in['last_login_at']>initial_member['last_login_at']
    assert signed_in['updated_at']==initial_member['updated_at']
    assert db.query(member_sql,(B,actor))[0]==other_membership
    auth_after=db.query(auth_sql,(actor,))[0]
    assert all(auth_after[field]>initial_auth[field] for field in ('last_login_at','updated_at'))

    command={'action':'update_member_card','telegram_id':actor,'expected_tenant_id':A,
             'expected_user_id':owner,'expected_version':card['card_version'],
             'changes':{'display_name':'Accepted owner edit'}}
    changed=await employee(request('POST',command))
    assert changed.status==200, payload(changed)
    assert payload(changed)['applied']['card_version']!=card['card_version']
    assert db.query(member_sql,(A,actor))[0]['last_login_at']==signed_in['last_login_at']
    rejected=await employee(request('POST',{**command,'changes':{'display_name':'Stale overwrite'}}))
    assert rejected.status==409 and payload(rejected)['code']=='member_card_conflict'
    reviewed=await employee(request('GET'))
    assert next(x for x in payload(reviewed)['members'] if x['telegram_id']==actor)['display_name']=='Accepted owner edit'
