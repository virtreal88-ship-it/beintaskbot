import ast
from pathlib import Path
from unittest.mock import MagicMock, patch
import tenant_crm_notice_store as store
import tenant_crm_notice_outbox as outbox
import tenant_crm_notice_worker as worker
import tenant_notification_center as center
T='00000000-0000-0000-0000-000000000001'

def test_legacy_dispatch_retains_tenant_barrier_through_callback():
    conn=MagicMock();conn.__enter__.return_value=conn
    order=[]
    def callback():
        order.append('provider');conn.__exit__.assert_not_called();return 'done'
    with patch.object(outbox,'_connect',return_value=conn), patch('tenant_dialog_rollout.guard',side_effect=lambda *a,**k:order.append('tenant')), patch('tenant_dialog_lifecycle.enabled',return_value=False), patch('tenant_scoped_access.scoped_access_enabled',return_value=False):
        assert outbox.dispatch_guarded({'event':'incoming_message','tenant_id':T,'recipient_id':1},callback)=='done'
    assert order==['tenant','provider']
    conn.__exit__.assert_called_once()

def test_busy_activation_never_sends_notification():
    from tenant_linear_policy import TenantLinearError
    conn=MagicMock(); callback=MagicMock()
    with patch.object(outbox,'_connect',return_value=conn), patch('tenant_dialog_rollout.guard',side_effect=TenantLinearError('busy',409)):
        try:outbox.dispatch_guarded({'event':'incoming_message','tenant_id':T,'recipient_id':1},callback)
        except TenantLinearError:pass
        else:raise AssertionError('must fail closed')
    callback.assert_not_called()

def test_notification_access_locks_tenant_before_membership():
    from tenant_linear_policy import TenantLinearError
    cur=MagicMock();cur.fetchone.return_value=None;order=[]
    cur.execute.side_effect=lambda *a:order.append('membership')
    with patch('tenant_dialog_rollout.guard',side_effect=lambda *a,**k:order.append('tenant')):
        try:center._access(cur,{'tenant_id':T,'telegram_id':1})
        except TenantLinearError:pass
    assert order==['tenant','membership']

def test_visibility_and_modes_always_have_cursor_and_tenant():
    for module in (store,outbox,worker,center):
        for node in ast.walk(ast.parse(Path(module.__file__).read_text())):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name):
                if node.func.id=='lifecycle_enabled':assert len(node.args)==2
                if node.func.id=='visibility_sql':assert isinstance(node.args[0],ast.Name) and node.args[0].id=='cur'
