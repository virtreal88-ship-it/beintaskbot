"""Read-only, connection-scoped readiness for consultation request paths."""
import json
from tenant_linear_policy import TenantLinearError
from tenant_schema_preflight import ready

_INTERNAL={
 'saas_internal_consultations':'tenant_id id case_id channel_id conversation_id request_key reason evidence status recipient_id ai_run_id delivery_state created_by created_at answered_by answered_at response response_kind'.split(),
 'saas_consultation_telegram_deliveries':'tenant_id consultation_id recipient_id chat_id binding_verified_at status message_id created_at updated_at'.split(),
}
_GROUP={**_INTERNAL,
 'saas_consultation_group_deliveries':'tenant_id consultation_id request_id case_id channel_id conversation_id body body_hash state connect_message_id provider_message_id created_by created_at updated_at customer_draft draft_reply_id draft_revision'.split(),
 'saas_consultation_group_replies':'tenant_id consultation_id channel_id conversation_id message_id body created_at'.split(),
 'saas_consultation_customer_acks':'tenant_id consultation_id request_id case_id channel_id conversation_id body body_hash state connect_message_id created_at updated_at'.split(),
 'saas_consultation_group_media_deliveries':'tenant_id case_id consultation_id source_external_id channel_id conversation_id request_id state connect_message_id provider_message_id created_at updated_at'.split(),
 'saas_ai_pilot_followups':'tenant_id id consultation_id case_id channel_id conversation_id group_channel_id group_conversation_id group_message_id group_reply source_message_id agent_id event_fingerprint state error_code created_at updated_at customer_outbound_message_id customer_verified_at customer_verification_message_id reply_action awaiting_customer customer_reply_message_id turn_number'.split(),
}
_PUBLIC={
 'saas_public_case_shares':'id tenant_id ticket_id channel_id conversation_id group_channel_id group_conversation_id token_hash selected_message_ids selected_media_ids public_messages created_by revision_key snapshot_revision is_partial created_at updated_at expires_at revoked_at public_identity'.split(),
}
_UNIFIED={**_GROUP,**_PUBLIC,
 'saas_dialog_developer_markers':'tenant_id channel_id conversation_id enabled updated_by updated_at'.split(),
 'saas_dialog_consultation_replies':'tenant_id id consultation_id channel_id conversation_id message_id author body happened_at created_at handled_at handled_by draft draft_state draft_request_id draft_retry_after linked_customer_message_id draft_binding draft_agent_id source_kind source_actor message_type source_external_id followup_requested_at'.split(),
 'saas_dialog_consultation_reads':'tenant_id reply_id actor_id read_at'.split(),
}
_UNIFIED['saas_public_case_shares']=[*_PUBLIC['saas_public_case_shares'],'consultation_id','source_versions']
_INDEXES={
 'internal':{'saas_internal_consultations_case_idx':'saas_internal_consultations'},
 'group':{'saas_internal_consultations_case_idx':'saas_internal_consultations','saas_ai_pilot_one_waiting_customer_idx':'saas_ai_pilot_followups'},
 'public':{'saas_public_case_shares_expiry_idx':'saas_public_case_shares','saas_public_case_shares_revision_idx':'saas_public_case_shares'},
}
_INDEXES['unified']={**_INDEXES['group'],**_INDEXES['public'],'saas_dialog_consultation_pending_idx':'saas_dialog_consultation_replies'}


def schema_ready(cur,layer='unified',*,include_followup=True,include_public_identity=True):
    """Never inherit another database, search_path, or uncommitted cache hint."""
    columns={'internal':_INTERNAL,'group':_GROUP,'public':_PUBLIC,'unified':_UNIFIED}[layer]
    if layer=='unified' and not include_followup:
        columns={**columns,'saas_dialog_consultation_replies':[name for name in columns['saas_dialog_consultation_replies'] if name!='followup_requested_at']}
    if not include_public_identity and 'saas_public_case_shares' in columns:
        columns={**columns,'saas_public_case_shares':[name for name in columns['saas_public_case_shares'] if name!='public_identity']}
    if not ready(cur,columns,[]):return False
    cur.execute('''SELECT NOT EXISTS(SELECT 1 FROM jsonb_each_text(%s::jsonb) expected(name,relation)
      WHERE NOT EXISTS(SELECT 1 FROM pg_index i WHERE i.indexrelid=to_regclass(expected.name)
        AND i.indrelid=to_regclass(expected.relation) AND i.indisvalid)) AS ready''',(json.dumps(_INDEXES[layer]),))
    if not (cur.fetchone() or {}).get('ready'):return False
    if layer!='unified':return True
    cur.execute('''SELECT EXISTS(SELECT 1 FROM pg_constraint WHERE
        conrelid=to_regclass('saas_public_case_shares') AND conname='saas_public_snapshot_owner_check'
        AND contype='c' AND convalidated)
      AND EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass('saas_public_case_shares')
        AND conname='saas_public_consultation_fk' AND contype='f' AND convalidated
        AND confrelid=to_regclass('saas_internal_consultations'))
      AND (%s=FALSE OR EXISTS(SELECT 1 FROM pg_attribute WHERE
        attrelid=to_regclass('saas_dialog_consultation_replies') AND attname='followup_requested_at'
        AND attnum>0 AND NOT attisdropped AND atttypid='timestamptz'::regtype AND NOT attnotnull)) AS ready''',(include_followup,))
    return (cur.fetchone() or {}).get('ready') is True


def require_schema(conn,layer='unified'):
    """Fail closed without DDL, advisory locks, commits, or transaction repair."""
    with conn.cursor() as cur:
        if not schema_ready(cur,layer):
            raise TenantLinearError('Consultation schema is unavailable; startup migration is required.',503)
