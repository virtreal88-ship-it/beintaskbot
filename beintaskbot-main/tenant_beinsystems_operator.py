"""Private, read-only Operator connector and manually confirmed identity links.

Only API operations documented in API_DOCUMENTATION.md are transportable here.
Task creation deliberately has no live transport path until its response and
idempotency contract are confirmed.
"""
from __future__ import annotations

import json
import hashlib
import re
import requests
import sys
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.fernet import InvalidToken

from tenant_beinsystems_operator_access import (
    OPERATOR_API_BASE_URL, PROVIDER, api_base_url, is_operator_workspace,
)
from tenant_platform_core import (
    TenantPlatformError, _connect, _ensure_schema, _fernet, _json,
)
from tenant_policy import TenantPolicy

MAX_RESPONSE_BYTES = 1_000_000
MAX_AUTH_RESPONSE_BYTES = 64_000
REQUEST_TIMEOUT_SECONDS = 8
AUTH_LOGIN_URL = "https://auth.beinsystems.az/auth/login"
AUTH_CONTRACT_VERSION = "auth_login_uniq_device_id_v1"
AUTH_SUPPORTED_CONTRACT_VERSIONS = frozenset({
    AUTH_CONTRACT_VERSION,
    "auth_login_runtime_metadata_v1",
})
AUTH_UNIQ_DEVICE_ID = "beintaskbot-operator"
AUTH_APPLICATION_USER_AGENT = (
    f"Beintaskbot-Operator Python-urllib/{sys.version_info.major}.{sys.version_info.minor}"
)
READ_ENDPOINTS = {
    "me": ("/user/me", set()),
    "accounts": ("/user/accounts", {"search", "limit"}),
    "account_info": ("/user/info", {"account"}),
    "profile_users": ("/user/profileUsers", {"account"}),
    "task_list": ("/task/list", {"account"}),
}


_UPSTREAM_BEARER_ERROR_STATUS = {
    "invalid_token": 401,
    "insufficient_scope": 403,
}
_AUTH_PARAM_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def _bearer_challenge_error_code(headers, status):
    """Return only a recognized Bearer error code; never expose challenge text."""
    if status not in (401, 403) or headers is None:
        return None
    get_all = getattr(headers, "get_all", None)
    if callable(get_all):
        values = get_all("WWW-Authenticate") or []
    else:
        get_value = getattr(headers, "get", None)
        value = get_value("WWW-Authenticate") if callable(get_value) else None
        values = [value] if value is not None else []
    if isinstance(values, str):
        values = [values]

    for raw in values:
        if not isinstance(raw, str) or len(raw) > 2048:
            continue
        if any((ord(char) < 32 and char != "\t") or ord(char) == 127 for char in raw):
            continue
        scheme = re.match(r"(?i)^Bearer[ \t]+", raw)
        if not scheme:
            continue
        rest = raw[scheme.end():]
        position = 0
        error_value = None
        error_seen = False
        valid = True
        while position < len(rest):
            while position < len(rest) and rest[position] in " \t":
                position += 1
            key_match = _AUTH_PARAM_TOKEN.match(rest, position)
            if not key_match:
                valid = False
                break
            key = key_match.group(0)
            position = key_match.end()
            while position < len(rest) and rest[position] in " \t":
                position += 1
            if position >= len(rest) or rest[position] != "=":
                valid = False
                break
            position += 1
            while position < len(rest) and rest[position] in " \t":
                position += 1
            if position < len(rest) and rest[position] == '"':
                position += 1
                chars = []
                escaped = False
                closed = False
                while position < len(rest):
                    char = rest[position]
                    if char == '"':
                        position += 1
                        closed = True
                        break
                    if char == "\\":
                        position += 1
                        if position >= len(rest):
                            valid = False
                            break
                        chars.append(rest[position])
                        escaped = True
                        position += 1
                    else:
                        chars.append(char)
                        position += 1
                if not valid or not closed:
                    valid = False
                    break
                value = None if escaped else "".join(chars)
            else:
                value_match = _AUTH_PARAM_TOKEN.match(rest, position)
                if not value_match:
                    valid = False
                    break
                value = value_match.group(0)
                position = value_match.end()
            if key == "error":
                if error_seen:
                    valid = False
                    break
                error_seen = True
                error_value = value
            while position < len(rest) and rest[position] in " \t":
                position += 1
            if position == len(rest):
                break
            if rest[position] != ",":
                valid = False
                break
            position += 1
            if position == len(rest):
                valid = False
                break
        if valid and error_seen and _UPSTREAM_BEARER_ERROR_STATUS.get(error_value) == status:
            return error_value
    return None


class OperatorError(TenantPlatformError):
    def __init__(self, message, status=400, *, upstream_http_status=None,
                 upstream_error_code=None):
        super().__init__(message)
        self.status = status
        self.upstream_http_status = (
            upstream_http_status if upstream_http_status in (401, 403) else None
        )
        self.upstream_error_code = (
            upstream_error_code
            if (_UPSTREAM_BEARER_ERROR_STATUS.get(upstream_error_code)
                == self.upstream_http_status)
            else None
        )


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


def _pending_auth_state(cur, tenant, actor):
    """Read only timestamps for the current and legacy pending-auth stores."""
    cur.execute("""SELECT received_at,me_verified_at,accounts_access_verified_at
      FROM saas_beinsystems_operator_pending_auth_v2
      WHERE tenant_id=%s::uuid AND owner_telegram_id=%s AND activated_at IS NULL""",
      (tenant, actor))
    current = cur.fetchone()
    # Preserve visibility of legacy pending tokens without ever selecting their ciphertext.
    cur.execute("""SELECT received_at FROM saas_beinsystems_operator_pending_auth
      WHERE tenant_id=%s::uuid AND owner_telegram_id=%s""", (tenant, actor))
    legacy = cur.fetchone()
    return current, legacy


def _revoke_accounts_capability(cur, tenant):
    cur.execute("""DELETE FROM saas_beinsystems_operator_accounts_capabilities
      WHERE tenant_id=%s::uuid""", (tenant,))


def _lock_accounts_capability_scope(cur, tenant):
    """Serialize candidate replacement/revocation with one-account grants and writes."""
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (str(tenant) + ":operator-accounts-capability",))


