"""PostgreSQL regression for Operator rate limits and pending-token storage."""
import json
import os
import uuid

import pytest
import psycopg
from unittest.mock import patch

from tenant_beinsystems_operator_schema import migrate_operator_identity_links


@pytest.mark.skipif(
    not os.environ.get("BEINTASKBOT_TEST_POSTGRES_DSN"),
    reason="Set BEINTASKBOT_TEST_POSTGRES_DSN to an isolated PostgreSQL database",
)
def test_existing_rate_limit_table_accepts_auth_login_after_migration():
    dsn = os.environ["BEINTASKBOT_TEST_POSTGRES_DSN"]
    schema = "operator_migration_" + uuid.uuid4().hex
    tenant_id = str(uuid.uuid4())
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        try:
            conn.execute(f'SET search_path TO "{schema}", public')
            conn.execute("CREATE TABLE saas_tenants (id UUID PRIMARY KEY)")
            conn.execute("""CREATE TABLE saas_tenant_members (
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id),
              telegram_id BIGINT NOT NULL,
              PRIMARY KEY(tenant_id,telegram_id))""")
            conn.execute("""CREATE TABLE saas_beinsystems_operator_rate_limits (
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id),
              actor_telegram_id BIGINT NOT NULL,
              endpoint TEXT NOT NULL CHECK(endpoint IN
                ('me','accounts','account_info','profile_users','task_list')),
              bucket_start TIMESTAMPTZ NOT NULL,
              request_count INTEGER NOT NULL,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,actor_telegram_id,endpoint,bucket_start))""")
            conn.execute("INSERT INTO saas_tenants(id) VALUES(%s::uuid)", (tenant_id,))
            conn.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id) VALUES(%s::uuid,7)",
                         (tenant_id,))

            for _ in range(2):
                with conn.cursor() as cur:
                    migrate_operator_identity_links(cur)

            conn.execute("""INSERT INTO saas_beinsystems_operator_rate_limits
              (tenant_id,actor_telegram_id,endpoint,bucket_start,request_count)
              VALUES(%s::uuid,7,'auth_login',date_trunc('minute',now()),1)""", (tenant_id,))
            assert conn.execute("""SELECT endpoint FROM saas_beinsystems_operator_rate_limits
              WHERE tenant_id=%s::uuid""", (tenant_id,)).fetchone() == ("auth_login",)
            conn.execute("""INSERT INTO saas_beinsystems_operator_pending_auth
              (tenant_id,owner_telegram_id,token_ciphertext)
              VALUES(%s::uuid,7,%s)""", (tenant_id, b"legacy-encrypted"))
            conn.execute("""INSERT INTO saas_beinsystems_operator_pending_auth_v2
              (tenant_id,owner_telegram_id,token_ciphertext)
              VALUES(%s::uuid,7,%s)""", (tenant_id, b"contract-encrypted"))
            assert conn.execute("""SELECT token_ciphertext
              FROM saas_beinsystems_operator_pending_auth WHERE tenant_id=%s::uuid""",
              (tenant_id,)).fetchone() == (b"legacy-encrypted",)
            assert conn.execute("""SELECT token_ciphertext
              FROM saas_beinsystems_operator_pending_auth_v2 WHERE tenant_id=%s::uuid""",
              (tenant_id,)).fetchone() == (b"contract-encrypted",)
            conn.execute("DELETE FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=7",
                         (tenant_id,))
            assert conn.execute("""SELECT count(*) FROM saas_beinsystems_operator_pending_auth
              WHERE tenant_id=%s::uuid""", (tenant_id,)).fetchone() == (0,)
            assert conn.execute("""SELECT count(*) FROM saas_beinsystems_operator_pending_auth_v2
              WHERE tenant_id=%s::uuid""", (tenant_id,)).fetchone() == (0,)
        finally:
            conn.execute("SET search_path TO public")
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.fixture
def operator_context_sql(monkeypatch):
    """Real UUID comparisons in disposable localhost schema; auth seams recorded."""
    from contextlib import nullcontext
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.rows import dict_row
    import tenant_beinsystems_operator as operator
    import tenant_operator_access as access
    import tenant_crm_native_access as crm
    dsn=os.environ.get('BEINTASKBOT_TEST_POSTGRES_DSN')
    if not dsn: pytest.skip('Disposable localhost PostgreSQL required')
    if conninfo_to_dict(dsn).get('host') not in {'localhost','127.0.0.1','::1'}:
        pytest.fail('Local test server only')
    schema='operator_context_'+uuid.uuid4().hex
    tenant,other,channel,contact,company,instance,other_instance=[str(uuid.uuid4()) for _ in range(7)]
    with psycopg.connect(dsn,autocommit=True,row_factory=dict_row) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        try:
            conn.execute(f'SET search_path TO "{schema}"')
            conn.execute('CREATE TABLE saas_crm_native_contacts (tenant_id uuid,id uuid,company_id uuid,archived_at timestamptz)')
            conn.execute('CREATE TABLE saas_crm_native_companies (tenant_id uuid,id uuid,name text,archived_at timestamptz)')
            conn.execute('''CREATE TABLE saas_beinsystems_operator_dialog_company_bindings (
              tenant_id uuid,channel_id uuid,conversation_id text,company_id uuid,
              created_by bigint,updated_by bigint,created_at timestamptz,updated_at timestamptz,
              PRIMARY KEY(tenant_id,channel_id,conversation_id))''')
            conn.execute('''CREATE TABLE saas_beinsystems_operator_company_imports (
              tenant_id uuid,external_account text,company_id uuid,
              last_seen_connection_instance_id uuid,last_seen_generation text)''')
            conn.execute('CREATE TABLE saas_crm_native_contact_conversations (tenant_id uuid,provider text,channel_id uuid,conversation_id text,contact_id uuid)')
            conn.execute('''CREATE TABLE saas_beinsystems_operator_links (
              id uuid,tenant_id uuid,local_entity_type text,local_entity_id uuid,
              external_entity_type text,external_id text,external_scope_id text,
              connection_generation text,connection_instance_id uuid,revoked_at timestamptz,
              verification_basis text,evidence_summary text,verified_at timestamptz,
              verified_by bigint,version int,created_at timestamptz)''')
            conn.execute('INSERT INTO saas_crm_native_contacts VALUES(%s,%s,%s,NULL)',(tenant,contact,company))
            conn.execute('INSERT INTO saas_crm_native_companies VALUES(%s,%s,%s,NULL)',(tenant,company,'Synthetic Company'))
            conn.execute("INSERT INTO saas_crm_native_contact_conversations VALUES(%s,'connect',%s,'client',%s)",(tenant,channel,contact))
            for label,owner,inst,generation,revoked,kind,local_kind,local_id in [
                ('current',tenant,instance,'gen',False,'account','contact',contact),
                ('user',tenant,instance,'gen',False,'profile_user','contact',contact),
                ('company',tenant,instance,'gen',False,'account','company',company),
                ('other-instance',tenant,other_instance,'gen',False,'account','contact',contact),
                ('old-generation',tenant,instance,'old',False,'account','contact',contact),
                ('revoked',tenant,instance,'gen',True,'account','contact',contact),
                ('foreign-tenant',other,instance,'gen',False,'account','contact',contact)]:
                conn.execute('''INSERT INTO saas_beinsystems_operator_links VALUES
                  (%s,%s,%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s THEN now() END,
                  'manual','synthetic',now(),11,1,now())''',
                  (str(uuid.uuid4()),owner,local_kind,local_id,kind,label,'current' if kind=='profile_user' else '',generation,inst,revoked))
            calls=[]; metadata={'generation':'gen','connection_instance_id':instance}
            profile={'tenant_id':tenant,'telegram_id':11,'role':'owner','active':True}
            monkeypatch.setattr(operator,'is_operator_workspace',lambda value:value==tenant)
            monkeypatch.setattr(operator,'_connect',lambda:nullcontext(conn))
            monkeypatch.setattr(operator,'_ensure_schema',lambda _:None)
            def current(*_):calls.append('current_profile');return profile
            monkeypatch.setattr(operator,'_current_profile',current)
            def integration(*_):calls.append('metadata');return {'status':'connected','secrets':b'synthetic-active-connection','metadata':metadata}
            monkeypatch.setattr(operator,'_operator_row',integration)
            def authorize(*_):calls.append('dialog_access');return tenant,11,object()
            def route(*args,**kwargs):
                calls.append('assert_route');assert args[1]==tenant
                assert kwargs['channel']==channel and kwargs['conversation']=='client'
                return {'connect_channel_id':channel,'conversation_id':'client'}
            monkeypatch.setattr(access,'access',authorize);monkeypatch.setattr(access,'assert_route',route)
            def crm_access(*_):calls.append('crm_access');return profile
            def resource(*args):
                calls.append('locked_resource')
                assert args[-2] == tenant
                ident=args[-1]
                return {'id':ident} if ident in {contact,company} else None
            monkeypatch.setattr(crm,'current_access',crm_access);monkeypatch.setattr(crm,'locked_resource',resource)
            def snapshot():return conn.execute('SELECT row_to_json(l)::text AS row FROM saas_beinsystems_operator_links l ORDER BY id').fetchall()
            yield dict(operator=operator,conn=conn,profile=profile,channel=channel,contact=contact,company=company,instance=instance,metadata=metadata,calls=calls,snapshot=snapshot)
        finally:
            conn.execute('SET search_path TO public')
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_pending_accounts_capability_adds_one_exact_local_company_and_disconnect_revokes(monkeypatch):
    """Exercise the grant/write lifecycle in an empty disposable real PostgreSQL schema."""
    from cryptography.fernet import Fernet
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.rows import dict_row
    import tenant_beinsystems_operator as operator
    import tenant_beinsystems_operator_company_import as company_import
    import tenant_platform_schema

    dsn = os.environ.get("BEINTASKBOT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set BEINTASKBOT_TEST_POSTGRES_DSN to an isolated PostgreSQL database")
    if conninfo_to_dict(dsn).get("host") not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("Local test server only")

    schema = "operator_selected_company_" + uuid.uuid4().hex
    tenant, actor = str(uuid.uuid4()), 73
    token = "synthetic-pending-jwt"
    key = Fernet.generate_key().decode("ascii")
    profile = {"tenant_id": tenant, "telegram_id": actor, "role": "owner", "active": True}

    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
        try:
            admin.execute(f'SET search_path TO "{schema}"')
            # Bootstrap only the tenant and membership required by the real app migration.
            admin.execute("""CREATE TABLE saas_tenants (
              id uuid PRIMARY KEY,slug text NOT NULL UNIQUE,name text NOT NULL,
              industry text NOT NULL DEFAULT '',status text NOT NULL DEFAULT 'active',
              owner_telegram_id bigint NOT NULL,modules jsonb NOT NULL DEFAULT '{}'::jsonb,
              notification_rules jsonb NOT NULL DEFAULT '{}'::jsonb,
              onboarding jsonb NOT NULL DEFAULT '{}'::jsonb,
              created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now())""")
            admin.execute("""CREATE TABLE saas_tenant_members (
              tenant_id uuid NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
              telegram_id bigint NOT NULL,display_name text NOT NULL DEFAULT '',
              role text NOT NULL,permissions jsonb NOT NULL DEFAULT '[]'::jsonb,
              active boolean NOT NULL DEFAULT true,invited_by bigint NULL,
              created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,telegram_id))""")
            admin.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s::uuid,'synthetic','Synthetic','active',%s)",
                          (tenant, actor))
            admin.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role) VALUES(%s::uuid,%s,'owner')",
                          (tenant, actor))
            capability_relation = f"{schema}.saas_beinsystems_operator_accounts_capabilities"
            assert admin.execute("SELECT to_regclass(%s) AS relation",
                                 (capability_relation,)).fetchone()[0] is None

            def connect_test_schema():
                return psycopg.connect(dsn, row_factory=dict_row,
                                       options=f"-c search_path={schema},public")

            monkeypatch.setenv("DATABASE_URL", dsn)
            monkeypatch.setenv("TENANT_ENCRYPTION_KEY", key)
            monkeypatch.setenv(operator.WORKSPACE_ENV, tenant)
            monkeypatch.setattr(tenant_platform_schema, "_schema_ready", False)
            monkeypatch.setattr(operator, "_connect", connect_test_schema)
            # Run the real additive app migration against the initially absent tables.
            with connect_test_schema() as conn:
                operator._ensure_schema(conn)
            with connect_test_schema() as check:
                assert check.execute("SELECT to_regclass(%s) AS relation",
                                     (capability_relation,)).fetchone()["relation"]
                check.execute("""INSERT INTO saas_tenant_integrations
                  (tenant_id,provider,status,account_domain,metadata,secrets)
                  VALUES(%s::uuid,%s,'not_connected','', '{}'::jsonb,NULL)""",
                  (tenant, operator.PROVIDER))
                envelope = operator._json({
                    "tenant_id": tenant, "provider": operator.PROVIDER,
                    "owner_telegram_id": actor, "purpose": "pending_auth_verification",
                    "auth_contract": operator.AUTH_CONTRACT_VERSION, "token": token,
                })
                check.execute("""INSERT INTO saas_beinsystems_operator_pending_auth_v2
                  (tenant_id,owner_telegram_id,token_ciphertext)
                  VALUES(%s::uuid,%s,%s)""",
                  (tenant, actor, operator._fernet().encrypt(envelope.encode("utf-8"))))

            calls = []
            def provider(endpoint, payload, **kwargs):
                calls.append((endpoint, payload, kwargs))
                assert kwargs["token"] == token
                if endpoint == "me":
                    assert payload == {} and kwargs["requests_me"] is True
                    return {"status": "success", "data": {
                        "identifier": "owner@synthetic.test", "role": "superadmin",
                        "partnerPin": "never-display"}}
                assert endpoint == "accounts"
                assert payload == {"search": payload["search"], "limit": 1}
                return {"status": "success", "data": [{"account": payload["search"],
                    "email": "private@synthetic.test", "name": "private-name"}]}

            monkeypatch.setattr(operator, "_request_operator", provider)

            preview = company_import.prepare_accounts_capability(profile)
            assert set(preview) == {"identifier", "candidate_received_at"}
            confirmation = company_import.confirm_accounts_capability(
                profile, acknowledged_identifier=preview["identifier"],
                candidate_received_at=preview["candidate_received_at"])
            assert confirmation == {"ready": True, "label": "accounts_only",
                                    "expires_in_hours": 24}
            state = operator.status(profile)
            assert state["status"] == "not_connected" and not state["connected"]
            assert state["accounts_only_ready"] is True

            created = company_import.add_selected_account_company(profile, "Synthetic Account")
            assert created["status"] == "create"
            assert created["source"] == "accounts_only"
            company_id = created["company"]["id"]
            with connect_test_schema() as check:
                check.execute("""UPDATE saas_crm_native_companies SET
                  phone='manual-phone',email='manual@crm.test',description='manual description'
                  WHERE tenant_id=%s::uuid AND id=%s::uuid""", (tenant, company_id))
            reused = company_import.add_selected_account_company(profile, "Synthetic Account")
            assert reused["status"] == "reuse" and reused["company"]["id"] == company_id
            with connect_test_schema() as check:
                rows = check.execute("""SELECT id::text,name,phone,email,description
                  FROM saas_crm_native_companies WHERE tenant_id=%s::uuid""", (tenant,)).fetchall()
                assert rows == [{"id": company_id, "name": "Synthetic Account",
                    "phone": "manual-phone", "email": "manual@crm.test",
                    "description": "manual description"}]
                assert check.execute("""SELECT count(*) FROM saas_beinsystems_operator_company_imports
                  WHERE tenant_id=%s::uuid AND external_account='Synthetic Account'""",
                  (tenant,)).fetchone()["count"] == 1

                # Case-insensitive name collision is a review outcome, never a second company.
                check.execute("""INSERT INTO saas_crm_native_companies
                  (id,tenant_id,name,phone,email,description,responsible_id,access_responsible_id)
                  VALUES(%s::uuid,%s::uuid,'CASE VARIANT','keep-phone','keep@email.test','keep',NULL,%s)""",
                  (str(uuid.uuid4()), tenant, actor))
            collision = company_import.add_selected_account_company(profile, "case variant")
            assert collision["status"] == "manual_review"
            assert collision["reason"] == "name_collision"
            with connect_test_schema() as check:
                assert check.execute("""SELECT count(*) FROM saas_crm_native_companies
                  WHERE tenant_id=%s::uuid""", (tenant,)).fetchone()["count"] == 2
                assert check.execute("""SELECT count(*) FROM saas_beinsystems_operator_company_imports
                  WHERE tenant_id=%s::uuid AND external_account='case variant'""",
                  (tenant,)).fetchone()["count"] == 0

            requests_before_disconnect = len(calls)
            operator.disconnect(profile)
            assert len(calls) == requests_before_disconnect
            with connect_test_schema() as check:
                assert check.execute("""SELECT count(*) FROM saas_beinsystems_operator_accounts_capabilities
                  WHERE tenant_id=%s::uuid""", (tenant,)).fetchone()["count"] == 0
                assert check.execute("""SELECT count(*) FROM saas_beinsystems_operator_pending_auth_v2
                  WHERE tenant_id=%s::uuid""", (tenant,)).fetchone()["count"] == 0
                assert check.execute("""SELECT count(*) FROM saas_crm_native_companies
                  WHERE tenant_id=%s::uuid""", (tenant,)).fetchone()["count"] == 2
            with pytest.raises(operator.OperatorError):
                company_import.add_selected_account_company(profile, "Synthetic Account")
            assert len(calls) == requests_before_disconnect
            assert all((call[1].get("limit") == 1) for call in calls if call[0] == "accounts")
            public_results = (preview, confirmation, created, reused, collision)
            assert all("partnerPin" not in repr(result)
                       and "private@synthetic.test" not in repr(result)
                       and "private-name" not in repr(result)
                       for result in public_results)
        finally:
            admin.execute("SET search_path TO public")
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.mark.parametrize('invalid',[None,'','not-a-uuid',{},'00000000-0000-0000-0000'])
def test_invalid_instance_is_unlinked_but_authorized_history_is_stale(operator_context_sql,invalid):
    f=operator_context_sql; metadata=f['metadata']
    if invalid is None:metadata.pop('connection_instance_id')
    else:metadata['connection_instance_id']=invalid
    before=f['snapshot']()
    result=f['operator'].dialog_context(f['profile'],channel_id=f['channel'],conversation_id='client')
    assert result['status']=='unlinked'
    assert result['account_links']==result['profile_user_links']==[]
    assert f['calls'][:4]==['current_profile','dialog_access','assert_route','metadata']
    f['calls'].clear()
    links=f['operator'].links_for_local(f['profile'],local_entity_type='contact',local_entity_id=f['contact'])
    assert f['calls']==['current_profile','crm_access','locked_resource','metadata']
    assert {row['external_id']:row['status'] for row in links}=={
        'current':'stale','user':'stale','other-instance':'stale','old-generation':'stale','revoked':'revoked'}
    assert f['snapshot']()==before


