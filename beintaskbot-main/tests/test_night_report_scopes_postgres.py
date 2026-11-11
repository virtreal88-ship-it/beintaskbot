"""Run the report scope matrix unchanged on disposable native PostgreSQL too."""
from test_night_report_scopes import (
    db, night_db, scoped_night,
    test_scoped_lists_counts_summaries_failures_and_details,
    test_none_and_malformed_report_cells_override_legacy,
    test_current_permissions_departments_sources_and_membership,
    test_current_tenant_status_denies,
    test_ticket_linked_history_never_falls_back_to_entire_source_or_other_cases,
    test_scoped_pages_and_catalog_do_not_grant_other_sections,
    test_legacy_guard_and_gate_off_behavior,
    test_owner_master_and_no_provider_or_settings_change,
    test_narrow_report_defaults_do_not_disclose_other_agent_schedule,
    test_ticket_pagination_does_not_reveal_other_rows_or_counts,
)
from test_connect_channel_multiprocess import database
from test_night_report_scopes import (
    test_opaque_access_snapshot_rejects_stale_pages_and_detail,
    test_only_permitted_rows_influence_access_snapshot,
    test_exact_message_snapshot_rejects_removed_material_on_earlier_page,
    test_http_report_routes_reauthorize_and_never_mutate,
)
from test_night_report_scopes import (
    test_legacy_scoped_transitions_have_non_null_snapshots,
    test_legacy_history_snapshots_also_detect_removed_messages,
)
