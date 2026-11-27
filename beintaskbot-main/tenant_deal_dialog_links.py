"""A scoped local association. This module never creates or exports a CRM record."""
import os
import re
import uuid

from kommo_phone_duplicates import normalize_phone, variants
from tenant_connect_direct_identity import direct_identity, verified_phone_jid
from tenant_crm_native_access import scoped_sql
from tenant_crm_relation_identity import current_binding
from tenant_deal_dialog_candidates import allowed_kommo, choices, kommo_contact, kommo_scope, related_contact
from tenant_dialog_access import require_source
from tenant_kommo_board_provenance import revisions
from tenant_linear_policy import TenantLinearError, identifier
from tenant_operator_access import access, assert_route
from tenant_platform import _connect


def enabled(tenant):
    return str(tenant) in os.getenv('CRM_DEAL_DIALOG_LINK_TENANTS', '').replace(',', ' ').split()


def _route_number(cur, tenant, channel, conversation):
    from tenant_connect_identity import conflicted
    if not direct_identity(conversation) or conflicted(cur, tenant, channel, conversation):
        return ''
    return verified_phone_jid(cur, tenant, channel, conversation).split('@', 1)[0]


def _contact(cur, tenant, channel, conversation):
    number = _route_number(cur, tenant, channel, conversation)
    if not number:
        return None
    cur.execute("""
        SELECT l.contact_id::text AS contact_id FROM saas_crm_native_contact_conversations l
        JOIN saas_crm_native_contacts c ON c.tenant_id=l.tenant_id AND c.id=l.contact_id
        JOIN saas_crm_native_contact_identities i ON i.tenant_id=l.tenant_id
          AND i.contact_id=l.contact_id AND i.identity_type='whatsapp_phone' AND i.identity_value=%s
        WHERE l.tenant_id=%s::uuid AND l.provider='connect' AND l.channel_id=%s::uuid
          AND l.conversation_id=%s AND c.archived_at IS NULL
    """, (number, tenant, channel, conversation))
    return (cur.fetchone() or {}).get('contact_id')


def _subject(cur, tenant, channel, conversation, lead, workflow):
    if workflow and workflow['state'] == 'accepted':
        cur.execute("""
            SELECT id::text AS id FROM saas_dialog_episodes
            WHERE tenant_id=%s::uuid AND id=%s::uuid AND channel_id=%s::uuid
              AND conversation_id=%s AND lead_id=%s AND closed_at IS NULL
        """, (tenant, workflow['id'], channel, conversation, lead))
        row = cur.fetchone()
        return ('episode_id', row['id']) if row else None
    if workflow:
        return None
    # Before acceptance there is no lifecycle episode. Pin the exact first
    # observed phone message. Existing history or a later sale never rides along.
    cur.execute("""
        SELECT id::text AS id, direction, raw FROM saas_crm_messages
        WHERE tenant_id=%s::uuid AND channel=%s AND kommo_lead_id=%s
          AND direction IN ('incoming','outgoing')
        LIMIT 2
    """, (tenant, 'connect:'+channel, lead))
    rows = cur.fetchall()
    if len(rows) != 1 or rows[0]['direction'] != 'outgoing':
        return None
    raw = rows[0]['raw'] or {}
    if (isinstance(raw, dict) and raw.get('fromMe') is True and raw.get('_crm_signed_outbound_receipt') is True
            and raw.get('message_deleted') is not True and raw.get('ai_authored') is not True
            and (raw.get('source') == 'linked_phone' or raw.get('authorshipUncertain') is True)):
        return ('message_id', rows[0]['id'])
    return None


