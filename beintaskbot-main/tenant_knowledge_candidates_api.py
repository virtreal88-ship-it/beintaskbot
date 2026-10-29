"""Same-origin APIs for manual knowledge extraction and employee review."""
import asyncio
import re
import uuid
from tenant_read_timing import ReadTiming
from aiohttp import web
import tenant_knowledge_batches as batches
import tenant_knowledge_campaigns as campaigns
_batch_tasks=set()

async def _run_batch(profile,batch_id,logger):
    try: await asyncio.to_thread(batches.process,profile,batch_id)
    except Exception: logger.error("Knowledge batch interrupted")

from tenant_ai_agents_api import _expected, _origin
from tenant_linear_policy import TenantLinearError
from tenant_knowledge_candidates_service import analyze
from tenant_knowledge_improve import improve
from tenant_knowledge_candidates_store import conversation_link, detail, listing, mutate, request_detail, source_review_preflight

def handler(member_from_request,canonical_origin,logger,resource='collection'):
    async def handle(request):
        headers={'Cache-Control':'no-store'}
        timing=ReadTiming(3,logger) if request.method=='GET' and resource=='conversation' else None
        try:
            profile=timing.authenticate(member_from_request,request) if timing else member_from_request(request)
            if not profile: raise TenantLinearError('Нет доступа.',403)
            if request.method=='GET':
                _expected(request.query,profile)
                if resource=='campaign_preview':
                    if request.query.get('all_existing_cases')=='true':
                        if set(request.query)-{'all_existing_cases','acknowledge_prior_unknown','analysis_agent_id','expected_tenant_id','expected_user_id'}:
                            raise TenantLinearError('Некорректные параметры предварительного просмотра.')
                        retry_ack=request.query.get('acknowledge_prior_unknown','false')
                        if retry_ack not in {'true','false'}:
                            raise TenantLinearError('Некорректное подтверждение повторной попытки.')
                        result=await asyncio.to_thread(campaigns.preview_existing_cases,profile,
                            acknowledge_prior_unknown=retry_ack=='true',
                            analysis_agent_id=request.query.get('analysis_agent_id'))
                    else:
                        result=await asyncio.to_thread(campaigns.preview,profile,request.query.getall('channel_id',[]))
                elif resource=='campaigns': result=await asyncio.to_thread(campaigns.get,profile)
                elif resource=='campaign': result=await asyncio.to_thread(campaigns.get,profile,request.match_info['campaign_id'])
                elif resource=='campaign_case_continuation':
                    if set(request.query)-{'expected_version','expected_tenant_id','expected_user_id'}:raise TenantLinearError('Invalid continuation preview.',400)
                    try:version=int(request.query.get('expected_version',''))
                    except ValueError:raise TenantLinearError('Invalid campaign version.',400) from None
                    from tenant_case_continuation_ui import preview_owner
                    result=await asyncio.to_thread(preview_owner,profile,request.match_info['campaign_id'],version)
                elif resource=='batch': result=await asyncio.to_thread(batches.get,profile,request.match_info['batch_id'])
                elif resource=='request': result=await asyncio.to_thread(request_detail,profile,request.match_info['request_id'])
                elif resource=='candidate': result=await asyncio.to_thread(detail,profile,request.match_info['candidate_id'])
                elif resource=='source_review_preflight':
                    if set(request.query)-{'message_id','expected_version','expected_tenant_id','expected_user_id'}:
                        raise TenantLinearError('Некорректные параметры диагностики источника.')
                    try: expected_version=int(request.query.get('expected_version',''))
                    except ValueError: raise TenantLinearError('Некорректная версия карточки.') from None
                    result=await asyncio.to_thread(source_review_preflight,profile,request.match_info['candidate_id'],request.query.get('message_id'),expected_version)
                elif resource=='conversation':
                    if set(request.query)-{'message_id','review_run_id','expected_version','expected_tenant_id','expected_user_id'}:
                        raise TenantLinearError('Invalid conversation selection.',400)
                    try: expected_version=int(request.query['expected_version']) if 'expected_version' in request.query else None
                    except ValueError: raise TenantLinearError('Invalid case version.',400) from None
                    if expected_version is not None and (not request.query.get('message_id') or request.query.get('review_run_id')):
                        raise TenantLinearError('Invalid attachment selection.',400)
                    result=await timing.run(conversation_link,profile,request.match_info['candidate_id'],request.query.get('message_id'),request.query.get('review_run_id'),expected_version)
                else: result=await asyncio.to_thread(listing,profile,dict(request.query))
            else:
                _origin(request,canonical_origin)
                if request.content_type!='application/json': raise TenantLinearError('Ожидается JSON.')
                data=await request.json()
                if not isinstance(data,dict): raise TenantLinearError('Некорректный запрос.')
                _expected(data,profile)
                data={k:v for k,v in data.items() if k not in ('expected_tenant_id','expected_user_id')}
                if resource=='campaigns':
                    key=await asyncio.to_thread(campaigns.prepare,profile,data)
                    result=await asyncio.to_thread(campaigns.get,profile,key)
                elif resource=='campaign_start': result=await asyncio.to_thread(campaigns.approve,profile,request.match_info['campaign_id'],data)
                elif resource=='campaign_control':
                    if set(data)!={'expected_version','action'}: raise TenantLinearError('Некорректные параметры.')
                    await asyncio.to_thread(campaigns.control,profile,request.match_info['campaign_id'],data['expected_version'],data['action'])
                    result=await asyncio.to_thread(campaigns.get,profile,request.match_info['campaign_id'])
                elif resource=='campaign_case_continuation':
                    from tenant_case_continuation_ui import apply_owner
                    result=await asyncio.to_thread(apply_owner,profile,request.match_info['campaign_id'],data)
                elif resource=='campaign_current_media_check':
                    from tenant_case_current_media import verify_owner_source
                    result=await asyncio.to_thread(verify_owner_source,profile,request.match_info['campaign_id'],data)
                elif resource=='batches':
                    receipt=await asyncio.to_thread(batches.start,profile,data)
                    if receipt['created']:
                        task=asyncio.create_task(_run_batch(profile,receipt['id'],logger))
                        _batch_tasks.add(task);task.add_done_callback(_batch_tasks.discard)
                    result=await asyncio.to_thread(batches.get,profile,receipt['id'])
                    return web.json_response({'success':True,'result':result},status=202,headers=headers)
                elif resource=='improve': result=await asyncio.to_thread(improve,profile,request.match_info['candidate_id'],data)
                elif resource=='boundaries': result=await asyncio.to_thread(mutate,profile,request.match_info['candidate_id'],data,'boundaries')
                elif resource=='source_review': result=await asyncio.to_thread(mutate,profile,request.match_info['candidate_id'],data,'source_review')
                elif resource=='collection': result=await asyncio.to_thread(analyze,profile,data)
                else: result=await asyncio.to_thread(mutate,profile,request.match_info['candidate_id'],data,'edit' if resource=='candidate' else resource)
            return web.json_response({'success':True,'result':result},headers=headers)
        except campaigns.CampaignStartReadbackFailed as error:
            reference = uuid.uuid4().hex
            error_class = error.error_class if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', error.error_class) else 'UnknownError'
            logger.error('campaign_start stage=committed_readback error_class=%s reference=%s', error_class, reference)
            return web.json_response({'success':True,'result':{'start_committed':True,'readback_pending':True,'reference':reference}},headers=headers)
        except TenantLinearError as error:
            if resource=='campaign_current_media_check':
                code=getattr(error,'code',None)
                retry=getattr(error,'retry_after',None)
                if error.status==429 and isinstance(retry,int) and 1<=retry<=3600:headers['Retry-After']=str(retry)
                return web.json_response({'success':False,'error':str(error),'code':code or 'source_access_or_state','provider':'connect'},status=error.status,headers=headers)
            return web.json_response({'success':False,'error':str(error)},status=error.status,headers=headers)
        except (ValueError,TypeError,KeyError):
            if resource=='campaign_current_media_check':
                logger.error('case_media_check stage=internal_failure error_class=invalid_data')
                return web.json_response({'success':False,'error':'Не удалось проверить источник. Код: source_check_failed.','code':'source_check_failed','provider':'connect'},status=503,headers=headers)
            return web.json_response({'success':False,'error':'Некорректные параметры.'},status=400,headers=headers)
        except Exception as error:
            if resource in {'campaign_start','campaigns','campaign','campaign_current_media_check'}:
                reference = uuid.uuid4().hex
                error_class = type(error).__name__
                if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', error_class): error_class = 'UnknownError'
                stage = 'source_check_failed' if resource=='campaign_current_media_check' else ('start_unconfirmed' if resource == 'campaign_start' else ('campaign_read' if request.method == 'GET' else 'campaign_prepare'))
                logger.error('campaign_operation stage=%s error_class=%s reference=%s', stage, error_class, reference)
                if resource=='campaign_current_media_check':
                    return web.json_response({'success':False,'error':f'Не удалось проверить источник. Код: source_check_internal; номер: {reference}',
                      'code':'source_check_internal','reference':reference},status=503,headers=headers)
                return web.json_response({'success':False,'error':f'Операция временно недоступна. Код: {reference}'},status=503,headers=headers)
            logger.error('Knowledge candidate operation unavailable')
            return web.json_response({'success':False,'error':'Операция временно недоступна.'},status=503,headers=headers)
        finally:
            if timing: timing.finish()
    return handle
