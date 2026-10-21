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

import requests

from tenant_platform_common import _DEFAULT_MODULES, _DEFAULT_NOTIFICATIONS, _ROLE_PERMISSIONS, _slug, _tenant_payload, _invite_token
from tenant_platform_core import TenantPlatformError, _connect, _ensure_schema, _json

_EMAIL = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,63}$")
_LOG = logging.getLogger(__name__)


def _email(value: object) -> str:
    result = str(value or '').strip().casefold()
    if not _EMAIL.fullmatch(result) or len(result) > 254:
        raise TenantPlatformError('Email ünvanını düzgün yazın.')
    return result


def _login_identity(value: object) -> str:
    raw = str(value or '').strip().casefold()
    if '@' in raw:
        return _email(raw)
    if not re.fullmatch(r'[a-z0-9_.-]{3,64}', raw):
        raise TenantPlatformError('Email və ya login məlumatını düzgün daxil edin.')
    return raw


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
        return
    except Exception as smtp_exc:
        # Railway environments can block outbound SMTP ports.  Resend supports
        # the same sending key over HTTPS, which keeps confirmation mail working
        # without weakening the primary SMTP configuration.  This fallback is
        # deliberately limited to Resend's host; no arbitrary SMTP secret is
        # sent to another provider.
        _LOG.warning('Email delivery failed via SMTP (%s)', type(smtp_exc).__name__)
        if host.casefold() != 'smtp.resend.com' or not password:
            raise TenantPlatformError('Təsdiq məktubu göndərilmədi. Bir az sonra yenidən cəhd edin.') from smtp_exc
        try:
            _send_via_resend_api(sender=sender, destination=destination, subject=subject, body=body, api_key=password)
            return
        except requests.HTTPError as api_exc:
            status = getattr(getattr(api_exc, 'response', None), 'status_code', 'unknown')
            _LOG.warning('Email delivery failed via Resend HTTPS API (HTTP %s)', status)
            raise TenantPlatformError(f'Poçt provayderi məktubu rədd etdi (Resend HTTP {status}).') from api_exc
        except Exception as api_exc:
            _LOG.warning('Email delivery failed via Resend HTTPS API (%s)', type(api_exc).__name__)
            raise TenantPlatformError('Təsdiq məktubu göndərilmədi. Bir az sonra yenidən cəhd edin.') from api_exc


def _send_via_resend_api(*, sender: str, destination: str, subject: str, body: str, api_key: str) -> None:
    """Send through Resend HTTPS when its SMTP relay is unreachable."""
    response = requests.post(
        'https://api.resend.com/emails',
        json={'from': sender, 'to': [destination], 'subject': subject, 'text': body},
        headers={'Authorization': f'Bearer {api_key}'}, timeout=15,
    )
    response.raise_for_status()


def _product(value: object) -> str:
    """Accept a destination hint, never a URL or authorization decision."""
    return 'connect' if str(value or '').strip().casefold() == 'connect' else 'beintaskbot'


def _issue_token(cur, user_id: uuid.UUID, purpose: str, request_ip: str, destination_product: str = 'beintaskbot') -> str:
    cur.execute("SELECT count(*) AS total FROM saas_auth_tokens WHERE user_id=%s AND purpose=%s AND created_at>now()-interval '1 hour'", (user_id, purpose))
    if int((cur.fetchone() or {}).get('total') or 0) >= 5:
        raise TenantPlatformError('Bu ünvan üçün çox sorğu var. Bir az sonra yenidən cəhd edin.')
    cur.execute("SELECT count(*) AS total FROM saas_auth_tokens WHERE request_ip=%s AND created_at>now()-interval '1 hour'", (request_ip,))
    if int((cur.fetchone() or {}).get('total') or 0) >= 20:
        raise TenantPlatformError('Çox sorğu göndərildi. Bir az sonra yenidən cəhd edin.')
    token = secrets.token_urlsafe(32)
    cur.execute("UPDATE saas_auth_tokens SET used_at=now() WHERE user_id=%s AND purpose=%s AND used_at IS NULL", (user_id, purpose))
    cur.execute("INSERT INTO saas_auth_tokens(id,user_id,purpose,token_hash,request_ip,expires_at,destination_product) VALUES(%s,%s,%s,%s,%s,now()+interval '30 minutes',%s)", (uuid.uuid4(), user_id, purpose, _token_hash(token), request_ip[:80], _product(destination_product)))
    return token


