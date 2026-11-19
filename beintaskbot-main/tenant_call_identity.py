"""Provider-confirmed caller labels; never combine independent histories."""
import re
from tenant_linear_policy import TenantLinearError

PN=re.compile(r'^[1-9][0-9]{5,21}@s\.whatsapp\.net$')
LID=re.compile(r'^[1-9][0-9]{5,21}@lid$')


def valid(peer,value):
    return (isinstance(value,dict) and set(value)=={'phone_jid','display_name'}
        and isinstance(value['phone_jid'],str) and bool(PN.fullmatch(value['phone_jid']))
        and (peer==value['phone_jid'] or bool(LID.fullmatch(peer)))
        and isinstance(value['display_name'],str) and len(value['display_name'])<=256
        and not any(ord(c)<32 or ord(c)==127 for c in value['display_name']))


def enrich(cur,c,peer,value,*,expected=None):
    if not valid(peer,value):return {}
    from tenant_connect_direct_identity import canonical_conversation
    metadata={'kind':'contact','provider_conversation_id':peer,
        'phone_jid':value['phone_jid'],'display_name':value['display_name']}
    data={'directIdentity':{'jid':value['phone_jid'],'lid':peer}} if LID.fullmatch(peer) else {}
    cur.execute('SAVEPOINT caller_identity')
    canonical,conflict=canonical_conversation(cur,c['tenant_id'],c['channel_id'],peer,data,metadata)
    if conflict or expected and canonical!=expected:
        cur.execute('ROLLBACK TO SAVEPOINT caller_identity');cur.execute('RELEASE SAVEPOINT caller_identity')
        return {}
    # Add missing labels only. Keep manual names, route/lead IDs, timestamps,
    # history, ownership and existing source grants untouched.
    cur.execute('''UPDATE saas_connect_conversations SET
      phone=CASE WHEN phone='' THEN %s ELSE phone END,
      display_name=CASE WHEN display_name='' THEN %s ELSE display_name END,
      contact_name=CASE WHEN contact_name='' THEN %s ELSE contact_name END
      WHERE tenant_id=%s AND connect_channel_id=%s AND conversation_id=%s
        AND conversation_kind<>'group' AND (phone='' OR display_name='' OR contact_name='')''',
      (value['phone_jid'].split('@')[0],value['display_name'],value['display_name'],c['tenant_id'],c['channel_id'],canonical))
    cur.execute('RELEASE SAVEPOINT caller_identity')
    return {'canonical':canonical,'metadata':metadata}


def refresh_call(profile,call_id):
    """Addressed owner repair using current gateway evidence, not user labels."""
    from tenant_call_sources import _connect,_owner,remote
    from tenant_whatsapp_native_transport import bind
    with _connect() as conn,conn.cursor() as cur:
        cur.execute('SELECT * FROM saas_browser_calls WHERE tenant_id=%s AND id=%s',(profile['tenant_id'],call_id))
        row=cur.fetchone()
        if not row:raise TenantLinearError('Звонок не найден.',404)
        channel=str(row['channel_id']);tenant,_=_owner(cur,profile,channel)
        route=bind(cur,{'tenant_id':tenant,'channel_id':channel,'api_key':''})
        binding=route.get('native')
        if not binding or str(row['gateway_id'])!=binding['gateway_id']:raise TenantLinearError('Источник звонка изменился.',409)
        peer=row['peer_jid'];conversation=row['conversation_id']
    result=remote('/v1/sources/'+binding['gateway_id']+'/messages/contact',{'scope':binding['scope'],'chat':peer})
    if result.get('scope')!=binding['scope'] or result.get('peer_jid')!=peer or not valid(peer,result.get('caller')):
        raise TenantLinearError('WhatsApp не подтвердил звонящего.',409)
    with _connect() as conn,conn.cursor() as cur:
        _owner(cur,profile,channel)
        current=bind(cur,route)
        if current.get('native')!=binding:raise TenantLinearError('Источник звонка изменился.',409)
        cur.execute('SELECT 1 FROM saas_browser_calls WHERE tenant_id=%s AND id=%s AND channel_id=%s AND gateway_id=%s AND peer_jid=%s AND conversation_id=%s FOR SHARE',
          (tenant,call_id,channel,binding['gateway_id'],peer,conversation))
        if not cur.fetchone() or not enrich(cur,{'tenant_id':tenant,'channel_id':channel},peer,result['caller'],expected=conversation):
            raise TenantLinearError('Связь диалога неоднозначна. История не изменена.',409)
    return {'updated':True,'call_id':call_id,'conversation_id':conversation}
