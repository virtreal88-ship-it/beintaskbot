"""Send legacy Kommo Talk messages through the configured transport."""

from __future__ import annotations

import logging

from legacy_runtime_config import KOMMO_BASE_URL


logger = logging.getLogger(__name__)
_kommo_quote_bodies = lambda payload, reply_id, reply_text="": [payload]
_post_kommo_json = lambda _url, _body, _headers=None: None
_kommo_error_detail = lambda _response: ""


def configure_chat_sender(*, quote_bodies, post_json, error_detail, app_logger=None) -> None:
    global _kommo_quote_bodies, _post_kommo_json, _kommo_error_detail, logger
    _kommo_quote_bodies = quote_bodies
    _post_kommo_json = post_json
    _kommo_error_detail = error_detail
    if app_logger is not None:
        logger = app_logger


def _send_kommo_talk_message(
    talk_id: int,
    text: str,
    attachment: dict | None = None,
    reply_to: str = "",
    chat_id: str = "",
    reply_text: str = "",
    plain_fallback: bool = True,
) -> tuple[bool, str, int]:
    # Official send_message is text/attachment only and silently drops reply_to.
    # Native quotes go through amojo / ajax / talks messages only.
    att_clean = dict(attachment) if isinstance(attachment, dict) else None
    is_voice = bool(att_clean and att_clean.pop("is_voice", False))
    payload: dict = {}
    if text:
        payload["text"] = text
    elif att_clean:
        if not is_voice:
            payload["text"] = "Fayl"
    if att_clean:
        payload["attachment"] = att_clean
    if not payload:
        return False, "Mesaj boş ola bilməz", 0
    reply_id = str(reply_to or "").strip()
    talk_send = f"{KOMMO_BASE_URL}/api/v4/talks/{int(talk_id)}/send_message"
    talk_messages = f"{KOMMO_BASE_URL}/api/v4/talks/{int(talk_id)}/messages"
    ajax_talk_send = f"{KOMMO_BASE_URL}/ajax/v4/talks/{int(talk_id)}/send_message"
    ajax_talk_send_v2 = f"{KOMMO_BASE_URL}/ajax/v2/talks/{int(talk_id)}/send_message"
    attempts: list[tuple[str, dict, dict]] = []
    if reply_id:
        quote_bodies = _kommo_quote_bodies(payload, reply_id, reply_text)
        primary = quote_bodies[0]
        msgid_only = {**payload, "reply_to": {"message": {"msgid": reply_id}}}
        id_only = {**payload, "reply_to": {"message": {"id": reply_id}}}
        ajax = {"X-Requested-With": "XMLHttpRequest"}
        if chat_id:
            amojo_body = {"text": payload.get("text", text or ""), "reply_to": {"message": {"id": reply_id, "msgid": reply_id}}}
            if att_clean:
                amojo_body["attachment"] = att_clean
            if reply_text:
                amojo_body["reply_to"]["message"]["type"] = "text"
                amojo_body["reply_to"]["message"]["text"] = str(reply_text)[:200]
            if is_voice and att_clean:
                voice_amojo = dict(amojo_body)
                voice_amojo["attachment"] = {**att_clean, "type": "voice"}
                attempts.append((f"https://amojo.kommo.com/v2/chats/{chat_id}", voice_amojo, {}))
            attempts.append((f"https://amojo.kommo.com/v2/chats/{chat_id}", amojo_body, {}))
            if is_voice and att_clean:
                voice_pri = {**primary, "attachment": {**att_clean, "type": "voice"}}
                attempts.append((f"{KOMMO_BASE_URL}/ajax/v4/chats/{chat_id}/send", voice_pri, ajax))
                attempts.append((f"{KOMMO_BASE_URL}/ajax/v2/chats/{chat_id}/send", voice_pri, ajax))
            attempts.append((f"{KOMMO_BASE_URL}/ajax/v4/chats/{chat_id}/send", primary, ajax))
            attempts.append((f"{KOMMO_BASE_URL}/ajax/v2/chats/{chat_id}/send", primary, ajax))
        if is_voice and att_clean:
            voice_pri = {**primary, "attachment": {**att_clean, "type": "voice"}}
            attempts.append((talk_messages, voice_pri, {}))
            attempts.append((f"{KOMMO_BASE_URL}/ajax/v4/talks/{int(talk_id)}/messages", voice_pri, ajax))
            attempts.append((ajax_talk_send, voice_pri, ajax))
            attempts.append((ajax_talk_send_v2, voice_pri, ajax))
        attempts.append((talk_messages, primary, {}))
        attempts.append((talk_messages, msgid_only, {}))
        attempts.append((talk_messages, id_only, {}))
        attempts.append((f"{KOMMO_BASE_URL}/ajax/v4/talks/{int(talk_id)}/messages", primary, ajax))
        attempts.append((ajax_talk_send, primary, ajax))
        attempts.append((ajax_talk_send_v2, primary, ajax))
        if plain_fallback:
            if is_voice and att_clean and not text:
                voice_att = {**att_clean, "type": "voice"}
                audio_att = {**att_clean, "type": "audio"}
                file_att = {**att_clean, "type": "file"}
                attempts.append((talk_send, {"attachment": voice_att}, {}))
                attempts.append((talk_send, {"text": "", "attachment": voice_att}, {}))
                attempts.append((talk_send, {"attachment": audio_att}, {}))
                attempts.append((talk_send, {"text": "", "attachment": audio_att}, {}))
                attempts.append((talk_send, {"attachment": file_att}, {}))
                attempts.append((talk_send, {"text": "", "attachment": file_att}, {}))
            else:
                attempts.append((talk_send, payload, {}))
    if not reply_id:
        if is_voice and att_clean and not text:
            voice_att = {**att_clean, "type": "voice"}
            audio_att = {**att_clean, "type": "audio"}
            file_att = {**att_clean, "type": "file"}
            ajax = {"X-Requested-With": "XMLHttpRequest"}
            attempts.append((talk_send, {"attachment": voice_att}, {}))
            attempts.append((talk_send, {"text": "", "attachment": voice_att}, {}))
            attempts.append((ajax_talk_send, {"attachment": voice_att}, ajax))
            attempts.append((ajax_talk_send_v2, {"attachment": voice_att}, ajax))
            attempts.append((talk_send, {"attachment": audio_att}, {}))
            attempts.append((talk_send, {"text": "", "attachment": audio_att}, {}))
            attempts.append((talk_send, {"attachment": file_att}, {}))
            attempts.append((talk_send, {"text": "", "attachment": file_att}, {}))
        else:
            attempts.append((talk_send, payload, {}))
    last_detail = ""
    last_body = ""
    last_status = 0
    for url, body, extra in attempts:
        try:
            resp = _post_kommo_json(url, body, extra)
        except Exception as exc:
            logger.warning("Talk send failed: %s", exc)
            last_detail = "Kommo çata göndərmək alınmadı."
            last_status = 0
            continue
        last_status = resp.status_code
        if 200 <= resp.status_code < 300:
            return True, "", resp.status_code
        last_detail = _kommo_error_detail(resp)
        last_body = (resp.text or "")[:400]
        logger.warning("Talk send status %s %s: %s | %s", resp.status_code, url, last_detail, last_body)
    if last_status == 403:
        folded = f"{last_detail} {last_body}".lower()
        if "external chat" in folded:
            return False, "Kommo tokenində çat göndərmə hüququ yoxdur (Sending to external chats).", last_status
        return False, "Kommo müvəqqəti icazə vermir. Bir azdan yenidən yoxlayın.", last_status
    if last_status == 429:
        return False, "Kommo sorğu limitini aşıb. Bir azdan yenidən yoxlayın.", last_status
    if last_status == 422:
        return False, "Çat bağlıdır. Kommo-da söhbəti açın.", last_status
    if last_status == 402:
        return False, "Kommo Chat API limiti bitib.", last_status
    return False, last_detail or "Mesaj göndərilmədi.", last_status



def _send_kommo_talk_text(talk_id: int, text: str) -> tuple[bool, str, int]:
    return _send_kommo_talk_message(talk_id, text)
