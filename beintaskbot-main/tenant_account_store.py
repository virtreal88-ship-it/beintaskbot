"""Tenant-scoped account profiles and revocable browser sessions."""
from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from tenant_platform_core import _connect, _ensure_schema, _json, TenantPlatformError


def _clean_avatar(value: object) -> str:
    value = str(value or '').strip()[:500]
    if not value:
        return ''
    parsed = urlparse(value)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password:
        raise TenantPlatformError('Avatar üçün təhlükəsiz HTTPS ünvanı daxil edin.')
    return value


def profile(*, actor_id: int) -> dict:
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT %s::bigint AS actor_id,au.email,COALESCE(up.phone,au.phone,'') AS phone,
                                  COALESCE(NULLIF(au.display_name,''),NULLIF(concat_ws(' ',up.first_name,up.last_name),''),m.display_name,'') AS display_name,
                                  COALESCE(NULLIF(up.first_name,''),au.first_name,m.display_name,'') AS first_name,
                                  COALESCE(up.last_name,au.last_name,'') AS last_name,
                                  COALESCE(NULLIF(au.login,''),up.login,'') AS login,
                                  COALESCE(up.avatar_url,au.avatar_url,'') AS avatar_url,
                                  COALESCE(au.status,'active') AS status,au.verified_at,
                                  COALESCE(au.status='active' AND au.disabled_at IS NULL
                                      AND au.verified_at IS NOT NULL AND NULLIF(au.password_hash,'') IS NOT NULL,
                                      FALSE) AS password_change_available,
                                  COALESCE(au.last_login_at,m.last_login_at) AS last_login_at
                           FROM (SELECT %s::bigint AS actor_id) x
                           LEFT JOIN saas_auth_users au ON au.actor_id=x.actor_id
                           LEFT JOIN saas_user_profiles up ON up.actor_id=x.actor_id
                           LEFT JOIN LATERAL (SELECT last_login_at,display_name FROM saas_tenant_members WHERE telegram_id=x.actor_id ORDER BY last_login_at DESC NULLS LAST LIMIT 1) m ON TRUE''',
                        (int(actor_id),int(actor_id)))
            row = cur.fetchone()
    if not row:
        return {'actor_id': int(actor_id), 'display_name': '', 'email': '', 'phone': '',
                'first_name': '', 'last_name': '', 'login': '', 'avatar_url': '',
                'status': 'active', 'verified': False, 'last_login_at': None,
                'password_change_available': False}
    result = dict(row)
    result['verified'] = bool(result.pop('verified_at'))
    for key in ('last_login_at',):
        if result.get(key): result[key] = result[key].isoformat()
    return result


def update_profile(*, actor_id: int, first_name: object, last_name: object,
                   phone: object, avatar_url: object, login: object) -> dict:
    first = ' '.join(str(first_name or '').split())[:120]
    last = ' '.join(str(last_name or '').split())[:120]
    mobile = re.sub(r'[^0-9+]', '', str(phone or '').strip())
    if mobile:
        digits = re.sub(r'\D', '', mobile)
        if not 8 <= len(digits) <= 15: raise TenantPlatformError('Telefon nömrəsini ölkə kodu ilə düzgün yazın.')
        mobile = '+' + digits
    username = re.sub(r'[^a-zA-Z0-9_.-]', '', str(login or '').strip())[:64].casefold()
    if username and len(username) < 3: raise TenantPlatformError('Login ən azı 3 simvol olmalıdır.')
    avatar = _clean_avatar(avatar_url)
    display = ' '.join(part for part in (first, last) if part).strip()
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            if username:
                cur.execute('''SELECT EXISTS(SELECT 1 FROM saas_auth_users WHERE lower(login)=lower(%s) AND actor_id<>%s)
                               OR EXISTS(SELECT 1 FROM saas_user_profiles WHERE lower(login)=lower(%s) AND actor_id<>%s) AS taken''',
                            (username,int(actor_id),username,int(actor_id)))
                if cur.fetchone()['taken']: raise TenantPlatformError('Bu login artıq istifadə olunur.')
            cur.execute('''INSERT INTO saas_user_profiles(actor_id,first_name,last_name,phone,avatar_url,login,updated_at)
                           VALUES(%s,%s,%s,%s,%s,%s,now()) ON CONFLICT(actor_id) DO UPDATE SET first_name=EXCLUDED.first_name,
                           last_name=EXCLUDED.last_name,phone=EXCLUDED.phone,avatar_url=EXCLUDED.avatar_url,
                           login=EXCLUDED.login,updated_at=now()''',(int(actor_id),first,last,mobile,avatar,username))
            cur.execute('''UPDATE saas_auth_users SET first_name=%s,last_name=%s,display_name=CASE WHEN %s<>'' THEN %s ELSE display_name END,
                           phone=%s,avatar_url=%s,login=%s,updated_at=now() WHERE actor_id=%s''',
                        (first,last,display,display,mobile,avatar,username,int(actor_id)))
            cur.execute('''SELECT %s::bigint AS actor_id,email,%s AS phone,%s AS display_name,%s AS first_name,%s AS last_name,
                           %s AS login,%s AS avatar_url,COALESCE(status,'active') AS status,verified_at,last_login_at
                           FROM saas_auth_users WHERE actor_id=%s UNION ALL SELECT %s::bigint,NULL,%s,%s,%s,%s,%s,%s,
                           'active',NULL,(SELECT max(last_login_at) FROM saas_tenant_members WHERE telegram_id=%s)
                           WHERE NOT EXISTS(SELECT 1 FROM saas_auth_users WHERE actor_id=%s)''',
                        (int(actor_id),mobile,display,first,last,username,avatar,int(actor_id),int(actor_id),mobile,display,first,last,username,avatar,int(actor_id),int(actor_id)))
            row = cur.fetchone()
            cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                           SELECT gen_random_uuid(),m.tenant_id,%s,'account.profile.updated','account',%s,%s::jsonb
                           FROM saas_tenant_members m WHERE m.telegram_id=%s AND m.active=TRUE''',
                        (int(actor_id),str(actor_id),_json({'phone_changed': bool(mobile)}),int(actor_id)))
        conn.commit()
    result = dict(row); result['verified'] = bool(result.pop('verified_at'))
    if result.get('last_login_at'): result['last_login_at'] = result['last_login_at'].isoformat()
    return result