def _confirmed_outbound(cur, tenant, channel, conversation, lead, subject):
    # A pre-pilot conversation may lack a native contact. Only a persisted
    # provider receipt in the selected context can resolve that exact route.
    if subject[0] == 'episode_id':
        selected = """EXISTS (SELECT 1 FROM saas_dialog_episode_messages e
            WHERE e.tenant_id=m.tenant_id AND e.message_id=m.id AND e.episode_id=%s::uuid)"""
    else:
        selected = 'm.id=%s::uuid'
    cur.execute(f"""
        SELECT m.external_id FROM saas_crm_messages m
        WHERE m.tenant_id=%s::uuid AND m.channel=%s AND m.kommo_lead_id=%s
          AND m.direction='outgoing' AND m.external_id LIKE 'connect:%%'
          AND m.raw->>'fromMe'='true' AND m.raw->>'message_deleted' IS DISTINCT FROM 'true'
          AND m.raw->>'channelId'=%s AND m.raw->>'conversationId'=%s
          AND (m.raw->>'_crm_signed_outbound_receipt'='true' OR m.raw->>'_crm_native_send_receipt'='true')
          AND ({selected})
        ORDER BY m.created_at,m.id LIMIT 1
    """, (tenant, 'connect:'+channel, lead, channel, conversation, subject[1]))
    row = cur.fetchone()
    return row['external_id'].removeprefix('connect:') if row else None


def _existing(cur, tenant, subject):
    column, ident = subject
    cur.execute(f"SELECT * FROM saas_crm_deal_dialog_links WHERE tenant_id=%s::uuid AND {column}=%s::uuid",
                (tenant, ident))
    return cur.fetchone()


def _kommo_deal(cur, tenant, binding, policy, ident, *, update=False, lock=False):
    if not binding:
        return None
    cur.execute("""
        SELECT * FROM saas_crm_deals
        WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND deleted_at IS NULL
    """ + (' FOR SHARE' if lock else ''), (tenant, ident))
    deal = cur.fetchone()
    if (deal and allowed_kommo(policy, deal, update=update)
            and revisions(cur, tenant, 'leads', [ident], binding=binding,
                          expected_raw={str(ident):deal['raw']})):
        return deal
    return None


def _deal(cur, tenant, binding, policy, source, ident, contact, *, update=False, explicit_contact=''):
    """Recheck an individual deal for the saved contact and current credentials."""
    if source == 'native':
        view, view_args = scoped_sql(policy.profile, 'deals', 'view', 'd')
        edit, edit_args = scoped_sql(policy.profile, 'deals', 'update', 'd') if update else ('TRUE', [])
        lock = ' FOR SHARE OF d' if update else ''
        cur.execute(f"""
            SELECT d.id FROM saas_crm_native_deals d
            WHERE d.tenant_id=%s::uuid AND d.id=%s::uuid AND d.contact_id=%s::uuid
              AND d.archived_at IS NULL AND ({view}) AND ({edit}){lock}
        """, (tenant, ident, contact, *view_args, *edit_args))
        return bool(cur.fetchone())
    deal = _kommo_deal(cur, tenant, binding, policy, ident, update=update, lock=update)
    if not deal:
        return False
    verified_contact = kommo_contact(cur, tenant, contact, binding)
    if explicit_contact and verified_contact not in (None, explicit_contact):
        return False
    return related_contact(deal, explicit_contact or verified_contact)


def _same_deal(link, binding, source, ident, contact):
    scope = kommo_scope(binding) if source=='kommo' else ('', '')
    return ((link['deal_source'], link['deal_id'], str(link['contact_id']),
             link['account_id'], link['connection_generation']) == (source, ident, contact, *scope))


def proposal(cur, tenant, actor, policy, route, workflow):
    """Only called for an already-authorized ordinary dialogue read."""
    channel, conversation, lead = str(route['channel_id']), route['conversation_id'], route['lead_id']
    if (not direct_identity(conversation) or not route.get('channel_active')
            or not policy.allows_action('dialogs', 'update')
            or (workflow and workflow.get('operator_id') != actor)):
        return None
    contact = _contact(cur, tenant, channel, conversation)
    subject = _subject(cur, tenant, channel, conversation, lead, workflow) if contact else None
    if not subject:
        return None
    try:
        require_source(cur, tenant, policy, 'connect', channel, 'update')
    except TenantLinearError:
        return None
    binding = current_binding(cur, tenant, lock=False)
    prior = _existing(cur, tenant, subject)
    if prior:
        if (_same_deal(prior, binding, prior['deal_source'], prior['deal_id'], contact)
                and _deal(cur, tenant, binding, policy, prior['deal_source'], prior['deal_id'], contact,
                          explicit_contact=prior['kommo_contact_id'])):
            return {'linked':True}
        return None
    available, suggested = choices(cur, tenant, contact, binding, policy)
    if not available or binding != current_binding(cur, tenant, lock=False):
        return None
    return {'context_id':subject[1], 'deals':available, 'suggested':suggested}


