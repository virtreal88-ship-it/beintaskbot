"""Authenticated same-origin APIs, expected company/user checked before provider I/O."""
import asyncio
from aiohttp import web
from tenant_chat_ai_settings import read, save, available_models, test_model
from tenant_chat_ai_service import run
from tenant_linear_policy import TenantLinearError
from tenant_request_origin import require_json_post
from tenant_chat_ai_provider import ChatAIProviderError


def handler(member_from_request, canonical_origin, logger, *, settings=False):
    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            profile = member_from_request(request)
            if not profile:
                raise TenantLinearError('İcazə yoxdur.', 403)
            if request.method == 'GET' and settings:
                result = await asyncio.to_thread(read, profile)
            else:
                require_json_post(request, canonical_origin)
                data = await request.json()
                if not isinstance(data, dict):
                    raise TenantLinearError('Sorğu düzgün deyil.')
                if str(data.get('expected_tenant_id') or '') != str(profile['tenant_id']) or str(data.get('expected_user_id') or '') != str(profile['telegram_id']):
                    raise TenantLinearError('Kabinet dəyişib. Yeniləyin.', 409)
                result = await asyncio.to_thread(save, profile, data) if settings else await run(profile, data, logger)
            return web.json_response({'success': True, **result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success': False, 'error': str(error), 'keep_request': getattr(error, 'keep_request', False)}, status=error.status, headers=headers)
        except (ValueError, TypeError, KeyError):
            return web.json_response({'success': False, 'error': 'Sorğu düzgün deyil.'}, status=400, headers=headers)
        except Exception:
            logger.error('Tenant chat AI service unavailable')
            return web.json_response({'success': False, 'error': 'AI xidməti əlçatan deyil.'}, status=503, headers=headers)
    return handle


def model_management_handler(member_from_request, canonical_origin, logger, *, probe=False):
    """Owner-only model discovery and an explicit minimal, context-free probe."""
    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            profile = member_from_request(request)
            if not profile:
                raise TenantLinearError('Нет доступа.', 403)
            if probe:
                require_json_post(request, canonical_origin)
                data = await request.json()
                if not isinstance(data, dict):
                    raise TenantLinearError('Некорректный запрос.')
                if (str(data.get('expected_tenant_id') or '') != str(profile['tenant_id'])
                        or str(data.get('expected_user_id') or '') != str(profile['telegram_id'])):
                    raise TenantLinearError('Кабинет изменился. Обновите страницу.', 409)
                result = await asyncio.to_thread(test_model, profile, data.get('model'))
            else:
                if request.method != 'GET':
                    raise TenantLinearError('Метод не поддерживается.', 405)
                if (str(request.query.get('expected_tenant_id') or '') != str(profile['tenant_id'])
                        or str(request.query.get('expected_user_id') or '') != str(profile['telegram_id'])):
                    raise TenantLinearError('Кабинет изменился. Обновите страницу.', 409)
                result = await asyncio.to_thread(available_models, profile)
            return web.json_response({'success': True, **result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success': False, 'error': str(error)}, status=error.status, headers=headers)
        except ChatAIProviderError as error:
            messages = {
                'invalid_credentials': 'OpenAI отклонил сохранённый ключ. Проверьте существующий ключ компании.',
                'project_access': 'У сохранённого ключа нет доступа к проекту OpenAI.',
                'billing_or_quota': 'OpenAI сообщает об исчерпанном лимите или неактивной оплате.',
                'model_access': 'Эта модель недоступна сохранённому ключу. Выберите модель из списка и проверьте её.',
                'rate_limit': 'OpenAI временно ограничил запросы. Повторите позже.',
                'request_rejected': 'OpenAI отклонил запрос модели. Проверьте совместимость Responses API.',
            }
            logger.warning('Copilot model check failed category=%s status=%s', error.category, error.status_code)
            return web.json_response({'success': False, 'error': messages.get(error.category, 'Не удалось проверить модели OpenAI.')}, status=503, headers=headers)
        except Exception:
            logger.warning('Copilot model check unavailable')
            return web.json_response({'success': False, 'error': 'Не удалось проверить модели OpenAI.'}, status=503, headers=headers)
    return handle
