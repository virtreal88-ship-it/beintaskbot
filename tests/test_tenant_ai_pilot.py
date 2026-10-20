import uuid
from unittest.mock import patch

import tenant_ai_pilot as pilot

TENANT='d60efb41-e736-4d1e-a7fd-bd234a668c70'
CHANNEL='6d758c54-a4bc-4966-87a6-bbb4aa3961b3'
OTHER='349795dc-bdf1-49e6-a3bf-75c20c9bc131'


def scope_env(**extra):
    return {'AI_AGENTS_PILOT_SCOPE_ENABLED':'true','AI_AGENTS_PILOT_TENANT_ID':TENANT,
            'AI_AGENTS_PILOT_CHANNEL_ID':CHANNEL,'AI_AGENTS_PILOT_CONVERSATION_ID':'test-chat',**extra}


def test_existing_automatic_send_policy_is_unchanged_when_pilot_guard_is_off():
    with patch.dict('os.environ', {}, clear=True):
        assert pilot.automatic_send_allowed({'tenant_id':TENANT,'channel_id':CHANNEL,'conversation_id':'other'})


def test_armed_guard_allows_only_the_exact_dialog_after_competing_handler_confirmation():
    env=scope_env(AI_AGENTS_PILOT_COMPETING_HANDLER_STATE='disabled')
    job={'tenant_id':TENANT,'channel_id':CHANNEL,'conversation_id':'test-chat'}
    with patch.dict('os.environ',env,clear=True):
        assert pilot.automatic_send_allowed(job)
        assert not pilot.automatic_send_allowed({**job,'conversation_id':'another-chat'})
        assert not pilot.automatic_send_allowed({**job,'tenant_id':OTHER})
        assert not pilot.automatic_send_allowed({**job,'channel_id':OTHER})


def test_unknown_competing_handler_or_incomplete_scope_fails_closed():
    job={'tenant_id':TENANT,'channel_id':CHANNEL,'conversation_id':'test-chat'}
    for env in (scope_env(), {**scope_env(AI_AGENTS_PILOT_COMPETING_HANDLER_STATE='disabled'),
                              'AI_AGENTS_PILOT_CHANNEL_ID':'not-a-uuid'}):
        with patch.dict('os.environ',env,clear=True):
            assert not pilot.automatic_send_allowed(job)


def test_preflight_is_read_only_and_unknown_kwid_blocks_readiness(monkeypatch):
    class Cursor:
        row = {'active': True, 'integration_status': 'connected', 'conversation_id': 'test-chat',
               'conversation_state': 'active', 'handoff_reason': '', 'company_enabled': True,
               'operating_mode': 'active', 'auto_send_enabled': True, 'emergency_stop': False,
               'tenant_status': 'active', 'agent_id': 'agent-id', 'agent_enabled': True,
               'agent_mode': 'automatic', 'scopes': [{'provider': 'connect', 'channel_id': CHANNEL}]}
        def __init__(self): self.sql=[]
        def __enter__(self): return self
        def __exit__(self,*_): return False
        def execute(self,sql,params=None): self.sql.append(sql)
        def fetchone(self): return self.row
    class Conn:
        def __init__(self): self.cur=Cursor()
        def __enter__(self): return self
        def __exit__(self,*_): return False
        def cursor(self): return self.cur
    conn=Conn()
    monkeypatch.setattr('tenant_platform._connect',lambda:conn)
    monkeypatch.setattr('tenant_ai_agents_store._authorize',lambda cur,profile:(TENANT,7))
    monkeypatch.setenv('AI_AGENTS_PILOT_SCOPE_ENABLED','true')
    monkeypatch.setenv('AI_AGENTS_PILOT_TENANT_ID',TENANT)
    monkeypatch.setenv('AI_AGENTS_PILOT_CHANNEL_ID',CHANNEL)
    monkeypatch.setenv('AI_AGENTS_PILOT_CONVERSATION_ID','test-chat')
    monkeypatch.delenv('AI_AGENTS_PILOT_COMPETING_HANDLER_STATE',raising=False)
    result=pilot.preflight({'tenant_id':TENANT,'telegram_id':7})
    assert not result['ready']
    assert result['competing_handler']=='unknown'
    assert result['competing_handler_source']=='unavailable'
    assert 'competing_handler_not_confirmed_disabled' in result['blockers']
    assert conn.cur.sql and all(sql.lstrip().upper().startswith('SELECT') for sql in conn.cur.sql)
