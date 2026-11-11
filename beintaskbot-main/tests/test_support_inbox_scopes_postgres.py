"""Run the identical support matrix against the disposable PostgreSQL CI fixture.

PGlite covers SQL semantics locally; this module never substitutes PGlite for
PostgreSQL and skips if BEINTASKBOT_TEST_POSTGRES_DSN is unavailable.
"""
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db
from test_support_inbox_scopes import (
    seeded, test_list_count_search_detail, test_independent_update_and_summary_guard,
    test_create_only_and_upsert_cannot_update_foreign_case,
    test_department_live_changes_and_tenant_fence, test_gate_off_sparse_and_ai_inheritance,
    test_notifications_filter_scoped_tickets_and_counts, test_dialog_projection_does_not_leak_ticket_fields,
    test_aggregate_diagnostics_fail_closed_with_narrow_support,
    test_complete_and_current_member_revocation, test_no_create_without_department_and_no_delete_catalog,
    test_read_only_listing_does_not_enumerate_assignee_options,
)

from test_support_inbox_scopes import test_consultation_replay_requires_exact_actor_case_and_input


def test_consultation_create_retry_and_reply_lock_case_first(db,seeded,monkeypatch):
    """Two real sessions force the old case/consultation cycle deterministically."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import psycopg
    from psycopg.rows import dict_row
    import tenant_internal_consultations as consultations
    import tenant_dialog_consultations as unified
    from test_support_inbox_scopes import configure
    from test_tenant_support_inbox import A
    keys,_,_=seeded
    profile=configure(db,view='none',update='self')
    request={'case_id':keys[0],'request_key':'race-retry','recipient_id':31,'reason':'Synthetic race'}
    first=consultations.create(profile,request)
    db.commit()
    created_lock=threading.Event();reply_case_attempt=threading.Event()
    trace=[]
    class RaceCursor(psycopg.Cursor):
        def execute(self,query,params=None,**kwargs):
            sql=str(query);thread=threading.current_thread().name
            case_lock='saas_support_tickets' in sql and 'FOR UPDATE' in sql
            consultation_lock='saas_internal_consultations' in sql and 'FOR UPDATE' in sql
            if case_lock or consultation_lock:trace.append((thread,'case' if case_lock else 'consultation'))
            if thread=='scope-reply' and case_lock:reply_case_attempt.set()
            result=super().execute(query,params,**kwargs)
            if thread=='scope-create' and case_lock and not created_lock.is_set():
                created_lock.set()
                assert reply_case_attempt.wait(5),'reply never attempted the case lock'
            return result
    def connection():
        return psycopg.connect(db.dsn,row_factory=dict_row,cursor_factory=RaceCursor,
                               options='-c statement_timeout=8000')
    monkeypatch.setattr(consultations,'_connect',connection)
    # The race concerns document locks, not reply materialization or providers.
    monkeypatch.setattr(unified,'record_manual_reply',lambda *args,**kwargs:None)
    def run(name,operation):
        threading.current_thread().name=name
        return operation()
    with ThreadPoolExecutor(max_workers=2) as pool:
        create=pool.submit(run,'scope-create',lambda:consultations.create(profile,request))
        assert created_lock.wait(5),'retry never locked its case'
        reply=pool.submit(run,'scope-reply',lambda:consultations.reply(profile,first['id'],{'response':'Local diagnosis','kind':'diagnosis'}))
        assert create.result(timeout=12)['id']==first['id']
        assert reply.result(timeout=12)['status']=='answered'
    reply_locks=[kind for thread,kind in trace if thread=='scope-reply']
    assert reply_locks[0]=='case' and 'consultation' in reply_locks
