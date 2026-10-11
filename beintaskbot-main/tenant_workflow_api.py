"""Owner-only HTTP boundary for tenant workflow configuration."""
import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from aiohttp import web


@dataclass(frozen=True)
class WorkflowApiContext:
    member: Callable[..., dict | None]
    load_workflow: Callable[..., dict]
    save_workflow: Callable[..., dict]
    crm_request: Callable[..., object]
    role_permissions: dict
    platform_error: type[Exception]
    logger: object


def _owner(context: WorkflowApiContext, request: web.Request) -> dict | None:
    return context.member(request, owner_only=True)


async def _object_body(request: web.Request) -> dict:
    data = await request.json()
    if not isinstance(data, dict):
        raise ValueError("Workflow məlumatı düzgün deyil.")
    return data


def handlers(context: WorkflowApiContext) -> dict[str, Callable]:
    async def config(request: web.Request) -> web.Response:
        profile = _owner(context, request)
        if not profile:
            return web.json_response({"success": False, "error": "Yalnız şirkət sahibi üçün."}, status=403)
        tenant_id = str(profile["tenant_id"])
        owner_id = int(profile["telegram_id"])
        try:
            if request.method == "GET":
                workflow = await asyncio.to_thread(
                    context.load_workflow, tenant_id=tenant_id, owner_id=owner_id)
            else:
                data = await _object_body(request)
                workflow = await asyncio.to_thread(
                    context.save_workflow,
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                    pipelines=data.get("pipelines"),
                    stages=data.get("stages"),
                    policies=data.get("policies"),
                    members=data.get("members"),
                )
            return web.json_response({"success": True, **workflow,
                                      "role_permissions": context.role_permissions})
        except context.platform_error as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        except (ValueError, TypeError):
            return web.json_response({"success": False, "error": "Workflow məlumatı düzgün deyil."}, status=400)
        except Exception:
            context.logger.exception("Tenant workflow configuration failed: tenant=%s", tenant_id)
            return web.json_response({"success": False, "error": "Workflow ayarları saxlanmadı."}, status=500)

    async def catalog(request: web.Request) -> web.Response:
        profile = _owner(context, request)
        if not profile:
            return web.json_response({"success": False, "error": "Yalnız şirkət sahibi üçün."}, status=403)
        tenant_id = str(profile["tenant_id"])
        try:
            result = await context.crm_request(tenant_id, "GET", "leads/pipelines", params=None)
        except Exception:
            context.logger.warning("Tenant workflow catalog failed: tenant=%s kind=pipelines", tenant_id)
            return web.json_response({"success": True, "pipelines": [], "users": [],
                                      "warnings": ["Kommo vərəqləri yüklənmədi."]})
        pipelines = []
        for item in (result.get("_embedded") or {}).get("pipelines", []):
            pipelines.append({
                "pipeline_id": str(item["id"]),
                "name": item.get("name") or "",
                "stages": [
                    {"stage_id": str(stage["id"]), "name": stage.get("name") or "",
                     "sort_order": int(stage.get("sort") or 0)}
                    for stage in (item.get("_embedded") or {}).get("statuses", [])
                ],
            })
        # Employees are application memberships, not paid Kommo users.
        return web.json_response({"success": True, "pipelines": pipelines,
                                  "users": [], "warnings": []})

    return {"config": config, "catalog": catalog}
