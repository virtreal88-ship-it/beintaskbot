import json
from datetime import datetime, timezone
import pytest
from test_tenant_support_inbox import database, db, A, B, CA, CB, AGENT, PROFILE
from test_integrated_connect_release import flow
from test_connect_channel_isolation import channels, AGENT2
import tenant_ai_group_coordination as groups
import tenant_ai_agents_store as agents
import tenant_member_store as member_store
import tenant_ai_agent_connect as connect
import tenant_operator_assignment as assignment
from tenant_linear_policy import TenantLinearError
from tenant_platform_core import TenantPlatformError

G='120363431037910651@g.us'

@pytest.fixture
def group(channels,monkeypatch):
    f=channels; d=f['db'];monkeypatch.setattr(groups,'_connect',lambda:d);monkeypatch.setattr(assignment,'_connect',lambda:d)
    for channel,agent in [(CA,AGENT),(CB,AGENT2)]:
        d.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind,lead_id) VALUES(%s::uuid,%s::uuid,%s,'group',%s)",(A,channel,G,1001 if channel==CA else 1002))
        d.execute('UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE id=%s::uuid',(json.dumps([{'provider':'connect','channel_id':channel,'allow_group_messages':True}]),agent))
    d.commit();return f

def config(**kw):
    return {'conversation_id':G,'expected_revision':0,'assignments':[{'agent_id':AGENT,'channel_id':CA,'priority':10}],**kw}

def test_no_assignment_fails_closed(group):
    with group['db'].cursor() as c:
        assert groups.authority(c,A,CA,G) is None
        assert groups.admit(c,A,CA,G,'SAME_EVENT') is None

def test_employee_phone_is_tenant_scoped_and_requires_explicit_recognition(group):
    d=group['db']
    d.execute("INSERT INTO saas_tenant_member_whatsapp_numbers(tenant_id,telegram_id,phone_digits,recognition_enabled,created_by) VALUES(%s::uuid,11,'994501234567',TRUE,11)",(A,))
    d.execute("INSERT INTO saas_tenant_member_whatsapp_numbers(tenant_id,telegram_id,phone_digits,recognition_enabled,created_by) VALUES(%s::uuid,11,'994551234567',FALSE,11)",(A,))
    d.commit()
    with d.cursor() as c:
        assert groups.verified_employee(c,A,{'participantJid':'994501234567@s.whatsapp.net'})==11
        assert groups.verified_employee(c,A,{'participantJid':'994551234567@s.whatsapp.net'}) is None
        assert groups.verified_employee(c,B,{'participantJid':'994501234567@s.whatsapp.net'}) is None
        assert groups.verified_employee(c,A,{'participantJid':'994509999999@lid'}) is None

def test_quick_employee_link_is_idempotent_and_rejects_cross_member_conflict(group,monkeypatch):
    d=group['db'];monkeypatch.setattr(member_store,'_connect',lambda:d)
    monkeypatch.setattr(member_store,'_ensure_schema',lambda _conn:None)
    first=member_store.add_member_whatsapp_number(tenant_id=A,owner_id=11,telegram_id=11,phone='+994501112233')
    again=member_store.add_member_whatsapp_number(tenant_id=A,owner_id=11,telegram_id=11,phone='0501112233')
    assert first==again=={'phone':'+994501112233','recognition_enabled':True}
    with d.cursor() as cur:
        assert groups.verified_employee(cur,A,{'participantJid':'994501112233@s.whatsapp.net'})==11
    d.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,12,'operator','Second')",(A,));d.commit()
    with pytest.raises(TenantPlatformError):
        member_store.add_member_whatsapp_number(tenant_id=A,owner_id=11,telegram_id=12,phone='+994501112233')

def test_configuration_pauses_resume_and_cross_channel_dedup(group):
    d=group['db']; groups.configure(PROFILE,config())
    with d.cursor() as c:assert groups.authority(c,A,CA,G) is None
    d.commit();agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CB)
    with d.cursor() as c:
        assert groups.authority(c,A,CA,G,AGENT)
        assert groups.authority(c,A,CB,G,AGENT2) is None
        assert groups.admit(c,A,CB,G,'SAME_EVENT') is None
        assert groups.admit(c,A,CA,G,'SAME_EVENT')
        assert groups.admit(c,A,CA,G,'SAME_EVENT') is None
    current=groups.read(d.cursor(),A,G)
    unchanged=groups.configure(PROFILE,config(expected_revision=int(current['revision'])))
    assert unchanged['unchanged'] and unchanged['state']=='active' and unchanged['revision']==current['revision']
    d.commit()