def _deal_id(source, value):
    if source == 'native':
        return identifier(value)
    if source == 'kommo' and isinstance(value, str) and re.fullmatch(r'[1-9][0-9]{0,18}', value) and int(value) < 2**63:
        return value
    raise TenantLinearError('Сделка недоступна.', 404)


def _unavailable():
    return TenantLinearError('Связь со сделкой недоступна.', 404)


def _phone_evidence(cur, tenant, binding, policy, deal_id, message_id=None, *, lock=False):
    from tenant_kommo_deal_details import _relations
    from tenant_policy import positive_id

    deal = _kommo_deal(cur, tenant, binding, policy, deal_id, update=True, lock=lock)
    if not deal or deal['status_id'] <= 0 or deal['status_id'] in (142, 143):
        raise _unavailable()
    contact, _ = _relations(deal['raw'])
    external_contact = positive_id(contact.get('id'))
    if not external_contact:
        raise _unavailable()

    # Message text is a lookup hint only. In automatic mode require one
    # normalized number across the visible TikTok messages for this deal.
    require_source(cur, tenant, policy, 'kommo', action='view')
    selected = 'id=%s::uuid' if message_id else "length(body)<=80 AND btrim(body) ~ '^[+0-9 ()-]+$'"
    parameters = [message_id] if message_id else []
    cur.execute(f"""
        SELECT id::text AS id,body FROM saas_crm_messages WHERE tenant_id=%s::uuid
          AND kommo_lead_id=%s AND direction='incoming' AND message_type='text'
          AND lower(channel) IN ('tiktok','tiktok_kommo')
          AND raw->>'message_deleted' IS DISTINCT FROM 'true'
          AND created_at>=%s::timestamptz
          AND ({selected})
        ORDER BY created_at DESC,id LIMIT 101
    """ + (' FOR SHARE' if lock else ''),
        (tenant, deal_id, binding.connection_generation.split(':', 1)[1], *parameters))
    messages = cur.fetchall()
    numbers = {}
    for row in messages:
        try:
            numbers.setdefault(normalize_phone(row['body'])[1:], row['id'])
        except ValueError:
            if message_id:
                raise TenantLinearError('В сообщении нужен полный номер телефона.', 422) from None
    if len(messages) > 100 or len(numbers) != 1:
        raise _unavailable()
    number, proof_id = next(iter(numbers.items()))
    return number, str(external_contact), proof_id


def _phone_routes(cur, tenant, number):
    aliases = [number+'@s.whatsapp.net', number+'@c.us']
    cur.execute("""
            SELECT c.connect_channel_id::text AS channel_id,c.conversation_id,c.lead_id,
                   s.name AS channel_name
            FROM saas_connect_channels s JOIN LATERAL (
                SELECT conversation_id FROM saas_connect_conversations
                WHERE tenant_id=s.tenant_id AND connect_channel_id=s.connect_channel_id
                  AND conversation_id=ANY(%s::text[])
                UNION
                SELECT canonical_conversation_id FROM saas_connect_direct_identities
                WHERE tenant_id=s.tenant_id AND connect_channel_id=s.connect_channel_id
                  AND identity_key=ANY(%s::text[])
            ) x ON TRUE
            JOIN saas_connect_conversations c ON c.tenant_id=s.tenant_id
              AND c.connect_channel_id=s.connect_channel_id AND c.conversation_id=x.conversation_id
            WHERE s.tenant_id=%s::uuid AND s.active AND c.conversation_kind='contact'
            ORDER BY s.connect_channel_id,c.conversation_id
    """, (aliases, aliases, tenant))
    return cur.fetchall()


