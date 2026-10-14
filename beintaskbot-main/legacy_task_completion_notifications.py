"""Delivery policy for a completed task in the legacy workspace.

The HTTP handler owns task completion.  This adapter owns the side effects
that follow it: an optional piecework record, an owner approval request and
an informational notice to the task creator.  All platform-specific services
are injected so this module does not import the running bot.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
import re
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class TaskCompletionNotificationContext:
    logger: Any
    timezone: tzinfo
    task_type_names: Mapping[int, str]
    kommo_users: Mapping[int, str]
    stage_names: Mapping[int, str]
    telegram_token: str
    task_creators_file: Any
    rufat_chat_id: int
    name_to_chat: Mapping[str, int]
    http_post: Callable[..., Any]
    get_employee_name: Callable[[int, str], str]
    get_kommo_user_id: Callable[[int], int]
    get_employee_type: Callable[[int], str]
    add_balance_transaction: Callable[..., Any]
    get_admin_chat: Callable[[int], int | None]
    requires_approval: Callable[[int], bool]
    get_lead_details: Callable[[int], Mapping[str, Any] | None]
    notification_enabled: Callable[[int, str, str], bool]
    save_pending_action: Callable[..., Any]
    send_push_to_admin: Callable[..., Any]
    read_json: Callable[[Any], Any]


def _task_text_without_marker(value: object) -> str:
    return re.sub(r"^\[[^\]]+\]\s*", "", str(value or "").strip())


def notify_legacy_task_completion(
    context: TaskCompletionNotificationContext,
    *,
    chat_id: int,
    task_id: int,
    task_data: Mapping[str, Any],
    task_type_id: int,
    task_deadline_ts: int,
    kpi_result: Mapping[str, Any] | None,
    delay_reason: str,
    lead_id: int | None,
    contact_name: str,
    phone: str,
    note_text: str,
    task_result_text: str,
    link: str,
    task_creator_is_rufat: bool,
) -> None:
    """Preserve the legacy completion-notification contract and side effects."""
    try:
        context.logger.info("complete_task: ENTERING notification block")
        task_text_full = str(task_data.get("text") or "").strip()
        task_desc_display = _task_text_without_marker(task_text_full)
        task_type_name = context.task_type_names.get(task_type_id, f"Tip {task_type_id}")
        completion_sender = context.get_employee_name(
            chat_id,
            context.kommo_users.get(context.get_kommo_user_id(chat_id), "Əməkdaş"),
        )
        try:
            price_match = re.match(r"^\[(?:[^:\]]*:)?(\d+(?:\.\d+)?)\]\s*(.*)", task_text_full)
            if context.get_employee_type(chat_id) == "piecework" and price_match and price_match.group(1):
                amount = float(price_match.group(1))
                if amount > 0:
                    context.add_balance_transaction(
                        chat_id, int(task_id), amount,
                        price_match.group(2) or task_text_full,
                        executor_name=completion_sender, client=contact_name or "—",
                        phone=phone or "—", task_type=task_type_name,
                        result_text=note_text or task_result_text, kpi=0, status="pending",
                    )
        except Exception as exc:
            context.logger.error("Pending balance transaction error: %s", exc)

        admin_chat = context.get_admin_chat(10932455) or 1628569350
        context.logger.info(
            "complete_task notify: admin_chat=%s, contact=%s, creator_is_rufat=%s",
            admin_chat, contact_name, task_creator_is_rufat,
        )
        if admin_chat and context.requires_approval(chat_id):
            deadline_display = (
                datetime.fromtimestamp(task_deadline_ts, tz=context.timezone).strftime("%d.%m.%Y %H:%M")
                if task_deadline_ts else "—"
            )
            kpi_info = ""
            if kpi_result:
                timing_label = "vaxtında" if kpi_result["completed_before_deadline"] else "gecikib"
                kpi_info = f"\n📊 KPI: {kpi_result['kpi_score']}/100 ({timing_label})"
                if delay_reason:
                    kpi_info += f"\n⚠️ Səbəb: {delay_reason}"
            current_stage_name = "—"
            if lead_id:
                try:
                    lead_detail = context.get_lead_details(int(lead_id))
                    if lead_detail:
                        status_id = lead_detail.get("status_id")
                        current_stage_name = context.stage_names.get(status_id, f"ID:{status_id}")
                except Exception:
                    pass
            completion_message = (
                f"✅ {completion_sender} tapşırığı tamamladı:\n\n"
                f"👤 {contact_name or '—'}\n📝 {task_desc_display or '—'}\n"
                f"📞 {phone or '—'}\n⏰ {deadline_display}\n"
                f"📋 {task_type_name}\n📌 Mərhələ: {current_stage_name}"
            )
            if note_text:
                completion_message += f"\n💬 İcraçı qeydi: {note_text}"
            completion_message += kpi_info
            if link:
                completion_message += f"\n🔗 {link}"
            raw_task_text = task_text_full
            price_match = re.match(r"^\[(?:[^:\]]*:)?(\d+(?:\.\d+)?)\]", raw_task_text)
            try:
                if context.notification_enabled(admin_chat, "approval", "telegram"):
                    context.http_post(
                        f"https://api.telegram.org/bot{context.telegram_token}/sendMessage",
                        json={"chat_id": admin_chat, "text": completion_message + "\n\nTəsdiq səhifəsində yoxlayın.",
                              "disable_web_page_preview": True}, timeout=8,
                    )
                context.save_pending_action("change_stage", {
                    "contact_name": contact_name or "—", "phone": phone or "—", "lead_id": lead_id,
                    "task_id": int(task_id), "sender_name": completion_sender, "sender_chat_id": int(chat_id),
                    "task_text": _task_text_without_marker(raw_task_text) or "—",
                    "task_price": price_match.group(1) if price_match else "", "note": note_text or "",
                    "completion_kind": "employee_review", "stage_name": current_stage_name,
                    "description": "Əməkdaş işi tamamladı. Admin təsdiqi gözlənilir.", "link": link,
                }, ["Təsdiq et", "Rədd et"])
                context.send_push_to_admin(
                    f"{completion_sender}: {contact_name or '—'}",
                    title="✅ Sifariş tamamlandı — təsdiq gözləyir", url="#pending", event="approval",
                )
            except Exception as exc:
                context.logger.error("Completion notification error: %s", exc)

        try:
            creators = context.read_json(context.task_creators_file) or {}
            creator_name = creators.get(str(task_id), "")
            creator_chat = context.rufat_chat_id if task_creator_is_rufat else (
                context.name_to_chat.get(creator_name) if creator_name else None
            )
            if creator_chat and int(creator_chat) != int(chat_id):
                creator_message = (
                    f"✅ Sizin yaratdığınız tapşırıq tamamlandı:\n\n"
                    f"👤 {contact_name or '—'}\n📝 {task_desc_display or '—'}\n"
                    f"👷 İcraçı: {completion_sender}\n📞 {phone or '—'}"
                )
                if link:
                    creator_message += f"\n🔗 {link}"
                context.http_post(
                    f"https://api.telegram.org/bot{context.telegram_token}/sendMessage",
                    json={"chat_id": int(creator_chat), "text": creator_message,
                          "disable_web_page_preview": True}, timeout=8,
                )
        except Exception as exc:
            context.logger.warning("Cavabdeh notification error: %s", exc)
    except Exception:
        context.logger.exception("complete_task notification block error")