def test_human_on_either_channel_fences_group_only(group):
    d=group['db'];groups.configure(PROFILE,config());agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CA)
    before=d.execute("SELECT * FROM saas_ai_conversation_state WHERE conversation_id='client'");d.commit()
    with d.cursor() as c:
        old=groups.admit(c,A,CA,G,'BEFORE_HUMAN');groups.stop(c,A,G)
        assert not groups.job_allowed(c,{'provider':'connect','tenant_id':A,'channel_id':CA,'conversation_id':G,'agent_id':AGENT,'payload':old})
    d.commit();agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CB)
    with d.cursor() as c:
        assert groups.admit(c,A,CA,G,'BEFORE_HUMAN') is None
        assert not groups.job_allowed(c,{'provider':'connect','tenant_id':A,'channel_id':CA,'conversation_id':G,'agent_id':AGENT,'payload':old})
        assert groups.admit(c,A,CA,G,'AFTER_HUMAN')
    assert d.execute("SELECT * FROM saas_ai_conversation_state WHERE conversation_id='client'")==before
    d.commit();group['send'].assert_not_called()

def test_legacy_shift_slots_are_ambiguous_and_schedule_never_selects_primary():
    slots=[{'agent_id':'day','channel_id':CA,'priority':10},{'agent_id':'night','channel_id':CA,'priority':10}]
    def agent(schedule):return {'enabled':True,'scopes':[{'provider':'connect','channel_id':CA,'allow_group_messages':True}],'schedule':schedule}
    aa={'day':agent({'timezone':'UTC','days':[0],'start':'08:00','end':'22:00'}),'night':agent({'timezone':'UTC','days':[0],'start':'22:00','end':'08:00'})}
    assert groups.choose(slots,aa) is None
    assert groups.choose([slots[0]],aa,now=datetime(2026,10,20,2,tzinfo=timezone.utc))['agent_id']=='day'
    slots[1]['priority']=100
    assert groups.choose(slots,aa) is None


def test_group_configuration_rejects_multiple_primary_agents(group):
    with pytest.raises(TenantLinearError):
        groups.configure(PROFILE,config(assignments=[{'agent_id':AGENT,'channel_id':CA},
            {'agent_id':AGENT2,'channel_id':CB}]))

@pytest.mark.parametrize('jid',['same name','123@lid','123@s.whatsapp.net','abc@g.us','120363431037910651@g.us.attacker'])
def test_unverified_identity(jid):assert groups.group_jid(jid) is None

@pytest.mark.parametrize('patch',[{'channel_id':CB},{'agent_id':AGENT2}])
def test_scope_mismatch_rejected(group,patch):
    data=config();data['assignments'][0].update(patch)
    with pytest.raises(TenantLinearError):groups.configure(PROFILE,data)

def test_tenant_and_legacy_quarantine(group):
    with pytest.raises(TenantLinearError):groups.configure({**PROFILE,'tenant_id':B},config())
    group['db'].execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,state) VALUES(%s::uuid,'connect','',%s,'human')",(A,G));group['db'].commit()
    with pytest.raises(TenantLinearError):groups.configure(PROFILE,config())


def _legacy_group_fixture(group):
    d=group['db']
    d.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,agent_id,state,reason,revision) VALUES(%s::uuid,'connect','',%s,%s::uuid,'human','legacy_handoff',9)",(A,G,AGENT))
    d.commit()
    return {'provider':'connect','channel_id':CA,'conversation_id':G,'lead_id':1001,'agent_id':AGENT}


def test_owner_group_legacy_preview_confirms_exact_channel_and_stays_paused(group):
    d=group['db'];selection=_legacy_group_fixture(group)
    preview=groups.legacy_resolution_preview(PROFILE,selection)
    assert preview['allowed'] and preview['requires_resolution']
    assert [(row['channel_id'],row['lead_id'],row['message_count']) for row in preview['channels']]==[(CA,1001,0),(CB,1002,0)]
    assert 'text' not in str(preview).lower()
    before=d.execute("SELECT channel_id,conversation_id,agent_id,state,reason,revision FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND conversation_id=%s ORDER BY channel_id",(A,G));d.commit()
    result=groups.configure(PROFILE,{'conversation_id':G,'expected_revision':preview['expected_revision'],
      'assignments':[{'agent_id':AGENT,'channel_id':CA}],
      'legacy_resolution':{'confirmed':True,'expected_token':preview['expected_token'],
        'agent_version':preview['agent_version'],'channel_id':CA,'lead_id':1001,'agent_id':AGENT}})
    assert result=={'revision':1,'state':'paused','legacy_resolved':True,'resume_required':True,'automatic_replies_enabled':False}
    after=d.execute("SELECT channel_id,conversation_id,agent_id,state,reason,revision FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND conversation_id=%s ORDER BY channel_id",(A,G));d.commit()
    assert after==before,'the original state/history stays in its unbound row; no channel history was moved'
    assert d.execute('SELECT channel_id::text AS channel_id,agent_id::text AS agent_id,legacy_revision FROM saas_ai_group_channel_legacy_permits WHERE tenant_id=%s::uuid AND group_jid=%s',(A,G))==[{'channel_id':CA,'agent_id':AGENT,'legacy_revision':9}]
    assert not d.execute('SELECT ai_legacy_blocked(%s::uuid,%s,%s,NULL) AS blocked',(A,CA,G))[0]['blocked']
    assert d.execute('SELECT ai_legacy_blocked(%s::uuid,%s,%s,NULL) AS blocked',(A,CB,G))[0]['blocked']
    with d.cursor() as cur:
        assert groups.authority(cur,A,CA,G) is None,'assignment never resumes the group'
        assert groups.admit(cur,A,CA,G,'LEGACY_EVENT') is None
    d.commit();group['send'].assert_not_called();group['generate'].assert_not_called()


