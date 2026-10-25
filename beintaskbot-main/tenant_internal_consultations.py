"""Tenant-scoped employee consultation receipts; never sends customer messages."""
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from tenant_platform import _connect, _json
from tenant_ai_agents_schema import ensure_schema
from tenant_operator_access import authorize
from tenant_ai_agents_store import _audit
from tenant_linear_policy import TenantLinearError

STATUSES={'queued_after_hours','waiting_specialist','answered','confirmed_bug','workaround','deployed_fix','closed'}

def schema(conn):
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_internal_consultations(
          tenant_id UUID NOT NULL,id UUID NOT NULL,case_id UUID NOT NULL,channel_id UUID NOT NULL,conversation_id TEXT NOT NULL,
          request_key TEXT NOT NULL,reason TEXT NOT NULL,evidence JSONB NOT NULL DEFAULT '{}'::jsonb,status TEXT NOT NULL,
          recipient_id BIGINT NULL,ai_run_id UUID NULL,delivery_state TEXT NOT NULL DEFAULT 'internal_inbox',
          created_by BIGINT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),answered_by BIGINT NULL,answered_at TIMESTAMPTZ NULL,
          response TEXT NOT NULL DEFAULT '',response_kind TEXT NOT NULL DEFAULT '',PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,request_key),
          FOREIGN KEY(tenant_id,case_id) REFERENCES saas_support_tickets(tenant_id,id) ON DELETE RESTRICT)''')
        cur.execute("ALTER TABLE saas_internal_consultations ADD COLUMN IF NOT EXISTS recipient_id BIGINT NULL")
        cur.execute("ALTER TABLE saas_internal_consultations ADD COLUMN IF NOT EXISTS ai_run_id UUID NULL")
        cur.execute("ALTER TABLE saas_internal_consultations ADD COLUMN IF NOT EXISTS delivery_state TEXT NOT NULL DEFAULT 'internal_inbox'")
        cur.execute('CREATE INDEX IF NOT EXISTS saas_internal_consultations_case_idx ON saas_internal_consultations(tenant_id,case_id,created_at DESC)')

def _hours():
    now=datetime.now(ZoneInfo('Asia/Baku'));return 8<=now.hour<20

def _recipient(cur, tenant, recipient):
    """An internal delivery target must be an active, explicitly authorized member.

    This deliberately is not a free-form WhatsApp target: creating a consultation
    cannot cause an unreviewed external message to be sent.
    """
    try: recipient=int(recipient)
    except (TypeError,ValueError): raise TenantLinearError('consultation_recipient_invalid',400) from None
    cur.execute('''SELECT telegram_id,role,permissions,display_name FROM saas_tenant_members
      WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE FOR SHARE''',(tenant,recipient))
    row=cur.fetchone()
    if not row or (row.get('role') not in {'owner','admin'} and 'support' not in (row.get('permissions') or [])):
        raise TenantLinearError('consultation_recipient_forbidden',403)
    return row

def create(profile,data):
    if not isinstance(data,dict) or set(data)-{'case_id','reason','evidence','request_key','recipient_id'}: raise TenantLinearError('consultation_request_invalid',400)
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            tenant,actor=authorize(cur,profile)
            cur.execute('''SELECT channel_id,conversation_id FROM saas_support_tickets WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE''',(tenant,data.get('case_id')));case=cur.fetchone()
            if not case: raise TenantLinearError('consultation_case_unavailable',404)
            key=str(data.get('request_key') or '')[:180]
            if not key: raise TenantLinearError('consultation_request_invalid',400)
            recipient=_recipient(cur,tenant,data.get('recipient_id'))
            cur.execute('SELECT id,status FROM saas_internal_consultations WHERE tenant_id=%s::uuid AND request_key=%s FOR UPDATE',(tenant,key));old=cur.fetchone()
            if old: return {'id':str(old['id']),'status':old['status'],'created':False}
            status='waiting_specialist' if _hours() else 'queued_after_hours'; ident=uuid.uuid4()
            evidence=data.get('evidence') if isinstance(data.get('evidence'),dict) else {}
            cur.execute('''INSERT INTO saas_internal_consultations(tenant_id,id,case_id,channel_id,conversation_id,request_key,reason,evidence,status,recipient_id,created_by)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s::jsonb,%s,%s,%s)''',(tenant,ident,data['case_id'],case['channel_id'],case['conversation_id'],key,str(data.get('reason') or '')[:1000],_json(evidence),status,recipient['telegram_id'],actor))
            _audit(cur,tenant,actor,'internal_consultation_created','support_case',data['case_id'],{'status':status,'recipient_id':recipient['telegram_id'],'delivery':'internal_inbox'})
        conn.commit();return {'id':str(ident),'status':status,'created':True,'recipient':recipient['display_name'] or str(recipient['telegram_id']),'delivery':'internal_inbox','customer_message_sent':False}

def create_from_ai(job, *, recipient_id, reason, evidence, run_id=None):
    """Create a durable internal-only consultation after the AI has handed off.

    The caller is a worker, so it has no browser profile.  It is deliberately
    narrower than ``create``: exact tenant/channel/conversation and the
    webhook fingerprint are taken solely from the claimed job.
    """
    if job.get('provider')!='connect': return {'created':False,'reason':'provider_not_supported'}
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            tenant=str(job['tenant_id']);recipient=_recipient(cur,tenant,recipient_id)
            cur.execute('''SELECT id FROM saas_support_tickets WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
              AND conversation_id=%s ORDER BY created_at DESC LIMIT 1 FOR SHARE''',(tenant,job['channel_id'],job['conversation_id']))
            case=cur.fetchone()
            if not case: return {'created':False,'reason':'support_case_missing'}
            key='ai:'+str(job['agent_id'])+':'+str(job.get('event_fingerprint') or '')
            cur.execute('SELECT id,status FROM saas_internal_consultations WHERE tenant_id=%s::uuid AND request_key=%s FOR UPDATE',(tenant,key));old=cur.fetchone()
            if old: return {'id':str(old['id']),'status':old['status'],'created':False,'customer_message_sent':False}
            status='waiting_specialist' if _hours() else 'queued_after_hours';ident=uuid.uuid4()
            cur.execute('''INSERT INTO saas_internal_consultations(tenant_id,id,case_id,channel_id,conversation_id,request_key,reason,evidence,status,recipient_id,ai_run_id,created_by)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s::jsonb,%s,%s,%s::uuid,NULL)''',
              (tenant,ident,case['id'],job['channel_id'],job['conversation_id'],key,str(reason)[:1000],_json(evidence if isinstance(evidence,dict) else {}),status,recipient['telegram_id'],run_id))
            cur.execute('''INSERT INTO saas_tenant_notifications(id,tenant_id,telegram_id,event_key,title,body,target_url)
              VALUES(%s,%s::uuid,%s,%s,'AI: требуется консультация разработчика',%s,'/app?view=support')
              ON CONFLICT DO NOTHING''',(uuid.uuid4(),tenant,recipient['telegram_id'],'ai_consultation:'+key,str(reason)[:500]))
            _audit(cur,tenant,None,'ai_internal_consultation_created','consultation',ident,{'case_id':str(case['id']),'recipient_id':recipient['telegram_id'],'status':status,'internal_only':True})
        conn.commit();return {'id':str(ident),'status':status,'created':True,'customer_message_sent':False,'delivery':'internal_inbox'}

def listing(profile,case_id):
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            tenant,_=authorize(cur,profile)
            cur.execute('''SELECT c.id,c.case_id,c.reason,c.evidence,c.status,c.delivery_state,c.created_at,c.answered_at,c.response,c.response_kind,
              c.recipient_id,COALESCE(m.display_name,c.recipient_id::text) AS recipient_name
              FROM saas_internal_consultations c LEFT JOIN saas_tenant_members m ON m.tenant_id=c.tenant_id AND m.telegram_id=c.recipient_id
              WHERE c.tenant_id=%s::uuid AND c.case_id=%s::uuid ORDER BY c.created_at DESC''',(tenant,case_id))
            return {'consultations':[dict(row) for row in cur.fetchall()]}

def reply(profile,consultation_id,data):
    if not isinstance(data,dict) or set(data)!={'response','kind'} or data['kind'] not in {'diagnosis','hypothesis','confirmed_bug','workaround','deployed_fix'}: raise TenantLinearError('consultation_reply_invalid',400)
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            tenant,actor=authorize(cur,profile)
            cur.execute('SELECT * FROM saas_internal_consultations WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,consultation_id));row=cur.fetchone()
            if not row: raise TenantLinearError('consultation_unavailable',404)
            cur.execute('SELECT role FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s FOR SHARE',(tenant,actor));member=cur.fetchone()
            if row.get('recipient_id') not in (None,actor) and (not member or member.get('role') not in {'owner','admin'}):
                raise TenantLinearError('consultation_reply_forbidden',403)
            text=str(data['response']).strip()
            if not text: raise TenantLinearError('consultation_reply_invalid',400)
            status=data['kind'] if data['kind'] in {'confirmed_bug','workaround','deployed_fix'} else 'answered'
            cur.execute('UPDATE saas_internal_consultations SET response=%s,response_kind=%s,status=%s,answered_by=%s,answered_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid',(text[:4000],data['kind'],status,actor,tenant,consultation_id))
            if status in {'confirmed_bug','workaround','deployed_fix'}:
                # The existing Knowledge Candidate flow owns extraction, review
                # and publication.  This marker is evidence that a verified
                # internal result may be considered there; it is never a
                # publication or an agent binding.
                cur.execute('''UPDATE saas_support_tickets SET knowledge_candidate_requested=TRUE,updated_at=now()
                  WHERE tenant_id=%s::uuid AND id=%s::uuid''',(tenant,row['case_id']))
            _audit(cur,tenant,actor,'internal_consultation_answered','consultation',consultation_id,{'kind':data['kind'],'learning_eligible':status in {'confirmed_bug','deployed_fix'}})
        conn.commit();return {'id':str(consultation_id),'status':status,'safe_customer_draft':True,'publication_requires_approval':True}
