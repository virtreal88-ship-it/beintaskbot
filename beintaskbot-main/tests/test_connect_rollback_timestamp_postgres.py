from test_connect_rollback_timestamp import (
    db,test_verified_touch_dry_run_and_rollback_keep_newer_timestamp,
    test_timestamp_proof_never_masks_other_changes,
    test_second_unverified_touch_and_bad_provenance_are_blocked,
    test_avatar_success_read_does_not_touch_existing_available_route,
)
from test_connect_channel_multiprocess import database


def test_concurrent_writer_cannot_change_data_between_dry_run_and_restore(db,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import psycopg
    from psycopg.rows import dict_row
    from test_connect_rollback_timestamp import merged,touch,certify,r,request,PROFILE
    import tenant_connect_reconcile_dependencies as deps
    from tenant_linear_policy import TenantLinearError
    import pytest
    _,after=merged(db,monkeypatch);current=touch(db);certify(db,after,current)
    r.rollback_preflight(PROFILE,request()['pair_id'])
    # A separate real worker connection updates state after the dry run.
    with psycopg.connect(db.dsn,row_factory=dict_row) as other:
        other.execute("UPDATE saas_ai_agent_jobs SET attempts=attempts+1 WHERE lead_id=8101")
    with pytest.raises(TenantLinearError,match='state_changed'):r.apply(PROFILE,request('rollback'))
    # Fixed-order production locks also fence an overlapping writer.
    with db.cursor() as cur:deps.lock(cur)
    def writer():
        try:
            with psycopg.connect(db.dsn) as other:
                other.execute("SET LOCAL lock_timeout='100ms'")
                other.execute('UPDATE saas_connect_conversations SET manual_hold=true WHERE lead_id=8101')
        except psycopg.errors.LockNotAvailable:return 'fenced'
        return 'unexpected write'
    with ThreadPoolExecutor(max_workers=1) as pool:assert pool.submit(writer).result(timeout=10)=='fenced'
    db.rollback()
