"""Same-origin tenant APIs for manual agent configuration and knowledge."""
import asyncio
import os
from aiohttp import web
from tenant_ai_agents_store import (delete_agent, delete_knowledge, list_agents,
    list_knowledge, read_settings, save_agent, save_knowledge, save_settings, configuration_options, refresh_configuration_catalog,
    list_triggers, save_trigger, delete_trigger, set_conversation_state, read_conversation_state, read_conversation_for_lead, list_handoffs, list_connect_diagnostics)
from tenant_ai_knowledge_files import extract
from tenant_linear_policy import TenantLinearError
from tenant_ai_agent_runtime_flags import (configured as runtime_configured,
    enabled as runtime_flag, staging_mode as runtime_staging_mode)
from tenant_ai_expert_tools import registry_snapshot


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

        def validation_field(message=''):
            """Return a safe field identifier; never reflect request data."""
            text = str(message or '').casefold()
            if any(value in text for value in ('agent adı', 'agent ayarları')):
                return 'name'
            if 'təlimat' in text:
                return 'instructions'
            if 'saat qurşağı' in text:
                return 'timezone'
            if any(value in text for value in ('iş günləri', 'iş saatları', 'iş cədvəli')):
                return 'schedule'
            if 'model' in text:
                return 'model'
            if any(value in text for value in ('connect', 'whatsapp kanalı', 'mənbə və ya kanal', 'əhatə dairəsi')):
                return 'channel'
            if any(value in text for value in ('açar ifadə', 'trigger')):
                return 'handoff'
            if any(value in text for value in ('bilik', 'fayl', 'mətn', 'mənbə adı')):
                return 'knowledge_file'
            if any(value in text for value in ('icazə', 'üzvlüy', 'şirkət')):
                return 'permissions'
            return {
                'agents': 'configuration', 'agent': 'activation',
                'triggers': 'handoff', 'trigger': 'handoff',
                'knowledge_upload': 'knowledge_file', 'knowledge': 'knowledge_file',
            }.get(resource, 'configuration')
        try:
            profile = member_from_request(request)
            if not profile:
                raise TenantLinearError('İcazə yoxdur.', 403)
            method = request.method
            if method == 'GET':
                if resource == 'settings':
                    result = await asyncio.to_thread(read_settings, profile)
                    result = _attach_runtime_status(result)
                elif resource == 'reliability':
                    from tenant_ai_reliability import snapshot
                    result = await asyncio.to_thread(snapshot, profile, request.query.get('limit', 50))
                elif resource == 'diagnostics':
                    # Keep diagnostics in the GET dispatch.  The generic GET
                    # fallback below returns the knowledge list, which used
                    # to look like a successful but empty diagnostics result
                    # to the browser (false flags, no tenant, zero channels).
                    result = await asyncio.to_thread(list_connect_diagnostics, profile, request.query.get('limit', 50))
                elif resource == 'pilot_preflight':
                    from tenant_ai_pilot import preflight
                    result = await asyncio.to_thread(preflight, profile)
                elif resource == 'agents':
                    if request.query.get('view') == 'teams':
                        rows = await asyncio.to_thread(list_agents, profile)
                        teams=[]
                        for agent in rows:
                            triggers=await asyncio.to_thread(list_triggers,profile,agent['id'])
                            teams.append({
                                'id':agent['id'],'name':agent['name'],'role':agent.get('role') or 'primary',
                                'enabled':bool(agent.get('enabled')),'mode':agent.get('mode') or 'manual',
                                'expert_policy':agent.get('expert_policy') or {},'version':agent.get('version'),
                                'triggers':[{'id':item['id'],'name':item['name'],'trigger_type':item['trigger_type'],
                                    'enabled':bool(item.get('enabled')),'actions':item.get('actions') or [],
                                    'expert_agent_ids':(item.get('condition') or {}).get('expert_agent_ids') or [],
                                    'version':item.get('version')} for item in triggers],
                            })
                        result={'agents':teams,'tool_registry':registry_snapshot()}
                    else:
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
                    {'title': title, 'kind': kind, 'content': content, 'active': True,
                     **({'agent_id':fields['agent_id']} if fields.get('agent_id') else {}),
                     **({'setup_key':fields['setup_key']} if fields.get('setup_key') else {})})
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
                elif resource == 'pilot_kwid_confirmation' and method == 'POST':
                    from tenant_ai_pilot import confirm_kwid_disabled
                    result = await asyncio.to_thread(confirm_kwid_disabled, profile, payload)
                elif resource == 'pilot_owner_approval' and method == 'POST':
                    from tenant_ai_pilot import approve_pilot
                    result = await asyncio.to_thread(approve_pilot, profile, payload)
                elif resource == 'agents' and method == 'POST':
                    result = await asyncio.to_thread(save_agent, profile, payload)
                elif resource == 'agent' and method == 'PATCH':
                    result = await asyncio.to_thread(save_agent, profile, payload, agent_id=request.match_info['agent_id'])
                elif resource == 'agent' and method == 'DELETE':
                    result = await asyncio.to_thread(
                        delete_agent, profile, request.match_info['agent_id'],
                        replacement_agent_id=payload.get('replacement_agent_id'),
                        expected_version=payload.get('expected_version'))
                elif resource == 'triggers' and method == 'POST':
                    result = await asyncio.to_thread(save_trigger,profile,request.match_info['agent_id'],payload)
                elif resource == 'trigger' and method == 'PATCH':
                    result = await asyncio.to_thread(save_trigger,profile,request.match_info['agent_id'],payload,trigger_id=request.match_info['trigger_id'])
                elif resource == 'trigger' and method == 'DELETE':
                    result = await asyncio.to_thread(delete_trigger,profile,request.match_info['agent_id'],request.match_info['trigger_id'])
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
            field = validation_field(error)
            return web.json_response({'success': False, 'error': str(error),
                                      'code': f'invalid_{field}', 'field': field},
                                     status=error.status, headers=headers)
        except (ValueError, TypeError, KeyError) as error:
            # The previous broad response hid the failing wizard step and made
            # safe recovery impossible to explain.  Keep the implementation
            # detail in server logs only; the browser gets a stable field key.
            field = validation_field()
            logger.warning('Tenant AI Agents request validation failed for resource=%s type=%s',
                           resource, type(error).__name__, exc_info=True)
            return web.json_response({'success': False,
                                      'error': 'Sorğu emal edilə bilmədi.',
                                      'code': f'invalid_{field}', 'field': field},
                                     status=400, headers=headers)
        except Exception:
            logger.error('Tenant AI Agents request failed')
            return web.json_response({'success': False, 'error': 'AI Agents xidməti əlçatan deyil.'}, status=503, headers=headers)
    return handle
