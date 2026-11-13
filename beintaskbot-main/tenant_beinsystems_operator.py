"""Private, read-only Operator connector and manually confirmed identity links.

Only API operations documented in API_DOCUMENTATION.md are transportable here.
Task creation deliberately has no live transport path until its response and
idempotency contract are confirmed.
"""
from __future__ import annotations

import json
import hashlib
import re
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.fernet import InvalidToken

from tenant_beinsystems_operator_access import (
    PROVIDER, api_base_url, is_operator_workspace,
)
from tenant_platform_core import (
    TenantPlatformError, _connect, _ensure_schema, _fernet, _json,
)
from tenant_policy import TenantPolicy

MAX_RESPONSE_BYTES = 1_000_000
MAX_AUTH_RESPONSE_BYTES = 64_000
REQUEST_TIMEOUT_SECONDS = 8
AUTH_API_BASE_URL = "https://auth-api.beinsystems.az"
READ_ENDPOINTS = {
    "me": ("/user/me", set()),
    "accounts": ("/user/accounts", {"search"}),
    "account_info": ("/user/info", {"account"}),
    "profile_users": ("/user/profileUsers", {"account"}),
    "task_list": ("/task/list", {"account"}),
}


class OperatorError(TenantPlatformError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _tenant(profile):
    tenant = str((profile or {}).get("tenant_id") or "")
    if not tenant or not is_operator_workspace(tenant):
        raise OperatorError("Operator недоступен для этого рабочего пространства.")
    return tenant


def _current_profile(cur, tenant, actor):
    from tenant_scoped_access_store import read_access_profile

    current = read_access_profile(cur, tenant, actor, lock=True)
    if (not current or current.get("active") is not True
            or current.get("tenant_status") != "active"):
        raise OperatorError("Нет доступа к активному рабочему пространству.")
    return current


def _authorize(cur, profile, permission):
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Нет доступа.") from None
    current = _current_profile(cur, tenant, actor)
    if not TenantPolicy(current).allows_action("operator_data", permission):
        raise OperatorError("Недостаточно прав для данных Operator.")
    return tenant, actor, current


def _operator_row(cur, tenant, *, lock=False):
    cur.execute(
        """SELECT status,metadata,secrets,connected_at,updated_at
        FROM saas_tenant_integrations
        WHERE tenant_id=%s::uuid AND provider=%s""" + (" FOR UPDATE" if lock else " FOR SHARE"),
        (tenant, PROVIDER),
    )
    return cur.fetchone()


def _public(row):
    row = row or {}
    return {
        "provider": PROVIDER,
        "status": str(row.get("status") or "not_connected"),
        "connected": row.get("status") == "connected",
        "configured": row.get("status") in {"configured", "connected"},
        "connected_at": row.get("connected_at").isoformat() if row.get("connected_at") else None,
        "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
        "api_ready": bool(api_base_url()),
        "capabilities": {
            "account_search": True, "account_info": True, "profile_users": True,
            "task_list": False, "task_create": False, "public_links": False,
        },
    }


def status(profile):
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Нет доступа.") from None
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            _authorize(cur, profile, "view")
            row = _operator_row(cur, tenant)
    return _public(row)


def save_token(profile, token, *, verified=False):
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Нет доступа.") from None
    if profile.get("role") != "owner" or profile.get("active") is False:
        raise OperatorError("Настроить Operator может только владелец BeinSystems.")
    if not api_base_url():
        raise OperatorError("Адрес Operator API не задан в серверной конфигурации.")
    clean = str(token or "").strip()
    if not clean or len(clean) > 8192 or any(ord(char) < 32 for char in clean):
        raise OperatorError("Проверьте служебный токен Operator.")
    instance_id = str(uuid.uuid4())
    generation = str(uuid.uuid4())
    status_value = "connected" if verified else "configured"
    encrypted = _fernet().encrypt(_json({
        "tenant_id": tenant, "provider": PROVIDER, "token": clean,
        "connection_instance_id": instance_id,
    }).encode("utf-8"))
    metadata = {"connection_instance_id": instance_id, "generation": generation}
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            current = _current_profile(cur, tenant, actor)
            if current.get("role") != "owner":
                raise OperatorError("Настроить Operator может только владелец BeinSystems.")
            cur.execute(
                """INSERT INTO saas_tenant_integrations
                  (tenant_id,provider,status,account_domain,metadata,secrets,connected_at,updated_at)
                VALUES (%s::uuid,%s,%s,%s,%s::jsonb,%s,
                  CASE WHEN %s THEN now() ELSE NULL END,now())
                ON CONFLICT (tenant_id,provider) DO UPDATE SET
                  account_domain=EXCLUDED.account_domain,
                  metadata=EXCLUDED.metadata,secrets=EXCLUDED.secrets,
                  status=EXCLUDED.status,connected_at=EXCLUDED.connected_at,updated_at=now()
                RETURNING status,metadata,connected_at,updated_at""",
                (tenant, PROVIDER, status_value, api_base_url(), _json(metadata), encrypted, verified),
            )
            row = cur.fetchone()
        conn.commit()
    return _public(row)


def _authenticate_operator(login, password, *, device_id=None, opener=None):
    """Use the same HTTPS login endpoint used by the Operator web application.

    Only the returned SSO token is persisted by the caller. Passwords and
    authentication response bodies are never logged or stored.
    """
    username = str(login or "").strip()
    secret = str(password or "")
    if (not username or len(username) > 240
            or any(ord(char) < 32 for char in username)
            or not secret or len(secret) > 512):
        raise OperatorError("Укажите корректный логин и пароль Operator.")

    # Match the live SSO login page: a bare account name means its admin login;
    # email, phone, and fully qualified logins are passed through unchanged.
    phone = re.fullmatch(r"\+?[0-9]{9,15}", re.sub(r"\s+", "", username))
    if "@" not in username and not phone:
        username = "admin@" + username
    payload = {
        "login": username,
        "password": secret,
        "uniqDeviceId": str(device_id or (
            "device_" + uuid.uuid4().hex + uuid.uuid4().hex[:7])),
    }
    request = Request(
        AUTH_API_BASE_URL.rstrip("/") + "/auth/login",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json", "Accept": "application/json",
            # Match the same-origin browser request made by Bein ID.
            "Origin": "https://auth.beinsystems.az",
            "Referer": "https://auth.beinsystems.az/",
        },
    )
    transport = opener or build_opener(_NoRedirect())
    try:
        response = transport.open(request, timeout=REQUEST_TIMEOUT_SECONDS)
        with response:
            raw = response.read(MAX_AUTH_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        if exc.code in (400, 401, 403):
            raise OperatorError("Operator не принял логин и пароль.", 401) from None
        if exc.code == 429:
            raise OperatorError("Operator ограничил попытки входа. Повторите позже.", 429) from None
        raise OperatorError("Сервис авторизации Operator временно недоступен.", 503) from None
    except (URLError, TimeoutError, OSError):
        raise OperatorError("Сервис авторизации Operator временно недоступен.", 503) from None
    if len(raw) > MAX_AUTH_RESPONSE_BYTES:
        raise OperatorError("Ответ сервиса авторизации Operator слишком большой.", 502)
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise OperatorError("Сервис авторизации Operator вернул неизвестный ответ.", 502) from None
    data = result.get("data") if isinstance(result, dict) else None
    token = data.get("token") if isinstance(data, dict) else None
    if (not isinstance(result, dict) or result.get("success") is not True
            or not isinstance(token, str) or not token.strip()
            or len(token) > 8192 or any(ord(char) < 32 for char in token)):
        raise OperatorError("Operator не выдал токен. Проверьте логин и пароль.", 401)
    return token.strip()


def connect_with_password(profile, login, password):
    """Authenticate against Operator SSO, validate the JWT, then store it."""
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Нет доступа.") from None
    if profile.get("role") != "owner" or profile.get("active") is False:
        raise OperatorError("Подключить Operator может только владелец BeinSystems.")
    base_url = api_base_url()
    if not base_url:
        raise OperatorError("Адрес Operator API не задан в серверной конфигурации.")

    # Bound authentication attempts per owner and minute. No login or password
    # is written to this rate-limit record.
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            current = _current_profile(cur, tenant, actor)
            if current.get("role") != "owner":
                raise OperatorError("Подключить Operator может только владелец BeinSystems.")
            cur.execute("""INSERT INTO saas_beinsystems_operator_rate_limits
              (tenant_id,actor_telegram_id,endpoint,bucket_start,request_count)
              VALUES(%s::uuid,%s,'auth_login',date_trunc('minute',now()),1)
              ON CONFLICT(tenant_id,actor_telegram_id,endpoint,bucket_start)
              DO UPDATE SET request_count=saas_beinsystems_operator_rate_limits.request_count+1,
                updated_at=now() RETURNING request_count""", (tenant, actor))
            if int(cur.fetchone()["request_count"]) > 5:
                raise OperatorError("Слишком много попыток подключения. Повторите через минуту.", 429)
        conn.commit()

    device_seed = "beintaskbot:" + PROVIDER + ":" + tenant
    device_id = "device_" + uuid.uuid5(uuid.NAMESPACE_URL, device_seed).hex + \
        uuid.uuid5(uuid.NAMESPACE_DNS, device_seed).hex[:7]
    token = _authenticate_operator(login, password, device_id=device_id)
    me = _request_operator("me", {}, token=token, base_url=base_url)
    if (not isinstance(me, dict) or not me
            or (me.get("status") is not None and str(me.get("status")).casefold() != "success")):
        raise OperatorError("Полученный токен не прошёл проверку в Operator API.", 401)
    # Re-check owner/workspace after remote calls, then encrypt and store only
    # the validated SSO token.
    return save_token(profile, token, verified=True)


def disconnect(profile):
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Нет доступа.") from None
    if profile.get("role") != "owner":
        raise OperatorError("Отключить Operator может только владелец BeinSystems.")
    metadata = {"connection_instance_id": str(uuid.uuid4()), "generation": str(uuid.uuid4())}
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            current = _current_profile(cur, tenant, actor)
            if current.get("role") != "owner":
                raise OperatorError("Отключить Operator может только владелец BeinSystems.")
            cur.execute(
                """UPDATE saas_tenant_integrations SET status='not_connected',account_domain=%s,
                  metadata=%s::jsonb,secrets=NULL,connected_at=NULL,updated_at=now()
                  WHERE tenant_id=%s::uuid AND provider=%s
                  RETURNING status,metadata,connected_at,updated_at""",
                (api_base_url(), _json(metadata), tenant, PROVIDER),
            )
            row = cur.fetchone()
        conn.commit()
    return _public(row)


def _credentials(tenant, *, allow_configured=False):
    if not is_operator_workspace(tenant):
        raise OperatorError("Operator недоступен для этого рабочего пространства.")
    base_url = api_base_url()
    if not base_url:
        raise OperatorError("Адрес Operator API не задан в серверной конфигурации.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            row = _operator_row(cur, tenant)
    allowed_status = {"connected", "configured"} if allow_configured else {"connected"}
    if not row or row.get("status") not in allowed_status or not row.get("secrets"):
        raise OperatorError("Operator ещё не подключён.")
    try:
        secret = json.loads(_fernet().decrypt(bytes(row["secrets"])).decode("utf-8"))
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise OperatorError("Данные подключения Operator не удалось прочитать.") from exc
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    if (not isinstance(secret, dict) or secret.get("tenant_id") != tenant
            or secret.get("provider") != PROVIDER
            or secret.get("connection_instance_id") != metadata.get("connection_instance_id")
            or not secret.get("token") or not metadata.get("generation")):
        raise OperatorError("Данные подключения Operator не соответствуют рабочему пространству.")
    return {
        "token": str(secret["token"]), "base_url": base_url,
        "connection_instance_id": str(metadata["connection_instance_id"]),
        "generation": str(metadata["generation"]), "status": str(row["status"]),
    }


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request_operator(endpoint, payload, *, token, base_url=None, opener=None):
    """Call one allowlisted documented read method; never follows redirects."""
    if endpoint not in READ_ENDPOINTS:
        raise OperatorError("Операция Operator пока не поддерживается.")
    path, fields = READ_ENDPOINTS[endpoint]
    if not isinstance(payload, dict) or set(payload) - fields:
        raise OperatorError("Параметры запроса Operator некорректны.")
    if endpoint == "accounts":
        search = str(payload.get("search") or "").strip()
        if search and len(search) < 4:
            raise OperatorError("Поиск Operator требует не менее четырёх символов.")
        if len(search) > 120:
            raise OperatorError("Слишком длинный поисковый запрос.")
        payload = {"search": search} if search else {}
    if "account" in payload:
        account = str(payload.get("account") or "").strip()
        if not account or len(account) > 240 or any(ord(char) < 32 for char in account):
            raise OperatorError("Идентификатор аккаунта Operator некорректен.")
        payload = {"account": account}
    base = str(base_url or api_base_url()).rstrip("/")
    if not base.startswith("https://"):
        raise OperatorError("Адрес Operator API не настроен.")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(base + path, data=body, method="POST", headers={
        "Authorization": "Bearer " + str(token),
        "Content-Type": "application/json", "Accept": "application/json",
    })
    transport = opener or build_opener(_NoRedirect())
    try:
        response = transport.open(request, timeout=REQUEST_TIMEOUT_SECONDS)
        with response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        code = getattr(exc, "code", None)
        if code in (401, 403):
            raise OperatorError("Operator отклонил служебную авторизацию.") from None
        raise OperatorError("Operator временно не ответил.") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise OperatorError("Ответ Operator превышает допустимый размер.")
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperatorError("Operator вернул ответ неизвестного формата.") from exc
    if not isinstance(result, (dict, list)):
        raise OperatorError("Operator вернул ответ неизвестного формата.")
    return result


def verify_connection(profile):
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Нет доступа.") from None
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            current = _current_profile(cur, tenant, actor)
            if current.get("role") != "owner":
                raise OperatorError("Проверить Operator может только владелец BeinSystems.")
    credentials = _credentials(tenant, allow_configured=True)
    read(profile, "me", {}, allow_configured=True)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""UPDATE saas_tenant_integrations SET status='connected',
              connected_at=COALESCE(connected_at,now()),updated_at=now()
              WHERE tenant_id=%s::uuid AND provider=%s AND metadata->>'generation'=%s
              RETURNING status,metadata,connected_at,updated_at""",
              (tenant, PROVIDER, credentials["generation"]))
            row = cur.fetchone()
            current = _current_profile(cur, tenant, actor)
            if current.get("role") != "owner":
                raise OperatorError("Проверить Operator может только владелец BeinSystems.")
        conn.commit()
    if not row:
        raise OperatorError("Подключение Operator изменилось во время проверки.")
    return _public(row)


def read(profile, endpoint, payload, *, allow_configured=False):
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Нет доступа.") from None
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            _, actor, _ = _authorize(cur, profile, "view")
            limits = {"me": 30, "accounts": 12, "account_info": 30,
                      "profile_users": 20, "task_list": 20}
            cur.execute("""INSERT INTO saas_beinsystems_operator_rate_limits
              (tenant_id,actor_telegram_id,endpoint,bucket_start,request_count)
              VALUES(%s::uuid,%s,%s,date_trunc('minute',now()),1)
              ON CONFLICT(tenant_id,actor_telegram_id,endpoint,bucket_start)
              DO UPDATE SET request_count=saas_beinsystems_operator_rate_limits.request_count+1,
                updated_at=now() RETURNING request_count""", (tenant, actor, endpoint))
            count = int(cur.fetchone()["request_count"])
            if count > limits.get(endpoint, 1):
                raise OperatorError("Слишком много запросов Operator. Повторите позже.", 429)
            account = str((payload or {}).get("account") or "").strip()
            cur.execute("""INSERT INTO saas_tenant_audit_events
              (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
              VALUES(%s,%s::uuid,%s,'operator_read_requested','operator_endpoint',%s,%s::jsonb)""",
              (str(uuid.uuid4()), tenant, actor, endpoint,
               _json({"account_hash": hashlib.sha256(account.encode("utf-8")).hexdigest() if account else ""})))
        conn.commit()
    credentials = _credentials(tenant, allow_configured=allow_configured)
    result = _request_operator(endpoint, payload, token=credentials["token"],
                               base_url=credentials["base_url"])
    fresh = _credentials(tenant, allow_configured=allow_configured)
    if (fresh["generation"] != credentials["generation"]
            or fresh["connection_instance_id"] != credentials["connection_instance_id"]):
        raise OperatorError("Подключение Operator изменилось во время запроса.", 409)
    return result


def account_info_payload(response, requested_account):
    """Allowlist nonfinancial fields explicitly documented for account info."""
    if not isinstance(response, dict):
        raise OperatorError("Формат профиля Operator нужно подтвердить у разработчика.")
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    if not isinstance(data, dict):
        raise OperatorError("Формат профиля Operator нужно подтвердить у разработчика.")
    status_value = str(response.get("status") or "").casefold()
    if status_value and status_value != "success":
        raise OperatorError("Operator не вернул профиль этого аккаунта.", 404)
    result = {"account": str(requested_account)}
    for key in ("last_contact", "partner_name"):
        value = data.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            result[key] = str(value)[:240]
    return result


def task_list_contract_pending(response):
    """The path is documented, but its response schema is not."""
    raise OperatorError("Формат ответа /task/list нужно подтвердить у разработчика Operator.", 409)


def accounts_list_payload(response):
    """Project only documented account identifiers from the documented list."""
    if isinstance(response, dict):
        status = str(response.get("status") or "").strip().casefold()
        if status and status != "success":
            raise OperatorError("Operator сообщил об ошибке списка аккаунтов.")
        values = response.get("data")
    else:
        values = response
    if not isinstance(values, list):
        raise OperatorError("Формат списка аккаунтов Operator нужно подтвердить у разработчика.")
    result = []
    for item in values[:100]:
        if not isinstance(item, dict):
            continue
        account = str(item.get("account") or "").strip()
        if account and len(account) <= 240 and not any(ord(char) < 32 for char in account):
            result.append({"account": account})
    return result


def profile_users_payload(response):
    """Expose scoped logins; the user confirms account + login manually."""
    if isinstance(response, dict):
        status = str(response.get("status") or "").strip().casefold()
        if status and status != "success":
            raise OperatorError("Operator сообщил об ошибке списка пользователей.")
        values = response.get("data")
    else:
        values = response
    if not isinstance(values, list):
        raise OperatorError("Формат списка пользователей Operator нужно подтвердить у разработчика.")
    result = []
    for item in values[:100]:
        if isinstance(item, dict):
            login = str(item.get("login") or "").strip()
            if login and len(login) <= 240 and not any(ord(char) < 32 for char in login):
                result.append({"login": login, "bindable": True})
    return result


def _operator_owner(profile, cur):
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Нет доступа.") from None
    current = _current_profile(cur, tenant, actor)
    if current.get("role") != "owner":
        raise OperatorError("Связывать сотрудников с Operator может только владелец workspace.", 403)
    return tenant, actor, current


def operator_employee_users(profile, account):
    """List explicitly selectable Operator logins for the owner-managed staff map."""
    tenant = _tenant(profile)
    account = str(account or "").strip()
    if not account or len(account) > 240 or any(ord(char) < 32 for char in account):
        raise OperatorError("Укажите корректный Operator account.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            _, _, _ = _operator_owner(profile, cur)
            integration = _operator_row(cur, tenant)
            if not integration or integration.get("status") != "connected":
                raise OperatorError("Сначала проверьте подключение Operator.")
    response = read(profile, "profile_users", {"account": account})
    return profile_users_payload(response)


def confirm_employee_link(profile, *, member_telegram_id, account, login,
                          evidence_summary):
    """Map one active Beintaskbot employee to an exact listed account/login pair."""
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
        member_id = int(member_telegram_id)
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Выберите сотрудника Beintaskbot из списка рабочего пространства.") from None
    if member_id <= 0:
        raise OperatorError("Выберите сотрудника Beintaskbot из списка рабочего пространства.")
    account = str(account or "").strip()
    login = str(login or "").strip()
    evidence = str(evidence_summary or "").strip()
    if (not account or not login or len(account) > 240 or len(login) > 240
            or any(ord(char) < 32 for char in account + login)
            or len(evidence) < 8 or len(evidence) > 500):
        raise OperatorError("Выберите Operator account и пользователя, укажите основание проверки.")

    # The list is read with the configured restricted Operator JWT. Never trust
    # a login supplied only by the browser.
    listed = operator_employee_users(profile, account)
    if not any(item["login"] == login for item in listed):
        raise OperatorError("Пользователь Operator отсутствует в выбранном аккаунте.", 409)

    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            current = _current_profile(cur, tenant, actor)
            if current.get("role") != "owner":
                raise OperatorError("Связывать сотрудников с Operator может только владелец workspace.", 403)
            integration = _operator_row(cur, tenant, lock=True)
            metadata = integration.get("metadata") if integration and isinstance(integration.get("metadata"), dict) else {}
            generation = str(metadata.get("generation") or "")
            instance = str(metadata.get("connection_instance_id") or "")
            if not generation or not instance or not integration or integration.get("status") != "connected":
                raise OperatorError("Подключение Operator изменилось. Повторите проверку.", 409)
            cur.execute("""SELECT active FROM saas_tenant_members
              WHERE tenant_id=%s::uuid AND telegram_id=%s FOR UPDATE""", (tenant, member_id))
            member = cur.fetchone()
            if not member or member.get("active") is not True:
                raise OperatorError("Сотрудник Beintaskbot неактивен или не найден.", 404)
            cur.execute("""SELECT id,operator_account,operator_login,version,verified_at
              FROM saas_beinsystems_operator_employee_links
              WHERE tenant_id=%s::uuid AND connection_generation=%s
                AND connection_instance_id=%s::uuid AND member_telegram_id=%s
                AND revoked_at IS NULL FOR UPDATE""", (tenant, generation, instance, member_id))
            existing = cur.fetchone()
            if existing and existing["operator_account"] == account and existing["operator_login"] == login:
                row = existing
            else:
                cur.execute("""SELECT 1 FROM saas_beinsystems_operator_employee_links
                  WHERE tenant_id=%s::uuid AND connection_generation=%s AND operator_account=%s
                    AND operator_login=%s AND member_telegram_id<>%s AND revoked_at IS NULL""",
                  (tenant, generation, account, login, member_id))
                if cur.fetchone():
                    raise OperatorError("Этот Operator-пользователь уже связан с другим сотрудником.", 409)
                if existing:
                    cur.execute("""UPDATE saas_beinsystems_operator_employee_links
                      SET revoked_at=now(),version=version+1,updated_at=now()
                      WHERE tenant_id=%s::uuid AND id=%s::uuid AND revoked_at IS NULL""",
                      (tenant, str(existing["id"])))
                cur.execute("""INSERT INTO saas_beinsystems_operator_employee_links
                  (id,tenant_id,connection_instance_id,connection_generation,member_telegram_id,
                   operator_account,operator_login,verification_basis,evidence_summary,verified_by)
                  VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,'manual_operator_user_selection',%s,%s)
                  RETURNING id,operator_account,operator_login,version,verified_at""",
                  (str(uuid.uuid4()), tenant, instance, generation, member_id, account, login, evidence, actor))
                row = cur.fetchone()
                cur.execute("""INSERT INTO saas_tenant_audit_events
                  (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                  VALUES(%s,%s::uuid,%s,'operator_employee_link_confirmed','employee',%s,%s::jsonb)""",
                  (str(uuid.uuid4()), tenant, actor, str(member_id),
                   _json({"operator_account": account, "operator_login": login,
                          "generation": generation, "basis": "manual_operator_user_selection"})))
        conn.commit()
    return {"id": str(row["id"]), "member_telegram_id": member_id,
            "account": row["operator_account"], "login": row["operator_login"],
            "status": "confirmed", "version": int(row["version"]),
            "verified_at": row["verified_at"].isoformat()}


def employee_links(profile):
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Нет доступа.") from None
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            _operator_owner(profile, cur)
            integration = _operator_row(cur, tenant)
            metadata = integration.get("metadata") if integration and isinstance(integration.get("metadata"), dict) else {}
            generation = str(metadata.get("generation") or "")
            instance = str(metadata.get("connection_instance_id") or "")
            cur.execute("""SELECT l.id,l.member_telegram_id,l.operator_account,l.operator_login,
                l.connection_generation,l.connection_instance_id::text,l.verification_basis,
                l.evidence_summary,l.verified_by,l.verified_at,l.revoked_at,l.version,m.active
              FROM saas_beinsystems_operator_employee_links l
              LEFT JOIN saas_tenant_members m ON m.tenant_id=l.tenant_id
                AND m.telegram_id=l.member_telegram_id
              WHERE l.tenant_id=%s::uuid
              ORDER BY l.updated_at DESC LIMIT 200""", (tenant,))
            rows = cur.fetchall()
    result = []
    for row in rows:
        current = (not row["revoked_at"] and row["connection_generation"] == generation
                   and row["connection_instance_id"] == instance and row["active"] is True)
        result.append({"id": str(row["id"]), "member_telegram_id": int(row["member_telegram_id"]),
            "account": row["operator_account"], "login": row["operator_login"],
            "status": "confirmed" if current else "stale" if not row["revoked_at"] else "revoked",
            "evidence": row["evidence_summary"], "verified_by": int(row["verified_by"]),
            "verified_at": row["verified_at"].isoformat(), "revoked_at": row["revoked_at"].isoformat() if row["revoked_at"] else None,
            "version": int(row["version"])})
    return result


def revoke_employee_link(profile, link_id):
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
        ident = str(uuid.UUID(str(link_id)))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Связь Operator не найдена.", 404) from None
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            _operator_owner(profile, cur)
            cur.execute("""UPDATE saas_beinsystems_operator_employee_links
              SET revoked_at=now(),version=version+1,updated_at=now()
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND revoked_at IS NULL
              RETURNING member_telegram_id,operator_account,operator_login,version""", (tenant, ident))
            row = cur.fetchone()
            if row:
                cur.execute("""INSERT INTO saas_tenant_audit_events
                  (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                  VALUES(%s,%s::uuid,%s,'operator_employee_link_revoked','employee',%s,%s::jsonb)""",
                  (str(uuid.uuid4()), tenant, actor, str(row["member_telegram_id"]),
                   _json({"operator_account": row["operator_account"], "operator_login": row["operator_login"],
                          "version": int(row["version"])})))
        conn.commit()
    if not row:
        raise OperatorError("Связь Operator не найдена.", 404)
    return {"id": ident, "member_telegram_id": int(row["member_telegram_id"]),
            "account": row["operator_account"], "login": row["operator_login"],
            "status": "revoked", "version": int(row["version"])}


def confirm_account_link(profile, *, local_entity_type, local_entity_id,
                         external_account, evidence_summary,
                         verification_basis="manual_operator_review"):
    """Assert an account mapping after an employee verifies it in Operator."""
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
        local_id = str(uuid.UUID(str(local_entity_id)))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Идентификатор записи некорректен.") from None
    entity_type = str(local_entity_type or "")
    if entity_type not in {"company", "contact", "support_ticket"}:
        raise OperatorError("Тип локальной записи не поддерживается.")
    account = str(external_account or "").strip()
    evidence = str(evidence_summary or "").strip()
    if (not account or len(account) > 240 or any(ord(char) < 32 for char in account)
            or len(evidence) < 8 or len(evidence) > 500):
        raise OperatorError("Укажите аккаунт и краткое основание ручной проверки.")
    if entity_type == "contact":
        raise OperatorError("Связь контакта с ERP-пользователем отключена до подтверждения стабильного ID.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            current = _current_profile(cur, tenant, actor)
            if not TenantPolicy(current).allows_action("operator_data", "link"):
                raise OperatorError("Недостаточно прав для подтверждения связей Operator.")
            integration = _operator_row(cur, tenant, lock=True)
            if not integration or integration.get("status") not in {"configured", "connected"}:
                raise OperatorError("Operator ещё не подключён.")
            metadata = integration.get("metadata") if isinstance(integration.get("metadata"), dict) else {}
            generation = str(metadata.get("generation") or "")
            current_instance = str(metadata.get("connection_instance_id") or "")
            if not generation or not current_instance:
                raise OperatorError("Подключение Operator нужно повторно настроить.")
            if entity_type == "company":
                from tenant_crm_native_access import (
                    current_access, locked_resource, require_local_relation,
                )
                access_profile = current_access(cur, tenant, actor, "customers", "update")
                local = locked_resource(cur, access_profile, "customers", "update",
                    "saas_crm_native_companies", tenant, local_id)
                if not local:
                    raise OperatorError("CRM-компания недоступна по вашим правам.")
                require_local_relation(access_profile, tenant, "company")
            else:
                from tenant_support_access import require as require_ticket
                require_ticket(cur, {"tenant_id": tenant, "telegram_id": actor},
                               local_id, "update", lock=True)
                cur.execute("""SELECT c.company_id::text AS company_id
                  FROM saas_support_tickets t
                  JOIN saas_crm_native_contact_conversations m
                    ON m.tenant_id=t.tenant_id AND m.provider='connect'
                   AND m.channel_id=t.channel_id AND m.conversation_id=t.conversation_id
                  JOIN saas_crm_native_contacts c
                    ON c.tenant_id=m.tenant_id AND c.id=m.contact_id AND c.archived_at IS NULL
                  WHERE t.tenant_id=%s::uuid AND t.id=%s::uuid""", (tenant, local_id))
                case_contact = cur.fetchone()
                if not case_contact or not case_contact.get("company_id"):
                    raise OperatorError("В кейсе нет однозначной связи с CRM-компанией.")
                cur.execute("""SELECT 1 FROM saas_beinsystems_operator_links
                  WHERE tenant_id=%s::uuid AND local_entity_type='company' AND local_entity_id=%s::uuid
                    AND external_entity_type='account' AND external_scope_id='' AND external_id=%s
                    AND connection_generation=%s AND connection_instance_id=%s AND revoked_at IS NULL""",
                  (tenant, case_contact["company_id"], account, generation, current_instance))
                if not cur.fetchone():
                    raise OperatorError("Сначала подтвердите связь CRM-компании с этим Operator account.")
            cur.execute("""INSERT INTO saas_beinsystems_operator_links
              (id,tenant_id,connection_instance_id,connection_generation,local_entity_type,local_entity_id,
               external_entity_type,external_id,external_scope_id,verification_basis,evidence_summary,verified_by)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s::uuid,'account',%s,'',%s,%s,%s)
              ON CONFLICT DO NOTHING RETURNING id,version,verified_at""",
              (str(uuid.uuid4()), tenant, current_instance, generation, entity_type, local_id,
               account, "manual_operator_review", evidence, actor))
            row = cur.fetchone()
            if not row:
                cur.execute("""SELECT id,version,verified_at FROM saas_beinsystems_operator_links
                  WHERE tenant_id=%s::uuid AND connection_generation=%s AND external_entity_type='account'
                    AND external_scope_id='' AND external_id=%s AND local_entity_type=%s
                    AND local_entity_id=%s::uuid AND revoked_at IS NULL""",
                  (tenant, generation, account, entity_type, local_id))
                row = cur.fetchone()
            if not row:
                raise OperatorError("Аккаунт Operator уже связан с другой CRM-записью; требуется разбор владельцем.")
            cur.execute("""INSERT INTO saas_tenant_audit_events
              (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
              VALUES(%s,%s::uuid,%s,'operator_identity_confirmed',%s,%s,%s::jsonb)""",
              (str(uuid.uuid4()), tenant, actor, entity_type, local_id,
               _json({"external_entity_type": "account", "external_id": account,
                      "generation": generation, "basis": "manual_operator_review"})))
        conn.commit()
    return {"id": str(row["id"]), "version": int(row["version"]),
            "verified_at": row["verified_at"].isoformat(), "account": account,
            "status": "confirmed", "connection_generation": generation}


def confirm_profile_user_link(profile, *, local_contact_id, account, login, evidence_summary):
    """Bind a manually verified contact to a user within a confirmed account.

    The scoped identity is account+login because Operator documents account as
    the profileUsers namespace and uses login='admin' in more than one account.
    """
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
        contact_id = str(uuid.UUID(str(local_contact_id)))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Идентификатор контакта некорректен.") from None
    account, login = str(account or "").strip(), str(login or "").strip()
    evidence = str(evidence_summary or "").strip()
    if (not account or len(account) > 240 or not login or len(login) > 240
            or any(ord(char) < 32 for char in account + login)
            or len(evidence) < 8 or len(evidence) > 500):
        raise OperatorError("Укажите account, login и основание ручной проверки.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            current = _current_profile(cur, tenant, actor)
            if not TenantPolicy(current).allows_action("operator_data", "link"):
                raise OperatorError("Недостаточно прав для подтверждения связей Operator.")
            integration = _operator_row(cur, tenant, lock=True)
            if not integration or integration.get("status") not in {"configured", "connected"}:
                raise OperatorError("Operator ещё не подключён.")
            metadata = integration.get("metadata") if isinstance(integration.get("metadata"), dict) else {}
            generation = str(metadata.get("generation") or "")
            instance = str(metadata.get("connection_instance_id") or "")
            if not generation or not instance:
                raise OperatorError("Подключение Operator нужно повторно настроить.")
            from tenant_crm_native_access import current_access, locked_resource, require_local_relation
            access_profile = current_access(cur, tenant, actor, "customers", "update")
            contact = locked_resource(cur, access_profile, "customers", "update",
                                      "saas_crm_native_contacts", tenant, contact_id)
            if not contact:
                raise OperatorError("CRM-контакт недоступен по вашим правам.")
            company_id = str(contact.get("company_id") or "")
            if not company_id:
                raise OperatorError("Сначала привяжите контакт к CRM-компании.")
            require_local_relation(access_profile, tenant, "contact")
            cur.execute("""SELECT 1 FROM saas_beinsystems_operator_links
              WHERE tenant_id=%s::uuid AND local_entity_type='company' AND local_entity_id=%s::uuid
                AND external_entity_type='account' AND external_scope_id='' AND external_id=%s
                AND connection_generation=%s AND connection_instance_id=%s AND revoked_at IS NULL""",
              (tenant, company_id, account, generation, instance))
            if not cur.fetchone():
                raise OperatorError("CRM-компания не связана с этим Operator account.")
            cur.execute("""INSERT INTO saas_beinsystems_operator_links
              (id,tenant_id,connection_instance_id,connection_generation,local_entity_type,local_entity_id,
               external_entity_type,external_id,external_scope_id,verification_basis,evidence_summary,verified_by)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s,'contact',%s::uuid,'profile_user',%s,%s,%s,%s,%s)
              ON CONFLICT DO NOTHING RETURNING id,version,verified_at""",
              (str(uuid.uuid4()), tenant, instance, generation, contact_id, login, account,
               "manual_operator_review", evidence, actor))
            row = cur.fetchone()
            if not row:
                cur.execute("""SELECT id,version,verified_at FROM saas_beinsystems_operator_links
                  WHERE tenant_id=%s::uuid AND connection_generation=%s AND external_entity_type='profile_user'
                    AND external_scope_id=%s AND external_id=%s AND local_entity_type='contact'
                    AND local_entity_id=%s::uuid AND revoked_at IS NULL""",
                  (tenant, generation, account, login, contact_id))
                row = cur.fetchone()
            if not row:
                raise OperatorError("Этот профиль Operator уже связан с другой CRM-записью.")
            cur.execute("""INSERT INTO saas_tenant_audit_events
              (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
              VALUES(%s,%s::uuid,%s,'operator_identity_confirmed','contact',%s,%s::jsonb)""",
              (str(uuid.uuid4()), tenant, actor, contact_id,
               _json({"external_entity_type": "profile_user", "external_id": login,
                      "account": account, "generation": generation,
                      "basis": "manual_operator_review"})))
        conn.commit()
    return {"id": str(row["id"]), "version": int(row["version"]),
            "verified_at": row["verified_at"].isoformat(), "account": account,
            "login": login, "status": "confirmed", "connection_generation": generation}


def revoke_link(profile, link_id):
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
        ident = str(uuid.UUID(str(link_id)))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Идентификатор связи некорректен.") from None
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            current = _current_profile(cur, tenant, actor)
            if not TenantPolicy(current).allows_action("operator_data", "link"):
                raise OperatorError("Недостаточно прав для отзыва связей Operator.")
            cur.execute("""SELECT local_entity_type,local_entity_id FROM saas_beinsystems_operator_links
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND revoked_at IS NULL FOR UPDATE""",
              (tenant, ident))
            target = cur.fetchone()
            if not target:
                row = None
            elif target["local_entity_type"] == "support_ticket":
                from tenant_support_access import require as require_ticket
                require_ticket(cur, {"tenant_id": tenant, "telegram_id": actor},
                               str(target["local_entity_id"]), "update", lock=True)
            elif target["local_entity_type"] == "company":
                from tenant_crm_native_access import current_access, locked_resource
                access_profile = current_access(cur, tenant, actor, "customers", "update")
                if not locked_resource(cur, access_profile, "customers", "update",
                        "saas_crm_native_companies", tenant, str(target["local_entity_id"])):
                    raise OperatorError("CRM-компания недоступна по вашим правам.")
            else:
                from tenant_crm_native_access import current_access, locked_resource
                access_profile = current_access(cur, tenant, actor, "customers", "update")
                if not locked_resource(cur, access_profile, "customers", "update",
                        "saas_crm_native_contacts", tenant, str(target["local_entity_id"])):
                    raise OperatorError("CRM-контакт недоступен по вашим правам.")
            if target:
                cur.execute("""UPDATE saas_beinsystems_operator_links SET revoked_at=now(),
                  version=version+1,updated_at=now()
                  WHERE tenant_id=%s::uuid AND id=%s::uuid AND revoked_at IS NULL
                  RETURNING id,local_entity_type,local_entity_id,external_entity_type,external_scope_id,external_id,version""",
                  (tenant, ident))
                row = cur.fetchone()
            if row:
                cur.execute("""INSERT INTO saas_tenant_audit_events
                  (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                  VALUES(%s,%s::uuid,%s,'operator_identity_revoked',%s,%s,%s::jsonb)""",
                  (str(uuid.uuid4()), tenant, actor, row["local_entity_type"],
                   str(row["local_entity_id"]),
                   _json({"external_entity_type": row["external_entity_type"], "external_id": row["external_id"],
                          "external_scope_id": row["external_scope_id"],
                          "version": int(row["version"])})))
        conn.commit()
    if not row:
        raise OperatorError("Связь Operator не найдена.")
    return {"id": str(row["id"]), "version": int(row["version"]), "status": "revoked"}


def links_for_local(profile, *, local_entity_type, local_entity_id):
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
        local_id = str(uuid.UUID(str(local_entity_id)))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Идентификатор записи некорректен.") from None
    if local_entity_type not in {"company", "contact", "support_ticket"}:
        raise OperatorError("Тип локальной записи не поддерживается.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            current = _current_profile(cur, tenant, actor)
            if not TenantPolicy(current).allows_action("operator_data", "view"):
                raise OperatorError("Недостаточно прав для данных Operator.")
            if local_entity_type == "company":
                from tenant_crm_native_access import current_access, locked_resource
                access_profile = current_access(cur, tenant, actor, "customers", "view")
                resource = locked_resource(cur, access_profile, "customers", "view",
                    "saas_crm_native_companies", tenant, local_id)
                if not resource:
                    raise OperatorError("CRM-компания недоступна по вашим правам.")
            elif local_entity_type == "support_ticket":
                from tenant_support_access import require as require_ticket
                require_ticket(cur, {"tenant_id": tenant, "telegram_id": actor}, local_id, "view")
            else:
                from tenant_crm_native_access import current_access, locked_resource
                access_profile = current_access(cur, tenant, actor, "customers", "view")
                resource = locked_resource(cur, access_profile, "customers", "view",
                    "saas_crm_native_contacts", tenant, local_id)
                if not resource:
                    raise OperatorError("CRM-контакт недоступен по вашим правам.")
            integration = _operator_row(cur, tenant)
            metadata = integration.get("metadata") if integration and isinstance(integration.get("metadata"), dict) else {}
            cur.execute("""SELECT l.id,l.external_entity_type,l.external_id,l.external_scope_id,
                l.verification_basis,l.evidence_summary,l.verified_at,l.verified_by,l.revoked_at,l.version,
                (l.revoked_at IS NULL AND l.connection_generation=%s AND l.connection_instance_id=%s) AS current
              FROM saas_beinsystems_operator_links l
              WHERE l.tenant_id=%s::uuid AND l.local_entity_type=%s AND l.local_entity_id=%s::uuid
              ORDER BY l.created_at DESC LIMIT 50""",
              (str(metadata.get("generation") or ""), str(metadata.get("connection_instance_id") or ""),
               tenant, local_entity_type, local_id))
            rows = cur.fetchall()
    return [{"id": str(row["id"]), "external_type": row["external_entity_type"],
             "external_id": row["external_id"], "scope_id": row["external_scope_id"],
             "status": "confirmed" if row["current"] else "stale" if not row["revoked_at"] else "revoked",
             "basis": row["verification_basis"], "evidence": row["evidence_summary"],
             "verified_at": row["verified_at"].isoformat(), "verified_by": int(row["verified_by"]),
             "revoked_at": row["revoked_at"].isoformat() if row["revoked_at"] else None,
             "version": int(row["version"])} for row in rows]


def dialog_context(profile, *, channel_id, conversation_id):
    """Resolve only an exact, already authorized route and native contact map."""
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
        channel = str(uuid.UUID(str(channel_id)))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Диалог или канал недоступен.") from None
    conversation = str(conversation_id or "").strip()
    if not conversation or len(conversation) > 160:
        raise OperatorError("Диалог или канал недоступен.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            current = _current_profile(cur, tenant, actor)
            if not TenantPolicy(current).allows_action("operator_data", "view"):
                raise OperatorError("Недостаточно прав для данных Operator.")
            from tenant_operator_access import access, assert_route
            _, _, dialog_policy = access(cur, profile, "dialogs")
            route = assert_route(cur, tenant, dialog_policy, "connect",
                                 conversation=conversation, channel=channel)
            cur.execute("""SELECT c.id::text AS contact_id,c.company_id::text AS company_id
              FROM saas_crm_native_contact_conversations m
              JOIN saas_crm_native_contacts c ON c.tenant_id=m.tenant_id AND c.id=m.contact_id
              WHERE m.tenant_id=%s::uuid AND m.provider='connect'
                AND m.channel_id=%s::uuid AND m.conversation_id=%s AND c.archived_at IS NULL""",
              (tenant, str(route["connect_channel_id"]), str(route["conversation_id"])))
            contact = cur.fetchone()
            if not contact:
                return {"status": "crm_contact_unresolved", "account_links": [],
                        "profile_user_links": []}
            integration = _operator_row(cur, tenant)
            metadata = integration.get("metadata") if integration and isinstance(integration.get("metadata"), dict) else {}
            generation = str(metadata.get("generation") or "")
            instance = str(metadata.get("connection_instance_id") or "")
            local_targets = [("contact", str(contact["contact_id"]))]
            if contact.get("company_id"):
                local_targets.append(("company", str(contact["company_id"])))
            account_links = []
            profile_user_links = []
            for entity_type, entity_id in local_targets:
                cur.execute("""SELECT id,external_id,version FROM saas_beinsystems_operator_links
                  WHERE tenant_id=%s::uuid AND local_entity_type=%s AND local_entity_id=%s::uuid
                    AND external_entity_type='account' AND external_scope_id=''
                    AND revoked_at IS NULL AND connection_generation=%s
                    AND connection_instance_id=%s ORDER BY verified_at DESC LIMIT 20""",
                  (tenant, entity_type, entity_id, generation, instance))
                account_links.extend({"link_id": str(row["id"]), "account": row["external_id"],
                                      "version": int(row["version"]), "source_entity": entity_type,
                                      "source_entity_id": entity_id}
                                     for row in cur.fetchall())
                if entity_type == "contact":
                    cur.execute("""SELECT id,external_id,external_scope_id,version
                      FROM saas_beinsystems_operator_links
                      WHERE tenant_id=%s::uuid AND local_entity_type='contact' AND local_entity_id=%s::uuid
                        AND external_entity_type='profile_user' AND revoked_at IS NULL
                        AND connection_generation=%s AND connection_instance_id=%s
                      ORDER BY verified_at DESC LIMIT 20""",
                      (tenant, entity_id, generation, instance))
                    profile_user_links.extend({
                        "link_id": str(row["id"]), "login": row["external_id"],
                        "account": row["external_scope_id"], "version": int(row["version"]),
                    } for row in cur.fetchall())
    accounts = {item["account"] for item in account_links}
    state = "unlinked" if not accounts else "confirmed" if len(accounts) == 1 else "ambiguous"
    for item in profile_user_links:
        item["status"] = "confirmed" if item["account"] in accounts else "account_context_mismatch"
    return {"status": state, "contact_id": str(contact["contact_id"]),
            "account_links": account_links,
            "profile_user_links": profile_user_links}


def task_creation_contract():
    return {"enabled": False, "code": "operator_task_create_contract_unconfirmed",
            "missing": ["response_identifier", "idempotency", "timeout_reconciliation", "public_link"]}


def build_task_draft(title, description, account="", priority="normal"):
    """Prepare documented fields in memory; never send the request."""
    title, description = str(title or "").strip(), str(description or "").strip()
    priority, account = str(priority or "normal").strip().lower(), str(account or "").strip()
    if not title or len(title) > 240 or not description or len(description) > 8000:
        raise ValueError("invalid_task_draft")
    if priority not in {"low", "normal", "high"}:
        raise ValueError("invalid_task_priority")
    if account and (len(account) > 240 or any(ord(char) < 32 for char in account)):
        raise ValueError("invalid_task_account")
    payload = {"title": title, "description": description, "priority": priority}
    if account:
        payload["account"] = account
    return {"enabled": False, "payload": payload,
            "reason": "operator_task_create_contract_unconfirmed"}


def exact_linear_identifier(operator_task):
    """Use only Operator linear_id; never infer a match from a title."""
    if not isinstance(operator_task, dict):
        return ""
    value = str(operator_task.get("linear_id") or "").strip()
    return value if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*-[0-9]+", value) else ""
