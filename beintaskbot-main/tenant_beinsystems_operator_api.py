"""Same-origin HTTP handlers for the private Operator integration."""
from __future__ import annotations

import json
import asyncio
from collections.abc import Callable

from aiohttp import web

import tenant_beinsystems_operator as operator
from tenant_beinsystems_operator_access import is_operator_workspace
from tenant_linear_policy import TenantLinearError


def _profile(member, request):
    profile = member(request)
    if not profile or not is_operator_workspace(profile.get("tenant_id")):
        return None
    return profile


def _context(data, profile):
    expected_tenant = str(data.get("expected_tenant_id") or "")
    expected_user = str(data.get("expected_user_id") or "")
    if (expected_tenant != str(profile.get("tenant_id") or "")
            or expected_user != str(profile.get("telegram_id") or "")):
        raise operator.OperatorError("Контекст сотрудника изменился. Обновите страницу.", 409)


async def _body(request, profile, origin):
    from tenant_request_origin import require_json_post

    try:
        require_json_post(request, origin)
    except TenantLinearError as error:
        raise operator.OperatorError("Источник запроса не подтверждён.", 403) from error
    if request.content_type != "application/json":
        raise operator.OperatorError("Ожидается JSON.", 415)
    if request.content_length is not None and request.content_length > 20000:
        raise operator.OperatorError("Слишком большой запрос.", 413)
    raw = await request.read()
    if len(raw) > 20000:
        raise operator.OperatorError("Слишком большой запрос.", 413)
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise operator.OperatorError("Некорректный запрос.") from None
    if not isinstance(data, dict):
        raise operator.OperatorError("Некорректный запрос.")
    _context(data, profile)
    return {key: value for key, value in data.items()
            if key not in {"expected_tenant_id", "expected_user_id"}}


def _has_account_link(profile, entity_type, entity_id, account):
    if entity_type == "contact":
        from tenant_crm_native_contacts import get_contact
        contact = get_contact(str(profile.get("tenant_id")), entity_id,
                              actor_id=int(profile.get("telegram_id") or 0))
        company_id = str((contact or {}).get("company_id") or "")
        if not company_id:
            return False
        entity_type, entity_id = "company", company_id
    links = operator.links_for_local(
        profile, local_entity_type=entity_type, local_entity_id=entity_id)
    return any(row["status"] == "confirmed" and row["external_type"] == "account"
               and row["external_id"] == account for row in links)


def _json_response(data, *, status=200):
    return web.json_response({"success": True, **data}, status=status,
                             headers={"Cache-Control": "no-store", "Vary": "Cookie"})


def integration_handler(member: Callable, origin: str, logger):
    async def endpoint(request: web.Request):
        profile = _profile(member, request)
        if not profile:
            return web.json_response({"success": False, "error": "Интеграция недоступна."},
                                     status=404, headers={"Cache-Control": "no-store"})
        try:
            if request.method == "GET":
                return _json_response({"integration": await asyncio.to_thread(operator.status, profile)})
            data = await _body(request, profile, origin)
            action = str(data.get("action") or "").strip().lower()
            if action == "login" and set(data) == {"action", "login", "password"}:
                value = await asyncio.to_thread(
                    operator.connect_with_password, profile, data["login"], data["password"])
            elif action == "connect" and set(data) == {"action", "token"}:
                value = await asyncio.to_thread(operator.save_token, profile, data["token"])
            elif action == "test" and set(data) == {"action"}:
                value = await asyncio.to_thread(operator.verify_connection, profile)
            elif action == "disconnect" and set(data) == {"action"}:
                value = await asyncio.to_thread(operator.disconnect, profile)
            else:
                raise operator.OperatorError("Операция Operator некорректна.")
            return _json_response({"integration": value})
        except operator.OperatorError as error:
            return web.json_response({"success": False, "error": str(error)},
                                     status=error.status,
                                     headers={"Cache-Control": "no-store"})
        except Exception:
            logger.exception("Operator integration settings failed")
            return web.json_response({"success": False, "error": "Не удалось обновить подключение Operator."},
                                     status=503, headers={"Cache-Control": "no-store"})
    return endpoint


def accounts_handler(member: Callable, origin: str, logger):
    async def endpoint(request: web.Request):
        profile = _profile(member, request)
        if not profile:
            return web.json_response({"success": False, "error": "Интеграция недоступна."},
                                     status=404, headers={"Cache-Control": "no-store"})
        try:
            data = await _body(request, profile, origin)
            if set(data) != {"search"}:
                raise operator.OperatorError("Укажите строку поиска.")
            response = await asyncio.to_thread(operator.read, profile, "accounts", data)
            accounts = operator.accounts_list_payload(response)
            return _json_response({"accounts": accounts})
        except operator.OperatorError as error:
            return web.json_response({"success": False, "error": str(error)},
                                     status=error.status,
                                     headers={"Cache-Control": "no-store"})
        except Exception:
            logger.exception("Operator account search failed")
            return web.json_response({"success": False, "error": "Поиск аккаунтов Operator временно недоступен."},
                                     status=503, headers={"Cache-Control": "no-store"})
    return endpoint


