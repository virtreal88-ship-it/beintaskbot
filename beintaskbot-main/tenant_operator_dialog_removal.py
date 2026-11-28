"""Remove a conversation from the CRM inbox while keeping its source and history."""

from json import dumps
from uuid import UUID

from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access, assert_route
from tenant_platform import _connect, _ensure_schema


CONNECT_VISIBLE_SQL = """NOT EXISTS(
    SELECT 1 FROM saas_operator_dialog_removals r
    WHERE r.tenant_id=c.tenant_id AND r.provider='connect'
      AND r.channel_id=c.connect_channel_id AND r.conversation_id=c.conversation_id
      AND r.lead_id=c.lead_id AND NOT EXISTS(
        SELECT 1 FROM saas_crm_messages m
        WHERE m.tenant_id=c.tenant_id AND m.channel='connect:'||c.connect_channel_id::text
          AND (m.direction='incoming' OR (m.direction='system' AND m.message_type='call_missed'))
          AND m.created_at>r.removed_at
          AND COALESCE(m.happened_at,m.created_at)>=date_trunc('second',r.removed_at)
          AND m.kommo_lead_id IN (SELECT c.lead_id UNION ALL
            SELECT l.source_lead FROM saas_connect_dialog_links l
            WHERE l.tenant_id=c.tenant_id AND l.channel_id=c.connect_channel_id
              AND l.canonical_id=c.conversation_id AND l.active)))"""


def can_remove(policy, dialog):
    if (policy.profile.get('role') != 'owner' or dialog.get('provider') not in {'connect', 'operator_widget'}
            or any(dialog.get(flag) for flag in ('owner_overview_read_only', 'search_overview_read_only',
                                                 'colleague_read_only', 'case_attachment'))):
        return False
    assigned = (dialog.get('lifecycle') or {}).get('operator_id')
    if assigned is not None:
        return str(assigned) == str(policy.profile['telegram_id'])
    return not dialog.get('archive_read_only')


def remove(profile, payload):
    provider, conversation = payload.get('provider'), payload.get('conversation_id')
    raw_lead = payload.get('lead_id')
    valid_lead = type(raw_lead) is int or (isinstance(raw_lead, str) and raw_lead.isascii()
                                         and raw_lead.isdecimal() and len(raw_lead) <= 19)
    if (not isinstance(provider, str) or provider not in {'connect', 'operator_widget'}
            or not isinstance(conversation, str) or not 1 <= len(conversation) <= 250
            or conversation.strip() != conversation or not valid_lead
            or not 0 < int(raw_lead) < 2**63 or payload.get('confirmed') is not True):
        raise TenantLinearError('invalid_dialog_removal', 400)
    try:
        channel = str(UUID(str(payload.get('channel_id'))))
        request_id = str(UUID(str(payload.get('request_id'))))
    except ValueError:
        raise TenantLinearError('invalid_dialog_removal', 400) from None
    lead = int(raw_lead)
    route = {'provider':provider,'channel_id':channel,'conversation_id':conversation,'lead_id':str(lead)}
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor, policy = access(cur, profile, 'dialogs')
            if not can_remove(policy, route):
                raise TenantLinearError('dialog_removal_owner_required', 403)
            if provider == 'connect':
                from tenant_connect_dialog_equivalence import canonical
                from tenant_dialog_lifecycle import enabled, _row

                assert_route(cur, tenant, policy, provider, conversation, channel, lead)
                if canonical(cur, tenant, channel, conversation) != conversation:
                    raise TenantLinearError('dialog_removal_route_changed', 409)
                workflow = _row(cur, tenant, channel, conversation) if enabled(cur, tenant) else None
                if workflow and workflow['operator_id'] not in {None, actor}:
                    raise TenantLinearError('dialog_removal_owner_required', 403)
                inbound = None
            else:
                from tenant_operator_widget_queue import authorized

                row, _, _ = authorized(cur, profile, route, lock=True, waiting=True)
                if row['operator_id'] not in {None, actor} or (row['state'] == 'archive' and row['operator_id'] is None):
                    raise TenantLinearError('dialog_removal_owner_required', 403)
                inbound = row['latest_in']
            cur.execute("""INSERT INTO saas_tenant_audit_events
                (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload,created_at)
                VALUES(%s,%s,%s,'dialog_removed','operator_dialog',%s,%s::jsonb,clock_timestamp())
                ON CONFLICT(id) DO NOTHING RETURNING created_at""",
                (request_id, tenant, actor, str(lead), dumps(route)))
            event = cur.fetchone()
            if event:
                cur.execute("""INSERT INTO saas_operator_dialog_removals AS removal
                    (tenant_id,provider,channel_id,conversation_id,lead_id,removed_at,widget_inbound_id)
                    VALUES(%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(tenant_id,provider,channel_id,conversation_id,lead_id)
                    DO UPDATE SET removed_at=excluded.removed_at,widget_inbound_id=excluded.widget_inbound_id
                    WHERE removal.removed_at<excluded.removed_at""",
                    (tenant, provider, channel, conversation, lead, event['created_at'], inbound))
            else:
                cur.execute("""SELECT 1 FROM saas_tenant_audit_events WHERE id=%s AND tenant_id=%s
                    AND actor_telegram_id=%s AND action='dialog_removed' AND payload=%s::jsonb""",
                    (request_id, tenant, actor, dumps(route)))
                if not cur.fetchone():
                    raise TenantLinearError('dialog_removal_route_changed', 409)
        conn.commit()
    return {'removed':True, **route}
