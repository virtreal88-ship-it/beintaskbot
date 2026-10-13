"""Same-origin tenant APIs for manual agent configuration and knowledge."""
import asyncio
from aiohttp import web
from tenant_ai_agents_store import (delete_agent, delete_knowledge, list_agents,
    list_drafts, list_knowledge, read_settings, save_agent, save_knowledge, save_settings,
    list_triggers, save_trigger, delete_trigger, set_conversation_state, read_conversation_state, read_conversation_for_lead, review_draft, list_handoffs,
    reserve_draft_send,finish_draft_send)
from tenant_ai_knowledge_files import extract
from tenant_linear_policy import TenantLinearError
from tenant_chat_send_service import run as send_chat_message
from tenant_ai_agent_test_mode import read_test_mode, save_test_mode, verify_test_mode, discover_test_talks
from tenant_ai_agent_runtime_flags import enabled as runtime_flag


def _expected(data, profile):
    if (str(data.get('expected_tenant_id') or '') != str(profile.get('tenant_id'))
            or str(data.get('expected_user_id') or '') != str(profile.get('telegram_id'))):
        raise TenantLinearError('Kabinet dəyişib. Yeniləyin.', 409)


def _origin(request, canonical_origin):
    if request.method not in {'POST', 'PATCH', 'DELETE'} or request.headers.get('Origin', '').rstrip('/') != canonical_origin.rstrip('/'):
        raise TenantLinearError('Sorğunun mənbəyi düzgün deyil.', 403)


def _json_body(request):
    return request.content_type == 'application/json'