def test_group_preview_token_is_bound_to_the_exact_group_id(group):
    d=group['db'];selection=_legacy_group_fixture(group);other='120363431037910652@g.us'
    d.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind,lead_id) VALUES(%s::uuid,%s::uuid,%s,'group',1004),(%s::uuid,%s::uuid,%s,'group',1005)",(A,CA,other,A,CB,other))
    d.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,agent_id,state,reason,revision) VALUES(%s::uuid,'connect','',%s,%s::uuid,'human','legacy_handoff',9)",(A,other,AGENT));d.commit()
    second=groups.legacy_resolution_preview(PROFILE,{**selection,'conversation_id':other,'lead_id':1004})
    first=groups.legacy_resolution_preview(PROFILE,selection)
    assert first['allowed'] and second['allowed'] and first['expected_token']!=second['expected_token']


def test_group_legacy_preview_blocks_links_and_stale_or_wrong_scope(group):
    d=group['db'];selection=_legacy_group_fixture(group)
    # An active source link is never covered by the group-specific permit.
    d.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind,lead_id) VALUES(%s::uuid,%s::uuid,'120363431037910652@g.us','group',1003)",(A,CA))
    d.execute("INSERT INTO saas_connect_dialog_links(tenant_id,channel_id,source_id,canonical_id,source_lead,canonical_lead,pair_id,snapshot) VALUES(%s::uuid,%s::uuid,%s,%s,1001,1003,'pair','{}'::jsonb)",(A,CA,G,'120363431037910652@g.us'));d.commit()
    linked=groups.legacy_resolution_preview(PROFILE,selection)
    assert not linked['allowed'] and linked['reason']=='active_source_link'
    assert linked['evidence']['active_source_links']==1
    assert not d.execute('SELECT 1 FROM saas_ai_group_channel_legacy_permits WHERE tenant_id=%s::uuid',(A,));d.commit()
    d.execute("UPDATE saas_connect_dialog_links SET active=FALSE WHERE tenant_id=%s::uuid AND pair_id='pair'",(A,));d.commit()
    preview=groups.legacy_resolution_preview(PROFILE,selection)
    with pytest.raises(TenantLinearError):groups.legacy_resolution_preview({'tenant_id':B,'telegram_id':22},selection)
    with pytest.raises(TenantLinearError):groups.configure(PROFILE,{'conversation_id':G,'expected_revision':preview['expected_revision'],
      'assignments':[{'agent_id':AGENT,'channel_id':CB}],
      'legacy_resolution':{'confirmed':True,'expected_token':preview['expected_token'],'agent_version':preview['agent_version'],
        'channel_id':CA,'lead_id':1001,'agent_id':AGENT}})
    d.execute("INSERT INTO saas_ai_group_coordination(tenant_id,group_jid,revision,state) VALUES(%s::uuid,%s,%s,'paused') ON CONFLICT(tenant_id,provider,group_jid) DO UPDATE SET revision=saas_ai_group_coordination.revision+1",(A,G,preview['expected_revision']+1));d.commit()
    with pytest.raises(TenantLinearError):groups.configure(PROFILE,{'conversation_id':G,'expected_revision':preview['expected_revision'],
      'assignments':[{'agent_id':AGENT,'channel_id':CA}],
      'legacy_resolution':{'confirmed':True,'expected_token':preview['expected_token'],'agent_version':preview['agent_version'],
        'channel_id':CA,'lead_id':1001,'agent_id':AGENT}})
    assert not d.execute('SELECT 1 FROM saas_ai_group_channel_legacy_permits WHERE tenant_id=%s::uuid',(A,));d.commit()
    group['send'].assert_not_called();group['generate'].assert_not_called()


