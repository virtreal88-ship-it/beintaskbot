"""PostgreSQL regression for Operator rate limits and pending-token storage."""
import os
import uuid

import pytest
import psycopg

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
            conn.execute('CREATE TABLE saas_crm_native_contact_conversations (tenant_id uuid,provider text,channel_id uuid,conversation_id text,contact_id uuid)')
            conn.execute('''CREATE TABLE saas_beinsystems_operator_links (
              id uuid,tenant_id uuid,local_entity_type text,local_entity_id uuid,
              external_entity_type text,external_id text,external_scope_id text,
              connection_generation text,connection_instance_id uuid,revoked_at timestamptz,
              verification_basis text,evidence_summary text,verified_at timestamptz,
              verified_by bigint,version int,created_at timestamptz)''')
            conn.execute('INSERT INTO saas_crm_native_contacts VALUES(%s,%s,%s,NULL)',(tenant,contact,company))
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
            def integration(*_):calls.append('metadata');return {'metadata':metadata}
            monkeypatch.setattr(operator,'_operator_row',integration)
            def authorize(*_):calls.append('dialog_access');return tenant,11,object()
            def route(*args,**kwargs):
                calls.append('assert_route');assert args[1]==tenant
                assert kwargs['channel']==channel and kwargs['conversation']=='client'
                return {'connect_channel_id':channel,'conversation_id':'client'}
            monkeypatch.setattr(access,'access',authorize);monkeypatch.setattr(access,'assert_route',route)
            def crm_access(*_):calls.append('crm_access');return profile
            def resource(*args):calls.append('locked_resource');assert args[-2:]==(tenant,contact);return {'id':contact}
            monkeypatch.setattr(crm,'current_access',crm_access);monkeypatch.setattr(crm,'locked_resource',resource)
            def snapshot():return conn.execute('SELECT row_to_json(l)::text AS row FROM saas_beinsystems_operator_links l ORDER BY id').fetchall()
            yield dict(operator=operator,conn=conn,profile=profile,channel=channel,contact=contact,instance=instance,metadata=metadata,calls=calls,snapshot=snapshot)
        finally:
            conn.execute('SET search_path TO public')
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


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
