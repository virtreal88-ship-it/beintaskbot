"""Present two existing WhatsApp histories together under both route grants."""

import asyncio
import uuid

from tenant_connect_direct_identity import phone_jid
from tenant_linear_policy import TenantLinearError


def migrate(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_whatsapp_history_pairs (
      id UUID NOT NULL UNIQUE,
      tenant_id UUID NOT NULL, channel_id UUID NOT NULL, generation TEXT NOT NULL,
      primary_id TEXT NOT NULL CHECK(primary_id ~ '^[1-9][0-9]{5,21}@(s[.]whatsapp[.]net|c[.]us)$'),
      alternate_id TEXT NOT NULL CHECK(alternate_id ~ '^[1-9][0-9]{5,21}@lid$'),
      verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,channel_id,primary_id),
      UNIQUE(tenant_id,channel_id,alternate_id),
      FOREIGN KEY(tenant_id,channel_id,primary_id)
        REFERENCES saas_connect_conversations(tenant_id,connect_channel_id,conversation_id) ON DELETE CASCADE,
      FOREIGN KEY(tenant_id,channel_id,alternate_id)
        REFERENCES saas_connect_conversations(tenant_id,connect_channel_id,conversation_id) ON DELETE CASCADE)""")


def available_pairs(cur, tenant, channel=None, primary=None, *, lock=False):
    """Check live source generation and retire overlays when a route diverges."""
    cur.execute("""SELECT to_regclass('saas_whatsapp_native_routes') IS NOT NULL
      AND to_regclass('saas_whatsapp_history_pairs') IS NOT NULL
      AND to_regclass('saas_browser_call_sources') IS NOT NULL
      AND to_regclass('saas_dialog_workflows') IS NOT NULL AS ready""")
    if not cur.fetchone()['ready']:
        return []
    # Older deliveries sometimes flagged a known route without including either
    # alternate ID. A self-only receipt is covered only when matching signed
    # receipts predate and follow it, and WeCall has since confirmed the pair.
    cur.execute("""SELECT p.id::text,p.channel_id::text AS channel_id,p.primary_id,p.alternate_id,
             main.lead_id AS primary_lead,other.lead_id AS alternate_lead
      FROM saas_whatsapp_history_pairs p
      JOIN saas_whatsapp_native_routes r
        ON r.tenant_id=p.tenant_id AND r.channel_id=p.channel_id AND r.generation=p.generation
        AND r.enabled AND NOT r.paused
      JOIN saas_whatsapp_capture_generations g
        ON g.tenant_id=r.tenant_id AND g.channel_id=r.channel_id AND g.generation=r.generation AND g.enabled
      JOIN saas_browser_call_sources s ON s.tenant_id=g.tenant_id AND s.channel_id=g.channel_id
        AND s.gateway_id=g.gateway_id AND s.account_jid=g.account_jid AND s.enabled
      JOIN saas_connect_channels c ON c.tenant_id=p.tenant_id AND c.connect_channel_id=p.channel_id AND c.active
      JOIN saas_connect_conversations main ON main.tenant_id=p.tenant_id AND main.connect_channel_id=p.channel_id
        AND main.conversation_id=p.primary_id AND main.conversation_kind='contact'
      JOIN saas_connect_conversations other ON other.tenant_id=p.tenant_id AND other.connect_channel_id=p.channel_id
        AND other.conversation_id=p.alternate_id AND other.conversation_kind='contact'
      WHERE p.tenant_id=%s::uuid AND (%s::uuid IS NULL OR p.channel_id=%s::uuid)
        AND (%s::text IS NULL OR p.primary_id=%s)
        AND NOT EXISTS (SELECT 1 FROM saas_connect_dialog_links l
          WHERE l.tenant_id=p.tenant_id AND l.channel_id=p.channel_id AND l.active
            AND (l.source_id IN (p.primary_id,p.alternate_id) OR l.canonical_id IN (p.primary_id,p.alternate_id)))
        AND NOT EXISTS (SELECT 1 FROM saas_dialog_workflows w
          WHERE w.tenant_id=p.tenant_id AND w.channel_id=p.channel_id AND w.conversation_id=p.alternate_id)
        AND NOT EXISTS (SELECT 1 FROM saas_crm_messages m
          WHERE m.tenant_id=p.tenant_id AND m.channel='connect:'||p.channel_id::text
            AND m.kommo_lead_id=other.lead_id AND m.direction<>'outgoing')
        AND NOT EXISTS (SELECT 1 FROM saas_connect_identity_conflict_events conflict
          WHERE conflict.tenant_id=p.tenant_id AND conflict.connect_channel_id=p.channel_id
            AND conflict.conversation_id IN (p.primary_id,p.alternate_id)
            AND (conflict.evidence->>'pair_matches_conversation' IS DISTINCT FROM 'true'
              OR conflict.evidence->'directIdentity'->>'lid' IS DISTINCT FROM p.alternate_id
              OR COALESCE(conflict.evidence->'directIdentity'->>'jid','') NOT IN (
                split_part(p.primary_id,'@',1)||'@s.whatsapp.net',split_part(p.primary_id,'@',1)||'@c.us'))
            AND NOT (
              conflict.evidence->>'pair_matches_conversation' IS NOT DISTINCT FROM 'false'
              AND conflict.evidence->'directIdentity'->>'jid' IS NOT DISTINCT FROM ''
              AND conflict.evidence->'directIdentity'->>'lid' IS NOT DISTINCT FROM ''
              AND conflict.evidence->>'originalConversationId' IS NOT DISTINCT FROM conflict.conversation_id
              AND conflict.evidence->>'canonicalConversationId' IS NOT DISTINCT FROM conflict.conversation_id
              AND conflict.created_at<p.verified_at
              AND EXISTS (SELECT 1 FROM saas_connect_identity_conflict_events proof
                WHERE proof.tenant_id=conflict.tenant_id AND proof.connect_channel_id=conflict.connect_channel_id
                  AND proof.conversation_id=conflict.conversation_id
                  AND proof.evidence->>'pair_matches_conversation'='true'
                  AND proof.evidence->'directIdentity'->>'lid'=p.alternate_id
                  AND proof.evidence->'directIdentity'->>'jid' IN (
                    split_part(p.primary_id,'@',1)||'@s.whatsapp.net',split_part(p.primary_id,'@',1)||'@c.us')
                  AND proof.evidence->>'originalConversationId' IN (p.primary_id,p.alternate_id)
                  AND proof.evidence->>'canonicalConversationId' IN (p.primary_id,p.alternate_id)
                  AND proof.created_at<p.verified_at
                HAVING min(proof.created_at)<conflict.created_at AND max(proof.created_at)>conflict.created_at)))
      """ + (' FOR SHARE OF p,r,g,s,c' if lock else ''), (tenant, channel, channel, primary, primary))
    return cur.fetchall()


def history_pair(cur, tenant, policy, channel, conversation, lead_id):
    from tenant_operator_access import assert_route

    if not phone_jid(conversation):
        return None
    candidates = available_pairs(cur,tenant,channel,conversation)
    if not candidates or candidates[0]['primary_lead'] != lead_id:
        return None
    pair = candidates[0]
    try:
        assert_route(cur,tenant,policy,'connect',pair['alternate_id'],channel,pair['alternate_lead'])
    except TenantLinearError as exc:
        if exc.status in {403,404,409}:
            return None
        raise
    return pair if available_pairs(cur,tenant,channel,conversation,lock=True)==candidates else None


def fold_cards(rows, pairs):
    # We only fold routes already returned by the caller's ordinary visibility
    # projection. The primary route supplies status, ownership and all actions.
    def key(row):
        return row['channel_id'], row['conversation_id'], int(row['lead_id'])

    visible = {key(row) for row in rows if row['provider']=='connect'}
    removed = set()
    for pair in pairs:
        channel = pair['channel_id']
        primary = channel, pair['primary_id'], pair['primary_lead']
        alternate = channel, pair['alternate_id'], pair['alternate_lead']
        if primary in visible and alternate in visible:
            removed.add(alternate)
    return [row for row in rows if row['provider']!='connect' or key(row) not in removed]


def confirm(profile, channel, primary_lead, alternate_lead):
    """Record fresh proof from the currently paired device for two exact routes."""
    from tenant_contact_phone import fetch_contact
    from tenant_dialog_access import require_source
    from tenant_operator_access import access
    from tenant_platform import _connect
    from tenant_whatsapp_native_transport import bind

    channel = str(uuid.UUID(channel))

    def snapshot(cur):
        tenant, _, policy = access(cur, profile, action='update')
        if policy.profile.get('role') != 'owner':
            raise TenantLinearError('Подтверждать связь истории может владелец.',403)
        require_source(cur,tenant,policy,'connect',channel,'update')
        binding = bind(cur, {'tenant_id':tenant,'channel_id':channel,'api_key':''}).get('native')
        cur.execute("""SELECT lead_id,conversation_id FROM saas_connect_conversations
          WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND lead_id IN (%s,%s)
            AND conversation_kind='contact' FOR SHARE""",(tenant,channel,primary_lead,alternate_lead))
        routes = {row['lead_id']:row['conversation_id'] for row in cur.fetchall()}
        primary, alternate = routes.get(primary_lead,''), routes.get(alternate_lead,'')
        if not binding or not phone_jid(primary) or not alternate.endswith('@lid'):
            raise TenantLinearError('Эта пара диалогов недоступна.',409)
        return tenant, binding, primary, alternate

    with _connect() as conn, conn.cursor() as cur:
        prepared = snapshot(cur)
    tenant, binding, primary, alternate = prepared
    fact = asyncio.run(fetch_contact({'conversation_id':alternate,'native':binding}))
    if fact.get('phone') != '+' + phone_jid(primary).split('@')[0]:
        raise TenantLinearError('WeCall не подтвердил эту пару диалогов.',409)
    with _connect() as conn, conn.cursor() as cur:
        if snapshot(cur) != prepared:
            raise TenantLinearError('Источник изменился. Подтвердите пару заново.',409)
        cur.execute("""INSERT INTO saas_whatsapp_history_pairs AS existing
          (id,tenant_id,channel_id,generation,primary_id,alternate_id)
          VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s)
          ON CONFLICT (tenant_id,channel_id,primary_id) DO UPDATE SET
            id=CASE WHEN existing.generation=excluded.generation THEN existing.id ELSE excluded.id END,
            generation=excluded.generation,verified_at=now()
          WHERE existing.alternate_id=excluded.alternate_id RETURNING id""",
          (str(uuid.uuid4()),tenant,channel,binding['scope']['generation'],primary,alternate))
        if not cur.fetchone() or not available_pairs(cur,tenant,channel,primary):
            raise TenantLinearError('Обе части истории уже используются отдельно.',409)