def _lock_verified_credential(cur, actor_id: int, credential_fingerprint: str) -> dict:
    """Fence a credential-authenticated effect against password rotation."""
    if not isinstance(credential_fingerprint, str) or not re.fullmatch(r'[a-f0-9]{64}', credential_fingerprint):
        raise TenantPlatformError('Giriş məlumatları dəyişib. Yenidən daxil olun.')
    cur.execute('''SELECT password_hash,verified_at,disabled_at,status,email,login
          FROM saas_auth_users WHERE actor_id=%s FOR UPDATE''', (int(actor_id),))
    account = cur.fetchone()
    if (not account or not account.get('verified_at') or account.get('disabled_at')
            or account.get('status') != 'active'
            or not hmac.compare_digest(credential_fingerprint,
                hashlib.sha256((str(actor_id) + '\0' + str(account.get('email') or '').casefold() + '\0' + str(account.get('password_hash') or '') + '\0' + str(account.get('login') or '').casefold()).encode()).hexdigest())):
        raise TenantPlatformError('Giriş məlumatları dəyişib. Yenidən daxil olun.')
    return account


def _lock_source_session(cur, actor_id: int, source: dict) -> None:
    """Revalidate a signed browser session before a dependent session/effect."""
    try:
        if not isinstance(source, dict) or int(source.get('actor_id') or 0) != int(actor_id):
            raise ValueError
        tenant_id = str(uuid.UUID(str(source['tenant_id'])))
        sid = source['session_id']
        if not isinstance(sid, str):
            raise ValueError
        session_id = uuid.UUID(sid) if sid else None
    except (KeyError, TypeError, ValueError):
        raise TenantPlatformError('Sessiya etibarlı deyil. Yenidən daxil olun.') from None
    # This order matches owner recovery, including the missing-auth-row case
    # of a Telegram-only account. A stale checked profile is never sufficient.
    cur.execute("SET LOCAL lock_timeout='3s'")
    cur.execute('SELECT pg_advisory_xact_lock(1852404781)')
    cur.execute('''SELECT legacy_sessions_revoked,disabled_at,status
          FROM saas_auth_users WHERE actor_id=%s FOR UPDATE''', (int(actor_id),))
    account = cur.fetchone()
    if account and (account.get('disabled_at') or account.get('status') != 'active'):
        raise TenantPlatformError('Sessiya etibarlı deyil. Yenidən daxil olun.')
    cur.execute('''SELECT telegram_id FROM saas_tenant_members
          WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE FOR SHARE''',
        (tenant_id, int(actor_id)))
    if not cur.fetchone():
        raise TenantPlatformError('Sessiya etibarlı deyil. Yenidən daxil olun.')
    if session_id:
        cur.execute('''SELECT id FROM saas_tenant_browser_sessions
              WHERE id=%s AND tenant_id=%s::uuid AND actor_id=%s
                AND revoked_at IS NULL AND expires_at>clock_timestamp() FOR UPDATE''',
            (session_id, tenant_id, int(actor_id)))
        if not cur.fetchone():
            raise TenantPlatformError('Sessiya etibarlı deyil. Yenidən daxil olun.')
    elif account and account.get('legacy_sessions_revoked') is not False:
        raise TenantPlatformError('Sessiya etibarlı deyil. Yenidən daxil olun.')


