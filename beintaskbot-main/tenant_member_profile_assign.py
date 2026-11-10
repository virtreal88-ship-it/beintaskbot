"""Owner editing of a company-managed employee's account profile.

Uses the same ownership boundary as email/password assignment. Existing shared
identities are never modified by a company administrator.
"""
from __future__ import annotations

import re
from psycopg.errors import DeadlockDetected, LockNotAvailable, UniqueViolation

import tenant_email_auth as auth
from tenant_account_store import _clean_avatar, _lock_source_session
from tenant_member_password_assign import _eligible_owned_account
from tenant_member_store import MemberCardError, _audit_member, _require_member_schema, _version
from tenant_platform_core import TenantPlatformError, _connect

PROFILE_FIELDS = frozenset({'first_name', 'last_name', 'login', 'phone', 'avatar_url'})


def normalize_profile_changes(changes: object) -> dict:
    if not isinstance(changes, dict) or not changes or set(changes) - PROFILE_FIELDS:
        raise MemberCardError('member_card_invalid', fields={'changes': 'invalid'})
    result = {}
    for key, raw in changes.items():
        if not isinstance(raw, str):
            raise MemberCardError('member_card_invalid', fields={key: 'invalid'})
        value = raw.strip()
        if key in {'first_name', 'last_name'}:
            value = ' '.join(value.split())
            if len(value) > 120 or key == 'first_name' and not value:
                raise MemberCardError('member_card_invalid', fields={key: 'invalid'})
        elif key == 'login':
            if not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}', value):
                raise MemberCardError('member_card_invalid', fields={key: 'invalid'})
            value = value.casefold()
        elif key == 'phone':
            if value:
                digits = re.sub(r'\D', '', value)
                if (not re.fullmatch(r'\+?[0-9 ().-]+', value)
                        or not 8 <= len(digits) <= 15):
                    raise MemberCardError('member_card_invalid', fields={key: 'invalid'})
                value = '+' + digits
        elif key == 'avatar_url':
            if len(value) > 500:
                raise MemberCardError('member_card_invalid', fields={key: 'invalid'})
            try:
                value = _clean_avatar(value)
            except TenantPlatformError:
                raise MemberCardError('member_card_invalid', fields={key: 'invalid'}) from None
        result[key] = value
    return result


def assign_member_profile(*, tenant_id: str, owner_id: int, telegram_id: int,
                          changes: object, expected_version: str, source_session: dict) -> dict:
    if (not isinstance(source_session, dict) or source_session.get('actor_id') != int(owner_id)
            or str(source_session.get('tenant_id')) != str(tenant_id)):
        raise MemberCardError('member_access_revoked', 403)
    selected = normalize_profile_changes(changes)
    try:
        with _connect() as conn:
            _require_member_schema(conn)
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout='3s'")
                cur.execute('SELECT pg_advisory_xact_lock(1852404781)')
                cur.execute('LOCK TABLE saas_tenants IN SHARE MODE')
                cur.execute('LOCK TABLE saas_tenant_members IN SHARE MODE')
                _lock_source_session(cur, int(owner_id), source_session)
                try:
                    account = _eligible_owned_account(cur, tenant_id=tenant_id, owner_id=owner_id,
                        telegram_id=telegram_id, expected_version=expected_version)
                except MemberCardError as exc:
                    if 'password' in exc.fields:
                        exc.fields = {key: exc.fields['password'] for key in selected}
                    raise
                cur.execute('''SELECT first_name,last_name,login,phone,avatar_url
                    FROM saas_auth_users WHERE id=%s''', (account['id'],))
                before = dict(cur.fetchone())
                # Match card reads and preserve all unedited profile fields.
                cur.execute('''SELECT first_name,last_name,login,phone,avatar_url
                    FROM saas_user_profiles WHERE actor_id=%s FOR UPDATE''', (int(telegram_id),))
                profile = dict(cur.fetchone() or {})
                merged = {key: before.get(key) or profile.get(key) or '' for key in PROFILE_FIELDS}
                merged.update(selected)
                if merged['login']:
                    cur.execute('''SELECT EXISTS(SELECT 1 FROM saas_auth_users
                            WHERE lower(login)=lower(%s) AND actor_id<>%s)
                        OR EXISTS(SELECT 1 FROM saas_user_profiles
                            WHERE lower(login)=lower(%s) AND actor_id<>%s) AS taken''',
                        (merged['login'], int(telegram_id), merged['login'], int(telegram_id)))
                    if cur.fetchone()['taken']:
                        raise MemberCardError('member_duplicate', 409, fields={'login': 'duplicate'})
                display = ' '.join(part for part in (merged['first_name'], merged['last_name']) if part)
                cur.execute('''INSERT INTO saas_user_profiles
                    (actor_id,first_name,last_name,phone,avatar_url,login,updated_at)
                    VALUES(%s,%s,%s,%s,%s,%s,now()) ON CONFLICT(actor_id) DO UPDATE
                    SET first_name=EXCLUDED.first_name,last_name=EXCLUDED.last_name,
                        phone=EXCLUDED.phone,avatar_url=EXCLUDED.avatar_url,
                        login=EXCLUDED.login,updated_at=now()''',
                    (int(telegram_id), merged['first_name'], merged['last_name'], merged['phone'],
                        merged['avatar_url'], merged['login']))
                cur.execute('''UPDATE saas_auth_users SET first_name=%s,last_name=%s,
                    display_name=%s,phone=%s,avatar_url=%s,login=%s,updated_at=now() WHERE id=%s''',
                    (merged['first_name'], merged['last_name'], display, merged['phone'],
                        merged['avatar_url'], merged['login'], account['id']))
                if 'login' in selected and selected['login'] != (before.get('login') or '').casefold():
                    cur.execute('UPDATE saas_auth_users SET legacy_sessions_revoked=TRUE WHERE id=%s', (account['id'],))
                    cur.execute('''UPDATE saas_tenant_browser_sessions SET revoked_at=now()
                        WHERE actor_id=%s AND revoked_at IS NULL''', (int(telegram_id),))
                    auth._consume_password_setting_tokens(cur, account['id'])
                cur.execute('''UPDATE saas_tenant_members
                    SET updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond')
                    WHERE tenant_id=%s::uuid AND telegram_id=%s RETURNING updated_at''',
                    (tenant_id, int(telegram_id)))
                version = _version(cur.fetchone())
                _audit_member(cur, tenant_id, owner_id, telegram_id,
                    'employee.profile.assigned', {'fields': sorted(selected)})
            conn.commit()
    except UniqueViolation:
        raise MemberCardError('member_duplicate', 409, fields={'login': 'duplicate'}) from None
    except (LockNotAvailable, DeadlockDetected):
        raise MemberCardError('member_account_busy', 409) from None
    return {'telegram_id': int(telegram_id), 'card_version': version, 'profile_changed': True, 'profile': merged}
