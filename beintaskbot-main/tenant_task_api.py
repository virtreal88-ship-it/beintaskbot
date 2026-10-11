"""HTTP boundary for tenant-scoped CRM tasks and approval commands."""
import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from aiohttp import web


@dataclass(frozen=True)
class TaskApiContext:
    member: Callable[..., dict | None]
    policy: Callable[[dict], object]
    sync_crm: Callable[..., object]
    list_tasks: Callable[..., list]
    executor_profiles: Callable[[dict], list]
    list_deals: Callable[..., tuple[list, int]]
    create_task: Callable[..., object]
    complete_task: Callable[..., object]
    approval_page: Callable[..., dict]
    decide_approval: Callable[..., object]
    crm_request: Callable[..., object]
    origin: str
    platform_error: type[Exception]
    pending_error: type[Exception]
    logger: object


def _can_use_tasks(context: TaskApiContext, profile: dict | None) -> bool:
    return bool(profile and context.policy(profile).allows("tasks"))


def _valid_write(request: web.Request, origin: str) -> bool:
    request_origin = request.headers.get("Origin")
    return request.content_type == "application/json" and (
        not request_origin or request_origin.rstrip("/") == origin.rstrip("/"))


async def _object_body(request: web.Request) -> dict:
    data = await request.json()
    if not isinstance(data, dict):
        raise ValueError("Sorğu düzgün deyil.")
    return data


def handlers(context: TaskApiContext) -> dict[str, Callable]:
    async def tasks(request: web.Request) -> web.Response:
        profile = context.member(request)
        if not _can_use_tasks(context, profile):
            return web.json_response({"success": False, "error": "Tapşırıqlar üçün icazəniz yoxdur."}, status=403)
        should_refresh = str(request.rel_url.query.get("refresh") or "").lower() in {"1", "true", "yes"}
        sync_error = ""
        if should_refresh:
            try:
                await context.sync_crm(
                    profile, include_tasks=True,
                    force_full=str(request.rel_url.query.get("full") or "") == "1",
                )
            except context.platform_error as exc:
                sync_error = str(exc)
            except Exception:
                context.logger.exception("Tenant CRM task sync failed: tenant=%s", profile["tenant_id"])
                sync_error = "Kommo tapşırıqları yenilənmədi. Son saxlanmış siyahı göstərilir."
        rows = await asyncio.to_thread(
            context.list_tasks, tenant_id=profile["tenant_id"], limit=200,
            scope=context.policy(profile).task_scope(),
        )
        return web.json_response({"success": True, "tasks": rows, "stale": bool(sync_error), "warning": sync_error})

    async def options(request: web.Request) -> web.Response:
        profile = context.member(request)
        if not _can_use_tasks(context, profile):
            return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
        try:
            profiles = await asyncio.to_thread(context.executor_profiles, profile)
            executors = []
            for person in profiles:
                if not context.policy(person).allows("tasks"):
                    continue
                personal = context.policy({**person, "role": "manager"}).pipeline_scope()
                names = {str(row["pipeline_id"]): row.get("name") or ""
                         for row in (person.get("workflow") or {}).get("pipelines", [])}
                personal = [{**row, "name": names.get(str(row["pipeline_id"]), "")} for row in personal]
                executors.append({
                    "id": person["telegram_id"], "name": person.get("display_name") or "Əməkdaş",
                    "role": person["role"],
                    "pipelines": personal if person["role"] in {"manager", "owner", "admin"} else [],
                })
            rows, _ = await asyncio.to_thread(
                context.list_deals, tenant_id=profile["tenant_id"],
                scope=context.policy(profile).pipeline_scope(), limit=200,
            )
            deals = [{"id": row["kommo_lead_id"], "name": row["name"], "pipeline_id": row["pipeline_id"]}
                     for row in rows]
            return web.json_response({"success": True, "executors": executors, "deals": deals})
        except Exception:
            context.logger.exception("Tenant task options failed: tenant=%s", profile["tenant_id"])
            return web.json_response({"success": False, "error": "Tapşırıq ayarları yüklənmədi."}, status=500)

    async def create(request: web.Request) -> web.Response:
        profile = context.member(request)
        if not _can_use_tasks(context, profile):
            return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
        if not _valid_write(request, context.origin):
            return web.json_response({"success": False, "error": "Sorğunun mənbəyi düzgün deyil."}, status=403)
        try:
            result = await context.create_task(profile, await _object_body(request), context.crm_request)
            return web.json_response({"success": True, **result})
        except context.pending_error as exc:
            return web.json_response({"success": False, "error": str(exc), "retry_same_request": True}, status=409)
        except (ValueError, context.platform_error) as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        except Exception:
            context.logger.exception("Tenant task create failed: tenant=%s", profile["tenant_id"])
            return web.json_response({"success": False, "error": "Nəticə təsdiqlənmədi. Eyni sorğu ilə yenidən yoxlayın; yeni tapşırıq yaratmayın."}, status=500)

    async def complete(request: web.Request) -> web.Response:
        profile = context.member(request)
        if not _can_use_tasks(context, profile):
            return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
        if not _valid_write(request, context.origin):
            return web.json_response({"success": False, "error": "Sorğunun mənbəyi düzgün deyil."}, status=403)
        try:
            result = await context.complete_task(profile, await _object_body(request), context.crm_request)
            return web.json_response({"success": True, **result})
        except context.pending_error as exc:
            return web.json_response({"success": False, "error": str(exc), "retry_same_request": True}, status=409)
        except (ValueError, context.platform_error) as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        except Exception:
            context.logger.exception("Tenant task completion failed: tenant=%s", profile["tenant_id"])
            return web.json_response({"success": False, "error": "Nəticə yoxlanmalıdır. Eyni sorğu ilə yoxlayın; yeni sorğu yaratmayın."}, status=500)

    async def approvals(request: web.Request) -> web.Response:
        profile = context.member(request)
        policy = context.policy(profile) if profile else None
        if not policy or not policy.privileged or not policy.allows("tasks"):
            return web.json_response({"success": False, "error": "Təsdiq üçün icazəniz yoxdur."}, status=403)
        try:
            if request.method == "GET":
                page = await asyncio.to_thread(
                    context.approval_page, profile, limit=request.rel_url.query.get("limit", 50),
                    cursor=request.rel_url.query.get("cursor", ""),
                )
                return web.json_response({"success": True, **page}, headers={"Cache-Control": "no-store"})
            if not _valid_write(request, context.origin):
                return web.json_response({"success": False, "error": "Sorğunun mənbəyi düzgün deyil."}, status=403)
            result = await context.decide_approval(profile, await _object_body(request), context.crm_request)
            return web.json_response({"success": True, **result})
        except context.pending_error as exc:
            return web.json_response({"success": False, "error": str(exc), "retry_same_request": True}, status=409)
        except (ValueError, context.platform_error) as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        except Exception:
            context.logger.exception("Tenant task approval failed: tenant=%s", profile["tenant_id"])
            return web.json_response({"success": False, "error": "Nəticə yoxlanmalıdır. Yeni sorğu yaratmayın."}, status=500)

    return {"tasks": tasks, "options": options, "create": create, "complete": complete, "approvals": approvals}