def create_session(*, session_id: str, tenant_id: str, actor_id: int, expires_at: int,
                   credential_fingerprint: str | None = None, source_session: dict | None = None,
                   revoke_source: bool = False) -> None:
    if int(actor_id) <= 0: return  # signed demo identities remain non-persistent and tenant-limited
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            if source_session is not None:
                _lock_source_session(cur, actor_id, source_session)
            elif revoke_source:
                raise TenantPlatformError('Sessiya etibarlı deyil. Yenidən daxil olun.')
            if credential_fingerprint is not None:
                # Password checking and cookie creation are separated by company
                # selection/invitation handling. Fence the final session INSERT
                # against the exact credential checked, under the same auth-row
                # lock as password reset. Reset either revokes an already-created
                # session or wins first and makes this stale login fail closed.
                _lock_verified_credential(cur, actor_id, credential_fingerprint)
            cur.execute('''INSERT INTO saas_tenant_browser_sessions(id,tenant_id,actor_id,expires_at)
                           SELECT %s,%s::uuid,%s,to_timestamp(%s)
                           WHERE EXISTS (SELECT 1 FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE)''',
                        (uuid.UUID(session_id),tenant_id,int(actor_id),int(expires_at),tenant_id,int(actor_id)))
            if cur.rowcount != 1: raise TenantPlatformError('Şirkət üzvlüyü aktiv deyil.')
            if revoke_source and source_session.get('session_id'):
                cur.execute('''UPDATE saas_tenant_browser_sessions SET revoked_at=now()
                      WHERE id=%s AND actor_id=%s''',
                    (uuid.UUID(source_session['session_id']), int(actor_id)))
        conn.commit()


def legacy_session_is_allowed(*, actor_id: int) -> bool:
    """Check live account eligibility and the actor-specific recovery fence."""
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('SELECT legacy_sessions_revoked,disabled_at,status FROM saas_auth_users WHERE actor_id=%s',
                        (int(actor_id),))
            account = cur.fetchone()
    # Telegram-only accounts have no auth row and retain existing behavior.
    return not account or (account.get('legacy_sessions_revoked') is False
                           and account.get('disabled_at') is None and account.get('status') == 'active')


def session_is_active(*, session_id: str, tenant_id: str, actor_id: int) -> bool:
    try: sid = uuid.UUID(session_id)
    except (ValueError, TypeError): return False
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT 1 FROM saas_tenant_browser_sessions s JOIN saas_tenant_members m
                           ON m.tenant_id=s.tenant_id AND m.telegram_id=s.actor_id AND m.active=TRUE
                           LEFT JOIN saas_auth_users au ON au.actor_id=s.actor_id
                           WHERE s.id=%s AND s.tenant_id=%s::uuid AND s.actor_id=%s
                             AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
                             AND (au.id IS NULL OR (au.disabled_at IS NULL AND au.status='active'))''',
                        (sid,tenant_id,int(actor_id)))
            return bool(cur.fetchone())


def list_sessions(*, tenant_id: str, actor_id: int) -> list[dict]:
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT s.id,s.created_at,s.expires_at,s.tenant_id,t.name AS tenant_name FROM saas_tenant_browser_sessions s
                           JOIN saas_tenants t ON t.id=s.tenant_id
                           WHERE s.actor_id=%s AND s.revoked_at IS NULL AND s.expires_at>now()
                             AND EXISTS(SELECT 1 FROM saas_tenant_members m WHERE m.tenant_id=s.tenant_id AND m.telegram_id=%s AND m.active=TRUE)
                           ORDER BY s.created_at DESC''',(int(actor_id),int(actor_id)))
            rows=cur.fetchall()
    return [{'id':str(r['id']),'tenant_id':str(r['tenant_id']),'tenant_name':r['tenant_name'],
             'created_at':r['created_at'].isoformat(),'expires_at':r['expires_at'].isoformat()} for r in rows]


