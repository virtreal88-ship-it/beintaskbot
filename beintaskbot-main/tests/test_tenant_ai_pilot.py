from unittest.mock import Mock, patch

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


def test_expected_human_active_handoff_transition_keeps_scope_identity_stable():
    import tenant_ai_pilot as pilot
    scope={'tenant_id':TENANT,'channel_id':CHANNEL,'conversation_id':'test-chat'}
    base={'conversation_id':scope['conversation_id'],'scopes':[{'provider':'connect','channel_id':scope['channel_id']}],
      'active':True,'channel_version':'channel-v1','integration_status':'connected','integration_version':'integration-v1',
      'conversation_lead_id':41,'conversation_ai_enabled':True,'conversation_manual_hold':False,
      'conversation_state':'human','handoff_reason':'ai_developer_consultation:missing_knowledge',
      'company_enabled':True,'auto_send_enabled':False,'emergency_stop':False,'settings_version':'settings-v1',
      'tenant_status':'active','agent_id':'agent-1','agent_enabled':True,'agent_mode':'manual',
      'agent_version':1,'agent_settings_version':'agent-v1'}
    class Cursor:
        def __init__(self,row):self.row=row
        def execute(self,*_):pass
        def fetchone(self):return dict(self.row)
    with patch.dict('os.environ',{
        'AI_AGENTS_PILOT_TENANT_ID':scope['tenant_id'],
        'AI_AGENTS_PILOT_CHANNEL_ID':scope['channel_id'],
        'AI_AGENTS_PILOT_CONVERSATION_ID':scope['conversation_id']},clear=True):
        _row,human=pilot._snapshot(Cursor(base),scope['tenant_id'],scope)
        active_row={**base,'conversation_state':'active','handoff_reason':'pilot_developer_followup'}
        _row,active=pilot._snapshot(Cursor(active_row),scope['tenant_id'],scope)
        assert human==active
        assert pilot._snapshot(Cursor({**base,'conversation_manual_hold':True}),scope['tenant_id'],scope)[1]!=human


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


def _exact_pilot_env(**extra):
    return {
        'AI_AGENTS_PILOT_TENANT_ID': pilot.SCOPED_LIVE_TENANT,
        'AI_AGENTS_PILOT_CHANNEL_ID': pilot.SCOPED_LIVE_CHANNEL,
        'AI_AGENTS_PILOT_CONVERSATION_ID': pilot.SCOPED_LIVE_CUSTOMER,
        'AI_AGENTS_PILOT_GROUP_CONVERSATION_ID': pilot.SCOPED_LIVE_GROUP,
        'AI_AGENTS_SCOPED_LIVE_ENABLED': 'false',
        'AI_AGENTS_PILOT_SCOPE_ENABLED': 'false',
        **extra,
    }


def _pilot_resume_row(state='human'):
    return {
        'conversation_id': pilot.SCOPED_LIVE_CUSTOMER,
        'conversation_state': state,
        'conversation_ai_enabled': True,
        'conversation_manual_hold': False,
        'active': True,
        'integration_status': 'connected',
        'tenant_status': 'active',
        'company_enabled': True,
        'emergency_stop': False,
        'agent_id': pilot.SCOPED_LIVE_AGENT,
        'agent_enabled': True,
        'agent_mode': 'manual',
        'exact_agent_scope': True,
    }


def test_owner_can_resume_only_exact_pilot_dialog_while_both_guards_are_off(monkeypatch):
    class Cursor:
        def __init__(self): self.executed=[]
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, sql, params=None): self.executed.append((sql,params))
        def fetchone(self): return {'role':'owner'}
    class Connection:
        def __init__(self): self.cur=Cursor(); self.committed=False
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def cursor(self): return self.cur
        def commit(self): self.committed=True
    conn=Connection()
    resumed=[]
    monkeypatch.setattr('tenant_platform._connect',lambda:conn)
    monkeypatch.setattr('tenant_ai_agents_store._authorize',lambda cur,profile:(pilot.SCOPED_LIVE_TENANT,7))
    monkeypatch.setattr(pilot,'_snapshot',lambda *_:(_pilot_resume_row(),'fingerprint'))
    monkeypatch.setattr('tenant_ai_agents_store.set_conversation_state',lambda *args:resumed.append(args))
    monkeypatch.setattr(pilot,'preflight',lambda _profile:{'scope_configured':True,'handoff':'active'})
    with patch.dict('os.environ',_exact_pilot_env(),clear=True):
        result=pilot.resume_pilot_dialog({'tenant_id':pilot.SCOPED_LIVE_TENANT,'telegram_id':7},{'confirmed':True})
    assert result['handoff']=='active' and conn.committed
    assert len(resumed)==1
    assert resumed[0][1:]==(pilot.SCOPED_LIVE_CUSTOMER,'active','pilot_preparation_resume','connect',pilot.SCOPED_LIVE_CHANNEL)


