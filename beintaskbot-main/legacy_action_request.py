"""Authentication and JSON parsing for the legacy webapp action endpoint."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class ParsedActionRequest:
    data: dict[str, Any]
    chat_id: int | None
    error: str = ""
    status: int = 200


async def parse_action_request(
    request: Any,
    load_users: Callable[[], Mapping[str, Any]],
) -> ParsedActionRequest:
    """Parse one request without trusting a user id from its JSON body."""
    try:
        raw_data = await request.json()
    except Exception:
        return ParsedActionRequest({}, None, "Invalid JSON", 400)
    if not isinstance(raw_data, dict):
        return ParsedActionRequest({}, None, "Invalid JSON", 400)

    raw_user_id = str(request.headers.get("X-TG-User-ID", "") or "").strip()
    users = load_users()
    chat_id: int | None = None
    if raw_user_id in users:
        try:
            chat_id = int(raw_user_id)
        except (TypeError, ValueError):
            chat_id = None
    if chat_id is None:
        try:
            chat_id = int(raw_user_id)
        except (TypeError, ValueError):
            chat_id = None
    if not chat_id:
        return ParsedActionRequest(
            raw_data,
            None,
            "İstifadəçi tapılmadı. Botda /start yazın.",
            403,
        )
    return ParsedActionRequest(raw_data, chat_id)
