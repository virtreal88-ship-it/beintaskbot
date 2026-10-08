"""Only current company owner can bind a channel. Canonical chat IDs are exclusive."""
import json
import uuid
from datetime import timedelta
from tenant_platform import _connect
from tenant_news_ai_settings import authorize
from tenant_news import access
from tenant_news_telegram_schema import ensure_schema
from tenant_news_telegram_policy import settings
from tenant_linear_policy import TenantLinearError


def public(row):
    row=row or {}
    return {**{'enabled':False,'timezone':'Asia/Baku','start':'09:00','end':'19:00','interval_minutes':60},
        **row.get('config',{}),'configured':bool(row),'chat_id':str(row.get('chat_id') or ''),'title':row.get('title',''),
        'binding_version':str(row.get('binding_version') or ''),'updated_at':str(row.get('updated_at') or ''),'next_at':str(row.get('next_at') or '')}


def read(session,*,owner_only=True):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,_=(authorize if owner_only else access)(cur,session)
            cur.execute('SELECT * FROM saas_news_channels WHERE tenant_id=%s::uuid',(tenant,))
            return public(cur.fetchone())


def save(session,data,verified):
    config=settings(data)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,user=authorize(cur,session)
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-news-channel:'+tenant,))
            cur.execute('SELECT * FROM saas_news_channels WHERE tenant_id=%s::uuid FOR UPDATE',(tenant,))
            previous=cur.fetchone() or {}
            if str(previous.get('updated_at') or '')!=str(data.get('expected_updated_at') or ''):
                raise TenantLinearError('Kanal ayarları dəyişib. Yeniləyin.',409)
            if verified is None:
                if config['enabled'] or not previous:raise TenantLinearError('Əvvəlcə kanalı yoxlayın.')
                verified={k:previous[k] for k in ('chat_id','bot_id','verified_by','title')}
            if verified['verified_by']!=user:raise TenantLinearError('Kanal sahibi dəyişib. Yenidən yoxlayın.',409)
            # Global chat lock plus UNIQUE prevents two companies binding one channel concurrently.
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('news-chat:'+str(verified['chat_id']),))
            cur.execute('SELECT tenant_id FROM saas_news_channels WHERE chat_id=%s AND tenant_id<>%s::uuid',(verified['chat_id'],tenant))
            if cur.fetchone():raise TenantLinearError('Bu kanal artıq başqa şirkətə bağlıdır.',409)
            same=previous and all(previous[k]==verified[k] for k in ('chat_id','bot_id','verified_by'))
            binding=str(previous['binding_version']) if same else str(uuid.uuid4())
            next_at=previous.get('next_at')
            if next_at:
                next_at=max(next_at,next_at+timedelta(minutes=config['interval_minutes']-previous['config']['interval_minutes']))
            cur.execute('''INSERT INTO saas_news_channels(tenant_id,chat_id,bot_id,verified_by,title,binding_version,config,next_at)
              VALUES(%s::uuid,%s,%s,%s,%s,%s::uuid,%s::jsonb,%s) ON CONFLICT(tenant_id) DO UPDATE SET
              chat_id=EXCLUDED.chat_id,bot_id=EXCLUDED.bot_id,verified_by=EXCLUDED.verified_by,title=EXCLUDED.title,
              binding_version=EXCLUDED.binding_version,config=EXCLUDED.config,next_at=EXCLUDED.next_at,updated_at=now() RETURNING *''',
              (tenant,verified['chat_id'],verified['bot_id'],user,verified['title'],binding,json.dumps(config),next_at))
            result=public(cur.fetchone())
            if not same:
                cur.execute("UPDATE saas_news_telegram_queue SET status='blocked',updated_at=now() WHERE tenant_id=%s::uuid AND status='pending'",(tenant,))
        conn.commit()
    return result
