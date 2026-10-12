"""Owner-only HTTP API for connecting BeinSystems ERP."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from aiohttp import web

from tenant_bein_integration import credential, save, set_primary, status
from tenant_crm_provider import CrmProviderError
from tenant_platform_core import TenantPlatformError


def _valid_write(request: web.Request, origin: str) -> bool:
    request_origin = request.headers.get("Origin")
    return request.content_type == "application/json" and (
        not request_origin or request_origin.rstrip("/") == origin.rstrip("/"))


def handler(member: Callable, origin: str, logger) -> Callable:
    async def endpoint(request: web.Request) -> web.Response:
        profile = member(request, owner_only=True)
        if not profile:
            return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
        if request.method == "GET":
            try:
                return web.json_response({"success": True, "integration": await asyncio.to_thread(status, profile)})
            except TenantPlatformError as exc:
                return web.json_response({"success": False, "error": str(exc)}, status=400)
        if not _valid_write(request, origin):
            return web.json_response({"success": False, "error": "Sorğunun mənbəyi düzgün deyil."}, status=403)
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError
        except Exception:
            return web.json_response({"success": False, "error": "Sorğu düzgün deyil."}, status=400)
        action = str(data.get("action") or "").casefold()
        try:
            if action == "connect":
                # Never save an unverified credential. A lightweight catalog
                # read also proves token, host and branch visibility.
                from tenant_bein_crm import BeinCrm
                client = BeinCrm(str(data.get("base_url") or ""), str(data.get("token") or ""),
                                 str(data.get("branch_id") or ""))
                catalog = await client.list_resource("pipelines", page=1)
                item = await asyncio.to_thread(save, profile, base_url=client.base_url,
                    token=client.integration_token, branch_id=client.branch_id,
                    primary=bool(data.get("primary")))
                return web.json_response({"success": True, "integration": item,
                                          "message": "BeinSystems ERP bağlantısı yoxlanıldı və saxlanıldı.",
                                          "catalog_available": bool(catalog)})
            if action == "test":
                client = await asyncio.to_thread(credential, profile["tenant_id"])
                payload = await client.list_resource("pipelines", page=1)
                return web.json_response({"success": True, "message": "BeinSystems ERP bağlantısı işləyir.",
                                          "catalog_available": bool(payload)})
            if action == "primary":
                selected = await asyncio.to_thread(set_primary, profile, str(data.get("provider") or ""))
                return web.json_response({"success": True, **selected})
            return web.json_response({"success": False, "error": "Əməliyyat düzgün deyil."}, status=400)
        except (TenantPlatformError, CrmProviderError) as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=getattr(exc, "status", 400))
        except Exception:
            logger.exception("BeinSystems ERP integration command failed: tenant=%s action=%s",
                             profile.get("tenant_id"), action)
            return web.json_response({"success": False, "error": "BeinSystems ERP bağlantısı tamamlanmadı."}, status=500)

    return endpoint
