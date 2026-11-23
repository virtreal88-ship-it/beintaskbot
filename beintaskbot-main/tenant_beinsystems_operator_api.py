"""Same-origin HTTP handlers for the private Operator integration."""
from __future__ import annotations

import json
import asyncio
from collections.abc import Callable

from aiohttp import web

import tenant_beinsystems_operator as operator
import tenant_beinsystems_operator_company_import as company_import
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
        action = ""
        try:
            if request.method == "GET":
                if request.query.get("include_connected_login") == "1":
                    _context(request.query, profile)
                    return _json_response({"integration": await asyncio.to_thread(
                        operator.status, profile, include_connected_login=True)})
                return _json_response({"integration": await asyncio.to_thread(operator.status, profile)})
            data = await _body(request, profile, origin)
            action = str(data.get("action") or "").strip().lower()
            verification = None
            if action == "login" and set(data) == {"action", "login", "password"}:
                value = await asyncio.to_thread(
                    operator.connect_with_password, profile, data["login"], data["password"])
            elif action == "renew_connection" and set(data) == {"action","candidate_received_at"}:
                value = await asyncio.to_thread(operator.renew_connected_token, profile, data["candidate_received_at"])
            elif action == "directory_search" and set(data) == {"action", "search"}:
                directory = await asyncio.to_thread(
                    operator.search_authenticated_operator_account, profile, data["search"])
                directory["accounts_only_ready"] = bool(
                    (await asyncio.to_thread(operator.status, profile)).get("accounts_only_ready"))
                return _json_response({"directory": directory})
            elif action == "prepare_accounts_capability" and set(data) == {
                    "action", "candidate_received_at"}:
                confirmation = await asyncio.to_thread(
                    company_import.prepare_accounts_capability, profile,
                    data["candidate_received_at"])
                return _json_response({"accounts_only_confirmation": confirmation})
            elif action == "confirm_accounts_capability" and set(data) == {
                    "action", "acknowledged_identifier", "candidate_received_at"}:
                capability = await asyncio.to_thread(
                    company_import.confirm_accounts_capability, profile,
                    acknowledged_identifier=data["acknowledged_identifier"],
                    candidate_received_at=data["candidate_received_at"])
                value = await asyncio.to_thread(operator.status, profile)
                return _json_response({"accounts_only": capability, "integration": value})
            elif action == "verify_public_link_proof" and set(data) == {"action", "candidate_received_at"}:
                from tenant_operator_public_link_proof import verify_public_link_proof
                proof = await asyncio.to_thread(verify_public_link_proof, profile, data["candidate_received_at"])
                return _json_response({"public_link_proof": proof})
            elif action == "test" and set(data) == {"action", "search"}:
                verification = await asyncio.to_thread(
                    operator.verify_accounts_access, profile, data["search"])
                value = await asyncio.to_thread(operator.status, profile)
            elif action == "disconnect" and set(data) == {"action"}:
                value = await asyncio.to_thread(operator.disconnect, profile)
            else:
                raise operator.OperatorError("Операция Operator некорректна.")
            return _json_response({"integration": value,
                                   **({"verification": verification} if verification else {})})
        except operator.OperatorError as error:
            headers = {"Cache-Control": "no-store"}
            if error.status == 401 or error.upstream_http_status in (401, 403):
                # Provider-side auth failures must not be mistaken for a
                # Beintaskbot session failure by the shared browser coordinator.
                headers["X-Tenant-Auth-Source"] = "operator"
            body = {"success": False, "error": str(error)}
            if error.upstream_http_status in (401, 403):
                body["upstream_http_status"] = error.upstream_http_status
            if error.upstream_error_code:
                body["upstream_error_code"] = error.upstream_error_code
            return web.json_response(body, status=error.status, headers=headers)
        except Exception:
            if action == "verify_public_link_proof":
                logger.error("Operator public-link demo verification failed")
            else:
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
        action = ""
        try:
            if request.method == "GET":
                _context(dict(request.query), profile)
                rows = await asyncio.to_thread(
                    operator.imported_accounts, profile, search=request.query.get("search", ""))
                return _json_response({"accounts": rows})
            data = await _body(request, profile, origin)
            action = str(data.get("action") or "").strip().lower()
            if data == {"action": "import"}:
                result = await asyncio.to_thread(operator.import_accounts, profile)
                return _json_response({"imported": result["imported"],
                    "activated": result["activated"], "integration": result["status"],
                    "identity": result["identity"]})
            if data == {"action": "preview_first_company_batch"}:
                result = await asyncio.to_thread(company_import.first_company_batch, profile)
                return _json_response({"company_batch_preview": result})
            if data == {"action": "preview_company_import_plan"}:
                result = await asyncio.to_thread(company_import.first_company_batch, profile, plan=True, catalogue=True)
                return _json_response({"company_batch_preview": result})
            if data == {"action": "preview_first_company_batch_plan"}:
                result = await asyncio.to_thread(
                    company_import.first_company_batch, profile, plan=True)
                return _json_response({"company_batch_preview": result})
            if (set(data) == {"action", "receipt"} and action == "confirm_company_import_batch"
                    and isinstance(data["receipt"], str)):
                result = await asyncio.to_thread(
                    company_import.first_company_batch, profile, receipt=data["receipt"], catalogue=True)
                return _json_response({"company_import": result})
            if (set(data) == {"action", "receipt"} and action == "confirm_first_company_batch"
                    and isinstance(data["receipt"], str) and data["receipt"]):
                result = await asyncio.to_thread(
                    company_import.first_company_batch, profile, receipt=data["receipt"])
                return _json_response({"company_import": result})
            if data == {"action": "create_companies"}:
                if operator.first_company_batch_plan_enabled(profile.get("tenant_id")):
                    raise operator.OperatorError("Сначала откройте предпросмотр импорта компаний.", 409)
                result = await asyncio.to_thread(company_import.create_companies, profile)
                return _json_response({"company_import": result})
            if set(data) == {"action", "account_id"} and data.get("action") == "add_account_company":
                result = await asyncio.to_thread(
                    company_import.add_selected_account_company, profile, data["account_id"])
                return _json_response({"account_company": result})
            if set(data) != {"search"}:
                raise operator.OperatorError("Укажите строку поиска или действие import.")
            response = await asyncio.to_thread(operator.read, profile, "accounts", data)
            accounts = operator.accounts_list_payload(response)
            return _json_response({"accounts": accounts})
        except operator.OperatorError as error:
            return web.json_response({"success": False, "error": str(error)},
                                     status=error.status,
                                     headers={"Cache-Control": "no-store"})
        except Exception:
            if action in {"create_companies", "preview_first_company_batch",
                          "preview_first_company_batch_plan", "confirm_first_company_batch",
                          "preview_company_import_plan", "confirm_company_import_batch"}:
                logger.error("Operator company creation failed")
            else:
                logger.exception("Operator account request failed")
            return web.json_response({"success": False, "error": "Список аккаунтов Operator временно недоступен."},
                                     status=503, headers={"Cache-Control": "no-store"})
    return endpoint


