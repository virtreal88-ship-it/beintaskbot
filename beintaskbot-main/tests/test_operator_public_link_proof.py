"""Demo check is purpose-bound and cannot activate the integration."""
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest

import tenant_operator_public_link_proof as proof
from tenant_beinsystems_operator import OperatorError
from tenant_beinsystems_operator_api import integration_handler


REVISION = datetime(2026, 11, 21, tzinfo=timezone.utc)
OWNER = {'tenant_id': str(uuid.uuid4()), 'telegram_id': 17, 'role': 'owner'}


@pytest.fixture
def hooks(monkeypatch):
    monkeypatch.setenv(proof.IDENTITY_ENV, 'expected@synthetic')
    monkeypatch.setenv(proof.ACCOUNT_ENV, 'synthetic-demo')
    candidate = {'token': 'synthetic-secret', 'base_url': 'https://operator.example.test/v1',
                 'received_at': REVISION}
    verified = Mock(return_value=(OWNER['tenant_id'], OWNER['telegram_id'], candidate,
                                 {'identifier': 'EXPECTED@SYNTHETIC'}))
    audit = Mock()
    fence = Mock()
    post = Mock(return_value='https://1000.az/abc123')
    for name, call in [('_verified_pending_identity', verified),('_record_operator_read', audit),
                       ('_pending_candidate_fence', fence),('_lookup_public_link', post)]:
        monkeypatch.setattr(proof, name, call)
    return verified, audit, fence, post, candidate


def test_unconfigured_or_unbound_revision_never_reads_saved_candidate(monkeypatch, hooks):
    verified, audit, fence, post, _ = hooks
    with pytest.raises(OperatorError) as missing:
        proof.verify_public_link_proof(OWNER, None)
    assert missing.value.status == 409
    monkeypatch.delenv(proof.IDENTITY_ENV)
    assert not proof.probe_configured()
    with pytest.raises(OperatorError) as unconfigured:
        proof.verify_public_link_proof(OWNER, REVISION.isoformat())
    assert unconfigured.value.status == 404
    for fn in (verified, audit, fence, post):
        fn.assert_not_called()


def test_wrong_identity_or_failed_owner_read_never_fetches(hooks):
    verified, audit, fence, post, candidate = hooks
    verified.return_value = (OWNER['tenant_id'], OWNER['telegram_id'], candidate,
                             {'identifier':'different@synthetic'})
    with pytest.raises(OperatorError) as wrong:
        proof.verify_public_link_proof(OWNER, REVISION.isoformat())
    assert wrong.value.status == 409
    verified.side_effect = OperatorError('denied', 403)
    with pytest.raises(OperatorError) as denied:
        proof.verify_public_link_proof({**OWNER, 'role':'operator'}, REVISION.isoformat())
    assert denied.value.status == 403
    for fn in (audit, fence, post):
        fn.assert_not_called()


def test_success_pins_demo_and_reports_proof_only(hooks):
    verified, audit, fence, post, candidate = hooks
    result = proof.verify_public_link_proof(OWNER, REVISION.isoformat())
    assert result == {'verified': True,'candidate_received_at': REVISION.isoformat()}
    verified.assert_called_once_with(OWNER, REVISION.isoformat())
    audit.assert_called_once_with(OWNER, proof._ENDPOINT, {'account':'synthetic-demo'})
    assert fence.call_count == 2
    post.assert_called_once_with('synthetic-demo',token=candidate['token'],base_url=candidate['base_url'])


def test_rejects_replaced_candidate_before_read_and_after_receipt(hooks):
    _, _, fence, post, _ = hooks
    fence.side_effect = OperatorError('replaced',409)
    with pytest.raises(OperatorError):
        proof.verify_public_link_proof(OWNER, REVISION.isoformat())
    post.assert_not_called()
    fence.side_effect = [None, OperatorError('replaced',409)]
    with pytest.raises(OperatorError):
        proof.verify_public_link_proof(OWNER, REVISION.isoformat())
    post.assert_called_once()


def test_demo_must_already_have_a_link(hooks):
    _, _, fence, lookup, _ = hooks
    lookup.return_value = None
    with pytest.raises(OperatorError) as missing:
        proof.verify_public_link_proof(OWNER, REVISION.isoformat())
    assert missing.value.status == 404
    lookup.assert_called_once()
    assert fence.call_count == 2


def test_ambiguous_upstream_failure_is_not_retried(hooks):
    _, _, fence, post, _ = hooks
    post.side_effect = OperatorError('unconfirmed',502)
    with pytest.raises(OperatorError) as failure:
        proof.verify_public_link_proof(OWNER, REVISION.isoformat())
    assert failure.value.status == 502
    post.assert_called_once()
    fence.assert_called_once()


@pytest.mark.asyncio
async def test_settings_action_checks_owner_context_and_accepts_no_target_parameters(monkeypatch):
    monkeypatch.setenv('BEINSYSTEMS_OPERATOR_TENANT_ID',OWNER['tenant_id'])
    method = Mock(return_value={'verified':True, 'candidate_received_at':REVISION.isoformat()})
    monkeypatch.setattr(proof,'verify_public_link_proof',method)
    origin = 'https://crm.test'
    handler = integration_handler(lambda request:OWNER,origin,Mock())
    body = {'action':'verify_public_link_proof','candidate_received_at':REVISION.isoformat(),
            'expected_tenant_id':OWNER['tenant_id'],'expected_user_id':str(OWNER['telegram_id'])}

    async def call(values=body, request_origin=origin):
        raw = json.dumps(values).encode()
        async def read():
            return raw
        return await handler(SimpleNamespace(method='POST',content_type='application/json',
             content_length=len(raw),headers={'Origin':request_origin},read=read))

    denied = [await call({**body, 'account':'forged'}), await call({**body,'token':'forged'}),
              await call({**body,'expected_user_id':'33'}), await call(request_origin='https://other.test')]
    assert [r.status for r in denied] == [400,400,409,403]
    method.assert_not_called()
    success = await call()
    assert success.status == 200 and success.headers['Cache-Control'] == 'no-store'
    assert json.loads(success.body) == {'success':True,'public_link_proof':method.return_value}
    method.assert_called_once_with(OWNER, REVISION.isoformat())
