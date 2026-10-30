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
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,consultation_id),UNIQUE(tenant_id,request_id),
          FOREIGN KEY(tenant_id,consultation_id) REFERENCES saas_internal_consultations(tenant_id,id) ON DELETE RESTRICT,
          FOREIGN KEY(tenant_id,case_id) REFERENCES saas_support_tickets(tenant_id,id) ON DELETE RESTRICT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_consultation_group_replies(
          tenant_id UUID NOT NULL,consultation_id UUID NOT NULL,channel_id UUID NOT NULL,conversation_id TEXT NOT NULL,
          message_id TEXT NOT NULL,body TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,channel_id,conversation_id,message_id),
          FOREIGN KEY(tenant_id,consultation_id) REFERENCES saas_internal_consultations(tenant_id,id) ON DELETE RESTRICT)''')
        cur.execute("ALTER TABLE saas_consultation_group_deliveries ADD COLUMN IF NOT EXISTS customer_draft TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE saas_consultation_group_deliveries ADD COLUMN IF NOT EXISTS draft_reply_id TEXT NULL")


def _owner(cur,profile):
    tenant,actor,policy=access(cur,profile,'support')
    if policy.profile.get('role')!='owner' or not policy.allows('dialogs'):
        raise TenantLinearError('Only the company owner with dialog access may send a consultation.',403)
    return tenant,actor,policy


def destinations(profile):
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,_,policy=_owner(cur,profile)
            cur.execute('''SELECT c.connect_channel_id::text AS channel_id,c.conversation_id,c.contact_name
              FROM saas_connect_conversations c JOIN saas_connect_channels ch
                ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
              WHERE c.tenant_id=%s::uuid AND ch.active=TRUE AND c.conversation_id LIKE %s
              ORDER BY c.last_message_at DESC NULLS LAST LIMIT 200''',(tenant,'%@g.us'))
            return {'groups':[dict(row) for row in cur.fetchall() if group_jid(row['conversation_id'])]}


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
            cur.execute('''SELECT c.id,c.case_id,c.channel_id,c.conversation_id,c.status,t.status AS ticket_status
              FROM saas_internal_consultations c JOIN saas_support_tickets t ON t.tenant_id=c.tenant_id AND t.id=c.case_id
              WHERE c.tenant_id=%s::uuid AND c.id=%s::uuid FOR UPDATE OF c,t''',(tenant,key));consult=cur.fetchone()
            if not consult:raise TenantLinearError('Consultation is unavailable.',404)
            if consult['status']=='closed' or consult['ticket_status']=='resolved':raise TenantLinearError('Consultation is closed.',409)
            source=assert_route(cur,tenant,policy,'connect',consult['conversation_id'],str(consult['channel_id']))
            assert_route(cur,tenant,policy,'connect',group,channel)
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
            cur.execute('SELECT * FROM saas_consultation_group_deliveries WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid FOR UPDATE',(tenant,key));old=cur.fetchone()
            if old:
                if (str(old['request_id'])!=request_id or str(old['channel_id'])!=channel or old['conversation_id']!=group or old['body_hash']!=digest):
                    raise TenantLinearError('This consultation already has a different delivery.',409)
                return {'consultation_id':key,'case_id':str(consult['case_id']),'state':old['state'],'created':False}
            cur.execute('''INSERT INTO saas_consultation_group_deliveries(tenant_id,consultation_id,request_id,case_id,channel_id,conversation_id,body,body_hash,state,created_by)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,'ready',%s)''',(tenant,key,request_id,consult['case_id'],channel,group,body,digest,actor))
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
            cur.execute('SELECT * FROM saas_consultation_group_deliveries WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid FOR UPDATE',(tenant,key));row=cur.fetchone()
            if not row or str(row['request_id'])!=request_id:raise TenantLinearError('Delivery is unavailable.',404)
            if row['state']=='sent':return {'state':'sent','message_id':row['connect_message_id']}
            if row['state']!='ready':raise TenantLinearError('Delivery outcome is unresolved; inspect it before retrying.',409)
            cur.execute('''SELECT c.channel_id,c.conversation_id,c.status,t.status AS ticket_status FROM saas_internal_consultations c
              JOIN saas_support_tickets t ON t.tenant_id=c.tenant_id AND t.id=c.case_id
              WHERE c.tenant_id=%s::uuid AND c.id=%s::uuid FOR SHARE OF c,t''',(tenant,key));consult=cur.fetchone()
            if not consult or consult['status']=='closed' or consult['ticket_status']=='resolved':raise TenantLinearError('Consultation is closed.',409)
            assert_route(cur,tenant,policy,'connect',consult['conversation_id'],str(consult['channel_id']))
            group=assert_route(cur,tenant,policy,'connect',row['conversation_id'],str(row['channel_id']))
            route=connect_route(cur,tenant,int(group['lead_id']))
            if not route or route['channel_id']!=str(row['channel_id']) or route['conversation_id']!=row['conversation_id']:
                raise TenantLinearError('Destination group changed or is unavailable.',409)
            cur.execute("UPDATE saas_consultation_group_deliveries SET state='sending',updated_at=now() WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid",(tenant,key))
            body=row['body']
        conn.commit()
    try: receipt=send_group_with_receipt(route,body,request_id)
    except Exception:
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute("UPDATE saas_consultation_group_deliveries SET state='unknown',updated_at=now() WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND state='sending'",(tenant,key))
            conn.commit()
        raise TenantLinearError('Delivery outcome is unknown; do not resend.',503) from None
    with _connect() as conn:
        with conn.cursor() as cur:
            # Serialize with the signed group webhook: a reply can arrive
            # between the provider accepting the send and receipt persistence.
            from tenant_ai_dialog_scope import lock_dialog
            lock_dialog(cur,tenant,'connect',str(row['channel_id']),row['conversation_id'])
            cur.execute("""UPDATE saas_consultation_group_deliveries SET state='sent',connect_message_id=%s,provider_message_id=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND state='sending' RETURNING consultation_id""",(receipt['message_id'],receipt['external_message_id'],tenant,key))
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
              WHERE d.tenant_id=%s::uuid AND d.consultation_id=%s::uuid''',(tenant,key));row=cur.fetchone()
            if not row:raise TenantLinearError('Consultation is unavailable.',404)
            assert_route(cur,tenant,policy,'connect',row['source_conversation'],str(row['source_channel']))
            group=assert_route(cur,tenant,policy,'connect',row['conversation_id'],str(row['channel_id']))
            cur.execute('''SELECT message_id,body,created_at FROM saas_consultation_group_replies
              WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid ORDER BY created_at,message_id''',(tenant,key))
            replies=[{'message_id':r['message_id'],'body':r['body'],'created_at':r['created_at'].isoformat() if hasattr(r['created_at'],'isoformat') else str(r['created_at'])} for r in cur.fetchall()]
            return {'consultation_id':key,'case_id':str(row['case_id']),'state':row['state'],'request_id':str(row['request_id']),
                    'destination':group.get('contact_name') or row['conversation_id'],'prepared_text':row['body'],
                    'replies':replies,'customer_draft':row['customer_draft'],
                    'draft_reply_id':row['draft_reply_id'],'customer_message_sent':False}


