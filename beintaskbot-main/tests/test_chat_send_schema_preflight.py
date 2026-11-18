"""Warm paths read only; cold receipt DDL serializes before creation."""
from unittest.mock import MagicMock,Mock
import pytest
import tenant_chat_send_schema as schema


@pytest.mark.parametrize('checks,needs_lock,ddl',[
    ([True],False,False),([False,True],True,False),([False,False],True,True)])
def test_receipt_schema_double_checks_under_shared_lock(monkeypatch,checks,needs_lock,ddl):
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    events=[]
    monkeypatch.setattr(schema,'_ensure_schema',Mock())
    values=iter(checks)
    def ready(*args):events.append('ready');return next(values)
    def lock(*args):events.append('lock')
    monkeypatch.setattr('tenant_schema_preflight.ready',ready)
    monkeypatch.setattr('tenant_schema_preflight.lock_schema',lock)
    cur.execute.side_effect=lambda *args:events.append('ddl')
    schema.ensure_schema(conn)
    assert events==(['ready','lock','ready']+(['ddl','ddl'] if ddl else []) if needs_lock else ['ready'])
    conn.commit.assert_not_called()