def _email_link(token: str, purpose: str = 'confirm', destination_product: str = 'beintaskbot') -> str:
    origin = _origin()
    if not origin:
        raise TenantPlatformError('Sayt ünvanı email təsdiqi üçün quraşdırılmayıb.')
    path = {'reset': '/auth/reset', 'link_email': '/auth/link'}.get(purpose, '/auth/confirm')
    return f'{origin}{path}?token={token}&product={_product(destination_product)}'


def request_registration(*, name: object, company_name: object, phone: object, email: object, request_ip: str,
                         destination_product: object = 'beintaskbot', invite_token: object = None) -> dict:
    address = _email(email)
    invite_hash = hashlib.sha256(_invite_token(invite_token).encode('utf-8')).hexdigest() if invite_token else ''
    _assert_mail_ready()
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            invitation = None
            if invite_hash:
                cur.execute('''SELECT i.*,t.name AS tenant_name FROM saas_tenant_invites i JOIN saas_tenants t ON t.id=i.tenant_id
                               WHERE i.token_hash=%s FOR UPDATE OF i''',(invite_hash,))
                invitation=cur.fetchone()
                if not invitation or invitation.get('accepted_at') or invitation.get('expires_at') <= datetime.now(timezone.utc):
                    raise TenantPlatformError('Dəvət linkinin müddəti bitib və ya artıq istifadə olunub.')
                invited_email=str(invitation.get('invited_email') or '').strip().casefold()
                if invited_email and invited_email != address:
                    raise TenantPlatformError('Bu dəvət başqa email ünvanı üçün yaradılıb.')
                if not invited_email:
                    cur.execute('UPDATE saas_tenant_invites SET invited_email=%s WHERE id=%s',(address,invitation['id']))
            display_name = _name(name or (invitation or {}).get('display_name'), 'Adınızı')
            company = _name(company_name or (invitation or {}).get('tenant_name') or 'Şirkət', 'Şirkət adını')
            mobile = _phone(phone) if str(phone or '').strip() else ''
            cur.execute('SELECT * FROM saas_auth_users WHERE email=%s FOR UPDATE', (address,))
            user = cur.fetchone()
            if user and user.get('verified_at'):
                return {'accepted': True, 'existing': True, 'needs_login': bool(invitation)}
            if not user:
                user_id = uuid.uuid4()
                cur.execute("SELECT nextval('saas_auth_actor_seq') AS actor_id")
                actor_id = int(cur.fetchone()['actor_id'])
                cur.execute('''INSERT INTO saas_auth_users(id,actor_id,email,phone,display_name,company_name,pending_invite_id,tenant_id)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
                    (user_id, actor_id, address, mobile, display_name, company, invitation['id'] if invitation else None,
                     invitation['tenant_id'] if invitation else None))
            else:
                user_id = user['id']
                if invitation and user.get('pending_invite_id') and str(user['pending_invite_id']) != str(invitation['id']):
                    raise TenantPlatformError('Bu email üçün başqa dəvət təsdiqlənməyi gözləyir.')
                cur.execute('''UPDATE saas_auth_users SET phone=CASE WHEN %s<>'' THEN %s ELSE phone END,display_name=%s,company_name=%s,
                    pending_invite_id=COALESCE(%s,pending_invite_id),tenant_id=COALESCE(%s,tenant_id),updated_at=now() WHERE id=%s''',
                    (mobile,mobile,display_name,company,invitation['id'] if invitation else None,
                     invitation['tenant_id'] if invitation else None,user_id))
            product = _product(destination_product)
            token = _issue_token(cur, user_id, 'confirm', request_ip, product)
        conn.commit()
    _send_mail(address, 'Beintaskbot: подтверждение email', f'Подтвердите email и создайте пароль: {_email_link(token, destination_product=product)}\n\nСсылка действует 30 минут и используется один раз.')
    return {'accepted': True, 'existing': False}


def complete_confirmation(*, token: object, password: object) -> dict:
    raw_token, plain = str(token or ''), _password(password)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT t.id AS token_id,t.destination_product,u.* FROM saas_auth_tokens t JOIN saas_auth_users u ON u.id=t.user_id
                WHERE t.token_hash=%s AND t.purpose='confirm' AND t.used_at IS NULL AND t.expires_at>now() FOR UPDATE OF t,u''', (_token_hash(raw_token),))
            user = cur.fetchone()
            if not user:
                raise TenantPlatformError('Təsdiq keçidi etibarsızdır və ya müddəti bitib.')
            destination_product = _product(user.get('destination_product'))
            tenant_id = user.get('tenant_id')
            pending_invite_id = user.get('pending_invite_id')
            if pending_invite_id:
                cur.execute('SELECT * FROM saas_tenant_invites WHERE id=%s FOR UPDATE', (pending_invite_id,))
                invitation = cur.fetchone()
                if (not invitation or invitation.get('accepted_at')
                        or invitation.get('expires_at') <= datetime.now(timezone.utc)
                        or (invitation.get('invited_email') or '').casefold() != str(user['email']).casefold()):
                    raise TenantPlatformError('Dəvət keçidi etibarsızdır və ya müddəti bitib.')
                tenant_id = invitation['tenant_id']
                cur.execute('''INSERT INTO saas_tenant_members(tenant_id,telegram_id,display_name,role,permissions,active,invited_by)
                               VALUES(%s::uuid,%s,%s,%s,%s::jsonb,TRUE,%s)
                               ON CONFLICT(tenant_id,telegram_id) DO NOTHING''',
                            (tenant_id,user['actor_id'],user['display_name'],invitation['role'],_json(invitation['permissions']),invitation['created_by']))
                if cur.rowcount != 1:
                    raise TenantPlatformError('Bu hesab artıq şirkətin üzvüdür.')
                cur.execute('UPDATE saas_tenant_invites SET accepted_by=%s,accepted_at=now() WHERE id=%s',
                            (user['actor_id'], invitation['id']))
                cur.execute('UPDATE saas_auth_users SET pending_invite_id=NULL WHERE id=%s', (user['id'],))
            elif not tenant_id:
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
    return {'tenant_id': str(tenant_id), 'actor_id': int(user['actor_id']), 'destination_product': destination_product}


