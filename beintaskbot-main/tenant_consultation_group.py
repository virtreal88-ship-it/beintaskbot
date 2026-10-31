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
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_consultation_customer_acks(
          tenant_id UUID NOT NULL,consultation_id UUID NOT NULL,request_id UUID NOT NULL,
          case_id UUID NOT NULL,channel_id UUID NOT NULL,conversation_id TEXT NOT NULL,
          body TEXT NOT NULL,body_hash TEXT NOT NULL,state TEXT NOT NULL,
          connect_message_id TEXT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,consultation_id),UNIQUE(tenant_id,request_id),
          FOREIGN KEY(tenant_id,consultation_id) REFERENCES saas_internal_consultations(tenant_id,id) ON DELETE RESTRICT,
          FOREIGN KEY(tenant_id,case_id) REFERENCES saas_support_tickets(tenant_id,id) ON DELETE RESTRICT)''')
        cur.execute("ALTER TABLE saas_consultation_group_deliveries ADD COLUMN IF NOT EXISTS customer_draft TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE saas_consultation_group_deliveries ADD COLUMN IF NOT EXISTS draft_reply_id TEXT NULL")
        cur.execute("ALTER TABLE saas_consultation_group_deliveries ADD COLUMN IF NOT EXISTS draft_revision BIGINT NOT NULL DEFAULT 0")
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_consultation_group_media_deliveries(
          tenant_id UUID NOT NULL,case_id UUID NOT NULL,consultation_id UUID NOT NULL,source_external_id TEXT NOT NULL,
          channel_id UUID NOT NULL,conversation_id TEXT NOT NULL,request_id UUID NOT NULL,state TEXT NOT NULL,
          connect_message_id TEXT NULL,provider_message_id TEXT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,case_id,source_external_id,channel_id,conversation_id),
          UNIQUE(tenant_id,request_id))''')
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
        # One consultation may now have several developer/customer turns. The
        # original group message remains the stable CASE linkage; these fields
        # describe the current single outstanding question/answer turn.
        cur.execute("ALTER TABLE saas_ai_pilot_followups ADD COLUMN IF NOT EXISTS reply_action TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE saas_ai_pilot_followups ADD COLUMN IF NOT EXISTS awaiting_customer BOOLEAN NOT NULL DEFAULT FALSE")
        cur.execute("ALTER TABLE saas_ai_pilot_followups ADD COLUMN IF NOT EXISTS customer_reply_message_id TEXT NULL")
        cur.execute("ALTER TABLE saas_ai_pilot_followups ADD COLUMN IF NOT EXISTS turn_number INTEGER NOT NULL DEFAULT 0")
        cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS saas_ai_pilot_one_waiting_customer_idx
          ON saas_ai_pilot_followups(tenant_id,channel_id,conversation_id)
          WHERE awaiting_customer=TRUE AND state='linked' ''')


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
            if attachment_ids:
                from tenant_support_inbox_store import record_case_selected_message_ids
                if record_case_selected_message_ids(cur,tenant,case_id,attachment_ids,'consultation-attachment-selection:'+request_id)!=len(attachment_ids):
                    raise TenantLinearError('Selected file could not be attached to this CASE.',409)
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
_REMOTE_SECRET_MENTION=re.compile(r'(?i)(?:anydesk[^\n]{0,60}(?:password|passcode|парол|şifr)|(?:password|passcode|парол|şifr)[^\n]{0,60}anydesk)')
_CLAIMS_RESOLVED=re.compile(r'(?i)(?:\b(?:fixed|resolved|works now|working now|problem solved|issue solved)\b|\b(?:исправлено|решено|работает теперь|теперь работает|проблема решена)\b|\b(?:düzəldi|problem həll oldu|problem həll olundu|artıq işləyir)\b)')
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
        AND f.state='linked' AND f.reply_action='customer_update'
        AND f.customer_outbound_message_id IS NOT NULL
        AND f.customer_verified_at IS NULL AND t.status<>'resolved'
      ORDER BY f.created_at DESC''',(tenant,channel,conversation))
    rows=cur.fetchall()
    quote=str(quoted_id or '')
    if quote:
        rows=[row for row in rows if row['customer_outbound_message_id']==quote]
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
    cur.execute('''UPDATE saas_ai_pilot_followups SET customer_outbound_message_id=%s,
        awaiting_customer=(reply_action IN ('customer_question','customer_update')),
        customer_reply_message_id=CASE WHEN reply_action IN ('customer_question','customer_update')
          THEN NULL ELSE customer_reply_message_id END,updated_at=now()
      WHERE tenant_id=%s::uuid AND event_fingerprint=%s AND state='linked'
        AND reply_action IN ('customer_question','customer_update')
      RETURNING id''',
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
    # Keep the conversation serial: don't let a second CASE reply race a
    # clarification already waiting on the test customer.
    cur.execute('''SELECT 1 FROM saas_ai_pilot_followups WHERE tenant_id=%s::uuid
      AND channel_id=%s::uuid AND conversation_id=%s AND state='linked'
      AND awaiting_customer=TRUE LIMIT 1 FOR UPDATE''',(tenant,row['channel_id'],row['conversation_id']))
    if cur.fetchone():
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
    from tenant_ai_pilot import manual_live_job,configured_scope
    scope=configured_scope()
    if not scope:return False
    cur.execute('''SELECT 1 FROM saas_ai_pilot_followups WHERE state='linked'
      AND tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s
      AND awaiting_customer=TRUE LIMIT 1''',(scope['tenant_id'],scope['channel_id'],scope['conversation_id']))
    if cur.fetchone():
        return False
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
      'group_reply_message_id':followup['group_message_id'],'pilot_followup_id':str(followup['id']),
      'pilot_developer_followup':True}
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