def test_pilot_preparation_resume_rejects_admin_or_live_guard(monkeypatch):
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, *_): pass
        def fetchone(self): return {'role':'admin'}
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def cursor(self): return Cursor()
    monkeypatch.setattr('tenant_platform._connect',lambda:Connection())
    monkeypatch.setattr('tenant_ai_agents_store._authorize',lambda *_:(pilot.SCOPED_LIVE_TENANT,7))
    monkeypatch.setattr(pilot,'_snapshot',lambda *_:(_pilot_resume_row(),'fingerprint'))
    with patch.dict('os.environ',_exact_pilot_env(),clear=True):
        try: pilot.resume_pilot_dialog({'tenant_id':pilot.SCOPED_LIVE_TENANT,'telegram_id':7},{'confirmed':True})
        except TenantLinearError as error: assert error.status==403
        else: raise AssertionError('only the database-verified owner may resume')
    with patch.dict('os.environ',_exact_pilot_env(AI_AGENTS_SCOPED_LIVE_ENABLED='true'),clear=True):
        try: pilot.resume_pilot_dialog({'tenant_id':pilot.SCOPED_LIVE_TENANT,'telegram_id':7},{'confirmed':True})
        except TenantLinearError as error: assert error.status==409
        else: raise AssertionError('preparation resume must reject a live pilot guard')


def test_pilot_preparation_resume_preserves_legacy_route_conflict(monkeypatch):
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, *_): pass
        def fetchone(self): return {'role':'owner'}
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def cursor(self): return Cursor()
        def commit(self): pass
    monkeypatch.setattr('tenant_platform._connect',lambda:Connection())
    monkeypatch.setattr('tenant_ai_agents_store._authorize',lambda *_:(pilot.SCOPED_LIVE_TENANT,7))
    monkeypatch.setattr(pilot,'_snapshot',lambda *_:(_pilot_resume_row(),'fingerprint'))
    conflict=TenantLinearError('Legacy dialogue channel is ambiguous; manual resolution is required.',409)
    monkeypatch.setattr('tenant_ai_agents_store.set_conversation_state',Mock(side_effect=conflict))
    with patch.dict('os.environ',_exact_pilot_env(),clear=True):
        try: pilot.resume_pilot_dialog({'tenant_id':pilot.SCOPED_LIVE_TENANT,'telegram_id':7},{'confirmed':True})
        except TenantLinearError as error: assert error.status==409 and 'ambiguous' in str(error)
        else: raise AssertionError('the existing exact-route conflict must be preserved')


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


def test_scoped_live_group_allowlist_is_exact_and_independent_of_global_auto_send():
    group='120363431915456655@g.us'
    env={'AI_AGENTS_PILOT_TENANT_ID':pilot.SCOPED_LIVE_TENANT,
         'AI_AGENTS_PILOT_CHANNEL_ID':pilot.SCOPED_LIVE_CHANNEL,
         'AI_AGENTS_PILOT_CONVERSATION_ID':pilot.SCOPED_LIVE_CUSTOMER,
         'AI_AGENTS_PILOT_GROUP_CONVERSATION_ID':group,
         'AI_AGENTS_SCOPED_LIVE_ENABLED':'true'}
    with patch.dict('os.environ',env,clear=True):
        assert pilot.scoped_live_enabled()
        assert pilot.is_pilot_group_target(pilot.SCOPED_LIVE_TENANT,pilot.SCOPED_LIVE_CHANNEL,group)
        assert not pilot.is_pilot_group_target(pilot.SCOPED_LIVE_TENANT,pilot.SCOPED_LIVE_CHANNEL,'120363406724408631@g.us')
        assert not pilot.is_pilot_group_target(pilot.SCOPED_LIVE_TENANT,OTHER,group)
        assert not pilot.is_pilot_group_target(OTHER,pilot.SCOPED_LIVE_CHANNEL,group)
        assert not pilot._enabled('AI_AGENTS_AUTO_SEND_ENABLED')


def test_scoped_live_group_allowlist_rejects_missing_or_non_group_target():
    for group in ('', '994502072240@s.whatsapp.net', '120363406724408631@g.us'):
        env={'AI_AGENTS_PILOT_TENANT_ID':pilot.SCOPED_LIVE_TENANT,
             'AI_AGENTS_PILOT_CHANNEL_ID':pilot.SCOPED_LIVE_CHANNEL,
             'AI_AGENTS_PILOT_CONVERSATION_ID':pilot.SCOPED_LIVE_CUSTOMER,
             'AI_AGENTS_PILOT_GROUP_CONVERSATION_ID':group,
             'AI_AGENTS_SCOPED_LIVE_ENABLED':'true'}
        with patch.dict('os.environ',env,clear=True):
            assert not pilot.scoped_live_enabled()
            assert not pilot.is_pilot_group_target(pilot.SCOPED_LIVE_TENANT,pilot.SCOPED_LIVE_CHANNEL,group)


