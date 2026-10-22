from unittest.mock import patch

import tenant_ai_pilot as pilot
from tenant_linear_policy import TenantLinearError

TENANT = 'd60efb41-e736-4d1e-a7fd-bd234a668c70'
CHANNEL = '6d758c54-a4bc-4966-87a6-bbb4aa3961b3'
OTHER = '349795dc-bdf1-49e6-a3bf-75c20c9bc131'
JOB = {'provider':'connect', 'tenant_id': TENANT, 'channel_id': CHANNEL, 'conversation_id': 'test-chat', 'event_fingerprint':'evt'}


def pilot_env(**extra):
    return {'AI_AGENTS_PILOT_TENANT_ID': TENANT,
            'AI_AGENTS_PILOT_CHANNEL_ID': CHANNEL,
            'AI_AGENTS_PILOT_CONVERSATION_ID': 'test-chat', **extra}


def test_unconfigured_pilot_does_not_change_existing_dialogs():
    with patch.dict('os.environ', {}, clear=True):
        assert pilot.automatic_send_allowed(JOB)
        assert pilot.automatic_send_allowed({**JOB, 'tenant_id': OTHER})


def test_configured_dialog_is_blocked_until_pilot_is_armed_and_confirmed():
    with patch.dict('os.environ', pilot_env(), clear=True):
        assert not pilot.automatic_send_allowed(JOB)
        # Other conversations retain existing policy until the guard is armed.
        assert pilot.automatic_send_allowed({**JOB, 'conversation_id': 'other-chat'})


def test_armed_pilot_fails_closed_for_scope_mismatch_or_missing_confirmation(monkeypatch):
    row = {'conversation_state': 'active', 'conversation_ai_enabled': True, 'conversation_manual_hold': False,
           'active': True, 'integration_status': 'connected', 'tenant_status': 'active',
           'company_enabled': True, 'auto_send_enabled': True,
           'emergency_stop': False, 'agent_enabled': True, 'agent_mode': 'automatic', 'exact_agent_scope': True}
    monkeypatch.setattr(pilot, '_snapshot', lambda *_: (row, 'fp'))

    class Cursor:
        def __init__(self, valid): self.valid = valid; self.sql = ''
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, sql, _params=None): self.sql = sql
        def fetchone(self): return {'valid': self.valid}

    class Connection:
        def __init__(self, valid): self.cursor_obj = Cursor(valid)
        def cursor(self): return self.cursor_obj

    with patch.dict('os.environ', pilot_env(AI_AGENTS_PILOT_SCOPE_ENABLED='true', AI_AGENTS_AUTO_SEND_ENABLED='true'), clear=True):
        connection = Connection(True)
        assert pilot.automatic_send_allowed(JOB, connection)
        assert 'received_at>=GREATEST' in connection.cursor_obj.sql
        assert not pilot.automatic_send_allowed({**JOB, 'channel_id': OTHER}, Connection(True))
        assert not pilot.automatic_send_allowed({**JOB, 'tenant_id': OTHER}, Connection(True))
        assert not pilot.automatic_send_allowed({**JOB, 'conversation_id': 'other-chat'}, Connection(True))
        assert not pilot.automatic_send_allowed(JOB, Connection(False))  # expired, stale fingerprint, or absent


def test_armed_pilot_blocks_unknown_scope_and_handoff(monkeypatch):
    with patch.dict('os.environ', {'AI_AGENTS_PILOT_SCOPE_ENABLED': 'true'}, clear=True):
        assert not pilot.automatic_send_allowed(JOB)

    row = {'conversation_state': 'human', 'conversation_ai_enabled': True, 'conversation_manual_hold': False,
           'active': True, 'integration_status': 'connected', 'tenant_status': 'active',
           'company_enabled': True, 'auto_send_enabled': True,
           'emergency_stop': False, 'agent_enabled': True, 'agent_mode': 'automatic', 'exact_agent_scope': True}
    monkeypatch.setattr(pilot, '_snapshot', lambda *_: (row, 'fp'))
    with patch.dict('os.environ', pilot_env(AI_AGENTS_PILOT_SCOPE_ENABLED='true', AI_AGENTS_AUTO_SEND_ENABLED='true'), clear=True):
        assert not pilot.automatic_send_allowed(JOB, object())


