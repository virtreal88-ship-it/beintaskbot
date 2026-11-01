"""Real PostgreSQL: request DDL must not invert Connect/Spend transaction locks."""
from concurrent.futures import ThreadPoolExecutor
import threading
import uuid

import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database  # disposable localhost only
from test_tenant_support_inbox import db, A, CA, PROFILE
import tenant_ai_agents_schema as agents_schema
import tenant_ai_spend_budget as spend
import tenant_connect_transport as connect
import tenant_operator_dialogs as dialogs
import tenant_support_inbox_store as tickets


def test_create_ticket_against_ingest_row_and_nested_schema_never_deadlocks(db, monkeypatch):
    factory=lambda:psycopg.connect(db.dsn,row_factory=dict_row)
    monkeypatch.setattr(tickets,'_connect',factory)
    monkeypatch.setattr(tickets,'summarize_created',lambda *a:None)
    row_held,read_schema_done=threading.Event(),threading.Event()
    original=tickets.ensure_schema
    def request_schema(conn):
        original(conn);read_schema_done.set()
    monkeypatch.setattr(tickets,'ensure_schema',request_schema)
    request_id=str(uuid.uuid4())
    def create():
        assert row_held.wait(10)
        try:return ('created',tickets.create(PROFILE,{'request_id':request_id,'lead_id':41})['ticket']['id'])
        except psycopg.Error as error:return ('db_error',error.sqlstate,type(error).__name__)
    def ingest_schema_boundary():
        try:
            with factory() as conn:
                conn.execute('SET LOCAL statement_timeout = 8000')
                conn.execute("UPDATE saas_connect_conversations SET contact_name=contact_name WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id='client'",(A,CA))
                row_held.set();assert read_schema_done.wait(10)
                agents_schema.ensure_schema(conn)
            return ('ingested',)
        except psycopg.Error as error:return ('db_error',error.sqlstate,type(error).__name__)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures=[executor.submit(ingest_schema_boundary),executor.submit(create)]
        outcomes=[future.result(timeout=20) for future in futures]
    assert all(result[0]!='db_error' for result in outcomes),outcomes
    original_id=next(result[1] for result in outcomes if result[0]=='created')
    assert tickets.create(PROFILE,{'request_id':request_id,'lead_id':41})['ticket']['id']==original_id
    with factory() as conn:
        assert conn.execute('SELECT count(*) AS count FROM saas_support_tickets WHERE tenant_id=%s::uuid',(A,)).fetchone()['count']==1


def test_warm_schema_uses_no_exclusive_table_locks_or_implicit_commit(db):
    with psycopg.connect(db.dsn,row_factory=dict_row) as conn:
        conn.execute("UPDATE saas_connect_conversations SET contact_name='uncommitted-marker' WHERE tenant_id=%s::uuid AND lead_id=41",(A,))
        spend.ensure_schema(conn);connect._schema(conn)
        locks=conn.execute("SELECT relation::regclass::text AS relation FROM pg_locks WHERE pid=pg_backend_pid() AND mode='AccessExclusiveLock' AND relation IS NOT NULL").fetchall()
        assert not locks,locks
        conn.rollback()
    assert db.execute('SELECT contact_name FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND lead_id=41',(A,))[0]['contact_name']=='Client'
    db.commit()


def test_legacy_spend_migration_survives_rollback_and_is_not_cached(db):
    from tenant_schema_preflight import ready
    factory=lambda:psycopg.connect(db.dsn,row_factory=dict_row)
    retained=str(uuid.uuid4())
    with factory() as conn:
        conn.execute("INSERT INTO saas_ai_spend_reservations(tenant_id,id,call_key,model,kind,reserved_microusd,state) VALUES(%s::uuid,%s::uuid,'preserve-unknown','fixture-model','fixture',713,'unknown')",(A,retained))
        conn.execute('ALTER TABLE saas_ai_spend_reservations DROP COLUMN media_units')
    try:
        with factory() as migration:
            spend.ensure_schema(migration)
            with factory() as observer:
                observer.execute('SET LOCAL statement_timeout=2000')
                with observer.cursor() as cur:assert not ready(cur,spend._SCHEMA_COLUMNS,spend._SCHEMA_INDEXES)
            migration.rollback()
        with factory() as migration:
            spend.ensure_schema(migration)
        with factory() as observer:
            with observer.cursor() as cur:assert ready(cur,spend._SCHEMA_COLUMNS,spend._SCHEMA_INDEXES)
            row=observer.execute('SELECT reserved_microusd,state FROM saas_ai_spend_reservations WHERE id=%s::uuid',(retained,)).fetchone()
            assert row=={'reserved_microusd':713,'state':'unknown'}
    finally:
        with factory() as conn:spend.ensure_schema(conn)


