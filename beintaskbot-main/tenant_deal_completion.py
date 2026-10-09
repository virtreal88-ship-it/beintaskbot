"""Explicit deal completion. Never triggered by completing a task."""
import asyncio
import hashlib
import json
import uuid
import logging
from tenant_policy import TenantPolicy, positive_id
from tenant_linear_policy import TenantLinearError
from tenant_platform import member, get_crm_deal, upsert_crm_deals, append_audit_event
from tenant_chat_import_store import authorize
from tenant_deal_completion_store import DealCommandStore, command, pending


def normalize(data: dict) -> dict:
    try:request=str(uuid.UUID(str(data.get('request_id',''))))
    except (ValueError,TypeError):raise TenantLinearError('Sorğu kodu düzgün deyil.') from None
    lead=positive_id(data.get('lead_id'));text=str(data.get('result_text') or '').strip()
    if not 0<lead<2**63 or len(text)>3500:raise TenantLinearError('Sövdələşmə və nəticə düzgün deyil.')
    return {'request_id':request,'lead_id':lead,'result_text':text}


async def current(session: dict, *, review: bool=False) -> dict:
    p=await asyncio.to_thread(member,str(session['tenant_id']),int(session['telegram_id']))
    policy=TenantPolicy(p or {})
    if (not p or p.get('active') is not True or p.get('tenant_status')!='active'
            or str(p.get('tenant_id'))!=str(session['tenant_id']) or not policy.allows('deals')
            or (review and not policy.privileged)):
        raise TenantLinearError('Sövdələşmə üçün icazəniz yoxdur.',403)
    return p


async def complete(session: dict, data: dict, provider, *, reviewer: dict | None=None) -> dict:
    profile=await current(session)
    if reviewer is not None:reviewer=await current(reviewer,review=True)
    if reviewer and str(reviewer['tenant_id'])!=str(profile['tenant_id']):raise TenantLinearError('İcazə yoxdur.',403)
    payload=normalize(data);tenant=str(profile['tenant_id']);actor=int(profile['telegram_id']);lead=payload['lead_id']
    fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    store=await asyncio.to_thread(DealCommandStore,tenant,actor,payload['request_id'],lead,fingerprint)
    try:
        if store.state.get('step')=='done':return store.state['result']
        if store.state.get('step')=='rejected':raise TenantLinearError('Sorğu rədd edilib.',409)
        if store.state.get('step')=='sending':raise TenantLinearError('Kommo nəticəsini yoxlayın. Təkrar tamamlamayın.',409)
        if reviewer and store.state.get('step')!='waiting_approval':raise TenantLinearError('Təsdiq sorğusu yoxdur.',409)
        connection=await asyncio.to_thread(authorize,profile,lead)
        if reviewer:await asyncio.to_thread(authorize,reviewer,lead)
        if store.state.get('connection') and connection!=store.state['connection']:
            raise TenantLinearError('Kommo hesabı dəyişib. Köhnə sorğunu rədd edin.',409)
        cached=await asyncio.to_thread(get_crm_deal,tenant_id=tenant,kommo_lead_id=lead)
        live=await provider(tenant,'GET',f'leads/{lead}')
        if positive_id(live.get('id'))!=lead or live.get('is_deleted') or not TenantPolicy(profile).can_access_deal({**live,'tenant_id':tenant}):
            raise TenantLinearError('Sövdələşmə artıq sizin vərəqinizdə deyil.',403)
        if reviewer and not TenantPolicy(reviewer).can_access_deal({**live,'tenant_id':tenant}):
            raise TenantLinearError('Təsdiq üçün sövdələşməyə giriş yoxdur.',403)
        if positive_id(live.get('status_id'))==143:raise TenantLinearError('İmtina edilmiş sövdələşməni tamamlamayın.',409)
        if store.state.get('pipeline_id') and positive_id(live.get('pipeline_id'))!=store.state['pipeline_id']:
            raise TenantLinearError('Sövdələşmənin vərəqi dəyişib. Sorğunu rədd edin.',409)
        if positive_id(live.get('status_id'))!=142:
            envelope={**payload,'connection':connection,'pipeline_id':positive_id(live.get('pipeline_id')),'name':live.get('name','')}
            if reviewer is None and (store.state.get('step')=='waiting_approval' or
                    (not TenantPolicy(profile).privileged and TenantPolicy(profile).requires_deal_approval())):
                await asyncio.to_thread(store.save,{**envelope,'step':'waiting_approval'})
                return {'approval_pending':True,'request_id':payload['request_id']}
            # Ensure the provider still exposes the canonical successful status.
            catalog=await provider(tenant,'GET',f"leads/pipelines/{live['pipeline_id']}/statuses")
            if not any(positive_id(s.get('id'))==142 for s in (catalog.get('_embedded') or {}).get('statuses',[])):
                raise TenantLinearError('Kommo-da uğurlu mərhələ tapılmadı.',409)
            profile=await current(profile)
            if reviewer:
                reviewer=await current(reviewer,review=True)
                await asyncio.to_thread(authorize,reviewer,lead)
                if not TenantPolicy(reviewer).can_access_deal({**live,'tenant_id':tenant}):raise TenantLinearError('İcazə yoxdur.',403)
            if await asyncio.to_thread(authorize,profile,lead)!=connection:raise TenantLinearError('Kommo hesabı dəyişib.',409)
            if not TenantPolicy(profile).can_access_deal({**live,'tenant_id':tenant}):raise TenantLinearError('İcazə yoxdur.',403)
            await asyncio.to_thread(store.save,{**envelope,'step':'sending','reviewer_id':int(reviewer['telegram_id']) if reviewer else None})
            response=await provider(tenant,'PATCH',f'leads/{lead}',json_body={'status_id':142},session=profile,connection=connection)
            if positive_id(response.get('id'))!=lead:raise RuntimeError('Unknown provider result')
            live={**live,**response,'status_id':142}
        result={'lead_id':lead,'completed':True}
        await asyncio.to_thread(store.save,{'step':'done','result':result})
        await asyncio.to_thread(upsert_crm_deals,tenant_id=tenant,deals=[{**(cached or {}),'kommo_lead_id':lead,
            'pipeline_id':positive_id(live.get('pipeline_id')),'status_id':142,'stage_name':'Uğurla tamamlandı','raw':live}])
        await asyncio.to_thread(append_audit_event,tenant_id=tenant,actor_telegram_id=actor,action='deal_completed',
            entity_type='kommo_lead',entity_id=str(lead),payload={**payload,'reviewer_id':int(reviewer['telegram_id']) if reviewer else None})
        return result
    except Exception:
        if store.state.get('step')=='done':
            logging.getLogger(__name__).error('Tenant deal completed; cache/audit unavailable')
            return {**store.state['result'],'warning':'Sövdələşmə tamamlandı. Siyahını yeniləyin.'}
        if store.state.get('step')=='sending':raise TenantLinearError('Kommo nəticəsi qeyri-müəyyəndir. Təkrar tamamlamayın.',409) from None
        raise
    finally:await asyncio.to_thread(store.close)


