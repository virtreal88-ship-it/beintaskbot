"""Testing-center catalogue uses tenant-owned cache and read-only Kommo lookups."""
from tenant_platform import _connect
from tenant_ai_agents_schema import ensure_schema
from tenant_ai_agents_store import _authorize
from tenant_linear_policy import TenantLinearError


def catalog(profile,search='',lead_id=None):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,_=_authorize(cur,profile)
            cur.execute("SELECT account_domain FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='kommo' AND status='connected'",(tenant,));account=cur.fetchone()
            if not account:raise TenantLinearError('Подключите Kommo для этой компании.',409)
            if lead_id is None:
                cur.execute("SELECT kommo_lead_id AS id,name,contact_name FROM saas_crm_deals WHERE tenant_id=%s::uuid AND deleted_at IS NULL AND concat_ws(' ',name,contact_name,phone,kommo_lead_id::text) ILIKE %s ORDER BY synced_at DESC,kommo_lead_id LIMIT 50",(tenant,'%'+str(search)[:120]+'%'))
                return tenant,{'account_domain':account['account_domain'],'deals':[dict(r) for r in cur.fetchall()]}
            try:lead_id=int(lead_id)
            except (ValueError,TypeError):raise TenantLinearError('Выберите сделку.') from None
            if lead_id<=0:raise TenantLinearError('Выберите сделку.')
    return tenant,{'account_domain':account['account_domain'],'lead_id':lead_id}

async def kommo_context(profile,request_provider,search='',lead_id=None):
    import asyncio
    tenant,result=await asyncio.to_thread(catalog,profile,search,lead_id)
    if lead_id is not None:
        from tenant_chat_reader import read_talks
        lead=await request_provider(tenant,'GET',f"leads/{result['lead_id']}",params={'with':'contacts'})
        if int(lead.get('id') or 0)!=result['lead_id']:raise TenantLinearError('Сделка не найдена в подключённой компании.',404)
        contacts=(lead.get('_embedded') or {}).get('contacts') or []
        result['contacts']=[{'id':int(c['id']),'name':str(c.get('name') or '')} for c in contacts if isinstance(c,dict) and str(c.get('id') or '').isdigit()][:50]
        talks=await read_talks(tenant,result['lead_id'],request_provider)
        result['talks']=[{'talk_id':str(r.get('talk_id') or r.get('id') or ''),'chat_id':str(r.get('chat_id') or ''),'channel_id':str(r.get('origin') or '')} for r in talks if r.get('chat_id')][:50]
        # Membership may have been revoked during a remote read.
        _,current=await asyncio.to_thread(catalog,profile,search,lead_id)
        if current['account_domain']!=result['account_domain']:raise TenantLinearError('Подключение Kommo изменилось. Повторите выбор.',409)
    return result
