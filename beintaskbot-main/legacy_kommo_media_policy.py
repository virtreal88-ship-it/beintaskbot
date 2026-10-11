"""Allow-list and normalize external media URLs used by legacy Kommo chats."""

from __future__ import annotations

from urllib.parse import urlparse

from legacy_runtime_config import KOMMO_BASE_URL


_TELEPHONY_MEDIA_SUFFIXES = (
    "sipuni.com", "mango-office.ru", "onlinepbx.ru", "telphin.ru",
    "zadarma.com", "binotel.ua", "uiscom.ru", "callgear.ru",
    "voximplant.com", "mcn.ru", "novofon.com", "gravitel.ru",
    "beeline-cloud.ru", "proto.online", "uis.st",
)
_PROFILE_PHOTO_SUFFIXES = (
    "lookaside.fbsbx.com", "fbcdn.net", "cdninstagram.com",
    "pps.whatsapp.net", "mms.whatsapp.net", "graph.facebook.com",
)


def _media_host(raw: str) -> str:
    try:
        parsed = urlparse(str(raw or "").strip())
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return parsed.netloc.split("@")[-1].split(":")[0].casefold()


def _is_allowed_kommo_media_url(raw: str) -> bool:
    host = _media_host(raw)
    if not host:
        return False
    return (
        host.endswith(".kommo.com")
        or host.endswith(".amocrm.ru")
        or host.endswith(".amocrm.com")
        or host.endswith(".amojo.ru")
        or host in {
            "kommo.com", "amocrm.ru", "amocrm.com",
            "amojo.kommo.com", "amojo.amocrm.ru",
        }
    )


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)


def _is_allowed_avatar_url(raw: str) -> bool:
    if _is_allowed_media_url(raw):
        return True
    host = _media_host(raw)
    return bool(host) and _host_matches(host, _PROFILE_PHOTO_SUFFIXES)


def _is_allowed_media_url(raw: str) -> bool:
    if _is_allowed_kommo_media_url(raw):
        return True
    host = _media_host(raw)
    if not host:
        return False
    if _host_matches(host, _TELEPHONY_MEDIA_SUFFIXES) or _host_matches(host, _PROFILE_PHOTO_SUFFIXES):
        return True
    return host == "whatsapp.net" or host.endswith(".whatsapp.net")


def _as_absolute_media_url(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    if value.startswith("/"):
        value = f"{KOMMO_BASE_URL}{value}"
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return value
    return ""