def handler(member_from_request, canonical_origin, logger, resource, request_provider=None, wait_slot=None):
    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            profile = member_from_request(request)
            if not profile:
                raise TenantLinearError('İcazə yoxdur.', 403)
            method = request.method
            if method == 'GET':
                if resource == 'settings':
                    result = await asyncio.to_thread(read_settings, profile)
                    result['server_auto_send_enabled']=runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED')
                    result['server_crm_actions_enabled']=runtime_flag('AI_AGENTS_CRM_ENABLED')
                    result['server_ai_agents_enabled']=runtime_flag('AI_AGENTS_ENABLED')
                elif resource == 'test_mode':
                    result = await asyncio.to_thread(read_test_mode, profile)
                elif resource == 'agents':
                    result = await asyncio.to_thread(list_agents, profile)
                elif resource == 'drafts':
                    result = await asyncio.to_thread(list_drafts, profile, limit=request.query.get('limit', 50))
                elif resource == 'triggers':
                    result = await asyncio.to_thread(list_triggers,profile,request.match_info['agent_id'])
                elif resource == 'conversation_state':
                    result = await asyncio.to_thread(read_conversation_state,profile,request.match_info['conversation_id'])
                elif resource == 'conversation_for_lead':
                    result = await asyncio.to_thread(read_conversation_for_lead,profile,request.match_info['lead_id'])
                else:
                    result = await asyncio.to_thread(list_knowledge, profile, request.query.get('id'))
                return web.json_response({'success': True, 'result': result}, headers=headers)
            _origin(request, canonical_origin)
            if request.content_length is not None and request.content_length > 6 * 1024 * 1024:
                raise TenantLinearError('Sorğu həddən artıq böyükdür.', 413)
            if resource == 'knowledge_upload':
                if not request.content_type.startswith('multipart/form-data'):
                    raise TenantLinearError('Multipart fayl sorğusu tələb olunur.', 415)
                if request.content_length is not None and request.content_length > 6 * 1024 * 1024:
                    raise TenantLinearError('Sorğu həddən artıq böyükdür.', 413)
                reader = await request.multipart()
                fields, uploaded = {}, None
                total = 0
                while part := await reader.next():
                    if part.filename:
                        if uploaded is not None:
                            raise TenantLinearError('Bir sorğuda yalnız bir fayl yükləmək olar.')
                        pieces = bytearray()
                        while chunk := await part.read_chunk(64 * 1024):
                            total += len(chunk)
                            if total > 5 * 1024 * 1024:
                                raise TenantLinearError('Fayl ölçüsü 5 MB-dan çox ola bilməz.', 413)
                            pieces.extend(chunk)
                        uploaded = (part.filename, bytes(pieces))
                    else:
                        value_parts, value_size = [], 0
                        while chunk := await part.read_chunk(4096):
                            value_size += len(chunk)
                            if value_size > 20_000:
                                raise TenantLinearError('Form sahəsi həddən artıq böyükdür.')
                            value_parts.append(chunk)
                        value = b''.join(value_parts).decode('utf-8', errors='strict')
                        if len(value) > 20_000:
                            raise TenantLinearError('Form sahəsi həddən artıq böyükdür.')
                        fields[part.name] = value
                _expected(fields, profile)
                if not uploaded:
                    raise TenantLinearError('Yüklənəcək faylı seçin.')
                filename, raw = uploaded
                safe_name, kind, content = await asyncio.to_thread(extract, filename, raw)
                title = str(fields.get('title') or safe_name).strip()[:180]
                result = await asyncio.to_thread(save_knowledge, profile,
                    {'title': title, 'kind': kind, 'content': content, 'active': True})
            else:
                if not _json_body(request):
                    raise TenantLinearError('JSON sorğusu tələb olunur.', 415)
                data = await request.json()
                if not isinstance(data, dict):
                    raise TenantLinearError('Sorğu düzgün deyil.')
                _expected(data, profile)
                payload = {key: value for key, value in data.items()
                           if key not in {'expected_tenant_id', 'expected_user_id'}}
                if resource == 'settings':
                    result = await asyncio.to_thread(save_settings, profile, payload)
                elif resource == 'test_mode':
                    result = await asyncio.to_thread(save_test_mode, profile, payload)
                elif resource == 'test_mode_verify' and method == 'POST':
                    if request_provider is None:
                        raise TenantLinearError('Kommo read service is unavailable.',503)
                    result = await verify_test_mode(profile, request_provider)
                elif resource == 'test_mode_discover' and method == 'POST':
                    if request_provider is None:
                        raise TenantLinearError('Kommo read service is unavailable.',503)
                    result = await discover_test_talks(profile,payload.get('lead_id'),payload.get('contact_id'),request_provider)
                elif resource == 'agents' and method == 'POST':
                    result = await asyncio.to_thread(save_agent, profile, payload)
                elif resource == 'agent' and method == 'PATCH':
                    result = await asyncio.to_thread(save_agent, profile, payload, agent_id=request.match_info['agent_id'])
                elif resource == 'agent' and method == 'DELETE':
                    result = await asyncio.to_thread(delete_agent, profile, request.match_info['agent_id'])
                elif resource == 'triggers' and method == 'POST':
                    result = await asyncio.to_thread(save_trigger,profile,request.match_info['agent_id'],payload)
                elif resource == 'trigger' and method == 'PATCH':
                    result = await asyncio.to_thread(save_trigger,profile,request.match_info['agent_id'],payload,trigger_id=request.match_info['trigger_id'])
                elif resource == 'trigger' and method == 'DELETE':
                    result = await asyncio.to_thread(delete_trigger,profile,request.match_info['agent_id'],request.match_info['trigger_id'])
                elif resource == 'draft_review' and method == 'POST':
                    result = await asyncio.to_thread(review_draft,profile,request.match_info['draft_id'],payload.get('state'))
                elif resource == 'draft_send' and method == 'POST':
                    reserved=await asyncio.to_thread(reserve_draft_send,profile,request.match_info['draft_id'])
                    try:
                        result=await send_chat_message(profile,{'request_id':reserved['id'],'lead_id':reserved['lead_id'],'text':reserved['draft']},request_provider,wait_slot,logger,expected_route=reserved['expected_route'])
                    except TenantLinearError as error:
                        await asyncio.to_thread(finish_draft_send,reserved['tenant_id'],reserved['id'],'unknown' if getattr(error,'keep_request',False) else 'blocked')
                        raise
                    except Exception:
                        await asyncio.to_thread(finish_draft_send,reserved['tenant_id'],reserved['id'],'unknown')
                        raise
                    await asyncio.to_thread(finish_draft_send,reserved['tenant_id'],reserved['id'],'accepted')
                elif resource == 'handoffs':
                    result = await asyncio.to_thread(list_handoffs,profile,request.query.get('limit',50))
                elif resource in {'handoff','resume'} and method == 'POST':
                    result = await asyncio.to_thread(set_conversation_state,profile,request.match_info['conversation_id'],
                        'human' if resource=='handoff' else 'active',str(payload.get('reason') or ('manual_handoff' if resource=='handoff' else 'manager_resume')))
                elif resource == 'knowledge' and method == 'POST':
                    result = await asyncio.to_thread(save_knowledge, profile, payload)
                elif resource == 'knowledge_item' and method == 'PATCH':
                    result = await asyncio.to_thread(save_knowledge, profile, payload, source_id=request.match_info['source_id'])
                elif resource == 'knowledge_item' and method == 'DELETE':
                    result = await asyncio.to_thread(delete_knowledge, profile, request.match_info['source_id'])
                else:
                    raise TenantLinearError('Əməliyyat dəstəklənmir.', 405)
            return web.json_response({'success': True, 'result': result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success': False, 'error': str(error)}, status=error.status, headers=headers)
        except (ValueError, TypeError, KeyError):
            return web.json_response({'success': False, 'error': 'Sorğu düzgün deyil.'}, status=400, headers=headers)
        except Exception:
            logger.error('Tenant AI Agents request failed')
            return web.json_response({'success': False, 'error': 'AI Agents xidməti əlçatan deyil.'}, status=503, headers=headers)
    return handle