def revoke_session(*, tenant_id: str, actor_id: int, session_id: str | None = None) -> int:
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            if session_id:
                try: sid=uuid.UUID(session_id)
                except (ValueError,TypeError): raise TenantPlatformError('Sessiya tapılmadı.')
                cur.execute('''UPDATE saas_tenant_browser_sessions SET revoked_at=now() WHERE id=%s
                               AND actor_id=%s AND revoked_at IS NULL''',(sid,int(actor_id)))
            else:
                cur.execute('''UPDATE saas_tenant_browser_sessions SET revoked_at=now() WHERE actor_id=%s AND revoked_at IS NULL''',(int(actor_id),))
            count=cur.rowcount
            cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,payload)
                           SELECT gen_random_uuid(),m.tenant_id,%s,'account.sessions.revoked','session',%s::jsonb
                           FROM saas_tenant_members m WHERE m.telegram_id=%s AND m.active=TRUE''',
                        (int(actor_id),_json({'count':count,'all':not bool(session_id)}),int(actor_id)))
        conn.commit()
    return count


def record_login(*, tenant_id: str, actor_id: int) -> None:
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('UPDATE saas_auth_users SET last_login_at=now(),updated_at=now() WHERE actor_id=%s AND disabled_at IS NULL AND status=\'active\'',(int(actor_id),))
            cur.execute('UPDATE saas_tenant_members SET last_login_at=now(),updated_at=now() WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE',(tenant_id,int(actor_id)))
        conn.commit()


def notification_preferences(*, tenant_id: str, actor_id: int, value: dict | None = None) -> dict:
    """Read/update only this membership's supported event preferences."""
    from tenant_member_store import member
    from tenant_notifications import available_notification_events, notification_catalog, validate_notification_preferences
    from tenant_policy import TenantPolicy
    current=member(tenant_id,int(actor_id))
    if not current: raise TenantPlatformError('Şirkət üzvlüyü aktiv deyil.')
    policy=TenantPolicy(current)
    supported=set(available_notification_events(policy))
    existing=(policy.member_settings.get('notifications') or {})
    if value is not None:
        try: validate_notification_preferences(value)
        except ValueError as exc: raise TenantPlatformError(str(exc)) from exc
        if set(value)-supported: raise TenantPlatformError('Bu rol üçün bildiriş növünə icazə yoxdur.')
        normalized={event:{channel:bool(enabled) for channel,enabled in channels.items()} for event,channels in value.items()}
        actor_key=str(int(actor_id))
        with _connect() as conn:
            _ensure_schema(conn)
            risk_channels = normalized.get('proactive_risk') or {}
            old_risk_channels = existing.get('proactive_risk') or {}
            restart_risk_backfill = (
                not any(old_risk_channels.get(channel) is True for channel in ('internal', 'push'))
                and any(risk_channels.get(channel) is True for channel in ('internal', 'push')))
            if any(risk_channels.get(channel) is True for channel in ('internal', 'push')):
                from tenant_proactive_risk_schema import ensure_schema as ensure_risk_schema
                ensure_risk_schema(conn)
            with conn.cursor() as cur:
                cur.execute('''INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by)
                    VALUES(%s::uuid,'members',jsonb_build_object(%s::text,jsonb_build_object('notifications',%s::jsonb)),%s)
                    ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=jsonb_set(
                        jsonb_set(saas_tenant_workflow_policies.value,ARRAY[%s::text]::text[],
                                  COALESCE(saas_tenant_workflow_policies.value->(%s::text),'{}'::jsonb),TRUE),
                        ARRAY[%s::text,'notifications']::text[],%s::jsonb,TRUE),updated_by=%s,updated_at=now()''',
                    (tenant_id,actor_key,_json(normalized),int(actor_id),actor_key,actor_key,actor_key,_json(normalized),int(actor_id)))
                if any(risk_channels.get(channel) is True for channel in ('internal', 'push')):
                    from tenant_proactive_risk_delivery import register_baseline_backfill
                    register_baseline_backfill(cur, tenant_id, int(actor_id), restart=restart_risk_backfill)
            conn.commit()
        existing=normalized
    catalog=[item for item in notification_catalog() if item['key'] in supported]
    from tenant_telegram_binding import binding_status
    bound = binding_status(tenant_id=tenant_id, actor_id=int(actor_id)).get('bound')
    return {'events':catalog,'preferences':{key:existing.get(key,{}) for key in supported},
            'channels_available':['internal','push'] + (['telegram'] if bound else [])}