def _public(row, *, pending_auth_received=False, pending_auth_received_at=None,
            pending_auth_me_verified=False, pending_auth_accounts_access_verified=False,
            legacy_pending_auth_received=False, accounts_only_ready=False):
    row = row or {}
    return {
        "provider": PROVIDER,
        "status": str(row.get("status") or "not_connected"),
        "connected": row.get("status") == "connected",
        "configured": row.get("status") in {"configured", "connected"},
        "connected_at": row.get("connected_at").isoformat() if row.get("connected_at") else None,
        "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
        "api_ready": bool(api_base_url()),
        "pending_auth_received": bool(pending_auth_received),
        # Existing pending rows may predate the current login contract. This status
        # projection does not inspect their encrypted envelope, so never infer provenance.
        "pending_auth_contract": None,
        "pending_auth_access_confirmed": bool(pending_auth_accounts_access_verified)
            if pending_auth_received else None,
        "pending_auth_me_verified": bool(pending_auth_me_verified),
        "pending_auth_accounts_access_verified": bool(pending_auth_accounts_access_verified),
        "legacy_pending_auth_received": bool(legacy_pending_auth_received),
        "accounts_only_ready": bool(accounts_only_ready),
        "pending_auth_received_at": (
            pending_auth_received_at.isoformat()
            if hasattr(pending_auth_received_at, "isoformat")
            else (str(pending_auth_received_at) if pending_auth_received_at else None)
        ),
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
            _, actor, current = _authorize(cur, profile, "view")
            row = _operator_row(cur, tenant)
            pending = legacy_pending = None
            accounts_only_ready = False
            if current.get("role") == "owner":
                pending, legacy_pending = _pending_auth_state(cur, tenant, actor)
                if pending:
                    cur.execute("""SELECT 1 FROM saas_beinsystems_operator_accounts_capabilities
                      WHERE tenant_id=%s::uuid AND owner_telegram_id=%s
                        AND candidate_received_at=%s AND expires_at>now()""",
                      (tenant, actor, pending.get("received_at")))
                    accounts_only_ready = cur.fetchone() is not None
    return _public(
        row,
        pending_auth_received=bool(pending),
        pending_auth_received_at=(pending or {}).get("received_at"),
        pending_auth_me_verified=bool((pending or {}).get("me_verified_at")),
        pending_auth_accounts_access_verified=bool((pending or {}).get("accounts_access_verified_at")),
        legacy_pending_auth_received=bool(legacy_pending),
        accounts_only_ready=accounts_only_ready,
    )


def save_token(profile, token, *, verified=False):
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Нет доступа.") from None
    if profile.get("role") != "owner" or profile.get("active") is False:
        raise OperatorError("Настроить Operator может только владелец BeinSystems.")
    if verified is not True:
        raise OperatorError(
            "Непроверенный токен хранится отдельно до подтверждения доступа Operator.", 409)
    if not api_base_url():
        raise OperatorError("Адрес Operator API не задан в серверной конфигурации.")
    clean = str(token or "").strip()
    if not clean or len(clean) > 8192 or any(ord(char) < 32 for char in clean):
        raise OperatorError("Проверьте служебный токен Operator.")
    instance_id = str(uuid.uuid4())
    generation = str(uuid.uuid4())
    encrypted = _fernet().encrypt(_json({
        "tenant_id": tenant, "provider": PROVIDER, "token": clean,
        "connection_instance_id": instance_id,
    }).encode("utf-8"))
    metadata = {"connection_instance_id": instance_id, "generation": generation}
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            _lock_accounts_capability_scope(cur, tenant)
            current = _current_profile(cur, tenant, actor)
            if current.get("role") != "owner":
                raise OperatorError("Настроить Operator может только владелец BeinSystems.")
            _revoke_accounts_capability(cur, tenant)
            cur.execute("""DELETE FROM saas_beinsystems_operator_pending_auth_v2
              WHERE tenant_id=%s::uuid AND owner_telegram_id=%s""", (tenant, actor))
            cur.execute(
                """INSERT INTO saas_tenant_integrations
                  (tenant_id,provider,status,account_domain,metadata,secrets,connected_at,updated_at)
                VALUES (%s::uuid,%s,'connected',%s,%s::jsonb,%s,now(),now())
                ON CONFLICT (tenant_id,provider) DO UPDATE SET
                  account_domain=EXCLUDED.account_domain,
                  metadata=EXCLUDED.metadata,secrets=EXCLUDED.secrets,
                  status=EXCLUDED.status,connected_at=EXCLUDED.connected_at,updated_at=now()
                RETURNING status,metadata,connected_at,updated_at""",
                (tenant, PROVIDER, api_base_url(), _json(metadata), encrypted),
            )
            row = cur.fetchone()
            cur.execute("""DELETE FROM saas_beinsystems_operator_pending_auth
              WHERE tenant_id=%s::uuid AND owner_telegram_id=%s""", (tenant, actor))
        conn.commit()
    return _public(row)


def stage_auth_token(profile, token):
    """Retain legacy pending tokens separately from the current auth contract."""
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Нет доступа.") from None
    if profile.get("role") != "owner" or profile.get("active") is False:
        raise OperatorError("Подключить Operator может только владелец BeinSystems.")
    clean = str(token or "").strip()
    if not clean or len(clean) > 8192 or any(ord(char) < 32 for char in clean):
        raise OperatorError("Проверьте служебный токен Operator.")
    encrypted = _fernet().encrypt(_json({
        "tenant_id": tenant, "provider": PROVIDER,
        "owner_telegram_id": actor, "purpose": "pending_auth_verification",
        "token": clean,
    }).encode("utf-8"))
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            current = _current_profile(cur, tenant, actor)
            if current.get("role") != "owner":
                raise OperatorError("Подключить Operator может только владелец BeinSystems.")
            cur.execute("""INSERT INTO saas_beinsystems_operator_pending_auth
              (tenant_id,owner_telegram_id,token_ciphertext,received_at)
              VALUES(%s::uuid,%s,%s,now())
              ON CONFLICT(tenant_id,owner_telegram_id) DO UPDATE SET
                token_ciphertext=EXCLUDED.token_ciphertext,received_at=now()
              RETURNING received_at""", (tenant, actor, encrypted))
            pending = cur.fetchone() or {}
            row = _operator_row(cur, tenant)
        conn.commit()
    return _public(row, legacy_pending_auth_received=True)


def _authenticate_operator(login, password, *, opener=None):
    """Exchange credentials for a pending token using the confirmed auth contract."""
    username = str(login or "").strip()
    secret = str(password or "")
    login_parts = username.split("@")
    if (not username or len(username) > 240 or len(login_parts) != 2
            or any(not part.strip() for part in login_parts)
            or any(ord(char) < 32 for char in username)
            or not secret or len(secret) > 512):
        raise OperatorError("Укажите логин в формате user@account и пароль Operator.")

    payload = {
        "login": username,
        "password": secret,
        "uniqDeviceId": AUTH_UNIQ_DEVICE_ID,
    }
    request = Request(
        AUTH_LOGIN_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": AUTH_APPLICATION_USER_AGENT,
        },
    )
    transport = opener or build_opener(_NoRedirect())
    try:
        response = transport.open(request, timeout=REQUEST_TIMEOUT_SECONDS)
        with response:
            raw = response.read(MAX_AUTH_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        if exc.code in (400, 401, 403):
            raise OperatorError(f"Operator отклонил вход (HTTP {exc.code}).", 401) from None
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
    if not isinstance(result, dict):
        raise OperatorError("Сервис авторизации Operator вернул неизвестный ответ.", 502)
    if result.get("success") is False:
        # Do not return arbitrary provider text or any token-like field to the UI.
        raise OperatorError("Operator отклонил вход.", 401)
    if result.get("success") is not True:
        raise OperatorError("Сервис авторизации Operator вернул неизвестный ответ.", 502)
    data = result.get("data")
    token = data.get("token") if isinstance(data, dict) else None
    if (not isinstance(token, str) or not token.strip()
            or len(token) > 8192 or any(ord(char) < 32 for char in token)):
        raise OperatorError("Operator не выдал токен.", 502)
    return token.strip()


def stage_confirmed_auth_token(profile, token):
    """Encrypt this confirmed auth-contract token separately from legacy pending tokens."""
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Нет доступа.") from None
    if profile.get("role") != "owner" or profile.get("active") is False:
        raise OperatorError("Подключить Operator может только владелец BeinSystems.")
    clean = str(token or "").strip()
    if not clean or len(clean) > 8192 or any(ord(char) < 32 for char in clean):
        raise OperatorError("Проверьте служебный токен Operator.")
    encrypted = _fernet().encrypt(_json({
        "tenant_id": tenant, "provider": PROVIDER,
        "owner_telegram_id": actor, "purpose": "pending_auth_verification",
        "auth_contract": AUTH_CONTRACT_VERSION, "token": clean,
    }).encode("utf-8"))
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            _lock_accounts_capability_scope(cur, tenant)
            current = _current_profile(cur, tenant, actor)
            if current.get("role") != "owner":
                raise OperatorError("Подключить Operator может только владелец BeinSystems.")
            _revoke_accounts_capability(cur, tenant)
            cur.execute("""DELETE FROM saas_beinsystems_operator_pending_auth_v2
              WHERE tenant_id=%s::uuid AND owner_telegram_id=%s""", (tenant, actor))
            cur.execute("""INSERT INTO saas_beinsystems_operator_pending_auth_v2
              (tenant_id,owner_telegram_id,token_ciphertext,received_at,me_verified_at,
               accounts_access_verified_at,verified_identifier,verified_role,activated_at)
              VALUES(%s::uuid,%s,%s,now(),NULL,NULL,NULL,NULL,NULL)
              ON CONFLICT(tenant_id,owner_telegram_id) DO UPDATE SET
                token_ciphertext=EXCLUDED.token_ciphertext,received_at=now(),
                me_verified_at=NULL,accounts_access_verified_at=NULL,
                verified_identifier=NULL,verified_role=NULL,activated_at=NULL
              RETURNING received_at""", (tenant, actor, encrypted))
            pending = cur.fetchone() or {}
            _, legacy_pending = _pending_auth_state(cur, tenant, actor)
            row = _operator_row(cur, tenant)
        conn.commit()
    return _public(row, pending_auth_received=True,
                   pending_auth_received_at=pending.get("received_at"),
                   legacy_pending_auth_received=bool(legacy_pending))


def connect_with_password(profile, login, password):
    """Receive and encrypt an Operator JWT without activating an unverified token."""
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Нет доступа.") from None
    if profile.get("role") != "owner" or profile.get("active") is False:
        raise OperatorError("Подключить Operator может только владелец BeinSystems.")

    # Rate-limit attempts per owner and minute. Credentials are never recorded.
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

    token = _authenticate_operator(login, password)
    # No API call is made with the new token until Operator documents a
    # read-only access check. The active connection remains untouched.
    return stage_confirmed_auth_token(profile, token)


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
            _lock_accounts_capability_scope(cur, tenant)
            current = _current_profile(cur, tenant, actor)
            if current.get("role") != "owner":
                raise OperatorError("Отключить Operator может только владелец BeinSystems.")
            _revoke_accounts_capability(cur, tenant)
            cur.execute("""DELETE FROM saas_beinsystems_operator_pending_auth_v2
              WHERE tenant_id=%s::uuid AND owner_telegram_id=%s""", (tenant, actor))
            cur.execute("""DELETE FROM saas_beinsystems_operator_pending_auth
              WHERE tenant_id=%s::uuid AND owner_telegram_id=%s""", (tenant, actor))
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


def _operator_upstream_auth_error(status, headers):
    challenge_code = _bearer_challenge_error_code(headers, status)
    if challenge_code == "invalid_token":
        message = "Upstream Bearer challenge invalid_token (HTTP 401)."
    elif challenge_code == "insufficient_scope":
        message = ("Upstream Bearer challenge insufficient_scope "
                   "(HTTP 403; доступ к чтению не подтверждён).")
    else:
        message = (
            f"Operator отклонил служебную авторизацию (upstream HTTP {status}).")
    # The provider body and all unrecognized header text remain private.
    return OperatorError(
        message, 502, upstream_http_status=status,
        upstream_error_code=challenge_code,
    )


class _NoRedirectRequestsSession(requests.Session):
    """Prevent Requests from prefetching response bodies while resolving redirects."""

    def get_redirect_target(self, response):
        return None


def _request_accounts_with_requests(url, payload, token):
    """Use the confirmed requests transport for the documented accounts read."""
    try:
        with _NoRedirectRequestsSession() as session:
            # Keep the verified direct TLS path used by the successful control.
            session.trust_env = False
            with session.post(
                url,
                json=payload,
                headers={"Authorization": "Bearer " + str(token)},
                timeout=REQUEST_TIMEOUT_SECONDS,
                allow_redirects=False,
                stream=True,
                verify=True,
            ) as response:
                status = int(response.status_code)
                if status in (401, 403):
                    raise _operator_upstream_auth_error(status, response.headers)
                if not 200 <= status < 300:
                    raise OperatorError("Operator временно не ответил.")
                raw = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise OperatorError("Ответ Operator превышает допустимый размер.")
                    raw.extend(chunk)
                return bytes(raw)
    except OperatorError:
        raise
    except requests.RequestException:
        raise OperatorError("Operator временно не ответил.") from None


def _request_me_with_requests(url, token):
    """Read the independently authenticated identity without redirects or proxies."""
    try:
        with _NoRedirectRequestsSession() as session:
            session.trust_env = False
            with session.get(
                url, headers={"Authorization": "Bearer " + str(token)},
                timeout=REQUEST_TIMEOUT_SECONDS, allow_redirects=False,
                stream=True, verify=True,
            ) as response:
                status = int(response.status_code)
                if status in (401, 403):
                    raise _operator_upstream_auth_error(status, response.headers)
                if not 200 <= status < 300:
                    raise OperatorError("Operator временно не ответил.")
                raw = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise OperatorError("Ответ Operator превышает допустимый размер.")
                    raw.extend(chunk)
                return bytes(raw)
    except OperatorError:
        raise
    except requests.RequestException:
        raise OperatorError("Operator временно не ответил.") from None


def _request_operator(endpoint, payload, *, token, base_url=None, opener=None,
                      requests_me=False):
    """Call one allowlisted documented read method; never follows redirects."""
    if endpoint not in READ_ENDPOINTS:
        raise OperatorError("Операция Operator пока не поддерживается.")
    if requests_me and (endpoint != "me" or opener is not None):
        raise OperatorError("Операция Operator пока не поддерживается.")
    path, fields = READ_ENDPOINTS[endpoint]
    if not isinstance(payload, dict) or set(payload) - fields:
        raise OperatorError("Параметры запроса Operator некорректны.")
    if endpoint == "accounts":
        search_value = payload.get("search", "")
        if not isinstance(search_value, str):
            raise OperatorError("Поисковый запрос Operator некорректен.")
        search = search_value.strip()
        if search and len(search) < 4:
            raise OperatorError("Поиск Operator требует не менее четырёх символов.")
        if len(search) > 120:
            raise OperatorError("Слишком длинный поисковый запрос.")
        has_limit = "limit" in payload
        limit = payload.get("limit")
        if has_limit and (type(limit) is not int or not 1 <= limit <= 100):
            raise OperatorError("Размер списка Operator должен быть от 1 до 100.")
        payload = {}
        if search:
            payload["search"] = search
        if has_limit:
            payload["limit"] = limit
    if "account" in payload:
        account = str(payload.get("account") or "").strip()
        if not account or len(account) > 240 or any(ord(char) < 32 for char in account):
            raise OperatorError("Идентификатор аккаунта Operator некорректен.")
        payload = {"account": account}
    approved_base = str(api_base_url() or "").rstrip("/")
    base = str(base_url or approved_base).rstrip("/")
    if not approved_base or base != approved_base or base != OPERATOR_API_BASE_URL:
        raise OperatorError("Адрес Operator API не настроен.")
    if endpoint == "accounts":
        raw = _request_accounts_with_requests(base + path, payload, token)
    elif endpoint == "me" and requests_me:
        raw = _request_me_with_requests(base + path, token)
    else:
        method = "GET" if endpoint == "me" else "POST"
        body = None if method == "GET" else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(base + path, data=body, method=method, headers={
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
                raise _operator_upstream_auth_error(
                    code, getattr(exc, "headers", None)) from None
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


def operator_me_payload(response):
    """Project identity from the documented /user/me response, without granting app rights."""
    if (not isinstance(response, dict)
            or str(response.get("status") or "").strip().casefold() != "success"
            or not isinstance(response.get("data"), dict)):
        raise OperatorError("Operator не подтвердил доступ к профилю пользователя.", 403)
    data = response["data"]
    def clean(value):
        if not isinstance(value, str):
            raise OperatorError("Operator вернул некорректный профиль пользователя.")
        value = value.strip()
        if not value or len(value) > 240 or any(ord(char) < 32 for char in value):
            raise OperatorError("Operator вернул некорректный профиль пользователя.")
        return value
    return {"identifier": clean(data.get("identifier")), "role": clean(data.get("role"))}


def accounts_import_payload(response):
    """Validate the entire documented list before an atomic local import."""
    if (not isinstance(response, dict)
            or str(response.get("status") or "").strip().casefold() != "success"
            or not isinstance(response.get("data"), list)):
        raise OperatorError("Operator не вернул полный список доступных аккаунтов.")
    values = response["data"]
    if len(values) > 10000:
        raise OperatorError("Список аккаунтов превышает безопасный предел импорта.")
    result, seen = [], set()
    for item in values:
        if not isinstance(item, dict):
            raise OperatorError("Список аккаунтов Operator содержит некорректную запись.")
        account = item.get("account")
        if not isinstance(account, str):
            raise OperatorError("Список аккаунтов Operator содержит некорректный идентификатор.")
        account = account.strip()
        if not account or len(account) > 240 or any(ord(char) < 32 for char in account):
            raise OperatorError("Список аккаунтов Operator содержит некорректный идентификатор.")
        if account not in seen:
            seen.add(account)
            result.append({"account": account})
    return result


def _pending_auth_candidate(profile, *, required=True):
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Нет доступа.") from None
    if profile.get("role") != "owner" or profile.get("active") is False:
        raise OperatorError("Проверить Operator может только владелец BeinSystems.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            current = _current_profile(cur, tenant, actor)
            if current.get("role") != "owner":
                raise OperatorError("Проверить Operator может только владелец BeinSystems.")
            cur.execute("""SELECT token_ciphertext,received_at
              FROM saas_beinsystems_operator_pending_auth_v2
              WHERE tenant_id=%s::uuid AND owner_telegram_id=%s AND activated_at IS NULL""",
              (tenant, actor))
            row = cur.fetchone()
    if not row:
        if required:
            raise OperatorError("Сначала получите JWT из карточки интеграции.")
        return None
    try:
        envelope = json.loads(_fernet().decrypt(bytes(row["token_ciphertext"])).decode("utf-8"))
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise OperatorError("Зашифрованный кандидат Operator не удалось прочитать.") from None
    if (not isinstance(envelope, dict) or envelope.get("tenant_id") != tenant
            or envelope.get("provider") != PROVIDER
            or envelope.get("owner_telegram_id") != actor
            or envelope.get("purpose") != "pending_auth_verification"
            or envelope.get("auth_contract") not in AUTH_SUPPORTED_CONTRACT_VERSIONS
            or not isinstance(envelope.get("token"), str) or not envelope.get("token")):
        raise OperatorError("Кандидат Operator не относится к этому владельцу и workspace.")
    return {"token": envelope["token"], "received_at": row["received_at"],
            "base_url": api_base_url()}


def _record_operator_read(profile, endpoint, payload):
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Нет доступа.") from None
    limits = {"me": 12, "accounts": 12, "account_info": 30, "profile_users": 20, "task_list": 20}
    if endpoint not in limits:
        raise OperatorError("Операция Operator пока не поддерживается.")
    account = str((payload or {}).get("account") or "").strip()
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            _, actor, _ = _authorize(cur, profile, "view")
            cur.execute("""INSERT INTO saas_beinsystems_operator_rate_limits
              (tenant_id,actor_telegram_id,endpoint,bucket_start,request_count)
              VALUES(%s::uuid,%s,%s,date_trunc('minute',now()),1)
              ON CONFLICT(tenant_id,actor_telegram_id,endpoint,bucket_start)
              DO UPDATE SET request_count=saas_beinsystems_operator_rate_limits.request_count+1,
                updated_at=now() RETURNING request_count""", (tenant, actor, endpoint))
            if int(cur.fetchone()["request_count"]) > limits[endpoint]:
                raise OperatorError("Слишком много запросов Operator. Повторите позже.", 429)
            cur.execute("""INSERT INTO saas_tenant_audit_events
              (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
              VALUES(%s,%s::uuid,%s,'operator_read_requested','operator_endpoint',%s,%s::jsonb)""",
              (str(uuid.uuid4()), tenant, actor, endpoint,
               _json({"account_hash": hashlib.sha256(account.encode("utf-8")).hexdigest() if account else ""})))
        conn.commit()


def _assert_current_operator_owner(tenant, actor, *, pending_received_at=None):
    """Recheck the live workspace owner immediately around the external read."""
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            current = _current_profile(cur, tenant, actor)
            if current.get("role") != "owner":
                raise OperatorError(
                    "Проверить доступ к accounts может только владелец BeinSystems.")
            if (pending_received_at is not None
                    and not _pending_timestamp_is_current(
                        cur, tenant, actor, pending_received_at)):
                raise OperatorError(
                    "JWT Operator был заменён во время проверки. Повторите попытку.", 409)


def _operator_directory_fence(profile, tenant, actor, source, credentials):
    """Recheck owner and the exact selected credentials around each provider read."""
    _assert_current_operator_owner(tenant, actor)
    if source == "active":
        fresh = _credentials(tenant, allow_configured=True)
        if (fresh["generation"] != credentials["generation"]
                or fresh["connection_instance_id"] != credentials["connection_instance_id"]):
            raise OperatorError("Подключение Operator изменилось во время поиска.", 409)
        return
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            row = _operator_row(cur, tenant)
            if row and row.get("status") in {"connected", "configured"} and row.get("secrets"):
                raise OperatorError("Подключение Operator изменилось во время поиска.", 409)
            if not _pending_timestamp_is_current(
                    cur, tenant, actor, credentials["received_at"]):
                raise OperatorError("JWT Operator был заменён во время поиска.", 409)


def search_authenticated_operator_account(profile, search):
    """Verify identity with /me, then return only one account ID from a narrow search."""
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Нет доступа.") from None
    if profile.get("role") != "owner" or profile.get("active") is False:
        raise OperatorError("Поиск Operator доступен только владельцу BeinSystems.")
    if not isinstance(search, str):
        raise OperatorError("Укажите строку поиска аккаунта Operator.")
    if any(ord(char) < 32 or ord(char) == 127 for char in search):
        raise OperatorError("Поиск Operator должен содержать от 4 до 120 символов без управляющих знаков.")
    query = search.strip()
    if len(query) < 4 or len(query) > 120:
        raise OperatorError("Поиск Operator должен содержать от 4 до 120 символов без управляющих знаков.")

    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            current = _current_profile(cur, tenant, actor)
            if current.get("role") != "owner":
                raise OperatorError("Поиск Operator доступен только владельцу BeinSystems.")
            active_row = _operator_row(cur, tenant)
    active_exists = bool(active_row and active_row.get("status") in {"connected", "configured"}
                         and active_row.get("secrets"))
    if active_exists:
        credentials = _credentials(tenant, allow_configured=True)
        source = "active"
    else:
        candidate = _pending_auth_candidate(profile, required=False)
        if not candidate:
            raise OperatorError("Сначала получите JWT из карточки интеграции.")
        credentials = candidate
        source = "pending"

    _record_operator_read(profile, "me", {})
    _operator_directory_fence(profile, tenant, actor, source, credentials)
    identity = operator_me_payload(_request_operator(
        "me", {}, token=credentials["token"], base_url=credentials["base_url"],
        requests_me=True))
    # Identity confirms this credential only. It is deliberately neither returned
    # nor used for CRM permissions; the live CRM owner check remains authoritative.
    del identity
    _operator_directory_fence(profile, tenant, actor, source, credentials)

    _record_operator_read(profile, "accounts", {"search": query})
    _operator_directory_fence(profile, tenant, actor, source, credentials)
    response = _request_operator("accounts", {"search": query, "limit": 1},
                                 token=credentials["token"], base_url=credentials["base_url"])
    accounts = _accounts_access_probe_payload(response)
    _operator_directory_fence(profile, tenant, actor, source, credentials)
    return {"source": source, "account_id": accounts[0] if accounts else None,
            "result_count": len(accounts),
            "exact_match": bool(accounts and accounts[0].casefold() == query.casefold())}


def verify_accounts_access(profile, search):
    """Check a bounded accounts read without activating pending credentials."""
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Нет доступа.") from None
    if profile.get("role") != "owner" or profile.get("active") is False:
        raise OperatorError("Проверить доступ к accounts может только владелец BeinSystems.")
    if not isinstance(search, str):
        raise OperatorError("Укажите строку поиска аккаунта Operator.")
    query = search.strip()
    if len(query) < 4:
        raise OperatorError("Поиск Operator требует не менее четырёх символов.")
    if len(query) > 120:
        raise OperatorError("Слишком длинный поисковый запрос.")

    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            current = _current_profile(cur, tenant, actor)
            if current.get("role") != "owner":
                raise OperatorError("Проверить доступ к accounts может только владелец BeinSystems.")
            row = _operator_row(cur, tenant)

    candidate = _pending_auth_candidate(profile, required=False)
    if candidate:
        credentials = candidate
        source = "pending"
    elif row and row.get("status") in {"connected", "configured"} and row.get("secrets"):
        credentials = _credentials(tenant, allow_configured=True)
        source = "active"
    else:
        raise OperatorError("Сначала получите JWT из карточки интеграции.")

    _record_operator_read(profile, "accounts", {"search": query})
    if source == "active":
        fresh = _credentials(tenant, allow_configured=True)
        if (fresh["generation"] != credentials["generation"]
                or fresh["connection_instance_id"] != credentials["connection_instance_id"]):
            raise OperatorError("Подключение Operator изменилось до проверки.", 409)
        # The audit path checks operator_data:view; this separate live check
        # ensures the caller is still the workspace owner immediately preflight.
        _assert_current_operator_owner(tenant, actor)
    else:
        _assert_current_operator_owner(
            tenant, actor, pending_received_at=candidate["received_at"])

    response = _request_operator(
        "accounts", {"search": query, "limit": 1},
        token=credentials["token"], base_url=credentials["base_url"])
    accounts = _accounts_access_probe_payload(response)
    account_read = bool(accounts)
    match_found = bool(
        accounts and accounts[0].casefold() == query.casefold())

    if source == "active":
        fresh = _credentials(tenant, allow_configured=True)
        if (fresh["generation"] != credentials["generation"]
                or fresh["connection_instance_id"] != credentials["connection_instance_id"]):
            raise OperatorError("Подключение Operator изменилось во время запроса.", 409)
        _assert_current_operator_owner(tenant, actor)
    else:
        with _connect() as conn:
            _ensure_schema(conn)
            with conn.cursor() as cur:
                current = _current_profile(cur, tenant, actor)
                if current.get("role") != "owner":
                    raise OperatorError(
                        "Проверить доступ к accounts может только владелец BeinSystems.")
                if not _pending_timestamp_is_current(
                        cur, tenant, actor, candidate["received_at"]):
                    raise OperatorError(
                        "JWT Operator был заменён во время проверки. Повторите попытку.", 409)
                if account_read:
                    cur.execute("""UPDATE saas_beinsystems_operator_pending_auth_v2
                      SET accounts_access_verified_at=now()
                      WHERE tenant_id=%s::uuid AND owner_telegram_id=%s AND received_at=%s
                        AND activated_at IS NULL""",
                      (tenant, actor, candidate["received_at"]))
                    if cur.rowcount != 1:
                        raise OperatorError(
                            "JWT Operator был заменён во время проверки. Повторите попытку.", 409)
            if account_read:
                conn.commit()
        _assert_current_operator_owner(
            tenant, actor, pending_received_at=candidate["received_at"])

    return {
        "source": source,
        "request_accepted": True,
        "access_confirmed": account_read,
        "account_read": account_read,
        "result_count": len(accounts),
        "match_found": match_found,
    }

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
            limits = {"me": 12, "accounts": 12, "account_info": 30,
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


def _upsert_imported_accounts(cur, tenant, accounts):
    cur.executemany("""INSERT INTO saas_beinsystems_operator_account_catalog
      (tenant_id,external_account,first_seen_at,last_seen_at)
      VALUES(%s::uuid,%s,now(),now())
      ON CONFLICT(tenant_id,external_account) DO UPDATE SET last_seen_at=now()""",
      [(tenant, item["account"]) for item in accounts])


def _pending_timestamp_is_current(cur, tenant, actor, received_at):
    cur.execute("""SELECT received_at FROM saas_beinsystems_operator_pending_auth_v2
      WHERE tenant_id=%s::uuid AND owner_telegram_id=%s AND activated_at IS NULL FOR UPDATE""",
      (tenant, actor))
    row = cur.fetchone()
    return bool(row and row.get("received_at") == received_at)


def _activate_candidate_if_unconfigured(cur, tenant, actor, token):
    row = _operator_row(cur, tenant, lock=True)
    if row and row.get("status") in {"connected", "configured"} and row.get("secrets"):
        return False
    instance_id, generation = str(uuid.uuid4()), str(uuid.uuid4())
    encrypted = _fernet().encrypt(_json({
        "tenant_id": tenant, "provider": PROVIDER, "token": token,
        "connection_instance_id": instance_id,
    }).encode("utf-8"))
    metadata = {"connection_instance_id": instance_id, "generation": generation}
    cur.execute("""INSERT INTO saas_tenant_integrations
      (tenant_id,provider,status,account_domain,metadata,secrets,connected_at,updated_at)
      VALUES(%s::uuid,%s,'connected',%s,%s::jsonb,%s,now(),now())
      ON CONFLICT(tenant_id,provider) DO UPDATE SET
        account_domain=EXCLUDED.account_domain,metadata=EXCLUDED.metadata,
        secrets=EXCLUDED.secrets,status='connected',connected_at=now(),updated_at=now()
      WHERE saas_tenant_integrations.status NOT IN ('connected','configured')
        OR saas_tenant_integrations.secrets IS NULL
      RETURNING status""",
      (tenant, PROVIDER, api_base_url(), _json(metadata), encrypted))
    activated = cur.fetchone() is not None
    if activated:
        _revoke_accounts_capability(cur, tenant)
        cur.execute("""UPDATE saas_beinsystems_operator_pending_auth_v2
          SET activated_at=now(),accounts_access_verified_at=now()
          WHERE tenant_id=%s::uuid AND owner_telegram_id=%s""", (tenant, actor))
    return activated


def import_accounts(profile):
    """Import the complete documented account result in one local transaction."""
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Нет доступа.") from None
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            current = _current_profile(cur, tenant, actor)
            if current.get("role") != "owner" or profile.get("role") != "owner":
                raise OperatorError("Импортировать аккаунты Operator может только владелец.")
            active_row = _operator_row(cur, tenant)
    active = bool(active_row and active_row.get("status") in {"connected", "configured"}
                  and active_row.get("secrets"))
    candidate = _pending_auth_candidate(profile, required=False)
    if candidate:
        active = False
        credentials = candidate
    elif active:
        credentials = _credentials(tenant, allow_configured=True)
    else:
        raise OperatorError("Сначала получите JWT из карточки интеграции.")
    _record_operator_read(profile, "me", {})
    identity = operator_me_payload(_request_operator(
        "me", {}, token=credentials["token"], base_url=credentials["base_url"]))
    _record_operator_read(profile, "accounts", {})
    response = _request_operator(
        "accounts", {}, token=credentials["token"], base_url=credentials["base_url"])
    accounts = accounts_import_payload(response)
    if active:
        fresh = _credentials(tenant, allow_configured=True)
        if (fresh["generation"] != credentials["generation"]
                or fresh["connection_instance_id"] != credentials["connection_instance_id"]):
            raise OperatorError("Подключение Operator изменилось во время импорта.", 409)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            if candidate:
                _lock_accounts_capability_scope(cur, tenant)
            current = _current_profile(cur, tenant, actor)
            if current.get("role") != "owner":
                raise OperatorError("Импортировать аккаунты Operator может только владелец.")
            if candidate and not _pending_timestamp_is_current(
                    cur, tenant, actor, candidate["received_at"]):
                raise OperatorError("JWT Operator был заменён во время импорта. Повторите попытку.", 409)
            _upsert_imported_accounts(cur, tenant, accounts)
            cur.execute("""INSERT INTO saas_tenant_audit_events
              (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
              VALUES(%s,%s::uuid,%s,'operator_accounts_imported','operator_account_catalog','all',%s::jsonb)""",
              (str(uuid.uuid4()), tenant, actor, _json({"imported_count": len(accounts)})))
            activated = False
            if candidate:
                _revoke_accounts_capability(cur, tenant)
                cur.execute("""UPDATE saas_beinsystems_operator_pending_auth_v2
                  SET me_verified_at=COALESCE(me_verified_at,now()),accounts_access_verified_at=now(),
                      verified_identifier=%s,verified_role=%s
                  WHERE tenant_id=%s::uuid AND owner_telegram_id=%s AND received_at=%s
                    AND activated_at IS NULL""",
                  (identity["identifier"], identity["role"], tenant, actor, candidate["received_at"]))
                if cur.rowcount != 1:
                    raise OperatorError("JWT Operator был заменён во время импорта. Повторите попытку.", 409)
                activated = _activate_candidate_if_unconfigured(cur, tenant, actor, credentials["token"])
        conn.commit()
    return {"imported": len(accounts), "activated": activated,
            "identity": identity, "status": status(profile)}


def imported_accounts(profile, *, search="", limit=100):
    tenant = _tenant(profile)
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 100
    term = str(search or "").strip()[:120]
    if any(ord(char) < 32 for char in term):
        raise OperatorError("Поисковый запрос некорректен.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            _, actor, _ = _authorize(cur, profile, "view")
            cur.execute("""SELECT external_account AS account
              FROM saas_beinsystems_operator_account_catalog
              WHERE tenant_id=%s::uuid AND position(lower(%s) in lower(external_account))>0
              ORDER BY external_account LIMIT %s""",
              (tenant, term, limit))
            rows = cur.fetchall()
    return [{"account": row["account"]} for row in rows]


def _dialog_company_route(cur, profile, channel, conversation, action):
    from tenant_operator_access import access, assert_route
    tenant, actor, policy = access(cur, profile, "dialogs", action)
    route = assert_route(cur, tenant, policy, "connect", conversation=conversation,
                         channel=channel, action=action)
    return tenant, actor, route


def _dialog_company_external_account(cur, tenant, company_id, integration):
    """Return one current verified Operator account for a visible CRM company."""
    if (not isinstance(integration, dict)
            or integration.get("status") not in {"connected", "configured"}
            or not integration.get("secrets")):
        return None
    generation, instance = _link_connection_scope(integration)
    if not generation or not instance:
        return None
    cur.execute("""SELECT account FROM (
        SELECT external_account AS account
          FROM saas_beinsystems_operator_company_imports
          WHERE tenant_id=%s::uuid AND company_id=%s::uuid
            AND last_seen_generation=%s
            AND last_seen_connection_instance_id=%s::uuid
        UNION
        SELECT external_id AS account
          FROM saas_beinsystems_operator_links
          WHERE tenant_id=%s::uuid AND local_entity_type='company'
            AND local_entity_id=%s::uuid AND external_entity_type='account'
            AND external_scope_id='' AND connection_generation=%s
            AND connection_instance_id=%s::uuid AND revoked_at IS NULL
      ) AS verified_accounts
      ORDER BY account LIMIT 2""",
      (tenant, company_id, generation, instance,
       tenant, company_id, generation, instance))
    rows = cur.fetchall()
    # An absent or ambiguous current association is not safe to send onward.
    if len(rows) != 1:
        return None
    account = rows[0].get("account")
    return account if isinstance(account, str) and account else None


def dialog_company_binding(profile, *, channel_id, conversation_id):
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
        channel = str(uuid.UUID(str(channel_id)))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Диалог или канал недоступен.") from None
    conversation = str(conversation_id or "").strip()
    if not conversation or len(conversation) > 160:
        raise OperatorError("Диалог или канал недоступен.")
    from tenant_crm_native_access import current_access, locked_resource
    from tenant_linear_policy import TenantLinearError
    try:
        with _connect() as conn:
            _ensure_schema(conn)
            with conn.cursor() as cur:
                _, actor, route = _dialog_company_route(cur, profile, channel, conversation, "view")
                current = _current_profile(cur, tenant, actor)
                can_manage = current.get("role") == "owner"
                can_view_operator = TenantPolicy(current).allows_action("operator_data", "view")
                company_access = current_access(cur, tenant, actor, "companies", "view")
                cur.execute("""SELECT b.company_id::text AS company_id,c.name
                  FROM saas_beinsystems_operator_dialog_company_bindings b
                  JOIN saas_crm_native_companies c ON c.tenant_id=b.tenant_id
                    AND c.id=b.company_id AND c.archived_at IS NULL
                  WHERE b.tenant_id=%s::uuid AND b.channel_id=%s::uuid
                    AND b.conversation_id=%s""",
                  (tenant, route["connect_channel_id"], route["conversation_id"]))
                row = cur.fetchone()
                if not row:
                    return {"company_id": None, "company_name": None,
                            "external_account": None, "can_manage": can_manage}
                allowed = locked_resource(cur, company_access, "companies", "view",
                    "saas_crm_native_companies", tenant, row["company_id"])
                if not allowed:
                    return {"company_id": None, "company_name": None,
                            "external_account": None, "can_manage": can_manage}
                external_account = None
                if can_view_operator:
                    external_account = _dialog_company_external_account(
                        cur, tenant, row["company_id"], _operator_row(cur, tenant)
                    )
                return {"company_id": row["company_id"], "company_name": row["name"],
                        "external_account": external_account, "can_manage": can_manage}
    except TenantLinearError as error:
        raise OperatorError("Нет доступа к диалогу или компании.", getattr(error, "status", 403)) from error
    except ValueError as error:
        raise OperatorError("Нет доступа к диалогу или компании.", 403) from error


def set_dialog_company_binding(profile, *, channel_id, conversation_id, company_id=None):
    tenant = _tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
        channel = str(uuid.UUID(str(channel_id)))
    except (TypeError, ValueError, AttributeError):
        raise OperatorError("Диалог или канал недоступен.") from None
    conversation = str(conversation_id or "").strip()
    if not conversation or len(conversation) > 160:
        raise OperatorError("Диалог или канал недоступен.")
    from tenant_crm_native_access import current_access, locked_resource
    from tenant_linear_policy import TenantLinearError
    try:
        with _connect() as conn:
            _ensure_schema(conn)
            with conn.cursor() as cur:
                _, actor, route = _dialog_company_route(cur, profile, channel, conversation, "update")
                _operator_owner(profile, cur)
                if company_id:
                    company = str(uuid.UUID(str(company_id)))
                    read_profile = current_access(cur, tenant, actor, "companies", "view")
                    update_profile = current_access(cur, tenant, actor, "companies", "update")
                    visible = locked_resource(cur, read_profile, "companies", "view",
                        "saas_crm_native_companies", tenant, company)
                    editable = locked_resource(cur, update_profile, "companies", "update",
                        "saas_crm_native_companies", tenant, company)
                    if not visible or not editable:
                        raise OperatorError("CRM-компания недоступна по вашим правам.", 403)
                    cur.execute("""INSERT INTO saas_beinsystems_operator_dialog_company_bindings
                      (tenant_id,channel_id,conversation_id,company_id,created_by,updated_by)
                      VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s,%s)
                      ON CONFLICT(tenant_id,channel_id,conversation_id) DO UPDATE SET
                        company_id=EXCLUDED.company_id,updated_by=EXCLUDED.updated_by,updated_at=now()""",
                      (tenant, route["connect_channel_id"], route["conversation_id"], company, actor, actor))
                else:
                    cur.execute("""DELETE FROM saas_beinsystems_operator_dialog_company_bindings
                      WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s""",
                      (tenant, route["connect_channel_id"], route["conversation_id"]))
            conn.commit()
        return dialog_company_binding(profile, channel_id=channel, conversation_id=conversation)
    except OperatorError:
        raise
    except TenantLinearError as error:
        raise OperatorError("Нет доступа к диалогу или компании.", getattr(error, "status", 403)) from error
    except ValueError as error:
        raise OperatorError("Нет доступа к диалогу или компании.", 403) from error


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
    requested = str(requested_account or "").strip()
    actual = data.get("account")
    if (not isinstance(actual, str) or not actual.strip()
            or len(actual.strip()) > 240
            or any(ord(char) < 32 for char in actual.strip())
            or actual.strip() != requested):
        raise OperatorError("Operator не подтвердил идентификатор запрошенного аккаунта.", 403)
    result = {"account": actual.strip()}
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


def _accounts_access_probe_payload(response):
    """Validate only the confirmed response shape for the one-row access probe.

    Unlike the general catalogue parser, this rejects unknown envelopes, provider
    errors, ignored limits, and malformed rows instead of silently filtering them.
    """
    if not isinstance(response, dict) or set(response) != {"status", "data"}:
        raise OperatorError("Operator вернул неизвестный формат списка аккаунтов.")
    if response.get("status") != "success":
        raise OperatorError("Operator сообщил об ошибке списка аккаунтов.")
    values = response.get("data")
    if not isinstance(values, list):
        raise OperatorError("Operator вернул неверный формат списка аккаунтов.")
    if len(values) > 1:
        raise OperatorError("Operator вернул больше одной записи при limit:1.")
    if not values:
        return []
    row = values[0]
    if not isinstance(row, dict):
        raise OperatorError("Operator вернул повреждённую запись аккаунта.")
    account = row.get("account")
    if not isinstance(account, str):
        raise OperatorError("Operator вернул повреждённую запись аккаунта.")
    account = account.strip()
    if (not account or len(account) > 240
            or any(ord(char) < 32 for char in account)):
        raise OperatorError("Operator вернул повреждённую запись аккаунта.")
    return [account]


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


def _link_connection_scope(integration):
    """Read-only link fence; missing/invalid identity can never match old links."""
    metadata = integration.get("metadata") if isinstance(integration, dict) else None
    if not isinstance(metadata, dict):
        return None, None
    generation = metadata.get("generation")
    if not isinstance(generation, str) or not generation.strip():
        return None, None
    try:
        instance = str(uuid.UUID(str(metadata.get("connection_instance_id") or "")))
    except (ValueError, TypeError, AttributeError):
        return None, None
    return generation, instance


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
            generation, instance = _link_connection_scope(integration)
            cur.execute("""SELECT l.id,l.external_entity_type,l.external_id,l.external_scope_id,
                l.verification_basis,l.evidence_summary,l.verified_at,l.verified_by,l.revoked_at,l.version,
                COALESCE(l.revoked_at IS NULL AND l.connection_generation=%s AND l.connection_instance_id=%s,FALSE) AS current
              FROM saas_beinsystems_operator_links l
              WHERE l.tenant_id=%s::uuid AND l.local_entity_type=%s AND l.local_entity_id=%s::uuid
              ORDER BY l.created_at DESC LIMIT 50""",
              (generation, instance, tenant, local_entity_type, local_id))
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
            generation, instance = _link_connection_scope(integration)
            if instance is None:
                return {"status": "unlinked", "contact_id": str(contact["contact_id"]),
                        "account_links": [], "profile_user_links": []}
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
