"""Same-origin tenant APIs for manual agent configuration and knowledge."""
import asyncio
import os
from aiohttp import web
from tenant_ai_agents_store import (delete_agent, delete_knowledge, list_agents,
    list_drafts, list_knowledge, read_settings, save_agent, save_knowledge, save_settings, configuration_options, refresh_configuration_catalog,
    list_triggers, save_trigger, delete_trigger, set_conversation_state, read_conversation_state, read_conversation_for_lead, review_draft, list_handoffs, list_connect_diagnostics,
    reserve_draft_send,finish_draft_send)
from tenant_ai_knowledge_files import extract
from tenant_linear_policy import TenantLinearError
from tenant_chat_send_service import run as send_chat_message
from tenant_ai_agent_test_mode import (read_test_mode, save_test_mode, verify_test_mode,
    discover_test_talks, assert_expected_route)
from tenant_ai_agent_runtime_flags import (configured as runtime_configured,
    enabled as runtime_flag, staging_mode as runtime_staging_mode)


def _expected(data, profile):
    if (str(data.get('expected_tenant_id') or '') != str(profile.get('tenant_id'))
            or str(data.get('expected_user_id') or '') != str(profile.get('telegram_id'))):
        raise TenantLinearError('Kabinet dəyişib. Yeniləyin.', 409)


def _origin(request, canonical_origin):
    if request.method not in {'POST', 'PATCH', 'DELETE'} or request.headers.get('Origin', '').rstrip('/') != canonical_origin.rstrip('/'):
        raise TenantLinearError('Sorğunun mənbəyi düzgün deyil.', 403)


def _json_body(request):
    return request.content_type == 'application/json'


