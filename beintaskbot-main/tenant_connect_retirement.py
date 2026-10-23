"""Owner-approved retirement of the one obsolete pilot channel, retaining history."""
import json
from tenant_platform import _connect
from tenant_ai_agents_store import _authorize, _audit
from tenant_linear_policy import TenantLinearError
import tenant_connect_transport as transport

TARGET_TENANT='e09a5c24-093f-4141-992d-532052e00c90'
TARGET_CHANNEL='4993eadb-6794-4ffa-90b4-f7062331807a'
TARGET_PHONE='994102135103'


def retire(profile,channel_id,*,confirmed=False):
    if (not isinstance(profile,dict) or str(profile.get('tenant_id'))!=TARGET_TENANT
            or str(channel_id)!=TARGET_CHANNEL or confirmed is not True):
        raise TenantLinearError('channel_retirement_not_authorized',403)
    tenant=TARGET_TENANT
    with transport._tenant_connect_lock(tenant):
        with _connect() as conn:
            with conn.cursor() as cur:
                _,actor=_authorize(cur,profile)
                cur.execute('SELECT role FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s',(tenant,actor))
                if (cur.fetchone() or {}).get('role')!='owner':raise TenantLinearError('channel_retirement_owner_required',403)
                cur.execute('SELECT active,status FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid',(tenant,channel_id))
                local=cur.fetchone()
                if not local or local['active'] or local['status'] not in {'disconnected','disabled'}:
                    raise TenantLinearError('channel_must_be_locally_disconnected',409)
                cur.execute('SELECT scopes FROM saas_ai_agents WHERE tenant_id=%s::uuid AND enabled=TRUE AND archived_at IS NULL',(tenant,))
                for agent in cur.fetchall():
                    if any(s.get('provider')=='connect' and str(s.get('channel_id') or '') in {'',channel_id} for s in agent.get('scopes') or []):
                        raise TenantLinearError('channel_still_used_by_enabled_agent',409)
                cur.execute("SELECT 1 FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND state NOT IN ('sent','skipped','failed') LIMIT 1",(tenant,channel_id))
                if cur.fetchone():raise TenantLinearError('channel_has_unresolved_ai_work',409)
        credentials=transport._stored_connect_credentials(profile)
        key=str(credentials['api_key']);path='/v1/channels/'+channel_id
        remote=transport._request(key,'GET',path)
        # Never call a legacy destructive DELETE. Capability is explicit,
        # authenticated and supplied by the compatible Connect server only.
        if (remote.get('retirementPolicy')!='retain_history_v1' or remote.get('id')!=channel_id
                or remote.get('phoneConflict') or str(remote.get('phone') or '').lstrip('+')!=TARGET_PHONE):
            raise TenantLinearError('safe_channel_retirement_not_confirmed',409)
        if remote.get('status')!='disabled':
            if remote.get('status')!='disconnected':
                transport._request(key,'POST',path+'/disconnect')
            transport._request(key,'DELETE',path)
        archived=transport._request(key,'GET',path)
        if archived.get('status')!='disabled':raise TenantLinearError('channel_retirement_unconfirmed',409)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE saas_connect_channels SET active=FALSE,status='disabled',updated_at=now() WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid",(tenant,channel_id))
                cur.execute("SELECT metadata FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='connect' FOR UPDATE",(tenant,))
                metadata=dict((cur.fetchone() or {}).get('metadata') or {})
                if isinstance(metadata.get('channel_ids'),list):metadata['channel_ids']=[x for x in metadata['channel_ids'] if str(x)!=channel_id]
                cur.execute("UPDATE saas_tenant_integrations SET metadata=%s::jsonb,updated_at=now() WHERE tenant_id=%s::uuid AND provider='connect'",(json.dumps(metadata),tenant))
                if local['status']!='disabled':
                    _audit(cur,tenant,actor,'connect_channel_retired','connect_channel',channel_id,{'history_retained':True,'retirement_policy':'retain_history_v1'})
            conn.commit()
    return transport.status(profile)
