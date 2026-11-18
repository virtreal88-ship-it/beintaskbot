"""Exact legacy message identity lookup inside a guarded CRM transaction.

The caller must resolve the existing conversation and hold its normal dialogue
lock and source-generation guard. This read grants no access or source ownership.
An absent mapping is not permission to send, create a route or replay history.
"""
import re
import uuid


class NativeIdentityConflict(ValueError):
    pass


def existing_message_id(cur, *, tenant, source, lead_id, provider_id, outgoing):
    try:
        tenant, source = str(uuid.UUID(tenant)), str(uuid.UUID(source))
    except (ValueError, TypeError, AttributeError):
        raise NativeIdentityConflict('native_identity_scope_invalid') from None
    if (type(lead_id) is not int or type(outgoing) is not bool
            or not isinstance(provider_id, str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{5,200}', provider_id)):
        raise NativeIdentityConflict('native_identity_scope_invalid')
    cur.execute('''SELECT external_id FROM saas_crm_messages
      WHERE tenant_id=%s::uuid AND channel=%s AND kommo_lead_id=%s
        AND direction=%s AND raw->>'externalMessageId'=%s
      LIMIT 2''', (tenant, 'connect:'+source, lead_id,
                   'outgoing' if outgoing else 'incoming', provider_id))
    rows = cur.fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise NativeIdentityConflict('native_identity_ambiguous')
    external = rows[0]['external_id']
    if (not isinstance(external, str) or not external.startswith('connect:')
            or not re.fullmatch(r'[A-Za-z0-9_-]{5,200}', external[8:])):
        raise NativeIdentityConflict('native_identity_legacy_invalid')
    return external[8:]
