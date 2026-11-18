"""Provider receipt evidence; no customer turns, sends or inferred delivery."""
import json
import re
from tenant_whatsapp_native_projection import _jid, _error, PROVIDER_ID


def migrate(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_whatsapp_native_status (
      tenant_id UUID NOT NULL,channel_id UUID NOT NULL,generation TEXT NOT NULL,
      chat TEXT NOT NULL,provider_id TEXT NOT NULL,sender TEXT NOT NULL,
      rank SMALLINT NOT NULL CHECK(rank IN (2,3)),
      PRIMARY KEY(tenant_id,channel_id,generation,chat,provider_id,sender),
      FOREIGN KEY(tenant_id,channel_id,generation)
        REFERENCES saas_whatsapp_capture_generations(tenant_id,channel_id,generation))""")


def lock(cur,tenant,source,provider):
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
      ('native-status:'+tenant+':'+source+':'+provider,))


def observe(cur,scope,payload):
    # read-self, retry, inactive and played-self are not remote delivery.
    rank={'':2,'read':3,'played':3}.get(payload.get('status',''))
    if rank is None or payload.get('from_me') is not False:
        return False
    from tenant_linear_policy import TenantLinearError
    try:
        chat=_jid(payload.get('chat'),group=True)
        sender=receipt_sender(payload.get('sender'))
    except TenantLinearError:
        # Broadcast/newsletter/control destinations have no CRM chat mapping.
        # Retain their encrypted event without poisoning this source's cursor.
        return False
    provider=payload.get('provider_id')
    if not isinstance(provider,str) or not PROVIDER_ID.fullmatch(provider):return False
    if not chat.endswith('@g.us') and sender!=chat:
        return False
    lock(cur,scope['tenant'],scope['source'],provider)
    cur.execute("""INSERT INTO saas_whatsapp_native_status
      (tenant_id,channel_id,generation,chat,provider_id,sender,rank)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s)
      ON CONFLICT(tenant_id,channel_id,generation,chat,provider_id,sender)
      DO UPDATE SET rank=GREATEST(saas_whatsapp_native_status.rank,EXCLUDED.rank)""",
      (scope['tenant'],scope['source'],scope['generation'],chat,provider,sender,rank))
    reconcile(cur,scope['tenant'],scope['source'],provider)
    return True


def reconcile(cur,tenant,source,provider):
    lock(cur,tenant,source,provider)
    cur.execute("""SELECT m.id,m.raw FROM saas_crm_messages m
      WHERE m.tenant_id=%s::uuid AND m.channel=%s AND m.direction='outgoing'
        AND m.raw->>'externalMessageId'=%s FOR UPDATE""",(tenant,'connect:'+source,provider))
    messages=cur.fetchall()
    identities={}
    def aliases(chat):
        if chat not in identities:identities[chat]=verified_receipt_chats(cur,tenant,source,chat)
        return identities[chat]
    for message in messages:
        raw=message['raw'] or {}
        chat=raw.get('_crm_provider_conversation_id') or raw.get('conversationId')
        if not chat:continue
        # Never merge equal provider IDs belonging to different conversations.
        if sum(1 for m in messages if aliases((m['raw'] or {}).get('_crm_provider_conversation_id') or (m['raw'] or {}).get('conversationId')) & aliases(chat))!=1:
            continue
        cur.execute("""SELECT sender,MAX(rank) AS rank FROM saas_whatsapp_native_status
          WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND chat=ANY(%s::text[]) AND provider_id=%s
          GROUP BY sender""",(tenant,source,sorted(aliases(chat)),provider))
        rows=cur.fetchall()
        if not rows:continue
        if chat.endswith('@g.us'):
            # One group member is not proof every member read the message.
            update={'nativeRecipientStatus':{r['sender']:('read' if r['rank']==3 else 'delivered') for r in rows}}
        else:
            rank=max(r['rank'] for r in rows)
            if {'pending':0,'sent':1,'delivered':2,'read':3}.get(raw.get('status'),0)>=rank:continue
            update={'status':'read' if rank==3 else 'delivered'}
        cur.execute("UPDATE saas_crm_messages SET raw=COALESCE(raw,'{}'::jsonb)||%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",
          (json.dumps(update),tenant,str(message['id'])))


def receipt_sender(value):
    # Compatibility with retained receipts from before Go ToNonAD capture.
    # Strip only structural device/agent suffix, never infer a PN/LID pair.
    if isinstance(value,str):
        found=re.fullmatch(r'([1-9][0-9]{5,22})(?:[.]([0-9]{1,3}))?:([0-9]{1,5})@(s[.]whatsapp[.]net|lid)',value)
        if found and int(found[2] or 0)<=255 and int(found[3])<=65535:
            value=found[1]+'@'+found[4]
    return _jid(value)


def verified_receipt_chats(cur,tenant,source,chat):
    """Only an existing verified source-scoped PN/LID binding relates receipts.

    No telephone labels, names, timestamps or new inferred aliases are used.
    Ambiguous retained bindings leave the exact-chat rule in force.
    """
    from tenant_connect_direct_identity import established_canonical_conversation
    from tenant_whatsapp_native_projection import DIRECT
    if not isinstance(chat,str) or not DIRECT.fullmatch(chat):return {chat} if chat else set()
    canonical=established_canonical_conversation(cur,tenant,source,chat) or chat
    cur.execute("""SELECT identity_key FROM saas_connect_direct_identities
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND canonical_conversation_id=%s""",
      (tenant,source,canonical))
    candidates={r['identity_key'] for r in cur.fetchall()}|{canonical}
    candidates={x for x in candidates if DIRECT.fullmatch(x)}
    if chat not in candidates or any(sum(x.endswith('@'+kind) for x in candidates)>1 for kind in ('s.whatsapp.net','lid')):
        return {chat}
    return candidates