async def reviews(session: dict, data: dict | None, provider, *, limit: int=50, offset: int=0) -> dict:
    profile=await current(session,review=True)
    if data is None:
        if not 1<=limit<=100 or not 0<=offset<=100000:raise TenantLinearError('Səhifə düzgün deyil.')
        return await asyncio.to_thread(pending,profile,limit,offset)
    try:request=str(uuid.UUID(str(data.get('request_id',''))))
    except (ValueError,TypeError):raise TenantLinearError('Sorğu kodu düzgün deyil.') from None
    actor=positive_id(data.get('creator_id'));action=data.get('action')
    if not actor or action not in {'approve','reject'}:raise TenantLinearError('Sorğu düzgün deyil.')
    saved=await asyncio.to_thread(command,str(profile['tenant_id']),actor,request)
    deal=await asyncio.to_thread(get_crm_deal,tenant_id=str(profile['tenant_id']),kommo_lead_id=saved['lead_id'])
    if not deal or deal.get('deleted_at') or not TenantPolicy(profile).can_access_deal(deal):
        raise TenantLinearError('Təsdiq üçün sövdələşməyə giriş yoxdur.',403)
    payload=normalize({'request_id':request,'lead_id':saved['lead_id'],'result_text':saved['state'].get('result_text','')})
    if action=='approve':
        return await complete({**profile,'telegram_id':actor},payload,provider,reviewer=profile)
    store=await asyncio.to_thread(DealCommandStore,str(profile['tenant_id']),actor,request,payload['lead_id'],
        hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest())
    try:
        if store.state.get('step') not in {'waiting_approval','rejected'}:raise TenantLinearError('Əməliyyat başlayıb; rədd etmək olmaz.',409)
        await asyncio.to_thread(store.save,{'step':'rejected','reviewer_id':int(profile['telegram_id'])})
        return {'rejected':True}
    finally:await asyncio.to_thread(store.close)