def test_scoped_live_customer_can_pass_final_gate_while_saved_modes_stay_manual(monkeypatch):
    customer=pilot.SCOPED_LIVE_CUSTOMER
    tenant=pilot.SCOPED_LIVE_TENANT
    channel=pilot.SCOPED_LIVE_CHANNEL
    job={'provider':'connect','tenant_id':tenant,'channel_id':channel,
         'conversation_id':customer,'event_fingerprint':'pilot-event','agent_id':'agent'}
    row={'conversation_state':'active','conversation_ai_enabled':True,'conversation_manual_hold':False,
         'active':True,'integration_status':'connected','tenant_status':'active','company_enabled':True,
         'auto_send_enabled':False,'emergency_stop':False,'agent_enabled':True,'agent_mode':'manual',
         'exact_agent_scope':True,'agent_id':'agent'}
    monkeypatch.setattr(pilot,'_snapshot',lambda *_:(row,'pilot-fingerprint'))
    monkeypatch.setattr(pilot,'_lock_scope',lambda *_:True)
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*_):return False
        def execute(self,sql,_params=None):self.sql=sql
        def fetchone(self):return {'valid':True}
    class Connection:
        def cursor(self):return Cursor()
    monkeypatch.setattr(pilot,'runtime_flag',lambda name:name=='AI_AGENTS_ENABLED')
    env={'AI_AGENTS_PILOT_TENANT_ID':tenant,'AI_AGENTS_PILOT_CHANNEL_ID':channel,
         'AI_AGENTS_PILOT_CONVERSATION_ID':customer,
         'AI_AGENTS_PILOT_GROUP_CONVERSATION_ID':pilot.SCOPED_LIVE_GROUP,
         'AI_AGENTS_SCOPED_LIVE_ENABLED':'true'}
    with patch.dict('os.environ',env,clear=True):
        assert pilot.automatic_send_allowed(job,Connection())
        assert pilot.processing_allowed(job,Connection())
        assert pilot.processing_allowed({'provider':'connect','tenant_id':tenant,'channel_id':channel,
            'conversation_id':'ordinary-client','event_fingerprint':'ordinary-event'},Connection())


def test_worker_config_overrides_modes_only_for_exact_scoped_customer(monkeypatch):
    import tenant_ai_agent_run_store as store
    customer=pilot.SCOPED_LIVE_CUSTOMER;tenant=pilot.SCOPED_LIVE_TENANT;channel=pilot.SCOPED_LIVE_CHANNEL
    job={'provider':'connect','tenant_id':tenant,'channel_id':channel,'conversation_id':customer,
         'agent_id':'agent','event_fingerprint':'event','lease_token':'lease','revision':1}
    row={'role':'primary','agent_enabled':True,'scopes':[{'provider':'connect','channel_id':channel}],
         'agent_model':'model','safety':{},'response_policy':{},'schedule':{},'mode':'manual',
         'settings_enabled':True,'operating_mode':'simulation','auto_send_enabled':False,
         'emergency_stop':False,'crm_actions_enabled':False,'native_crm_actions_enabled':False,
         'metadata':{'enabled':True,'consent':True,'model':'model','models':['model']},
         'secrets':b'credential','tenant_status':'active','conversation_state':'active'}
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*_):return False
        def execute(self,*_):pass
        def fetchone(self):return row
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*_):return False
        def cursor(self):return Cursor()
    monkeypatch.setattr(store,'_connect',lambda:Connection())
    monkeypatch.setattr(store,'ensure_schema',lambda *_:None)
    monkeypatch.setattr(store,'platform_service_ready',lambda:False)
    monkeypatch.setattr(store,'platform_key_ready',lambda *_:True)
    monkeypatch.setattr(store,'platform_model_allowed',lambda *_:True)
    env={'AI_AGENTS_PILOT_TENANT_ID':tenant,'AI_AGENTS_PILOT_CHANNEL_ID':channel,
         'AI_AGENTS_PILOT_CONVERSATION_ID':customer,
         'AI_AGENTS_PILOT_GROUP_CONVERSATION_ID':pilot.SCOPED_LIVE_GROUP,
         'AI_AGENTS_SCOPED_LIVE_ENABLED':'true'}
    with patch.dict('os.environ',env,clear=True):
        config=store.load_agent_config(job)
        assert config['mode']=='automatic' and config['auto_send_enabled'] is True
        assert row['mode']=='manual' and row['operating_mode']=='simulation' and row['auto_send_enabled'] is False
        other=store.load_agent_config({**job,'conversation_id':'ordinary-client'})
        assert other is None




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