def save_draft(profile,consultation_id,data):
    if not isinstance(data,dict) or set(data)!={'message_id','draft'}:
        raise TenantLinearError('Invalid draft.',400)
    reply_id=data['message_id'];draft=data['draft']
    if (not isinstance(reply_id,str) or not reply_id or len(reply_id)>200 or
        not isinstance(draft,str) or not draft.strip() or len(draft)>4000):raise TenantLinearError('Invalid draft.',400)
    key=identifier(consultation_id)
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            tenant,actor,policy=_owner(cur,profile)
            cur.execute('''SELECT d.*,c.channel_id AS source_channel,c.conversation_id AS source_conversation
              FROM saas_consultation_group_deliveries d JOIN saas_internal_consultations c
                ON c.tenant_id=d.tenant_id AND c.id=d.consultation_id
              WHERE d.tenant_id=%s::uuid AND d.consultation_id=%s::uuid FOR UPDATE OF d''',(tenant,key));row=cur.fetchone()
            if not row:raise TenantLinearError('Consultation is unavailable.',404)
            assert_route(cur,tenant,policy,'connect',row['source_conversation'],str(row['source_channel']))
            assert_route(cur,tenant,policy,'connect',row['conversation_id'],str(row['channel_id']))
            cur.execute('''SELECT 1 FROM saas_consultation_group_replies
              WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND message_id=%s''',(tenant,key,reply_id))
            if not cur.fetchone():raise TenantLinearError('Group reply is unavailable.',404)
            cur.execute('''UPDATE saas_consultation_group_deliveries SET customer_draft=%s,draft_reply_id=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid''',(draft.strip(),reply_id,tenant,key))
            _audit(cur,tenant,actor,'consultation_customer_draft_saved','consultation',key,{'reply_id':reply_id})
        conn.commit()
    return {'consultation_id':key,'customer_message_sent':False}


def record_group_reply(cur,tenant,channel,conversation,message_id,text,quoted_id=None):
    """Consume a signed inbound event without treating its content as commands."""
    if (not group_jid(conversation) or not isinstance(text,str) or not text.strip()
            or not isinstance(message_id,str) or not message_id or len(message_id)>200):return None
    matches=set(_MARKER.findall(text));quoted=quoted_id if isinstance(quoted_id,str) and re.fullmatch(r'[A-Za-z0-9_-]{5,200}',quoted_id) else None
    if len(matches)>1:return None
    cur.execute('''SELECT consultation_id::text AS id,case_id::text AS case_id,provider_message_id FROM saas_consultation_group_deliveries
      WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s AND state='sent'
        AND (provider_message_id=%s OR consultation_id::text=%s)''',(tenant,channel,conversation,quoted,next(iter(matches),'').lower()))
    rows=cur.fetchall()
    if len(rows)!=1:return None
    item=rows[0]
    if quoted and matches and (item['provider_message_id']!=quoted or item['id'] not in {value.lower() for value in matches}):return None
    cur.execute('''INSERT INTO saas_consultation_group_replies(tenant_id,consultation_id,channel_id,conversation_id,message_id,body)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING message_id''',(tenant,item['id'],channel,conversation,str(message_id)[:200],text[:4000]))
    return item['id'] if cur.fetchone() else None
