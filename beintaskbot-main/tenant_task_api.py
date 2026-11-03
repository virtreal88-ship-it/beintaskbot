"""HTTP boundary for tenant-scoped CRM tasks and approval commands."""
import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from aiohttp import web
from tenant_task_flow import (TaskFlowError, query as flow_query, read as read_flow,
    current_profile, external_options, execution_profiles, authority_key)
from tenant_task_commands import TaskCommandUnavailable


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
    def intent(request, profile):
        params = request.rel_url.query
        return flow_query(request, profile or {}, required=params.get('flow') == '1')

    def flow_error(exc):
        return web.json_response({'success': False, 'error': exc.code}, status=exc.status,
                                 headers={'Cache-Control': 'no-store'})

    async def fresh_external(request, profile):
        params = intent(request, profile)
        if params.get('flow') == '1' or 'expected_tenant_id' in params:
            profile = await asyncio.to_thread(current_profile, profile or {})
        return profile, params

    async def guarded_transport(profile, executor_id=None):
        actor, executor = await asyncio.to_thread(execution_profiles, profile, executor_id)
        keys = authority_key(actor), authority_key(executor)
        async def dispatch(*args, **kwargs):
            fresh_actor, fresh_executor = await asyncio.to_thread(execution_profiles, actor, executor['telegram_id'])
            if keys != (authority_key(fresh_actor), authority_key(fresh_executor)):
                raise TaskFlowError('task_forbidden', 403)
            return await context.crm_request(*args, **kwargs)
        return actor, dispatch

    async def tasks(request: web.Request) -> web.Response:
        profile = context.member(request)
        if not profile:
            return flow_error(TaskFlowError('task_forbidden', 403))
        try:
            params = intent(request, profile)
            if params.get('flow') == '1':
                if not profile:
                    raise TaskFlowError('task_forbidden', 403)
                if 'refresh' in request.rel_url.query or 'full' in request.rel_url.query:
                    raise TaskFlowError('task_invalid_query')
                result = await asyncio.to_thread(read_flow, profile, source=params.get('source'),
                    task_id=params.get('task_id'), request_id=params.get('request_id'))
                return web.json_response({'success': True, **result}, headers={'Cache-Control': 'no-store'})
            if 'expected_tenant_id' in params:
                profile = await asyncio.to_thread(current_profile, profile or {})
        except TaskFlowError as exc:
            return flow_error(exc)
        except Exception:
            context.logger.exception('Task flow read unavailable')
            return flow_error(TaskFlowError('task_unavailable', 503))
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
        if not profile:
            return flow_error(TaskFlowError('task_forbidden', 403))
        try:
            profile, params = await fresh_external(request, profile)
            if params.get('flow') == '1':
                if params.get('source') not in (None, 'kommo') or params.get('task_id') or params.get('request_id'):
                    raise TaskFlowError('task_invalid_query')
                result = await asyncio.to_thread(external_options, profile)
                return web.json_response({'success': True, **result}, headers={'Cache-Control': 'no-store'})
        except TaskFlowError as exc:
            return flow_error(exc)
        except Exception:
            context.logger.exception('Task flow options unavailable')
            return flow_error(TaskFlowError('task_unavailable', 503))
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
        if not profile:
            return flow_error(TaskFlowError('task_forbidden', 403))
        try:
            profile, params = await fresh_external(request, profile)
        except TaskFlowError as exc:
            return flow_error(exc)
        except Exception:
            context.logger.exception('Task flow authority unavailable')
            return flow_error(TaskFlowError('task_unavailable', 503))
        if not _can_use_tasks(context, profile):
            return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
        if not _valid_write(request, context.origin):
            return web.json_response({"success": False, "error": "Sorğunun mənbəyi düzgün deyil."}, status=403)
        try:
            data = await _object_body(request)
            transport = context.crm_request
            if params.get('flow') == '1':
                if params.get('source') not in (None, 'kommo') or params.get('task_id') or params.get('request_id'):
                    raise TaskFlowError('task_invalid_query')
                profile, transport = await guarded_transport(profile, data.get('executor_id'))
            result = await context.create_task(profile, data, transport)
            if params.get('flow') == '1':
                # The service commits provider success before optional cache/audit.
                # A failed readback must never turn that receipt into a failed write.
                command = dict(source='kommo', request_id=str(data.get('request_id') or ''),
                    state='approval_pending' if result.get('approval_pending') is True else
                          'created' if result.get('task_id') else 'unknown',
                    task_id=str(result['task_id']) if result.get('task_id') else None,
                    task=None, cache_state='unavailable' if result.get('task_id') else 'not_applicable')
                try:
                    saved = await asyncio.to_thread(read_flow, profile, source='kommo', request_id=data.get('request_id'))
                    if saved['command']['state'] != 'not_confirmed':
                        command = saved['command']
                except Exception:
                    context.logger.warning('Task receipt readback unavailable: tenant=%s', profile['tenant_id'])
                result = {**result, 'command': command}
            return web.json_response({"success": True, **result})
        except TaskFlowError as exc:
            return flow_error(exc)
        except TaskCommandUnavailable:
            return flow_error(TaskFlowError('task_unavailable', 503))
        except context.pending_error as exc:
            return web.json_response({"success": False, "error": str(exc), "retry_same_request": True}, status=409)
        except (ValueError, context.platform_error) as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        except Exception:
            context.logger.exception("Tenant task create failed: tenant=%s", profile["tenant_id"])
            return web.json_response({"success": False, "error": "Nəticə təsdiqlənmədi. Eyni sorğu ilə yenidən yoxlayın; yeni tapşırıq yaratmayın."}, status=500)

    async def complete(request: web.Request) -> web.Response:
        profile = context.member(request)
        if not profile:
            return flow_error(TaskFlowError('task_forbidden', 403))
        try:
            profile, params = await fresh_external(request, profile)
        except TaskFlowError as exc:
            return flow_error(exc)
        except Exception:
            context.logger.exception('Task flow authority unavailable')
            return flow_error(TaskFlowError('task_unavailable', 503))
        if not _can_use_tasks(context, profile):
            return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
        if not _valid_write(request, context.origin):
            return web.json_response({"success": False, "error": "Sorğunun mənbəyi düzgün deyil."}, status=403)
        try:
            transport = context.crm_request
            if params.get('flow') == '1':
                if params.get('source') not in (None, 'kommo') or params.get('task_id') or params.get('request_id'):
                    raise TaskFlowError('task_invalid_query')
                profile, transport = await guarded_transport(profile)
            result = await context.complete_task(profile, await _object_body(request), transport)
            return web.json_response({"success": True, **result})
        except TaskFlowError as exc:
            return flow_error(exc)
        except TaskCommandUnavailable:
            return flow_error(TaskFlowError('task_unavailable', 503))
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
        except TaskCommandUnavailable:
            return flow_error(TaskFlowError('task_unavailable', 503))
        except context.pending_error as exc:
            return web.json_response({"success": False, "error": str(exc), "retry_same_request": True}, status=409)
        except (ValueError, context.platform_error) as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        except Exception:
            context.logger.exception("Tenant task approval failed: tenant=%s", profile["tenant_id"])
            return web.json_response({"success": False, "error": "Nəticə yoxlanmalıdır. Yeni sorğu yaratmayın."}, status=500)

    return {"tasks": tasks, "options": options, "create": create, "complete": complete, "approvals": approvals}