def test_group_legacy_preview_blocks_unknown_work_and_does_not_change_agent_scope(group):
    d=group['db'];selection=_legacy_group_fixture(group)
    d.execute("UPDATE saas_ai_agent_jobs SET conversation_id=%s,state='unknown' WHERE tenant_id=%s::uuid AND provider='connect'",(G,A));d.commit()
    pending=groups.legacy_resolution_preview(PROFILE,selection)
    assert not pending['allowed'] and pending['reason']=='outstanding_work'
    assert pending['evidence']['job_counts']
    assert not d.execute('SELECT 1 FROM saas_ai_group_channel_legacy_permits WHERE tenant_id=%s::uuid',(A,));d.commit()
    d.execute("UPDATE saas_ai_agent_jobs SET conversation_id='client',state='skipped' WHERE tenant_id=%s::uuid AND provider='connect'",(A,))
    d.execute("UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(json.dumps([{'provider':'connect','channel_id':CA,'allow_group_messages':False}]),A,AGENT));d.commit()
    blocked=groups.legacy_resolution_preview(PROFILE,selection)
    assert not blocked['allowed'] and blocked['reason']=='agent_not_group_scoped'
    assert d.execute("SELECT scopes FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT))[0]['scopes']==[{'provider':'connect','channel_id':CA,'allow_group_messages':False}]
    assert not d.execute('SELECT 1 FROM saas_ai_group_channel_legacy_permits WHERE tenant_id=%s::uuid',(A,));d.commit()
    group['send'].assert_not_called();group['generate'].assert_not_called()


def test_group_legacy_confirmation_rechecks_route_kind(group):
    d=group['db'];selection=_legacy_group_fixture(group);preview=groups.legacy_resolution_preview(PROFILE,selection)
    assert preview['allowed']
    d.execute("UPDATE saas_connect_conversations SET conversation_kind='direct' WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s",(A,CA,G));d.commit()
    with pytest.raises(TenantLinearError):groups.configure(PROFILE,{'conversation_id':G,'expected_revision':preview['expected_revision'],
      'assignments':[{'agent_id':AGENT,'channel_id':CA}],
      'legacy_resolution':{'confirmed':True,'expected_token':preview['expected_token'],'agent_version':preview['agent_version'],
        'channel_id':CA,'lead_id':1001,'agent_id':AGENT}})
    assert not d.execute('SELECT 1 FROM saas_ai_group_channel_legacy_permits WHERE tenant_id=%s::uuid',(A,));d.commit()
    group['send'].assert_not_called();group['generate'].assert_not_called()


def test_group_legacy_resolution_replaces_paused_stale_channel_binding_only(group):
    d=group['db'];selection=_legacy_group_fixture(group)
    old=[{'agent_id':AGENT2,'channel_id':CB,'priority':0}]
    d.execute("INSERT INTO saas_ai_group_coordination(tenant_id,group_jid,revision,state,assignments,reason) VALUES(%s::uuid,%s,4,'paused',%s::jsonb,'legacy_binding')",(A,G,json.dumps(old)))
    d.execute("UPDATE saas_connect_channels SET active=FALSE WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid",(A,CB));d.commit()
    preview=groups.legacy_resolution_preview(PROFILE,selection)
    assert preview['allowed'] and preview['expected_revision']==4
    assert preview['current_assignments']==[{'agent_id':AGENT2,'channel_id':CB}]
    before=d.execute("SELECT channel_id,conversation_id,agent_id,state,reason,revision FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND conversation_id=%s ORDER BY channel_id",(A,G));d.commit()
    result=groups.configure(PROFILE,{'conversation_id':G,'expected_revision':4,
      'assignments':[{'agent_id':AGENT,'channel_id':CA}],
      'legacy_resolution':{'confirmed':True,'expected_token':preview['expected_token'],
        'agent_version':preview['agent_version'],'channel_id':CA,'lead_id':1001,'agent_id':AGENT}})
    assert result['state']=='paused' and result['automatic_replies_enabled'] is False
    assert groups.read(d.cursor(),A,G)['assignments']==[{'agent_id':AGENT,'channel_id':CA,'priority':0}]
    after=d.execute("SELECT channel_id,conversation_id,agent_id,state,reason,revision FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND conversation_id=%s ORDER BY channel_id",(A,G));d.commit()
    assert after==before
    audit=d.execute("SELECT payload FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid AND action='ai_group_legacy_channel_resolved' ORDER BY created_at DESC LIMIT 1",(A,))[0]['payload'];d.commit()
    assert audit['previous_assignments']==old and audit['history_merged'] is False
    with d.cursor() as cur:
        assert groups.authority(cur,A,CA,G) is None
    d.commit();group['send'].assert_not_called();group['generate'].assert_not_called()

def test_stale_config_and_old_jobs(group):
    groups.configure(PROFILE,config())
    with pytest.raises(TenantLinearError):groups.configure(PROFILE,config())
    with group['db'].cursor() as c:
        assert not groups.job_allowed(c,{'provider':'connect','tenant_id':A,'channel_id':CA,'conversation_id':G,'agent_id':AGENT})
        assert groups.job_allowed(c,{'provider':'kommo','conversation_id':G})


def test_single_agent_edit_preserves_handoff_and_assignee(group):
    d=group['db'];groups.configure(PROFILE,config())
    with d.cursor() as cur:
        groups.stop(cur,A,G,state='human',reason='customer_requested_manager',actor=11)
        d.commit()
    current=groups.read(d.cursor(),A,G)
    groups.configure(PROFILE,config(expected_revision=int(current['revision'])))
    state=groups.read(d.cursor(),A,G)
    assert state['state']=='human' and state['reason']=='customer_requested_manager' and state['assigned_to']==11


def incoming(f,channel,external):
    import uuid
    d=f['db'];mid=str(uuid.uuid5(uuid.NAMESPACE_URL,channel+external))
    lead=d.execute('SELECT lead_id FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s',(A,channel,G))[0]['lead_id']
    d.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,message_type,happened_at) VALUES(%s::uuid,%s::uuid,%s,%s,%s,'incoming','Question','text',clock_timestamp()) ON CONFLICT DO NOTHING",(mid,A,lead,'connect:'+mid,'connect:'+channel));d.commit()
    return connect.enqueue(A,channel_id=channel,conversation_id=G,lead_id=lead,message_id=mid,event_id=mid,text='Question',is_group=True,provider_message_id=external)


def test_unique_group_agent_is_recorded_but_group_stays_paused(group):
    f=group;d=f['db']
    assert incoming(f,CA,'AUTO_ASSIGN_UNIQUE')==0
    state=groups.view(d.cursor(),A,G)
    assert state['primary_agent_id']==AGENT
    assert state['state']=='paused'
    assert state['assignments']==[{'agent_id':AGENT,'channel_id':CA,'priority':0}]
    assert d.execute("SELECT 1 FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND conversation_id=%s",(A,G))==[]
    f['send'].assert_not_called();f['generate'].assert_not_called()


def test_group_requires_choice_when_multiple_agents_match(group):
    f=group;d=f['db']
    d.execute("UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",
      (json.dumps([{'provider':'connect','channel_id':CA,'allow_group_messages':True}]),A,AGENT2));d.commit()
    assert incoming(f,CA,'AMBIGUOUS_GROUP')==0
    state=groups.view(d.cursor(),A,G)
    assert state['assignments']==[] and state['primary_agent_id'] is None and state['state']=='paused'
    assert d.execute("SELECT 1 FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND conversation_id=%s",(A,G))==[]
    f['send'].assert_not_called();f['generate'].assert_not_called()


def test_owner_bulk_preview_assigns_unique_group_without_resuming(group):
    f=group;d=f['db']
    preview=assignment.bulk_preview(PROFILE,CA,AGENT)
    group_item=next(item for item in preview['eligible'] if item.get('kind')=='group')
    assert group_item['conversation_id']==G
    assert preview['summary']['group_eligible']==1
    result=assignment.bulk_apply(PROFILE,{'confirmed':True,'channel_id':CA,'agent_id':AGENT,
      'preview_token':preview['preview_token'],'batch_id':'00000000-0000-0000-0000-000000000901'})
    assert result['assigned']==1
    state=groups.view(d.cursor(),A,G)
    assert state['primary_agent_id']==AGENT and state['state']=='paused'
    assert not state.get('resumed_at')
    f['send'].assert_not_called();f['generate'].assert_not_called()


@pytest.mark.parametrize('mode',['manual','automatic'])
def test_worker_one_group_two_channels(group,mode):
    f=group;d=f['db'];d.execute("UPDATE saas_ai_agents SET mode=%s WHERE tenant_id=%s::uuid",(mode,A));d.execute("DELETE FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid",(A,));d.commit()
    groups.configure(PROFILE,config());agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CA)
    assert incoming(f,CB,'PROVIDER_SHARED')==0
    assert incoming(f,CA,'PROVIDER_SHARED')==(1 if mode=='automatic' else 0)
    assert incoming(f,CA,'PROVIDER_SHARED')==0
    f['process']()
    assert f['generate'].call_count==(1 if mode=='automatic' else 0)
    assert f['send'].call_count==(1 if mode=='automatic' else 0)
    drafts=d.execute('SELECT * FROM saas_ai_agent_drafts WHERE tenant_id=%s::uuid',(A,));d.commit()
    assert len(drafts)==0


