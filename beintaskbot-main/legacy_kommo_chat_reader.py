"""Read legacy Kommo Talk and channel history through configured transports."""

from __future__ import annotations

import logging

from legacy_runtime_config import KOMMO_BASE_URL

logger = logging.getLogger(__name__)
_http = None
_raw_http = None
_headers: dict[str, str] = {}
_token = ""


def configure_chat_reader(
    *, http, raw_http, headers: dict[str, str], token: str, app_logger=None,
) -> None:
    global _http, _raw_http, _headers, _token, logger
    _http = http
    _raw_http = raw_http
    _headers = dict(headers)
    _token = str(token or "")
    if app_logger is not None:
        logger = app_logger


def _fetch_talk_messages(
    talk_id: int, pages: int = 1, page_limit: int = 50,
) -> tuple[list[dict], bool, bool]:
    rows: list[dict] = []
    blocked = False
    maybe_more = False
    try:
        talk_id = int(talk_id)
        pages = max(1, int(pages or 1))
        page_limit = max(1, min(int(page_limit or 50), 250))
    except (TypeError, ValueError):
        return [], False, False
    urls = (
        f"{KOMMO_BASE_URL}/api/v4/talks/{talk_id}/messages",
        f"{KOMMO_BASE_URL}/ajax/v4/talks/{talk_id}/messages",
        f"{KOMMO_BASE_URL}/ajax/v2/talks/{talk_id}/messages",
    )
    working_url = ""
    for url in urls:
        try:
            response = _http.get(
                url,
                headers=_headers,
                params={"limit": page_limit, "page": 1, "order[created_at]": "desc"},
                timeout=10,
            )
        except Exception as exc:
            logger.warning("Deal talk %s %s failed: %s", talk_id, url, exc)
            continue
        if response.status_code in {401, 402, 403, 429}:
            blocked = True
            logger.warning("Deal talk %s messages %s status %s", talk_id, url, response.status_code)
            break
        if response.status_code != 200:
            logger.warning("Deal talk %s messages %s status %s", talk_id, url, response.status_code)
            continue
        payload = response.json() if response.content else {}
        raw_messages = (
            (payload.get("_embedded") or {}).get("messages")
            or payload.get("messages")
            or []
        ) if isinstance(payload, dict) else None
        if not isinstance(raw_messages, list):
            continue
        messages = raw_messages
        rows.extend(messages)
        working_url = url
        blocked = False
        maybe_more = len(messages) >= page_limit
        break
    if working_url and pages > 1:
        for page in range(2, pages + 1):
            try:
                response = _http.get(
                    working_url,
                    headers=_headers,
                    params={"limit": page_limit, "page": page, "order[created_at]": "desc"},
                    timeout=10,
                )
            except Exception:
                break
            if response.status_code != 200:
                break
            payload = response.json() if response.content else {}
            raw_messages = (
                (payload.get("_embedded") or {}).get("messages")
                or payload.get("messages")
                or []
            ) if isinstance(payload, dict) else None
            if not isinstance(raw_messages, list):
                break
            messages = raw_messages
            if not messages:
                maybe_more = False
                break
            rows.extend(messages)
            maybe_more = len(messages) >= page_limit
            if len(messages) < page_limit:
                break
    return rows, blocked, maybe_more


def _extract_messages_payload(payload) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    messages = (
        (payload.get("_embedded") or {}).get("messages")
        or payload.get("messages")
        or payload.get("items")
        or []
    )
    return messages if isinstance(messages, list) else []


def _fetch_chat_history_by_chat_id(chat_id: str) -> list[dict]:
    chat_id = str(chat_id or "").strip()
    if not chat_id:
        return []
    urls = (
        f"https://amojo.kommo.com/v1/chats/{chat_id}/history",
        f"https://amojo.kommo.com/v2/chats/{chat_id}/history",
        f"{KOMMO_BASE_URL}/ajax/v4/chats/{chat_id}/history",
        f"{KOMMO_BASE_URL}/ajax/v2/chats/{chat_id}/history",
    )
    for url in urls:
        try:
            response = _raw_http.get(
                url,
                headers={"Authorization": f"Bearer {_token}"},
                params={"limit": 50, "offset": 0},
                timeout=12,
            )
        except Exception as exc:
            logger.warning("Chat history %s failed: %s", url, exc)
            continue
        if response.status_code != 200:
            logger.warning("Chat history %s status %s", url, response.status_code)
            continue
        try:
            messages = _extract_messages_payload(response.json())
        except Exception:
            continue
        if messages:
            return messages
    return []