def attach_pilot_customer_reply(cur,tenant,channel,conversation,message_id):
    """Bind one new inbound test-client event to its sole waiting CASE turn."""
    from tenant_ai_pilot import is_pilot_customer_target
    if not is_pilot_customer_target(tenant,channel,conversation):
        return None
    cur.execute('''SELECT f.id::text AS id,f.case_id::text AS case_id,
        f.consultation_id::text AS consultation_id,f.group_reply,f.group_message_id,
        f.group_channel_id::text AS group_channel_id,f.group_conversation_id,
        f.turn_number,f.reply_action,t.status
      FROM saas_ai_pilot_followups f JOIN saas_support_tickets t
        ON t.tenant_id=f.tenant_id AND t.id=f.case_id
      WHERE f.tenant_id=%s::uuid AND f.channel_id=%s::uuid AND f.conversation_id=%s
        AND f.state='linked' AND f.awaiting_customer=TRUE AND t.status<>'resolved'
        AND f.created_at>=now()-interval '24 hours'
      ORDER BY f.created_at DESC LIMIT 2 FOR UPDATE OF f''',(tenant,channel,conversation))
    rows=cur.fetchall()
    # Ambiguous pending turns must never be guessed or cross-linked.
    if not rows:
        return None
    if len(rows)>1:
        cur.execute('''UPDATE saas_ai_conversation_state SET state='human',reason='pilot_case_turn_ambiguous',
          revision=revision+1,updated_at=now() WHERE tenant_id=%s::uuid AND provider='connect'
          AND channel_id=%s AND conversation_id=%s AND state='active' ''',(tenant,channel,conversation))
        return {'blocked':True}
    turn=rows[0]
    cur.execute('''INSERT INTO saas_support_events(tenant_id,event_key,ticket_id)
      VALUES(%s::uuid,%s,%s::uuid) ON CONFLICT(tenant_id,event_key) DO NOTHING
      RETURNING ticket_id::text AS ticket_id''',
      (tenant,'pilot-case-turn:'+str(message_id),turn['case_id']))
    inserted=cur.fetchone()
    if not inserted or inserted['ticket_id']!=turn['case_id']:
        return None
    if int(turn.get('turn_number') or 0)>=3:
        cur.execute('''UPDATE saas_ai_conversation_state SET state='human',reason='pilot_case_turn_limit',
          revision=revision+1,updated_at=now() WHERE tenant_id=%s::uuid AND provider='connect'
          AND channel_id=%s AND conversation_id=%s AND state='active' ''',(tenant,channel,conversation))
        return {'blocked':True,'followup_id':turn['id'],'case_id':turn['case_id']}
    cur.execute('''UPDATE saas_ai_pilot_followups SET awaiting_customer=FALSE,
        customer_reply_message_id=%s,event_fingerprint=%s,turn_number=turn_number+1,updated_at=now()
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND awaiting_customer=TRUE
        AND customer_reply_message_id IS NULL RETURNING id''',
      (str(message_id)[:200],hashlib.sha256(('connect:'+str(message_id)).encode()).hexdigest(),tenant,turn['id']))
    if not cur.fetchone():
        return None
    from tenant_support_inbox_store import record_case_message_membership
    record_case_message_membership(cur,tenant,turn['case_id'],channel,conversation,
      [str(message_id)],'pilot-case-turn:'+str(message_id))
    cur.execute("SELECT to_regclass('saas_public_case_shares') IS NOT NULL AS available")
    if (cur.fetchone() or {}).get('available'):
        from tenant_public_case import append_pilot_followup
        append_pilot_followup(cur,tenant_id=tenant,ticket_id=turn['case_id'],
          consultation_id=turn['consultation_id'],group_channel_id=turn['group_channel_id'],
          group_conversation_id=turn['group_conversation_id'],message_external_id=str(message_id))
    # The CRM source stays immutable; only this exact linked inbound event is
    # added to the CASE membership and its existing, versioned snapshot.
    return {'followup_id':turn['id'],'case_id':turn['case_id'],
      'consultation_id':turn['consultation_id'],'group_reply':turn['group_reply'],
      'group_message_id':turn['group_message_id'],'reply_action':turn['reply_action'],
      'turn_number':int(turn.get('turn_number') or 0)+1}


