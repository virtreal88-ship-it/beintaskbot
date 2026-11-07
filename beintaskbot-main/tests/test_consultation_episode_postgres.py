"""Same episode contracts and cross-entry race on disposable native PostgreSQL."""
from test_consultation_episode import *
from test_connect_channel_multiprocess import database


def test_parallel_header_and_case_prepare_have_one_durable_consultation(ready,monkeypatch):
    import concurrent.futures
    import threading
    import psycopg
    from psycopg.rows import dict_row
    local,data,sender=ready
    case=case_for(local,data)
    monkeypatch.setattr(flow,'_connect',lambda:psycopg.connect(local.dsn,row_factory=dict_row))
    barrier=threading.Barrier(2)
    def prepare(explicit):
        payload={**data,'request_id':str(uuid.uuid4()),'reuse_existing':True,**({'case_id':case} if explicit else {})}
        barrier.wait(10)
        try:return flow.prepare(PROFILE,payload,'https://crm.test')
        except TenantLinearError as error:
            assert error.status==409
            return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(prepare,explicit) for explicit in (False,True)]
        results=[future.result(timeout=20) for future in futures]
    assert any(result and result.get('created') for result in results)
    assert local.execute('SELECT count(*) AS n FROM saas_internal_consultations')[0]['n']==1
    assert local.execute('SELECT count(*) AS n FROM saas_consultation_group_deliveries')[0]['n']==1
    assert not sender.called
