"""Real independent-worker storage races; existing disposable localhost DB fixture."""
import multiprocessing,time,uuid
import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db,PROFILE
from test_instruction_library import ready
from test_instruction_videos import setup,clip
import tenant_instruction_library as lib


def upload_child(dsn,row,body,barrier,results):
    import tenant_instruction_videos as videos
    videos.TENANT_STORAGE_BYTES=len(body)
    class Cursor:
        def __init__(self,c):self.c=c
        def execute(self,sql,params=()):
            self.c.execute(sql,params)
            if sql.startswith('SELECT COALESCE(sum(size_bytes)'):
                # Force an overlap window: without the tenant transaction lock,
                # both workers would observe an empty budget and both insert.
                time.sleep(.3)
        def fetchone(self):return self.c.fetchone()
    try:
        barrier.wait(20)
        with psycopg.connect(dsn,row_factory=dict_row) as conn:
            with conn.cursor() as c:
                value=videos.manage(Cursor(c),PROFILE,row['id'],'video_upload',
                  {'expected_version':row['version'],'source_kind':'owner_upload',
                   'metadata':{'task':'inventory save','product':'Akul','product_version':'v1','when_offer':'After text'}},body)
        results.put(('ok',value['id']))
    except Exception as exc:results.put(('blocked',getattr(exc,'status',None),str(exc)))


def test_concurrent_quota_across_lessons_and_workers(setup):
    conn,one,body=setup
    two=lib.operation(PROFILE,'save',{'expected_version':0,'doc':one['doc']},str(uuid.uuid4()))
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2);results=ctx.Queue()
    # Initializing schema is outside worker race; uploads use the same production
    # transaction function with separate process slots/connections.
    import tenant_instruction_videos as videos
    videos.schema(conn);conn.commit()
    workers=[ctx.Process(target=upload_child,args=(conn.dsn,row,body,barrier,results)) for row in (one,two)]
    for worker in workers:worker.start()
    try:
        values=[results.get(timeout=40) for _ in workers]
        for worker in workers:worker.join(10);assert worker.exitcode==0
        assert sorted(v[0] for v in values)==['blocked','ok'],values
        assert [v[1] for v in values if v[0]=='blocked']==[413],values
        assert conn.execute('SELECT sum(size_bytes) AS used,count(*) AS n FROM saas_instruction_videos')[0]=={'used':len(body),'n':1}
    finally:
        for worker in workers:
            if worker.is_alive():worker.terminate();worker.join(5)


def test_concurrent_duplicate_upload_returns_one_asset(setup):
    conn,row,body=setup
    import tenant_instruction_videos as videos
    videos.schema(conn);conn.commit()
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2);results=ctx.Queue()
    workers=[ctx.Process(target=upload_child,args=(conn.dsn,row,body,barrier,results)) for _ in range(2)]
    for worker in workers:worker.start()
    try:
        values=[results.get(timeout=40) for _ in workers]
        for worker in workers:worker.join(10);assert worker.exitcode==0
        assert values[0][0]==values[1][0]=='ok',values
        assert values[0][1]==values[1][1]
        assert conn.execute('SELECT count(*) AS n FROM saas_instruction_videos')[0]['n']==1
    finally:
        for worker in workers:
            if worker.is_alive():worker.terminate();worker.join(5)
