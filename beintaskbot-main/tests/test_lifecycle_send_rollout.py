"""Local final-dispatch barrier checks; callbacks never contact a provider."""
from contextlib import contextmanager
from unittest.mock import Mock
import pytest
from tenant_dialog_rollout import RolloutState
from tenant_linear_policy import TenantLinearError
import tenant_chat_send_store as sends


@pytest.mark.parametrize('mode',['legacy','shadow'])
@pytest.mark.parametrize('command',[None,{}])
def test_legacy_unscoped_dispatch_retains_shared_barrier(monkeypatch,mode,command):
    events=[]
    class Connection:
        @contextmanager
        def cursor(self):
            events.append('enter')
            try:yield object()
            finally:events.append('leave')
    @contextmanager
    def connect():yield Connection()
    monkeypatch.setattr(sends,'_connect',connect)
    monkeypatch.setattr('tenant_scoped_access.scoped_access_enabled',lambda:False)
    def guard(cur,tenant):events.append('guard');return RolloutState(mode,False,False,7)
    monkeypatch.setattr('tenant_dialog_rollout.guard',guard)
    def source_guard(cur,tenant,expected):
        assert tenant=='tenant'
        events.append('source_guard')
    monkeypatch.setattr('tenant_whatsapp_native_transport.guard_dispatch',source_guard)
    authority=Mock(side_effect=AssertionError('must preserve original legacy fastpath'))
    monkeypatch.setattr(sends,'authority',authority)
    def callback(credentials):
        assert events==['enter','guard','source_guard']
        events.append('dispatch');return 'synthetic'
    item={'key':('tenant',1,'request'),'connection':['connect','channel','dialog']}
    if command is not None:item['command']=command
    assert sends.dispatch_guarded(item,{},callback)=='synthetic'
    assert events==['enter','guard','source_guard','dispatch','leave']
    authority.assert_not_called()


@pytest.mark.parametrize('active,scoped',[(True,False),(False,True),(True,True)])
def test_active_or_scoped_send_never_uses_legacy_fastpath(monkeypatch,active,scoped):
    class Connection:
        @contextmanager
        def cursor(self):yield object()
    @contextmanager
    def connect():yield Connection()
    monkeypatch.setattr(sends,'_connect',connect)
    monkeypatch.setattr('tenant_scoped_access.scoped_access_enabled',lambda:scoped)
    monkeypatch.setattr('tenant_dialog_rollout.guard',lambda *args:RolloutState('active' if active else 'legacy',active,active,1))
    authority=Mock(side_effect=TenantLinearError('Denied',403))
    monkeypatch.setattr(sends,'authority',authority)
    callback=Mock()
    item={'key':('tenant',1,'request'),'connection':['connect','channel','dialog'],'command':{'lead_id':41}}
    with pytest.raises(TenantLinearError):sends.dispatch_guarded(item,{},callback)
    authority.assert_called_once();callback.assert_not_called()


def test_case_scope_guard_precedes_legacy_fastpath(monkeypatch):
    class Connection:
        @contextmanager
        def cursor(self):yield object()
    @contextmanager
    def connect():yield Connection()
    monkeypatch.setattr(sends,'_connect',connect)
    def deny(*args,**kwargs):raise TenantLinearError('Case denied',403)
    monkeypatch.setattr('tenant_consultation_chat_media.authorize',deny)
    rollout=Mock(side_effect=AssertionError('must not reach legacy dispatch'))
    monkeypatch.setattr('tenant_dialog_rollout.guard',rollout)
    callback=Mock()
    item={'key':('tenant',1,'request'),'connection':['connect','channel','dialog'],
          'command':{'lead_id':41,'consultation_id':'case-consultation'}}
    with pytest.raises(TenantLinearError,match='Case denied'):sends.dispatch_guarded(item,{},callback)
    callback.assert_not_called();rollout.assert_not_called()
