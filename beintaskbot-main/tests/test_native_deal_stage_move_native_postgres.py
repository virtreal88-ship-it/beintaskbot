"""Run the stage-move contracts on disposable localhost native PostgreSQL too."""
from test_native_deal_stage_move_postgres import (
    db, graph, scalar, stage_move,
    test_only_stage_and_revision_change_and_scalar_receipt_is_narrow,
    test_stale_cas_does_not_overwrite_newer_fields_or_repeat_automation,
    test_current_actor_tenant_and_pipeline_denials_preserve_rows,
    test_stage_entry_automation_runs_once_and_noop_keeps_revision,
)
from test_connect_channel_multiprocess import database
