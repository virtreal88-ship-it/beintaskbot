"""Legacy authorised search: only GETs and no global contact-search fallback."""
import asyncio
from aiohttp import web
from kommo_phone_duplicates import search
from tenant_policy import TenantPolicy


def handler(identify, authorize, can_view, http, base_url, headers, logger):
    async def handle(request):
        output_headers = {'Cache-Control': 'no-store'}
        try:
            actor = identify(request)
            if not actor:
                return web.json_response({'success': False, 'error': 'İcazə yoxdur.'}, status=401, headers=output_headers)
            lead_id = int(request.query.get('lead_id') or 0)
            if lead_id <= 0:
                raise ValueError('Sövdələşmə seçilməyib.')
            lead, denied = await asyncio.to_thread(authorize, actor, lead_id)
            if denied is not None:
                return denied
            async def read(path, params):
                def get():
                    response = http.get(base_url.rstrip('/') + '/api/v4/' + path, headers=headers,
                                        params=params, timeout=(3, 8), allow_redirects=False)
                    if response.status_code == 204:
                        return {'_embedded': {}}
                    if response.status_code != 200:
                        raise RuntimeError('Provider unavailable')
                    return response.json()
                return await asyncio.to_thread(get)
            result = await search(request.query.get('phone', ''), lead_id, read, lambda row: can_view(actor, row))
            # Re-check the source after the external reads (no stale-authority response).
            _, denied = await asyncio.to_thread(authorize, actor, lead_id)
            if denied is not None:
                return denied
            result['deals'] = [row for row in result['deals'] if can_view(actor, row)]
            return web.json_response({'success': True, **result}, headers=output_headers)
        except (ValueError, TypeError):
            return web.json_response({'success': False, 'error': 'Telefon nömrəsini tam yazın.'}, status=400, headers=output_headers)
        except Exception:
            logger.warning('Duplicate phone search unavailable')
            return web.json_response({'success': False, 'error': 'Axtarış tamamlanmadı. Yenidən yoxlayın.'}, status=503, headers=output_headers)
    return handle


def tenant_handler(member_from_request, request_provider, logger):
    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            profile = member_from_request(request)
            if not profile:
                return web.json_response({'success': False, 'error': 'İcazə yoxdur.'}, status=403, headers=headers)
            identity = (str(profile['tenant_id']), str(profile['telegram_id']))
            if (request.query.get('expected_tenant_id'), request.query.get('expected_user_id')) != identity:
                return web.json_response({'success': False, 'error': 'Kabinet dəyişib. Yeniləyin.'}, status=409, headers=headers)
            lead_id = int(request.query.get('lead_id') or 0)
            if lead_id <= 0:
                raise ValueError()
            async def read(path, params):
                return await request_provider(identity[0], 'GET', path, params=params)
            lead = await read(f'leads/{lead_id}', {})
            if not TenantPolicy(profile).can_access_deal({**lead, 'tenant_id': identity[0]}):
                return web.json_response({'success': False, 'error': 'İcazə yoxdur.'}, status=403, headers=headers)
            result = await search(request.query.get('phone', ''), lead_id, read,
                                  lambda row: TenantPolicy(profile).can_access_deal({**row, 'tenant_id': identity[0]}))
            current = member_from_request(request)
            if not current or (str(current['tenant_id']), str(current['telegram_id'])) != identity:
                return web.json_response({'success': False, 'error': 'Kabinet dəyişib. Yeniləyin.'}, status=409, headers=headers)
            if not TenantPolicy(current).can_access_deal({**lead, 'tenant_id': identity[0]}):
                return web.json_response({'success': False, 'error': 'İcazə yoxdur.'}, status=403, headers=headers)
            result['deals'] = [row for row in result['deals'] if TenantPolicy(current).can_access_deal({**row, 'tenant_id': identity[0]})]
            return web.json_response({'success': True, **result}, headers=headers)
        except (ValueError, TypeError):
            return web.json_response({'success': False, 'error': 'Telefon nömrəsini tam yazın.'}, status=400, headers=headers)
        except Exception:
            logger.warning('Tenant duplicate phone search unavailable')
            return web.json_response({'success': False, 'error': 'Axtarış tamamlanmadı. Yenidən yoxlayın.'}, status=503, headers=headers)
    return handle
