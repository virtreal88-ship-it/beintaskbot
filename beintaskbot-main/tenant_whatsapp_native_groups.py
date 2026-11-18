"""Source-bound provider group metadata, never a message or authority grant."""
import json
from datetime import datetime, timedelta, timezone
import requests
from tenant_platform import _connect, TenantPlatformError
from tenant_whatsapp_native_projection import GROUP


def apply_metadata(cur,scope,payload):
    from tenant_group_participants import projection
    chat=payload.get('chat')
    data=payload.get('snapshot')
    if not isinstance(chat,str) or not GROUP.fullmatch(chat) or not isinstance(data,dict):
        return False
    # Replayed metadata can legitimately be old. Validate its roster against
    # its own observation time; order below prevents stale names overwriting.
    try: observed=datetime.fromisoformat(data['observedAt'].replace('Z','+00:00'))
    except (KeyError,TypeError,ValueError,AttributeError):return False
    if (observed.tzinfo is None or observed.year<2009 or observed>datetime.now(timezone.utc)+timedelta(minutes=1)
            or projection(data,observed).get('status')!='available'):return False
    name=data.get('group_name')
    if not isinstance(name,str) or not name.strip() or len(name)>256 or any(ord(c)<32 for c in name):return False
    # No contact/source/permission changes, no fake activity, no local label
    # overwrite and no group creation just because a metadata response exists.
    cur.execute('''UPDATE saas_connect_conversations SET display_name=%s,
      conversation_kind='group',native_group_observed_at=%s
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s
        AND (native_group_observed_at IS NULL OR native_group_observed_at<%s)''',
      (name.strip(),observed,scope['tenant'],scope['source'],chat,observed))
    return True


def fetch(route):
    from tenant_call_sources import service
    from tenant_whatsapp_native_transport import bind
    url,token=service()
    with _connect() as conn,conn.cursor() as cur:
        current=bind(cur,route)
        if not current.get('native') or current['native']!=route.get('native'):
            raise TenantPlatformError('native_source_changed')
        chat=route['conversation_id']
        if not GROUP.fullmatch(chat):raise TenantPlatformError('native_group_invalid')
        try:
            with requests.post(url+'/v1/sources/'+current['native']['gateway_id']+'/messages/group',
                headers={'Authorization':'Bearer '+token,'Accept-Encoding':'identity'},
                json={'scope':current['native']['scope'],'chat':chat},
                timeout=(3,8),allow_redirects=False,stream=True) as response:
                if response.status_code!=200 or response.headers.get('Content-Encoding','identity')!='identity':raise ValueError()
                body=bytearray()
                for chunk in response.iter_content(8192):
                    body.extend(chunk)
                    if len(body)>65536:raise ValueError()
                return json.loads(body)
        except (requests.RequestException,ValueError):
            return {'status':'unavailable'}
