"""Legacy finance mutations kept outside the web action dispatcher."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from legacy_action_result import LegacyActionResult


@dataclass(frozen=True)
class PayoutContext:
    get_kommo_user_id_for_chat: Callable[[int], int | None]
    add_balance_transaction: Callable[..., Any]
    get_employee_name: Callable[[int, str], str]
    admin_kommo_user_id: int = 10932455


def handle_payout(
    data: Mapping[str, Any], chat_id: int, context: PayoutContext
) -> LegacyActionResult:
    if context.get_kommo_user_id_for_chat(chat_id) != context.admin_kommo_user_id:
        return LegacyActionResult({"success": False, "error": "İcazə yoxdur."})
    try:
        employee_id = int(data.get("employee_id") or 0)
        amount = float(data.get("amount") or 0)
    except (TypeError, ValueError):
        employee_id, amount = 0, 0.0
    note = str(data.get("note") or "Ödəniş")
    if not employee_id or amount <= 0:
        return LegacyActionResult(
            {"success": False, "error": "Məbləğ və əməkdaş seçin."}
        )
    saved = context.add_balance_transaction(
        employee_id,
        0,
        -amount,
        note,
        executor_name=context.get_employee_name(employee_id, str(employee_id)),
        client="—",
        phone="—",
        task_type="Məxaric",
        result_text=note,
        kpi=0,
        status="confirmed",
        transaction_type="məxaric",
    )
    if not saved:
        return LegacyActionResult(
            {"success": False, "error": "Ödəniş yadda saxlanmadı."}, 502
        )
    return LegacyActionResult(
        {"success": True, "message": f"✅ {amount:.2f} AZN ödənildi."}
    )
