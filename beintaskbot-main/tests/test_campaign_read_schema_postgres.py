"""Campaign reads on disposable native PostgreSQL, with no provider calls."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import json
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
import pytest

from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A, B, AGENT, PROFILE
import tenant_ai_agents_schema as agents_schema
import tenant_chat_ai_schema as chat_schema
import tenant_platform_schema as platform_schema
import tenant_knowledge_campaigns as campaigns
from tenant_linear_policy import TenantLinearError


def seed_campaign(conn, status='awaiting_budget'):
    key=str(uuid.uuid4())
    conn.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by) VALUES(%s::uuid,%s::uuid,'Fixture',11)",(A,AGENT))
    conn.execute('''INSERT INTO saas_knowledge_campaigns
      (tenant_id,id,created_by,sources,analysis_agent_id,status,max_calls,max_tokens,reserved_calls,reserved_tokens)
      VALUES(%s::uuid,%s::uuid,11,'[]',%s::uuid,%s,3,1000,1,100)''',(A,key,AGENT,status))
    conn.execute('''INSERT INTO saas_knowledge_campaign_fragments
      (tenant_id,campaign_id,id,source_key,route,status,reservation)
      VALUES(%s::uuid,%s::uuid,%s::uuid,'fixture','{}','unknown',%s::jsonb)''',
      (A,key,str(uuid.uuid4()),json.dumps({'main':1})))
    conn.commit()
    return key


@pytest.mark.parametrize('existing_campaign',[False,True])
def test_parallel_warm_campaign_reads_do_not_upgrade_schema_locks(db,monkeypatch,existing_campaign):
    if existing_campaign:
        with psycopg.connect(db.dsn,row_factory=dict_row) as conn:seed_campaign(conn)
    ddl=[]
    # On the former implementation, both existing-index ShareLocks are held
    # before either ALTER asks for AccessExclusiveLock: a deterministic 40P01.
    # On the fixed implementation neither read reaches this scheduling hook.
    alter_barrier=Barrier(2)
    start_barrier=Barrier(2)

    class ReadCursor(psycopg.Cursor):
        def execute(self,query,params=None,**kwargs):
            if str(query).lstrip().split()[0] in {'CREATE','ALTER','DROP'}:
                ddl.append(str(query).splitlines()[0])
            if str(query).startswith('ALTER TABLE saas_knowledge_campaigns ADD COLUMN IF NOT EXISTS media_policy'):
                alter_barrier.wait(10)
            return super().execute(query,params,**kwargs)

    factory=lambda:psycopg.connect(db.dsn,row_factory=dict_row,cursor_factory=ReadCursor,
        options='-c statement_timeout=15000 -c deadlock_timeout=100')
    monkeypatch.setattr(campaigns,'_connect',factory)

    def read():
        start_barrier.wait(10)
        try:return campaigns.get(PROFILE)
        except psycopg.Error as error:return {'db_error':error.sqlstate}

    with ThreadPoolExecutor(max_workers=2) as pool:
        for _ in range(4):
            futures=[pool.submit(read) for _ in range(2)]
            results=[future.result(timeout=25) for future in futures]
            assert all('campaign' in result for result in results),results
            assert all(bool(result['campaign'])==existing_campaign for result in results)
    assert not ddl,ddl


@pytest.mark.parametrize('status',['running','paused','stopped'])
def test_single_campaign_get_preserves_holds_status_and_tenant_scope(db,monkeypatch,status):
    factory=lambda:psycopg.connect(db.dsn,row_factory=dict_row)
    monkeypatch.setattr(campaigns,'_connect',factory)
    with factory() as conn:
        key=seed_campaign(conn,status)
        before=conn.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,)).fetchall()
        fragments=conn.execute('SELECT * FROM saas_knowledge_campaign_fragments WHERE campaign_id=%s::uuid',(key,)).fetchall()
    for selected in (None,key):
        result=campaigns.get(PROFILE,selected)['campaign']
        assert result['id']==key and result['status']==status
    assert campaigns.get({'tenant_id':B,'telegram_id':22},key)=={'campaign':None}
    with pytest.raises(TenantLinearError):campaigns.get({'tenant_id':A,'telegram_id':999},key)
    with factory() as conn:
        assert conn.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,)).fetchall()==before
        assert conn.execute('SELECT * FROM saas_knowledge_campaign_fragments WHERE campaign_id=%s::uuid',(key,)).fetchall()==fragments


def test_cold_campaign_get_initializes_owned_tables_before_read(database,monkeypatch):
    # A second empty database proves the GET still runs the real owning
    # initializer; an already initialized fixture alone cannot establish this.
    settings=conninfo_to_dict(database.dsn)
    assert settings['host'] in {'127.0.0.1','localhost','::1'}
    name='test_campaign_cold_'+uuid.uuid4().hex
    with psycopg.connect(database.dsn,autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    dsn=make_conninfo(database.dsn,dbname=name)
    factory=lambda:psycopg.connect(dsn,row_factory=dict_row)
    try:
        monkeypatch.setattr(platform_schema,'_schema_ready',False)
        monkeypatch.setattr(agents_schema,'_ready',False)
        monkeypatch.setattr(chat_schema,'_ready',False)
        monkeypatch.setattr(campaigns,'_connect',factory)
        with factory() as conn:
            platform_schema._ensure_schema(conn)
            conn.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s::uuid,%s,'Fixture','active',11)",(A,A))
            conn.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,11,'owner','Fixture')",(A,))
            assert conn.execute("SELECT to_regclass('saas_knowledge_campaigns') AS relation").fetchone()['relation'] is None
        assert campaigns.get(PROFILE)=={'campaign':None}
        with factory() as conn:
            assert conn.execute("SELECT to_regclass('saas_knowledge_quality_checks') AS relation").fetchone()['relation']
            key=seed_campaign(conn)
        assert campaigns.get(PROFILE,key)['campaign']['id']==key
    finally:
        with psycopg.connect(database.dsn,autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
