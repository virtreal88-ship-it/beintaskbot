"""Run the owner duplicate entry points against disposable native PostgreSQL."""
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db
from test_dialog_duplicate_actions import (
    test_browser_reuses_provider_merge_and_exact_rollback,
    test_manual_phone_never_changes_route_or_merge_evidence,
    test_preflight_and_execute_respect_closed_lifecycle_fence,
)
from test_dialog_duplicate_discovery import (
    exact,
    test_default_phone_suffix_is_discovery_only_and_both_uses_and,
    test_owner_only_and_conflicted_lid_has_no_phone,
    test_result_pages_emit_each_route_once_not_cartesian_pairs,
    test_manual_number_is_a_separate_discovery_hint_and_exits_missing_queue,
)
