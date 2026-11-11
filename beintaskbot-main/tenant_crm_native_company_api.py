"""Tenant-authorized native customer company API."""
import asyncio
from datetime import date,datetime
from uuid import UUID
from aiohttp import web
from tenant_crm_native_companies import create_company,get_company,list_companies,update_company,archive_company,link_entity,unlink_entity
from tenant_request_origin import require_json_post
from tenant_linear_policy import TenantLinearError
from tenant_policy import TenantPolicy
from tenant_scoped_access import scoped_access_enabled
from tenant_crm_native_access import CRMAccessError
from tenant_crm_request_context import require_expected_context, CRMContextError, native_policy_profile


def _jsonable(v):
    if isinstance(v,UUID):return str(v)
    if isinstance(v,(date,datetime)):return v.isoformat()
    if isinstance(v,dict):return {k:_jsonable(x) for k,x in v.items()}
    if isinstance(v,list):return [_jsonable(x) for x in v]
    return v


def relation_actions(policy):
    """Use the same effective CRUD permissions as the native editing UI."""
    update = policy.allows_action('companies', 'update')
    delete = policy.allows_action('companies', 'delete')
    return {'link_contact': update,
            'link_deal': update if scoped_access_enabled() else update and policy.allows_action('deals', 'update'),
            'unlink_contact': update if scoped_access_enabled() else delete,
            'unlink_deal': update if scoped_access_enabled() else delete}


def handler(member_from_request,origin,logger):
    async def handle(request):
        profile=member_from_request(request)
        if not profile:
            return web.json_response({'success':False,'error':'forbidden'},status=403,headers={'Cache-Control':'no-store'})
        profile = native_policy_profile(profile)
        policy=TenantPolicy(profile)
        tenant=str(profile['tenant_id']); ident=request.match_info.get('id')
        actor = profile.get('telegram_id') or 0
        if ident:
            try:UUID(ident)
            except (TypeError,ValueError):return web.json_response({'success':False,'error':'invalid_id'},status=400)
        try:
            require_expected_context(request, profile)
            if request.method=='GET':
                if not policy.allows_action('companies','view'):
                    return web.json_response({'success':False,'error':'forbidden'},status=403,headers={'Cache-Control':'no-store'})
                result=await asyncio.to_thread(get_company,tenant,ident,actor_id=actor) if ident else await asyncio.to_thread(list_companies,tenant,request.query.get('search',''),50,actor_id=actor)
                if ident and result is None:return web.json_response({'success':False,'error':'not_found'},status=404)
                return web.json_response({'success':True,('company' if ident else 'companies'):_jsonable(result)},headers={'Cache-Control':'no-store'})
            require_json_post(request,origin); body=await request.json()
            if not isinstance(body,dict):return web.json_response({'success':False,'error':'invalid_body'},status=400)
            action = body.get('action')
            # Link/archive operations require an existing company. Otherwise
            # their update/delete permission could authorize a new company.
            allowed_actions = ('update', 'archive', 'link_contact', 'link_deal',
                               'unlink_contact', 'unlink_deal') if ident else (None, 'create')
            if action not in allowed_actions:
                return web.json_response({'success': False, 'error': 'invalid_action'}, status=400)
            permission_action = 'delete' if action == 'archive' else 'create' if not ident else 'update'
            relations = relation_actions(policy)
            allowed = relations[action] if action in relations else policy.allows_action('companies', permission_action)
            if not allowed:
                return web.json_response({'success':False,'error':'forbidden'},status=403,headers={'Cache-Control':'no-store'})
            if scoped_access_enabled() and action in {'link_contact', 'unlink_contact', 'link_deal', 'unlink_deal'} and set(body) != {'action', 'entity_id'}:
                raise ValueError('invalid relation fields')
            if body.get('action')=='archive' and ident:
                result=await asyncio.to_thread(archive_company,tenant,ident,actor_id=actor)
            elif body.get('action') in ('link_contact','link_deal') and ident:
                result=await asyncio.to_thread(link_entity,tenant,ident,'contact' if body['action']=='link_contact' else 'deal',body.get('entity_id'),actor_id=actor)
            elif body.get('action') in ('unlink_contact','unlink_deal') and ident:
                result=await asyncio.to_thread(unlink_entity,tenant,ident,'contact' if body['action']=='unlink_contact' else 'deal',body.get('entity_id'),actor_id=actor)
            elif ident and body.get('action')=='update':
                result=await asyncio.to_thread(update_company,tenant,ident,body.get('name'),body.get('phone',''),body.get('email',''),body.get('description',''),body.get('responsible_id'),actor_id=actor)
            elif not ident:
                result=await asyncio.to_thread(create_company,tenant,body.get('name'),body.get('phone',''),body.get('email',''),body.get('description',''),body.get('responsible_id'),company_id=body.get('id'),actor_id=actor)
            else:return web.json_response({'success':False,'error':'method_not_allowed'},status=405)
            if result is None or result is False:return web.json_response({'success':False,'error':'not_found'},status=404)
            return web.json_response({'success':True,'data':_jsonable(result)},headers={'Cache-Control':'no-store'})
        except CRMContextError as exc:
            return web.json_response({'success':False,'error':exc.code,'code':exc.code},status=exc.status,
                                     headers={'Cache-Control':'no-store','Vary':'Cookie'})
        except CRMAccessError as exc:
            return web.json_response({'success': False, 'error': exc.code}, status=exc.status,
                                     headers={'Cache-Control': 'no-store'})
        except TenantLinearError:return web.json_response({'success':False,'error':'invalid_origin'},status=403)
        except (ValueError,TypeError):return web.json_response({'success':False,'error':'invalid_company'},status=400)
        except Exception:
            logger.exception('Native company operation failed tenant=%s',tenant)
            return web.json_response({'success':False,'error':'company_operation_failed'},status=503)
    return handle
