"""Private inbox -> existing CRM event contract, without transport credentials.

These functions do not grant source access, activate a source, ingest an event or
send anything. The rollout consumer must hold the source/generation transaction
guard before using the result. Unsupported control events remain in the encrypted
inbox for explicit processing; they must not become fresh AI/customer messages.
"""
import hashlib
import hmac
import json
import re
from datetime import datetime, timezone

from tenant_linear_policy import TenantLinearError
from tenant_platform import _fernet
from tenant_whatsapp_shared_content import KINDS as SHARE_KINDS, facts as shared_facts

DIRECT = re.compile(r'^[1-9][0-9]{5,22}@(s\.whatsapp\.net|lid)$')
DEVICE = re.compile(r'^([1-9][0-9]{5,22})(?:\.([0-9]{1,3}))?:([0-9]{1,5})@(s\.whatsapp\.net|lid)$')
GROUP = re.compile(r'^[0-9]{5,30}(?:-[0-9]{5,20})?@g\.us$')
PROVIDER_ID = re.compile(r'^[A-Za-z0-9_-]{5,200}$')
FIELDS = {'kind','chat','sender','sender_alt','recipient_alt','provider_id','from_me','timestamp',
          'text','status','proto','media_kind','media_mime','media_size','file_name','message_type',
          'push_name','quote_id','quote_chat','quote_sender','variant','app_dispatch','edit','revoke','contacts','location'}
MEDIA_KINDS = frozenset({'image','audio','video','document','sticker'})
MESSAGE_KINDS = MEDIA_KINDS | SHARE_KINDS | {'text'}


def _error(code='native_projection_invalid'):
    return TenantLinearError(code, 409)


def open_stored(row, scope):
    """Detect ciphertext substitution across tenants/sources/generations/events.

    Return only validated protocol evidence, not a public API value. Errors are
    fixed codes; neither decryption exceptions nor payloads reach a logger.
    """
    try:
        if ((str(row['tenant_id']), str(row['channel_id']), row['generation']) !=
                (scope['tenant'], scope['source'], scope['generation'])):
            raise ValueError()
        sealed = json.loads(_fernet().decrypt(bytes(row['encrypted_payload'])))
        if (set(sealed) != {'scope','event_id','payload'} or sealed['scope'] != scope
                or sealed['event_id'] != row['event_id'] or not isinstance(sealed['payload'], dict)):
            raise ValueError()
        payload = sealed['payload']
        raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(',',':')).encode()
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), row['payload_sha256']):
            raise ValueError()
    except Exception:
        raise _error('native_inbox_evidence_invalid') from None
    return payload


def _jid(value, *, group=False, optional=False, participant=False):
    if optional and not value:
        return ''
    # Provider-authenticated participant device addresses identify the same
    # account (whatsmeow ToNonAD semantics). Never rewrite conversation IDs,
    # persisted evidence, source bindings or arbitrary browser-supplied values.
    if participant and isinstance(value, str):
        device = DEVICE.fullmatch(value)
        if device and int(device[2] or 0) <= 255 and int(device[3]) <= 65535:
            value = device[1] + '@' + device[4]
    if not isinstance(value, str) or not (DIRECT.fullmatch(value) or group and GROUP.fullmatch(value)):
        raise _error()
    return value


def _text(value, limit):
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        raise _error()
    return value


