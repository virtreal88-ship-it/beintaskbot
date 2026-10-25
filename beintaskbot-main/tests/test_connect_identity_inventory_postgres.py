"""Same historical contracts against disposable native PostgreSQL."""
from test_connect_identity_inventory import (
    db, test_screenshot_shape_reports_unproven_contact_without_guessing,
    test_persisted_alias_without_event_pair_is_discovered_and_survives_restart,
    test_same_customer_on_other_channel_or_tenant_is_never_a_pair,
    test_groups_and_ambiguous_aliases_are_excluded_or_blocked,
    test_inventory_requires_active_owner,
    test_scan_does_not_miss_evidence_after_first_thousand_messages,
    test_existing_reconcile_accepts_alias_evidence_and_preserves_media,
    test_case_evidence_and_assignments_quarantine_physical_reconciliation,
    test_linked_device_echo_and_legacy_rows_are_diagnostic_only,
)
from test_connect_channel_multiprocess import database