def test_valid_instance_matches_only_authorized_current_links(operator_context_sql):
    f=operator_context_sql;before=f['snapshot']()
    result=f['operator'].dialog_context(f['profile'],channel_id=f['channel'],conversation_id='client')
    assert {r['account'] for r in result['account_links']}=={'current','company'}
    assert [r['login'] for r in result['profile_user_links']]==['user']
    links=f['operator'].links_for_local(f['profile'],local_entity_type='contact',local_entity_id=f['contact'])
    assert {r['external_id']:r['status'] for r in links}=={
        'current':'confirmed','user':'confirmed','other-instance':'stale','old-generation':'stale','revoked':'revoked'}
    assert f['snapshot']()==before


def test_dialog_company_binding_is_local_route_and_tenant_scoped(operator_context_sql):
    import tenant_beinsystems_operator as operator
    f=operator_context_sql
    with patch.object(operator,'_request_operator') as outbound:
        binding=operator.set_dialog_company_binding(
            f['profile'],channel_id=f['channel'],conversation_id='client',
            company_id=str(f['company']))
        assert binding=={'company_id':str(f['company']),'company_name':'Synthetic Company','external_account':'company','can_manage':True}
        rows=f['conn'].execute("""SELECT tenant_id::text,channel_id::text,conversation_id,company_id::text
          FROM saas_beinsystems_operator_dialog_company_bindings""").fetchall()
        assert [tuple(row[key] for key in ('tenant_id','channel_id','conversation_id','company_id'))
                for row in rows]==[(f['profile']['tenant_id'],f['channel'],'client',str(f['company']))]
        outsider=str(uuid.uuid4())
        with pytest.raises(operator.OperatorError) as denied:
            operator.set_dialog_company_binding(
                f['profile'],channel_id=f['channel'],conversation_id='client',company_id=outsider)
        assert denied.value.status==403
        persisted=f['conn'].execute(
            "SELECT company_id::text FROM saas_beinsystems_operator_dialog_company_bindings").fetchone()
        assert persisted['company_id']==str(f['company'])
        f['profile']['role']='manager'
        readonly=f['operator'].dialog_company_binding(
            f['profile'],channel_id=f['channel'],conversation_id='client')
        assert readonly['can_manage'] is False
        with patch.object(operator.TenantPolicy,'allows_action',return_value=False):
            restricted=operator.dialog_company_binding(
                f['profile'],channel_id=f['channel'],conversation_id='client')
        assert restricted['company_id']==str(f['company'])
        assert restricted['external_account'] is None

        with pytest.raises(operator.OperatorError) as owner_required:
            f['operator'].set_dialog_company_binding(
                f['profile'],channel_id=f['channel'],conversation_id='client',
                company_id=str(f['company']))
        assert owner_required.value.status==403
        f['profile']['role']='owner'
        outbound.assert_not_called()




