"""Read-only channel catalogue. Transport identity is never a channel type.

No provider requests, session changes, credential decryption or AI calls here.
Connection observations are explicitly cached, not a live WhatsApp health probe.
"""
from tenant_ai_agents_schema import ensure_schema
from tenant_ai_agents_store import _authorize
from tenant_ai_agent_access import tenant_status_allows_ai
from tenant_platform import _connect


def channel_key(provider, channel_id):
    return provider + ':' + str(channel_id)


def read_catalog(cur, tenant):
    """Internal reader; a caller must authorize the tenant in this transaction."""
    cur.execute('''SELECT t.status AS tenant_status,
      EXISTS(SELECT 1 FROM saas_tenant_integrations i WHERE i.tenant_id=t.id
        AND i.provider='connect' AND i.status='connected') AS connect_connected,
      EXISTS(SELECT 1 FROM saas_tenant_integrations i WHERE i.tenant_id=t.id
        AND i.provider='kommo' AND i.status='connected') AS kommo_connected
      FROM saas_tenants t WHERE t.id=%s::uuid''', (tenant,))
    context = cur.fetchone() or {}
    cur.execute('''SELECT connect_channel_id::text AS channel_id,name,status,active,updated_at
      FROM saas_connect_channels WHERE tenant_id=%s::uuid ORDER BY name,connect_channel_id LIMIT 501''', (tenant,))
    rows = cur.fetchall()
    result = []
    for row in rows[:500]:
        connected = bool(row['active'] and context.get('connect_connected'))
        state = row.get('status') if row.get('status') in {
            'connected', 'disconnected', 'connecting', 'pending_qr', 'reconnecting',
            'reconnect_required', 'error', 'disabled'} else 'unknown'
        if not connected:
            state = 'disconnected'
        reasons = []
        if not connected: reasons.append('integration_disconnected')
        elif state != 'connected': reasons.append('channel_connection_unconfirmed')
        if not tenant_status_allows_ai(context.get('tenant_status'), 'connect'):
            reasons.append('tenant_inactive')
        result.append({
            'id': channel_key('connect', row['channel_id']), 'channel_id': row['channel_id'],
            'provider': 'connect', 'type': 'whatsapp', 'name': row.get('name') or 'WhatsApp',
            'connection': {'state': state, 'source': 'cached', 'checked_at': str(row.get('updated_at') or '')},
            'ai': {'supported': True, 'available': not reasons, 'reason_codes': reasons,
                   'availability_scope': 'channel'},
            'capabilities': {'text_replies': True, 'incoming_voice': True, 'voice_replies': False,
                             'groups': True, 'drafts': True, 'crm_actions': False},
            'scope': {'provider': 'connect', 'channel_id': row['channel_id']},
            'requirements': ['agent_policy', 'group_opt_in', 'voice_provider_and_decoder', 'resolved_dialogue_scope'],
        })
    # Only actual Kommo receipts prove a CRM channel exists. Neither saved scope
    # strings nor Connect UUIDs are advertised as discovered Kommo connections.
    cur.execute('''SELECT channel_id,max(updated_at) AS observed_at FROM saas_ai_agent_jobs
      WHERE tenant_id=%s::uuid AND provider='kommo' AND channel_id<>''
      GROUP BY channel_id ORDER BY channel_id LIMIT 501''', (tenant,))
    kommo = cur.fetchall()
    for row in kommo[:500]:
        reasons = []
        if not context.get('kommo_connected'): reasons.append('integration_disconnected')
        if not tenant_status_allows_ai(context.get('tenant_status'), 'kommo'): reasons.append('tenant_inactive')
        result.append({
            'id': channel_key('kommo', row['channel_id']), 'channel_id': row['channel_id'],
            'provider': 'kommo', 'type': 'unknown', 'name': 'Канал CRM',
            'connection': {'state': 'unknown' if context.get('kommo_connected') else 'disconnected',
                           'source': 'cached', 'checked_at': str(row.get('observed_at') or '')},
            'ai': {'supported': True, 'available': not reasons,
                   'reason_codes': reasons, 'availability_scope': 'channel'},
            'capabilities': {'text_replies': True, 'incoming_voice': False, 'voice_replies': False,
                             'groups': False, 'drafts': True, 'crm_actions': True},
            'scope': {'provider': 'kommo', 'channel_id': row['channel_id']},
            'requirements': ['pipeline_id', 'stage_id', 'agent_policy', 'crm_action_policy', 'exact_talk_route'],
        })
    return {'schema_version': 1, 'channels': result, 'limited': len(rows) > 500 or len(kommo) > 500,
            'integrations': {p: bool(context.get(p + '_connected')) for p in ('connect', 'kommo')},
            'multiple_scopes_supported': True,
            'limitations': ['cached_connection_status', 'kommo_type_not_discovered', 'per_message_policy_checked_at_runtime']}


def catalog(profile):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = _authorize(cur, profile)
            cur.execute("SET LOCAL statement_timeout='5000ms'")
            return read_catalog(cur, tenant)
