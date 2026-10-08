"""Transactional approval queue. Once-only receipts survive news archive cleanup."""
import json
from datetime import datetime,timezone,timedelta
from tenant_platform import _connect
from tenant_linear_policy import identifier,TenantLinearError
from tenant_news_telegram_schema import ensure_schema
from tenant_news_telegram_policy import due


def enqueue(cur,tenant,user,row,data):
    if data.get('confirm_telegram') is not True:raise TenantLinearError('Telegram kanalında dərc olunmasını təsdiqləyin.')
    if row['status']!='published':raise TenantLinearError('Əvvəlcə xəbəri saytda təsdiqləyin.',409)
    cur.execute('SELECT * FROM saas_news_channels WHERE tenant_id=%s::uuid FOR UPDATE',(tenant,))
    channel=cur.fetchone()
    if not channel or channel['config'].get('enabled') is not True or str(channel['binding_version'])!=str(data.get('channel_version') or ''):
        raise TenantLinearError('Kanal dəyişib və ya aktiv deyil. Ayarları yoxlayın.',409)
    cur.execute('SELECT status FROM saas_news_telegram_queue WHERE tenant_id=%s::uuid AND news_id=%s::uuid FOR UPDATE',(tenant,row['id']))
    previous=cur.fetchone()
    if previous and (previous['status'] not in {'blocked','cancelled'} or data.get('action')!='enqueue'):return previous['status']
    payload={k:row.get(k) or '' for k in ('identifier','project_name','title','summary','url')}
    cur.execute('''INSERT INTO saas_news_telegram_queue(tenant_id,news_id,requested_by,chat_id,binding_version,payload)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s::uuid,%s::jsonb) ON CONFLICT(tenant_id,news_id) DO UPDATE
      SET requested_by=EXCLUDED.requested_by,chat_id=EXCLUDED.chat_id,binding_version=EXCLUDED.binding_version,
      payload=EXCLUDED.payload,status='pending',updated_at=now()''',
      (tenant,row['id'],user,channel['chat_id'],str(channel['binding_version']),json.dumps(payload)))
    return 'pending'


def operate(session,data):
    from tenant_news import access
    action=data.get('action')
    if action not in {'enqueue','cancel'}:raise TenantLinearError('Əməliyyat düzgün deyil.')
    key=identifier(data.get('id'))
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,user=access(cur,session)
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-news:'+tenant+':'+key,))
            cur.execute("SELECT * FROM saas_linear_news WHERE tenant_id=%s::uuid AND id=%s::uuid AND published_at>=now()-interval '90 days' FOR UPDATE",(tenant,key))
            row=cur.fetchone()
            if not row:raise TenantLinearError('Xəbər tapılmadı.',404)
            if action=='enqueue':
                if str(row['updated_at'])!=str(data.get('expected_updated_at') or ''):raise TenantLinearError('Xəbər dəyişib.',409)
                status=enqueue(cur,tenant,user,row,data)
            else:
                cur.execute("UPDATE saas_news_telegram_queue SET status='cancelled',updated_at=now() WHERE tenant_id=%s::uuid AND news_id=%s::uuid AND status IN ('pending','blocked') RETURNING status",(tenant,key))
                result=cur.fetchone()
                if not result:raise TenantLinearError('Göndəriş artıq başlayıb və ya ləğv edilə bilməz.',409)
                status='cancelled'
        conn.commit()
    return {'tenant_id':tenant,'user_id':user,'telegram_status':status}