def test_manual_group_agent_is_assigned_without_background_processing(group):
    f=group;d=f['db'];d.execute("UPDATE saas_ai_agents SET mode='manual' WHERE tenant_id=%s::uuid",(A,));d.execute("DELETE FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid",(A,));d.commit()
    groups.configure(PROFILE,config());agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CA)
    assert incoming(f,CA,'MANUAL_NO_BACKGROUND')==0
    assert groups.view(d.cursor(),A,G)['primary_agent_id']==AGENT
    f['process']();f['send'].assert_not_called();f['generate'].assert_not_called()
    assert not d.execute('SELECT * FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid',(A,));d.commit()


def test_delivery_reservation_remains_tenant_and_channel_scoped(group):
    f=group;d=f['db'];d.execute("DELETE FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid",(A,));d.commit()
    groups.configure(PROFILE,config());agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CA)
    assert incoming(f,CA,'SEND_ONCE_ONLY')==1;f['process']()
    run=d.execute('SELECT id FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid',(A,))[0]['id'];d.commit()
    assert not groups.reserve_delivery(A,CB,G,run)
    assert not groups.reserve_delivery(B,CA,G,run)
    assert not groups.reserve_delivery(A,CA,G,run)


def test_pre_resume_event_seen_on_secondary_never_replayed(group):
    f=group;groups.configure(PROFILE,config())
    assert incoming(f,CB,'BEFORE_RESUME')==0
    agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CA)
    assert incoming(f,CA,'BEFORE_RESUME')==0


