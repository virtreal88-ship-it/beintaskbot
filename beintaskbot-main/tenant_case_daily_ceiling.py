"""Versioned owner approval for one original case campaign's daily ceiling."""
from datetime import datetime, timezone
from tenant_linear_policy import TenantLinearError, identifier
from tenant_platform import _connect, _json
import tenant_knowledge_candidates_store as store

BASE=50
CEILING=150


def effective(cur,tenant,campaign_id,base):
    """Keep the shared counter; extend only a specifically approved campaign."""
    if base!=BASE or not campaign_id:return base
    cur.execute('''SELECT c.approved_by,c.approved_at,c.max_cost_microusd,c.progress,
      g.approved_by AS grant_approved_by,g.approved_at AS grant_approved_at,g.status,g.cap_microusd
      FROM saas_knowledge_campaigns c JOIN saas_ai_media_grants g
      ON g.tenant_id=c.tenant_id AND g.id=c.id AND g.campaign_id=c.id
      WHERE c.tenant_id=%s::uuid AND c.id=%s::uuid''',(tenant,campaign_id))
    row=cur.fetchone()
    if not row:return base
    progress=row.get('progress') or {};approval=progress.get('daily_call_ceiling') or {}
    if (progress.get('mode')=='case_media_batch' and row.get('approved_at') and row.get('grant_approved_at')
        and row.get('status')=='active' and row.get('max_cost_microusd')==10000000 and row.get('cap_microusd')==10000000
        and str(row.get('approved_by'))==str(row.get('grant_approved_by'))==str(approval.get('owner_id'))
        and approval.get('base_limit')==BASE and approval.get('ceiling')==CEILING and approval.get('approved_at')):
        return CEILING
    return base


def approve(profile,campaign_id,expected_version):
    campaign_id=identifier(campaign_id)
    if type(expected_version) is not int:raise TenantLinearError('Некорректная версия.',400)
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,actor,policy=store.authorize(cur,profile)
            if policy.profile.get('role')!='owner':raise TenantLinearError('Только владелец может подтвердить лимит.',403)
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-chat-ai:'+tenant,))
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('knowledge-campaign:'+tenant,))
            cur.execute('SELECT * FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,campaign_id))
            row=cur.fetchone()
            if not row:raise TenantLinearError('Кампания не найдена.',404)
            if row['version']!=expected_version:raise TenantLinearError('Кампания изменилась. Обновите состояние.',409)
            if row['status']!='paused' or not row.get('approved_at') or str(row.get('approved_by'))!=str(actor) or (row.get('progress') or {}).get('mode')!='case_media_batch' or row.get('max_cost_microusd')!=10000000:
                raise TenantLinearError('Исходная разрешённая кампания должна быть на паузе.',409)
            cur.execute('''SELECT approved_by,approved_at,status,cap_microusd FROM saas_ai_media_grants
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND campaign_id=%s::uuid FOR UPDATE''',(tenant,campaign_id,campaign_id))
            grant=cur.fetchone()
            if not grant or grant.get('status')!='active' or not grant.get('approved_at') or str(grant.get('approved_by'))!=str(actor) or grant.get('cap_microusd')!=10000000:
                raise TenantLinearError('Исходное разрешение недоступно.',409)
            cur.execute("SELECT metadata->>'daily_limit' AS value FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'",(tenant,))
            raw=(cur.fetchone() or {}).get('value')
            try:base=int(raw or 50) or 50
            except (TypeError,ValueError):raise TenantLinearError('Не удалось проверить суточный лимит.',409) from None
            if base!=BASE:raise TenantLinearError('Суточный лимит компании изменился; обновите состояние.',409)
            cur.execute("SELECT 1 FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND status='running' LIMIT 1",(tenant,campaign_id))
            if cur.fetchone():raise TenantLinearError('Дождитесь завершения текущего фрагмента.',409)
            progress=dict(row.get('progress') or {})
            progress['daily_call_ceiling']={'base_limit':BASE,'ceiling':CEILING,'owner_id':str(actor),'approved_at':datetime.now(timezone.utc).isoformat()}
            cur.execute('UPDATE saas_knowledge_campaigns SET progress=%s::jsonb,version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid',(_json(progress),tenant,campaign_id))
            store._audit(cur,tenant,actor,'knowledge_campaign_daily_ceiling_approved','knowledge_campaign',campaign_id,{'base_limit':BASE,'ceiling':CEILING})
        conn.commit()
    return {'daily_call_ceiling':CEILING}