def dialog_company_handler(member: Callable, origin: str, logger):
    async def endpoint(request: web.Request):
        profile = _profile(member, request)
        if not profile:
            return web.json_response({"success": False, "error": "Интеграция недоступна."},
                                     status=404, headers={"Cache-Control": "no-store"})
        try:
            from tenant_dialog_company_binding import binding as exact_binding
            legacy = {"channel_id", "conversation_id"}
            exact = legacy | {"provider", "lead_id"}
            if request.method == "GET":
                query = dict(request.query)
                _context(query, profile)
                fields = set(query) - {"expected_tenant_id", "expected_user_id"}
                if fields == exact:
                    found = await asyncio.to_thread(exact_binding, profile,
                        **{key: query[key] for key in exact})
                elif fields == legacy:
                    found = await asyncio.to_thread(operator.dialog_company_binding, profile,
                        channel_id=query["channel_id"], conversation_id=query["conversation_id"])
                else:
                    raise operator.OperatorError("Поля привязки компании некорректны.")
                return _json_response({"binding": found})
            data = await _body(request, profile, origin)
            if set(data) == exact | {"action","expected_company_id"} and data["action"] in ("company_tasks","company_modules","company_history"):
                from tenant_dialog_company_public_link import lookup_section
                result=await asyncio.to_thread(lookup_section,profile,section=data["action"],
                                               **{key:data[key] for key in exact | {"expected_company_id"}})
                return _json_response({"company_section":result})
            if set(data) == exact | {"action", "expected_company_id"} and data["action"] == "company_profile":
                from tenant_dialog_company_public_link import lookup_profile
                try:
                    result = await asyncio.to_thread(lookup_profile, profile,
                        **{key: data[key] for key in exact | {"expected_company_id"}})
                except operator.OperatorError:
                    raise
                except Exception:
                    logger.error("Operator dialogue company profile request failed")
                    raise operator.OperatorError("Данные компании недоступны.", 503) from None
                return _json_response({"company_profile": result})
            if set(data) == exact | {"company_search"} and isinstance(data["company_search"],str):
                choices = await asyncio.to_thread(exact_binding, profile, **data)
                return _json_response({"companies": choices})
            if set(data) in (exact | {"operator_search"}, exact | {"operator_account"},
                             exact | {"operator_account", "expected_company_id"}):
                from tenant_dialog_company_operator import chooser
                choice = "operator_account" in data
                term = data["operator_account" if choice else "operator_search"]
                result = await asyncio.to_thread(chooser,profile,**{key:data[key] for key in exact},
                                                 term=term,choice=choice,**({"expected_company_id":data["expected_company_id"]}
                                                     if "expected_company_id" in data else {}))
                return _json_response(result)
            if set(data) in (exact | {"company_id"}, exact | {"company_id", "expected_company_id"}):
                found = await asyncio.to_thread(exact_binding, profile, **data)
            elif set(data) in (legacy, legacy | {"company_id"}):
                found = await asyncio.to_thread(operator.set_dialog_company_binding, profile,
                    channel_id=data["channel_id"], conversation_id=data["conversation_id"],
                    company_id=data.get("company_id"))
            else:
                raise operator.OperatorError("Поля привязки компании некорректны.")
            return _json_response({"binding": found})
        except operator.OperatorError as error:
            return web.json_response({"success": False, "error": str(error)}, status=error.status,
                                     headers={"Cache-Control": "no-store"})
        except Exception:
            logger.exception("Operator dialog company binding failed")
            return web.json_response({"success": False, "error": "Не удалось сохранить компанию диалога."},
                                     status=503, headers={"Cache-Control": "no-store"})
    return endpoint

