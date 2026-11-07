"""Run the case alias binding contract on disposable native PostgreSQL too."""
from test_case_history_scope import (
    db,
    test_case_history_scope_proves_original_and_canonical_with_pagination,
    test_case_history_scope_never_proves_mismatched_authority,
)
from test_connect_channel_multiprocess import database