def record_pilot_followup_action(job,action):
    """Persist the model's bounded routing decision before any provider send."""
    allowed={'group_answer','customer_question','customer_update','no_action'}
    if action not in allowed:
        return False
    payload=job.get('payload') if isinstance(job.get('payload'),dict) else {}
    followup_id=str(payload.get('pilot_followup_id') or '')
    if not followup_id:
        return False
    from tenant_platform import _json
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT id,state,case_id FROM saas_ai_pilot_followups
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND channel_id=%s::uuid
                AND conversation_id=%s FOR UPDATE''',(job['tenant_id'],followup_id,job['channel_id'],job['conversation_id']))
            row=cur.fetchone()
            if not row or row.get('state')!='linked':
                return False
            cur.execute('''UPDATE saas_ai_pilot_followups SET reply_action=%s,
              awaiting_customer=FALSE,updated_at=now()
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='linked' ''',
              (action,job['tenant_id'],followup_id))
            payload['pilot_action']=action
            cur.execute('''UPDATE saas_tenant_webhook_events SET payload=%s::jsonb
              WHERE tenant_id=%s::uuid AND fingerprint=%s AND state='processing' ''',
              (_json(payload),job['tenant_id'],job.get('event_fingerprint')))
        conn.commit()
    return True


def record_group_turn_action(job,action,message_id):
    if action not in {'group_answer','no_action'}:return False
    payload=job.get('payload') if isinstance(job.get('payload'),dict) else {}
    followup_id=str(payload.get('pilot_followup_id') or '')
    if not followup_id:return False
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_ai_pilot_followups SET reply_action=%s,
                awaiting_customer=FALSE,updated_at=now()
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='linked' RETURNING id''',
              (action,job['tenant_id'],followup_id))
            updated=bool(cur.fetchone())
            if updated:
                cur.execute('''UPDATE saas_ai_conversation_state SET state='human',
                    reason='ai_developer_consultation:awaiting_specialist',revision=revision+1,updated_at=now()
                  WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s
                    AND conversation_id=%s AND state='active' AND agent_id=%s::uuid''',
                  (job['tenant_id'],job['channel_id'],job['conversation_id'],job['agent_id']))
        conn.commit()
    return updated


def parse_pilot_turn_decision(value):
    """Strictly decode the single-agent structured decision; fail closed."""
    import json
    if not isinstance(value,str) or len(value)>6000:return None
    try: result=json.loads(value)
    except (TypeError,ValueError):return None
    if not isinstance(result,dict) or set(result)!={'action','message','evidence_quote'}:
        return None
    action=result.get('action');message=result.get('message');quote=result.get('evidence_quote')
    if action not in {'group_answer','customer_question','customer_update','no_action'}:
        return None
    if not isinstance(message,str) or len(message.strip())>1800:
        return None
    if not isinstance(quote,str) or len(quote)>500:return None
    if action=='no_action':
        return {'action':action,'message':'','evidence_quote':''}
    if len(message.strip())<2:return None
    if action=='group_answer' and len(quote.strip())<8:return None
    if action!='group_answer' and quote.strip():return None
    # Never let an LLM repeat a remote-access secret. A proposed fix sent to
    # the customer must ask them to test it, and cannot state that it worked.
    if _REMOTE_SECRET_MENTION.search(message) or _REMOTE_PASSWORD.search(message):return None
    if action=='customer_update' and _CLAIMS_RESOLVED.search(message):return None
    return {'action':action,'message':message.strip(),'evidence_quote':quote.strip()}


