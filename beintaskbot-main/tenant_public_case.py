"""Bounded, read-only public support-case shares.

Only an owner can create/revoke shares. Tokens are returned once and only a
SHA-256 digest is stored. Public content is an explicit snapshot; files are
separately allowlisted by message UUID and resolved server-side.
"""
import hashlib
import json
import re
import secrets
import time
import uuid

from tenant_linear_policy import TenantLinearError, identifier
from tenant_operator_access import authorize as _authorize
from tenant_platform import _connect
from tenant_ai_agents_schema import ensure_schema

_RATE = {}
_RATE_LIMIT = 60
_RATE_WINDOW = 60
_SECRET_LINE = re.compile(r"(?i)(anydesk|password|passcode|парол|şifrə|пароль|api[_ -]?key|token\s*[:=])")
_SYNTHETIC_MEDIA = re.compile(r"(?is)\[(?:image supplied for analysis|attachment unavailable|untrusted document|untrusted sampled video|unverified video|audio transcript|audio unavailable|video was not analyzed)[^\]]*\]")
_MEDIA_TYPES = {'image','video','audio','voice','document'}


def _schema(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_public_case_shares(
          id UUID PRIMARY KEY, tenant_id UUID NOT NULL, ticket_id UUID NOT NULL,
          channel_id UUID NOT NULL, conversation_id TEXT NOT NULL,
          group_channel_id UUID NOT NULL, group_conversation_id TEXT NOT NULL,
          token_hash TEXT NOT NULL UNIQUE, selected_message_ids JSONB NOT NULL,
          selected_media_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
          public_messages JSONB NOT NULL, created_by BIGINT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ NOT NULL,
          revoked_at TIMESTAMPTZ NULL,
          FOREIGN KEY(tenant_id,ticket_id) REFERENCES saas_support_tickets(tenant_id,id) ON DELETE CASCADE,
          FOREIGN KEY(tenant_id,group_channel_id,group_conversation_id)
            REFERENCES saas_connect_conversations(tenant_id,connect_channel_id,conversation_id) ON DELETE RESTRICT,
          FOREIGN KEY(tenant_id,created_by) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT)''')
        cur.execute('''CREATE INDEX IF NOT EXISTS saas_public_case_shares_expiry_idx
          ON saas_public_case_shares(expires_at) WHERE revoked_at IS NULL''')
    conn.commit()


def _public_text(value):
    text='\n'.join(line for line in str(value or '').splitlines() if not _SECRET_LINE.search(line))
    text=_SYNTHETIC_MEDIA.sub('',text)
    text=re.sub(r'(?i)https?://\S+', '[link removed]', text)
    return text[:2000].strip()


def _valid_ids(value, *, required=False):
    if not isinstance(value,list) or len(value)>30 or (required and not value):
        raise TenantLinearError('Выберите сообщения обращения.',400)
    result=[]
    for item in value:
        try: key=str(uuid.UUID(str(item)))
        except (ValueError,TypeError,AttributeError): raise TenantLinearError('Некорректный список сообщений.',400) from None
        if key in result: raise TenantLinearError('Повторяется сообщение.',400)
        result.append(key)
    return result


def create(profile,ticket_id,payload,origin):
    if not isinstance(payload,dict): raise TenantLinearError('Некорректный запрос.',400)
    message_ids=_valid_ids(payload.get('message_ids'),required=True)
    media_ids=_valid_ids(payload.get('media_message_ids',[]))
    if not set(media_ids).issubset(message_ids): raise TenantLinearError('Файл должен входить в выбранные сообщения.',400)
    if media_ids and payload.get('confirm_media_review') is not True:
        raise TenantLinearError('Подтвердите просмотр каждого выбранного файла.',400)
    group_channel=identifier(payload.get('group_channel_id'))
    group_conversation=str(payload.get('group_conversation_id') or '').strip()
    from tenant_ai_group_coordination import group_jid
    if not group_jid(group_conversation):raise TenantLinearError('Выберите WhatsApp-группу разработчиков.',400)
    ticket_id=identifier(ticket_id)
    token=secrets.token_urlsafe(32)
    token_hash=hashlib.sha256(token.encode()).hexdigest()
    share_id=str(uuid.uuid4())
    with _connect() as conn:
        ensure_schema(conn);_schema(conn)
        with conn.cursor() as cur:
            tenant,actor=_authorize(cur,profile)
            cur.execute("SELECT role FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE",(tenant,actor))
            if (cur.fetchone() or {}).get('role')!='owner':raise TenantLinearError('Только владелец может создать ссылку.',403)
            cur.execute('''SELECT t.channel_id::text AS channel_id,t.conversation_id,c.lead_id
              FROM saas_support_tickets t JOIN saas_connect_conversations c
                ON c.tenant_id=t.tenant_id AND c.connect_channel_id=t.channel_id AND c.conversation_id=t.conversation_id
              WHERE t.tenant_id=%s::uuid AND t.id=%s::uuid FOR SHARE OF t,c''',(tenant,ticket_id))
            route=cur.fetchone()
            if not route:raise TenantLinearError('Обращение недоступно.',404)
            cur.execute('''SELECT 1 FROM saas_connect_conversations c JOIN saas_connect_channels ch
              ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
              WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid AND c.conversation_id=%s AND ch.active=TRUE''',
              (tenant,group_channel,group_conversation))
            if not cur.fetchone():raise TenantLinearError('Группа недоступна в этой компании.',404)
            cur.execute('''SELECT id::text AS id,external_id,body,message_type,direction,
                COALESCE(happened_at,created_at) AS happened_at
              FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s
                AND id=ANY(%s::uuid[]) ORDER BY COALESCE(happened_at,created_at),id''',
                (tenant,int(route['lead_id']),'connect:'+route['channel_id'],message_ids))
            rows=cur.fetchall()
            if len(rows)!=len(message_ids):raise TenantLinearError('Одно или несколько сообщений не принадлежат этому обращению.',409)
            by_id={str(row['id']):row for row in rows}
            messages=[]
            for key in message_ids:
                row=by_id[key];kind=str(row.get('message_type') or 'text').casefold()
                if kind in _MEDIA_TYPES and key not in media_ids:
                    messages.append({'id':key,'speaker':'Müştəri' if row['direction']=='incoming' else 'Texniki komanda',
                      'text':_public_text(row['body']) or 'Fayl paylaşılmayıb.','at':row['happened_at'].isoformat() if row.get('happened_at') else ''})
                else:
                    messages.append({'id':key,'speaker':'Müştəri' if row['direction']=='incoming' else 'Texniki komanda',
                      'text':_public_text(row['body']) or ('Əlavə fayl' if kind in _MEDIA_TYPES else ''),
                      'type':kind if key in media_ids else 'text','at':row['happened_at'].isoformat() if row.get('happened_at') else ''})
            cur.execute('''INSERT INTO saas_public_case_shares(id,tenant_id,ticket_id,channel_id,conversation_id,group_channel_id,group_conversation_id,
              token_hash,selected_message_ids,selected_media_ids,public_messages,created_by,expires_at)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,now()+interval '24 hours')''',
              (share_id,tenant,ticket_id,route['channel_id'],route['conversation_id'],group_channel,group_conversation,token_hash,
                json.dumps(message_ids),json.dumps(media_ids),json.dumps(messages),actor))
            from tenant_ai_agents_store import _audit
            _audit(cur,tenant,actor,'public_case_share_create','support_ticket',ticket_id,
              {'share_id':share_id,'selected_message_ids':message_ids,'selected_media_ids':media_ids,
               'group_channel_id':group_channel,'group_conversation_id':group_conversation,'expires_in_hours':24})
        conn.commit()
    return {'id':share_id,'url':origin.rstrip('/')+'/public/case/'+token,'expires_in_hours':24,'selected_message_ids':message_ids}


def revoke(profile,share_id):
    share_id=identifier(share_id)
    with _connect() as conn:
        ensure_schema(conn);_schema(conn)
        with conn.cursor() as cur:
            tenant,actor=_authorize(cur,profile)
            cur.execute("SELECT role FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE",(tenant,actor))
            if (cur.fetchone() or {}).get('role')!='owner':raise TenantLinearError('Только владелец может отключить ссылку.',403)
            cur.execute('''UPDATE saas_public_case_shares SET revoked_at=now()
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND revoked_at IS NULL RETURNING id''',(tenant,share_id))
            if not cur.fetchone():raise TenantLinearError('Ссылка не найдена.',404)
            from tenant_ai_agents_store import _audit
            _audit(cur,tenant,actor,'public_case_share_revoke','public_case_share',share_id,{})
        conn.commit()
    return {'revoked':True}


def list_shares(profile,ticket_id):
    ticket_id=identifier(ticket_id)
    with _connect() as conn:
        ensure_schema(conn);_schema(conn)
        with conn.cursor() as cur:
            tenant,actor=_authorize(cur,profile)
            cur.execute("SELECT role FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE",(tenant,actor))
            if (cur.fetchone() or {}).get('role')!='owner':raise TenantLinearError('Только владелец может управлять ссылками.',403)
            cur.execute('''SELECT id::text,created_at,expires_at,revoked_at,
              jsonb_array_length(selected_message_ids) AS message_count,jsonb_array_length(selected_media_ids) AS media_count
              FROM saas_public_case_shares WHERE tenant_id=%s::uuid AND ticket_id=%s::uuid ORDER BY created_at DESC LIMIT 20''',(tenant,ticket_id))
            rows=[dict(row) for row in cur.fetchall()]
    return [{'id':r['id'],'created_at':str(r['created_at']),'expires_at':str(r['expires_at']),
      'revoked':bool(r['revoked_at']),'message_count':int(r['message_count']),'media_count':int(r['media_count'])} for r in rows]


def _rate_limit(key):
    now=time.monotonic();start,count=_RATE.get(key,(now,0))
    if now-start>=_RATE_WINDOW:start,count=now,0
    count+=1;_RATE[key]=(start,count)
    if len(_RATE)>10000:
        for old,(window,_) in list(_RATE.items()):
            if now-window>=_RATE_WINDOW:_RATE.pop(old,None)
        if len(_RATE)>10000:
            for old in list(_RATE)[:len(_RATE)-10000]:_RATE.pop(old,None)
    if count>_RATE_LIMIT:raise TenantLinearError('Ссылка временно недоступна.',429)


def read(token,client=''):
    if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,100}',token):raise TenantLinearError('Страница недоступна.',404)
    digest=hashlib.sha256(token.encode()).hexdigest();_rate_limit(hashlib.sha256(str(client or 'unknown').encode()).hexdigest())
    with _connect() as conn:
        ensure_schema(conn);_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT id::text,public_messages,selected_media_ids FROM saas_public_case_shares
              WHERE token_hash=%s AND revoked_at IS NULL AND expires_at>now()''',(digest,))
            row=cur.fetchone()
    if not row:raise TenantLinearError('Страница недоступна.',404)
    return dict(row)