def links_handler(member: Callable, origin: str, logger):
    async def endpoint(request: web.Request):
        profile = _profile(member, request)
        if not profile:
            return web.json_response({"success": False, "error": "Интеграция недоступна."},
                                     status=404, headers={"Cache-Control": "no-store"})
        try:
            if request.method == "GET":
                links = await asyncio.to_thread(operator.links_for_local, profile,
                    local_entity_type=request.query.get("entity_type", ""),
                    local_entity_id=request.query.get("entity_id", ""))
                return _json_response({"links": links})
            data = await _body(request, profile, origin)
            if data.get("entity_type") == "contact":
                if set(data) != {"entity_type", "entity_id", "account", "login", "evidence"}:
                    raise operator.OperatorError("Для профиля Operator нужны account, login и основание проверки.")
                link = await asyncio.to_thread(operator.confirm_profile_user_link, profile,
                    local_contact_id=data["entity_id"], account=data["account"],
                    login=data["login"], evidence_summary=data["evidence"])
            else:
                if set(data) != {"entity_type", "entity_id", "account", "evidence"}:
                    raise operator.OperatorError("Поля связи Operator некорректны.")
                link = await asyncio.to_thread(operator.confirm_account_link, profile,
                    local_entity_type=data["entity_type"], local_entity_id=data["entity_id"],
                    external_account=data["account"], evidence_summary=data["evidence"])
            return _json_response({"link": link}, status=201)
        except operator.OperatorError as error:
            return web.json_response({"success": False, "error": str(error)},
                                     status=error.status,
                                     headers={"Cache-Control": "no-store"})
        except Exception:
            logger.exception("Operator identity link operation failed")
            return web.json_response({"success": False, "error": "Не удалось обработать связь Operator."},
                                     status=503, headers={"Cache-Control": "no-store"})
    return endpoint


def revoke_handler(member: Callable, origin: str, logger):
    async def endpoint(request: web.Request):
        profile = _profile(member, request)
        if not profile:
            return web.json_response({"success": False, "error": "Интеграция недоступна."},
                                     status=404, headers={"Cache-Control": "no-store"})
        try:
            data = await _body(request, profile, origin)
            if data:
                raise operator.OperatorError("Операция некорректна.")
            result = await asyncio.to_thread(operator.revoke_link, profile, request.match_info["link_id"])
            return _json_response({"link": result})
        except operator.OperatorError as error:
            return web.json_response({"success": False, "error": str(error)},
                                     status=error.status,
                                     headers={"Cache-Control": "no-store"})
        except Exception:
            logger.exception("Operator identity link revoke failed")
            return web.json_response({"success": False, "error": "Не удалось отозвать связь Operator."},
                                     status=503, headers={"Cache-Control": "no-store"})
    return endpoint


def employee_users_handler(member: Callable, origin: str, logger):
    async def endpoint(request: web.Request):
        profile = _profile(member, request)
        if not profile:
            return web.json_response({"success": False, "error": "Интеграция недоступна."},
                                     status=404, headers={"Cache-Control": "no-store"})
        try:
            data = await _body(request, profile, origin)
            if set(data) != {"account"}:
                raise operator.OperatorError("Укажите Operator account.")
            users = await asyncio.to_thread(operator.operator_employee_users, profile, data["account"])
            return _json_response({"users": users})
        except operator.OperatorError as error:
            return web.json_response({"success": False, "error": str(error)}, status=error.status,
                                     headers={"Cache-Control": "no-store"})
        except Exception:
            logger.exception("Operator employee user list failed")
            return web.json_response({"success": False, "error": "Список пользователей Operator временно недоступен."},
                                     status=503, headers={"Cache-Control": "no-store"})
    return endpoint


def employee_links_handler(member: Callable, origin: str, logger):
    async def endpoint(request: web.Request):
        profile = _profile(member, request)
        if not profile:
            return web.json_response({"success": False, "error": "Интеграция недоступна."},
                                     status=404, headers={"Cache-Control": "no-store"})
        try:
            if request.method == "GET":
                links = await asyncio.to_thread(operator.employee_links, profile)
                return _json_response({"links": links})
            data = await _body(request, profile, origin)
            if set(data) != {"member_telegram_id", "account", "login", "evidence"}:
                raise operator.OperatorError("Поля связи сотрудника с Operator некорректны.")
            link = await asyncio.to_thread(operator.confirm_employee_link, profile,
                member_telegram_id=data["member_telegram_id"], account=data["account"],
                login=data["login"], evidence_summary=data["evidence"])
            return _json_response({"link": link}, status=201)
        except operator.OperatorError as error:
            return web.json_response({"success": False, "error": str(error)}, status=error.status,
                                     headers={"Cache-Control": "no-store"})
        except Exception:
            logger.exception("Operator employee identity link operation failed")
            return web.json_response({"success": False, "error": "Не удалось обработать связь сотрудника с Operator."},
                                     status=503, headers={"Cache-Control": "no-store"})
    return endpoint