def test_worker_stops_when_human_takes_over_during_generation(group):
    f=group;d=f['db'];d.execute("DELETE FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid",(A,));d.commit()
    groups.configure(PROFILE,config());agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CA)
    incoming(f,CA,'HUMAN_RACE')
    def generate(*args):
        agents.set_conversation_state(PROFILE,G,'human',provider='connect',channel_id=CB)
        return ('stale',{'total_tokens':1})
    f['generate'].side_effect=generate;f['process']();f['send'].assert_not_called()
    assert not d.execute('SELECT 1 FROM saas_ai_agent_drafts WHERE tenant_id=%s::uuid',(A,));d.commit()


def test_logical_group_keeps_channel_history_and_permissions(group,monkeypatch):
    import tenant_operator_dialogs as dialogs
    from test_tenant_operator_workspace import employee
    d=group['db'];monkeypatch.setattr(dialogs,'_connect',lambda:d)
    groups.configure(PROFILE,config());agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CA)
    rows=[r for r in dialogs.listing(PROFILE)['dialogs'] if r['conversation_id']==G]
    assert len(rows)==1 and len(rows[0]['group_coordination']['channels'])==2
    assert rows[0]['agent_id']==AGENT and rows[0]['display_state']=='active'
    person=employee(d,['dialogs','support'])
    lead=d.execute('SELECT lead_id FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s',(A,CB,G))[0]['lead_id'];d.commit()
    page=dialogs.history(person,'connect',lead,CB,G)
    assert not page['dialog']['group_coordination']['can_configure']
    assert page['dialog']['channel_id']==CB
    with pytest.raises(TenantLinearError):groups.configure(person,config(expected_revision=2))
    with pytest.raises(TenantLinearError):dialogs.history({**PROFILE,'tenant_id':B},'connect',lead,CB,G)


