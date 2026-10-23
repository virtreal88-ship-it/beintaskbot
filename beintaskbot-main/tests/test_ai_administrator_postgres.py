"""Run the same isolation/security contract against disposable native PostgreSQL."""
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db
from test_ai_administrator_read import (
    test_existing_connection_company_reads_and_immediate_owner_revocation,
    test_all_read_projections_are_valid_bounded_and_tenant_scoped,
    test_message_content_redacts_secrets_and_preserves_ids,
    test_cursor_is_bound_to_tenant_tool_and_content_policy,
    test_simulation_queue_diagnostic_is_tenant_bound_read_only,
)