def test_channel_or_agent_setting_change_invalidates_scope_fingerprint():
    scope = {'tenant_id': TENANT, 'channel_id': CHANNEL, 'conversation_id': 'test-chat'}
    class Cursor:
        def __init__(self, row): self.row = row
        def execute(self, *_): pass
        def fetchone(self): return dict(self.row)
    common = {'conversation_id':'test-chat','active':True,'channel_version':'v1',
        'integration_status':'connected','integration_version':'i1','conversation_ai_enabled':True,
        'conversation_manual_hold':False,'conversation_state':'active','company_enabled':True,
        'auto_send_enabled':True,'emergency_stop':False,'tenant_status':'active',
        'agent_id':'agent-1','agent_enabled':True,'agent_mode':'automatic','agent_version':3,
        'agent_settings_version':'a1','scopes':[{'provider':'connect','channel_id':CHANNEL}]}
    _, initial = pilot._snapshot(Cursor(common), TENANT, scope)
    for key, value in [('channel_version','v2'),('agent_settings_version','a2')]:
        changed = dict(common, **{key:value})
        _, fingerprint = pilot._snapshot(Cursor(changed), TENANT, scope)
        assert fingerprint != initial


def test_preflight_marks_kwid_as_unknown_until_fresh_manual_attestation(monkeypatch):
    row = {'active': True, 'integration_status': 'connected', 'conversation_id': 'test-chat',
           'conversation_state': 'active', 'company_enabled': False,
           'auto_send_enabled': False, 'emergency_stop': True, 'tenant_status': 'active',
           'agent_id': None, 'agent_enabled': False, 'agent_mode': 'manual', 'scopes': []}

    class Cursor:
        def __init__(self): self.sql = []
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, sql, _params=None): self.sql.append(sql)
        def fetchone(self): return {}

    class Connection:
        def __init__(self): self.cur = Cursor()
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def cursor(self): return self.cur

    conn = Connection()
    monkeypatch.setattr('tenant_platform._connect', lambda: conn)
    monkeypatch.setattr('tenant_ai_agents_store._authorize', lambda _cur, _profile: (TENANT, 7))
    monkeypatch.setattr(pilot, '_snapshot', lambda *_: (row, 'fingerprint'))
    monkeypatch.setattr(pilot, '_confirmation', lambda *_: ({}, {}))
    with patch.dict('os.environ', pilot_env(), clear=True):
        result = pilot.preflight({'tenant_id': TENANT, 'telegram_id': 7})
    assert result['competing_handler'] == {'state': 'unknown', 'source': 'manual_confirmation_required'}
    assert result['kwid_confirmation']['state'] == 'missing'
    assert result['separate_owner_approval']['state'] == 'missing'
    assert not result['ready'] and 'kwid_confirmation_missing_expired_or_scope_changed' in result['blockers']
    assert all(sql.lstrip().upper().startswith('SELECT') for sql in conn.cur.sql)


def test_owner_approval_is_a_separate_step_and_not_a_runtime_switch(monkeypatch):
    # The owner decision only writes its own attestation; it never mutates a
    # runtime flag. The store/API policy itself is exercised through SQL below.
    with patch.dict('os.environ', pilot_env(), clear=True):
        assert not pilot._enabled('AI_AGENTS_PILOT_SCOPE_ENABLED')
        assert not pilot._enabled('AI_AGENTS_AUTO_SEND_ENABLED')
    assert pilot.CONFIRMATION_MINUTES <= 15


