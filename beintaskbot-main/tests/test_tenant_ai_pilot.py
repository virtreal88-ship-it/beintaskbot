from unittest.mock import Mock, patch
import pytest

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




def test_episode_cutoff_excludes_old_history_but_keeps_fresh_inbound():
    from datetime import datetime, timezone
    from tenant_ai_agent_worker import _after_pilot_cutoff
    cutoff=datetime(2026,10,31,14,0,tzinfo=timezone.utc)
    rows=[{'external_id':'old','happened_at':'2026-10-31T13:59:59+00:00','body':'old case'},
          {'external_id':'new','happened_at':'2026-10-31T14:00:01+00:00','body':'fresh question'}]
    assert _after_pilot_cutoff(rows,cutoff)==[rows[1]]
    assert rows[0]['body']=='old case'  # cutoff filtering does not mutate stored/history data


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


def test_preparation_scope_uses_server_selected_direct_peer_not_retired_test_number():
    replacement='994102135103@s.whatsapp.net'
    with patch.dict('os.environ',_exact_pilot_env(AI_AGENTS_PILOT_CONVERSATION_ID=replacement),clear=True):
        assert pilot.configured_group_scope()['conversation_id']==pilot.SCOPED_LIVE_GROUP
        assert pilot.is_pilot_customer_target(pilot.SCOPED_LIVE_TENANT,pilot.SCOPED_LIVE_CHANNEL,replacement) is False
        with patch.dict('os.environ',{'AI_AGENTS_SCOPED_LIVE_ENABLED':'true'},clear=False):
            assert pilot.is_pilot_customer_target(pilot.SCOPED_LIVE_TENANT,pilot.SCOPED_LIVE_CHANNEL,replacement)
        assert not pilot.configured_group_scope() is None
    for invalid in ('120363431915456655@g.us','not-a-peer','123@s.whatsapp.net'):
        with patch.dict('os.environ',_exact_pilot_env(AI_AGENTS_PILOT_CONVERSATION_ID=invalid),clear=True):
            assert pilot.configured_group_scope() is None


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
