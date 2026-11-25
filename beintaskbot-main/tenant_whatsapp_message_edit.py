"""Apply whatsmeow-confirmed edits to the original CRM message."""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from tenant_ai_dialog_scope import lock_dialog
from tenant_chat_send_policy import fingerprint
from tenant_chat_send_store import authority
from tenant_connect_dialog_equivalence import canonical, members
from tenant_connect_direct_identity import established_canonical_conversation
from tenant_connect_identity import conflicted
from tenant_connect_transport import ConnectPreflightError
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access
from tenant_platform import TenantPlatformError, _connect
from tenant_whatsapp_native_projection import DIRECT, GROUP, PROVIDER_ID, _jid
from tenant_whatsapp_native_transport import message_actions_enabled, submit


# Matches the pinned whatsmeow EditWindow. The provider still decides whether
# to accept a particular edit. Age is always measured from the original send.
# Apply the stricter public WhatsApp edit limit; whatsmeow's own builder only
# creates the protocol stanza and is not evidence that the server will accept it.
EDIT_WINDOW = timedelta(minutes=15)
DELETE_WINDOW = timedelta(hours=48)
OPERATION = re.compile(r'^(edit|delete):([1-9][0-9]{0,18}):([0-9a-f-]{36})$')
VERSION = re.compile(r'^[0-9a-f]{64}$')