def test_dialog_company_binding_resolves_current_imported_account(operator_context_sql,monkeypatch):
    import tenant_beinsystems_operator as operator
    monkeypatch.setattr(operator,"_request_operator",
        lambda *args,**kwargs: pytest.fail("dialog binding must remain local"))
    f=operator_context_sql
    f['conn'].execute("""DELETE FROM saas_beinsystems_operator_links
      WHERE tenant_id=%s::uuid AND local_entity_type='company'
        AND local_entity_id=%s::uuid""",(f['profile']['tenant_id'],f['company']))
    f['conn'].execute("""INSERT INTO saas_beinsystems_operator_company_imports
      VALUES(%s::uuid,'imported-current',%s::uuid,%s::uuid,'gen')""",
      (f['profile']['tenant_id'],f['company'],f['instance']))
    binding=operator.set_dialog_company_binding(
        f['profile'],channel_id=f['channel'],conversation_id='client',
        company_id=str(f['company']))
    assert binding['external_account']=='imported-current'


def test_dialog_company_binding_refuses_ambiguous_accounts(operator_context_sql,monkeypatch):
    import tenant_beinsystems_operator as operator
    monkeypatch.setattr(operator,"_request_operator",
        lambda *args,**kwargs: pytest.fail("dialog binding must remain local"))
    f=operator_context_sql
    f['conn'].execute("""INSERT INTO saas_beinsystems_operator_company_imports
      VALUES(%s::uuid,'imported-different',%s::uuid,%s::uuid,'gen')""",
      (f['profile']['tenant_id'],f['company'],f['instance']))
    binding=operator.set_dialog_company_binding(
        f['profile'],channel_id=f['channel'],conversation_id='client',
        company_id=str(f['company']))
    assert binding['company_id']==str(f['company'])
    assert binding['external_account'] is None