def _phone_candidates(cur, tenant, actor, policy, binding, number, external_contact, routes, *, allow_existing=False):
    from tenant_dialog_lifecycle import _row, enabled as lifecycle_enabled
    active = lifecycle_enabled(cur, tenant)
    result = []
    for row in routes:
        channel, conversation, lead = row['channel_id'], row['conversation_id'], row['lead_id']
        try:
            require_source(cur, tenant, policy, 'connect', channel, 'update')
            assert_route(cur, tenant, policy, 'connect', conversation, channel, lead)
        except TenantLinearError as error:
            if error.status not in (403, 404):
                raise
            continue
        if _route_number(cur, tenant, channel, conversation) != number:
            continue
        workflow = _row(cur, tenant, channel, conversation) if active else None
        if workflow and workflow.get('operator_id') != actor:
            continue
        subject = _subject(cur, tenant, channel, conversation, lead, workflow)
        if not subject or (not allow_existing and _existing(cur, tenant, subject)):
            continue
        contact = _contact(cur, tenant, channel, conversation)
        if contact:
            if kommo_contact(cur, tenant, contact, binding) not in (None, external_contact):
                continue
        elif not _confirmed_outbound(cur, tenant, channel, conversation, lead, subject):
            continue
        result.append({**row, 'context_id':subject[1]})
    if binding != current_binding(cur, tenant, lock=False):
        raise _unavailable()
    return result


def phone_candidates(profile, deal_id, phone_message_id):
    tenant = identifier(profile.get('tenant_id'))
    if not enabled(tenant):
        raise _unavailable()
    ident, message_id = _deal_id('kommo', deal_id), identifier(phone_message_id)
    with _connect() as conn, conn.cursor() as cur:
        tenant, actor, policy = access(cur, profile, action='update')
        binding = current_binding(cur, tenant, lock=False)
        number, external, _ = _phone_evidence(cur, tenant, binding, policy, ident, message_id)
        return {'candidates':_phone_candidates(cur, tenant, actor, policy, binding, number,
                                               external, _phone_routes(cur, tenant, number))}


def _auto_attach(profile, deal_id):
    # Count matching routes before filtering by employee access. Never select
    # the only visible route if another match is hidden from this employee.
    try:
        with _connect() as conn, conn.cursor() as cur:
            tenant, actor, policy = access(cur, profile, action='update')
            binding = current_binding(cur, tenant, lock=False)
            number, external, message_id = _phone_evidence(cur, tenant, binding, policy, deal_id)
            routes = _phone_routes(cur, tenant, number)
            if len(routes) != 1:
                return {'linked':False}
            eligible = _phone_candidates(cur, tenant, actor, policy, binding, number, external, routes,
                                         allow_existing=True)
        if not eligible:
            return {'linked':False}
        candidate = {key:value for key, value in eligible[0].items() if key != 'channel_name'}
        return attach(profile, {**candidate,'deal_source':'kommo','deal_id':deal_id,
                                'suggested':True,'phone_message_id':message_id})
    except TenantLinearError as error:
        if error.status not in (403, 404, 409, 422):
            raise
        return {'linked':False}


def _other_tiktok_deal_for_number(cur, tenant, binding, deal_id, number):
    # Unverified text in another open deal is enough to block automatic
    # matching, but is never used to grant access to that deal.
    numbers = {item.removeprefix('+') for item in variants('+'+number)} | {'00'+number}
    cur.execute("""
        SELECT 1 FROM saas_crm_messages m
        JOIN saas_crm_deals d ON d.tenant_id=m.tenant_id AND d.kommo_lead_id=m.kommo_lead_id
        WHERE m.tenant_id=%s::uuid AND m.kommo_lead_id<>%s AND d.deleted_at IS NULL
          AND d.status_id>0 AND d.status_id NOT IN (142,143)
          AND m.direction='incoming' AND m.message_type='text' AND lower(m.channel) IN ('tiktok','tiktok_kommo')
          AND m.raw->>'message_deleted' IS DISTINCT FROM 'true' AND m.created_at>=%s::timestamptz
          AND length(m.body)<=80 AND btrim(m.body) ~ '^[+0-9 ()-]+$'
          AND regexp_replace(m.body,'[^0-9]','','g')=ANY(%s::text[])
        LIMIT 1
    """, (tenant, deal_id, binding.connection_generation.split(':', 1)[1], sorted(numbers)))
    return bool(cur.fetchone())


