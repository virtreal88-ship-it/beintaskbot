"""Refresh missing, verified phone aliases for one explicitly enabled WeCall source."""
import asyncio
import contextlib
import json
import os
import re
import uuid

from tenant_connect_direct_identity import canonical_conversation, lock_identities
from tenant_contact_phone import contact_source_revision, fetch_contact
from tenant_platform import TenantPlatformError, _connect
from tenant_whatsapp_native_transport import bind

BATCH_SIZE = 24
INTERVAL_SECONDS = 60
AUDIT_ACTION = 'whatsapp_device_phone_confirmed'


def migrate(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_whatsapp_device_display_phones (
        tenant_id UUID NOT NULL, channel_id UUID NOT NULL, conversation_id TEXT NOT NULL,
        generation TEXT NOT NULL, gateway_id UUID NOT NULL, account_jid TEXT NOT NULL,
        phone TEXT NOT NULL CHECK(phone ~ '^[+][1-9][0-9]{6,14}$'),
        verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,channel_id,conversation_id),
        FOREIGN KEY(tenant_id,channel_id,conversation_id)
          REFERENCES saas_connect_conversations(tenant_id,connect_channel_id,conversation_id) ON DELETE CASCADE)""")


def enabled_source():
    value = os.environ.get('BEINTASKBOT_NATIVE_CONTACT_SOURCE', '')
    try:
        tenant, channel = value.split(':')
        return str(uuid.UUID(tenant)), str(uuid.UUID(channel))
    except ValueError:
        return None


def enabled_for(tenant, channel):
    return enabled_source() == (str(tenant), str(channel))


def approved_conflict_leads(tenant, channel):
    """An explicit presentation approval for two lead IDs on one source."""
    value = os.environ.get('BEINTASKBOT_NATIVE_CONFLICT_DISPLAY', '')
    try:
        company, source, lid_lead, phone_lead = value.split(':')
        if (str(uuid.UUID(company)), str(uuid.UUID(source))) != (str(tenant), str(channel)):
            return None
        lid_lead, phone_lead = int(lid_lead), int(phone_lead)
        if lid_lead != phone_lead and all(0 < lead < 2**63 for lead in (lid_lead, phone_lead)):
            return lid_lead, phone_lead
    except ValueError:
        return None


def approved_conflict_evidence(cur, tenant, channel, lid, phone, leads):
    """A routing conflict may contain one exact pair, or competing evidence."""
    pn = phone[1:] + '@s.whatsapp.net'
    legacy = phone[1:] + '@c.us'
    keys = [lid, pn, legacy]
    cur.execute("""WITH observations AS (
        SELECT evidence FROM saas_connect_identity_conflicts
        WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=ANY(%s)
        UNION ALL
        SELECT evidence FROM saas_connect_identity_conflict_events
        WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=ANY(%s)
      ), facts AS (
        SELECT evidence->'directIdentity'->>'lid'=%s
                 AND evidence->'directIdentity'->>'jid' IN (%s,%s)
                 AND evidence->>'pair_matches_conversation'='true' AS exact_pair,
               evidence->'directIdentity'->>'lid'=''
                 AND evidence->'directIdentity'->>'jid'=''
                 AND evidence->>'pair_matches_conversation'='false' AS no_pair,
               evidence->>'originalConversationId'=ANY(%s)
                 AND evidence->>'canonicalConversationId'=ANY(%s) AS same_routes
        FROM observations
      )
      SELECT EXISTS(SELECT 1 FROM facts WHERE same_routes AND exact_pair)
        AND NOT EXISTS(SELECT 1 FROM facts WHERE (same_routes AND (exact_pair OR no_pair)) IS NOT TRUE)
        AND (SELECT count(*) FROM saas_connect_conversations
             WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid
               AND conversation_kind IS DISTINCT FROM 'group'
               AND ((lead_id=%s AND conversation_id=%s)
                 OR (lead_id=%s AND conversation_id IN (%s,%s))))=2 AS approved""", (
        tenant, channel, keys, tenant, channel, keys, lid, pn, legacy, keys, keys,
        tenant, channel, leads[0], lid, leads[1], pn, legacy))
    return bool(cur.fetchone()['approved'])


def _binding(cur, tenant, channel):
    try:
        binding = bind(cur, {'tenant_id': tenant, 'channel_id': channel, 'api_key': ''}).get('native')
    except TenantPlatformError:
        return None
    if not binding:
        return None
    cur.execute("""SELECT 1 FROM saas_whatsapp_native_routes r
        JOIN saas_whatsapp_capture_generations g USING (tenant_id,channel_id,generation)
        WHERE r.tenant_id=%s::uuid AND r.channel_id=%s::uuid AND NOT r.paused
          AND g.projection_enabled""", (tenant, channel))
    return binding if cur.fetchone() else None


def candidates(tenant, channel, after):
    if not enabled_for(tenant, channel):
        return None, []
    with _connect() as conn, conn.cursor() as cur:
        from tenant_connect_transport import _schema
        _schema(conn)
        binding = _binding(cur, tenant, channel)
        if not binding:
            return None, []
        approved = approved_conflict_leads(tenant, channel)
        cur.execute("""SELECT c.lead_id,c.conversation_id FROM saas_connect_conversations c
          WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid AND c.lead_id>%s
            AND c.conversation_kind IS DISTINCT FROM 'group'
            AND c.conversation_id ~ '^[1-9][0-9]{5,21}@lid$' AND COALESCE(c.phone,'')=''
            AND (c.lead_id=%s OR NOT EXISTS (SELECT 1 FROM saas_connect_identity_conflicts f
              WHERE (f.tenant_id,f.connect_channel_id,f.conversation_id)=
                    (c.tenant_id,c.connect_channel_id,c.conversation_id)))
            AND NOT EXISTS (SELECT 1 FROM saas_connect_direct_identities i
              WHERE (i.tenant_id,i.connect_channel_id,i.canonical_conversation_id)=
                    (c.tenant_id,c.connect_channel_id,c.conversation_id)
                AND (i.identity_key LIKE '%%@s.whatsapp.net' OR i.identity_key LIKE '%%@c.us'))
            AND NOT EXISTS (SELECT 1 FROM saas_whatsapp_device_display_phones d
              WHERE (d.tenant_id,d.channel_id,d.conversation_id)=
                    (c.tenant_id,c.connect_channel_id,c.conversation_id)
                AND (d.generation,d.gateway_id,d.account_jid)=(%s,%s::uuid,%s))
          ORDER BY c.lead_id LIMIT %s""", (
            tenant, channel, after, approved[0] if approved else None,
            binding['scope']['generation'], binding['gateway_id'],
            binding['scope']['account'], BATCH_SIZE))
        return binding, cur.fetchall()


def save_confirmation(tenant, channel, binding, candidate, phone):
    """Reserve free aliases or save a presentation-only phone for separate routes."""
    if not enabled_for(tenant, channel) or not re.fullmatch(r'\+[1-9][0-9]{6,14}', phone):
        return False
    lid = candidate['conversation_id']
    if not re.fullmatch(r'[1-9][0-9]{5,21}@lid', lid):
        return False
    pn = phone[1:] + '@s.whatsapp.net'
    if pn == binding['scope']['account']:
        return False
    keys = [lid, pn, phone[1:] + '@c.us']
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout='2s'")
        if _binding(cur, tenant, channel) != binding:
            return False
        lock_identities(cur, tenant, channel, keys)
        cur.execute("""SELECT lead_id,conversation_id,phone,conversation_kind
          FROM saas_connect_conversations
          WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=ANY(%s)
          FOR UPDATE""", (tenant, channel, keys))
        routes = {row['conversation_id']: row for row in cur.fetchall()}
        target = routes.get(lid)
        phone_routes = set(routes) - {lid}
        if (not target or target['lead_id'] != candidate['lead_id'] or target['phone']
                or target['conversation_kind'] == 'group' or len(phone_routes) > 1):
            return False
        phone_route = next(iter(phone_routes), '')
        cur.execute("""SELECT identity_key,canonical_conversation_id FROM saas_connect_direct_identities
          WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid
            AND (identity_key=ANY(%s) OR canonical_conversation_id=%s)""", (tenant, channel, keys, lid))
        aliases = cur.fetchall()
        if any(row['canonical_conversation_id'] != (lid if row['identity_key'] == lid else phone_route)
               for row in aliases):
            return False
        cur.execute("""SELECT 'conflict' AS reason FROM saas_connect_identity_conflicts
          WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=ANY(%s)
          UNION ALL SELECT 'link' AS reason FROM saas_connect_dialog_links
          WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND active
            AND (source_id=ANY(%s) OR canonical_id=ANY(%s))""",
            (tenant, channel, keys, tenant, channel, keys, keys))
        reasons = {row['reason'] for row in cur.fetchall()}
        approved = approved_conflict_leads(tenant, channel)
        if 'link' in reasons:
            return False
        if 'conflict' in reasons and (
            not phone_route or approved != (target['lead_id'], routes[phone_route]['lead_id'])
            or not approved_conflict_evidence(cur, tenant, channel, lid, phone, approved)
        ):
            return False
        if phone_route:
            # The phone route may already be associated with a different LID in CRM.
            cur.execute("""SELECT 1 FROM saas_connect_direct_identities
              WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid
                AND canonical_conversation_id=%s AND identity_key LIKE '%%@lid' LIMIT 1""",
                (tenant, channel, phone_route))
            if cur.fetchone():
                return False
            cur.execute("""INSERT INTO saas_whatsapp_device_display_phones AS current
                (tenant_id,channel_id,conversation_id,generation,gateway_id,account_jid,phone)
                VALUES(%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT(tenant_id,channel_id,conversation_id) DO UPDATE
                SET generation=excluded.generation,gateway_id=excluded.gateway_id,
                    account_jid=excluded.account_jid,verified_at=now()
                WHERE current.phone=excluded.phone AND (current.generation,current.gateway_id,current.account_jid)
                    IS DISTINCT FROM (excluded.generation,excluded.gateway_id,excluded.account_jid)
              RETURNING 1""", (tenant, channel, lid, binding['scope']['generation'], binding['gateway_id'],
                                binding['scope']['account'], phone))
            if not cur.fetchone():
                return False
        else:
            canonical, conflict = canonical_conversation(
                cur, tenant, channel, lid, {'directIdentity': {'jid': pn, 'lid': lid}}, {'kind': 'contact'})
            if conflict or canonical != lid:
                conn.rollback()
                return False
        revision = contact_source_revision(binding['gateway_id'], binding['scope']['account'],
                                           binding['scope']['generation'])
        cur.execute("""INSERT INTO saas_tenant_audit_events(id,tenant_id,action,entity_type,entity_id,payload)
          VALUES(%s,%s,%s,'conversation',%s,%s::jsonb)""", (
            uuid.uuid4(), tenant, AUDIT_ACTION, str(candidate['lead_id']),
            json.dumps({'source': channel, 'generation': binding['scope']['generation'],
                        'source_revision': revision, 'display_only': bool(phone_route),
                        'conflict_display_approved': 'conflict' in reasons})))
        return True


def attach_display_phones(cur, tenant, dialogs):
    source = enabled_source()
    if not source or source[0] != str(tenant):
        return
    approved = approved_conflict_leads(*source)
    rows = {row['conversation_id']: row for row in dialogs
            if str(row['channel_id']) == source[1] and row['conversation_id'].endswith('@lid')
            and not row.get('phone') and (not row.get('contact_identity_conflict')
                or approved and row.get('lead_id') == approved[0])}
    if not rows:
        return
    approved_lid = next((lid for lid, row in rows.items() if approved and row.get('lead_id') == approved[0]), '')
    cur.execute("""SELECT d.conversation_id,d.phone
        FROM saas_whatsapp_device_display_phones d
        JOIN saas_whatsapp_native_routes r USING(tenant_id,channel_id,generation)
        JOIN saas_whatsapp_capture_generations g USING(tenant_id,channel_id,generation)
        JOIN saas_browser_call_sources s ON s.tenant_id=g.tenant_id AND s.channel_id=g.channel_id
          AND s.gateway_id=g.gateway_id AND s.account_jid=g.account_jid
        JOIN saas_connect_channels c ON c.tenant_id=g.tenant_id AND c.connect_channel_id=g.channel_id
        WHERE d.tenant_id=%s::uuid AND d.channel_id=%s::uuid AND d.conversation_id=ANY(%s)
          AND (d.gateway_id,d.account_jid)=(g.gateway_id,g.account_jid)
          AND r.enabled AND NOT r.paused AND g.enabled AND g.projection_enabled AND s.enabled AND c.active
          AND (d.conversation_id=%s OR NOT EXISTS(SELECT 1 FROM saas_connect_identity_conflicts f
            WHERE f.tenant_id=d.tenant_id AND f.connect_channel_id=d.channel_id
              AND f.conversation_id IN (d.conversation_id,substring(d.phone from 2)||'@s.whatsapp.net',
                                        substring(d.phone from 2)||'@c.us')))""", (*source, list(rows), approved_lid))
    for fact in cur.fetchall():
        lid, phone = fact['conversation_id'], fact['phone']
        if lid == approved_lid and not approved_conflict_evidence(cur, *source, lid, phone, approved):
            continue
        rows[lid]['device_display_phone'] = phone


async def refresh_batch(tenant, channel, after=0):
    binding, rows = await asyncio.to_thread(candidates, tenant, channel, after)
    slots = asyncio.Semaphore(2)

    async def confirm(candidate):
        async with slots:
            facts = await fetch_contact({**candidate, 'native': binding})
            return await asyncio.to_thread(save_confirmation, tenant, channel, binding,
                                           candidate, facts['phone']) if facts.get('phone') else False

    results = await asyncio.gather(*(confirm(candidate) for candidate in rows), return_exceptions=True)
    failures = sum(isinstance(result, Exception) for result in results)
    cursor = rows[-1]['lead_id'] if len(rows) == BATCH_SIZE else 0
    return cursor, len(rows), sum(result is True for result in results), failures


def install(app, logger):
    scope = enabled_source()
    if not scope:
        return

    async def run():
        after = 0
        while True:
            try:
                after, checked, confirmed, failures = await refresh_batch(*scope, after)
                if confirmed or failures:
                    logger.info('whatsapp_contact_refresh checked=%d confirmed=%d failures=%d',
                                checked, confirmed, failures)
            except Exception:
                logger.warning('whatsapp_contact_refresh_unavailable')
            await asyncio.sleep(INTERVAL_SECONDS)

    async def context(_):
        task = asyncio.create_task(run(), name='whatsapp-contact-refresh')
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app.cleanup_ctx.append(context)
