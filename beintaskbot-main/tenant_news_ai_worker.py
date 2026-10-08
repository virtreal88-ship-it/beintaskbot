"""One opt-in draft per tick. Quota/receipt committed before paid provider call."""
import json
from tenant_platform import _connect, _fernet
from tenant_news_ai_schema import ensure_ai_schema
from tenant_news_ai_provider import classify


def claim():
    with _connect() as conn:
        ensure_ai_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_news_ai_runs SET status='unknown',updated_at=now() WHERE status='sending' AND updated_at<now()-interval '5 minutes'")
            cur.execute('''SELECT n.* FROM saas_linear_news n JOIN saas_tenants t ON t.id=n.tenant_id
              JOIN saas_tenant_members m ON m.tenant_id=t.id AND m.telegram_id=t.owner_telegram_id
              JOIN saas_tenant_integrations i ON i.tenant_id=n.tenant_id AND i.provider='news_ai'
              WHERE n.status='pending' AND n.created_at>=now()-interval '90 days' AND t.status='active'
                AND m.active=TRUE AND m.role='owner' AND n.source->'manual' IS DISTINCT FROM 'true'::jsonb
                AND i.metadata->'enabled'='true'::jsonb AND i.metadata->'consent'='true'::jsonb AND i.secrets IS NOT NULL
                AND NOT EXISTS(SELECT 1 FROM saas_news_ai_runs r WHERE r.tenant_id=n.tenant_id AND r.news_id=n.id)
                AND (SELECT count(*) FROM saas_news_ai_runs r WHERE r.tenant_id=n.tenant_id
                     AND r.usage_day=(now() AT TIME ZONE 'UTC')::date)<(i.metadata->>'daily_limit')::integer
              ORDER BY n.created_at,n.id LIMIT 1 FOR UPDATE OF n SKIP LOCKED''')
            item = cur.fetchone()
            if not item:
                conn.commit()
                return None
            tenant = str(item['tenant_id'])
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tenant-news-ai:' + tenant,))
            cur.execute("SELECT metadata,secrets,updated_at FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='news_ai' FOR SHARE", (tenant,))
            connection = cur.fetchone(); config = connection['metadata']
            if config.get('enabled') is not True or config.get('consent') is not True or not connection['secrets']:
                return None
            cur.execute("SELECT count(*) AS used FROM saas_news_ai_runs WHERE tenant_id=%s::uuid AND usage_day=(now() AT TIME ZONE 'UTC')::date", (tenant,))
            if cur.fetchone()['used'] >= config['daily_limit']:
                return None
            cur.execute('''INSERT INTO saas_news_ai_runs(tenant_id,news_id,status,source_version,config_version)
              VALUES(%s::uuid,%s::uuid,'sending',%s,%s) ON CONFLICT DO NOTHING RETURNING news_id''',
              (tenant, item['id'], item['updated_at'], str(connection['updated_at'])))
            if not cur.fetchone():
                return None
            try:
                payload = json.loads(_fernet().decrypt(bytes(connection['secrets'])))
                if payload.get('tenant_id') != tenant or payload.get('provider') != 'news_ai':
                    raise ValueError('News AI credential mismatch')
                key = payload['api_key']
                if not isinstance(key, str) or not key:
                    raise ValueError('Missing key')
            except Exception:
                cur.execute("UPDATE saas_news_ai_runs SET status='unknown',updated_at=now() WHERE tenant_id=%s::uuid AND news_id=%s::uuid", (tenant, item['id']))
                conn.commit()
                return None
            item = {**item,'tenant_id':tenant,'config_version':str(connection['updated_at']),
                'api_key':key,'model':config['model']}
        conn.commit()
    return item


def finish(item, result):
    with _connect() as conn:
        ensure_ai_schema(conn)
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tenant-news-ai:' + item['tenant_id'],))
            cur.execute('''SELECT n.*,i.metadata AS ai_config,i.updated_at AS ai_version,t.status AS company_status
              FROM saas_linear_news n JOIN saas_tenants t ON t.id=n.tenant_id
              JOIN saas_tenant_members m ON m.tenant_id=t.id AND m.telegram_id=t.owner_telegram_id
              JOIN saas_news_ai_runs r ON r.tenant_id=n.tenant_id AND r.news_id=n.id
              JOIN saas_tenant_integrations i ON i.tenant_id=n.tenant_id AND i.provider='news_ai'
              WHERE n.tenant_id=%s::uuid AND n.id=%s::uuid AND r.status='sending' AND m.active=TRUE AND m.role='owner'
              FOR UPDATE OF n,r FOR SHARE OF i,t,m''', (item['tenant_id'], item['id']))
            current = cur.fetchone(); status = 'unknown'
            if result is not None:
                applicable = (current and current['status']=='pending' and current['updated_at']==item['updated_at']
                    and current['company_status']=='active' and str(current['ai_version'])==item['config_version']
                    and current['ai_config'].get('enabled') is True and current['ai_config'].get('consent') is True)
                status = 'completed' if applicable else 'skipped'
                if applicable:
                    # No route from classification to published; excluded items remain in rejected archive.
                    source = {**current['source'],'_news_ai':{'category':result['category'],'confidence':result['confidence']}}
                    cur.execute('''UPDATE saas_linear_news SET title=%s,summary=%s,status=%s,source=%s::jsonb,updated_at=now()
                      WHERE tenant_id=%s::uuid AND id=%s::uuid AND status='pending' ''',
                      (result['title'], result['summary'], 'rejected' if result['excluded'] else 'pending', json.dumps(source), item['tenant_id'], item['id']))
            cur.execute("UPDATE saas_news_ai_runs SET status=%s,updated_at=now() WHERE tenant_id=%s::uuid AND news_id=%s::uuid AND status='sending'", (status, item['tenant_id'], item['id']))
        conn.commit()


def process_one(logger):
    item = claim()
    if not item:
        return
    result = None
    try:
        result = classify(item['api_key'], item['model'], item['title'], item['summary'])
    except Exception:
        logger.warning('Tenant news AI result unavailable; draft preserved')
    finish(item, result)
