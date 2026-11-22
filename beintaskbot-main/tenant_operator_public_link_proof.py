"""One operator-owner requested demo check. Never activate or widen a pending JWT."""
import os

from tenant_beinsystems_operator import OperatorError, _record_operator_request
from tenant_beinsystems_operator_company_import import (
    _pending_candidate_fence, _revision, _verified_pending_identity,
)
from tenant_dialog_company_public_link import _ENDPOINT, _issue_public_link


IDENTITY_ENV = 'BEINSYSTEMS_OPERATOR_PUBLIC_LINK_PROBE_IDENTIFIER'
ACCOUNT_ENV = 'BEINSYSTEMS_OPERATOR_PUBLIC_LINK_PROBE_ACCOUNT'


def _configuration():
    values = [os.environ.get(key, '') for key in (IDENTITY_ENV, ACCOUNT_ENV)]
    if all(0 < len(value) <= 240 and value.strip() == value and value.isprintable() for value in values):
        return tuple(values)
    return None


def probe_configured():
    return _configuration() is not None


def verify_public_link_proof(profile, candidate_received_at):
    """Verify saved identity and the fixed demo call; disclose only the result."""
    if not isinstance(profile,dict) or profile.get('role') != 'owner' or profile.get('active') is False:
        raise OperatorError('Проверка ссылки доступна только владельцу.',403)
    if (not isinstance(candidate_received_at, str)
            or not 0 < len(candidate_received_at) <= 64
            or candidate_received_at != candidate_received_at.strip()):
        raise OperatorError('Обновите карточку и выберите сохранённый JWT Operator.',409)

    configuration = _configuration()
    if configuration is None:
        raise OperatorError('Проверка ссылки Operator не настроена.',404)
    expected_identity, demo_account = configuration
    tenant, actor, candidate, identity = _verified_pending_identity(profile,candidate_received_at)
    if identity['identifier'].casefold() != expected_identity.casefold():
        raise OperatorError('Сохранённый JWT принадлежит не ожидаемой учётной записи Operator.',409)
    _record_operator_request(profile, _ENDPOINT, {'account': demo_account}, issuance=True)
    _pending_candidate_fence(profile, tenant, actor, candidate)
    # Reuse the exact same fixed-host transport and receipt validation as the
    # active integration, but do not return the URL or persist any capability.
    _issue_public_link(demo_account, token=candidate['token'], base_url=candidate['base_url'])
    _pending_candidate_fence(profile, tenant, actor, candidate)
    return {'verified': True, 'candidate_received_at': _revision(candidate['received_at'])}
