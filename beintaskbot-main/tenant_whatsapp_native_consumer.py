"""Internal durable inbox consumer using the existing CRM ingestion transaction.

Not a browser endpoint. Activation is explicit and defaults off; capturing an
event never grants permission to project it. No provider sends occur here.
"""
import hashlib
import json
import os
import uuid

from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect
from tenant_whatsapp_native_projection import open_stored, message_event


def migrate(cur):
    cur.execute('''ALTER TABLE saas_whatsapp_capture_generations
      ADD COLUMN IF NOT EXISTS projection_enabled BOOLEAN NOT NULL DEFAULT FALSE''')
    cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS whatsapp_one_projection_generation
      ON saas_whatsapp_capture_generations(tenant_id,channel_id)
      WHERE projection_enabled''')
    from tenant_whatsapp_native_status import migrate as status_migrate
    status_migrate(cur)


def load_evidence(cur, reference, *, lock=False):
    if os.environ.get('BEINTASKBOT_UNIFIED_WHATSAPP_PROJECTION') != '1':
        raise TenantLinearError('native_projection_disabled', 409)
    tenant, source, generation, event_id = reference
    cur.execute('''SELECT i.*,g.account_jid FROM saas_whatsapp_native_inbox i
      JOIN saas_whatsapp_capture_generations g USING(tenant_id,channel_id,generation)
      JOIN saas_browser_call_sources s ON s.tenant_id=g.tenant_id AND s.channel_id=g.channel_id
        AND s.gateway_id=g.gateway_id AND s.account_jid=g.account_jid
      JOIN saas_connect_channels c ON c.tenant_id=g.tenant_id AND c.connect_channel_id=g.channel_id
      WHERE i.tenant_id=%s::uuid AND i.channel_id=%s::uuid AND i.generation=%s AND i.event_id=%s
        AND g.enabled AND g.projection_enabled AND s.enabled AND c.active'''
      + (' FOR SHARE OF i,g,s,c' if lock else ''), reference)
    row = cur.fetchone()
    if not row:
        raise TenantLinearError('native_projection_source_changed', 409)
    scope = dict(tenant=tenant, source=source, generation=generation, account=row['account_jid'])
    payload = open_stored(row, scope)
    return scope, payload


def load(cur, reference, *, lock=False):
    scope, payload = load_evidence(cur,reference,lock=lock)
    tenant, source, generation, event_id = reference
    # Stable across reconnect/generation changes, but never across source/chat/
    # direction. A proven legacy mapping replaces this ID under the dialog lock.
    identity = [tenant, source, payload.get('chat'), payload.get('provider_id'), payload.get('from_me')]
    message_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'beintaskbot:native:'+json.dumps(identity, separators=(',', ':'))))
    return tenant, message_event(scope, event_id, payload, message_id)


def consume(reference):
    reference = tuple(reference)
    if len(reference) != 4 or not all(isinstance(x, str) for x in reference):
        raise TenantLinearError('native_projection_reference_invalid', 400)
    with _connect() as conn, conn.cursor() as cur:
        scope,payload=load_evidence(cur,reference,lock=True)
        if payload.get('kind')=='receipt':
            from tenant_whatsapp_native_status import observe
            result=observe(cur,scope,payload)
            conn.commit()
            return result
        _, event = load(cur, reference)
    from tenant_connect_transport import _ingest_once
    # The shared path re-reads authenticated encrypted evidence and locks the
    # current source/generation in the SAME transaction as CRM/lifecycle/AI.
    result=_ingest_once(b'', '', event, _native_reference=reference)
    if event['data']['fromMe']:
        with _connect() as conn,conn.cursor() as cur:
            scope,payload=load_evidence(cur,reference,lock=True)
            from tenant_whatsapp_native_status import reconcile
            reconcile(cur,scope['tenant'],scope['source'],payload['provider_id'])
            conn.commit()
    return result


def project_stored(receipt):
    scope = receipt['scope']
    try:
        return consume((scope['tenant'], scope['source'], scope['generation'], receipt['event_id']))
    except TenantLinearError as error:
        if str(error) == 'native_event_not_projectable':
            # Receipts/control/history events remain in the durable encrypted
            # inbox. They must not become fake customer turns or block capture.
            return False
        raise


def authorize(cur, reference, event):
    tenant, current = load(cur, reference, lock=True)
    if current != event:
        raise TenantLinearError('native_projection_evidence_changed', 409)
    return tenant


def preserve_identity(cur, tenant, source, conversation, data):
    from tenant_whatsapp_native_identity import existing_message_id, message_id
    cur.execute('''SELECT lead_id FROM saas_connect_conversations
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s''',
      (tenant, source, conversation))
    row = cur.fetchone()
    previous = existing_message_id(cur, tenant=tenant, source=source,
        lead_id=row['lead_id'], provider_id=data['externalMessageId'], outgoing=data['fromMe']) if row else None
    return {**data, 'id': previous or message_id(tenant,source,conversation,data['externalMessageId'],data['fromMe'])}