def test_dialog_company_binding_ignores_stale_and_other_tenant_accounts(operator_context_sql,monkeypatch):
    import tenant_beinsystems_operator as operator
    monkeypatch.setattr(operator,"_request_operator",
        lambda *args,**kwargs: pytest.fail("dialog binding must remain local"))
    f=operator_context_sql
    tenant=f['profile']['tenant_id']
    f['conn'].execute("""DELETE FROM saas_beinsystems_operator_links
      WHERE tenant_id=%s::uuid AND local_entity_type='company'
        AND local_entity_id=%s::uuid""",(tenant,f['company']))
    f['conn'].execute("INSERT INTO saas_crm_native_companies VALUES(%s::uuid,%s::uuid,'Foreign Company',NULL)",
      (str(uuid.uuid4()),f['company']))
    f['conn'].execute("""INSERT INTO saas_beinsystems_operator_company_imports
      VALUES(%s::uuid,'stale-account',%s::uuid,%s::uuid,'old-generation'),
            (%s::uuid,'foreign-tenant-account',%s::uuid,%s::uuid,'gen')""",
      (tenant,f['company'],f['instance'],str(uuid.uuid4()),f['company'],f['instance']))
    binding=operator.set_dialog_company_binding(
        f['profile'],channel_id=f['channel'],conversation_id='client',
        company_id=str(f['company']))
    assert binding['company_id']==str(f['company'])
    assert binding['external_account'] is None