def _attach_runtime_status(result):
    runtime = result.setdefault('runtime', {})
    runtime.update({
        'configured_ai_agents_enabled': runtime_configured('AI_AGENTS_ENABLED'),
        'configured_auto_send_enabled': runtime_configured('AI_AGENTS_AUTO_SEND_ENABLED'),
        'server_ai_agents_enabled': runtime_flag('AI_AGENTS_ENABLED'),
        'server_auto_send_enabled': runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED'),
        'server_crm_actions_enabled': runtime_flag('AI_AGENTS_CRM_ENABLED'),
        'staging_mode': runtime_staging_mode(),
    })
    runtime.update({
        'service_name': str(os.environ.get('RAILWAY_SERVICE_NAME') or ''),
        'service_id': str(os.environ.get('RAILWAY_SERVICE_ID') or ''),
        'deployment_id': str(os.environ.get('RAILWAY_DEPLOYMENT_ID') or ''),
        'environment_name': str(os.environ.get('RAILWAY_ENVIRONMENT_NAME') or ''),
        'environment_id': str(os.environ.get('RAILWAY_ENVIRONMENT_ID') or ''),
    })
    # Keep the established fields for older clients.
    result['server_ai_agents_enabled'] = runtime['server_ai_agents_enabled']
    result['server_auto_send_enabled'] = runtime['server_auto_send_enabled']
    result['server_crm_actions_enabled'] = runtime['server_crm_actions_enabled']
    return result


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
                    result = _attach_runtime_status(result)
                elif resource == 'diagnostics':
                    # Keep diagnostics in the GET dispatch.  The generic GET
                    # fallback below returns the knowledge list, which used
                    # to look like a successful but empty diagnostics result
                    # to the browser (false flags, no tenant, zero channels).
                    result = await asyncio.to_thread(list_connect_diagnostics, profile, request.query.get('limit', 50))
                elif resource == 'test_mode':
                    result = await asyncio.to_thread(read_test_mode, profile)
                elif resource == 'agents':
                    result = await asyncio.to_thread(list_agents, profile)
                elif resource == 'options':
                    # Use the tenant's existing Kommo adapter, not a browser
                    # supplied account.  This keeps the pick-list current and
                    # prevents saved agent scopes from narrowing it.
                    if request_provider is not None and request.query.get('cached') != '1':
                        try:
                            remote = await request_provider(profile['tenant_id'], 'GET', 'leads/pipelines')
                        except Exception:
                            # The transport is optional. Keep agent settings and
                            # the last tenant-owned catalog usable while Kommo
                            # is disconnected or temporarily unavailable.
                            logger.warning('AI Agents pipeline catalog unavailable; using cached tenant configuration')
                            result = await asyncio.to_thread(configuration_options, profile)
                        else:
                            result = await asyncio.to_thread(
                                refresh_configuration_catalog, profile,
                                ((remote.get('_embedded') or {}).get('pipelines') or []),
                            )
                    else:
                        result = await asyncio.to_thread(configuration_options, profile)
                elif resource == 'handoffs':
                    result = await asyncio.to_thread(list_handoffs, profile, request.query.get('limit', 50))
                elif resource == 'drafts':
                    result = await asyncio.to_thread(list_drafts, profile, limit=request.query.get('limit', 50))
                elif resource == 'triggers':
                    result = await asyncio.to_thread(list_triggers,profile,request.match_info['agent_id'])
                elif resource == 'conversation_state':
                    # Provider/channel originate from a same-origin request but
                    # are still verified against the authorized tenant in the
                    # store before any state is read.
                    result = await asyncio.to_thread(read_conversation_state, profile,
                        request.match_info['conversation_id'], request.query.get('provider', 'kommo'),
                        request.query.get('channel_id', ''))
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
                    # Return a fresh, tenant-scoped read after saving.  The
                    # old response omitted server flags, which made the UI
                    # show a just-saved auto-send switch as disabled.
                    await asyncio.to_thread(save_settings, profile, payload)
                    result = await asyncio.to_thread(read_settings, profile)
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
                        if reserved.get('provider') == 'connect':
                            from tenant_connect_transport import send_ai_reply_if_active
                            message_id = await asyncio.to_thread(send_ai_reply_if_active,
                                reserved['tenant_id'], reserved['lead_id'], reserved['channel_id'],
                                reserved['conversation_id'], reserved['draft'], reserved['id'],
                                reserved['expected_revision'], **({'handoff_fingerprint':reserved['source_fingerprint']}
                                    if reserved.get('handoff_after_send') else {}))
                            if not message_id:
                                raise TenantLinearError('Qaralama köhnəlib və ya dialoq menecerə verilib.', 409)
                            result = {'state': 'accepted', 'message_id': message_id, 'origin': 'connect'}
                        else:
                            # The reviewed draft must still point to the exact
                            # live Kommo chat before its normal sender resolves
                            # a route.  This guard used to be passed as an
                            # unsupported keyword and therefore never ran.
                            await assert_expected_route(reserved['tenant_id'], reserved['lead_id'],
                                reserved['expected_route'], request_provider)
                            result=await send_chat_message(profile,{'request_id':reserved['id'],'lead_id':reserved['lead_id'],'text':reserved['draft']},request_provider,wait_slot,logger)
                    except TenantLinearError as error:
                        await asyncio.to_thread(finish_draft_send,reserved['tenant_id'],reserved['id'],'unknown' if getattr(error,'keep_request',False) else 'blocked')
                        raise
                    except Exception:
                        await asyncio.to_thread(finish_draft_send,reserved['tenant_id'],reserved['id'],'unknown')
                        raise
                    await asyncio.to_thread(finish_draft_send,reserved['tenant_id'],reserved['id'],'accepted')
                elif resource == 'handoffs':
                    result = await asyncio.to_thread(list_handoffs,profile,request.query.get('limit',50))
                elif resource in {'handoff','resume','pause'} and method == 'POST':
                    result = await asyncio.to_thread(set_conversation_state,profile,request.match_info['conversation_id'],
                        {'handoff':'human','resume':'active','pause':'paused'}[resource],
                        str(payload.get('reason') or {'handoff':'manual_handoff','resume':'manager_resume','pause':'manager_pause'}[resource]),
                        str(payload.get('provider') or 'kommo'),str(payload.get('channel_id') or ''))
                elif resource == 'knowledge' and method == 'POST':
                    result = await asyncio.to_thread(save_knowledge, profile, payload)
                elif resource == 'knowledge_item' and method == 'PATCH':
                    result = await asyncio.to_thread(save_knowledge, profile, payload, source_id=request.match_info['source_id'])
                elif resource == 'knowledge_item' and method == 'DELETE':
                    result = await asyncio.to_thread(delete_knowledge, profile, request.match_info['source_id'])
                else:
                    raise TenantLinearError('Əməliyyat dəstəklənmir.', 405)
            if resource == 'settings':
                result = _attach_runtime_status(result)
            return web.json_response({'success': True, 'result': result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success': False, 'error': str(error)}, status=error.status, headers=headers)
        except (ValueError, TypeError, KeyError):
            return web.json_response({'success': False, 'error': 'Sorğu düzgün deyil.'}, status=400, headers=headers)
        except Exception:
            logger.error('Tenant AI Agents request failed')
            return web.json_response({'success': False, 'error': 'AI Agents xidməti əlçatan deyil.'}, status=503, headers=headers)
    return handle

