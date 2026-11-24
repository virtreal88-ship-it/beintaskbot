"""Personal composer suggestions from confirmed WhatsApp sends."""

import re
import uuid

from tenant_dialog_lifecycle import visibility_sql
from tenant_dialog_access import require_source
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access, assert_route
from tenant_platform_core import _connect


def _suggestions(cur, tenant, actor, channel, policy, prefix, key=None):
    visible = visibility_sql(cur, policy, "'connect'", 'c.connect_channel_id::text',
                             'c.conversation_id', allow_closed_archive=True)
    # A positive send receipt binds the actor even if a provider echo wrote the
    # history row first. A display name alone is never proof of authorship.
    cur.execute('''WITH sent AS (
      SELECT DISTINCT m.id, btrim(m.body) AS body, m.happened_at
      FROM saas_chat_send_receipts r
      JOIN saas_crm_messages m ON m.tenant_id=r.tenant_id AND m.external_id='connect:'||r.message_id
        AND m.kommo_lead_id=r.lead_id
      JOIN saas_connect_conversations c ON c.tenant_id=m.tenant_id AND c.lead_id=m.kommo_lead_id
        AND c.connect_channel_id=%s::uuid
      WHERE r.tenant_id=%s::uuid AND r.actor_id=%s AND r.state='accepted' AND r.route->>'origin'='connect'
        AND r.updated_at>=now()-interval '3 months'
        AND m.channel='connect:'||c.connect_channel_id::text AND m.direction='outgoing' AND m.message_type='text'
        AND m.happened_at>=now()-interval '3 months'
        AND NOT (m.raw ? 'run_id') AND m.author_name IS DISTINCT FROM 'AI Agent'
        AND COALESCE(m.raw->>'status','') NOT IN ('failed','deleted','revoked')
        AND starts_with(lower(normalize(btrim(m.body))), lower(normalize(%s::text))) AND ''' + visible + '''
    ), keyed AS (
      SELECT body, happened_at, encode(sha256(convert_to(body,'UTF8')),'hex') AS reply_key FROM sent
    )
    SELECT s.reply_key AS id, s.body, count(*) AS uses, max(s.happened_at) AS latest
    FROM keyed s LEFT JOIN saas_dialog_quick_reply_dismissals d ON d.tenant_id=%s::uuid AND d.actor_id=%s
      AND d.channel_id=%s::uuid AND d.reply_key=s.reply_key
    WHERE (d.dismissed_through IS NULL OR s.happened_at>d.dismissed_through)
      AND (%s::text IS NULL OR s.reply_key=%s)
    GROUP BY s.reply_key,s.body ORDER BY uses DESC,latest DESC,s.reply_key LIMIT 5''',
        (channel, tenant, actor, prefix, tenant, actor, channel, key, key))
    return cur.fetchall()


def quick_replies(profile, payload):
    action = payload.get('action')
    prefix = payload.get('prefix')
    conversation = payload.get('conversation_id')
    key = payload.get('reply_id', '')
    if (action not in ('search', 'dismiss') or not isinstance(prefix, str) or not 2 <= len(prefix.strip()) <= 180
            or '\x00' in prefix or not isinstance(conversation, str) or not 0 < len(conversation) <= 180
            or '\x00' in conversation or (action == 'dismiss' and not re.fullmatch(r'[a-f0-9]{64}', str(key)))):
        raise TenantLinearError('invalid_quick_reply', 400)
    try:
        channel = str(uuid.UUID(str(payload.get('channel_id'))))
        lead_id = int(str(payload.get('lead_id')))
        if not 0 < lead_id < 2**63:
            raise ValueError
    except (ValueError, TypeError):
        raise TenantLinearError('invalid_quick_reply', 400) from None

    prefix = prefix.lstrip()
    with _connect() as conn, conn.cursor() as cur:
        tenant, actor, policy = access(cur, profile, 'dialogs')
        assert_route(cur, tenant, policy, 'connect', conversation, channel, lead_id, action='update')
        require_source(cur, tenant, policy, 'connect', channel, 'view')
        cur.execute("SELECT to_regclass('saas_chat_send_receipts') IS NOT NULL AS ready")
        if not cur.fetchone()['ready']:
            return {'items': []}
        if action == 'dismiss':
            picked = _suggestions(cur, tenant, actor, channel, policy, prefix, key)
            if picked:
                cur.execute('''INSERT INTO saas_dialog_quick_reply_dismissals
                  (tenant_id,actor_id,channel_id,reply_key,dismissed_through) VALUES(%s,%s,%s,%s,%s)
                  ON CONFLICT (tenant_id,actor_id,channel_id,reply_key) DO UPDATE SET dismissed_through=
                    greatest(saas_dialog_quick_reply_dismissals.dismissed_through,EXCLUDED.dismissed_through)''',
                    (tenant, actor, channel, key, picked[0]['latest']))
                cur.execute('''DELETE FROM saas_dialog_quick_reply_dismissals WHERE tenant_id=%s AND actor_id=%s
                  AND channel_id=%s AND dismissed_through<now()-interval '3 months' ''', (tenant, actor, channel))
        rows = _suggestions(cur, tenant, actor, channel, policy, prefix)
    return {'items': [{key: row[key] for key in ('id', 'body', 'uses')} for row in rows]}