def attach(profile, data):
    tenant = identifier(profile.get('tenant_id'))
    if not enabled(tenant):
        raise _unavailable()
    if set(data) == {'auto_phone','deal_id'} and data['auto_phone'] is True:
        return _auto_attach(profile, _deal_id('kommo', data['deal_id']))
    required = {'channel_id','conversation_id','lead_id','context_id','deal_source','deal_id','suggested'}
    if set(data) not in (required, required|{'phone_message_id'}):
        raise TenantLinearError('Некорректный запрос.', 400)
    source, suggested = data['deal_source'], data['suggested']
    ident, context = _deal_id(source, data['deal_id']), identifier(data['context_id'])
    channel, conversation = identifier(data['channel_id']), data['conversation_id']
    lead = data['lead_id']
    phone_message_id = identifier(data['phone_message_id']) if 'phone_message_id' in data else None
    if (type(suggested) is not bool or type(lead) is not int or not 0 < lead < 2**63
            or phone_message_id and source != 'kommo'
            or not isinstance(conversation, str) or direct_identity(conversation) != conversation or not conversation):
        raise TenantLinearError('Выберите личный диалог WhatsApp.', 400)

    with _connect() as conn, conn.cursor() as cur:
        tenant, actor, policy = access(cur, profile, action='update')
        require_source(cur, tenant, policy, 'connect', channel, 'view')
        require_source(cur, tenant, policy, 'connect', channel, 'update')
        cur.execute("""
            SELECT 1 FROM saas_connect_channels WHERE tenant_id=%s::uuid
              AND connect_channel_id=%s::uuid AND active FOR SHARE
        """, (tenant, channel))
        if not cur.fetchone():
            raise _unavailable()
        from tenant_ai_dialog_scope import lock_dialog
        from tenant_dialog_lifecycle import _row, enabled as lifecycle_enabled
        lock_dialog(cur, tenant, 'connect', channel, conversation)
        route = assert_route(cur, tenant, policy, 'connect', conversation, channel, lead)
        if route.get('conversation_kind') == 'group':
            raise _unavailable()
        contact = _contact(cur, tenant, channel, conversation)
        if not contact and not phone_message_id:
            raise _unavailable()
        binding = current_binding(cur, tenant, lock=False)
        cur.execute("""
            SELECT * FROM saas_crm_deal_dialog_links WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
              AND conversation_id=%s AND lead_id=%s AND (message_id=%s::uuid OR episode_id=%s::uuid)
        """, (tenant, channel, conversation, lead, context, context))
        prior = cur.fetchone()
        if prior:
            if (_same_deal(prior, binding, source, ident, contact)
                    and _deal(cur, tenant, binding, policy, source, ident, contact, update=True,
                              explicit_contact=prior['kommo_contact_id'])):
                return {'linked':True,'link_id':str(prior['id'])}
            raise TenantLinearError('Переписку уже привязали. Обновите диалог.', 409)

        workflow = _row(cur, tenant, channel, conversation) if lifecycle_enabled(cur, tenant) else None
        if workflow and workflow.get('operator_id') != actor:
            raise _unavailable()
        subject = _subject(cur, tenant, channel, conversation, lead, workflow)
        if not subject or subject[1] != context:
            raise TenantLinearError('История изменилась. Обновите диалог.', 409)
        explicit_contact = ''
        if phone_message_id:
            number, explicit_contact, proof_id = _phone_evidence(
                cur, tenant, binding, policy, ident, None if suggested else phone_message_id, lock=True)
            if suggested and (proof_id != phone_message_id or len(_phone_routes(cur, tenant, number)) != 1
                              or _other_tiktok_deal_for_number(cur, tenant, binding, ident, number)):
                raise TenantLinearError('Совпадение перестало быть однозначным.', 409)
            if _route_number(cur, tenant, channel, conversation) != number:
                raise _unavailable()
            if not contact:
                evidence = _confirmed_outbound(cur, tenant, channel, conversation, lead, subject)
                if not evidence:
                    raise _unavailable()
                from tenant_crm_native_contacts import upsert_whatsapp_contact
                contact = upsert_whatsapp_contact(cur, tenant, channel, conversation, number,
                                                  message_id=evidence, adopt_by_phone=False)
            if not contact or kommo_contact(cur, tenant, contact, binding) not in (None, explicit_contact):
                raise _unavailable()
            if suggested:
                available, unique = choices(cur, tenant, contact, binding, policy,
                                            verified_kommo_contact=explicit_contact)
                if not unique or (available[0]['source'], available[0]['id']) != ('kommo', ident):
                    raise TenantLinearError('Совпадение перестало быть однозначным.', 409)
        else:
            if not _deal(cur, tenant, binding, policy, source, ident, contact, update=True):
                raise _unavailable()
            available, unambiguous = choices(cur, tenant, contact, binding, policy)
            if (not any((row['source'], row['id']) == (source, ident) for row in available)
                    or suggested and not unambiguous):
                raise TenantLinearError('Список доступных сделок изменился. Обновите диалог.', 409)
        if binding != current_binding(cur, tenant, lock=False):
            raise TenantLinearError('Список доступных сделок изменился. Обновите диалог.', 409)

        link_id = str(uuid.uuid4())
        scope = kommo_scope(binding) if source == 'kommo' else ('', '')
        cur.execute("""
            INSERT INTO saas_crm_deal_dialog_links
              (tenant_id,id,channel_id,conversation_id,lead_id,contact_id,message_id,episode_id,
               deal_source,deal_id,account_id,connection_generation,created_by,kommo_phone_message_id,kommo_contact_id)
            VALUES (%s::uuid,%s::uuid,%s::uuid,%s,%s,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s::uuid,%s)
            ON CONFLICT DO NOTHING RETURNING id
        """, (tenant, link_id, channel, conversation, lead, contact,
              context if subject[0]=='message_id' else None, context if subject[0]=='episode_id' else None,
              source, ident, *scope, actor, phone_message_id, explicit_contact))
        if not cur.fetchone():
            raise TenantLinearError('Переписку уже привязали. Обновите диалог.', 409)
        audit_action = 'deal_dialog_auto_link' if phone_message_id and suggested else 'deal_dialog_link'
        cur.execute("""
            INSERT INTO saas_tenant_audit_events
              (id,tenant_id,actor_telegram_id,action,entity_type,entity_id)
            VALUES (%s::uuid,%s::uuid,%s,%s,'deal_dialog_link',%s)
        """, (str(uuid.uuid4()), tenant, actor, audit_action, link_id))
        return {'linked':True,'link_id':link_id}