def test_admin_attestation_is_short_lived_and_clears_prior_owner_approval(monkeypatch):
    class Cursor:
        def __init__(self): self.sql = []; self.params = []
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, sql, params=None): self.sql.append(sql); self.params.append(params)
    class Connection:
        def __init__(self): self.cur = Cursor(); self.committed = False
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def cursor(self): return self.cur
        def commit(self): self.committed = True
    conn = Connection()
    monkeypatch.setattr('tenant_ai_agents_schema.ensure_schema', lambda _conn: None)
    monkeypatch.setattr('tenant_platform._connect', lambda: conn)
    monkeypatch.setattr(pilot, '_authorized_scope', lambda *_: (TENANT, 7,
        {'tenant_id':TENANT,'channel_id':CHANNEL,'conversation_id':'test-chat'}, {}, 'fp'))
    monkeypatch.setattr(pilot, 'preflight', lambda _profile: {'ready':False})
    result = pilot.confirm_kwid_disabled({'tenant_id':TENANT,'telegram_id':7}, {'confirmed':True})
    assert result == {'ready':False} and conn.committed
    sql = conn.cur.sql[0].lower()
    assert 'on conflict(tenant_id) do update' in sql and 'owner_approved_by=null' in sql
    assert 'interval' in sql and conn.cur.params[0] == (TENANT, CHANNEL, 'test-chat', 'fp', 7, 15)


def test_owner_approval_rejects_admin_and_is_separate_from_kwid_attestation(monkeypatch):
    class Cursor:
        def __init__(self, role): self.role=role; self.sql=[]
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, sql, _params=None): self.sql.append(sql)
        def fetchone(self):
            if 'SELECT role' in self.sql[-1]: return {'role':self.role}
            if 'RETURNING tenant_id' in self.sql[-1]: return {'tenant_id':TENANT}
            return None
    class Connection:
        def __init__(self, role): self.cur=Cursor(role); self.committed=False
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def cursor(self): return self.cur
        def commit(self): self.committed=True
    monkeypatch.setattr('tenant_ai_agents_schema.ensure_schema', lambda _conn: None)
    monkeypatch.setattr(pilot, '_authorized_scope', lambda *_: (TENANT, 7,
        {'tenant_id':TENANT,'channel_id':CHANNEL,'conversation_id':'test-chat'}, {}, 'fp'))
    monkeypatch.setattr(pilot, 'preflight', lambda _profile: {'ready':False})
    admin_conn=Connection('admin')
    monkeypatch.setattr('tenant_platform._connect', lambda: admin_conn)
    try:
        pilot.approve_pilot({'tenant_id':TENANT,'telegram_id':7}, {'approved':True})
    except TenantLinearError as error:
        assert error.status == 403
    else:
        raise AssertionError('admin must not issue separate owner approval')
    assert not admin_conn.committed and not any('UPDATE' in sql for sql in admin_conn.cur.sql)

    owner_conn=Connection('owner')
    monkeypatch.setattr('tenant_platform._connect', lambda: owner_conn)
    assert pilot.approve_pilot({'tenant_id':TENANT,'telegram_id':7}, {'approved':True}) == {'ready':False}
    assert owner_conn.committed and 'LEAST(kwid_expires_at' in owner_conn.cur.sql[-1]


def test_active_exact_scope_fails_closed_when_confirmation_table_is_missing(monkeypatch):
    row = {'conversation_state':'active','conversation_ai_enabled':True,'conversation_manual_hold':False,
        'active':True,'integration_status':'connected','tenant_status':'active','company_enabled':True,
        'auto_send_enabled':True,'emergency_stop':False,'agent_enabled':True,
        'agent_mode':'automatic','exact_agent_scope':True}
    monkeypatch.setattr(pilot, '_snapshot', lambda *_: (row, 'fingerprint'))
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, sql, *_):
            if 'FROM saas_ai_pilot_confirmations' in sql:
                raise RuntimeError('relation does not exist')
        def fetchone(self): return {'exists':True}
    class Connection:
        def cursor(self): return Cursor()
    with patch.dict('os.environ', pilot_env(AI_AGENTS_PILOT_SCOPE_ENABLED='true',
            AI_AGENTS_AUTO_SEND_ENABLED='true'), clear=True):
        assert not pilot.processing_allowed(JOB, Connection())


