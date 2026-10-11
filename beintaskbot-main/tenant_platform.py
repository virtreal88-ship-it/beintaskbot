"""Stable compatibility facade for tenant platform repositories.

New code should import the focused repository module it needs. Existing callers
may continue importing these names from tenant_platform while the legacy
application is migrated incrementally.
"""
from tenant_platform_core import (
    TenantPlatformError, _connect, _database_url, _ensure_schema, _fernet,
    _json, _kommo_domain,
)
from tenant_platform_common import (
    _DEFAULT_MODULES, _DEFAULT_NOTIFICATIONS, _ROLE_PERMISSIONS,
    _invite_token, _permissions, _role, _slug, _tenant_payload,
)
from tenant_linear_account_store import create_linear_account, list_linear_accounts
from tenant_linear_news_store import (
    delete_linear_news, list_linear_news, mark_linear_news_published,
    save_linear_news_classification, sync_linear_news_environments,
    update_linear_news_review, upsert_linear_news,
)
from tenant_linear_news_publication_store import (
    claim_linear_news_publish_slot, prune_linear_news,
    require_linear_news_review_once, reset_linear_news_publishing_once,
    restore_linear_news_publications_once, seed_linear_news_review_once,
)
from tenant_workflow_store import (
    _read_tenant_workflow, list_tenant_workflow_config,
    save_tenant_workflow_config,
)
from tenant_crm_store import (
    _public_crm_row, append_audit_event, get_crm_deal, get_crm_task,
    list_crm_deals, list_crm_messages, list_crm_tasks, upsert_crm_deals,
    upsert_crm_messages, upsert_crm_tasks,
)
from tenant_member_store import (
    _member_payload, create_tenant, deactivate_member, finalize_onboarding,
    list_members, member, tenants_for_user, update_onboarding, upsert_member,
)
from tenant_invite_store import (
    create_invite, decide_invite_request, list_invite_requests,
    request_invite_acceptance,
)
from tenant_integration_store import (
    begin_kommo_oauth, consume_kommo_oauth_state, disconnect_integration,
    kommo_credentials, list_integrations, replace_kommo_tokens,
    request_kommo_connection, save_kommo_oauth_tokens,
)