def test_context_authorization_still_precedes_metadata(operator_context_sql,monkeypatch):
    import tenant_operator_access as access
    from tenant_linear_policy import TenantLinearError
    f=operator_context_sql;f['metadata'].clear()
    def denied(*_,**__):raise TenantLinearError('denied',403)
    monkeypatch.setattr(access,'assert_route',denied)
    with pytest.raises(TenantLinearError):
        f['operator'].dialog_context(f['profile'],channel_id=f['channel'],conversation_id='client')
    assert f['calls']==['current_profile','dialog_access']


@pytest.mark.parametrize('generation',[None,'','   ',123,{}])
def test_invalid_generation_never_confirms_existing_links(operator_context_sql,generation):
    f=operator_context_sql;f['metadata']['generation']=generation;before=f['snapshot']()
    result=f['operator'].dialog_context(f['profile'],channel_id=f['channel'],conversation_id='client')
    assert result['status']=='unlinked' and not result['account_links'] and not result['profile_user_links']
    links=f['operator'].links_for_local(f['profile'],local_entity_type='contact',local_entity_id=f['contact'])
    assert len(links)==5 and all(row['status'] in {'stale','revoked'} for row in links)
    assert f['snapshot']()==before



@pytest.mark.skipif(
    not os.environ.get("BEINTASKBOT_TEST_POSTGRES_DSN"),
    reason="Set BEINTASKBOT_TEST_POSTGRES_DSN to an isolated PostgreSQL database",
)
def test_operator_account_catalog_import_is_atomic_idempotent_and_never_deletes_absent_rows():
    import tenant_beinsystems_operator as operator
    dsn = os.environ["BEINTASKBOT_TEST_POSTGRES_DSN"]
    schema = "operator_account_import_" + uuid.uuid4().hex
    tenant_id = str(uuid.uuid4())
    accounts = [{"account": f"synthetic-account-{index:03d}"} for index in range(137)]
    with psycopg.connect(dsn, autocommit=True) as setup:
        setup.execute(f'CREATE SCHEMA "{schema}"')
        try:
            setup.execute(f'SET search_path TO "{schema}"')
            setup.execute("""CREATE TABLE saas_beinsystems_operator_account_catalog (
              tenant_id UUID NOT NULL, external_account TEXT NOT NULL,
              first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              local_label TEXT NOT NULL DEFAULT '',
              PRIMARY KEY(tenant_id,external_account))""")
            setup.execute("SET search_path TO public")
        finally:
            setup.execute("SET search_path TO public")

    try:
        with psycopg.connect(dsn) as conn:
            conn.execute(f'SET search_path TO "{schema}"')
            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        operator._upsert_imported_accounts(cur, tenant_id, accounts)
                    raise RuntimeError("synthetic failure before commit")
            except RuntimeError:
                pass
            assert conn.execute("SELECT count(*) FROM saas_beinsystems_operator_account_catalog").fetchone() == (0,)

            with conn.transaction():
                with conn.cursor() as cur:
                    operator._upsert_imported_accounts(cur, tenant_id, accounts)
            assert conn.execute("SELECT count(*) FROM saas_beinsystems_operator_account_catalog").fetchone() == (137,)
            before = conn.execute("""SELECT first_seen_at FROM saas_beinsystems_operator_account_catalog
              WHERE external_account='synthetic-account-000'""").fetchone()
            conn.execute("""UPDATE saas_beinsystems_operator_account_catalog
              SET local_label='preserve-me',last_seen_at=now()-interval '1 day'
              WHERE external_account='synthetic-account-000'""")
            with conn.transaction():
                with conn.cursor() as cur:
                    operator._upsert_imported_accounts(cur, tenant_id, accounts)
            with conn.transaction():
                with conn.cursor() as cur:
                    operator._upsert_imported_accounts(cur, tenant_id, accounts[1:])
            assert conn.execute("SELECT count(*) FROM saas_beinsystems_operator_account_catalog").fetchone() == (137,)
            after = conn.execute("""SELECT first_seen_at,local_label,
                last_seen_at>now()-interval '1 minute'
              FROM saas_beinsystems_operator_account_catalog
              WHERE external_account='synthetic-account-000'""").fetchone()
            assert after == (before[0], "preserve-me", True)
            assert conn.execute("""SELECT count(*) FROM saas_beinsystems_operator_account_catalog
              WHERE external_account='synthetic-account-136'""").fetchone() == (1,)
        with psycopg.connect(dsn, autocommit=True) as cleanup:
            cleanup.execute("SET search_path TO public")
            cleanup.execute(f'DROP SCHEMA "{schema}" CASCADE')
    finally:
        with psycopg.connect(dsn, autocommit=True) as cleanup:
            cleanup.execute("SET search_path TO public")
            cleanup.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_active_account_company_creation_is_idempotent_and_rotation_scoped(monkeypatch):
    import tenant_beinsystems_operator as operator
    import tenant_beinsystems_operator_company_import as company_import
    from cryptography.fernet import Fernet
    from psycopg.rows import dict_row

    dsn = os.environ.get("BEINTASKBOT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Disposable localhost PostgreSQL required")
    from psycopg.conninfo import conninfo_to_dict
    if conninfo_to_dict(dsn).get("host") not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("Local test server only")

    schema = "operator_company_import_" + uuid.uuid4().hex
    tenant, legacy_company, collision_company, instance_one, instance_two = [
        str(uuid.uuid4()) for _ in range(5)
    ]
    profile = {"tenant_id": tenant, "telegram_id": 7, "role": "owner", "active": True}
    active = {
        "token": "synthetic-active-token-one",
        "base_url": "https://operator-api.beinsystems.az/v1",
        "generation": "generation-one",
        "connection_instance_id": instance_one,
        "status": "connected",
    }
    account_snapshots = [
        [{"account": "legacy-account"}, {"account": "name-collision"},
         {"account": "manually-bound"}],
        [{"account": "new-only"}, {"account": "legacy-account"}],
    ]
    requests = []

    def connect():
        conn = psycopg.connect(dsn, row_factory=dict_row)
        conn.execute(f'SET search_path TO "{schema}", public')
        return conn

    cipher = Fernet(Fernet.generate_key())

    def integration_row(_cur, _tenant, lock=False):
        envelope = {
            "tenant_id": tenant, "provider": operator.PROVIDER,
            "token": active["token"],
            "connection_instance_id": active["connection_instance_id"],
        }
        return {
            "status": active["status"],
            "secrets": cipher.encrypt(json.dumps(envelope).encode()),
            "metadata": {
                "generation": active["generation"],
                "connection_instance_id": active["connection_instance_id"],
            },
        }

    def provider_read(endpoint, payload, *, token, base_url):
        requests.append((endpoint, token, base_url))
        if endpoint == "me":
            return {"status": "success", "data": {
                "identifier": "synthetic.operator", "role": "operator"
            }}
        return {"status": "success", "data": account_snapshots.pop(0)}

    def visible_company(cur, _profile, _module, _action, _table, tenant_id, company_id):
        row = cur.execute(
            "SELECT id::text AS id FROM saas_crm_native_companies "
            "WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL",
            (tenant_id, company_id),
        ).fetchone()
        return row

    class FullAccess:
        def __init__(self, _profile):
            pass

        def has_scoped_access(self, _module):
            return False

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        try:
            conn.execute(f'SET search_path TO "{schema}", public')
            conn.execute("CREATE TABLE saas_tenants (id UUID PRIMARY KEY)")
            conn.execute("""CREATE TABLE saas_tenant_members (
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id),
              telegram_id BIGINT NOT NULL,
              PRIMARY KEY(tenant_id,telegram_id))""")
            conn.execute("""CREATE TABLE saas_crm_native_companies (
              id UUID NOT NULL, tenant_id UUID NOT NULL REFERENCES saas_tenants(id),
              name TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '',
              email TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
              responsible_id BIGINT NULL, access_responsible_id BIGINT NULL,
              archived_at TIMESTAMPTZ NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              UNIQUE(tenant_id,id))""")
            conn.execute("""CREATE TABLE saas_tenant_audit_events (
              id UUID PRIMARY KEY, tenant_id UUID NOT NULL, actor_telegram_id BIGINT NOT NULL,
              action TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
              payload JSONB NOT NULL)""")
            conn.execute("INSERT INTO saas_tenants(id) VALUES(%s::uuid)", (tenant,))
            conn.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id) VALUES(%s::uuid,7)",
                         (tenant,))
            conn.execute("""INSERT INTO saas_crm_native_companies
              (id,tenant_id,name,phone,email,description,responsible_id,access_responsible_id)
              VALUES(%s::uuid,%s::uuid,'name-collision','','','',NULL,7)""",
                         (collision_company, tenant))
            conn.execute("""INSERT INTO saas_crm_native_companies
              (id,tenant_id,name,phone,email,description,responsible_id,access_responsible_id)
              VALUES(%s::uuid,%s::uuid,'Existing CRM company','','','',NULL,7)""",
                         (legacy_company, tenant))
            with conn.cursor() as cur:
                migrate_operator_identity_links(cur)
            conn.execute("""INSERT INTO saas_beinsystems_operator_links
              (id,tenant_id,connection_instance_id,connection_generation,local_entity_type,
               local_entity_id,external_entity_type,external_id,external_scope_id,
               verification_basis,evidence_summary,verified_by)
              VALUES(%s::uuid,%s::uuid,%s::uuid,'generation-one','company',%s::uuid,
                     'account','manually-bound','','manual_operator_review',
                     'Owner confirmed synthetic account',7)""",
                         (str(uuid.uuid4()), tenant, instance_one, legacy_company))
            conn.execute("""INSERT INTO saas_beinsystems_operator_account_catalog
              (tenant_id,external_account) VALUES(%s::uuid,'catalog-only')""", (tenant,))
        finally:
            conn.execute("SET search_path TO public")
    try:
        with (
            monkeypatch.context() as patcher,
        ):
            patcher.setattr(operator, "_tenant", lambda _profile: tenant)
            patcher.setattr(operator, "_connect", connect)
            patcher.setattr(operator, "_ensure_schema", lambda _conn: None)
            patcher.setattr(operator, "_current_profile", lambda *_args: profile)
            patcher.setattr(operator, "_operator_row", integration_row)
            patcher.setattr(operator, "_credentials", lambda _tenant, **_kwargs: dict(active))
            patcher.setattr(operator, "_record_operator_read", lambda *_args: None)
            patcher.setattr(operator, "_pending_auth_candidate",
                            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pending token read")))
            patcher.setattr(operator, "_request_operator", provider_read)
            patcher.setattr(operator, "_fernet", lambda: cipher)
            patcher.setattr(company_import, "current_access", lambda *_args: profile)
            patcher.setattr(company_import, "authorize_create", lambda *_args: None)
            patcher.setattr(company_import, "locked_resource", visible_company)
            patcher.setattr(company_import, "TenantPolicy", FullAccess)

            first = company_import.create_companies(profile)
            assert first == {"checked": 3, "created": 1, "reused": 1, "manual_review": 1,
                "name_collision_count": 1, "name_collision_accounts": ["name-collision"],
                "name_collision_truncated": False}
            with connect() as db:
                first_legacy = db.execute("""SELECT company_id::text AS company_id
                  FROM saas_beinsystems_operator_company_imports
                  WHERE tenant_id=%s::uuid AND external_account='legacy-account'""",
                                          (tenant,)).fetchone()["company_id"]
                assert db.execute("""SELECT count(*) AS n FROM saas_crm_native_companies
                  WHERE tenant_id=%s::uuid AND lower(name)='name-collision'""",
                                  (tenant,)).fetchone()["n"] == 1
                assert db.execute("""SELECT count(*) AS n FROM saas_beinsystems_operator_company_imports
                  WHERE tenant_id=%s::uuid AND external_account='manually-bound'
                    AND company_id=%s::uuid""", (tenant, legacy_company)).fetchone()["n"] == 1
                assert db.execute("""SELECT count(*) AS n FROM saas_beinsystems_operator_company_imports
                  WHERE tenant_id=%s::uuid AND external_account='catalog-only'""",
                                  (tenant,)).fetchone()["n"] == 0

            # A rotated active connection has a disjoint fresh list. It creates only
            # that list's new account and keeps old stable mappings untouched.
            active.update({
                "token": "synthetic-active-token-two",
                "generation": "generation-two",
                "connection_instance_id": instance_two,
            })
            second = company_import.create_companies(profile)
            assert second == {"checked": 2, "created": 1, "reused": 1, "manual_review": 0,
                 "name_collision_count": 0, "name_collision_accounts": [],
                 "name_collision_truncated": False}
            with connect() as db:
                mapping = db.execute("""SELECT external_account,company_id::text AS company_id,
                    last_seen_generation FROM saas_beinsystems_operator_company_imports
                    WHERE tenant_id=%s::uuid ORDER BY external_account""", (tenant,)).fetchall()
                values = {row["external_account"]: row for row in mapping}
                assert values["legacy-account"]["company_id"] == first_legacy
                assert values["legacy-account"]["last_seen_generation"] == "generation-two"
                assert values["manually-bound"]["company_id"] == legacy_company
                assert values["manually-bound"]["last_seen_generation"] == "generation-one"
                assert set(values) == {"legacy-account", "manually-bound", "new-only"}
                assert db.execute("""SELECT count(*) AS n FROM saas_crm_native_companies
                  WHERE tenant_id=%s::uuid""", (tenant,)).fetchone()["n"] == 4
            assert [endpoint for endpoint, _token, _base in requests] == [
                "me", "accounts", "me", "accounts"
            ]
            assert requests[0][1] == "synthetic-active-token-one"
            assert requests[2][1] == "synthetic-active-token-two"
            assert all(base == active["base_url"] for _endpoint, _token, base in requests)
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

def test_company_import_name_collision_report_is_bounded_and_specific():
    import tenant_beinsystems_operator_company_import as company_import
    plan = [
        {"account": f"collision-{index}", "action": "manual_review",
         "reason": "name_collision"}
        for index in range(company_import.MANUAL_REVIEW_ACCOUNT_LIMIT + 3)
    ]
    plan.append({"account": "mapping-conflict", "action": "manual_review"})
    result = company_import._name_collision_summary(plan)
    assert result["name_collision_count"] == company_import.MANUAL_REVIEW_ACCOUNT_LIMIT + 3
    assert result["name_collision_accounts"] == [
        f"collision-{index}" for index in range(company_import.MANUAL_REVIEW_ACCOUNT_LIMIT)
    ]
    assert result["name_collision_truncated"] is True
