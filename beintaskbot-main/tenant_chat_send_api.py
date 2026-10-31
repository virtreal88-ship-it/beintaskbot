"""Same-origin, company/user-bound chat send endpoint."""
from aiohttp import web
from tenant_chat_send_service import run
from tenant_linear_policy import TenantLinearError
from tenant_request_origin import require_json_post


def handler(member_from_request, canonical_origin, request_provider, wait_slot, logger):
    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            session = member_from_request(request)
            if not session:
                raise TenantLinearError('İcazə yoxdur.', 403)
            require_json_post(request, canonical_origin)
            data = await request.json()
            if not isinstance(data, dict):
                raise TenantLinearError('Sorğu düzgün deyil.')
            if str(data.get('expected_tenant_id') or '') != str(session['tenant_id']) or str(data.get('expected_user_id') or '') != str(session['telegram_id']):
                raise TenantLinearError('Kabinet dəyişib. Yeniləyin.', 409)
            result = await run(session, data, request_provider, wait_slot, logger)
            return web.json_response({'success': True, **result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success': False, 'error': str(error), 'keep_request': getattr(error, 'keep_request', False)}, status=error.status, headers=headers)
        except (ValueError, TypeError, KeyError):
            return web.json_response({'success': False, 'error': 'Sorğu düzgün deyil.'}, status=400, headers=headers)
        except Exception:
            logger.error('Tenant chat send service unavailable')
            return web.json_response({'success': False, 'error': 'Göndərmə vəziyyəti yoxlanmadı. Sorğunu dəyişmədən yenidən yoxlayın.', 'keep_request': True}, status=503, headers=headers)
    return handle


def receipt_handler(member_from_request,logger):
    """GET status never dispatches, records customer history or marks handling."""
    import asyncio
    from tenant_read_session import require_read_session
    from tenant_chat_send_store import read_quoted_receipt,accept_quoted_lookup
    from tenant_connect_transport import quoted_delivery_status
    async def handle(request):
        headers={'Cache-Control':'no-store'}
        try:
            session=require_read_session(request,member_from_request)
            lead_id=request.query.get('lead_id');request_id=request.query.get('request_id')
            item=await asyncio.to_thread(read_quoted_receipt,session,lead_id,request_id)
            status=await asyncio.to_thread(quoted_delivery_status,item['credentials'],item['message_id'],item['key'][2],item['quote'])
            fresh=await asyncio.to_thread(read_quoted_receipt,session,lead_id,item['key'][2])
            if fresh['connection']!=item['connection'] or fresh['quote']!=item['quote']:
                raise TenantLinearError('İstinad edilən dialoq dəyişib.',409)
            # Preserve existing timeout recovery: reconcile only an already-verified
            # own receipt cache, never invoke send/history/handling from this GET.
            state=fresh['receipt_state']
            if request_id and state in {'sending','unknown'} and fresh['quote']:
                await asyncio.to_thread(accept_quoted_lookup,fresh,status['message_id']);state='accepted'
            return web.json_response({'success':True,'state':state,'receipt_state':state,'request_id':item['key'][2],**status},headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error),'keep_request':True},status=error.status,headers=headers)
        except (ValueError,TypeError,KeyError):
            return web.json_response({'success':False,'error':'Sorğu düzgün deyil.','keep_request':True},status=400,headers=headers)
        except Exception:
            logger.warning('Quoted receipt read unavailable; no dispatch')
            return web.json_response({'success':False,'error':'İstinad vəziyyəti əlçatan deyil. Yenidən yoxlama yeni mesaj göndərmir.','keep_request':True},status=503,headers=headers)
    return handle