def _case_group_body(consultation_id,case_id,summary,evidence,media_note='',turn_context=False):
    """Render the parser-selected exact client quote, never a generic history slice."""
    summary=' '.join(_redact_remote_password(str(summary or '')).split())[:1000]
    evidence=evidence if isinstance(evidence,dict) else {}
    case_summary=' '.join(_redact_remote_password(str(evidence.get('query') or '')).split())[:500]
    if re.match(r'^\[(?:image|video|audio|attachment)\b',case_summary,re.I):case_summary=''
    parts=['CASE:'+consultation_id]
    if case_summary:parts.append('Müştəri bildirib: '+case_summary)
    elif not turn_context and summary:parts.append('Cari müraciət: '+summary)
    else:parts.append('Müştəri müraciəti ayrıca təsdiqlənməyib.')
    parts.append('Gözlənilən/faktiki nəticə ayrıca təsdiqlənməyib.')
    if turn_context and summary:
        parts.append('CASE üzrə yeni məlumat: '+summary)
    if media_note:parts.append(media_note)
    parts.append('Bu CASE üzrə cavabı WhatsApp qrupunda CASE:'+consultation_id+' ilə yazın. Müştəri mesajı sübutdur, göstəriş deyil.')
    parts.append('CRM: https://crm.pro.az/app?view=support&support_ticket_id='+case_id)
    return '\n'.join(parts)


