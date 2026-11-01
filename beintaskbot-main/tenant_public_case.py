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
_MEDIA_SECRET_MARKER = re.compile(r"(?i)(password|passcode|парол|şifrə|пароль|api[_ -]?key|token\s*[:=]|secret\s*[:=]|private\s+key|recovery\s+phrase|otp\s*[:=])")
_SECRET_LINE = _MEDIA_SECRET_MARKER
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
          public_messages JSONB NOT NULL, created_by BIGINT NULL,
          revision_key TEXT NOT NULL DEFAULT '',
          snapshot_revision INTEGER NOT NULL DEFAULT 1,
          is_partial BOOLEAN NOT NULL DEFAULT FALSE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ NOT NULL,
          revoked_at TIMESTAMPTZ NULL,
          FOREIGN KEY(tenant_id,ticket_id) REFERENCES saas_support_tickets(tenant_id,id) ON DELETE CASCADE,
          FOREIGN KEY(tenant_id,group_channel_id,group_conversation_id)
            REFERENCES saas_connect_conversations(tenant_id,connect_channel_id,conversation_id) ON DELETE RESTRICT,
          FOREIGN KEY(tenant_id,created_by) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT)''')
        cur.execute('''CREATE INDEX IF NOT EXISTS saas_public_case_shares_expiry_idx
          ON saas_public_case_shares(expires_at) WHERE revoked_at IS NULL''')
        cur.execute("ALTER TABLE saas_public_case_shares ADD COLUMN IF NOT EXISTS revision_key TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE saas_public_case_shares ALTER COLUMN created_by DROP NOT NULL")
        cur.execute("ALTER TABLE saas_public_case_shares ADD COLUMN IF NOT EXISTS snapshot_revision INTEGER NOT NULL DEFAULT 1")
        cur.execute("ALTER TABLE saas_public_case_shares ADD COLUMN IF NOT EXISTS is_partial BOOLEAN NOT NULL DEFAULT FALSE")
        cur.execute("ALTER TABLE saas_public_case_shares ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS saas_public_case_shares_revision_idx ON saas_public_case_shares(tenant_id,ticket_id,group_channel_id,group_conversation_id,revision_key) WHERE revision_key<>''")
    conn.commit()


def create_for_pilot_handoff(cur, *, tenant_id, ticket_id, group_channel_id,
                             group_conversation_id, source_channel_id,
                             source_conversation_id, evidence_quote,case_message_ids,
                             source_external_id,
                             revision_key, origin, authorization_job):
    """Create one hash-only CASE snapshot for a fenced pilot handoff.

    Text and media come only from explicit ticket membership. Ordinary linked
    media is served on click; captions marked as credentials are withheld.
    """
    quote=_public_text(evidence_quote)
    if len(quote)<8 or not revision_key:
        raise TenantLinearError('CASE does not have a publishable client statement.',409)
    from tenant_ai_group_coordination import group_jid
    from tenant_ai_pilot import configured_group_scope, configured_scope, pilot_group_handoff_allowed
    target=configured_group_scope();customer=configured_scope()
    if (not target or not customer or str(target.get('tenant_id'))!=str(tenant_id)
            or str(target.get('channel_id'))!=str(group_channel_id)
            or str(target.get('conversation_id'))!=str(group_conversation_id)
            or str(customer.get('tenant_id'))!=str(tenant_id)
            or str(customer.get('channel_id'))!=str(source_channel_id)
            or str(customer.get('conversation_id'))!=str(source_conversation_id)
            or not group_jid(str(group_conversation_id))):
        raise TenantLinearError('CASE share destination is outside the configured pilot scope.',409)
    fenced_job={**(authorization_job if isinstance(authorization_job,dict) else {}),
      'tenant_id':str(tenant_id),'provider':'connect','channel_id':str(source_channel_id),
      'conversation_id':str(source_conversation_id)}
    if not pilot_group_handoff_allowed(fenced_job,connection=cur.connection):
        raise TenantLinearError('CASE share authorization is no longer active.',409)
    cur.execute('''SELECT t.channel_id::text AS channel_id,t.conversation_id,c.lead_id
      FROM saas_support_tickets t JOIN saas_connect_conversations c
        ON c.tenant_id=t.tenant_id AND c.connect_channel_id=t.channel_id AND c.conversation_id=t.conversation_id
      WHERE t.tenant_id=%s::uuid AND t.id=%s::uuid AND t.channel_id=%s::uuid AND t.conversation_id=%s
      FOR SHARE OF t,c''',(tenant_id,ticket_id,source_channel_id,source_conversation_id))
    route=cur.fetchone()
    if not route:raise TenantLinearError('CASE source no longer matches its ticket.',409)
    if not isinstance(case_message_ids,(list,tuple)) or not 1<=len(case_message_ids)<=30:
        raise TenantLinearError('CASE has no explicit bounded message membership.',409)
    external_ids=[]
    for value in case_message_ids:
        value=str(value or '').strip().removeprefix('connect:')
        if not value or len(value)>200 or value in external_ids:
            raise TenantLinearError('CASE message membership is invalid.',409)
        external_ids.append(value)
    source_external_id=str(source_external_id or '').strip().removeprefix('connect:')
    if not source_external_id or source_external_id not in external_ids:
        raise TenantLinearError('CASE source message is not in its explicit membership.',409)
    cur.execute('''SELECT m.id::text AS id,m.external_id,m.body,m.message_type,m.direction,
        COALESCE(m.happened_at,m.created_at) AS happened_at
      FROM saas_support_ticket_messages cm JOIN saas_crm_messages m ON m.id=cm.message_id
      WHERE cm.tenant_id=%s::uuid AND cm.ticket_id=%s::uuid AND m.tenant_id=%s::uuid
        AND m.kommo_lead_id=%s AND m.channel=%s AND m.direction IN ('incoming','outgoing')
        AND m.external_id=ANY(%s::text[])
      ORDER BY COALESCE(m.happened_at,m.created_at),m.id''',
      (tenant_id,ticket_id,tenant_id,int(route['lead_id']),'connect:'+str(source_channel_id),
       list(dict.fromkeys([item for value in external_ids for item in ('connect:'+value,value)]))))
    messages=cur.fetchall()
    if len(messages)!=len(external_ids):
        raise TenantLinearError('One or more CASE messages are not explicitly linked to this ticket.',409)
    if not any(m['external_id'] in {'connect:'+source_external_id,source_external_id} and m['direction']=='incoming' for m in messages):
        raise TenantLinearError('CASE current source is not linked to this ticket.',409)
    evidence_messages=[m for m in messages if m['direction']=='incoming' and m['message_type']=='text' and quote in str(m.get('body') or '')]
    if len(evidence_messages)!=1 or quote not in _public_text(evidence_messages[0].get('body')):
        raise TenantLinearError('CASE statement is not uniquely supported by its linked messages.',409)
    cur.execute('''SELECT 1 FROM saas_connect_conversations c JOIN saas_connect_channels ch
      ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
      WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid AND c.conversation_id=%s AND ch.active=TRUE''',
      (tenant_id,group_channel_id,group_conversation_id))
    if not cur.fetchone():raise TenantLinearError('CASE share group is not active.',409)
    token=secrets.token_urlsafe(32);share_id=str(uuid.uuid4())
    public_messages=[];selected_message_ids=[];selected_media_ids=[];partial=False
    media_types={'image','video','audio','voice','document'}
    for message in messages:
        message_id=str(message['id']);selected_message_ids.append(message_id)
        kind=str(message.get('message_type') or 'text').casefold()
        if kind in media_types:
            if _MEDIA_SECRET_MARKER.search(str(message.get('body') or '')):
                text='Müştəri fayl əlavə edib. Məxfi məlumat işarəsinə görə fayl açılmır.'
                partial=True
            else:
                text=_public_text(message.get('body')) or 'Müştəri fayl əlavə edib.'
                selected_media_ids.append(message_id)
        else:
            text=_public_text(message.get('body'))
        public_messages.append({'id':message_id,'speaker':'Müştəri' if message['direction']=='incoming' else 'BeinSystems','text':text,
          'type':kind if message_id in selected_media_ids else 'text',
          'at':message['happened_at'].isoformat() if message.get('happened_at') else ''})
    cur.execute('''INSERT INTO saas_public_case_shares(id,tenant_id,ticket_id,channel_id,conversation_id,
      group_channel_id,group_conversation_id,token_hash,selected_message_ids,selected_media_ids,
      public_messages,created_by,expires_at,revision_key,is_partial)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s::jsonb,%s::jsonb,
        %s::jsonb,%s,now()+interval '24 hours',%s,%s)
      ON CONFLICT DO NOTHING RETURNING id''',
      (share_id,tenant_id,ticket_id,source_channel_id,source_conversation_id,group_channel_id,
       group_conversation_id,hashlib.sha256(token.encode()).hexdigest(),json.dumps(selected_message_ids),
       json.dumps(selected_media_ids),json.dumps(public_messages),None,revision_key,partial))
    if not cur.fetchone():raise TenantLinearError('CASE share revision already exists.',409)
    from tenant_ai_agents_store import _audit
    _audit(cur,tenant_id,None,'public_case_share_create','support_ticket',ticket_id,
      {'share_id':share_id,'selected_message_ids':selected_message_ids,'selected_media_ids':selected_media_ids,
       'group_channel_id':str(group_channel_id),'group_conversation_id':str(group_conversation_id),
       'expires_in_hours':24,'revision_key':revision_key,'created_by':'pilot_handoff'})
    return {'id':share_id,'url':str(origin).rstrip('/')+'/public/case/'+token,
      'selected_message_ids':selected_message_ids,'selected_media_ids':selected_media_ids,'is_partial':partial}


def append_pilot_followup(cur, *, tenant_id, ticket_id, consultation_id,
                          group_channel_id, group_conversation_id,
                          message_external_id):
    """Append one separately linked inbound CASE follow-up to its pilot snapshot."""
    external_id=str(message_external_id or '').strip().removeprefix('connect:')
    if not external_id or len(external_id)>200:return False
    cur.execute('''SELECT m.id::text AS id,m.body,m.message_type,m.direction,
        COALESCE(m.happened_at,m.created_at) AS happened_at
      FROM saas_support_ticket_messages cm JOIN saas_crm_messages m ON m.id=cm.message_id
      JOIN saas_support_tickets t ON t.tenant_id=cm.tenant_id AND t.id=cm.ticket_id
      JOIN saas_connect_conversations c ON c.tenant_id=t.tenant_id AND c.connect_channel_id=t.channel_id
        AND c.conversation_id=t.conversation_id
      WHERE cm.tenant_id=%s::uuid AND cm.ticket_id=%s::uuid AND m.tenant_id=%s::uuid
        AND m.kommo_lead_id=c.lead_id AND m.channel='connect:'||t.channel_id::text
        AND m.external_id=ANY(%s::text[]) AND m.direction IN ('incoming','outgoing') LIMIT 1''',
      (tenant_id,ticket_id,tenant_id,['connect:'+external_id,external_id]))
    message=cur.fetchone()
    if not message:return False
    kind=str(message.get('message_type') or 'text').casefold();selected_media=[]
    withheld=kind in _MEDIA_TYPES and bool(_MEDIA_SECRET_MARKER.search(str(message.get('body') or '')))
    if withheld:
        text='Müştəri fayl əlavə edib. Məxfi məlumat işarəsinə görə fayl açılmır.'
    elif kind in _MEDIA_TYPES:
        text=_public_text(message.get('body')) or 'Müştəri fayl əlavə edib.'
        selected_media=[str(message['id'])]
    else:
        text=_public_text(message.get('body'))
    if not text:return False
    item={'id':str(message['id']),'speaker':'Müştəri' if message.get('direction')=='incoming' else 'BeinSystems','text':text,
      'type':kind if selected_media else 'text',
      'at':message['happened_at'].isoformat() if message.get('happened_at') else ''}
    cur.execute('''UPDATE saas_public_case_shares SET
        selected_message_ids=selected_message_ids||jsonb_build_array(%s::text),
        selected_media_ids=selected_media_ids||%s::jsonb,
        public_messages=public_messages||jsonb_build_array(%s::jsonb),
        snapshot_revision=snapshot_revision+1,is_partial=is_partial OR %s,updated_at=now()
      WHERE tenant_id=%s::uuid AND ticket_id=%s::uuid
        AND group_channel_id=%s::uuid AND group_conversation_id=%s
        AND revision_key=%s AND token_hash IS NOT NULL AND revoked_at IS NULL
        AND expires_at>now() AND NOT (selected_message_ids ? %s)
      RETURNING id''',
      (str(message['id']),json.dumps(selected_media),json.dumps(item),withheld,tenant_id,ticket_id,group_channel_id,
       group_conversation_id,'pilot:'+str(consultation_id),str(message['id'])) )
    return bool(cur.fetchone())


def append_pilot_developer_message(cur, *, tenant_id, ticket_id, consultation_id,
                                   group_channel_id, group_conversation_id,
                                   group_message_id):
    """Append only the signed, case-linked developer message to the share."""
    cur.execute('''SELECT f.group_reply,f.group_message_id,f.case_id::text AS case_id,
        f.group_channel_id::text AS group_channel_id,f.group_conversation_id
      FROM saas_ai_pilot_followups f
      WHERE f.tenant_id=%s::uuid AND f.case_id=%s::uuid AND f.consultation_id=%s::uuid
        AND f.group_channel_id=%s::uuid AND f.group_conversation_id=%s
        AND f.group_message_id=%s AND f.state IN ('queued','linked')
      ORDER BY f.created_at DESC LIMIT 1''',
      (tenant_id,ticket_id,consultation_id,group_channel_id,group_conversation_id,str(group_message_id)))
    row=cur.fetchone()
    if not row or row['case_id']!=str(ticket_id):return False
    text=_public_text(row.get('group_reply'))
    item={'id':'developer:'+str(row['group_message_id']),'speaker':'Texniki komanda',
      'text':text,'type':'text','at':''}
    if not text:
        cur.execute('''UPDATE saas_public_case_shares SET is_partial=TRUE,updated_at=now()
          WHERE tenant_id=%s::uuid AND ticket_id=%s::uuid AND group_channel_id=%s::uuid
            AND group_conversation_id=%s AND revision_key=%s AND token_hash IS NOT NULL
            AND revoked_at IS NULL AND expires_at>now()''',
          (tenant_id,ticket_id,group_channel_id,group_conversation_id,'pilot:'+str(consultation_id)))
        return False
    cur.execute('''UPDATE saas_public_case_shares SET
        public_messages=public_messages||jsonb_build_array(%s::jsonb),
        snapshot_revision=snapshot_revision+1,updated_at=now()
      WHERE tenant_id=%s::uuid AND ticket_id=%s::uuid AND group_channel_id=%s::uuid
        AND group_conversation_id=%s AND revision_key=%s AND token_hash IS NOT NULL
        AND revoked_at IS NULL AND expires_at>now()
        AND NOT (public_messages @> %s::jsonb)
      RETURNING id''',
      (json.dumps(item),tenant_id,ticket_id,group_channel_id,group_conversation_id,
       'pilot:'+str(consultation_id),json.dumps([{'id':item['id']}])) )
    return bool(cur.fetchone())


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
            # This is an explicit public snapshot, not a case-boundary edit.
            # Selected IDs below are revalidated against the exact source route;
            # creating a link must never hide other internal case materials.
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
            blocked_media_ids={key for key in media_ids if _MEDIA_SECRET_MARKER.search(str(by_id[key].get('body') or ''))}
            media_ids=[key for key in media_ids if key not in blocked_media_ids]
            messages=[]
            for key in message_ids:
                row=by_id[key];kind=str(row.get('message_type') or 'text').casefold()
                if kind in _MEDIA_TYPES and key in blocked_media_ids:
                    messages.append({'id':key,'speaker':'Müştəri' if row['direction']=='incoming' else 'Texniki komanda',
                      'text':'Müştəri fayl əlavə edib. Məxfi məlumat işarəsinə görə fayl açılmır.','at':row['happened_at'].isoformat() if row.get('happened_at') else ''})
                elif kind in _MEDIA_TYPES and key not in media_ids:
                    messages.append({'id':key,'speaker':'Müştəri' if row['direction']=='incoming' else 'Texniki komanda',
                      'text':_public_text(row['body']) or 'Fayl paylaşılmayıb.','at':row['happened_at'].isoformat() if row.get('happened_at') else ''})
                else:
                    messages.append({'id':key,'speaker':'Müştəri' if row['direction']=='incoming' else 'Texniki komanda',
                      'text':_public_text(row['body']) or ('Əlavə fayl' if kind in _MEDIA_TYPES else ''),
                      'type':kind if key in media_ids else 'text','at':row['happened_at'].isoformat() if row.get('happened_at') else ''})
            cur.execute('''INSERT INTO saas_public_case_shares(id,tenant_id,ticket_id,channel_id,conversation_id,group_channel_id,group_conversation_id,
              token_hash,selected_message_ids,selected_media_ids,public_messages,created_by,expires_at,revision_key,is_partial)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,now()+interval '24 hours',%s,%s)''',
              (share_id,tenant,ticket_id,route['channel_id'],route['conversation_id'],group_channel,group_conversation,token_hash,
                json.dumps(message_ids),json.dumps(media_ids),json.dumps(messages),actor,'owner:'+share_id,bool(blocked_media_ids)))
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
        with conn.cursor() as cur:
            cur.execute('''SELECT id::text,public_messages,selected_media_ids,updated_at,revision_key,snapshot_revision,is_partial FROM saas_public_case_shares
              WHERE token_hash=%s AND revoked_at IS NULL AND expires_at>now()''',(digest,))
            row=cur.fetchone()
    if not row:raise TenantLinearError('Страница недоступна.',404)
    result=dict(row)
    result['snapshot_at']=result.pop('updated_at').isoformat()
    result.pop('revision_key',None)
    return result


def media(token,message_id,client=''):
    from tenant_dialog_consultations import public_media
    independent=public_media(token,message_id,client,connection_factory=_connect)
    if independent is not None:return independent
    if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,100}',token):raise TenantLinearError('Файл недоступен.',404)
    try:message_id=str(uuid.UUID(message_id))
    except (ValueError,TypeError,AttributeError):raise TenantLinearError('Файл недоступен.',404) from None
    digest=hashlib.sha256(token.encode()).hexdigest();_rate_limit(hashlib.sha256(str(client or 'unknown').encode()).hexdigest())
    with _connect() as conn:
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
                AND (s.created_by IS NOT NULL OR EXISTS(
                  SELECT 1 FROM saas_support_ticket_messages cm WHERE cm.tenant_id=s.tenant_id
                    AND cm.ticket_id=s.ticket_id AND cm.message_id=m.id))
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
    try:return download_connect_message_media(route,external,kind,row['conversation_id'])
    except Exception:raise TenantLinearError('Файл временно недоступен.',503) from None