@pytest.mark.parametrize('source',['employee','unknown','ai','uncertain'])
@pytest.mark.parametrize('outbound',[False,True])
def test_signed_group_authorship(group,monkeypatch,source,outbound):
    import uuid
    import tenant_connect_transport as transport
    d=group['db'];groups.configure(PROFILE,config());agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CA)
    incoming(group,CA,'READY_FOR_TAKEOVER')
    d.execute('INSERT INTO saas_ai_group_staff(tenant_id,channel_id,jid,member_id,verified_by) VALUES(%s::uuid,%s::uuid,%s,11,11)',(A,CB,'994500000000@s.whatsapp.net'));d.commit()
    monkeypatch.setattr(transport,'_verify_signature',lambda *a:True)
    data={'id':str(uuid.uuid4()),'externalMessageId':'STAFF_EVENT_ID','channelId':CB,'conversationId':G,'isGroup':True,'conversationType':'group','fromMe':outbound,'participantJid':'994500000000@s.whatsapp.net','type':'text','text':'local test'}
    if source=='unknown':data['participantJid']='994599999999@s.whatsapp.net'
    if source=='ai':
        group['process']()
        data['originClientMessageId']=str(d.execute('SELECT id FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid',(A,))[0]['id']);d.commit()
    if source=='uncertain':data['authorshipUncertain']=True
    event={'id':str(uuid.uuid4()),'type':'message.inbound.received','data':data}
    assert transport.ingest(json.dumps(event).encode(),'test',event)
    assert transport.ingest(json.dumps(event).encode(),'test',event)
    row=d.execute('SELECT state,assigned_to FROM saas_ai_group_coordination WHERE tenant_id=%s::uuid',(A,))[0];d.commit()
    assert row['state']=={'employee':'human','uncertain':'paused'}.get(source,'active')
    assert row['assigned_to']==(11 if source=='employee' else None)
    if source=='employee':
        agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CA)
        replay={**event,'id':str(uuid.uuid4())}
        assert transport.ingest(json.dumps(replay).encode(),'test',replay)
        with d.cursor() as c:assert groups.read(c,A,G)['state']=='active'
        d.commit()
        other={**event,'id':str(uuid.uuid4()),'data':{**data,'id':str(uuid.uuid4()),'channelId':CA}}
        assert transport.ingest(json.dumps(other).encode(),'test',other)
        with d.cursor() as c:assert groups.read(c,A,G)['state']=='active'
        d.commit()
    if source in ('employee','uncertain'):
        assert not d.execute("SELECT 1 FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND conversation_id=%s AND state IN ('queued','processing')",(A,G));d.commit()


def test_manual_group_handoff_is_idempotent_across_channels(group):
    d=group['db'];groups.configure(PROFILE,config())
    agents.set_conversation_state(PROFILE,G,'human',provider='connect',channel_id=CA)
    agents.set_conversation_state(PROFILE,G,'human',provider='connect',channel_id=CB)
    assert len(d.execute('SELECT id FROM saas_support_tickets WHERE tenant_id=%s::uuid',(A,)))==1;d.commit()


def test_schedule_switch_never_resumes_human_group(group,monkeypatch):
    d=group['db'];slots=[{'agent_id':AGENT,'channel_id':CA,'priority':10},{'agent_id':AGENT2,'channel_id':CB,'priority':20}]
    with pytest.raises(TenantLinearError):groups.configure(PROFILE,config(assignments=slots))
    groups.configure(PROFILE,config());agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CA)
    with d.cursor() as c:
        assert groups.authority(c,A,CA,G,AGENT)
        assert groups.authority(c,A,CB,G,AGENT2) is None
    d.commit()
    agents.set_conversation_state(PROFILE,G,'human',provider='connect',channel_id=CA)
    d.execute('UPDATE saas_ai_agents SET enabled=FALSE WHERE id=%s::uuid',(AGENT,));d.commit()
    assert incoming(group,CA,'DAY_AFTER_HANDOFF')==0
    with d.cursor() as c:assert groups.read(c,A,G)['state']=='human'
    d.commit()


@pytest.mark.parametrize('mode',['manual','automatic'])
def test_group_reply_handoff_and_late_callback_preserve_resume(group,monkeypatch,mode):
    import tenant_ai_agent_worker as worker
    d=group['db'];d.execute("DELETE FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid",(A,));d.execute('UPDATE saas_ai_agents SET mode=%s WHERE tenant_id=%s::uuid',(mode,A));d.commit()
    groups.configure(PROFILE,config());agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CA)
    monkeypatch.setattr(worker,'enabled_triggers',lambda *a:[{'trigger_type':'phrase','condition':{'phrase':'Question'},'actions':['handoff_after_reply']}])
    incoming(group,CA,'HANDOFF_REPLY');group['process']()
    if mode=='manual':
        assert d.execute('SELECT id FROM saas_ai_agent_drafts WHERE tenant_id=%s::uuid',(A,))==[]
        assert d.execute('SELECT id FROM saas_support_tickets WHERE tenant_id=%s::uuid',(A,))==[]
        assert group['generate'].call_count==0 and group['send'].call_count==0
        return
    with d.cursor() as c:assert groups.read(c,A,G)['state']=='human'
    d.commit()
    agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CB)
    with d.cursor() as c:assert groups.read(c,A,G)['state']=='active'
    assert len(d.execute('SELECT id FROM saas_support_tickets WHERE tenant_id=%s::uuid',(A,)))==1;d.commit()
    group['send'].assert_called_once()