def test_processing_gate_blocks_selected_dialog_while_unarmed_but_preserves_other_agents():
    with patch.dict('os.environ', pilot_env(), clear=True):
        assert not pilot.processing_allowed(JOB)
        assert pilot.processing_allowed({**JOB, 'conversation_id':'another-chat'})
        assert pilot.processing_allowed({**JOB, 'tenant_id':OTHER})


def test_processing_gate_is_global_fail_closed_when_armed(monkeypatch):
    with patch.dict('os.environ', pilot_env(AI_AGENTS_PILOT_SCOPE_ENABLED='true'), clear=True):
        assert not pilot.processing_allowed({**JOB, 'conversation_id':'another-chat'})
        assert not pilot.processing_allowed({**JOB, 'provider':'kommo'})
        assert not pilot.processing_allowed({**JOB, 'channel_id':OTHER})
        monkeypatch.setattr(pilot, 'automatic_send_allowed', lambda job, connection=None: True)
        assert pilot.processing_allowed(JOB)
        monkeypatch.setattr(pilot, 'automatic_send_allowed', lambda job, connection=None: False)
        assert not pilot.processing_allowed(JOB)


def test_confirmation_schema_migration_is_idempotent_additive_ddl():
    class Cursor:
        def __init__(self): self.statements=[]
        def execute(self, sql, *_): self.statements.append(sql)
    cur=Cursor()
    pilot.schema(cur)
    pilot.schema(cur)
    ddl=[sql for sql in cur.statements if 'CREATE TABLE IF NOT EXISTS saas_ai_pilot_confirmations' in sql]
    assert len(ddl)==2
    sql=ddl[0]
    for field in ('tenant_id UUID PRIMARY KEY','channel_id UUID NOT NULL','conversation_id TEXT NOT NULL',
                  'scope_fingerprint TEXT NOT NULL','kwid_confirmed_at TIMESTAMPTZ','kwid_expires_at TIMESTAMPTZ',
                  'owner_approved_at TIMESTAMPTZ','owner_expires_at TIMESTAMPTZ','kwid_confirmed_by IS NOT NULL AND owner_expires_at'):
        assert field in sql




def test_final_gate_rejects_agent_id_mismatch(monkeypatch):
    row = {'conversation_state': 'active', 'conversation_ai_enabled': True, 'conversation_manual_hold': False,
           'active': True, 'integration_status': 'connected', 'tenant_status': 'active',
           'company_enabled': True, 'auto_send_enabled': True,
           'emergency_stop': False, 'agent_enabled': True, 'agent_mode': 'automatic',
           'exact_agent_scope': True, 'agent_id': 'agent-ok'}
    monkeypatch.setattr(pilot, '_snapshot', lambda *_: (row, 'fp'))

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, *_): pass
        def fetchone(self): return {'valid': True}

    class Connection:
        def cursor(self): return Cursor()

    with patch.dict('os.environ', pilot_env(AI_AGENTS_PILOT_SCOPE_ENABLED='true',
                                             AI_AGENTS_AUTO_SEND_ENABLED='true'), clear=True):
        assert pilot.automatic_send_allowed({**JOB, 'agent_id': 'agent-ok'}, Connection())
        assert not pilot.automatic_send_allowed({**JOB, 'agent_id': 'agent-other'}, Connection())


def test_claimed_pilot_run_stays_blocked_if_scope_is_removed_before_send():
    job = {**JOB, '_pilot_scope_fenced': True}
    with patch.dict('os.environ', {}, clear=True):
        assert not pilot.automatic_send_allowed(job)