def candidate():
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''WITH locked AS MATERIALIZED (SELECT c.tenant_id,c.chat_id FROM saas_news_channels c
              WHERE EXISTS(SELECT 1 FROM saas_news_telegram_queue q WHERE q.tenant_id=c.tenant_id
              AND q.status='sending' AND q.updated_at<now()-interval '5 minutes') LIMIT 10 FOR UPDATE OF c SKIP LOCKED),
              stalled AS (UPDATE saas_news_telegram_queue q SET status='unknown',updated_at=now() FROM locked l
              WHERE q.tenant_id=l.tenant_id AND q.status='sending'
              AND q.updated_at<now()-interval '5 minutes' RETURNING q.tenant_id,q.chat_id)
              UPDATE saas_news_channels c SET next_at=GREATEST(c.next_at,now()+make_interval(mins=>(c.config->>'interval_minutes')::integer))
              FROM stalled s WHERE c.tenant_id=s.tenant_id AND c.chat_id=s.chat_id''')
            cur.execute('''WITH old AS (SELECT tenant_id,news_id FROM saas_news_telegram_queue
              WHERE created_at<now()-interval '90 days' AND payload<>'{}'::jsonb LIMIT 100 FOR UPDATE SKIP LOCKED)
              UPDATE saas_news_telegram_queue q SET payload='{}'::jsonb,status=CASE WHEN q.status IN ('pending','blocked','cancelled') THEN 'expired' ELSE q.status END
              FROM old WHERE q.tenant_id=old.tenant_id AND q.news_id=old.news_id''')
            cur.execute('''SELECT q.*,c.bot_id,c.verified_by FROM saas_news_telegram_queue q
              JOIN saas_news_channels c USING(tenant_id) JOIN saas_tenants t ON t.id=q.tenant_id
              WHERE q.status='pending' AND q.created_at>=now()-interval '90 days' AND t.status='active'
              AND c.config->'enabled'='true'::jsonb AND (c.next_at IS NULL OR c.next_at<=now())
              AND to_char(now() AT TIME ZONE (c.config->>'timezone'),'HH24:MI') BETWEEN c.config->>'start' AND c.config->>'end'
              ORDER BY q.created_at LIMIT 1''')
            item=cur.fetchone()
        conn.commit()
    return item


def claim(item,verified):
    from tenant_news import access
    tenant=str(item['tenant_id']);now=datetime.now(timezone.utc)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            try:access(cur,{'tenant_id':tenant,'telegram_id':item['requested_by']})
            except TenantLinearError:
                cur.execute("UPDATE saas_news_telegram_queue SET status='blocked',updated_at=now() WHERE tenant_id=%s::uuid AND news_id=%s::uuid AND status='pending'",(tenant,item['news_id']))
                conn.commit();return None
            cur.execute('''SELECT c.*,t.owner_telegram_id,m.active AS owner_active,m.role AS owner_role FROM saas_news_channels c
              JOIN saas_tenants t ON t.id=c.tenant_id JOIN saas_tenant_members m ON m.tenant_id=t.id AND m.telegram_id=t.owner_telegram_id
              WHERE c.tenant_id=%s::uuid FOR UPDATE OF c FOR SHARE OF t,m''',(tenant,))
            channel=cur.fetchone()
            cur.execute("SELECT * FROM saas_news_telegram_queue WHERE tenant_id=%s::uuid AND news_id=%s::uuid AND status='pending' FOR UPDATE",(tenant,item['news_id']))
            current=cur.fetchone()
            if not current or not channel:return None
            allowed=(verified is not None and bool(current['payload']) and current['created_at']>=now-timedelta(days=90)
                and channel['owner_active'] is True and channel['owner_role']=='owner'
                and channel['owner_telegram_id']==channel['verified_by'] and current['chat_id']==channel['chat_id']
                and str(current['binding_version'])==str(channel['binding_version'])
                and all(verified[k]==channel[k] for k in ('chat_id','bot_id','verified_by')))
            if not allowed:
                cur.execute("UPDATE saas_news_telegram_queue SET status='blocked',updated_at=now() WHERE tenant_id=%s::uuid AND news_id=%s::uuid",(tenant,item['news_id']))
                conn.commit();return None
            if not due({**channel['config'],'next_at':channel['next_at']},now):return None
            cur.execute("UPDATE saas_news_telegram_queue SET status='sending',updated_at=now() WHERE tenant_id=%s::uuid AND news_id=%s::uuid",(tenant,item['news_id']))
            cur.execute('UPDATE saas_news_channels SET next_at=%s WHERE tenant_id=%s::uuid',(now+timedelta(minutes=channel['config']['interval_minutes']),tenant))
        conn.commit()
    return current


def finish(item,status,message_id=None):
    if status not in {'sent','unknown'}:raise ValueError('Invalid delivery status')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            # Extend cooldown from acknowledgement/timeout, not just request start.
            cur.execute('''UPDATE saas_news_channels c SET next_at=GREATEST(c.next_at,now()+make_interval(mins=>(c.config->>'interval_minutes')::integer))
              WHERE c.tenant_id=%s::uuid AND c.chat_id=%s AND EXISTS(SELECT 1 FROM saas_news_telegram_queue q
              WHERE q.tenant_id=c.tenant_id AND q.news_id=%s::uuid AND q.status='sending')''',(str(item['tenant_id']),item['chat_id'],item['news_id']))
            cur.execute("UPDATE saas_news_telegram_queue SET status=%s,message_id=%s,updated_at=now() WHERE tenant_id=%s::uuid AND news_id=%s::uuid AND status='sending'",(status,message_id,str(item['tenant_id']),item['news_id']))
        conn.commit()
