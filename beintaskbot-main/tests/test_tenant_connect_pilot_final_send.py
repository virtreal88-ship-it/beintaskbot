"""Regression tests for the pilot check at the actual Connect send boundary."""
import ast
import json
import sys
import types
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TENANT = 'd60efb41-e736-4d1e-a7fd-bd234a668c70'
CHANNEL = '6d758c54-a4bc-4966-87a6-bbb4aa3961b3'
CONVERSATION = 'test-chat'
RUN_ID = '8a56a86a-7441-42b0-9c6d-8e2cbe431c70'
FINGERPRINT = 'signed-event-fingerprint'
AGENT = '349795dc-bdf1-49e6-a3bf-75c20c9bc131'


def load_sender():
    tree = ast.parse((ROOT / 'tenant_connect_transport.py').read_text(encoding='utf-8-sig'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'send_ai_reply_if_active')
    namespace = {'uuid': uuid, 'json': json, 'TenantPlatformError': RuntimeError,
                 '_json': json.dumps}
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'tenant_connect_transport.py', 'exec'), namespace)
    return namespace['send_ai_reply_if_active']


def test_final_send_passes_saved_run_context_to_real_pilot_gate():
    import tenant_ai_pilot as pilot
    from unittest.mock import patch
    send_fn = load_sender()
    captured, sent = {}, []
    row = {'connect_channel_id': CHANNEL, 'conversation_id': CONVERSATION,
           'secrets': b'ciphertext', 'revision': 4, 'state': 'active'}
    pilot_row = {'conversation_state': 'active', 'conversation_ai_enabled': True,
        'conversation_manual_hold': False, 'active': True, 'integration_status': 'connected',
        'tenant_status': 'active', 'company_enabled': True, 'operating_mode': 'active',
        'auto_send_enabled': True, 'emergency_stop': False, 'agent_enabled': True,
        'agent_mode': 'automatic', 'exact_agent_scope': True, 'agent_id': AGENT}

    class Cursor:
        def __init__(self): self.sql = ''; self.params = ()
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, sql, params=()): self.sql, self.params = sql, params
        def fetchone(self):
            if 'saas_ai_pilot_confirmations' in self.sql:
                captured['event_fingerprint'] = self.params[0]
                return {'valid': self.params[0] == FINGERPRINT}
            return dict(row)

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def cursor(self): return Cursor()
        def commit(self): pass
        def rollback(self): pass

    class Fernet:
        def decrypt(self, _): return b'{"api_key":"mock-key"}'

    group = types.ModuleType('tenant_ai_group_coordination')
    group.group_jid = lambda _conversation: False
    group.reserve_delivery = lambda *_: True
    group.job_allowed = lambda *_: True
    group.delivery_source = lambda _cur, _tenant, _run: {
        'agent_id': AGENT, 'event_fingerprint': FINGERPRINT, 'source_kind': 'run'}
    legacy = types.ModuleType('tenant_ai_legacy_permit')
    legacy.renew = lambda *_: None

    ns = send_fn.__globals__
    ns.update(_connect=lambda: Connection(), _schema=lambda *_: None,
              lock_dialog=lambda *_: None, _fernet=lambda: Fernet(),
              send_ai_reply=lambda *_: sent.append(True) or 'connect-message-1')
    env = {'AI_AGENTS_PILOT_TENANT_ID': TENANT,
           'AI_AGENTS_PILOT_CHANNEL_ID': CHANNEL,
           'AI_AGENTS_PILOT_CONVERSATION_ID': CONVERSATION,
           'AI_AGENTS_PILOT_SCOPE_ENABLED': 'true',
           'AI_AGENTS_AUTO_SEND_ENABLED': 'true'}
    with patch.dict(sys.modules, {'tenant_ai_group_coordination': group,
                                  'tenant_ai_legacy_permit': legacy}), \
         patch.dict('os.environ', env, clear=True), \
         patch.object(pilot, '_snapshot', return_value=(pilot_row, 'scope-fingerprint')), \
         patch.object(pilot, '_lock_scope', return_value=True), \
         patch.object(pilot, 'runtime_flag', return_value=True):
        result = send_fn(TENANT, 22, CHANNEL, CONVERSATION, 'draft', RUN_ID, 4,
                         pilot_scope_fenced=True)
        assert send_fn('other-tenant', 22, CHANNEL, CONVERSATION, 'draft', RUN_ID, 4,
                       pilot_scope_fenced=True) is None
        assert send_fn(TENANT, 22, 'other-channel', CONVERSATION, 'draft', RUN_ID, 4,
                       pilot_scope_fenced=True) is None
        assert send_fn(TENANT, 22, CHANNEL, 'other-conversation', 'draft', RUN_ID, 4,
                       pilot_scope_fenced=True) is None

    assert result == 'connect-message-1'
    assert sent == [True]
    assert captured['event_fingerprint'] == FINGERPRINT