def _visible_route(cur, tenant, binding, policy, link):
    channel, conversation, lead = str(link['channel_id']), link['conversation_id'], link['lead_id']
    try:
        route = assert_route(cur, tenant, policy, 'connect', conversation, channel, lead,
                             allow_closed_archive=True, owner_overview=policy.profile.get('role')=='owner')
    except TenantLinearError as error:
        if error.status not in (403, 404):
            raise
        return None
    contact = _contact(cur, tenant, channel, conversation)
    if (not contact or route.get('conversation_kind') == 'group'
            or not _same_deal(link, binding, link['deal_source'], link['deal_id'], contact)
            or not _deal(cur, tenant, binding, policy, link['deal_source'], link['deal_id'], contact,
                         explicit_contact=link['kommo_contact_id'])):
        return None
    cur.execute("""
        SELECT name FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid
    """, (tenant, channel))
    origin = cur.fetchone()
    if not origin:
        return None
    return {'provider':'connect','channel_id':channel,'conversation_id':conversation,'lead_id':lead,
            'channel_name':origin['name'] or 'WhatsApp','deal_link_id':str(link['id'])}


def routes(cur, tenant, policy, source, ident, binding):
    if not enabled(tenant):
        return []
    scope = kommo_scope(binding) if source == 'kommo' else ('', '')
    cur.execute("""
        SELECT DISTINCT ON (channel_id,conversation_id,lead_id,contact_id) *
        FROM saas_crm_deal_dialog_links WHERE tenant_id=%s::uuid AND deal_source=%s AND deal_id=%s
          AND account_id=%s AND connection_generation=%s
        ORDER BY channel_id, conversation_id, lead_id, contact_id, created_at, id
    """, (tenant, source, ident, *scope))
    return [route for link in cur.fetchall() if (route := _visible_route(cur, tenant, binding, policy, link))]


