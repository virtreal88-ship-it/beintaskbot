"""Channel identity and transaction serialization for Connect dialogues."""
import uuid
from tenant_linear_policy import TenantLinearError


def channel_scope(provider, channel=''):
    if provider != 'connect':
        return ''
    try:
        return str(uuid.UUID(str(channel)))
    except (ValueError, TypeError, AttributeError):
        raise TenantLinearError('Connect channel is required.', 400) from None


def job_scope(job):
    return channel_scope(job.get('provider'), job.get('channel_id'))


def lock_dialog(cur, tenant, provider, channel, conversation):
    scope = channel_scope(provider, channel)
    # JSON avoids delimiter collisions. Lock includes tenant/provider/channel/JID.
    import json
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                ('ai-dialog:' + json.dumps([str(tenant), provider, scope, conversation]),))
    return scope


def assert_resolved(cur, tenant, channel, conversation):
    channel_scope('connect', channel)
    cur.execute("SELECT 1 FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id='' AND conversation_id=%s", (tenant, conversation))
    if cur.fetchone():
        raise TenantLinearError('Legacy dialogue channel is ambiguous; manual resolution is required.', 409)
