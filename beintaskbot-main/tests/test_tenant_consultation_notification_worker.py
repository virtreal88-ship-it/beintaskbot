import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import tenant_consultation_notification_worker as worker


ITEM = {'tenant_id':'tenant-a','consultation_id':'consultation-a','recipient_id':901,
        'chat_id':12345,'binding_verified_at':'2026-10-25T08:00:00+00:00'}
PROFILE = {'tenant_id':'tenant-a','telegram_id':901,'active':True,'role':'worker',
           'permissions':['support'],'workflow':{'policies':[{'policy_key':'members','value':{'901':{'notifications':{'ai_developer_consultation':{'telegram':True}}}}}]}}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(worker, '_hours', lambda: True)
    monkeypatch.setattr(worker, 'member', lambda *_: dict(PROFILE))
    monkeypatch.setattr(worker, 'binding_snapshot', lambda **_: {'chat_id':12345,'verified_at':'2026-10-25T08:00:00+00:00'})
    monkeypatch.setattr(worker, 'notification_channels', lambda policy, *, tenant_id, **_: ['telegram'] if policy.profile.get('tenant_id') == tenant_id else [])


@pytest.mark.parametrize('change', [
    lambda monkeypatch: monkeypatch.setattr(worker, 'member', lambda *_: None),
    lambda monkeypatch: monkeypatch.setattr(worker, 'member', lambda *_: {**PROFILE,'active':False}),
    lambda monkeypatch: monkeypatch.setattr(worker, 'member', lambda *_: {**PROFILE,'permissions':[]}),
    lambda monkeypatch: monkeypatch.setattr(worker, 'binding_snapshot', lambda **_: None),
    lambda monkeypatch: monkeypatch.setattr(worker, 'binding_snapshot', lambda **_: {'chat_id':999,'verified_at':'2026-10-25T09:00:00+00:00'}),
    lambda monkeypatch: monkeypatch.setattr(worker, 'notification_channels', lambda *_, **__: []),
    lambda monkeypatch: monkeypatch.setattr(worker, '_hours', lambda: False),
])
def test_final_delivery_gate_rejects_revoked_access(monkeypatch, change):
    change(monkeypatch)
    assert worker._current_delivery_permission(ITEM) is False


def test_final_delivery_gate_accepts_current_tenant_binding_only():
    assert worker._current_delivery_permission(ITEM) is True
    foreign = {**ITEM, 'tenant_id':'tenant-b'}
    assert worker._current_delivery_permission(foreign) is False


@pytest.mark.asyncio
async def test_worker_skips_invalid_recipient_without_sending_or_retry(monkeypatch):
    claimed = Mock(side_effect=[dict(ITEM), None])
    skipped = Mock()
    monkeypatch.setattr(worker, 'claim_delivery', claimed)
    monkeypatch.setattr(worker, '_current_delivery_permission', lambda _: False)
    monkeypatch.setattr(worker, 'skip_delivery', skipped)
    bot=SimpleNamespace(send_message=AsyncMock())
    await worker.deliver_consultation_notifications(bot, Mock())
    bot.send_message.assert_not_awaited()
    skipped.assert_called_once_with(ITEM)


@pytest.mark.asyncio
async def test_worker_marks_transport_timeout_unknown_without_resend(monkeypatch):
    claimed = Mock(side_effect=[dict(ITEM), None])
    finished = Mock()
    monkeypatch.setattr(worker, 'claim_delivery', claimed)
    monkeypatch.setattr(worker, '_current_delivery_permission', lambda _: True)
    monkeypatch.setattr(worker, 'finish_delivery', finished)
    bot=SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError()))
    await worker.deliver_consultation_notifications(bot, Mock())
    bot.send_message.assert_awaited_once()
    finished.assert_called_once_with(ITEM, 'unknown')


def test_after_hours_never_claims_pending_delivery(monkeypatch):
    monkeypatch.setattr(worker, '_hours', lambda: False)
    assert worker.claim_delivery() is None