def history(profile, source, deal_id, link_id, before=None):
    tenant = identifier(profile.get('tenant_id'))
    if not enabled(tenant):
        raise _unavailable()
    ident = _deal_id(source, deal_id)
    with _connect() as conn, conn.cursor() as cur:
        tenant, _, policy = access(cur, profile)
        binding = current_binding(cur, tenant, lock=False) if source == 'kommo' else None
        scope = kommo_scope(binding) if source == 'kommo' else ('', '')
        cur.execute("""
            SELECT * FROM saas_crm_deal_dialog_links WHERE tenant_id=%s::uuid AND id=%s::uuid
              AND deal_source=%s AND deal_id=%s AND account_id=%s AND connection_generation=%s
        """, (tenant, identifier(link_id), source, ident, *scope))
        link = cur.fetchone()
        if not link or not (route := _visible_route(cur, tenant, binding, policy, link)):
            raise _unavailable()
        from tenant_dialog_lifecycle import enabled as lifecycle_enabled
        # Older workspaces can link one observed message without lifecycle DDL.
        episode_messages = """
              UNION
              SELECT e.message_id FROM links l JOIN saas_dialog_episode_messages e
                ON e.tenant_id=%s::uuid AND e.episode_id=l.episode_id
        """ if lifecycle_enabled(cur, tenant) else ""
        episode_args = (tenant,) if episode_messages else ()
        # Both the cursor and each selected row must belong to this deal's links
        # on this exact route. No quote expansion, internal events or previews.
        cur.execute("""
            WITH links AS MATERIALIZED (
              SELECT message_id,episode_id FROM saas_crm_deal_dialog_links
              WHERE tenant_id=%s::uuid AND deal_source=%s AND deal_id=%s AND account_id=%s
                AND connection_generation=%s AND channel_id=%s::uuid AND conversation_id=%s
                AND lead_id=%s AND contact_id=%s::uuid
            ), selected AS (
              SELECT message_id FROM links WHERE message_id IS NOT NULL
              """ + episode_messages + """
            ), anchor AS (
              SELECT COALESCE(m.happened_at,m.created_at) AS at,m.id
              FROM saas_crm_messages m JOIN selected s ON s.message_id=m.id
              WHERE m.tenant_id=%s::uuid AND m.channel=%s AND m.kommo_lead_id=%s AND m.id=%s::uuid
                AND m.direction IN ('incoming','outgoing')
            )
            SELECT m.id,m.external_id,m.direction,m.channel,m.author_name,m.body,m.message_type,
                   m.happened_at,m.created_at,m.raw,m.kommo_lead_id AS source_lead_id
            FROM saas_crm_messages m JOIN selected s ON s.message_id=m.id
            WHERE m.tenant_id=%s::uuid AND m.channel=%s AND m.kommo_lead_id=%s
              AND m.direction IN ('incoming','outgoing')
              AND (%s OR (COALESCE(m.happened_at,m.created_at),m.id)<(SELECT at,id FROM anchor))
            ORDER BY COALESCE(m.happened_at,m.created_at) DESC,m.id DESC LIMIT 51
        """, (tenant, source, ident, *scope, route['channel_id'], route['conversation_id'], route['lead_id'],
              link['contact_id'], *episode_args, tenant, 'connect:'+route['channel_id'], route['lead_id'],
              identifier(before) if before else None, tenant, 'connect:'+route['channel_id'],
              route['lead_id'], not before))
        rows = cur.fetchall()
        if source == 'kommo' and binding != current_binding(cur, tenant, lock=False):
            raise _unavailable()

    from tenant_operator_dialogs import _message
    messages = []
    for row in reversed(rows[:50]):
        item = _message({**row,'source_channel_id':route['channel_id'],
                         'source_conversation_id':route['conversation_id']})
        # Embed only data scoped to this message. The full conversation has
        # separate permission checks for attachments and related metadata.
        item.pop('quoted_provider_message_id', None)
        messages.append(item)
    return {'dialog':route,'messages':messages,'next_before':str(rows[49]['id']) if len(rows)>50 else None}
