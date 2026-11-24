"""WeCall reaction commands and reaction state for the existing message history."""
import json
import os
import unicodedata
import uuid

from tenant_linear_policy import TenantLinearError
from tenant_platform import TenantPlatformError, _connect
from tenant_whatsapp_native_projection import DIRECT, GROUP, PROVIDER_ID, _jid

EMOJI = frozenset(('👍', '❤️', '😂', '😮', '😢', '🙏'))
MESSAGE_TYPES = {'text', 'image', 'audio', 'voice', 'voice_message', 'ptt', 'video', 'document', 'sticker'}


def migrate(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_whatsapp_reaction_operations (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE, actor_id BIGINT NOT NULL, request_id UUID NOT NULL,
      lead_id BIGINT NOT NULL, message_id UUID NOT NULL, target JSONB NOT NULL,
      emoji TEXT NOT NULL, provider_id TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,actor_id,request_id))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_whatsapp_reactions (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE, channel_id UUID NOT NULL, account TEXT NOT NULL, chat TEXT NOT NULL,
      target_id TEXT NOT NULL, target_from_me BOOLEAN NOT NULL, participant TEXT NOT NULL,
      emoji TEXT NOT NULL, event_timestamp BIGINT NOT NULL, provider_id TEXT NOT NULL,
      PRIMARY KEY(tenant_id,channel_id,account,chat,target_id,target_from_me,participant))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_whatsapp_peer_reactions (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      channel_id UUID NOT NULL, account TEXT NOT NULL, chat TEXT NOT NULL,
      target_id TEXT NOT NULL, target_from_me BOOLEAN NOT NULL, participant TEXT NOT NULL,
      reactor TEXT NOT NULL, reactor_alt TEXT NOT NULL, participant_alt TEXT NOT NULL,
      emoji TEXT NOT NULL, event_timestamp BIGINT NOT NULL, provider_id TEXT NOT NULL,
      PRIMARY KEY(tenant_id,channel_id,account,chat,target_id,target_from_me,participant,reactor))""")


def _target(row):
    raw = row.get('raw') or {}
    if not isinstance(raw, dict):
        return None
    direction = row.get('direction')
    channel = str(row.get('source_channel_id') or '')
    conversation = row.get('source_conversation_id') or ''
    chat = raw.get('_crm_provider_conversation_id') or raw.get('conversationId')
    original = raw.get('externalMessageId')
    if (not channel or row.get('channel') != 'connect:'+channel or raw.get('channelId') != channel
            or raw.get('conversationIdentityConflict') is True
            or raw.get('fromMe') is not (direction == 'outgoing') or direction not in {'incoming', 'outgoing'}
            or row.get('message_type') not in MESSAGE_TYPES or not isinstance(original, str)
            or not PROVIDER_ID.fullmatch(original) or not isinstance(chat, str)
            or not (DIRECT.fullmatch(chat) or GROUP.fullmatch(chat))
            or (chat != conversation and not (DIRECT.fullmatch(chat) and DIRECT.fullmatch(conversation)))):
        return None
    key = {'id': original, 'from_me': direction == 'outgoing'}
    if not key['from_me'] and GROUP.fullmatch(chat):
        try:
            key['participant'] = _jid(raw.get('participantJid'), participant=True)
        except TenantLinearError:
            return None
    return {'chat': chat, 'key': key}


def _enabled():
    return all(os.environ.get('BEINTASKBOT_UNIFIED_WHATSAPP_'+name) == '1'
               for name in ('SEND','INBOX','PROJECTION'))


def _authorize(cur, session, lead, message_id):
    from tenant_chat_send_store import authority
    from tenant_connect_dialog_equivalence import members
    from tenant_connect_identity import conflicted
    from tenant_policy import TenantPolicy
    from tenant_whatsapp_history_pairs import history_pair

    if not _enabled():
        raise TenantLinearError('reaction_source_unavailable', 409)
    profile, connection, integration = authority(cur, session, lead)
    route = integration.get('route') or {}
    if connection[0] != 'connect' or not route.get('native'):
        raise TenantLinearError('reaction_source_unavailable', 409)
    tenant, channel, conversation = profile['tenant_id'], connection[1], connection[2]
    cur.execute("""SELECT m.raw,m.channel,m.direction,m.message_type,c.conversation_id AS source_conversation_id,
      c.lead_id AS source_lead_id,c.connect_channel_id::text AS source_channel_id
      FROM saas_crm_messages m JOIN saas_connect_conversations c
        ON c.tenant_id=m.tenant_id AND c.lead_id=m.kommo_lead_id AND m.channel='connect:'||c.connect_channel_id::text
      WHERE m.tenant_id=%s::uuid AND m.id=%s::uuid FOR SHARE OF m,c""", (tenant,message_id))
    row = cur.fetchone()
    target = _target(row) if row else None
    if not target:
        raise TenantLinearError('reaction_message_unavailable', 404)
    source, original_route = row['source_channel_id'], row['source_conversation_id']
    original_lead = int(row['source_lead_id'])
    if GROUP.fullmatch(conversation):
        if target['chat'] != conversation or original_route != conversation:
            raise TenantLinearError('reaction_message_unavailable', 404)
        if source != channel:
            _, source_connection, original_integration = authority(cur, session, original_lead)
            route = original_integration.get('route') or {}
            if source_connection != ['connect',source,conversation] or not route.get('native'):
                raise TenantLinearError('reaction_source_unavailable', 409)
    else:
        if source != channel or GROUP.fullmatch(target['chat']) or conflicted(cur,tenant,channel,conversation):
            raise TenantLinearError('reaction_message_unavailable', 404)
        allowed = members(cur,tenant,channel,conversation)
        if original_route not in allowed or target['chat'] not in allowed:
            pair = history_pair(cur,tenant,TenantPolicy(profile),channel,conversation,lead)
            if pair:
                allowed.update({pair['primary_id']:pair['primary_lead'],pair['alternate_id']:pair['alternate_lead']})
        if allowed.get(original_route) != original_lead:
            raise TenantLinearError('reaction_message_unavailable', 404)
        if target['chat'] not in allowed:
            from tenant_whatsapp_native_status import verified_receipt_chats
            if target['chat'] not in verified_receipt_chats(cur,tenant,channel,original_route):
                raise TenantLinearError('reaction_message_unavailable', 404)
    scope = route['native']['scope']
    cur.execute('''SELECT 1 FROM saas_whatsapp_capture_generations
      WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND generation=%s AND projection_enabled FOR SHARE''',
      (scope['tenant'],scope['source'],scope['generation']))
    if not cur.fetchone():
        raise TenantLinearError('reaction_source_unavailable',409)
    return {**route, 'conversation_id':target['chat'], 'native_actor_id':int(profile['telegram_id']),
            '_native_cursor':cur}, target['key']


def _observed(cur, binding):
    scope, key = binding['scope'], binding['key']
    cur.execute("""SELECT emoji,event_timestamp,provider_id FROM saas_whatsapp_reactions
      WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND account=%s AND chat=%s
        AND target_id=%s AND target_from_me=%s AND participant=%s""",
      (scope['tenant'],scope['source'],scope['account'],binding['chat'],key['id'],key['from_me'],key.get('participant','')))
    return cur.fetchone()


def _binding(route, key):
    return {'scope':route['native']['scope'],'gateway':route['native']['gateway_id'],
            'chat':route['conversation_id'],'key':key}


def _identity(session, request_id):
    return str(uuid.UUID(str(session['tenant_id']))),int(session['telegram_id']),str(uuid.UUID(str(request_id)))


def _read(cur, identity):
    cur.execute("""SELECT lead_id,message_id::text,target,emoji
      FROM saas_whatsapp_reaction_operations
      WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid FOR UPDATE""", identity)
    return cur.fetchone()


def _accepted(cur, identity, provider):
    cur.execute("""UPDATE saas_whatsapp_reaction_operations SET provider_id=%s
      WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid""", (provider,*identity))


def reserve(session, lead, message, request_id, emoji):
    identity = _identity(session, request_id)
    with _connect() as conn, conn.cursor() as cur:
        route,key = _authorize(cur,session,lead,message)
        binding = _binding(route,key)
        cur.execute("""INSERT INTO saas_whatsapp_reaction_operations
          (tenant_id,actor_id,request_id,lead_id,message_id,target,emoji)
          VALUES(%s::uuid,%s,%s::uuid,%s,%s::uuid,%s::jsonb,%s)
          ON CONFLICT DO NOTHING RETURNING request_id""", (*identity,lead,message,json.dumps(binding),emoji))
        created = cur.fetchone() is not None
        if not created:
            old = _read(cur,identity)
            if (old['lead_id'],old['message_id'],old['target'],old['emoji']) != (lead,message,binding,emoji):
                raise TenantLinearError('reaction_request_changed',409)
    return identity, binding, created


def send(session, lead, message_id, request_id, emoji):
    from tenant_whatsapp_native_transport import submit

    lead, message = int(str(lead)), str(uuid.UUID(str(message_id)))
    if not 0 < lead < 2**63 or not isinstance(emoji,str) or emoji not in EMOJI | {''}:
        raise ValueError('reaction')
    identity,binding,created = reserve(session,lead,message,request_id,emoji)
    if not created:
        return status(session,lead,request_id)
    with _connect() as conn, conn.cursor() as cur:
        route,key = _authorize(cur,session,lead,message)
        if _binding(route,key) != binding:
            raise TenantLinearError('reaction_source_changed',409)
        # Serialize reactions from different employees against the same account and message.
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                    ('whatsapp-reaction:'+identity[0]+':'+route['channel_id']+':'+route['conversation_id']+':'+key['id'],))
        try:
            sent = submit(route,identity[2],kind='reaction',text=emoji,reaction=key)
        except TenantPlatformError:
            return {'state':'unknown','emoji':emoji}
        _accepted(cur,identity,sent['external_message_id'])
        return {'state':'accepted','emoji':emoji}


def status(session, lead, request_id):
    from tenant_whatsapp_native_transport import lookup

    lead,identity = int(str(lead)),_identity(session,request_id)
    if not 0 < lead < 2**63:
        raise ValueError('reaction lead')
    with _connect() as conn, conn.cursor() as cur:
        # Read before acquiring route locks. POST takes route locks first.
        cur.execute("""SELECT lead_id,message_id::text,target,emoji,provider_id,
            floor(extract(epoch from created_at)*1000)::bigint AS created_ms
          FROM saas_whatsapp_reaction_operations
          WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid""", identity)
        row = cur.fetchone()
        if not row or row['lead_id'] != lead:
            raise TenantLinearError('reaction_request_unavailable',404)
        route,key = _authorize(cur,session,lead,row['message_id'])
        if _binding(route,key) != row['target']:
            raise TenantLinearError('reaction_source_changed',409)
        observed = _observed(cur,row['target'])
        if not row['provider_id']:
            try:
                receipt = lookup(route,identity[2],reaction=True)
            except TenantPlatformError:
                receipt = {}
            if (receipt.get('providerAccepted') or
                    observed and receipt.get('externalMessageId') == observed['provider_id']):
                _accepted(cur,identity,receipt['externalMessageId'])
                row['provider_id'] = receipt['externalMessageId']
        display_emoji = row['emoji']
        if observed and int(observed['event_timestamp']) >= int(row['created_ms']):
            display_emoji = observed['emoji']
        return {'state':'accepted' if row['provider_id'] else 'unknown','emoji':display_emoji}


def _alternate(jid, alternate):
    try:
        other = _jid(alternate, optional=True, participant=True)
    except TenantLinearError:
        return ''
    if not jid or not other or jid.rsplit('@',1)[1] == other.rsplit('@',1)[1]:
        return ''
    return other


def observe(cur, scope, payload):
    """Keep the linked account and each outside participant independent."""
    key = payload.get('reaction')
    own = payload.get('from_me')
    if (type(own) is not bool or not isinstance(key,dict)
            or set(key)-{'id','from_me','participant'} or type(key.get('from_me')) is not bool):
        return False
    chat, target, provider = payload.get('chat'),key.get('id'),payload.get('provider_id')
    emoji, stamp = payload.get('text',''),payload.get('timestamp')
    if (not all(isinstance(value,str) for value in (chat,target,provider,emoji))
            or not (DIRECT.fullmatch(chat) or GROUP.fullmatch(chat))
            or not PROVIDER_ID.fullmatch(target) or not PROVIDER_ID.fullmatch(provider)
            or any(c.isspace() or unicodedata.category(c) in {'Cc','Cs'} for c in emoji)
            or len(emoji.encode('utf-8')) > 128
            or type(stamp) is not int or not 1230768000000 <= stamp <= 4133980800000):
        return False
    participant = key.get('participant','')
    if GROUP.fullmatch(chat) and not key['from_me']:
        try:
            participant = _jid(participant,participant=True)
        except TenantLinearError:
            return False
    elif participant:
        return False
    if not own:
        try:
            actor = _jid(payload.get('sender'), participant=True)
        except TenantLinearError:
            return False
        actor_alt = _alternate(actor,payload.get('sender_alt'))
        if actor == scope['account'] or actor_alt == scope['account']:
            return False
        if GROUP.fullmatch(chat):
            if actor.endswith('@lid') and actor_alt:
                actor,actor_alt = actor_alt,actor
        else:
            if chat not in (actor,actor_alt):
                return False
            # One remote sender per private chat, even if its address changes.
            actor = actor_alt = ''
        participant_alt = _alternate(participant,payload.get('reaction_participant_alt'))
        cur.execute("""INSERT INTO saas_whatsapp_peer_reactions AS old
          (tenant_id,channel_id,account,chat,target_id,target_from_me,participant,
           reactor,reactor_alt,participant_alt,emoji,event_timestamp,provider_id)
          VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT(tenant_id,channel_id,account,chat,target_id,target_from_me,participant,reactor)
          DO UPDATE SET emoji=EXCLUDED.emoji,event_timestamp=EXCLUDED.event_timestamp,provider_id=EXCLUDED.provider_id,
            reactor_alt=COALESCE(NULLIF(EXCLUDED.reactor_alt,''),old.reactor_alt),
            participant_alt=COALESCE(NULLIF(EXCLUDED.participant_alt,''),old.participant_alt)
          WHERE (old.event_timestamp,old.provider_id)<(EXCLUDED.event_timestamp,EXCLUDED.provider_id)""",
          (scope['tenant'],scope['source'],scope['account'],chat,target,key['from_me'],participant,
           actor,actor_alt,participant_alt,emoji,stamp,provider))
        return True
    cur.execute("""INSERT INTO saas_whatsapp_reactions
      (tenant_id,channel_id,account,chat,target_id,target_from_me,participant,emoji,event_timestamp,provider_id)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s)
      ON CONFLICT(tenant_id,channel_id,account,chat,target_id,target_from_me,participant)
      DO UPDATE SET emoji=EXCLUDED.emoji,event_timestamp=EXCLUDED.event_timestamp,provider_id=EXCLUDED.provider_id
      WHERE (saas_whatsapp_reactions.event_timestamp,saas_whatsapp_reactions.provider_id)
          < (EXCLUDED.event_timestamp,EXCLUDED.provider_id)""",
      (scope['tenant'],scope['source'],scope['account'],chat,target,key['from_me'],participant,emoji,stamp,provider))
    return True


def attach(cur, tenant, messages):
    for message in messages:
        message.update(wa_reaction='',wa_reaction_available=False,wa_reaction_event='',wa_peer_reactions=[])
    targets = []
    messages_by_id = {str(message['id']):message for message in messages}
    for message in messages:
        target = _target(message)
        if target:
            key = target['key']
            targets.append({'row_id':str(message['id']),'channel':str(message['source_channel_id']),
                            'chat':target['chat'],'target_id':key['id'],'from_me':key['from_me'],
                            'participant':key.get('participant','')})
    if not targets:
        return
    cur.execute("""SELECT to_regclass('saas_whatsapp_native_routes') IS NOT NULL
      AND to_regclass('saas_whatsapp_reactions') IS NOT NULL AS ready""")
    if not cur.fetchone()['ready']:
        return
    cur.execute("""SELECT m.row_id,g.account_jid AS account,wr.emoji,wr.event_timestamp,wr.provider_id, r.enabled AND NOT r.paused AND g.enabled AND g.projection_enabled AND s.enabled AND ch.active AS available
      FROM jsonb_to_recordset(%s::jsonb) AS m(row_id text,channel uuid,chat text,target_id text,from_me boolean,participant text)
      JOIN saas_whatsapp_native_routes r ON r.tenant_id=%s::uuid AND r.channel_id=m.channel
      JOIN saas_whatsapp_capture_generations g USING(tenant_id,channel_id,generation)
      JOIN saas_browser_call_sources s ON s.tenant_id=g.tenant_id AND s.channel_id=g.channel_id
        AND s.gateway_id=g.gateway_id AND s.account_jid=g.account_jid
      JOIN saas_connect_channels ch ON ch.tenant_id=r.tenant_id AND ch.connect_channel_id=r.channel_id
      LEFT JOIN saas_whatsapp_reactions wr ON wr.tenant_id=r.tenant_id AND wr.channel_id=r.channel_id
        AND wr.account=g.account_jid AND wr.chat=m.chat AND wr.target_id=m.target_id
        AND wr.target_from_me=m.from_me AND wr.participant=m.participant""", (json.dumps(targets),tenant))
    state = {row['row_id']:row for row in cur.fetchall()}
    enabled = _enabled()
    for message in messages:
        current = state.get(str(message['id']))
        if current:
            message['wa_reaction'] = current['emoji'] or ''
            if current['provider_id']:
                message['wa_reaction_event'] = str(current['event_timestamp'])+':'+current['provider_id']
            message['wa_reaction_available'] = enabled and bool(current['available'])
    _attach_peer_reactions(cur,tenant,targets,state,messages_by_id)


def _attach_peer_reactions(cur, tenant, targets, state, messages_by_id):
    from tenant_whatsapp_native_status import verified_receipt_chats
    chat_aliases = {}
    peer_targets = []
    for target in targets:
        row_id, channel, chat = target['row_id'],target['channel'],target['chat']
        if row_id not in state:
            continue
        participant = target['participant']
        chats = [chat]
        if DIRECT.fullmatch(chat):
            if (channel,chat) not in chat_aliases:
                chat_aliases[channel,chat] = sorted(verified_receipt_chats(cur,tenant,channel,chat))
            chats = chat_aliases[channel,chat]
        raw = messages_by_id[row_id]['raw']
        alt = _alternate(participant,raw.get('participantAlt'))
        peer_targets.append({**target,'chats':chats,'participants':[participant,alt] if alt else [participant],
                             'account':state[row_id]['account']})
    if not peer_targets:
        return
    # Read replacements and removals across proven aliases before counting.
    # Only the latest state for each sender contributes to the displayed count.
    cur.execute("""SELECT m.row_id, peers.emoji, peers.count
      FROM jsonb_to_recordset(%s::jsonb) AS m(row_id text,channel uuid,account text,chats text[],
          target_id text,from_me boolean,participants text[])
      CROSS JOIN LATERAL (
        WITH candidates AS (
          SELECT reactor,reactor_alt,emoji,event_timestamp,provider_id FROM saas_whatsapp_peer_reactions
          WHERE tenant_id=%s::uuid AND channel_id=m.channel AND account=m.account AND chat=ANY(m.chats)
            AND target_id=m.target_id AND target_from_me=m.from_me
            AND (participant=ANY(m.participants) OR participant_alt<>'' AND participant_alt=ANY(m.participants))
        ), latest AS (
          SELECT DISTINCT ON (actor) emoji,
            COALESCE((SELECT min(p.reactor) FROM candidates p WHERE p.reactor_alt=c.reactor AND p.reactor<>c.reactor
                      HAVING count(DISTINCT p.reactor)=1),c.reactor) AS actor
          FROM candidates c ORDER BY actor,event_timestamp DESC,provider_id DESC
        )
        SELECT emoji,count(*)::int AS count FROM latest WHERE emoji<>'' GROUP BY emoji
        ORDER BY count DESC,emoji LIMIT 24
      ) peers""", (json.dumps(peer_targets),tenant))
    for row in cur.fetchall():
        messages_by_id[row['row_id']]['wa_peer_reactions'].append({'emoji':row['emoji'],'count':row['count']})