def message_event(scope, event_id, payload, message_id):
    """Explicit allowlist into the existing Connect-compatible CRM contract.

    `message_id` must be resolved inside the guarded CRM transaction, including
    an exact existing provider-ID mapping. No name/phone/text similarity here.
    All legacy table/provider names remain stable for the existing dialogs/AI.
    """
    if (not isinstance(payload, dict) or set(payload) - FIELDS or payload.get('kind') != 'message'
            or payload.get('variant') or not isinstance(payload.get('message_type'), str)
            or payload['message_type'] not in MESSAGE_KINDS):
        raise _error('native_event_not_projectable')
    # Status publications and broadcast/newsletter feeds are retained provider
    # evidence, never CRM conversations or AI turns. A valid non-chat event must
    # not poison delivery of later customer messages in the same durable inbox.
    chat_value = payload.get('chat')
    if isinstance(chat_value, str) and (chat_value == 'status@broadcast' or
            re.fullmatch(r'[1-9][0-9]{4,29}@(broadcast|newsletter)', chat_value)):
        raise _error('native_event_not_projectable')
    if (not isinstance(message_id, str) or not PROVIDER_ID.fullmatch(message_id)
            or not isinstance(event_id, str) or not re.fullmatch(r'[0-9a-f]{64}', event_id)
            or type(payload.get('from_me')) is not bool
            or type(payload.get('timestamp')) is not int):
        raise _error()
    chat = _jid(payload.get('chat'), group=True)
    sender = _jid(payload.get('sender'), participant=True)
    app_dispatch=payload.get('app_dispatch',False)
    if type(app_dispatch) is not bool or (app_dispatch and (not payload['from_me'] or sender!=scope['account'])):
        raise _error()
    sender_alt = _jid(payload.get('sender_alt'), optional=True, participant=True)
    recipient_alt = _jid(payload.get('recipient_alt'), optional=True, participant=True)
    provider = payload.get('provider_id')
    if not isinstance(provider, str) or not PROVIDER_ID.fullmatch(provider):
        raise _error()
    try:
        occurred = datetime.fromtimestamp(payload['timestamp']/1000, timezone.utc)
        if not 2009 <= occurred.year <= 2100:
            raise ValueError()
    except (ValueError, OverflowError, OSError):
        raise _error() from None
    kind = payload['message_type']
    text = _text(payload.get('text', ''), 65536)
    data = {'id':message_id,'channelId':scope['source'],'conversationId':chat,
            'externalMessageId':provider,'fromMe':payload['from_me'],'type':kind,'text':text,
            'participantJid':sender,'participantAlt':sender_alt,'source':'unknown',
            'authorshipUncertain':payload['from_me'] and not app_dispatch, 'isGroup':bool(GROUP.fullmatch(chat)),
            'transport':'beintaskbot_whatsapp','nativeEventId':event_id}
    if not payload['from_me']:
        data['pushName'] = _text(payload.get('push_name', ''), 256)
    if not data['isGroup']:
        # Only the provider pair of this exact remote party establishes PN/LID.
        # Outbound sender/its alternate is OUR identity, never the customer.
        alternate = recipient_alt if payload['from_me'] else sender_alt
        pair = {chat, alternate} - {''}
        pn = [x for x in pair if x.endswith('@s.whatsapp.net')]
        lid = [x for x in pair if x.endswith('@lid')]
        if len(pn) == len(lid) == 1 and (payload['from_me'] or sender == chat):
            data['directIdentity'] = {'jid':pn[0], 'lid':lid[0]}
    quote = payload.get('quote_id', '')
    if quote:
        quote_chat = payload.get('quote_chat', '')
        if (not isinstance(quote, str) or not PROVIDER_ID.fullmatch(quote)
                or quote_chat and quote_chat != chat):
            raise _error('native_quote_scope_unconfirmed')
        data['quotedExternalMessageId'] = quote
    if kind in SHARE_KINDS:
        data.update(shared_facts(kind, payload))
        data['text'] = ''
    if kind in MEDIA_KINDS:
        if payload.get('media_kind') != kind or type(payload.get('media_size', 0)) is not int or not 0 <= payload.get('media_size', 0) <= 2**63-1:
            raise _error()
        # No provider URL, media key, embedded proto, arbitrary fields or public
        # download link. Media access will resolve the private retained event.
        data['mimeType'] = _text(payload.get('media_mime', ''), 200)
        data['fileName'] = _text(payload.get('file_name', ''), 512)
        data['fileSize'] = payload.get('media_size', 0)
    return {'id':'native:'+event_id,'type':'message.outbound.observed' if payload['from_me'] else 'message.inbound.received',
            'occurred_at':occurred.isoformat(),'data':data}
