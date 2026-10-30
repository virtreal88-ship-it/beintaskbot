"""One-shot, tenant-scoped WhatsApp group delivery for existing consultations.

No model, attachment download, customer send, resolution or agent setting is
performed here. Provider errors after dispatch are deliberately quarantined.
"""
import hashlib
import re
import uuid
from tenant_platform import _connect
from tenant_linear_policy import TenantLinearError,identifier
from tenant_operator_access import access,assert_route
from tenant_ai_agents_store import _audit
from tenant_ai_group_coordination import group_jid
from tenant_internal_consultations import schema as consultation_schema

_MARKER=re.compile(r'(?<![A-Za-z0-9])CASE:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?![A-Za-z0-9])',re.I)


def schema(conn):
    consultation_schema(conn)
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_consultation_group_deliveries(
          tenant_id UUID NOT NULL,consultation_id UUID NOT NULL,request_id UUID NOT NULL,case_id UUID NOT NULL,
          channel_id UUID NOT NULL,conversation_id TEXT NOT NULL,body TEXT NOT NULL,body_hash TEXT NOT NULL,
          state TEXT NOT NULL,connect_message_id TEXT NULL,provider_message_id TEXT NULL,created_by BIGINT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,consultation_id,request_id),UNIQUE(tenant_id,request_id),
          FOREIGN KEY(tenant_id,consultation_id) REFERENCES saas_internal_consultations(tenant_id,id) ON DELETE RESTRICT,
          FOREIGN KEY(tenant_id,case_id) REFERENCES saas_support_tickets(tenant_id,id) ON DELETE RESTRICT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_consultation_group_replies(
          tenant_id UUID NOT NULL,consultation_id UUID NOT NULL,channel_id UUID NOT NULL,conversation_id TEXT NOT NULL,
          message_id TEXT NOT NULL,body TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,channel_id,conversation_id,message_id),
          FOREIGN KEY(tenant_id,consultation_id) REFERENCES saas_internal_consultations(tenant_id,id) ON DELETE RESTRICT)''')
        cur.execute("ALTER TABLE saas_consultation_group_deliveries ADD COLUMN IF NOT EXISTS customer_draft TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE saas_consultation_group_deliveries ADD COLUMN IF NOT EXISTS draft_reply_id TEXT NULL")
        cur.execute("ALTER TABLE saas_consultation_group_deliveries ADD COLUMN IF NOT EXISTS draft_revision BIGINT NOT NULL DEFAULT 0")
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_pilot_followups(
          tenant_id UUID NOT NULL, id UUID NOT NULL, consultation_id UUID NOT NULL, case_id UUID NOT NULL,
          channel_id UUID NOT NULL, conversation_id TEXT NOT NULL, group_channel_id UUID NOT NULL,
          group_conversation_id TEXT NOT NULL, group_message_id TEXT NOT NULL, group_reply TEXT NOT NULL,
          source_message_id TEXT NULL, agent_id UUID NULL, event_fingerprint TEXT NULL,
          state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','linked','blocked')),
          error_code TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,id),
          UNIQUE(tenant_id,group_channel_id,group_conversation_id,group_message_id),
          FOREIGN KEY(tenant_id,consultation_id) REFERENCES saas_internal_consultations(tenant_id,id) ON DELETE RESTRICT,
          FOREIGN KEY(tenant_id,case_id) REFERENCES saas_support_tickets(tenant_id,id) ON DELETE RESTRICT)''')
        cur.execute("ALTER TABLE saas_ai_pilot_followups ADD COLUMN IF NOT EXISTS customer_outbound_message_id TEXT NULL")
        cur.execute("ALTER TABLE saas_ai_pilot_followups ADD COLUMN IF NOT EXISTS customer_verified_at TIMESTAMPTZ NULL")
        cur.execute("ALTER TABLE saas_ai_pilot_followups ADD COLUMN IF NOT EXISTS customer_verification_message_id TEXT NULL")


def _owner(cur,profile):
    tenant,actor,policy=access(cur,profile,'support')
    if policy.profile.get('role')!='owner' or not policy.allows('dialogs'):
        raise TenantLinearError('Only the company owner with dialog access may send a consultation.',403)
    return tenant,actor,policy


def destinations(profile):
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,_,policy=_owner(cur,profile)
            cur.execute('''SELECT c.connect_channel_id::text AS channel_id,c.conversation_id,
                COALESCE(NULLIF(c.display_name,''),NULLIF(c.contact_name,''),c.conversation_id) AS contact_name,ch.name AS channel_name
              FROM saas_connect_conversations c JOIN saas_connect_channels ch
                ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
              WHERE c.tenant_id=%s::uuid AND ch.active=TRUE AND c.conversation_id LIKE %s
              ORDER BY c.last_message_at DESC NULLS LAST LIMIT 200''',(tenant,'%@g.us'))
            groups=[dict(row) for row in cur.fetchall() if group_jid(row['conversation_id'])]
            from tenant_ai_pilot import scoped_live_enabled, is_pilot_group_target
            if scoped_live_enabled():
                groups=[row for row in groups if is_pilot_group_target(
                    tenant,row['channel_id'],row['conversation_id'])]
            return {'groups':groups}