def test_final_send_blocks_missing_saved_fingerprint_and_scope_mismatch():
    send_fn = load_sender()
    sent = []
    row = {'connect_channel_id': CHANNEL, 'conversation_id': CONVERSATION,
           'secrets': b'ciphertext', 'revision': 4, 'state': 'active'}
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, *_): pass
        def fetchone(self): return dict(row)
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def cursor(self): return Cursor()
        def commit(self): pass
        def rollback(self): pass
    class Fernet:
        def decrypt(self, _): return b'{"api_key":"mock-key"}'
    group = types.ModuleType('tenant_ai_group_coordination')
    group.group_jid = lambda _conversation: False
    group.reserve_delivery = lambda *_: True
    group.job_allowed = lambda *_: True
    group.delivery_source = lambda *_: None
    pilot = types.ModuleType('tenant_ai_pilot')
    def deny(context, connection=None):
        assert context['event_fingerprint'] == ''
        assert context['provider'] == 'connect'
        return False
    pilot.automatic_send_allowed = deny
    legacy = types.ModuleType('tenant_ai_legacy_permit')
    legacy.renew = lambda *_: None
    ns = send_fn.__globals__
    ns.update(_connect=lambda: Connection(), _schema=lambda *_: None,
              lock_dialog=lambda *_: None, _fernet=lambda: Fernet(),
              send_ai_reply=lambda *_: sent.append(True) or 'unexpected')
    with patch.dict(sys.modules, {'tenant_ai_group_coordination': group,
                                  'tenant_ai_pilot': pilot,
                                  'tenant_ai_legacy_permit': legacy}):
        result = send_fn(TENANT, 22, CHANNEL, CONVERSATION, 'draft', RUN_ID, 4,
                         pilot_scope_fenced=True)
    assert result is None
    assert sent == []


def test_worker_carries_processing_pilot_fence_to_final_send():
    tree = ast.parse((ROOT / 'tenant_ai_agent_worker.py').read_text(encoding='utf-8-sig'))
    worker = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == 'process_connect_one')
    call = next(n for n in ast.walk(worker) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == 'send_ai_reply_if_active')
    options = next(n.value for n in ast.walk(worker) if isinstance(n, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'send_options'
                           for target in n.targets))
    assert isinstance(options, ast.Dict)
    keys = [key.value for key in options.keys if isinstance(key, ast.Constant)]
    assert 'pilot_scope_fenced' in keys
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == 'send_ai_reply_if_active'
               and any(keyword.arg is None and isinstance(keyword.value, ast.Name)
                       and keyword.value.id == 'send_options' for keyword in n.keywords)
               for n in ast.walk(worker))


def test_saved_delivery_source_selects_persisted_run_fingerprint_and_agent():
    import tenant_ai_group_coordination as coordination
    class Cursor:
        def execute(self, sql, params): self.sql, self.params = sql, params
        def fetchall(self):
            return [{'agent_id': AGENT, 'event_fingerprint': FINGERPRINT,
                     'payload': {'message_id': 'source-message'}, 'source_kind': 'run'}]
    cur = Cursor()
    result = coordination.delivery_source(cur, TENANT, RUN_ID)
    assert result['event_fingerprint'] == FINGERPRINT
    assert result['agent_id'] == AGENT
    assert 'r.event_fingerprint' in cur.sql
    assert 'd.source_fingerprint AS event_fingerprint' in cur.sql
    assert cur.params == (TENANT, RUN_ID, TENANT, RUN_ID)
