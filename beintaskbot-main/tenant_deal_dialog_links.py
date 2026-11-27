"""A scoped local association. This module never creates or exports a CRM record."""
import os
import re
import uuid

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


def _contact(cur, tenant, channel, conversation):
    from tenant_connect_identity import conflicted
    if not direct_identity(conversation) or conflicted(cur, tenant, channel, conversation):
        return None
    number = verified_phone_jid(cur, tenant, channel, conversation).split('@', 1)[0]
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


def _existing(cur, tenant, subject):
    column, ident = subject
    cur.execute(f"SELECT * FROM saas_crm_deal_dialog_links WHERE tenant_id=%s::uuid AND {column}=%s::uuid",
                (tenant, ident))
    return cur.fetchone()


def _deal(cur, tenant, binding, policy, source, ident, contact, *, update=False):
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
    if source != 'kommo' or not binding:
        return False
    lock = ' FOR SHARE' if update else ''
    cur.execute("""
        SELECT * FROM saas_crm_deals
        WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND deleted_at IS NULL
    """ + lock, (tenant, ident))
    deal = cur.fetchone()
    return bool(deal and allowed_kommo(policy, deal, update=update)
                and related_contact(deal, kommo_contact(cur, tenant, contact, binding))
                and revisions(cur, tenant, 'leads', [ident], binding=binding,
                              expected_raw={str(ident):deal['raw']}))


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
                and _deal(cur, tenant, binding, policy, prior['deal_source'], prior['deal_id'], contact)):
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


def attach(profile, data):
    tenant = identifier(profile.get('tenant_id'))
    if not enabled(tenant):
        raise _unavailable()
    if set(data) != {'channel_id','conversation_id','lead_id','context_id','deal_source','deal_id','suggested'}:
        raise TenantLinearError('Некорректный запрос.', 400)
    source, suggested = data['deal_source'], data['suggested']
    ident, context = _deal_id(source, data['deal_id']), identifier(data['context_id'])
    channel, conversation = identifier(data['channel_id']), data['conversation_id']
    lead = data['lead_id']
    if (type(suggested) is not bool or type(lead) is not int or not 0 < lead < 2**63
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
        if not contact:
            raise _unavailable()
        binding = current_binding(cur, tenant, lock=False)
        cur.execute("""
            SELECT * FROM saas_crm_deal_dialog_links WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
              AND conversation_id=%s AND lead_id=%s AND (message_id=%s::uuid OR episode_id=%s::uuid)
        """, (tenant, channel, conversation, lead, context, context))
        prior = cur.fetchone()
        if prior:
            if (_same_deal(prior, binding, source, ident, contact)
                    and _deal(cur, tenant, binding, policy, source, ident, contact, update=True)):
                return {'linked':True,'link_id':str(prior['id'])}
            raise TenantLinearError('Переписку уже привязали. Обновите диалог.', 409)

        workflow = _row(cur, tenant, channel, conversation) if lifecycle_enabled(cur, tenant) else None
        if workflow and workflow.get('operator_id') != actor:
            raise _unavailable()
        subject = _subject(cur, tenant, channel, conversation, lead, workflow)
        if not subject or subject[1] != context:
            raise TenantLinearError('История изменилась. Обновите диалог.', 409)
        if not _deal(cur, tenant, binding, policy, source, ident, contact, update=True):
            raise _unavailable()
        available, unambiguous = choices(cur, tenant, contact, binding, policy)
        if (not any((row['source'], row['id']) == (source, ident) for row in available)
                or (suggested and not unambiguous)
                or binding != current_binding(cur, tenant, lock=False)):
            raise TenantLinearError('Список доступных сделок изменился. Обновите диалог.', 409)

        link_id = str(uuid.uuid4())
        scope = kommo_scope(binding) if source == 'kommo' else ('', '')
        cur.execute("""
            INSERT INTO saas_crm_deal_dialog_links
              (tenant_id,id,channel_id,conversation_id,lead_id,contact_id,message_id,episode_id,
               deal_source,deal_id,account_id,connection_generation,created_by)
            VALUES (%s::uuid,%s::uuid,%s::uuid,%s,%s,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING RETURNING id
        """, (tenant, link_id, channel, conversation, lead, contact,
              context if subject[0]=='message_id' else None, context if subject[0]=='episode_id' else None,
              source, ident, *scope, actor))
        if not cur.fetchone():
            raise TenantLinearError('Переписку уже привязали. Обновите диалог.', 409)
        cur.execute("""
            INSERT INTO saas_tenant_audit_events
              (id,tenant_id,actor_telegram_id,action,entity_type,entity_id)
            VALUES (%s::uuid,%s::uuid,%s,'deal_dialog_link','deal_dialog_link',%s)
        """, (str(uuid.uuid4()), tenant, actor, link_id))
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
            or not _deal(cur, tenant, binding, policy, link['deal_source'], link['deal_id'], contact)):
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
