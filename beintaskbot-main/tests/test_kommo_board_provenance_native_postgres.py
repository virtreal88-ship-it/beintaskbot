"""Same provenance SQL contracts against disposable localhost PostgreSQL."""
from test_kommo_board_provenance_postgres import (
    db, source, board,
    test_schema_and_legitimate_observation_exact_digest_revision,
    test_connection_changed_between_read_and_record_cannot_stamp,
    test_stale_proof_binding_and_tenant_cannot_read_new_cache,
    test_unobserved_cache_change_invalidates_even_allow_latest,
    test_initial_revision_vs_latest_verified_same_source,
    test_missing_table_fails_closed_without_request_time_ddl,
    test_command_refresh_and_cache_write_rollback_atomically,
    test_schema_rejects_invalid_resource_and_nonpositive_id,
    test_same_id_observation_cannot_stamp_changed_source_payload,
    test_observation_digest_survives_mutation_of_shared_payload_object,
    test_legacy_id_only_observation_does_not_mint_board_proof,
    test_lead_contact_enrichment_allowed_but_relationship_and_stage_change_denied,
)
from test_connect_channel_multiprocess import database