def employee_link_revoke_handler(member: Callable, origin: str, logger):
    async def endpoint(request: web.Request):
        profile = _profile(member, request)
        if not profile:
            return web.json_response({"success": False, "error": "Интеграция недоступна."},
                                     status=404, headers={"Cache-Control": "no-store"})
        try:
            data = await _body(request, profile, origin)
            if data:
                raise operator.OperatorError("Операция некорректна.")
            link = await asyncio.to_thread(operator.revoke_employee_link, profile,
                                           request.match_info["link_id"])
            return _json_response({"link": link})
        except operator.OperatorError as error:
            return web.json_response({"success": False, "error": str(error)}, status=error.status,
                                     headers={"Cache-Control": "no-store"})
        except Exception:
            logger.exception("Operator employee identity link revoke failed")
            return web.json_response({"success": False, "error": "Не удалось отозвать связь сотрудника с Operator."},
                                     status=503, headers={"Cache-Control": "no-store"})
    return endpoint


def dialog_context_handler(member: Callable, logger):
    async def endpoint(request: web.Request):
        profile = _profile(member, request)
        if not profile:
            return web.json_response({"success": False, "error": "Интеграция недоступна."},
                                     status=404, headers={"Cache-Control": "no-store"})
        try:
            result = await asyncio.to_thread(operator.dialog_context, profile,
                channel_id=request.query.get("channel_id", ""),
                conversation_id=request.query.get("conversation_id", ""))
            return _json_response({"context": result})
        except operator.OperatorError as error:
            return web.json_response({"success": False, "error": str(error)},
                                     status=error.status,
                                     headers={"Cache-Control": "no-store"})
        except Exception:
            logger.exception("Operator dialog context failed")
            return web.json_response({"success": False, "error": "Контекст Operator недоступен."},
                                     status=503, headers={"Cache-Control": "no-store"})
    return endpoint


def account_data_handler(member: Callable, origin: str, logger, resource: str):
    async def endpoint(request: web.Request):
        profile = _profile(member, request)
        if not profile:
            return web.json_response({"success": False, "error": "Интеграция недоступна."},
                                     status=404, headers={"Cache-Control": "no-store"})
        try:
            data = await _body(request, profile, origin)
            if set(data) != {"entity_type", "entity_id", "account"}:
                raise operator.OperatorError("Контекст Operator некорректен.")
            if not _has_account_link(profile, data["entity_type"], data["entity_id"], data["account"]):
                raise operator.OperatorError("Для этой записи нет подтверждённой связи с аккаунтом.", 403)
            if resource == "account_info":
                response = await asyncio.to_thread(operator.read, profile, resource,
                                               {"account": data["account"]})
                result = operator.account_info_payload(response, data["account"])
                return _json_response({"account_info": result})
            if resource == "profile_users":
                response = await asyncio.to_thread(operator.read, profile, resource,
                                               {"account": data["account"]})
                return _json_response({"users": operator.profile_users_payload(response)})
            if resource == "task_list":
                response = await asyncio.to_thread(operator.read, profile, resource,
                                               {"account": data["account"]})
                operator.task_list_contract_pending(response)
            raise operator.OperatorError("Операция Operator не поддерживается.")
        except operator.OperatorError as error:
            return web.json_response({"success": False, "error": str(error)},
                                     status=error.status,
                                     headers={"Cache-Control": "no-store"})
        except Exception:
            logger.exception("Operator account data read failed")
            return web.json_response({"success": False, "error": "Данные Operator временно недоступны."},
                                     status=503, headers={"Cache-Control": "no-store"})
    return endpoint


def ticket_draft_handler(member: Callable, origin: str, logger):
    async def endpoint(request: web.Request):
        profile = _profile(member, request)
        if not profile:
            return web.json_response({"success": False, "error": "Интеграция недоступна."},
                                     status=404, headers={"Cache-Control": "no-store"})
        try:
            data = await _body(request, profile, origin)
            if data.get("action") == "create":
                return web.json_response({"success": False,
                    "error": "Создание тикетов отключено до подтверждения контракта Operator.",
                    "code": "operator_task_create_contract_unconfirmed"}, status=409,
                    headers={"Cache-Control": "no-store"})
            if set(data) != {"title", "description", "account", "priority"}:
                raise operator.OperatorError("Поля черновика тикета некорректны.")
            draft = await asyncio.to_thread(operator.build_task_draft, data["title"],
                data["description"], data["account"], data["priority"])
            return _json_response({"draft": draft, "contract": operator.task_creation_contract()})
        except (operator.OperatorError, ValueError) as error:
            status = getattr(error, "status", 400)
            return web.json_response({"success": False, "error": str(error)},
                                     status=status, headers={"Cache-Control": "no-store"})
        except Exception:
            logger.exception("Operator task draft failed")
            return web.json_response({"success": False, "error": "Не удалось подготовить черновик тикета."},
                                     status=503, headers={"Cache-Control": "no-store"})
    return endpoint
