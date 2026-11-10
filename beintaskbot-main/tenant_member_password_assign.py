"""Owner-assigned passwords for accounts exclusively belonging to this company."""
from __future__ import annotations

import hmac
from psycopg.errors import DeadlockDetected, LockNotAvailable

import tenant_email_auth as auth
from tenant_account_store import _lock_source_session
from tenant_member_store import MemberCardError, _audit_member, _locked_members, _require_member_schema, _version
from tenant_platform_core import TenantPlatformError, _connect


def _eligible_owned_account(cur, *, tenant_id, owner_id, telegram_id, expected_version):
    current, target = _locked_members(cur, tenant_id, owner_id, telegram_id)
    cur.execute('SELECT owner_telegram_id,status FROM saas_tenants WHERE id=%s::uuid', (tenant_id,))
    company = cur.fetchone() or {}
    if (current.get('role') != 'owner' or current.get('active') is not True
            or company.get('owner_telegram_id') != int(owner_id) or company.get('status') != 'active'):
        raise MemberCardError('member_access_revoked', 403)
    if not target:
        raise MemberCardError('member_not_found', 404)
    if int(telegram_id) == int(owner_id) or target.get('role') == 'owner':
        raise MemberCardError('member_field_read_only', 403, fields={'password': 'owner_protected'})
    if target.get('active') is not True:
        raise MemberCardError('member_inactive', 409)
    if not expected_version or expected_version != _version(target):
        raise MemberCardError('member_card_conflict', 409)
    cur.execute('SELECT EXISTS(SELECT 1 FROM saas_tenants WHERE owner_telegram_id=%s) AS owns_company', (int(telegram_id),))
    if cur.fetchone()['owns_company']:
        raise MemberCardError('member_field_read_only', 403, fields={'password': 'owner_protected'})
    cur.execute('SELECT tenant_id FROM saas_tenant_members WHERE telegram_id=%s ORDER BY tenant_id', (int(telegram_id),))
    memberships = cur.fetchall()
    if len(memberships) != 1 or str(memberships[0]['tenant_id']) != str(tenant_id):
        raise MemberCardError('member_field_read_only', 403, fields={'password': 'shared_account'})
    cur.execute('SELECT id,tenant_id,verified_at,disabled_at,status FROM saas_auth_users WHERE actor_id=%s FOR UPDATE', (int(telegram_id),))
    account = cur.fetchone()
    if (not account or not account.get('verified_at') or account.get('disabled_at')
            or account.get('status') != 'active'):
        raise MemberCardError('member_account_unavailable', 409)
    if str(account.get('tenant_id')) != str(tenant_id):
        raise MemberCardError('member_field_read_only', 403, fields={'password': 'shared_account'})
    cur.execute("""SELECT EXISTS(SELECT 1 FROM saas_tenant_audit_events
        WHERE tenant_id=%s::uuid AND action='employee.provisioned' AND entity_type='employee'
          AND entity_id=%s) AS managed""", (tenant_id, str(telegram_id)))
    if not cur.fetchone()['managed']:
        raise MemberCardError('member_field_read_only', 403, fields={'password': 'independent_account'})
    return account


def assign_member_password(*, tenant_id: str, owner_id: int, telegram_id: int,
                           password: object, confirm_password: object,
                           expected_version: str, source_session: dict) -> dict:
    if (not isinstance(source_session, dict) or source_session.get('actor_id') != int(owner_id)
            or str(source_session.get('tenant_id')) != str(tenant_id)):
        raise MemberCardError('member_access_revoked', 403)
    try:
        plain = auth._password(password)
    except TenantPlatformError:
        raise MemberCardError('member_card_invalid', fields={'password': 'invalid'}) from None
    if not isinstance(confirm_password, str) or not hmac.compare_digest(plain.encode(), confirm_password.encode()):
        raise MemberCardError('member_card_invalid', fields={'confirm_password': 'invalid'})
    try:
        with _connect() as conn:
            _require_member_schema(conn)
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout='3s'")
                cur.execute('SELECT pg_advisory_xact_lock(1852404781)')
                # Fence every existing membership writer, including invitations
                # and canonical ownership changes, before locking account rows.
                cur.execute('LOCK TABLE saas_tenants IN SHARE MODE')
                cur.execute('LOCK TABLE saas_tenant_members IN SHARE MODE')
                _lock_source_session(cur, int(owner_id), source_session)
                account = _eligible_owned_account(cur, tenant_id=tenant_id, owner_id=owner_id,
                    telegram_id=telegram_id, expected_version=expected_version)
                cur.execute('''UPDATE saas_auth_users SET password_hash=%s,force_password_change=FALSE,
                      legacy_sessions_revoked=TRUE,updated_at=now() WHERE id=%s''',
                    (auth._hash_password(plain), account['id']))
                cur.execute('UPDATE saas_tenant_browser_sessions SET revoked_at=now() WHERE actor_id=%s AND revoked_at IS NULL', (int(telegram_id),))
                auth._consume_password_setting_tokens(cur, account['id'])
                # Updating the card version prevents repeat writes from the
                # same UI command, including after an uncertain response.
                cur.execute('''UPDATE saas_tenant_members SET updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond')
                      WHERE tenant_id=%s::uuid AND telegram_id=%s RETURNING updated_at''', (tenant_id, int(telegram_id)))
                version = _version(cur.fetchone())
                _audit_member(cur, tenant_id, owner_id, telegram_id, 'employee.password.assigned', {})
            conn.commit()
    except (LockNotAvailable, DeadlockDetected):
        raise MemberCardError('member_account_busy', 409) from None
    return {'telegram_id': int(telegram_id), 'card_version': version, 'password_changed': True}
