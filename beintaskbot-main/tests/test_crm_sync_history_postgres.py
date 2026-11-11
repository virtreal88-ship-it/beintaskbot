"""History read contracts and live revocation fences on disposable PostgreSQL."""
from test_company_employees_navigation_postgres import database, db, staff, A
from test_crm_scoped_access_postgres import native
from test_crm_sync_history import (
    journal, matrix, set_scopes, add_event,
    test_outbox_unknown_is_not_downgraded_by_attempt_evidence,
    test_unsent_history_evidence_must_match_exact_legacy_claim,
    test_completed_attempt_does_not_prove_current_queued_claim_finished,
    test_synced_record_with_no_send_marker_is_contradictory,
    test_department_transfer_and_tenant_suspension_apply_to_history,
    test_historical_retry_is_unknown_without_durable_no_send_evidence,
    test_relation_revision_text_and_unknown_errors_never_echo,
    test_history_redacts_snapshots_targets_provider_and_actor,
    test_current_exact_source_read_not_update_grants,
    test_reverse_relation_history_belongs_to_source_not_changed_target,
    test_cross_tenant_archive_and_current_membership,
    test_pagination_is_bounded_stable_and_scope_preserving,
    test_unknown_attempt_overrides_legacy_recorded_success,
    test_http_no_store_context_and_stale_auth,
)
import pytest
import tenant_crm_sync_history as history
from tenant_crm_native_access import CRMAccessError


def test_history_holds_current_read_grant_through_fetch(journal,monkeypatch):
    import concurrent.futures
    import threading
    from test_contacts_directory_postgres import _observe_sql
    add_event(journal,state='pending')
    locked,release,started,finished=[threading.Event() for _ in range(4)]
    def after(sql,cursor):
        if 'FROM saas_crm_native_deals r' in sql and 'FOR UPDATE' in sql:
            locked.set();assert release.wait(5)
    monkeypatch.setattr(history,'_connect',_observe_sql(journal['factory'],after=after))
    def revoke():
        started.set();set_scopes(journal,81,matrix(deals={'update':'all'}));finished.set()
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        read=pool.submit(history.read_history,A,'deal',journal['ids']['deals']['self'],actor_id=81)
        try:
            assert locked.wait(5)
            revoked=pool.submit(revoke)
            assert started.wait(5) and not finished.wait(.2)
        finally:release.set()
        assert read.result(5)['events']
        revoked.result(5)
    with pytest.raises(CRMAccessError):
        history.read_history(A,'deal',journal['ids']['deals']['self'],actor_id=81)
