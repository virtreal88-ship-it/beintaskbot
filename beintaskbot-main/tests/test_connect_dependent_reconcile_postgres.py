"""Run all dependency contracts on disposable native PostgreSQL too."""
from test_connect_dependent_reconcile import (
    db, test_full_transfer_retry_and_exact_rollback,
    test_audit_error_rolls_back_every_write,
    test_in_flight_or_changed_dependencies_are_not_overwritten,
    test_rollback_rejects_new_messages_and_modified_drafts,
    test_old_links_resolve_only_exact_owner_tenant_channel_and_lead,
    test_wrong_owner_and_new_dependency_stop_without_partial_changes,
    test_new_message_and_stale_preview_block_rollback,
    test_history_paginates_all_messages_and_old_case_anchor_after_merge,
)
from test_connect_channel_multiprocess import database


def test_two_connections_same_request_commit_once(db,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import psycopg
    from psycopg.rows import dict_row
    from test_connect_dependent_reconcile import seed,request,A
    import tenant_connect_direct_reconcile as reconcile
    seed(db,monkeypatch);payload=request()
    monkeypatch.setattr(reconcile,'_connect',lambda:psycopg.connect(db.dsn,row_factory=dict_row))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:reconcile.apply({'tenant_id':A,'telegram_id':11},payload),range(2)))
    assert sum(r.get('idempotent_replay',False) for r in results)==1
    assert db.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE action='connect_direct_merge_merge'")[0]['n']==1