def test_legacy_connect_column_and_nested_index_are_not_skipped(db):
    with psycopg.connect(db.dsn,row_factory=dict_row) as conn:
        conn.execute('ALTER TABLE saas_connect_conversations DROP COLUMN manual_display_name')
        conn.execute('DROP INDEX saas_connect_dialog_links_canonical')
    try:
        with psycopg.connect(db.dsn,row_factory=dict_row) as conn:connect._schema(conn)
        with psycopg.connect(db.dsn,row_factory=dict_row) as conn:
            from tenant_schema_preflight import ready
            with conn.cursor() as cur:assert ready(cur,connect._SCHEMA_COLUMNS,connect._SCHEMA_INDEXES)
    finally:
        with psycopg.connect(db.dsn,row_factory=dict_row) as conn:connect._schema(conn)


def test_schema_readiness_is_scoped_to_committed_search_path(db):
    from tenant_schema_preflight import ready
    with psycopg.connect(db.dsn,row_factory=dict_row) as conn:
        with conn.cursor() as cur:assert ready(cur,spend._SCHEMA_COLUMNS,spend._SCHEMA_INDEXES)
        conn.execute('CREATE SCHEMA isolated_schema_check')
        conn.execute('SET LOCAL search_path=isolated_schema_check')
        with conn.cursor() as cur:assert not ready(cur,spend._SCHEMA_COLUMNS,spend._SCHEMA_INDEXES)
        spend.ensure_schema(conn)
        with conn.cursor() as cur:assert ready(cur,spend._SCHEMA_COLUMNS,spend._SCHEMA_INDEXES)
        conn.rollback()  # No readiness survives the rolled-back namespace.
    with psycopg.connect(db.dsn,row_factory=dict_row) as conn:
        assert conn.execute("SELECT to_regnamespace('isolated_schema_check') AS present").fetchone()['present'] is None


def test_parallel_repeated_case_create_dialog_reads_and_ingest_schema(db,monkeypatch):
    factory=lambda:psycopg.connect(db.dsn,row_factory=dict_row,options='-c statement_timeout=8000')
    monkeypatch.setattr(tickets,'_connect',factory);monkeypatch.setattr(dialogs,'_connect',factory)
    monkeypatch.setattr(tickets,'summarize_created',lambda *a:None)
    same_request=str(uuid.uuid4());barrier=threading.Barrier(10)
    def operation(index):
        barrier.wait(10)
        if index<6:
            key=same_request if index<4 else str(uuid.uuid4())
            return ('case',tickets.create(PROFILE,{'request_id':key,'lead_id':41})['ticket']['id'])
        if index==6:return ('list',len(dialogs.listing(PROFILE)['dialogs']))
        if index==7:return ('history',len(dialogs.history(PROFILE,'connect',41,CA,'client')['messages']))
        with factory() as conn:
            conn.execute("UPDATE saas_connect_conversations SET contact_name=contact_name WHERE tenant_id=%s::uuid AND lead_id=41",(A,))
            agents_schema.ensure_schema(conn)
        return ('ingest_schema',)
    with ThreadPoolExecutor(max_workers=10) as executor:
        outcomes=list(executor.map(operation,range(10)))
    assert len({result[1] for result in outcomes if result[0]=='case'})==1
    with factory() as conn:
        assert conn.execute('SELECT count(*) AS count FROM saas_support_tickets WHERE tenant_id=%s::uuid',(A,)).fetchone()['count']==1
        assert conn.execute('SELECT count(*) AS count FROM saas_support_events WHERE tenant_id=%s::uuid AND event_key=%s',(A,'manual:'+same_request)).fetchone()['count']==1