def prepare(profile,consultation_id,data):
    if not isinstance(data,dict) or set(data)!={'request_id','channel_id','conversation_id','summary','confirm_group','attachment_ids'}:
        raise TenantLinearError('Invalid consultation request.',400)
    if data.get('confirm_group') is not True:raise TenantLinearError('Confirm the destination group.',409)
    request_id=identifier(data['request_id']);channel=identifier(data['channel_id']);group=str(data['conversation_id'] or '')
    if not group_jid(group):raise TenantLinearError('A group destination is required.',400)
    summary=data['summary']
    if not isinstance(summary,str) or not summary.strip() or len(summary)>2600:raise TenantLinearError('A short consultation summary is required.',400)
    if not isinstance(data['attachment_ids'],list) or len(data['attachment_ids'])>10:
        raise TenantLinearError('Invalid attachment selection.',400)
    attachment_ids=[identifier(value) for value in data['attachment_ids']]
    if len(set(attachment_ids))!=len(attachment_ids):raise TenantLinearError('Duplicate attachment selection.',400)
    key=identifier(consultation_id)
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            tenant,actor,policy=_owner(cur,profile)
            # A client request UUID must never be mistaken for AI-run origin
            # when Connect later observes the signed outbound echo.
            cur.execute('''SELECT 1 FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid AND id=%s::uuid
              UNION ALL SELECT 1 FROM saas_ai_agent_drafts WHERE tenant_id=%s::uuid AND id=%s::uuid LIMIT 1''',
              (tenant,request_id,tenant,request_id))
            if cur.fetchone():raise TenantLinearError('Request identity is already in use.',409)
            cur.execute('''SELECT c.id,c.case_id,c.channel_id,c.conversation_id,c.status,t.status AS ticket_status
              FROM saas_internal_consultations c JOIN saas_support_tickets t ON t.tenant_id=c.tenant_id AND t.id=c.case_id
              WHERE c.tenant_id=%s::uuid AND c.id=%s::uuid FOR UPDATE OF c,t''',(tenant,key));consult=cur.fetchone()
            if not consult:raise TenantLinearError('Consultation is unavailable.',404)
            if consult['status']=='closed' or consult['ticket_status']=='resolved':raise TenantLinearError('Consultation is closed.',409)
            source=assert_route(cur,tenant,policy,'connect',consult['conversation_id'],str(consult['channel_id']))
            assert_route(cur,tenant,policy,'connect',group,channel)
            from tenant_ai_pilot import assert_pilot_group_target
            assert_pilot_group_target(tenant,channel,group)
            # The link contains no credentials and opens under the recipient's
            # own CRM session. Attachment access remains separately authorized.
            case_id=str(consult['case_id'])
            links=[]
            for message_id in attachment_ids:
                cur.execute('''SELECT id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND id=%s::uuid
                  AND kommo_lead_id=%s AND channel=%s AND message_type IN ('image','audio','voice','video','document')''',
                  (tenant,message_id,source['lead_id'],'connect:'+str(consult['channel_id'])))
                if not cur.fetchone():raise TenantLinearError('Attachment is unavailable in this conversation.',404)
                links.append('https://crm.pro.az/app?view=support&support_ticket_id='+case_id+'&support_ticket_message_id='+message_id)
            body=('CASE:'+key+'\n'+summary.strip()+'\n\n'
                  'https://crm.pro.az/app?view=support&support_ticket_id='+case_id+
                  (('\n\nƏlavələr (CRM-də giriş və icazə tələb olunur):\n'+'\n'.join(links)) if links else '')+
                  '\nCavabda CASE:'+key+' qeyd edin.')
            # Connect validates the JavaScript string length before dispatch.
            if len(body.encode('utf-16-le'))//2>4096:
                raise TenantLinearError('Сократите сводку или выберите меньше вложений.',400)
            digest=hashlib.sha256(body.encode()).hexdigest()
            cur.execute('''SELECT * FROM saas_consultation_group_deliveries WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid
              ORDER BY created_at,request_id FOR UPDATE''',(tenant,key));previous=cur.fetchall()
            old=next((r for r in previous if str(r['request_id'])==request_id),None)
            if old:
                if str(old['channel_id'])!=channel or old['conversation_id']!=group or old['body_hash']!=digest:
                    raise TenantLinearError('This request already has a different delivery.',409)
                return {'consultation_id':key,'case_id':str(consult['case_id']),'state':old['state'],'created':False}
            if previous and any(str(r['channel_id'])!=channel or r['conversation_id']!=group for r in previous):
                raise TenantLinearError('The consultation group cannot be changed.',409)
            if any(r['state'] not in {'sent','blocked'} for r in previous):
                raise TenantLinearError('An earlier delivery needs review before another message.',409)
            last=previous[-1] if previous else {}
            cur.execute('''INSERT INTO saas_consultation_group_deliveries(tenant_id,consultation_id,request_id,case_id,channel_id,conversation_id,body,body_hash,state,created_by,customer_draft,draft_reply_id,draft_revision)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,'ready',%s,%s,%s,%s)''',
              (tenant,key,request_id,consult['case_id'],channel,group,body,digest,actor,last.get('customer_draft') or '',last.get('draft_reply_id'),last.get('draft_revision') or 0))
            _audit(cur,tenant,actor,'consultation_group_prepared','consultation',key,{'request_id':request_id,'channel_id':channel})
        conn.commit()
    return {'consultation_id':key,'case_id':str(consult['case_id']),'state':'ready','created':True}