def login(*, email: object, password: object, request_ip: str) -> dict | None:
    address, plain = _login_identity(email), str(password or '')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS total FROM saas_auth_login_attempts WHERE (email=%s OR request_ip=%s) AND succeeded=FALSE AND created_at>now()-interval '15 minutes'", (address, request_ip[:80]))
            limited = int((cur.fetchone() or {}).get('total') or 0) >= 10
            cur.execute('''SELECT u.* FROM saas_auth_users u WHERE (lower(u.email)=lower(%s) OR lower(u.login)=lower(%s)
                OR EXISTS(SELECT 1 FROM saas_user_profiles p WHERE p.actor_id=u.actor_id AND lower(p.login)=lower(%s)))
                AND u.verified_at IS NOT NULL AND u.disabled_at IS NULL AND u.status='active' ''', (address, address, address))
            user = cur.fetchone()
            accepted = not limited and bool(user and _password_matches(plain, user.get('password_hash') or ''))
            cur.execute('INSERT INTO saas_auth_login_attempts(id,email,request_ip,succeeded) VALUES(%s,%s,%s,%s)', (uuid.uuid4(), address, request_ip[:80], accepted))
            if accepted:
                cur.execute('UPDATE saas_auth_users SET last_login_at=now(),updated_at=now() WHERE id=%s', (user['id'],))
        conn.commit()
    if not accepted or not user or not user.get('tenant_id'):
        return None
    return {'tenant_id': str(user['tenant_id']), 'actor_id': int(user['actor_id'])}


