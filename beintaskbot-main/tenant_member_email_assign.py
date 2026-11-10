"""Owner-managed login/recovery email for company-provisioned employee accounts.

This changes an existing company's managed account, not mailbox ownership proof.
No mail is sent and no account is merged, activated, or moved between companies.
"""
from __future__ import annotations

from psycopg.errors import DeadlockDetected, LockNotAvailable, UniqueViolation

import tenant_email_auth as auth
from tenant_account_store import _lock_source_session
from tenant_member_password_assign import _eligible_owned_account
from tenant_member_store import MemberCardError, _audit_member, _require_member_schema, _version
from tenant_platform_core import TenantPlatformError, _connect


def assign_member_email(*, tenant_id: str, owner_id: int, telegram_id: int,
                        email: object, expected_version: str, source_session: dict) -> dict:
    if (not isinstance(source_session, dict) or source_session.get('actor_id') != int(owner_id)
            or str(source_session.get('tenant_id')) != str(tenant_id)):
        raise MemberCardError('member_access_revoked', 403)
    if not isinstance(email, str):
        raise MemberCardError('member_card_invalid', fields={'email': 'invalid'})
    try:
        address = auth._email(email)
    except TenantPlatformError:
        raise MemberCardError('member_card_invalid', fields={'email': 'invalid'}) from None
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
                        exc.fields = {'email': exc.fields['password']}
                    raise
                cur.execute('''SELECT 1 FROM saas_auth_users
                      WHERE lower(email)=lower(%s) AND id<>%s''', (address, account['id']))
                if cur.fetchone():
                    raise MemberCardError('member_duplicate', 409, fields={'email': 'duplicate'})
                # verified_at is existing account activation, not a new claim
                # that this mailbox was verified. Preserve it and the password.
                cur.execute('''UPDATE saas_auth_users SET email=%s,
                      legacy_sessions_revoked=TRUE,updated_at=now() WHERE id=%s''',
                    (address, account['id']))
                cur.execute('''UPDATE saas_tenant_browser_sessions SET revoked_at=now()
                      WHERE actor_id=%s AND revoked_at IS NULL''', (int(telegram_id),))
                auth._consume_password_setting_tokens(cur, account['id'])
                cur.execute('''UPDATE saas_tenant_members
                      SET updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond')
                      WHERE tenant_id=%s::uuid AND telegram_id=%s RETURNING updated_at''',
                    (tenant_id, int(telegram_id)))
                version = _version(cur.fetchone())
                _audit_member(cur, tenant_id, owner_id, telegram_id, 'employee.email.assigned', {})
            conn.commit()
    except UniqueViolation:
        raise MemberCardError('member_duplicate', 409, fields={'email': 'duplicate'}) from None
    except (LockNotAvailable, DeadlockDetected):
        raise MemberCardError('member_account_busy', 409) from None
    return {'telegram_id': int(telegram_id), 'card_version': version, 'email_changed': True}
