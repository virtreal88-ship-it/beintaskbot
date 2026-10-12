"""Compatibility facade for legacy document, finance, KPI and push storage."""
from legacy_document_storage import init_storage, read_json, write_json
from legacy_finance_ledger import (
    add_balance_transaction, confirm_balance_transaction, get_all_balances,
    get_all_pending_balances, get_all_recent_transactions, get_balance,
    get_balance_transaction_count, get_balance_transactions, get_pending_balance,
)
from legacy_kpi_storage import (
    finish_task_session, get_kpi_summary, has_active_session, pause_task_session,
    set_kpi_score, start_task_session,
)
from legacy_push_storage import (
    get_push_subscription, load_push_subscriptions, remove_push_subscription,
    save_push_subscription,
)

__all__ = [name for name in globals() if not name.startswith("_")]