def dialog_company_action_handler(member, origin, logger):
    async def endpoint(request):
        profile = _profile(member, request)
        headers = {"Cache-Control": "no-store", "Vary": "Cookie"}
        try:
            if not profile:
                raise operator.OperatorError("Unavailable", 404)
            from tenant_dialog_company_actions import perform
            data = await _body(request, profile, origin)
            if set(data) != {"provider","lead_id","channel_id","conversation_id","expected_company_id",
                             "kind","request_id","fields"}:
                raise operator.OperatorError("Invalid fields", 400)
            state = await asyncio.to_thread(perform, profile, **data)
            return _json_response({"state": state})
        except (operator.OperatorError, TenantLinearError) as error:
            return web.json_response({"success":False},status=error.status,headers=headers)
        except Exception:
            logger.error("Operator company action unavailable")
            return web.json_response({"success":False},status=503,headers=headers)
    return endpoint


def dialog_company_public_link_handler(member: Callable, origin: str, logger):
    async def endpoint(request: web.Request):
        headers = {"Cache-Control": "no-store", "Vary": "Cookie"}
        profile = _profile(member, request)
        if not profile:
            return web.json_response({"success": False}, status=404, headers=headers)
        try:
            from tenant_dialog_company_public_link import copy_link, lookup_link
            data = await _body(request, profile, origin)
            fields = {"provider", "lead_id", "channel_id", "conversation_id", "expected_company_id"}
            lookup = lookup_link
            if data.get("action") == "copy_customer_link":
                lookup = copy_link
                del data["action"]
            if set(data) != fields:
                raise operator.OperatorError("Поля публичной ссылки некорректны.")
            result = await asyncio.to_thread(lookup, profile, **data)
            return _json_response({"public_link": result})
        except operator.OperatorError as error:
            return web.json_response({"success": False}, status=error.status, headers=headers)
        except Exception:
            # No exception body, provider response, account, JWT, or public link in logs.
            logger.error("Operator dialogue public link request failed")
            return web.json_response({"success": False}, status=503, headers=headers)
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
            if not data:
                return _json_response(await asyncio.to_thread(operator.staff_employee_users, profile))
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
            if set(data) == {"member_telegram_id", "login", "selection_revision"}:
                if (type(data["member_telegram_id"]) is not int
                        or not 0 < data["member_telegram_id"] < 2**63
                        or not isinstance(data["selection_revision"], str)):
                    raise operator.OperatorError("Поля связи сотрудника с Operator некорректны.")
                link = await asyncio.to_thread(operator.confirm_employee_link, profile,
                    member_telegram_id=data["member_telegram_id"], account=None,
                    login=data["login"], evidence_summary=None,
                    selection_revision=data["selection_revision"])
                return _json_response({"link": {k: v for k, v in link.items() if k != "account"}}, status=201)
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