def test_staff_registration_requires_owner_and_observed_company_identity(group):
    import uuid
    d=group['db'];data={'channel_id':CA,'jid':'994500000000@s.whatsapp.net','member_id':11}
    with pytest.raises(TenantLinearError):groups.register_employee(PROFILE,data)
    d.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,raw) VALUES(%s::uuid,%s::uuid,1001,'staff-proof',%s,'outgoing','test',%s::jsonb)",(str(uuid.uuid4()),A,'connect:'+CA,json.dumps({'fromMe':True,'source':'linked_phone','participantJid':data['jid']})));d.commit()
    assert groups.register_employee(PROFILE,data)=={'verified':True}
    with d.cursor() as c:
        assert groups.verified_employee(c,A,{'participantJid':data['jid'],'channelId':CA})==11
        assert groups.verified_employee(c,A,{'participantJid':data['jid'],'channelId':CB}) is None
        assert groups.verified_employee(c,A,{'participantJid':data['jid']}) is None
        assert groups.verified_employee(c,B,{'participantJid':data['jid'],'channelId':CA}) is None
    d.commit()


def test_schedule_does_not_clear_old_channel_human_state(group):
    d=group['db'];groups.configure(PROFILE,config());agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CA)
    d.execute("UPDATE saas_ai_conversation_state SET state='human' WHERE tenant_id=%s::uuid AND channel_id=%s AND conversation_id=%s",(A,CA,G));d.commit()
    assert incoming(group,CA,'KEEP_CHANNEL_HUMAN')==0
    with d.cursor() as c:
        assert groups.view(c,A,G)['reason']=='group_channel_requires_resume'
        assert groups.view(c,A,G)['state']=='human'
    d.commit()


def test_old_stored_message_without_coordination_receipt_never_requeues(group):
    import uuid
    d=group['db'];mid=str(uuid.uuid4())
    d.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,happened_at) VALUES(%s::uuid,%s::uuid,1001,%s,%s,'incoming','old',now()-interval '1 day')",(mid,A,'connect:'+mid,'connect:'+CA));d.commit()
    groups.configure(PROFILE,config());agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CA)
    assert connect.enqueue(A,channel_id=CA,conversation_id=G,lead_id=1001,message_id=mid,event_id=mid,text='old',is_group=True,provider_message_id='OLD_DELAYED_EVENT')==0


def test_authenticated_operator_claim_stops_ai_before_external_send(group,monkeypatch):
    import uuid
    import tenant_chat_send_store as sender
    d=group['db'];monkeypatch.setattr(sender,'_connect',lambda:d)
    groups.configure(PROFILE,config());agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CA)
    incoming(group,CA,'PENDING_BEFORE_OPERATOR')
    item=sender.reserve(PROFILE,{'lead_id':1002,'request_id':str(uuid.uuid4()),'text':'local manual response'})
    sender.mark_sending(item,{'origin':'connect'})
    with d.cursor() as c:
        state=groups.read(c,A,G);assert state['state']=='human' and state['assigned_to']==11
    assert not d.execute("SELECT 1 FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND conversation_id=%s AND state IN ('queued','processing')",(A,G));d.commit()
    group['send'].assert_not_called()


def test_settings_projection_only_primary_agents_and_authorized_helpers(group,monkeypatch):
    import uuid
    import tenant_operator_dialogs as dialogs
    d=group['db'];monkeypatch.setattr(dialogs,'_connect',lambda:d)
    helper,denied,foreign,disabled=[str(uuid.UUID(int=n)) for n in (801,802,803,804)]
    for agent,tenant,name,enabled,policy in [(helper,A,'Internal',True,{'internal':True,'allowed_primary_ids':[AGENT]}),(denied,A,'Denied',True,{'internal':True,'allowed_primary_ids':[AGENT2]}),(foreign,B,'Foreign',True,{}),(disabled,A,'Disabled',False,{})]:
        d.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,role,enabled,created_by,expert_policy) VALUES(%s::uuid,%s::uuid,%s,'expert',%s,%s,%s::jsonb)",(tenant,agent,name,enabled,11 if tenant==A else 22,json.dumps(policy)))
    d.execute('UPDATE saas_ai_agents SET expert_policy=%s::jsonb WHERE id=%s::uuid',(json.dumps({'expert_agent_ids':[helper,denied,foreign,disabled]}),AGENT));d.commit()
    result=dialogs.history(PROFILE,'connect',1001,CA,G)['dialog']['group_coordination']['agents']
    assert {r['id'] for r in result}=={AGENT,AGENT2}
    primary=next(r for r in result if r['id']==AGENT)
    assert primary['helpers']==['Internal'] and primary['role']=='primary'
    assert all('expert_policy' not in r for r in result)
    group['send'].assert_not_called()
