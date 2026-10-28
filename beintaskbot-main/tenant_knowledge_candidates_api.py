"""Same-origin APIs for manual knowledge extraction and employee review."""
import asyncio
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
                    if set(request.query)-{'message_id','review_run_id','expected_tenant_id','expected_user_id'}:
                        raise TenantLinearError('Invalid conversation selection.',400)
                    result=await timing.run(conversation_link,profile,request.match_info['candidate_id'],request.query.get('message_id'),request.query.get('review_run_id'))
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
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error)},status=error.status,headers=headers)
        except (ValueError,TypeError,KeyError):
            return web.json_response({'success':False,'error':'Некорректные параметры.'},status=400,headers=headers)
        except Exception:
            logger.error('Knowledge candidate operation unavailable')
            return web.json_response({'success':False,'error':'Операция временно недоступна.'},status=503,headers=headers)
        finally:
            if timing: timing.finish()
    return handle