def request_password_reset(*, email: object, request_ip: str, destination_product: object = 'beintaskbot') -> dict:
    address = _email(email)
    _assert_mail_ready()
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM saas_auth_users WHERE email=%s AND verified_at IS NOT NULL AND disabled_at IS NULL FOR UPDATE', (address,))
            user = cur.fetchone()
            product = _product(destination_product)
            token = _issue_token(cur, user['id'], 'reset', request_ip, product) if user else ''
        conn.commit()
    if token:
        _send_mail(address, 'Beintaskbot: восстановление пароля', f'Создайте новый пароль: {_email_link(token, "reset", product)}\n\nСсылка действует 30 минут и используется один раз.')
    return {'accepted': True}


def complete_password_reset(*, token: object, password: object) -> dict:
    raw_token, plain = str(token or ''), _password(password)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT t.id AS token_id,t.destination_product,u.id AS user_id FROM saas_auth_tokens t JOIN saas_auth_users u ON u.id=t.user_id
              WHERE t.token_hash=%s AND t.purpose='reset' AND t.used_at IS NULL AND t.expires_at>now() FOR UPDATE OF t,u''', (_token_hash(raw_token),))
            row = cur.fetchone()
            if not row: raise TenantPlatformError('Parol keçidi etibarsızdır və ya müddəti bitib.')
            cur.execute('UPDATE saas_auth_users SET password_hash=%s,updated_at=now() WHERE id=%s', (_hash_password(plain), row['user_id']))
            cur.execute('UPDATE saas_tenant_browser_sessions SET revoked_at=now() WHERE actor_id=(SELECT actor_id FROM saas_auth_users WHERE id=%s) AND revoked_at IS NULL', (row['user_id'],))
            cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,payload)
                           SELECT %s,m.tenant_id,m.telegram_id,'account.password.reset','account','{}'::jsonb
                           FROM saas_auth_users u JOIN saas_tenant_members m ON m.telegram_id=u.actor_id AND m.active=TRUE
                           WHERE u.id=%s''',(uuid.uuid4(),row['user_id']))
            cur.execute('UPDATE saas_auth_tokens SET used_at=now() WHERE id=%s', (row['token_id'],))
        conn.commit()
    return {'destination_product': _product(row.get('destination_product'))}


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
                # A stale, unverified signup has not established an account
                # or a tenant yet.  The current signed CRM owner can safely
                # finish that signup for their own address; confirmation is
                # still sent to the mailbox before any password is created.
                # Never transfer an account that has been verified or tied to
                # a tenant: that would allow one CRM session to take over a
                # real, independent identity.
                if user.get('verified_at') or user.get('tenant_id'):
                    raise TenantPlatformError('Bu email artıq başqa hesabla bağlıdır.')
                cur.execute('''UPDATE saas_auth_users
                    SET actor_id=%s, tenant_id=%s::uuid, display_name=%s,
                        updated_at=now()
                    WHERE id=%s''',
                    (int(actor_id), tenant_id, _name(display_name, 'Adınızı'), user['id']))
            if not user:
                user_id = uuid.uuid4()
                cur.execute('''INSERT INTO saas_auth_users(id,actor_id,email,display_name,tenant_id)
                    VALUES(%s,%s,%s,%s,%s::uuid)''',
                    (user_id, int(actor_id), address, _name(display_name, 'Adınızı'), tenant_id))
            else:
                user_id = user['id']
                cur.execute('UPDATE saas_auth_users SET tenant_id=%s::uuid,updated_at=now() WHERE id=%s',
                            (tenant_id, user_id))
            cur.execute('''UPDATE saas_auth_users u SET first_name=p.first_name,last_name=p.last_name,
                           phone=CASE WHEN p.phone<>'' THEN p.phone ELSE u.phone END,
                           avatar_url=p.avatar_url,login=CASE WHEN p.login<>'' THEN p.login ELSE u.login END,
                           updated_at=now() FROM saas_user_profiles p WHERE u.id=%s AND p.actor_id=u.actor_id''',(user_id,))
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
