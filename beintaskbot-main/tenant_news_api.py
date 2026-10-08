"""Same-origin review API and separate deliberately public approved-news API."""
import asyncio
from aiohttp import web
from tenant_news import listing, command, published
from tenant_linear_policy import TenantLinearError
from tenant_news_ai_settings import read as read_ai, save as save_ai


def news_handler(member_from_request, canonical_origin, logger, *, public=False, ai=False):
    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            if public:
                result = await asyncio.to_thread(published, request.match_info['company'], after=request.rel_url.query.get('after'))
            else:
                profile = member_from_request(request)
                if not profile:
                    raise TenantLinearError('İcazə yoxdur.', 403)
                if request.method == 'GET':
                    result = await asyncio.to_thread(read_ai, profile) if ai else await asyncio.to_thread(listing, profile, status=request.rel_url.query.get('status', 'pending'), after=request.rel_url.query.get('after'))
                else:
                    if request.content_type != 'application/json' or request.headers.get('Origin', '').rstrip('/') != canonical_origin.rstrip('/'):
                        raise TenantLinearError('Sorğunun mənbəyi düzgün deyil.', 403)
                    data = await request.json()
                    if not isinstance(data, dict):
                        raise TenantLinearError('Sorğu düzgün deyil.')
                    if str(data.get('expected_tenant_id') or '') != str(profile['tenant_id']) or str(data.get('expected_user_id') or '') != str(profile['telegram_id']):
                        raise TenantLinearError('Kabinet dəyişib. Yeniləyin.', 409)
                    result = await asyncio.to_thread(save_ai if ai else command, profile, data)
            return web.json_response({'success': True, **result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success': False, 'error': str(error)}, status=error.status, headers=headers)
        except (ValueError, TypeError, KeyError):
            return web.json_response({'success': False, 'error': 'Sorğu düzgün deyil.'}, status=400, headers=headers)
        except Exception:
            logger.error('Tenant news API unavailable')
            return web.json_response({'success': False, 'error': 'Xəbər xidməti əlçatan deyil.'}, status=503, headers=headers)
    return handle
