"""Build a role-safe recent conversation context for CRM AI actions."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable


def _is_history_audio(item: dict) -> bool:
    kind = str(item.get("message_type") or item.get("type") or "").casefold()
    source = " ".join(
        str(item.get(key) or "")
        for key in ("media_url", "media", "voice_url", "file_name")
    )
    return kind in {"audio", "voice", "ptt", "call", "call_in", "call_out"} or bool(
        re.search(r"\.(ogg|opus|mp3|m4a|wav|aac)(\?|$)", source, re.I)
    )


def _history_message_is_incoming(item: dict) -> bool:
    """Normalize booleans and direction strings returned by different sources."""
    incoming = item.get("incoming")
    if isinstance(incoming, bool):
        return incoming
    if isinstance(incoming, (int, float)):
        return bool(incoming)
    if isinstance(incoming, str):
        normalized = incoming.strip().casefold()
        if normalized in {"true", "1", "yes", "in", "incoming", "received", "client", "contact"}:
            return True
        if normalized in {"false", "0", "no", "out", "outgoing", "sent", "manager", "user"}:
            return False
    direction = str(item.get("direction") or item.get("message_direction") or "").strip().casefold()
    if direction in {"in", "incoming", "received", "client", "contact"}:
        return True
    if direction in {"out", "outgoing", "sent", "manager", "user"}:
        return False
    outgoing = item.get("outgoing")
    if isinstance(outgoing, bool):
        return not outgoing
    if isinstance(outgoing, (int, float)):
        return not bool(outgoing)
    return False


async def _ai_history_lines(
    rows: list[dict],
    lead: dict | None = None,
    *,
    reply_context: bool = False,
    transcribe: Callable[[dict, dict | None], str],
) -> tuple[list[str], int, int]:
    """Return at most 30 messages with stable client/manager roles."""
    lines: list[str] = []
    transcribed = 0
    unavailable_voice = 0
    recent = [item for item in rows if isinstance(item, dict)][-30:]
    for item in recent:
        text = str(item.get("text") or "").strip()
        who = "Müştəri" if _history_message_is_incoming(item) else "Satış meneceri"
        if text:
            limit = 6000 if reply_context else 400
            clipped = text[:limit]
            if reply_context and len(text) > limit:
                clipped += " [mətnin davamı ixtisar edilib]"
            lines.append(f"{who}: {clipped}")
        if _is_history_audio(item):
            transcript = str(item.get("transcript") or item.get("transcription") or "").strip()
            if not transcript:
                transcript = await asyncio.to_thread(transcribe, item, lead)
            if transcript:
                audio_kind = str(item.get("message_type") or item.get("type") or "").casefold()
                label = (
                    "zəng yazısının mətni"
                    if audio_kind in {"call", "call_in", "call_out"}
                    else "səsli mesajın mətni"
                )
                if reply_context and len(transcript) > 12000:
                    transcript = transcript[:12000] + " [mətnin davamı ixtisar edilib]"
                lines.append(f"{who} ({label}): {transcript}")
                transcribed += 1
            else:
                unavailable_voice += 1
                if reply_context:
                    lines.append(
                        f"{who}: [Səs yazısı var, mətnə çevirmək alınmadı; məzmunu məlum deyil.]"
                    )
        elif reply_context and (item.get("media_url") or item.get("media") or item.get("file_uuid")):
            lines.append(
                f"{who}: [Əlavə fayl var; məzmunu yalnız aşağıda ayrıca təqdim edilibsə məlumdur.]"
            )
    return lines, transcribed, unavailable_voice