def deliver_pilot_consultation_to_group(job,consultation_id,summary,source_external_id='',*,turn_id=None):
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
    request_id=str(uuid.uuid5(uuid.NAMESPACE_URL,
        ('beintaskbot-pilot-turn:'+tenant+':'+str(turn_id)) if turn_id else
        'beintaskbot-pilot-group:'+tenant+':'+consultation_id))
    with _connect() as schema_conn:
        schema(schema_conn)
        if not turn_id:
            from tenant_public_case import _schema as public_case_schema
            public_case_schema(schema_conn)
        schema_conn.commit()
    body='';media_request_id=None
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT c.case_id::text AS case_id,c.channel_id::text AS channel_id,
              c.conversation_id,c.status,c.evidence,t.status AS ticket_status
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
                if previous['state']!='blocked':
                    return {'state':previous['state'],'reason':'existing_receipt'}
                cur.execute('''DELETE FROM saas_consultation_group_deliveries WHERE tenant_id=%s::uuid
                  AND consultation_id=%s::uuid AND request_id=%s::uuid AND state='blocked' ''',
                  (tenant,consultation_id,request_id))
                cur.execute("DELETE FROM saas_public_case_shares WHERE tenant_id=%s::uuid AND ticket_id=%s::uuid AND revision_key=%s",
                  (tenant,consult['case_id'],'pilot:'+consultation_id))
                if source_external_id:
                    cur.execute("DELETE FROM saas_consultation_group_media_deliveries WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND source_external_id=%s AND channel_id=%s::uuid AND conversation_id=%s AND state='blocked'",
                      (tenant,consultation_id,str(source_external_id),target['channel_id'],target['conversation_id']))
            safe_summary=' '.join(_redact_remote_password(summary).split())[:1800]
            cur.execute('''SELECT lead_id FROM saas_connect_conversations
              WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s''',
              (tenant,consult['channel_id'],consult['conversation_id']))
            source_route=cur.fetchone() or {}
            source_lead_id=int(source_route.get('lead_id') or 0)
            cur.execute('''SELECT m.id,m.external_id,m.message_type,m.body FROM saas_crm_messages m
              WHERE m.tenant_id=%s::uuid AND m.kommo_lead_id=%s AND m.channel=%s
                AND m.direction='incoming' AND m.message_type IN ('image','audio','voice','video','document')
                AND m.external_id=%s''',
              (tenant,source_lead_id,'connect:'+customer_scope['channel_id'],'connect:'+str(source_external_id or '')))
            source_media=cur.fetchone() if source_external_id and not turn_id else None
            if source_media:
                media_request_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'pilot-case-media:'+tenant+':'+consult['case_id']+':'+target['channel_id']+':'+target['conversation_id']+':'+str(source_external_id)))
                cur.execute('''INSERT INTO saas_consultation_group_media_deliveries(tenant_id,case_id,consultation_id,source_external_id,channel_id,conversation_id,request_id,state)
                  VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s::uuid,%s,%s::uuid,'sending')
                  ON CONFLICT(tenant_id,case_id,source_external_id,channel_id,conversation_id) DO NOTHING RETURNING request_id''',
                  (tenant,consult['case_id'],consultation_id,str(source_external_id),target['channel_id'],target['conversation_id'],media_request_id))
                if not cur.fetchone():
                    conn.commit();return {'state':'blocked','reason':'case_media_delivery_already_reserved'}
            route=connect_route(cur,tenant,int(group['lead_id']))
            if not route or route['channel_id']!=target['channel_id'] or route['conversation_id']!=target['conversation_id']:
                conn.commit();return {'state':'blocked','reason':'pilot_group_transport_mismatch'}
            merged_evidence=consult.get('evidence') if isinstance(consult.get('evidence'),dict) else {}
            media_unverified=bool(source_media and source_media.get('message_type') in {'image','audio','voice','video','document'})
            body=_case_group_body(consultation_id,consult['case_id'],safe_summary if turn_id else '',
                merged_evidence,'',bool(turn_id))
            public_url=''
            if (not turn_id and len(str(merged_evidence.get('query') or '').strip())>=8
                    and isinstance(merged_evidence.get('case_message_ids'),list)):
                from tenant_public_case import create_for_pilot_handoff
                from legacy_runtime_config import CANONICAL_WEB_ORIGIN
                quote=str(merged_evidence.get('query') or '').strip()
                try:
                    share=create_for_pilot_handoff(cur,tenant_id=tenant,ticket_id=consult['case_id'],
                      group_channel_id=target['channel_id'],group_conversation_id=target['conversation_id'],
                      source_channel_id=customer_scope['channel_id'],source_conversation_id=customer_scope['conversation_id'],
                      evidence_quote=quote,case_message_ids=merged_evidence.get('case_message_ids'),
                      source_external_id=source_external_id,revision_key='pilot:'+consultation_id,origin=CANONICAL_WEB_ORIGIN,
                      authorization_job=job)
                except TenantLinearError:
                    conn.rollback()
                    return {'state':'blocked','reason':'case_share_or_pilot_guard'}
                public_url=share['url']
                body+='\nCASE dialoqu (24 saat): '+public_url
                if share.get('is_partial'):
                    body+='\nBəzi fayllar məxfi məlumat işarəsinə görə CASE linkində açılmır.'
            elif not turn_id:
                body+='\nCASE snapshot yaradılmadı: bu müraciət üçün açıq mesaj üzvlüyü yoxdur.'
                if media_unverified:
                    body+='\nFayl CASE linkinə daxil edilməyib, çünki onun CASE üzvlüyü təsdiqlənməyib.'
            if len(body.encode('utf-16-le'))//2>4096:
                conn.rollback()
                return {'state':'blocked','reason':'pilot_group_message_too_long'}
            digest=hashlib.sha256(body.encode()).hexdigest()
            stored_body=body.replace(public_url,'[one-time case share URL omitted from logs]') if public_url else body
            cur.execute('''INSERT INTO saas_consultation_group_deliveries(tenant_id,consultation_id,request_id,
              case_id,channel_id,conversation_id,body,body_hash,state,created_by)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,'sending',0)''',
              (tenant,consultation_id,request_id,consult['case_id'],target['channel_id'],target['conversation_id'],stored_body,digest))
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
                if not turn_id and source_external_id:
                    cur.execute('''SELECT m.external_id FROM saas_crm_messages m
                      WHERE m.tenant_id=%s::uuid AND m.kommo_lead_id=%s AND m.channel=%s
                        AND m.direction='incoming'
                      ORDER BY COALESCE(m.happened_at,m.created_at) DESC,m.id DESC LIMIT 1''',
                      (tenant,int(consult.get('lead_id') or source_route.get('lead_id') or 0),
                       'connect:'+customer_scope['channel_id']))
                    latest=cur.fetchone() or {}
                    if latest.get('external_id')!='connect:'+str(source_external_id):
                        raise TenantLinearError('A newer customer message replaced this consultation.',409)
                if media_unverified:
                    cur.execute("SELECT state FROM saas_consultation_group_media_deliveries WHERE tenant_id=%s::uuid AND request_id=%s::uuid FOR UPDATE",(tenant,media_request_id))
                    media_receipt=cur.fetchone()
                    if not media_receipt or media_receipt['state']!='sending':raise TenantLinearError('Case media delivery is already reserved or needs review.',409)
                    attachment_route=connect_route(cur,tenant,source_lead_id)
                    if not attachment_route or attachment_route['channel_id']!=customer_scope['channel_id'] or attachment_route['conversation_id']!=customer_scope['conversation_id']:
                        raise TenantLinearError('Case attachment route changed.',409)
                    cur.execute("UPDATE saas_consultation_group_media_deliveries SET state='blocked',updated_at=now() WHERE tenant_id=%s::uuid AND request_id=%s::uuid",(tenant,media_request_id))
                stored_body=body.replace(public_url,'[one-time case share URL omitted from logs]') if public_url else body
                cur.execute('''UPDATE saas_consultation_group_deliveries SET body=%s,body_hash=%s,updated_at=now()
                  WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND request_id=%s::uuid AND state='sending' ''',
                  (stored_body,hashlib.sha256(body.encode()).hexdigest(),tenant,consultation_id,request_id))
                attempted=True
                receipt=send_group_with_receipt(current_route,body,request_id)
                cur.execute('''UPDATE saas_consultation_group_deliveries SET state='sent',
                  connect_message_id=%s,provider_message_id=%s,updated_at=now()
                  WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND request_id=%s::uuid AND state='sending'
                  RETURNING consultation_id''',(receipt['message_id'],receipt['external_message_id'],tenant,consultation_id,request_id))
                if not cur.fetchone():
                    raise TenantLinearError('Pilot group receipt needs review.',409)
                cur.execute('''UPDATE saas_internal_consultations SET delivery_state=
                  CASE WHEN recipient_id IS NULL THEN 'pilot_group_sent' ELSE 'group_sent' END
                  WHERE tenant_id=%s::uuid AND id=%s::uuid AND delivery_state IN ('pilot_group_pending','internal_inbox','telegram_pending')''',
                  (tenant,consultation_id))
                from tenant_platform import _json
                cur.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,
                  channel,author_name,body,message_type,raw)
                  VALUES(%s::uuid,%s::uuid,%s,%s,'outgoing',%s,'AI Agent',%s,%s,%s::jsonb)
                  ON CONFLICT(tenant_id,external_id) DO NOTHING''',
                  (uuid.uuid4(),tenant,int(group['lead_id']),'connect:'+receipt['message_id'],
                   'connect:'+target['channel_id'],stored_body,'text',
                   _json({'source':'connect','pilot_consultation_id':consultation_id,'media_attached':False})))
                if turn_id:
                    cur.execute('''UPDATE saas_ai_conversation_state SET state='human',
                      reason='ai_developer_consultation:awaiting_specialist',revision=revision+1,updated_at=now()
                      WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s
                        AND conversation_id=%s AND state='active' AND reason='pilot_developer_followup'
                        AND agent_id=%s::uuid''',(tenant,customer_scope['channel_id'],customer_scope['conversation_id'],job['agent_id']))
            conn.commit()
    except Exception:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('''UPDATE saas_consultation_group_deliveries SET state=%s,updated_at=now()
                  WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND request_id=%s::uuid AND state='sending' ''',
                  ('unknown' if attempted else 'blocked',tenant,consultation_id,request_id))
                cur.execute("UPDATE saas_consultation_group_media_deliveries SET state='blocked',updated_at=now() WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND state='sending'",
                  (tenant,consultation_id))
                if not attempted:
                    cur.execute("DELETE FROM saas_public_case_shares WHERE tenant_id=%s::uuid AND ticket_id=%s::uuid AND revision_key=%s",
                      (tenant,consult.get('case_id'),'pilot:'+consultation_id))
                    cur.execute("DELETE FROM saas_consultation_group_media_deliveries WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND state='blocked'",
                      (tenant,consultation_id))
                cur.execute('''UPDATE saas_internal_consultations SET delivery_state=
                  CASE WHEN recipient_id IS NULL THEN %s ELSE 'group_delivery_needs_review' END
                  WHERE tenant_id=%s::uuid AND id=%s::uuid AND delivery_state='pilot_group_pending' ''',
                  ('pilot_group_unknown' if attempted else 'pilot_group_blocked',tenant,consultation_id))
            conn.commit()
        return {'state':'unknown' if attempted else 'blocked','reason':'pilot_group_delivery_needs_review'}
    return {'state':'sent','request_id':request_id,'message_id':receipt['message_id']}


def acknowledge_pilot_consultation_to_customer(job,consultation_id,source_external_id='',language='auto'):
    """Send a fixed customer acknowledgment only after the group receipt is durable."""
    from tenant_ai_pilot import manual_live_job,configured_group_scope,configured_scope
    from tenant_ai_dialog_scope import lock_dialog
    from tenant_connect_transport import connect_route,send_ai_reply_with_receipt
    from tenant_platform import _json
    if not manual_live_job(job):return {'state':'blocked','reason':'pilot_scope_disabled'}
    target=configured_group_scope();customer_scope=configured_scope()
    if not target or not customer_scope:return {'state':'blocked','reason':'pilot_scope_unconfigured'}
    tenant=str(job['tenant_id']);consultation_id=identifier(consultation_id)
    request_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'beintaskbot-pilot-customer-ack:'+tenant+':'+consultation_id))
    body={
      'az':'Müraciətinizi texniki komandaya göndərdik. Hələ həll olunduğunu təsdiqləmirik; əlavə məlumat lazım olsa, sizdən soruşacağıq.',
      'ru':'Мы передали обращение технической команде. Решение пока не подтверждено; если понадобятся сведения, мы уточним их у вас.',
      'tr':'Bildiriminizi teknik ekibe ilettik. Sorun henüz çözülmüş değil; ek bilgi gerekirse size soracağız.',
      'en':'We sent your report to the technical team. It is not confirmed resolved; we will ask if more details are needed.',
    }.get(str(language or 'auto').casefold(),'We sent your report to the technical team. It is not confirmed resolved; we will ask if more details are needed.')
    group_request=str(uuid.uuid5(uuid.NAMESPACE_URL,'beintaskbot-pilot-group:'+tenant+':'+consultation_id))
    try:source_key=str(uuid.UUID(str(source_external_id)))
    except (ValueError,TypeError,AttributeError):return {'state':'blocked','reason':'source_message_invalid'}
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT c.case_id::text AS case_id,c.channel_id::text AS channel_id,c.conversation_id,
                c.status,t.status AS ticket_status,d.state AS group_state,d.channel_id::text AS group_channel_id,
                d.conversation_id AS group_conversation_id
              FROM saas_internal_consultations c JOIN saas_support_tickets t
                ON t.tenant_id=c.tenant_id AND t.id=c.case_id
              JOIN saas_consultation_group_deliveries d ON d.tenant_id=c.tenant_id
                AND d.consultation_id=c.id AND d.request_id=%s::uuid
              WHERE c.tenant_id=%s::uuid AND c.id=%s::uuid''',(group_request,tenant,consultation_id))
            consult=cur.fetchone()
            if (not consult or consult['group_state']!='sent' or consult['status']=='closed'
                    or consult['ticket_status']=='resolved' or consult['channel_id']!=customer_scope['channel_id']
                    or consult['conversation_id']!=customer_scope['conversation_id']
                    or consult['group_channel_id']!=target['channel_id']
                    or consult['group_conversation_id']!=target['conversation_id']):
                return {'state':'blocked','reason':'group_receipt_or_case_unavailable'}
            cur.execute('''SELECT state,connect_message_id FROM saas_consultation_customer_acks
              WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid''',(tenant,consultation_id))
            previous=cur.fetchone()
            if previous:
                return {'state':'sent' if previous['state']=='sent' else
                    ('unknown' if previous['state'] in {'sending','unknown'} else 'blocked'),
                    'message_id':previous.get('connect_message_id')}
            cur.execute('''SELECT lead_id FROM saas_connect_conversations WHERE tenant_id=%s::uuid
              AND connect_channel_id=%s::uuid AND conversation_id=%s''',
              (tenant,customer_scope['channel_id'],customer_scope['conversation_id']))
            route_row=cur.fetchone() or {}
            cur.execute('''INSERT INTO saas_consultation_customer_acks(tenant_id,consultation_id,request_id,
              case_id,channel_id,conversation_id,body,body_hash,state)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,'sending')''',
              (tenant,consultation_id,request_id,consult['case_id'],consult['channel_id'],consult['conversation_id'],
               body,hashlib.sha256(body.encode()).hexdigest()))
        conn.commit()
    attempted=False
    try:
      with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            lock_dialog(cur,tenant,'connect',customer_scope['channel_id'],customer_scope['conversation_id'])
            from tenant_ai_pilot import pilot_group_handoff_allowed
            source_job={**job,'provider':'connect','tenant_id':tenant,'channel_id':customer_scope['channel_id'],
                'conversation_id':customer_scope['conversation_id']}
            if not pilot_group_handoff_allowed(source_job,connection=conn):
                raise TenantLinearError('Customer pilot authorization changed before the acknowledgment.',409)
            cur.execute('''SELECT state,revision,reason FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid
              AND provider='connect' AND channel_id=%s AND conversation_id=%s FOR UPDATE''',
              (tenant,customer_scope['channel_id'],customer_scope['conversation_id']))
            current_state=cur.fetchone() or {}
            if current_state.get('state')!='human' or not str(current_state.get('reason') or '').startswith('ai_developer_consultation:'):
                raise TenantLinearError('Customer handoff changed before acknowledgment.',409)
            cur.execute('''SELECT m.external_id FROM saas_crm_messages m WHERE m.tenant_id=%s::uuid
              AND m.kommo_lead_id=%s AND m.channel=%s AND m.direction='incoming'
              ORDER BY COALESCE(m.happened_at,m.created_at) DESC,m.id DESC LIMIT 1''',
              (tenant,int(route_row.get('lead_id') or 0),'connect:'+customer_scope['channel_id']))
            latest=cur.fetchone() or {}
            if latest.get('external_id')!='connect:'+source_key:
                raise TenantLinearError('A newer customer message replaced this acknowledgment.',409)
            group=connect_route(cur,tenant,int(route_row['lead_id']))
            if not group or group['channel_id']!=customer_scope['channel_id'] or group['conversation_id']!=customer_scope['conversation_id']:
                raise TenantLinearError('Customer route changed before acknowledgment.',409)
            cur.execute('''SELECT state FROM saas_consultation_customer_acks WHERE tenant_id=%s::uuid
              AND consultation_id=%s::uuid AND request_id=%s::uuid FOR UPDATE''',(tenant,consultation_id,request_id))
            row=cur.fetchone()
            if not row or row['state']!='sending':
                raise TenantLinearError('Acknowledgment request is unavailable.',409)
            attempted=True
            message_id=send_ai_reply_with_receipt(group,body,request_id)
            cur.execute('''UPDATE saas_consultation_customer_acks SET state='sent',connect_message_id=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND request_id=%s::uuid AND state='sending'
              RETURNING consultation_id''',(message_id,tenant,consultation_id,request_id))
            if not cur.fetchone():raise TenantLinearError('Customer acknowledgment receipt needs review.',409)
            cur.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,
              channel,author_name,body,message_type,raw)
              VALUES(%s::uuid,%s::uuid,%s,%s,'outgoing',%s,'AI Agent',%s,'text',%s::jsonb)
              ON CONFLICT(tenant_id,external_id) DO NOTHING''',
              (uuid.uuid4(),tenant,int(route_row['lead_id']),'connect:'+message_id,
               'connect:'+customer_scope['channel_id'],body,_json({'source':'connect','pilot_consultation_ack':consultation_id})))
        conn.commit()
    except Exception:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('''UPDATE saas_consultation_customer_acks SET state=%s,updated_at=now()
                  WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND request_id=%s::uuid AND state='sending' ''',
                  ('unknown' if attempted else 'blocked',tenant,consultation_id,request_id))
            conn.commit()
        return {'state':'unknown' if attempted else 'blocked','reason':'customer_ack_needs_review'}
    return {'state':'sent','request_id':request_id,'message_id':message_id}