def dispatch(profile,consultation_id,request_id):
    from tenant_connect_transport import connect_route,send_group_with_receipt
    key=identifier(consultation_id);request_id=identifier(request_id)
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            tenant,actor,policy=_owner(cur,profile)
            cur.execute('''SELECT c.channel_id,c.conversation_id,c.status,t.status AS ticket_status FROM saas_internal_consultations c
              JOIN saas_support_tickets t ON t.tenant_id=c.tenant_id AND t.id=c.case_id
              WHERE c.tenant_id=%s::uuid AND c.id=%s::uuid FOR UPDATE OF c,t''',(tenant,key));consult=cur.fetchone()
            if not consult or consult['status']=='closed' or consult['ticket_status']=='resolved':raise TenantLinearError('Consultation is closed.',409)
            cur.execute('SELECT * FROM saas_consultation_group_deliveries WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND request_id=%s::uuid FOR UPDATE',(tenant,key,request_id));row=cur.fetchone()
            if not row or str(row['request_id'])!=request_id:raise TenantLinearError('Delivery is unavailable.',404)
            if row['state']=='sent':return {'state':'sent','message_id':row['connect_message_id']}
            if row['state']!='ready':raise TenantLinearError('Delivery outcome is unresolved; inspect it before retrying.',409)
            assert_route(cur,tenant,policy,'connect',consult['conversation_id'],str(consult['channel_id']))
            group=assert_route(cur,tenant,policy,'connect',row['conversation_id'],str(row['channel_id']))
            route=connect_route(cur,tenant,int(group['lead_id']))
            if not route or route['channel_id']!=str(row['channel_id']) or route['conversation_id']!=row['conversation_id']:
                raise TenantLinearError('Destination group changed or is unavailable.',409)
            cur.execute("UPDATE saas_consultation_group_deliveries SET state='sending',updated_at=now() WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND request_id=%s::uuid",(tenant,key,request_id))
            body=row['body']
        conn.commit()
    attempted=False
    try:
      with _connect() as conn:
        with conn.cursor() as cur:
            # Hold the same group/dialogue fence as the normal Connect sender
            # across the provider call; never change the group's AI state.
            from tenant_ai_dialog_scope import lock_dialog
            from tenant_ai_group_coordination import read as group_state
            from tenant_platform import _json
            lock_dialog(cur,tenant,'connect',str(row['channel_id']),row['conversation_id'])
            _tenant,_actor,current_policy=_owner(cur,profile)
            assert_route(cur,tenant,current_policy,'connect',consult['conversation_id'],str(consult['channel_id']))
            current_group=assert_route(cur,tenant,current_policy,'connect',row['conversation_id'],str(row['channel_id']))
            state=group_state(cur,tenant,row['conversation_id'])
            cur.execute("""SELECT 1 FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect'
              AND conversation_id=%s AND state IN ('queued','processing') LIMIT 1""",(tenant,row['conversation_id']))
            if state.get('state') not in {'human','paused'} or cur.fetchone():
                raise TenantLinearError('Группа должна быть остановлена без активных AI-заданий.',409)
            current_route=connect_route(cur,tenant,int(current_group['lead_id']))
            if not current_route or current_route['channel_id']!=str(row['channel_id']) or current_route['conversation_id']!=row['conversation_id']:
                raise TenantLinearError('Destination group changed or is unavailable.',409)
            attempted=True;receipt=send_group_with_receipt(current_route,body,request_id)
            cur.execute("""UPDATE saas_consultation_group_deliveries SET state='sent',connect_message_id=%s,provider_message_id=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND request_id=%s::uuid AND state='sending' RETURNING consultation_id""",(receipt['message_id'],receipt['external_message_id'],tenant,key,request_id))
            if not cur.fetchone():raise TenantLinearError('Delivery receipt needs review.',409)
            cur.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,author_name,body,message_type,raw)
              VALUES(%s::uuid,%s::uuid,%s,%s,'outgoing',%s,'Operator',%s,'text',%s::jsonb)
              ON CONFLICT(tenant_id,external_id) DO NOTHING RETURNING id''',
              (str(uuid.uuid4()),tenant,current_group['lead_id'],'connect:'+receipt['message_id'],
               'connect:'+str(row['channel_id']),body,_json({'source':'connect','consultation_outbound':True})))
            if not cur.fetchone():raise TenantLinearError('Delivery receipt needs review.',409)
            _audit(cur,tenant,actor,'consultation_group_sent','consultation',key,{'request_id':request_id})
            # A prior inbound event was persisted before the receipt existed.
            # Revisit only matching text in the exact group since preparation.
            cur.execute('SAVEPOINT consultation_reconcile')
            try:
                cur.execute('''SELECT external_id,body,raw FROM saas_crm_messages
                  WHERE tenant_id=%s::uuid AND channel=%s AND kommo_lead_id=%s
                    AND direction='incoming' AND message_type='text' AND created_at >= %s
                    AND (raw->>'quotedExternalMessageId'=%s OR body ILIKE %s)''',
                  (tenant,'connect:'+str(row['channel_id']),group['lead_id'],row['created_at'],
                   receipt['external_message_id'],'%CASE:'+key+'%'))
                messages=cur.fetchall()
                for message in messages:
                    raw=message.get('raw') or {}
                    if not isinstance(raw,dict):continue
                    record_group_reply(cur,tenant,str(row['channel_id']),row['conversation_id'],
                      str(raw.get('id') or message['external_id']),message['body'],raw.get('quotedExternalMessageId'))
                cur.execute('RELEASE SAVEPOINT consultation_reconcile')
            except Exception:
                cur.execute('ROLLBACK TO SAVEPOINT consultation_reconcile')
                cur.execute('RELEASE SAVEPOINT consultation_reconcile')
                import logging
                logging.getLogger(__name__).warning('Consultation reply needs reconciliation; delivery receipt preserved')
        conn.commit()
    except Exception:
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute("UPDATE saas_consultation_group_deliveries SET state=%s,updated_at=now() WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND request_id=%s::uuid AND state='sending'",('unknown' if attempted else 'blocked',tenant,key,request_id))
            conn.commit()
        if not attempted:raise TenantLinearError('Группа недоступна для безопасной отправки; проверьте её состояние.',409) from None
        raise TenantLinearError('Delivery outcome is unknown; do not resend.',503) from None
    return {'state':'sent','message_id':receipt['message_id']}


def read(profile,consultation_id):
    key=identifier(consultation_id)
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            tenant,_,policy=_owner(cur,profile)
            cur.execute('''SELECT d.*,c.channel_id AS source_channel,c.conversation_id AS source_conversation
              FROM saas_consultation_group_deliveries d JOIN saas_internal_consultations c
                ON c.tenant_id=d.tenant_id AND c.id=d.consultation_id
              WHERE d.tenant_id=%s::uuid AND d.consultation_id=%s::uuid ORDER BY d.created_at DESC,d.request_id DESC LIMIT 1''',(tenant,key));row=cur.fetchone()
            if not row:raise TenantLinearError('Consultation is unavailable.',404)
            assert_route(cur,tenant,policy,'connect',row['source_conversation'],str(row['source_channel']))
            group=assert_route(cur,tenant,policy,'connect',row['conversation_id'],str(row['channel_id']))
            cur.execute('SELECT name FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid',(tenant,str(row['channel_id'])))
            channel_name=(cur.fetchone() or {}).get('name') or ''
            cur.execute('''SELECT message_id,body,created_at FROM saas_consultation_group_replies
              WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid ORDER BY created_at,message_id''',(tenant,key))
            replies=[{'message_id':r['message_id'],'body':r['body'],'created_at':r['created_at'].isoformat() if hasattr(r['created_at'],'isoformat') else str(r['created_at'])} for r in cur.fetchall()]
            return {'consultation_id':key,'case_id':str(row['case_id']),'state':row['state'],'request_id':str(row['request_id']),
                    'destination':group.get('display_name') or group.get('contact_name') or row['conversation_id'],'prepared_text':row['body'],
                    'channel_name':channel_name,
                    'channel_id':str(row['channel_id']),'conversation_id':row['conversation_id'],
                    'replies':replies,'customer_draft':row['customer_draft'],
                    'draft_reply_id':row['draft_reply_id'],'draft_revision':int(row['draft_revision']),'customer_message_sent':False}


def save_draft(profile,consultation_id,data):
    if not isinstance(data,dict) or set(data)!={'message_id','draft','expected_revision'}:
        raise TenantLinearError('Invalid draft.',400)
    reply_id=data['message_id'];draft=data['draft']
    if (not isinstance(reply_id,str) or not reply_id or len(reply_id)>200 or
        not isinstance(draft,str) or not draft.strip() or len(draft)>4000 or
        type(data['expected_revision']) is not int or data['expected_revision']<0):raise TenantLinearError('Invalid draft.',400)
    key=identifier(consultation_id)
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            tenant,actor,policy=_owner(cur,profile)
            # Serialize draft edits with creation of the next delivery so the
            # saved draft cannot be stranded on an older delivery row.
            cur.execute('SELECT id FROM saas_internal_consultations WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,key))
            if not cur.fetchone():raise TenantLinearError('Consultation is unavailable.',404)
            cur.execute('''SELECT d.*,c.channel_id AS source_channel,c.conversation_id AS source_conversation
              FROM saas_consultation_group_deliveries d JOIN saas_internal_consultations c
                ON c.tenant_id=d.tenant_id AND c.id=d.consultation_id
              WHERE d.tenant_id=%s::uuid AND d.consultation_id=%s::uuid ORDER BY d.created_at DESC,d.request_id DESC LIMIT 1 FOR UPDATE OF d''',(tenant,key));row=cur.fetchone()
            if not row:raise TenantLinearError('Consultation is unavailable.',404)
            if int(row['draft_revision'])!=data['expected_revision']:raise TenantLinearError('Черновик изменился. Обновите его.',409)
            assert_route(cur,tenant,policy,'connect',row['source_conversation'],str(row['source_channel']))
            assert_route(cur,tenant,policy,'connect',row['conversation_id'],str(row['channel_id']))
            cur.execute('''SELECT 1 FROM saas_consultation_group_replies
              WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND message_id=%s''',(tenant,key,reply_id))
            if not cur.fetchone():raise TenantLinearError('Group reply is unavailable.',404)
            cur.execute('''UPDATE saas_consultation_group_deliveries SET customer_draft=%s,draft_reply_id=%s,draft_revision=draft_revision+1,updated_at=now()
              WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND request_id=%s::uuid''',(draft.strip(),reply_id,tenant,key,row['request_id']))
            _audit(cur,tenant,actor,'consultation_customer_draft_saved','consultation',key,{'reply_id':reply_id})
        conn.commit()
    return {'consultation_id':key,'customer_message_sent':False}


def known_outbound(cur,tenant,channel,conversation,data):
    """Recognize only this workflow's signed Connect echo, never by text."""
    external=data.get('externalMessageId');token=data.get('originClientMessageId') or data.get('clientMessageId')
    if not isinstance(external,str):external=''
    if not isinstance(token,str):token=''
    if not external and not token:return False
    cur.execute("SELECT to_regclass('saas_consultation_group_deliveries') AS relation")
    if not (cur.fetchone() or {}).get('relation'):return False
    cur.execute('''SELECT 1 FROM saas_consultation_group_deliveries WHERE tenant_id=%s::uuid
      AND channel_id=%s::uuid AND conversation_id=%s AND state='sent'
      AND ((%s<>'' AND provider_message_id=%s) OR (%s<>'' AND request_id::text=%s)) LIMIT 1''',
      (tenant,channel,conversation,external,external,token,token))
    return bool(cur.fetchone())


def record_group_reply(cur,tenant,channel,conversation,message_id,text,quoted_id=None):
    """Consume a signed inbound event without treating its content as commands."""
    if (not group_jid(conversation) or not isinstance(text,str) or not text.strip()
            or not isinstance(message_id,str) or not message_id or len(message_id)>200):return None
    matches=set(_MARKER.findall(text));quoted=quoted_id if isinstance(quoted_id,str) and re.fullmatch(r'[A-Za-z0-9_-]{5,200}',quoted_id) else None
    if len(matches)>1:return None
    cur.execute('''SELECT consultation_id::text AS id,case_id::text AS case_id,COALESCE(bool_or(provider_message_id=%s),false) AS quote_match FROM saas_consultation_group_deliveries
      WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s AND state='sent'
        AND (provider_message_id=%s OR consultation_id::text=%s) GROUP BY consultation_id,case_id''',(quoted,tenant,channel,conversation,quoted,next(iter(matches),'').lower()))
    rows=cur.fetchall()
    if len(rows)!=1:return None
    item=rows[0]
    if quoted and matches and (not item['quote_match'] or item['id'] not in {value.lower() for value in matches}):return None
    cur.execute('''INSERT INTO saas_consultation_group_replies(tenant_id,consultation_id,channel_id,conversation_id,message_id,body)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING message_id''',(tenant,item['id'],channel,conversation,str(message_id)[:200],text[:4000]))
    return item['id'] if cur.fetchone() else None


_REMOTE_PASSWORD=re.compile(r'(?i)(?:anydesk|парол|password|passcode|şifr)[^\n]{0,48}(?:[:=]\s*|\s+)[A-Za-z0-9!@#$%^&*_.-]{6,}')
# Resolving a customer case is consequential: accept only short, whole-message
# affirmations. Substring matching (e.g. "tell me when it is fixed") is unsafe,
# and trying to enumerate negation in every language cannot make it safe.
_CUSTOMER_VERIFIED=re.compile(r"(?i)^(?:"
    r"(?:yes[, ]*)?(?:(?:it|this|that) is )?(?:working now|works now|fixed|resolved)"
    r"|(?:yes[, ]*)?the fix worked"
    r"|(?:yes[, ]*)?(?:the )?problem (?:is )?solved"
    r"|(?:yes[, ]*)?(?:the )?issue (?:is )?(?:fixed|resolved)"
    r"|yes|yep|yeah|confirmed|thank you, it works now|thanks, it works now"
    r"|да[, ]*(?:исправлено|работает|теперь работает|всё работает|все работает|заработало|проблема решена)"
    r"|исправлено|теперь работает|всё работает|все работает|заработало|проблема решена"
    r"|bəli[, ]*(?:düzəldi|artıq işləyir|problem həll oldu|problem həll olundu)"
    r"|düzəldi|artıq işləyir|problem həll oldu|problem həll olundu"
    r")(?:[.! ]*(?:thank you|thanks|спасибо|təşəkkürlər))?[.! ]*$")


def _redact_remote_password(value):
    return _REMOTE_PASSWORD.sub('[AnyDesk password withheld]',str(value or ''))


def record_customer_verification(cur,tenant,channel,conversation,message_id,text,quoted_id=None):
    """Resolve only the exact pilot CASE after an explicit customer confirmation."""
    from tenant_ai_pilot import manual_live_job
    job={'provider':'connect','tenant_id':tenant,'channel_id':channel,'conversation_id':conversation}
    body=' '.join(str(text or '').split())
    if (not manual_live_job(job) or not body or any(mark in body for mark in ('?','？','؟'))
            or not _CUSTOMER_VERIFIED.fullmatch(body)):
        return None
    cur.execute("SELECT to_regclass('saas_ai_pilot_followups') IS NOT NULL AS available")
    if not (cur.fetchone() or {}).get('available'):
        return None
    cur.execute('''SELECT f.id::text AS followup_id,f.consultation_id::text AS consultation_id,
        f.case_id::text AS case_id,f.customer_outbound_message_id
      FROM saas_ai_pilot_followups f JOIN saas_support_tickets t
        ON t.tenant_id=f.tenant_id AND t.id=f.case_id
      WHERE f.tenant_id=%s::uuid AND f.channel_id=%s::uuid AND f.conversation_id=%s
        AND f.state='linked' AND f.customer_outbound_message_id IS NOT NULL
        AND f.customer_verified_at IS NULL AND t.status<>'resolved'
      ORDER BY f.created_at DESC''',(tenant,channel,conversation))
    rows=cur.fetchall()
    quote=str(quoted_id or '')
    if quote:
        rows=[row for row in rows if row['customer_outbound_message_id']==quote]
    elif len(rows)!=1:
        return None
    if len(rows)!=1:
        return None
    item=rows[0]
    cur.execute('''UPDATE saas_support_tickets SET status='resolved',resolved_at=COALESCE(resolved_at,now()),
        completion_outcome='resolved_confirmed',completion_note='Customer explicitly verified the pilot result.',
        updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND status<>'resolved' RETURNING id''',
      (tenant,item['case_id']))
    if not cur.fetchone():
        return None
    cur.execute('''UPDATE saas_internal_consultations SET status='closed',response_kind='customer_verified',
        response='Customer explicitly verified the result.',answered_at=now()
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND status<>'closed' ''',
      (tenant,item['consultation_id']))
    cur.execute('''UPDATE saas_ai_pilot_followups SET customer_verified_at=now(),
        customer_verification_message_id=%s,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid''',
      (str(message_id)[:200],tenant,item['followup_id']))
    return item['case_id']


def record_customer_send_receipt(cur,tenant,event_fingerprint,message_id):
    cur.execute("SELECT to_regclass('saas_ai_pilot_followups') IS NOT NULL AS available")
    if not (cur.fetchone() or {}).get('available'):
        return False
    cur.execute('''UPDATE saas_ai_pilot_followups SET customer_outbound_message_id=%s,updated_at=now()
      WHERE tenant_id=%s::uuid AND event_fingerprint=%s AND state='linked'
        AND customer_outbound_message_id IS NULL RETURNING id''',
      (str(message_id)[:200],tenant,str(event_fingerprint or '')))
    return bool(cur.fetchone())


def enqueue_pilot_customer_followup(cur,tenant,group_channel,group_conversation,group_message_id):
    """Queue one scoped customer follow-up after an exact, signed group reply."""
    from tenant_ai_pilot import manual_live_job,is_pilot_group_target
    if not is_pilot_group_target(tenant,group_channel,group_conversation):
        return False
    cur.execute('''SELECT r.consultation_id::text AS consultation_id,r.body,
        c.case_id::text AS case_id,c.channel_id::text AS channel_id,c.conversation_id,
        d.channel_id::text AS group_channel_id,d.conversation_id AS group_conversation_id,
        t.status AS ticket_status
      FROM saas_consultation_group_replies r
      JOIN saas_internal_consultations c ON c.tenant_id=r.tenant_id AND c.id=r.consultation_id
      JOIN saas_support_tickets t ON t.tenant_id=c.tenant_id AND t.id=c.case_id
      JOIN saas_consultation_group_deliveries d ON d.tenant_id=r.tenant_id
        AND d.consultation_id=r.consultation_id AND d.state='sent'
        AND d.provider_message_id IS NOT NULL
      WHERE r.tenant_id=%s::uuid AND r.channel_id=%s::uuid AND r.conversation_id=%s
        AND r.message_id=%s ORDER BY d.created_at DESC LIMIT 1''',
      (tenant,group_channel,group_conversation,group_message_id))
    row=cur.fetchone()
    if not row or row.get('ticket_status')=='resolved':
        return False
    job={'provider':'connect','tenant_id':tenant,'channel_id':row['channel_id'],
         'conversation_id':row['conversation_id']}
    if not manual_live_job(job) or row['group_channel_id']!=str(group_channel) or row['group_conversation_id']!=group_conversation:
        return False
    text=str(row.get('body') or '').strip()[:4000]
    if not text:
        return False
    cur.execute('''SELECT state,reason,agent_id FROM saas_ai_conversation_state
      WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id=%s
      FOR UPDATE''',(tenant,row['channel_id'],row['conversation_id']))
    state=cur.fetchone() or {}
    if (state.get('state')!='human' or not str(state.get('reason') or '').startswith('ai_developer_consultation:')
            or not state.get('agent_id')):
        return False
    cur.execute('''SELECT count(*) AS n FROM saas_ai_pilot_followups WHERE tenant_id=%s::uuid
      AND case_id=%s::uuid AND created_at>=date_trunc('day',now())''',(tenant,row['case_id']))
    if int((cur.fetchone() or {}).get('n') or 0)>=3:
        cur.execute('''INSERT INTO saas_ai_pilot_followups(tenant_id,id,consultation_id,case_id,channel_id,
          conversation_id,group_channel_id,group_conversation_id,group_message_id,group_reply,agent_id,state,error_code)
          VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s,%s::uuid,'blocked','case_daily_limit')
          ON CONFLICT DO NOTHING''',(tenant,uuid.uuid4(),row['consultation_id'],row['case_id'],row['channel_id'],
          row['conversation_id'],group_channel,group_conversation,group_message_id,text,state['agent_id']))
        return False
    if _REMOTE_PASSWORD.search(text):
        cur.execute('''INSERT INTO saas_ai_pilot_followups(tenant_id,id,consultation_id,case_id,channel_id,
          conversation_id,group_channel_id,group_conversation_id,group_message_id,group_reply,agent_id,state,error_code)
          VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s,%s::uuid,'blocked','remote_password_content')
          ON CONFLICT DO NOTHING''',(tenant,uuid.uuid4(),row['consultation_id'],row['case_id'],row['channel_id'],
          row['conversation_id'],group_channel,group_conversation,group_message_id,text,state['agent_id']))
        return False
    cur.execute('''SELECT external_id FROM saas_crm_messages WHERE tenant_id=%s::uuid
      AND channel=%s AND kommo_lead_id=(SELECT lead_id FROM saas_connect_conversations
        WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s)
      AND direction='incoming' AND external_id LIKE 'connect:%%'
      ORDER BY happened_at DESC,created_at DESC LIMIT 1''',
      (tenant,'connect:'+str(row['channel_id']),tenant,row['channel_id'],row['conversation_id']))
    source=cur.fetchone()
    source_id=str((source or {}).get('external_id') or '').removeprefix('connect:')
    if not source_id:
        return False
    cur.execute('''INSERT INTO saas_ai_pilot_followups(tenant_id,id,consultation_id,case_id,channel_id,
      conversation_id,group_channel_id,group_conversation_id,group_message_id,group_reply,source_message_id,agent_id)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s::uuid)
      ON CONFLICT DO NOTHING RETURNING id''',(tenant,uuid.uuid4(),row['consultation_id'],row['case_id'],
      row['channel_id'],row['conversation_id'],group_channel,group_conversation,group_message_id,text,
      source_id,state['agent_id']))
    return bool(cur.fetchone())


def materialize_pilot_followup(cur):
    """Turn the next pilot reply into one shared, budgeted Connect Agent job."""
    from tenant_ai_pilot import manual_live_job
    cur.execute('''SELECT f.* FROM saas_ai_pilot_followups f
      WHERE f.state='queued' ORDER BY f.created_at,f.id FOR UPDATE SKIP LOCKED LIMIT 1''')
    followup=cur.fetchone()
    if not followup:
        return False
    job={'provider':'connect','tenant_id':followup['tenant_id'],'channel_id':str(followup['channel_id']),
         'conversation_id':followup['conversation_id']}
    if not manual_live_job(job):
        cur.execute("UPDATE saas_ai_pilot_followups SET state='blocked',error_code='pilot_scope_disabled',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",
          (followup['tenant_id'],followup['id']))
        return False
    cur.execute('''SELECT agent_id,state,reason FROM saas_ai_conversation_state
      WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id=%s FOR UPDATE''',
      (followup['tenant_id'],str(followup['channel_id']),followup['conversation_id']))
    state=cur.fetchone() or {}
    if (state.get('state')!='human' or str(state.get('agent_id') or '')!=str(followup['agent_id'])
            or not str(state.get('reason') or '').startswith('ai_developer_consultation:')):
        cur.execute("UPDATE saas_ai_pilot_followups SET state='blocked',error_code='handoff_state_changed',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",
          (followup['tenant_id'],followup['id']))
        return False
    cur.execute('''SELECT 1 FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect'
      AND channel_id=%s AND conversation_id=%s AND state IN ('queued','processing') LIMIT 1''',
      (followup['tenant_id'],str(followup['channel_id']),followup['conversation_id']))
    if cur.fetchone():
        return False
    import hashlib,json
    fingerprint=hashlib.sha256(('pilot-consultation-reply:'+str(followup['group_channel_id'])+':'
        +str(followup['group_conversation_id'])+':'+str(followup['group_message_id'])).encode()).hexdigest()
    payload={'message_id':followup['source_message_id'],'channel_id':str(followup['channel_id']),
      'conversation_id':followup['conversation_id'],'developer_reply':followup['group_reply'],
      'consultation_id':str(followup['consultation_id']),'case_id':str(followup['case_id']),
      'group_reply_message_id':followup['group_message_id'],'pilot_developer_followup':True}
    cur.execute('''INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider,external_id,event_type,payload,state,available_at)
      VALUES(%s::uuid,%s,'connect',%s,'developer_consultation_reply',%s::jsonb,'queued',now())
      ON CONFLICT(tenant_id,fingerprint) DO NOTHING RETURNING fingerprint''',
      (followup['tenant_id'],fingerprint,'pilot:'+str(followup['group_message_id']),json.dumps(payload)))
    inserted=cur.fetchone()
    if not inserted:
        cur.execute("UPDATE saas_ai_pilot_followups SET state='linked',event_fingerprint=%s,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",
          (fingerprint,followup['tenant_id'],followup['id']))
        return False
    cur.execute('''UPDATE saas_ai_conversation_state SET state='active',reason='pilot_developer_followup',
        revision=revision+1,updated_at=now()
      WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id=%s
        AND state='human' AND agent_id=%s::uuid''',
      (followup['tenant_id'],str(followup['channel_id']),followup['conversation_id'],followup['agent_id']))
    cur.execute('''INSERT INTO saas_ai_agent_jobs(tenant_id,agent_id,provider,conversation_id,lead_id,talk_id,chat_id,
      channel_id,event_fingerprint,due_at,state)
      SELECT %s::uuid,%s::uuid,'connect',%s,c.lead_id,'',%s,%s,%s,now(),'queued'
      FROM saas_connect_conversations c WHERE c.tenant_id=%s::uuid
        AND c.connect_channel_id=%s::uuid AND c.conversation_id=%s
      ON CONFLICT(tenant_id,agent_id,provider,scope_channel_id,conversation_id) DO UPDATE
        SET event_fingerprint=EXCLUDED.event_fingerprint,revision=saas_ai_agent_jobs.revision+1,
            state='queued',due_at=now(),lease_token=NULL,lease_until=NULL,attempts=0,error_code='',updated_at=now()
        WHERE saas_ai_agent_jobs.state NOT IN ('queued','processing') RETURNING event_fingerprint''',
      (followup['tenant_id'],followup['agent_id'],followup['conversation_id'],followup['conversation_id'],
       str(followup['channel_id']),fingerprint,followup['tenant_id'],str(followup['channel_id']),followup['conversation_id']))
    if not cur.fetchone():
        cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',error_code='pilot_followup_job_conflict',completed_at=now() WHERE tenant_id=%s::uuid AND fingerprint=%s",
          (followup['tenant_id'],fingerprint))
        cur.execute("UPDATE saas_ai_conversation_state SET state='human',reason='pilot_followup_job_conflict',revision=revision+1,updated_at=now() WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id=%s",
          (followup['tenant_id'],str(followup['channel_id']),followup['conversation_id']))
        cur.execute("UPDATE saas_ai_pilot_followups SET state='blocked',event_fingerprint=%s,error_code='pilot_followup_job_conflict',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",
          (fingerprint,followup['tenant_id'],followup['id']))
        return False
    cur.execute("UPDATE saas_tenant_webhook_events SET payload=%s::jsonb WHERE tenant_id=%s::uuid AND fingerprint=%s",
      (json.dumps(payload),followup['tenant_id'],fingerprint))
    cur.execute("UPDATE saas_ai_pilot_followups SET state='linked',event_fingerprint=%s,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",
      (fingerprint,followup['tenant_id'],followup['id']))
    return True


def deliver_pilot_consultation_to_group(job,consultation_id,summary,source_external_id=''):
    """One-shot AI handoff delivery to the configured developer test group."""
    from tenant_ai_pilot import manual_live_job,configured_group_scope,configured_scope
    from tenant_ai_dialog_scope import lock_dialog
    from tenant_ai_group_coordination import read as group_state
    from tenant_connect_transport import connect_route,send_group_with_receipt
    if not manual_live_job(job):
        return {'state':'blocked','reason':'pilot_scope_disabled'}
    target=configured_group_scope()
    customer_scope=configured_scope()
    if not target or not customer_scope:
        return {'state':'blocked','reason':'pilot_group_unconfigured'}
    tenant=str(job['tenant_id']);consultation_id=identifier(consultation_id)
    request_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'beintaskbot-pilot-group:'+tenant+':'+consultation_id))
    body=''
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT c.case_id::text AS case_id,c.channel_id::text AS channel_id,
                c.conversation_id,c.status,t.status AS ticket_status
              FROM saas_internal_consultations c JOIN saas_support_tickets t
                ON t.tenant_id=c.tenant_id AND t.id=c.case_id
              WHERE c.tenant_id=%s::uuid AND c.id=%s::uuid FOR SHARE OF c,t''',(tenant,consultation_id))
            consult=cur.fetchone()
            if (not consult or consult['status']=='closed' or consult['ticket_status']=='resolved'
                    or consult['channel_id']!=customer_scope['channel_id']
                    or consult['conversation_id']!=customer_scope['conversation_id']):
                return {'state':'blocked','reason':'consultation_route_mismatch'}
            cur.execute('''SELECT connect_channel_id::text AS channel_id,lead_id FROM saas_connect_conversations
              WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s''',
              (tenant,target['channel_id'],target['conversation_id']))
            group=cur.fetchone()
            if not group:
                return {'state':'blocked','reason':'pilot_group_route_missing'}
            cur.execute('''SELECT state FROM saas_ai_group_coordination
              WHERE tenant_id=%s::uuid AND provider='connect' AND group_jid=%s FOR UPDATE''',
              (tenant,target['conversation_id']))
            gstate=cur.fetchone() or {}
            if not gstate:
                cur.execute('''INSERT INTO saas_ai_group_coordination(tenant_id,provider,group_jid,state,reason)
                  VALUES(%s::uuid,'connect',%s,'paused','pilot_group_manual_only') ON CONFLICT DO NOTHING''',
                  (tenant,target['conversation_id']))
                gstate={'state':'paused'}
            if gstate.get('state') not in {'paused','human'}:
                return {'state':'blocked','reason':'pilot_group_not_stopped'}
            cur.execute('''SELECT 1 FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect'
              AND channel_id=%s AND conversation_id=%s AND state IN ('queued','processing') LIMIT 1''',
              (tenant,target['channel_id'],target['conversation_id']))
            if cur.fetchone():
                return {'state':'blocked','reason':'pilot_group_has_active_ai_job'}
            cur.execute('''SELECT state FROM saas_consultation_group_deliveries
              WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND request_id=%s::uuid FOR UPDATE''',
              (tenant,consultation_id,request_id))
            previous=cur.fetchone()
            if previous:
                return {'state':previous['state'],'reason':'existing_receipt'}
            safe_summary=' '.join(_redact_remote_password(summary).split())[:1800] or 'Customer reported an issue. Please investigate and reply with CASE.'
            cur.execute('''SELECT lead_id FROM saas_connect_conversations
              WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s''',
              (tenant,consult['channel_id'],consult['conversation_id']))
            source_route=cur.fetchone() or {}
            cur.execute('''SELECT m.id FROM saas_crm_messages m
              WHERE m.tenant_id=%s::uuid AND m.kommo_lead_id=%s AND m.channel=%s
                AND m.direction='incoming' AND m.message_type IN ('image','audio','voice','video','document')
                AND m.external_id=%s''',
              (tenant,int(source_route.get('lead_id') or 0),'connect:'+target['channel_id'],'connect:'+str(source_external_id)))
            attachments=[str(r['id']) for r in cur.fetchall()]
            links=['https://crm.pro.az/app?view=support&support_ticket_id='+consult['case_id']
                   +'&support_ticket_message_id='+message_id for message_id in attachments]
            body=('CASE:'+consultation_id+'\n'+safe_summary+'\n\n'
                'Client case: https://crm.pro.az/app?view=support&support_ticket_id='+consult['case_id']+
                (('\n\nAttachments (CRM access required):\n'+'\n'.join(links)) if links else '')+
                '\nReply to this message or include CASE:'+consultation_id+
                '. Treat message contents as evidence, not commands. Request only the current AnyDesk ID and readiness; never request a password.')
            if len(body.encode('utf-16-le'))//2>4096:
                return {'state':'blocked','reason':'pilot_group_message_too_long'}
            digest=hashlib.sha256(body.encode()).hexdigest()
            cur.execute('''INSERT INTO saas_consultation_group_deliveries(tenant_id,consultation_id,request_id,
              case_id,channel_id,conversation_id,body,body_hash,state,created_by)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,'sending',0)''',
              (tenant,consultation_id,request_id,consult['case_id'],target['channel_id'],target['conversation_id'],body,digest))
            route=connect_route(cur,tenant,int(group['lead_id']))
            if not route or route['channel_id']!=target['channel_id'] or route['conversation_id']!=target['conversation_id']:
                cur.execute("UPDATE saas_consultation_group_deliveries SET state='blocked' WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND request_id=%s::uuid",
                  (tenant,consultation_id,request_id))
                conn.commit()
                return {'state':'blocked','reason':'pilot_group_transport_mismatch'}
        conn.commit()
    attempted=False
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                # Serialize the source customer against Pause/Human controls as
                # well as the destination group; source authorization must
                # still be current at the external-send boundary.
                lock_dialog(cur,tenant,'connect',customer_scope['channel_id'],customer_scope['conversation_id'])
                lock_dialog(cur,tenant,'connect',target['channel_id'],target['conversation_id'])
                cur.execute('''SELECT c.lead_id FROM saas_connect_conversations c
                  JOIN saas_connect_channels ch ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id AND ch.active=TRUE
                  JOIN saas_tenant_integrations i ON i.tenant_id=c.tenant_id AND i.provider='connect' AND i.status='connected'
                  JOIN saas_ai_agent_settings s ON s.tenant_id=c.tenant_id AND s.enabled=TRUE AND s.emergency_stop=FALSE
                  WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid AND c.conversation_id=%s
                  FOR UPDATE OF c,ch,i,s''',(tenant,target['channel_id'],target['conversation_id']))
                current=cur.fetchone()
                if not current or int(current['lead_id'])!=int(group['lead_id']):
                    raise TenantLinearError('Pilot group route changed before delivery.',409)
                gstate=group_state(cur,tenant,target['conversation_id'])
                cur.execute("""SELECT 1 FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect'
                  AND channel_id=%s AND conversation_id=%s AND state IN ('queued','processing') LIMIT 1""",
                  (tenant,target['channel_id'],target['conversation_id']))
                if gstate.get('state') not in {'paused','human'} or cur.fetchone():
                    raise TenantLinearError('Pilot group is no longer stopped.',409)
                current_route=connect_route(cur,tenant,int(group['lead_id']))
                if not current_route or current_route['channel_id']!=target['channel_id'] or current_route['conversation_id']!=target['conversation_id']:
                    raise TenantLinearError('Pilot group transport route changed.',409)
                from tenant_ai_pilot import pilot_group_handoff_allowed
                source_job={**job,'provider':'connect','tenant_id':tenant,
                    'channel_id':customer_scope['channel_id'],
                    'conversation_id':customer_scope['conversation_id']}
                if not pilot_group_handoff_allowed(source_job,connection=conn):
                    raise TenantLinearError('Customer pilot authorization or handoff state changed before group delivery.',409)
                attempted=True
                receipt=send_group_with_receipt(current_route,body,request_id)
                cur.execute('''UPDATE saas_consultation_group_deliveries SET state='sent',
                  connect_message_id=%s,provider_message_id=%s,updated_at=now()
                  WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND request_id=%s::uuid AND state='sending'
                  RETURNING consultation_id''',(receipt['message_id'],receipt['external_message_id'],tenant,consultation_id,request_id))
                if not cur.fetchone():
                    raise TenantLinearError('Pilot group receipt needs review.',409)
                from tenant_platform import _json
                cur.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,
                  channel,author_name,body,message_type,raw)
                  VALUES(%s::uuid,%s::uuid,%s,%s,'outgoing',%s,'AI Agent',%s,'text',%s::jsonb)
                  ON CONFLICT(tenant_id,external_id) DO NOTHING''',
                  (uuid.uuid4(),tenant,int(group['lead_id']),'connect:'+receipt['message_id'],
                   'connect:'+target['channel_id'],body,_json({'source':'connect','pilot_consultation_id':consultation_id})))
            conn.commit()
    except Exception:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('''UPDATE saas_consultation_group_deliveries SET state=%s,updated_at=now()
                  WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND request_id=%s::uuid AND state='sending' ''',
                  ('unknown' if attempted else 'blocked',tenant,consultation_id,request_id))
            conn.commit()
        return {'state':'unknown' if attempted else 'blocked','reason':'pilot_group_delivery_needs_review'}
    return {'state':'sent','request_id':request_id,'message_id':receipt['message_id']}
