"""Resolve the exact Kommo Talk that belongs to a requested reply channel."""

from __future__ import annotations

from legacy_kommo_chat_channels import (
    _channels_from_talks, _talk_channel_key, _talk_chat_id, _talk_id_of,
    _talk_is_whatsapp_business,
)


_fetch_talks = lambda _lead_id, _contact_ids: []
_lead_contact_ids = lambda _lead: []
_remembered_talk_id = lambda _lead_id: 0


def configure_chat_resolver(*, fetch_talks, lead_contact_ids, remembered_talk_id) -> None:
    global _fetch_talks, _lead_contact_ids, _remembered_talk_id
    _fetch_talks = fetch_talks
    _lead_contact_ids = lead_contact_ids
    _remembered_talk_id = remembered_talk_id


def _resolve_channel_talk(
    lead: dict, channel: str, sender_digits: str = "", hinted: int = 0,
) -> tuple[int, str]:
    try:
        hinted_id = int(hinted or 0)
    except (TypeError, ValueError):
        hinted_id = 0
    try:
        lead_id = int(lead.get("id") or 0)
    except (TypeError, ValueError, AttributeError):
        lead_id = 0
    talks = _fetch_talks(lead_id, _lead_contact_ids(lead))
    wanted = str(channel or "whatsapp").strip().lower() or "whatsapp"
    channels = _channels_from_talks(talks, sender_digits)
    selected = next((item for item in channels if item.get("key") == wanted), None)

    def requested(actual: str) -> bool:
        return str(actual or "").strip().lower() == wanted

    if hinted_id:
        hinted_talk = next((talk for talk in talks if _talk_id_of(talk) == hinted_id), None)
        hinted_row = next(
            (item for item in channels if int(item.get("talk_id") or 0) == hinted_id), None,
        )
        if (
            hinted_talk
            and not _talk_is_whatsapp_business(hinted_talk)
            and requested(_talk_channel_key(hinted_talk))
        ):
            return hinted_id, _talk_chat_id(hinted_talk)
        if hinted_row and requested(str(hinted_row.get("key") or "")):
            return hinted_id, str(hinted_row.get("chat_id") or "")
    remembered_id = int(_remembered_talk_id(lead_id) or 0)
    if remembered_id:
        remembered = next((talk for talk in talks if _talk_id_of(talk) == remembered_id), None)
        if (
            remembered
            and not _talk_is_whatsapp_business(remembered)
            and requested(_talk_channel_key(remembered))
        ):
            return remembered_id, _talk_chat_id(remembered)
    if selected and selected.get("talk_id"):
        return int(selected.get("talk_id") or 0), str(selected.get("chat_id") or "")
    return 0, ""


def _resolve_channel_talk_id(
    lead: dict, channel: str, sender_digits: str = "", hinted: int = 0,
) -> int:
    talk_id, _chat_id = _resolve_channel_talk(lead, channel, sender_digits, hinted)
    return talk_id