def test_missing_schemas_initialize_serially_without_partial_ready_cache(db):
    factory=lambda:psycopg.connect(db.dsn,row_factory=dict_row)
    with factory() as conn:
        conn.execute('ALTER TABLE saas_ai_spend_reservations DROP COLUMN media_units')
        conn.execute('ALTER TABLE saas_connect_conversations DROP COLUMN manual_display_name')
    barrier=threading.Barrier(2)
    def initialize(reverse):
        with factory() as conn:
            conn.execute('SET LOCAL statement_timeout=6000');barrier.wait(10)
            helpers=(connect._schema,spend.ensure_schema) if reverse else (spend.ensure_schema,connect._schema)
            for helper in helpers:helper(conn)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:list(executor.map(initialize,(True,False)))
        with factory() as conn:
            from tenant_schema_preflight import ready
            with conn.cursor() as cur:
                assert ready(cur,spend._SCHEMA_COLUMNS,spend._SCHEMA_INDEXES)
                assert ready(cur,connect._SCHEMA_COLUMNS,connect._SCHEMA_INDEXES)
    finally:
        with factory() as conn:spend.ensure_schema(conn);connect._schema(conn)


from test_integrated_connect_release import flow, _signed_inbound_event


def test_optional_simulation_is_prepared_before_the_ingest_business_transaction(flow,monkeypatch):
    import json
    import tenant_ai_simulation_schema as simulation_schema
    f=flow;db=f['db'];monkeypatch.setenv('AI_SIMULATION_ENABLED','true')
    monkeypatch.setattr(connect,'_verify_signature',lambda *_:True)
    secret=connect._fernet().encrypt(json.dumps({'webhook_secret':'test-secret'}).encode())
    db.execute("UPDATE saas_tenant_integrations SET secrets=%s WHERE tenant_id=%s::uuid AND provider='connect'",(secret,A));db.commit()
    message_id,payload=_signed_inbound_event();observed=[]
    original=simulation_schema.ensure_schema
    def inspect(conn):
        with conn.cursor() as cur:
            cur.execute('SELECT count(*) AS count FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s',(A,'connect:'+message_id))
            observed.append(cur.fetchone()['count'])
        original(conn)
    monkeypatch.setattr(simulation_schema,'ensure_schema',inspect)
    assert connect.ingest(json.dumps(payload).encode(),'synthetic-signature',payload)
    assert observed==[0],'a committing schema initializer must run before durable CRM writes'
    assert db.execute('SELECT count(*) AS count FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s',(A,'connect:'+message_id))[0]['count']==1
    f['send'].assert_not_called();f['generate'].assert_not_called();f['speech'].assert_not_called()
    db.commit()


def test_indexes_from_a_hidden_schema_cannot_mark_the_selected_table_ready(db):
    from tenant_schema_preflight import ready
    with psycopg.connect(db.dsn,row_factory=dict_row) as conn:
        conn.execute('CREATE SCHEMA shadow_schema_check')
        conn.execute('CREATE TABLE shadow_schema_check.saas_ai_spend_reservations (LIKE public.saas_ai_spend_reservations INCLUDING DEFAULTS)')
        conn.execute('SET LOCAL search_path=shadow_schema_check,public')
        with conn.cursor() as cur:assert not ready(cur,spend._SCHEMA_COLUMNS,spend._SCHEMA_INDEXES)
        spend.ensure_schema(conn)
        with conn.cursor() as cur:assert ready(cur,spend._SCHEMA_COLUMNS,spend._SCHEMA_INDEXES)
        assert conn.execute("SELECT count(*) AS count FROM pg_index WHERE indrelid='shadow_schema_check.saas_ai_spend_reservations'::regclass").fetchone()['count']==2
        conn.rollback()
