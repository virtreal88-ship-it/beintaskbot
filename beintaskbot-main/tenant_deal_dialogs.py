"""Resolve saved deal routes using the same access checks as operator history."""

from tenant_crm_native_access import CRMAccessError
from tenant_linear_policy import TenantLinearError, identifier
from tenant_operator_access import access, assert_route
from tenant_operator_dialogs import ensure_schema, projection, runtime_flag
from tenant_platform import _connect


def linked_dialogs(profile, source, deal_id):
    from tenant_deal_dialog_links import enabled as deal_links_enabled, routes
    tenant = identifier(profile.get('tenant_id'))
    binding = None
    if source == 'native':
        from tenant_crm_native_deals import get_deal

        try:
            deal = get_deal(tenant, identifier(deal_id), actor_id=profile['telegram_id'])
        except TenantLinearError:
            raise TenantLinearError('Сделка не найдена.', 404) from None
        except CRMAccessError as error:
            raise TenantLinearError('Сделка недоступна.', error.status) from None
        if not deal:
            raise TenantLinearError('Сделка не найдена.', 404)
        channel, conversation = deal.get('channel_id'), deal.get('conversation_id')
        if not (channel and conversation) and not deal_links_enabled(tenant):
            return {'dialogs': []}
        predicate = "provider='connect' AND channel_id=%s AND conversation_id=%s"
        params = (str(channel), conversation)
    elif source == 'kommo' and isinstance(deal_id, str) and deal_id.isascii() and deal_id.isdecimal() and len(deal_id) < 20 and 0 < int(deal_id) < 2**63:
        # Connect and Kommo allocate IDs independently. A matching numeric ID
        # across providers is insufficient to authorize a conversation link.
        predicate = "provider='kommo' AND lead_id=%s AND (last_message_at IS NOT NULL OR conversation_id<>'')"
        params = (int(deal_id),)
    else:
        raise TenantLinearError('Сделка не найдена.', 404)

    from tenant_connect_transport import _schema as ensure_connect_schema

    with _connect() as conn:
        ensure_schema(conn)
        ensure_connect_schema(conn)
        with conn.cursor() as cur:
            tenant, _, policy = access(cur, profile, 'dialogs')
            if source == 'kommo':
                cur.execute("""
                    SELECT d.* FROM saas_crm_deals d
                    WHERE d.tenant_id=%s::uuid AND d.kommo_lead_id=%s
                      AND d.deleted_at IS NULL
                    """, (tenant, params[0]))
                deal = cur.fetchone()
                if not policy.kommo_access() or not policy.can_access_kommo_deal(deal):
                    raise TenantLinearError('Сделка недоступна.', 404)
                from tenant_kommo_board_provenance import revisions
                from tenant_crm_relation_identity import current_binding
                binding = current_binding(cur, tenant)
                if not binding or not revisions(cur, tenant, 'leads', [params[0]], binding=binding,
                                                expected_raw={str(params[0]): deal['raw']}):
                    return {'dialogs': []}

            owner_overview = policy.profile.get('role') == 'owner'
            sql, scopes = projection(cur, policy, allow_closed_archive=True, owner_overview=owner_overview)
            cur.execute(sql + f"""
                SELECT provider, lead_id, channel_id, conversation_id, customer_name, channel_name
                FROM visible WHERE {predicate}
                ORDER BY channel_name, channel_id, conversation_id
                """, (tenant, tenant, *scopes, runtime_flag('AI_AGENTS_ENABLED'), *params))
            candidates = cur.fetchall()
            dialogs = []
            for row in candidates:
                try:
                    assert_route(cur, tenant, policy, row['provider'], row['conversation_id'],
                                 row['channel_id'], row['lead_id'], allow_closed_archive=True,
                                 owner_overview=owner_overview)
                except TenantLinearError as error:
                    if error.status not in (403, 404):
                        raise
                    continue
                dialogs.append(dict(row))
            attached = routes(cur, tenant, policy, source, deal_id, binding)
            attached_keys = {(row['provider'],row['channel_id'],row['conversation_id']) for row in attached}
            return {'dialogs': [row for row in dialogs
                                if (row['provider'],row['channel_id'],row['conversation_id']) not in attached_keys]+attached}
