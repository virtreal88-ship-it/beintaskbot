"""Email/password authentication without weakening legacy Telegram access."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import smtplib
import ssl
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from tenant_platform_common import _DEFAULT_MODULES, _DEFAULT_NOTIFICATIONS, _ROLE_PERMISSIONS, _slug, _tenant_payload
from tenant_platform_core import TenantPlatformError, _connect, _ensure_schema, _json

_EMAIL = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,63}$")
_LOG = logging.getLogger(__name__)


def _email(value: object) -> str:
    result = str(value or '').strip().casefold()
    if not _EMAIL.fullmatch(result) or len(result) > 254:
        raise TenantPlatformError('Email ünvanını düzgün yazın.')
    return result


def _phone(value: object) -> str:
    result = re.sub(r'[^0-9+]', '', str(value or '').strip())
    if result.startswith('00'):
        result = '+' + result[2:]
    digits = re.sub(r'\D', '', result)
    if not 8 <= len(digits) <= 15:
        raise TenantPlatformError('Telefon nömrəsini ölkə kodu ilə düzgün yazın.')
    return '+' + digits


def _name(value: object, field: str) -> str:
    result = ' '.join(str(value or '').strip().split())[:120]
    if len(result) < 2:
        raise TenantPlatformError(f'{field} yazın.')
    return result


def _password(value: object) -> str:
    result = str(value or '')
    if not 12 <= len(result) <= 256:
        raise TenantPlatformError('Parol 12–256 simvol olmalıdır.')
    return result


def _hash_password(password: str, *, salt: bytes | None = None, rounds: int = 600_000) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, rounds)
    return f'pbkdf2_sha256${rounds}${salt.hex()}${digest.hex()}'


def _password_matches(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds_raw, salt_hex, expected_hex = str(encoded).split('$', 3)
        rounds = int(rounds_raw)
        if algorithm != 'pbkdf2_sha256' or not 300_000 <= rounds <= 1_000_000:
            return False
        actual = _hash_password(password, salt=bytes.fromhex(salt_hex), rounds=rounds).split('$', 3)[-1]
        return hmac.compare_digest(actual, expected_hex)
    except (TypeError, ValueError):
        return False


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _origin() -> str:
    return str(os.environ.get('CANONICAL_WEB_ORIGIN') or '').rstrip('/')


def _assert_mail_ready() -> None:
    if not str(os.environ.get('SMTP_HOST') or '').strip() or not str(os.environ.get('SMTP_FROM') or '').strip():
        raise TenantPlatformError('Email göndərişi hələ quraşdırılmayıb.')
    if not _origin():
        raise TenantPlatformError('Sayt ünvanı email təsdiqi üçün quraşdırılmayıb.')


def _send_mail(destination: str, subject: str, body: str) -> None:
    host = str(os.environ.get('SMTP_HOST') or '').strip()
    sender = str(os.environ.get('SMTP_FROM') or '').strip()
    if not host or not sender:
        raise TenantPlatformError('Email göndərişi hələ quraşdırılmayıb.')
    try:
        port = int(os.environ.get('SMTP_PORT') or '587')
    except ValueError as exc:
        raise TenantPlatformError('SMTP port ayarı düzgün deyil.') from exc
    message = EmailMessage()
    message['From'], message['To'], message['Subject'] = sender, destination, subject
    message.set_content(body)
    username, password = str(os.environ.get('SMTP_USERNAME') or ''), str(os.environ.get('SMTP_PASSWORD') or '')
    try:
        if str(os.environ.get('SMTP_SSL') or '').casefold() in {'1','true','yes'}:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=15) as smtp:
                if username: smtp.login(username, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if username: smtp.login(username, password)
                smtp.send_message(message)
    except Exception as exc:
        # Keep credentials, recipient and mail body out of logs.  The exception
        # type is sufficient to distinguish DNS/TLS/authentication failures in
        # Railway's protected service logs.
        _LOG.warning('Email delivery failed via SMTP (%s)', type(exc).__name__)
        raise TenantPlatformError('Təsdiq məktubu göndərilmədi. Bir az sonra yenidən cəhd edin.') from exc


def _issue_token(cur, user_id: uuid.UUID, purpose: str, request_ip: str) -> str:
    cur.execute("SELECT count(*) AS total FROM saas_auth_tokens WHERE user_id=%s AND purpose=%s AND created_at>now()-interval '1 hour'", (user_id, purpose))
    if int((cur.fetchone() or {}).get('total') or 0) >= 5:
        raise TenantPlatformError('Bu ünvan üçün çox sorğu var. Bir az sonra yenidən cəhd edin.')
    cur.execute("SELECT count(*) AS total FROM saas_auth_tokens WHERE request_ip=%s AND created_at>now()-interval '1 hour'", (request_ip,))
    if int((cur.fetchone() or {}).get('total') or 0) >= 20:
        raise TenantPlatformError('Çox sorğu göndərildi. Bir az sonra yenidən cəhd edin.')
    token = secrets.token_urlsafe(32)
    cur.execute("UPDATE saas_auth_tokens SET used_at=now() WHERE user_id=%s AND purpose=%s AND used_at IS NULL", (user_id, purpose))
    cur.execute("INSERT INTO saas_auth_tokens(id,user_id,purpose,token_hash,request_ip,expires_at) VALUES(%s,%s,%s,%s,%s,now()+interval '30 minutes')", (uuid.uuid4(), user_id, purpose, _token_hash(token), request_ip[:80]))
    return token


def _email_link(token: str, purpose: str = 'confirm') -> str:
    origin = _origin()
    if not origin:
        raise TenantPlatformError('Sayt ünvanı email təsdiqi üçün quraşdırılmayıb.')
    path = {'reset': '/auth/reset', 'link_email': '/auth/link'}.get(purpose, '/auth/confirm')
    return f'{origin}{path}?token={token}'


def request_registration(*, name: object, company_name: object, phone: object, email: object, request_ip: str) -> dict:
    display_name, company, mobile, address = _name(name, 'Adınızı'), _name(company_name, 'Şirkət adını'), _phone(phone), _email(email)
    _assert_mail_ready()
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM saas_auth_users WHERE email=%s FOR UPDATE', (address,))
            user = cur.fetchone()
            if user and user.get('verified_at'):
                return {'accepted': True, 'existing': True}
            if not user:
                user_id = uuid.uuid4()
                cur.execute("SELECT nextval('saas_auth_actor_seq') AS actor_id")
                actor_id = int(cur.fetchone()['actor_id'])
                cur.execute('''INSERT INTO saas_auth_users(id,actor_id,email,phone,display_name,company_name)
                    VALUES(%s,%s,%s,%s,%s,%s) RETURNING id''', (user_id, actor_id, address, mobile, display_name, company))
            else:
                user_id = user['id']
                cur.execute('''UPDATE saas_auth_users SET phone=%s,display_name=%s,company_name=%s,updated_at=now() WHERE id=%s''', (mobile, display_name, company, user_id))
            token = _issue_token(cur, user_id, 'confirm', request_ip)
        conn.commit()
    _send_mail(address, 'Beintaskbot: подтверждение email', f'Подтвердите email и создайте пароль: {_email_link(token)}\n\nСсылка действует 30 минут и используется один раз.')
    return {'accepted': True, 'existing': False}


def complete_confirmation(*, token: object, password: object) -> dict:
    raw_token, plain = str(token or ''), _password(password)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT t.id AS token_id,u.* FROM saas_auth_tokens t JOIN saas_auth_users u ON u.id=t.user_id
                WHERE t.token_hash=%s AND t.purpose='confirm' AND t.used_at IS NULL AND t.expires_at>now() FOR UPDATE OF t,u''', (_token_hash(raw_token),))
            user = cur.fetchone()
            if not user:
                raise TenantPlatformError('Təsdiq keçidi etibarsızdır və ya müddəti bitib.')
            tenant_id = user.get('tenant_id')
            if not tenant_id:
                tenant_id = uuid.uuid4()
                onboarding = {'company_name': user['company_name'], 'completed_steps': ['company','owner','email']}
                cur.execute('''INSERT INTO saas_tenants(id,slug,name,industry,status,owner_telegram_id,modules,notification_rules,onboarding)
                  VALUES(%s,%s,%s,'','active',%s,%s::jsonb,%s::jsonb,%s::jsonb)''',
                  (tenant_id, _slug(user['company_name']), user['company_name'], user['actor_id'], _json(_DEFAULT_MODULES), _json(_DEFAULT_NOTIFICATIONS), _json(onboarding)))
                cur.execute('''INSERT INTO saas_tenant_members(tenant_id,telegram_id,display_name,role,permissions)
                  VALUES(%s,%s,%s,'owner',%s::jsonb)''', (tenant_id, user['actor_id'], user['display_name'], _json(_ROLE_PERMISSIONS['owner'])))
            cur.execute('''UPDATE saas_auth_users SET password_hash=%s,verified_at=COALESCE(verified_at,now()),tenant_id=%s,updated_at=now() WHERE id=%s''', (_hash_password(plain), tenant_id, user['id']))
            cur.execute('UPDATE saas_auth_tokens SET used_at=now() WHERE id=%s', (user['token_id'],))
        conn.commit()
    return {'tenant_id': str(tenant_id), 'actor_id': int(user['actor_id'])}


def login(*, email: object, password: object, request_ip: str) -> dict | None:
    address, plain = _email(email), str(password or '')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS total FROM saas_auth_login_attempts WHERE (email=%s OR request_ip=%s) AND succeeded=FALSE AND created_at>now()-interval '15 minutes'", (address, request_ip[:80]))
            limited = int((cur.fetchone() or {}).get('total') or 0) >= 10
            cur.execute('SELECT * FROM saas_auth_users WHERE email=%s AND verified_at IS NOT NULL AND disabled_at IS NULL', (address,))
            user = cur.fetchone()
            accepted = not limited and bool(user and _password_matches(plain, user.get('password_hash') or ''))
            cur.execute('INSERT INTO saas_auth_login_attempts(id,email,request_ip,succeeded) VALUES(%s,%s,%s,%s)', (uuid.uuid4(), address, request_ip[:80], accepted))
        conn.commit()
    if not accepted or not user or not user.get('tenant_id'):
        return None
    return {'tenant_id': str(user['tenant_id']), 'actor_id': int(user['actor_id'])}


def request_password_reset(*, email: object, request_ip: str) -> dict:
    address = _email(email)
    _assert_mail_ready()
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM saas_auth_users WHERE email=%s AND verified_at IS NOT NULL AND disabled_at IS NULL FOR UPDATE', (address,))
            user = cur.fetchone()
            token = _issue_token(cur, user['id'], 'reset', request_ip) if user else ''
        conn.commit()
    if token:
        _send_mail(address, 'Beintaskbot: восстановление пароля', f'Создайте новый пароль: {_email_link(token, "reset")}\n\nСсылка действует 30 минут и используется один раз.')
    return {'accepted': True}


def complete_password_reset(*, token: object, password: object) -> bool:
    raw_token, plain = str(token or ''), _password(password)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT t.id AS token_id,u.id AS user_id FROM saas_auth_tokens t JOIN saas_auth_users u ON u.id=t.user_id
              WHERE t.token_hash=%s AND t.purpose='reset' AND t.used_at IS NULL AND t.expires_at>now() FOR UPDATE OF t,u''', (_token_hash(raw_token),))
            row = cur.fetchone()
            if not row: raise TenantPlatformError('Parol keçidi etibarsızdır və ya müddəti bitib.')
            cur.execute('UPDATE saas_auth_users SET password_hash=%s,updated_at=now() WHERE id=%s', (_hash_password(plain), row['user_id']))
            cur.execute('UPDATE saas_auth_tokens SET used_at=now() WHERE id=%s', (row['token_id'],))
        conn.commit()
    return True


def request_email_link(*, tenant_id: str, actor_id: int, display_name: object,
                       email: object, request_ip: str) -> dict:
    """Attach an email login to an already authenticated legacy identity.

    The current signed tenant session is the proof of ownership.  A different
    identity may never claim an address already linked to another actor.
    """
    address = _email(email)
    _assert_mail_ready()
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM saas_auth_users WHERE email=%s FOR UPDATE', (address,))
            user = cur.fetchone()
            if user and int(user['actor_id']) != int(actor_id):
                raise TenantPlatformError('Bu email artıq başqa hesabla bağlıdır.')
            if not user:
                user_id = uuid.uuid4()
                cur.execute('''INSERT INTO saas_auth_users(id,actor_id,email,display_name,tenant_id)
                    VALUES(%s,%s,%s,%s,%s::uuid)''',
                    (user_id, int(actor_id), address, _name(display_name, 'Adınızı'), tenant_id))
            else:
                user_id = user['id']
                cur.execute('UPDATE saas_auth_users SET tenant_id=%s::uuid,updated_at=now() WHERE id=%s',
                            (tenant_id, user_id))
            token = _issue_token(cur, user_id, 'link_email', request_ip)
        conn.commit()
    _send_mail(address, 'Beintaskbot: email girişini təsdiqləyin',
               f'Email girişini təsdiqləyin və parol yaradın: {_email_link(token, "link_email")}\n\n'
               'Keçid 30 dəqiqə qüvvədədir və yalnız bir dəfə istifadə edilir.')
    return {'accepted': True}


def complete_email_link(*, token: object, password: object) -> dict:
    raw_token, plain = str(token or ''), _password(password)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT t.id AS token_id,u.* FROM saas_auth_tokens t
                JOIN saas_auth_users u ON u.id=t.user_id
                WHERE t.token_hash=%s AND t.purpose='link_email' AND t.used_at IS NULL
                  AND t.expires_at>now() FOR UPDATE OF t,u''', (_token_hash(raw_token),))
            user = cur.fetchone()
            if not user or not user.get('tenant_id'):
                raise TenantPlatformError('Təsdiq keçidi etibarsızdır və ya müddəti bitib.')
            cur.execute('''UPDATE saas_auth_users
                SET password_hash=%s,verified_at=COALESCE(verified_at,now()),updated_at=now()
                WHERE id=%s''', (_hash_password(plain), user['id']))
            cur.execute('UPDATE saas_auth_tokens SET used_at=now() WHERE id=%s', (user['token_id'],))
        conn.commit()
    return {'tenant_id': str(user['tenant_id']), 'actor_id': int(user['actor_id'])}