def media(token,message_id,client=''):
    if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,100}',token):raise TenantLinearError('Файл недоступен.',404)
    try:message_id=str(uuid.UUID(message_id))
    except (ValueError,TypeError,AttributeError):raise TenantLinearError('Файл недоступен.',404) from None
    digest=hashlib.sha256(token.encode()).hexdigest();_rate_limit(hashlib.sha256(str(client or 'unknown').encode()).hexdigest())
    with _connect() as conn:
        ensure_schema(conn);_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT s.tenant_id::text,s.channel_id::text,s.conversation_id,c.lead_id,
                m.external_id,m.message_type,s.id::text AS share_id
              FROM saas_public_case_shares s
              JOIN saas_support_tickets t ON t.tenant_id=s.tenant_id AND t.id=s.ticket_id
              JOIN saas_connect_conversations c ON c.tenant_id=t.tenant_id AND c.connect_channel_id=t.channel_id AND c.conversation_id=t.conversation_id
              JOIN saas_crm_messages m ON m.tenant_id=s.tenant_id AND m.kommo_lead_id=c.lead_id
                AND m.channel='connect:'||s.channel_id::text AND m.id=%s::uuid
              WHERE s.token_hash=%s AND s.revoked_at IS NULL AND s.expires_at>now()
                AND s.selected_message_ids ? %s AND s.selected_media_ids ? %s
                AND m.message_type IN ('image','audio','voice','video','document')''',
                (message_id,digest,message_id,message_id))
            row=cur.fetchone()
            if not row:raise TenantLinearError('Файл недоступен.',404)
            from tenant_connect_transport import connect_route,download_connect_message_media
            route=connect_route(cur,row['tenant_id'],int(row['lead_id']),historical_read=True)
            if not route or route['channel_id']!=row['channel_id'] or route['conversation_id']!=row['conversation_id']:
                raise TenantLinearError('Файл недоступен.',404)
            external=str(row['external_id'] or '')
            if external.startswith('connect:'):external=external[len('connect:'):]
            kind={'voice':'audio'}.get(str(row['message_type']),str(row['message_type']))
        conn.commit()
    try:return download_connect_message_media(route,external,kind)
    except Exception:raise TenantLinearError('Файл временно недоступен.',503) from None