def migrate(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_whatsapp_message_edits (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      actor_id BIGINT NOT NULL, request_id UUID NOT NULL, message_id UUID NOT NULL,
      lead_id BIGINT NOT NULL, action TEXT NOT NULL DEFAULT 'edit' CHECK(action IN ('edit','delete')),
      target JSONB NOT NULL, expected_version TEXT NOT NULL, input_hash TEXT NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('pending','accepted','unknown','rejected')),
      error_code TEXT NOT NULL DEFAULT '', updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(tenant_id,actor_id,request_id))""")
    cur.execute("""CREATE INDEX IF NOT EXISTS saas_whatsapp_message_edits_pending
      ON saas_whatsapp_message_edits(tenant_id,message_id,expected_version)
      WHERE state IN ('pending','unknown')""")
    cur.execute("ALTER TABLE saas_whatsapp_message_edits ADD COLUMN IF NOT EXISTS action TEXT NOT NULL DEFAULT 'edit'")
    cur.execute("""DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='whatsapp_message_action_kind') THEN
      ALTER TABLE saas_whatsapp_message_edits ADD CONSTRAINT whatsapp_message_action_kind CHECK(action IN ('edit','delete'));
      END IF; END $$""")


def _version(row):
    return fingerprint({'text': row.get('body') or '', 'edit': (row.get('raw') or {}).get('_crm_text_edit')})


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 8000 or '\x00' in value:
        raise TenantLinearError('edit_text_invalid', 400)
    return value.strip()


def _address(value):
    return isinstance(value, str) and (DIRECT.fullmatch(value) or GROUP.fullmatch(value))


def _provider_id(value):
    return isinstance(value, str) and PROVIDER_ID.fullmatch(value)


def _original(row, actor, channel, conversation, *, action='edit', allow_deleted=False):
    raw = row.get('raw') or {}
    provider = raw.get('externalMessageId')
    chat = raw.get('_crm_provider_conversation_id') or raw.get('conversationId')
    if (row.get('direction') != 'outgoing' or (not allow_deleted and raw.get('message_deleted') is True)
            or (action == 'edit' and row.get('message_type') != 'text') or
            raw.get('fromMe') is not True or raw.get('_crm_native_send_receipt') is not True
            or str(raw.get('verified_employee_id')) != str(actor)
            or raw.get('run_id') or raw.get('ai_authored') or row.get('author_name') == 'AI Agent'
            or raw.get('channelId') != channel or raw.get('conversationIdentityConflict') is True
            or row.get('channel') != 'connect:'+channel or not _provider_id(provider)
            or not _address(chat) or chat != conversation):
        return None
    return chat, provider


def _sent_at(row):
    return min((value for value in (row.get('created_at'), row.get('happened_at')) if value is not None), default=None)


def attach_capabilities(rows, dialog, actor):
    """Project a version only for a writable, exact whatsmeow message route."""
    if (not message_actions_enabled() or not dialog.get('wecall') or dialog.get('can_send') is not True
            or dialog.get('identity_conflict') or dialog.get('owner_overview_read_only')
            or dialog.get('search_overview_read_only')):
        return
    channel, conversation, lead = str(dialog['channel_id']), dialog['conversation_id'], dialog['lead_id']
    now = datetime.now(timezone.utc)
    for row in rows:
        if (str(row.get('source_channel_id')) == channel and row.get('source_conversation_id') == conversation
                and row.get('source_lead_id') == lead):
            sent = _sent_at(row)
            edit_proof = _original(row, actor, channel, conversation, action='edit')
            if edit_proof and sent and sent <= now < sent + EDIT_WINDOW:
                row['wa_edit_version'] = _version(row)
                row['wa_edit_until'] = (sent + EDIT_WINDOW).isoformat()
            delete_proof = _original(row, actor, channel, conversation, action='delete')
            if delete_proof and sent and sent <= now < sent + DELETE_WINDOW:
                row['wa_delete_version'] = _delete_version(row)
                row['wa_delete_until'] = (sent + DELETE_WINDOW).isoformat()


def _authorize(cur, session, lead, message_id, *, action='edit', allow_deleted=False):
    if not message_actions_enabled():
        raise TenantLinearError('edit_source_unavailable', 409)
    access(cur, session, action='update')
    profile, connection, integration = authority(cur, session, lead)
    route = integration.get('route') or {}
    if connection[0] != 'connect' or not route.get('native'):
        raise TenantLinearError('edit_source_unavailable', 409)
    tenant, actor = profile['tenant_id'], int(profile['telegram_id'])
    channel, conversation = connection[1:]
    from tenant_dialog_access import require_source
    from tenant_policy import TenantPolicy
    require_source(cur, tenant, TenantPolicy(profile), 'connect', channel, 'update')
    scope = route['native']['scope']
    cur.execute("""SELECT 1 FROM saas_whatsapp_capture_generations
      WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND generation=%s AND projection_enabled FOR SHARE""",
        (tenant, channel, scope['generation']))
    if not cur.fetchone():
        raise TenantLinearError('edit_source_unavailable', 409)
    if not GROUP.fullmatch(conversation) and conflicted(cur, tenant, channel, conversation):
        raise TenantLinearError('edit_message_unavailable', 404)
    cur.execute("""SELECT id,external_id,channel,direction,message_type,created_at,happened_at,body,raw,author_name
      FROM saas_crm_messages WHERE tenant_id=%s::uuid AND id=%s::uuid AND kommo_lead_id=%s AND channel=%s
      FOR UPDATE""", (tenant, message_id, lead, 'connect:'+channel))
    row = cur.fetchone()
    proof = _original(row, actor, channel, conversation, action=action, allow_deleted=allow_deleted) if row else None
    if not proof:
        raise TenantLinearError('edit_message_unavailable', 404)
    target = {'scope': scope, 'gateway': route['native']['gateway_id'], 'chat': proof[0], 'provider_id': proof[1]}
    return row, target, {**route, 'native_actor_id': actor, '_native_cursor': cur}


def _edit_at(cur, key, lead, row, expected_version, text):
    if expected_version != _version(row):
        raise TenantLinearError('edit_version_conflict', 409)
    if row['body'] == text:
        raise TenantLinearError('edit_no_change', 400)
    tenant, actor = key[:2]
    cur.execute("""SELECT min(created_at) AS sent
      FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid AND actor_id=%s
        AND lead_id=%s AND message_id=%s AND state='accepted'""",
        (tenant, actor, lead, row['external_id'].removeprefix('connect:')))
    receipt_time = cur.fetchone()['sent']
    sent = min(_sent_at(row), receipt_time) if receipt_time else _sent_at(row)
    if not sent or not sent <= datetime.now(timezone.utc) < sent + EDIT_WINDOW:
        raise TenantLinearError('edit_expired', 409)
    return int(sent.timestamp() * 1000)


def _delete_version(row):
    raw = row.get('raw') or {}
    return fingerprint({'id': str(row.get('id') or ''), 'provider_id': raw.get('externalMessageId'),
        'body': row.get('body') or '', 'type': row.get('message_type') or '',
        'edit': raw.get('_crm_text_edit'), 'deleted': raw.get('message_deleted') is True})


def _delete_at(cur, key, lead, row, expected_version):
    if expected_version != _delete_version(row):
        raise TenantLinearError('delete_version_conflict', 409)
    if (row.get('raw') or {}).get('message_deleted') is True:
        raise TenantLinearError('delete_message_unavailable', 404)
    tenant, actor = key[:2]
    cur.execute("""SELECT min(created_at) AS sent FROM saas_chat_send_receipts
      WHERE tenant_id=%s::uuid AND actor_id=%s AND lead_id=%s AND message_id=%s AND state='accepted'""",
        (tenant, actor, lead, row['external_id'].removeprefix('connect:')))
    receipt_time = cur.fetchone()['sent']
    sent = min(_sent_at(row), receipt_time) if receipt_time else _sent_at(row)
    if not sent or not sent <= datetime.now(timezone.utc) < sent + DELETE_WINDOW:
        raise TenantLinearError('delete_expired', 409)
    return int(sent.timestamp() * 1000)


def _key(session, request_id):
    return (str(uuid.UUID(str(session['tenant_id']))), int(session['telegram_id']), str(uuid.UUID(request_id)))


def _operation(cur, key, *, action='edit', lock=False):
    cur.execute("""SELECT message_id,lead_id,target,expected_version,input_hash,state,error_code
      FROM saas_whatsapp_message_edits WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid AND action=%s"""
      + (' FOR UPDATE' if lock else ''), (*key, action))
    return cur.fetchone()


def _result(receipt):
    return {'state': receipt['state'], 'code': receipt['error_code']}


def _update(cur, key, state, code='', *, action='edit'):
    cur.execute("""UPDATE saas_whatsapp_message_edits SET state=%s,error_code=%s,updated_at=clock_timestamp()
      WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid AND action=%s AND state<>'accepted'""",
        (state, code, *key, action))


def save(session, lead, message_id, request_id, version, text):
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError()
    text = _text(text)
    key = _key(session, request_id)
    digest = fingerprint({'version': version, 'text': text})
    with _connect() as conn, conn.cursor() as cur:
        row, target, _ = _authorize(cur, session, lead, message_id, action='edit')
        saved = _operation(cur, key, action='edit', lock=True)
        if saved:
            if (str(saved['message_id']) != message_id or saved['lead_id'] != lead
                    or saved['target'] != target or saved['input_hash'] != digest):
                raise TenantLinearError('edit_request_conflict', 409)
            return _result(saved)
        _edit_at(cur, key, lead, row, version, text)
        cur.execute("""SELECT 1 FROM saas_whatsapp_message_edits WHERE tenant_id=%s::uuid AND message_id=%s::uuid
          AND action='edit' AND expected_version=%s AND state IN ('pending','unknown') LIMIT 1""", (key[0], message_id, version))
        if cur.fetchone():
            raise TenantLinearError('edit_pending', 409)
        cur.execute("""INSERT INTO saas_whatsapp_message_edits(tenant_id,actor_id,request_id,message_id,lead_id,action,
          target,expected_version,input_hash,state) VALUES(%s::uuid,%s,%s::uuid,%s::uuid,%s,'edit',%s::jsonb,%s,%s,'pending')""",
          (*key, message_id, lead, json.dumps(target), version, digest))

    # Durable reservation precedes provider submission. A replay returns its
    # receipt, including when the HTTP connection died during the first call.
    state, code = 'pending', ''
    try:
        with _connect() as conn, conn.cursor() as cur:
            row, current, route = _authorize(cur, session, lead, message_id, action='edit')
            if current != target:
                raise TenantLinearError('edit_source_unavailable', 409)
            at = _edit_at(cur, key, lead, row, version, text)
            submit(route, key[2], kind='edit', text=text, edit={'id': target['provider_id'], 'original_ms': at})
    except ConnectPreflightError as error:
        state, code = 'rejected', {
            'native_edit_expired': 'edit_expired',
            'native_action_original_unavailable': 'edit_message_unavailable',
            'native_edit_rejected': 'edit_provider_rejected',
        }.get(error.code, 'edit_message_unavailable')
    except TenantLinearError as error:
        state, code = 'rejected', str(error) if str(error) in {'edit_version_conflict','edit_expired',
            'edit_message_unavailable','edit_source_unavailable','edit_no_change'} else 'edit_source_unavailable'
    except TenantPlatformError:
        state, code = 'unknown', 'edit_unconfirmed'
    with _connect() as conn, conn.cursor() as cur:
        if state != 'pending':
            _update(cur, key, state, code)
        return _result(_operation(cur, key, action='edit'))


def delete(session, lead, message_id, request_id, version):
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError()
    key = _key(session, request_id)
    digest = fingerprint({'version': version, 'action': 'delete'})
    with _connect() as conn, conn.cursor() as cur:
        row, target, _ = _authorize(cur, session, lead, message_id, action='delete', allow_deleted=True)
        saved = _operation(cur, key, action='delete', lock=True)
        if saved:
            if (str(saved['message_id']) != message_id or saved['lead_id'] != lead
                    or saved['target'] != target or saved['input_hash'] != digest):
                raise TenantLinearError('delete_request_conflict', 409)
            return _result(saved)
        _delete_at(cur, key, lead, row, version)
        cur.execute("""SELECT 1 FROM saas_whatsapp_message_edits WHERE tenant_id=%s::uuid AND message_id=%s::uuid
          AND action='delete' AND state IN ('pending','unknown') LIMIT 1""", (key[0], message_id))
        if cur.fetchone():
            raise TenantLinearError('delete_pending', 409)
        cur.execute("""INSERT INTO saas_whatsapp_message_edits(tenant_id,actor_id,request_id,message_id,lead_id,action,
          target,expected_version,input_hash,state) VALUES(%s::uuid,%s,%s::uuid,%s::uuid,%s,'delete',%s::jsonb,%s,%s,'pending')""",
          (*key, message_id, lead, json.dumps(target), version, digest))

    state, code = 'pending', ''
    try:
        with _connect() as conn, conn.cursor() as cur:
            row, current, route = _authorize(cur, session, lead, message_id, action='delete')
            if current != target:
                raise TenantLinearError('delete_source_unavailable', 409)
            at = _delete_at(cur, key, lead, row, version)
            submit(route, key[2], kind='delete', delete={'id': target['provider_id'], 'original_ms': at})
    except ConnectPreflightError as error:
        state, code = 'rejected', {
            'native_delete_expired': 'delete_expired',
            'native_action_original_unavailable': 'delete_message_unavailable',
        }.get(error.code, 'delete_message_unavailable')
    except TenantLinearError as error:
        state, code = 'rejected', str(error) if str(error) in {'delete_version_conflict','delete_expired',
            'delete_message_unavailable','delete_source_unavailable'} else 'delete_source_unavailable'
    except TenantPlatformError:
        state, code = 'unknown', 'delete_unconfirmed'
    with _connect() as conn, conn.cursor() as cur:
        if state != 'pending':
            _update(cur, key, state, code, action='delete')
        return _result(_operation(cur, key, action='delete'))


def status(session, lead, message_id, request_id, action='edit'):
    if action not in {'edit','delete'}:
        raise ValueError()
    key = _key(session, request_id)
    with _connect() as conn, conn.cursor() as cur:
        _, target, _ = _authorize(cur, session, lead, message_id, action=action, allow_deleted=True)
        row = _operation(cur, key, action=action)
        if not row or str(row['message_id']) != message_id or row['lead_id'] != lead or row['target'] != target:
            raise TenantLinearError(action+'_request_unavailable', 404)
        return _result(row)


def _current_route(cur, tenant, channel, chat):
    return canonical(cur, tenant, channel, established_canonical_conversation(cur, tenant, channel, chat) or chat)


def observe(cur, scope, payload):
    """Project a verified text correction in place. It cannot create a message or run."""
    tenant, channel = scope['tenant'], scope['source']
    edit = payload.get('edit')
    from_me = payload.get('from_me')
    chat, update_id, stamp = payload.get('chat'), payload.get('provider_id'), payload.get('timestamp')
    if (not isinstance(edit, dict) or not set(edit) <= {'id','operation'} or not _provider_id(edit.get('id'))
            or type(from_me) is not bool or not _address(chat) or not _provider_id(update_id)
            or type(stamp) is not int or not 0 < stamp <= int(datetime.now(timezone.utc).timestamp()*1000)+300_000):
        return False
    text = payload.get('text')
    # A received WhatsApp text uses the same limit as an incoming original.
    if not isinstance(text, str) or not text.strip() or '\x00' in text:
        return False
    if (from_me and len(text.strip()) > 8000) or (not from_me and len(text) > 65536):
        return False
    authors = set()
    if not from_me:
        if payload.get('app_dispatch', False) is not False:
            return False
        try:
            sender = _jid(payload.get('sender'), participant=True)
            alternate = _jid(payload.get('sender_alt'), optional=True, participant=True)
        except TenantLinearError:
            return False
        authors.add(sender)
        if alternate and alternate.split('@')[1] != sender.split('@')[1]:
            authors.add(alternate)
        if not GROUP.fullmatch(chat) and chat not in authors:
            return False
    operation = edit.get('operation')
    own_edit = payload.get('app_dispatch') is True
    match = OPERATION.fullmatch(operation) if isinstance(operation, str) else None
    if own_edit != bool(match) or not own_edit and 'operation' in edit or match and match[1] != 'edit':
        return False

    from tenant_dialog_rollout import guard
    guard(cur, tenant)
    conversation = _current_route(cur, tenant, channel, chat)
    lock_dialog(cur, tenant, 'connect', channel, conversation)
    if _current_route(cur, tenant, channel, chat) != conversation:
        return False
    if not GROUP.fullmatch(conversation) and conflicted(cur, tenant, channel, conversation):
        return False
    leads = list(members(cur, tenant, channel, conversation).values())
    if not leads:
        return False
    original_id = edit['id']
    cur.execute("""SELECT id,kommo_lead_id,created_at,happened_at,body,raw FROM saas_crm_messages
      WHERE tenant_id=%s::uuid AND channel=%s AND kommo_lead_id=ANY(%s::bigint[])
        AND direction=%s AND message_type='text'
        AND raw->>'externalMessageId'=%s AND raw->>'channelId'=%s AND raw->'fromMe'=%s::jsonb
        AND COALESCE(raw->>'conversationIdentityConflict','false')<>'true'
      LIMIT 2 FOR UPDATE""", (tenant, 'connect:'+channel, leads, 'outgoing' if from_me else 'incoming',
                              original_id, channel, json.dumps(from_me)))
    rows = cur.fetchall()
    if len(rows) != 1:
        return False
    row = rows[0]
    raw = row['raw']
    if not from_me and GROUP.fullmatch(chat) and authors.isdisjoint((raw.get('participantJid'), raw.get('participantAlt'))):
        return False
    if raw.get('message_deleted') is True:
        return False
    original_chat = raw.get('_crm_provider_conversation_id') or raw.get('conversationId')
    if not from_me and GROUP.fullmatch(chat) and chat != original_chat:
        return False
    if not _address(original_chat) or _current_route(cur, tenant, channel, original_chat) != conversation:
        return False
    # Use small skew tolerance for provider time versus local ingestion time.
    sent = _sent_at(row)
    if sent and stamp < int(sent.timestamp()*1000)-300_000:
        return False
    key = None
    if own_edit:
        try:
            key = (tenant, int(match[2]), str(uuid.UUID(match[3])))
            if key[1] >= 2**63:
                return False
        except ValueError:
            return False
        saved = _operation(cur, key, action='edit', lock=True)
        expected = {'scope': scope, 'chat': chat, 'provider_id': original_id}
        if (not saved or saved['state'] == 'rejected' or saved['message_id'] != row['id']
                or saved['lead_id'] != row['kommo_lead_id']
                or any(saved['target'].get(field) != value for field, value in expected.items())
                or saved['input_hash'] != fingerprint({'version': saved['expected_version'], 'text': text})):
            return False
    previous = raw.get('_crm_text_edit') or {}
    if stamp > previous.get('timestamp_ms', 0):
        edit_info = {'timestamp_ms': stamp, 'provider_id': update_id}
        cur.execute("""UPDATE saas_crm_messages SET body=%s,raw=raw||%s::jsonb
          WHERE tenant_id=%s::uuid AND id=%s::uuid""",
            (text, json.dumps({'_crm_text_edit': edit_info, **({'text': text} if 'text' in raw else {})}), tenant, row['id']))
    if key:
        superseded = stamp <= previous.get('timestamp_ms', 0) and previous.get('provider_id') != update_id
        _update(cur, key, 'accepted', 'edit_superseded' if superseded else '', action='edit')
    return True


def observe_revoke(cur, scope, payload):
    """Redact the exact revoked CRM row in place; never create a message/turn."""
    tenant, channel = scope['tenant'], scope['source']
    revoke = payload.get('revoke')
    chat, stamp = payload.get('chat'), payload.get('timestamp')
    if (not isinstance(revoke, dict) or not set(revoke) <= {'id','chat','from_me','participant','operation'}
            or not _provider_id(revoke.get('id')) or not _address(revoke.get('chat'))
            or type(revoke.get('from_me')) is not bool or not _address(chat)
            or revoke.get('chat') != chat or type(stamp) is not int
            or not 0 < stamp <= int(datetime.now(timezone.utc).timestamp()*1000)+300_000):
        return False
    target_chat = revoke['chat']
    is_group = GROUP.fullmatch(target_chat) is not None
    participant = revoke.get('participant') or ''
    if is_group and not revoke['from_me'] and not _address(participant):
        return False
    if not is_group and participant:
        return False
    operation = revoke.get('operation')
    app_dispatch = payload.get('app_dispatch') is True
    match = OPERATION.fullmatch(operation) if isinstance(operation, str) else None
    if app_dispatch != bool(match) or match and match[1] != 'delete' or not app_dispatch and 'operation' in revoke:
        return False

    from tenant_dialog_rollout import guard
    guard(cur, tenant)
    conversation = _current_route(cur, tenant, channel, chat)
    lock_dialog(cur, tenant, 'connect', channel, conversation)
    if _current_route(cur, tenant, channel, chat) != conversation:
        return False
    if not GROUP.fullmatch(conversation) and conflicted(cur, tenant, channel, conversation):
        return False
    leads = list(members(cur, tenant, channel, conversation).values())
    if not leads:
        return False
    target_from_me = revoke['from_me']
    direction = 'outgoing' if target_from_me else 'incoming'
    sql = """SELECT id,kommo_lead_id,created_at,happened_at,body,raw,external_id,message_type,media_url
      FROM saas_crm_messages WHERE tenant_id=%s::uuid AND channel=%s AND kommo_lead_id=ANY(%s::bigint[])
        AND direction=%s AND raw->>'externalMessageId'=%s AND raw->>'channelId'=%s
        AND COALESCE(raw->>'conversationIdentityConflict','false')<>'true'"""
    params = [tenant, 'connect:'+channel, leads, direction, revoke['id'], channel]
    if is_group and not target_from_me:
        sql += " AND raw->>'participantJid'=%s"
        params.append(participant)
    sql += ' LIMIT 2 FOR UPDATE'
    cur.execute(sql, tuple(params))
    rows = cur.fetchall()
    if len(rows) != 1:
        return False
    row = rows[0]
    raw = row['raw'] or {}
    original_chat = raw.get('_crm_provider_conversation_id') or raw.get('conversationId')
    if (not _address(original_chat) or _current_route(cur, tenant, channel, original_chat) != conversation
            or raw.get('fromMe') is not target_from_me):
        return False
    sent = _sent_at(row)
    if sent and stamp < int(sent.timestamp()*1000)-300_000:
        return False

    key = None
    if app_dispatch:
        try:
            key = (tenant, int(match[2]), str(uuid.UUID(match[3])))
            if key[1] >= 2**63:
                return False
        except (ValueError, TypeError):
            return False
        saved = _operation(cur, key, action='delete', lock=True)
        expected = {'scope': scope, 'chat': target_chat, 'provider_id': revoke['id']}
        if (not saved or saved['state'] == 'rejected' or str(saved['message_id']) != str(row['id'])
                or saved['lead_id'] != row['kommo_lead_id']
                or any(saved['target'].get(field) != value for field, value in expected.items())
                or saved['input_hash'] != fingerprint({'version': saved['expected_version'], 'action': 'delete'})
                or target_from_me is not True):
            return False
        if sent and stamp < int(sent.timestamp()*1000)-300_000:
            return False

    previous = raw.get('message_deleted') is True
    if not previous:
        redacted = [
            'text','caption','body','mediaUrl','media_url','url','mimeType','mimetype','mime_type',
            'fileName','file_name','fileSize','file_size','mediaSize','mediaKey','voice_transcription',
            'nativeEventId','media_thumbnail','thumbnail','transcription','analysis_result',
        ]
        cur.execute("""UPDATE saas_crm_messages SET body='',media_url='',
          raw=(COALESCE(raw,'{}'::jsonb)-%s::text[])||jsonb_build_object(
            'message_deleted',true,'message_deleted_at',to_jsonb(to_timestamp(%s::double precision/1000)),
            'message_delete_source','whatsapp')
          WHERE tenant_id=%s::uuid AND id=%s::uuid""",
          (redacted, stamp, tenant, row['id']))
    if key:
        _update(cur, key, 'accepted', '', action='delete')
    return True
