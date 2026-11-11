"""Same SQL contracts plus real-session lock ordering; disposable localhost only."""
from test_company_employees_navigation_postgres import database, db, staff, A
from test_crm_scoped_access_postgres import native, set_scopes, KINDS
from test_crm_source_card_relations import (
    matrix, test_company_deal_relations_use_only_exact_source_update,
    test_deal_relation_capabilities_are_row_specific_and_do_not_open_target_lists,
    test_initial_contact_read_revocation_and_archive_fail_in_creation_transaction,
    test_unlink_is_exact_and_targets_are_same_tenant_active,
    test_initial_contact_requires_deal_create_and_current_contact_read,
    test_outbound_relation_blocker_is_atomic,
    test_source_specific_api_no_export_and_no_field_smuggling,
    test_new_deal_relation_endpoint_cannot_bypass_gate_off_legacy,
)
import tenant_crm_native_companies as companies
import tenant_crm_native_deals as deals


def test_opposite_company_deal_edits_share_deal_first_lock_order(native,monkeypatch):
    import concurrent.futures
    import threading
    from test_contacts_directory_postgres import _observe_sql
    set_scopes(native,81,matrix(companies={'update':'all'},deals={'update':'all'}))
    deal,company=native['ids']['deals']['self'],native['ids']['companies']['self']
    locked,release,waiting=threading.Event(),threading.Event(),threading.Event()
    def after_company(sql,cursor):
        if 'SELECT id FROM saas_crm_native_deals WHERE' in sql and 'FOR UPDATE' in sql:
            locked.set();assert release.wait(5)
    def before_deal(sql,cursor):
        if 'FROM saas_crm_native_deals r' in sql and 'FOR UPDATE' in sql:waiting.set()
    monkeypatch.setattr(companies,'_connect',_observe_sql(native['factory'],after=after_company))
    monkeypatch.setattr(deals,'_connect',_observe_sql(native['factory'],before=before_deal))
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        first=pool.submit(companies.link_entity,A,company,'deal',deal,actor_id=81)
        try:
            assert locked.wait(5)
            second=pool.submit(deals.set_deal_company_link,A,deal,company,actor_id=81)
            assert waiting.wait(5) and not second.done()
        finally:release.set()
        assert first.result(5) and second.result(5)


def test_initial_contact_activity_is_locked_through_create(native,monkeypatch):
    import concurrent.futures
    import threading
    from test_contacts_directory_postgres import _observe_sql
    set_scopes(native,81,matrix(deals={'create':'self'},customers={'view':'self'}))
    contact=native['ids']['customers']['self']
    locked,release,started,finished=[threading.Event() for _ in range(4)]
    def after(sql,cursor):
        if 'FROM saas_crm_native_contacts r' in sql and 'FOR UPDATE' in sql:
            locked.set();assert release.wait(5)
    monkeypatch.setattr(deals,'_connect',_observe_sql(native['factory'],after=after))
    def archive():
        with native['factory']() as conn:
            started.set()
            conn.execute('UPDATE saas_crm_native_contacts SET archived_at=now() WHERE id=%s::uuid',(contact,))
        finished.set()
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        create=pool.submit(deals.create_deal,A,'Initial','p','s',contact_id=contact,actor_id=81)
        try:
            assert locked.wait(5)
            remove=pool.submit(archive)
            assert started.wait(5) and not finished.wait(.2)
        finally:release.set()
        assert str(create.result(5)['contact_id'])==contact
        remove.result(5)
