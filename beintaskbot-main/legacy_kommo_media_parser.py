"""Extract and classify media metadata from legacy Kommo payloads."""

from __future__ import annotations

from legacy_kommo_media_policy import (
    _as_absolute_media_url, _is_allowed_avatar_url,
)


def _walk_media_urls(value, depth: int = 0) -> list[str]:
    if depth > 5 or value is None:
        return []
    if isinstance(value, str):
        found = _as_absolute_media_url(value)
        return [found] if found else []
    if isinstance(value, dict):
        preferred = []
        for key in (
            "link", "url", "media", "recording", "recording_link",
            "call_record", "download_link", "file_link", "href",
        ):
            preferred.extend(_walk_media_urls(value.get(key), depth + 1))
        extra = []
        skip = {
            "author", "sender", "avatar", "profile", "icon", "recipient",
            "chat", "talk", "phone", "uniq", "source", "duration",
        }
        for key, inner in value.items():
            if str(key).casefold() not in skip:
                extra.extend(_walk_media_urls(inner, depth + 1))
        return preferred + extra
    if isinstance(value, list):
        found: list[str] = []
        for inner in value:
            found.extend(_walk_media_urls(inner, depth + 1))
        return found
    return []


def _extract_media_url(params: dict) -> str:
    if not isinstance(params, dict):
        return ""
    return next(iter(_walk_media_urls(params)), "")


def _sniff_media_type(body: bytes, hinted: str = "", src: str = "", name: str = "") -> str:
    head = body[:16] if body else b""
    if head.startswith(b"OggS"):
        return "audio/ogg"
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    if head.startswith(b"RIFF") and b"WAVE" in head:
        return "audio/wav"
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "audio/mp4"
    hinted = str(hinted or "").split(";")[0].strip().lower()
    if hinted.startswith("audio/") and hinted != "application/octet-stream":
        return hinted
    low = f"{src} {name}".lower()
    if any(token in low for token in (".mp3", ".mpeg")):
        return "audio/mpeg"
    if ".wav" in low:
        return "audio/wav"
    if any(token in low for token in (".m4a", ".mp4", ".aac")):
        return "audio/mp4"
    if any(token in low for token in (".ogg", ".oga", ".opus")):
        return "audio/ogg"
    return hinted or "application/octet-stream"


def _extract_nested_text(value, depth: int = 0) -> str:
    if depth > 5 or value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "message", "body", "content", "caption", "service"):
            found = _extract_nested_text(value.get(key), depth + 1)
            if found:
                return found
    if isinstance(value, list):
        for inner in value:
            found = _extract_nested_text(inner, depth + 1)
            if found:
                return found
    return ""


def _extract_file_uuid(params, depth: int = 0) -> str:
    if depth > 4 or params is None:
        return ""
    keys = ("file_uuid", "uuid", "fileId", "file_id", "attachment_id")
    if isinstance(params, dict):
        for key in keys:
            value = str(params.get(key) or "").strip()
            if len(value) >= 32:
                return value
        file_object = params.get("file") if isinstance(params.get("file"), dict) else {}
        for key in keys:
            value = str(file_object.get(key) or "").strip()
            if len(value) >= 32:
                return value
        for inner in params.values():
            if isinstance(inner, (dict, list)):
                found = _extract_file_uuid(inner, depth + 1)
                if found:
                    return found
    elif isinstance(params, list):
        for inner in params:
            found = _extract_file_uuid(inner, depth + 1)
            if found:
                return found
    return ""


def _looks_audio_name(name: str) -> bool:
    value = str(name or "").casefold()
    extensions = (".ogg", ".mp3", ".m4a", ".wav", ".opus", ".aac", ".oga", ".webm", ".mpeg")
    return any(value.endswith(ext) for ext in extensions) or "voice" in value or "audio" in value


def _looks_image_name(name: str) -> bool:
    value = str(name or "").casefold()
    extensions = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp")
    markers = ("photo", "image", "şəkil", "sekil", "картинка", "фото")
    return any(value.endswith(ext) for ext in extensions) or any(marker in value for marker in markers)


def _is_media_notice_text(text: str) -> bool:
    value = str(text or "").casefold()
    markers = (
        "вам пришло медиа", "медиа-сообщение",
        "пожалуйста, подождите загрузки", "пожалуйста, подождите",
    )
    return any(marker in value for marker in markers)


def _looks_profile_photo(file_name: str, message_type: str = "") -> bool:
    blob = f"{file_name} {message_type}".casefold()
    return any(marker in blob for marker in (
        "profile", "avatar", "userpic", "фото профил", "pp.jpg", "pp.jpeg",
    ))


def _first_avatar_url(*blobs, depth: int = 0) -> str:
    if depth > 4:
        return ""
    for blob in blobs:
        if isinstance(blob, str):
            url = _as_absolute_media_url(blob)
            if url and _is_allowed_avatar_url(url) and (
                "/profiles/" in url.lower() or _looks_profile_photo(url, "avatar")
            ):
                return url
            continue
        if not isinstance(blob, dict):
            continue
        for key in ("avatar", "avatar_url", "photo", "picture", "icon"):
            raw = blob.get(key)
            if isinstance(raw, dict):
                raw = raw.get("url") or raw.get("link") or raw.get("src") or ""
            url = _as_absolute_media_url(str(raw or ""))
            if url and _is_allowed_avatar_url(url):
                return url
        embedded = blob.get("_embedded") if isinstance(blob.get("_embedded"), dict) else {}
        for item in (embedded.get("contacts") or [])[:3]:
            found = _first_avatar_url(item, depth=depth + 1)
            if found:
                return found
        for key in ("author", "peer", "client", "contact", "chat"):
            nested = blob.get(key)
            if isinstance(nested, dict):
                found = _first_avatar_url(nested, depth=depth + 1)
                if found:
                    return found
    return ""
