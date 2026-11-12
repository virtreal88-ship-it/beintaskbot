"""Synthetic tenant/department/source matrix. No real provider calls."""
import json
import uuid
from unittest.mock import Mock
import pytest
from cryptography.fernet import Fernet
from test_tenant_support_inbox import database,db,A,B,CA,CB,PROFILE
from test_tenant_operator_workspace import operator_db,employee
import tenant_dialog_access as access
import tenant_operator_dialogs as dialogs
import tenant_chat_send_store as sender
from tenant_policy import TenantPolicy
from tenant_linear_policy import TenantLinearError

D1,D2,C2=[str(uuid.UUID(int=x)) for x in (501,502,601)]


def grants(db,actor,view='department',update='self'):
    value={str(actor):{'access_scopes':{'dialogs':{'view':view,'update':update}}}}
    db.execute('''INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value)
      VALUES(%s::uuid,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key)
      DO UPDATE SET value=saas_tenant_workflow_policies.value||EXCLUDED.value''',(A,json.dumps(value)))
    db.commit()


@pytest.fixture
def scoped(operator_db,monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    monkeypatch.setattr(access,'_connect',lambda:operator_db,raising=False)
    import tenant_platform
    monkeypatch.setattr(tenant_platform,'_connect',lambda:operator_db)
    db=operator_db
    for department in (D1,D2):
        db.execute("INSERT INTO saas_tenant_departments(tenant_id,id,name) VALUES(%s::uuid,%s::uuid,%s)",(A,department,department))
    db.commit()
    for actor,department in ((33,D1),(34,D1),(35,D2)):
        employee(db,['dialogs','deals'],actor=actor)
        db.execute('UPDATE saas_tenant_members SET department_id=%s::uuid WHERE tenant_id=%s::uuid AND telegram_id=%s',(department,A,actor))
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Other source')",(A,C2))
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,'client',43)",(A,C2))
    for source,actor in ((CA,34),(C2,35)):
        db.execute("INSERT INTO saas_dialog_source_members(tenant_id,source_id,employee_id,updated_by) VALUES(%s::uuid,%s::uuid,%s,11)",(A,source,actor))
    cipher=Fernet(Fernet.generate_key())
    monkeypatch.setattr('tenant_connect_transport._fernet',lambda:cipher)
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets) VALUES(%s::uuid,'connect','connected',%s)",(A,cipher.encrypt(b'{"api_key":"synthetic-test"}')))
    db.commit()
    return db,{'tenant_id':A,'telegram_id':33}


@pytest.mark.parametrize('view,expected',[('none',None),('self',[]),('department',[CA]),('all',[CA,C2])])
def test_view_scope_matrix_without_own_source(scoped,view,expected):
    db,person=scoped;grants(db,33,view,'none')
    if expected is None:
        with pytest.raises(TenantLinearError):dialogs.listing(person)
    else:
        result=dialogs.listing(person)
        assert sorted(r['channel_id'] for r in result['dialogs'])==sorted(expected)
        assert all(not r['can_send'] for r in result['dialogs'])
        assert sorted(c['id'] for c in result['channels'])==sorted('connect:'+x for x in expected)
    with pytest.raises(TenantLinearError):dialogs.history(person,'connect',42,CB,'client')


@pytest.mark.parametrize('update,allowed',[('none',[]),('self',[]),('department',[41]),('all',[41,43])])
def test_update_is_independent_of_view(scoped,update,allowed):
    db,person=scoped;grants(db,33,'none',update)
    for lead in (41,43):
        with db.cursor() as cur:
            if lead in allowed:
                _,connection,_=sender.authority(cur,person,lead)
                assert connection==['connect',CA if lead==41 else C2,'client']
            else:
                with pytest.raises(TenantLinearError):sender.authority(cur,person,lead)
        db.commit()


def test_shared_source_self_department_changes_and_deactivation(scoped):
    db,person=scoped;grants(db,33,'self','self')
    db.execute("INSERT INTO saas_dialog_source_members(tenant_id,source_id,employee_id,updated_by) VALUES(%s::uuid,%s::uuid,33,11)",(A,CA));db.commit()
    assert dialogs.history(person,'connect',41,CA,'client')['messages'][0]['body']=='Internet is down'
    db.execute('DELETE FROM saas_dialog_source_members WHERE tenant_id=%s::uuid AND employee_id=33',(A,));db.commit()
    with pytest.raises(TenantLinearError):dialogs.history(person,'connect',41,CA,'client')
    grants(db,33,'department','department')
    assert dialogs.history(person,'connect',41,CA,'client')['dialog']['can_send']
    db.execute('UPDATE saas_tenant_members SET department_id=%s::uuid WHERE tenant_id=%s::uuid AND telegram_id=34',(D2,A));db.commit()
    assert dialogs.listing(person)['dialogs']==[]
    db.execute('UPDATE saas_tenant_members SET department_id=%s::uuid,active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=34',(D1,A));db.commit()
    assert dialogs.listing(person)['dialogs']==[]


def test_reserved_send_rechecks_revocation_before_provider(scoped):
    db,person=scoped;grants(db,33,'department','department')
    command={'lead_id':41,'request_id':str(uuid.uuid4()),'text':'synthetic only'}
    item=sender.reserve(person,command)
    _,credentials=sender.mark_sending(item,{'origin':'connect'})
    grants(db,33,'department','none')
    send=Mock()
    with pytest.raises(TenantLinearError):sender.dispatch_guarded(item,credentials,send,'synthetic only')
    send.assert_not_called()


def test_dispatch_source_cannot_switch_after_reservation(scoped):
    db,person=scoped;grants(db,33,'all','all')
    item=sender.reserve(person,{'lead_id':41,'request_id':str(uuid.uuid4()),'text':'synthetic'})
    _,credentials=sender.mark_sending(item,{'origin':'connect'})
    send=Mock()
    with pytest.raises(TenantLinearError):sender.dispatch_guarded(item,{**credentials,'channel_id':C2},send)
    send.assert_not_called()


def test_owner_bindings_are_explicit_versioned_and_tenant_bound(scoped):
    db,person=scoped
    initial=access.sources(PROFILE)
    source=next(s for s in initial['sources'] if s['source_id']==CA)
    assert source['employee_ids']==[34]
    assert initial['preflight']['ready']
    payload={'source_id':CA,'employee_ids':[33,34],'expected_revision':source['revision']}
    result=access.sources(PROFILE,payload)
    assert next(s for s in result['sources'] if s['source_id']==CA)['employee_ids']==[33,34]
    with pytest.raises(TenantLinearError):access.sources(PROFILE,payload)
    with pytest.raises(TenantLinearError):access.sources(person)
    with pytest.raises(TenantLinearError):access.sources(PROFILE,{'source_id':CB,'employee_ids':[33],'expected_revision':0})
    with pytest.raises(TenantLinearError):access.sources(PROFILE,{'source_id':CA,'employee_ids':[22],'expected_revision':1})
    assert db.execute('SELECT count(*) AS n FROM saas_dialog_source_members WHERE tenant_id=%s::uuid',(B,))[0]['n']==0


def test_unassigned_sources_allow_fail_closed_activation_without_mutation(scoped):
    db,_=scoped
    db.execute('DELETE FROM saas_dialog_source_members WHERE tenant_id=%s::uuid AND source_id=%s::uuid',(A,CA));db.commit()
    assert access.activation_preflight(db.cursor(),A)['unassigned_sources']==[{'source_id':CA,'name':'WhatsApp'}]
    access.validate_activation(db.cursor(),A,{'dialogs':{'view':'self'}})
    access.validate_activation(db.cursor(),A,{'dialogs':{'view':'all'}})
    assert not db.execute('SELECT * FROM saas_dialog_source_members WHERE tenant_id=%s::uuid AND source_id=%s::uuid',(A,CA))


def test_group_union_does_not_expose_another_source(scoped):
    db,person=scoped;grants(db,33,'department','department')
    group='120363123456@g.us'
    for source,lead,body in ((CA,51,'allowed'),(C2,52,'hidden')):
        db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,conversation_kind) VALUES(%s::uuid,%s::uuid,%s,%s,'group')",(A,source,group,lead))
        db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body) VALUES(%s::uuid,%s::uuid,%s,%s,%s,'incoming',%s)",(str(uuid.uuid4()),A,lead,'group'+str(lead),'connect:'+source,body))
    db.commit()
    page=dialogs.history(person,'connect',51,CA,group)
    assert [m['body'] for m in page['messages']]==['allowed']
    assert [c['channel_id'] for c in page['dialog']['group_coordination']['channels']]==[CA]


def test_kommo_mapping_is_not_inferred_from_deal_or_label(scoped):
    db,person=scoped;grants(db,33,'department','department')
    from tenant_scoped_access_store import read_access_profile
    policy=TenantPolicy(read_access_profile(db.cursor(),A,33))
    with pytest.raises(TenantLinearError):access.require_source(db.cursor(),A,policy,'kommo','whatsapp')


def test_legacy_lead_reader_cannot_bypass_source_scope(scoped,monkeypatch):
    db,person=scoped;grants(db,33,'department','none')
    import tenant_chat_history_store as history
    monkeypatch.setattr(history,'_connect',lambda:db)
    assert history.page(person,41)['messages'][0]['body']=='Internet is down'
    with pytest.raises(TenantLinearError):history.page(person,43)
    grants(db,33,'none','all')
    with pytest.raises(TenantLinearError):history.page(person,41)


def test_notification_plan_checks_source_and_current_department(scoped,monkeypatch):
    db,person=scoped;grants(db,33,'department','none')
    from tenant_scoped_access_store import read_access_profile
    from tenant_crm_notice_store import source_eligible
    import tenant_crm_notice_store as notice
    monkeypatch.setattr(notice,'eligible',lambda *args,**kwargs:True)
    member=read_access_profile(db.cursor(),A,33)
    allowed={'tenant_id':A,'channel':'connect:'+CA,'direction':'incoming'}
    hidden={**allowed,'channel':'connect:'+C2}
    assert source_eligible(db.cursor(),member,'incoming_message',allowed,'push')
    assert not source_eligible(db.cursor(),member,'incoming_message',hidden,'push')
    db.execute('UPDATE saas_tenant_members SET department_id=%s::uuid WHERE tenant_id=%s::uuid AND telegram_id=33',(D2,A));db.commit()
    assert not source_eligible(db.cursor(),member,'incoming_message',allowed,'push')


def test_scoped_dialog_permission_does_not_grant_ai(scoped):
    db,person=scoped;grants(db,33,'all','all')
    from tenant_scoped_access_store import read_access_profile
    policy=TenantPolicy(read_access_profile(db.cursor(),A,33))
    assert not access.independent_ai_allowed(policy)
    assert not access.independent_ai_allowed(policy,management=True)
    with pytest.raises(TenantLinearError):access.require_independent_ai(policy)
    assert dialogs.history(person,'connect',41,CA,'client')['dialog']['can_use_copilot'] is False


def test_notification_center_filters_list_count_and_read_by_source(scoped,monkeypatch):
    db,person=scoped;grants(db,33,'department','none')
    import tenant_notification_center as center
    from test_tenant_notification_center import notice
    monkeypatch.setattr(center,'_connect',lambda:db)
    ours=notice(db,actor=33,event='incoming:allowed',target='/app?view=dialogs&lead_id=41&channel_id='+CA+'&conversation_id=client')
    hidden=notice(db,actor=33,event='incoming:hidden',target='/app?view=dialogs&lead_id=43&channel_id='+C2+'&conversation_id=client')
    result=center.listing(person)
    assert [n['id'] for n in result['notifications']]==[ours]
    assert result['unread_count']==1
    with pytest.raises(TenantLinearError):center.mark_read(person,hidden)


def test_ai_management_does_not_bypass_explicit_dialog_scope(scoped,monkeypatch):
    db,person=scoped;grants(db,33,'department','none')
    import tenant_ai_agents_store as agents
    monkeypatch.setattr(agents,'_connect',lambda:db)
    monkeypatch.setattr(agents,'_authorize',lambda cur,profile:(A,33))
    with db.cursor() as cur:
        assert agents._conversation_authorize(cur,person,'connect','client',CA)==(A,33)
        with pytest.raises(TenantLinearError):agents._conversation_authorize(cur,person,'connect','client',C2)
        with pytest.raises(TenantLinearError):agents._conversation_authorize(cur,person,'connect','client',CA,action='update')


def test_disabled_connect_source_never_falls_back_to_kommo(scoped):
    db,person=scoped;grants(db,33,'all','all')
    db.execute('UPDATE saas_connect_channels SET active=FALSE WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid',(A,CA));db.commit()
    with pytest.raises(TenantLinearError) as caught:sender.authority(db.cursor(),person,41)
    assert caught.value.status==409
    assert 'Connect' in str(caught.value)


def test_partial_update_scope_keeps_legacy_read_but_disables_composer(scoped):
    db,person=scoped
    db.execute('''INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value)
      VALUES(%s::uuid,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value''',
      (A,json.dumps({'33':{'access_scopes':{'dialogs':{'update':'none'}}}})));db.commit()
    rows=dialogs.listing(person)['dialogs']
    assert {r['channel_id'] for r in rows}=={CA,C2}
    assert all(r['can_send'] is False for r in rows)
    with pytest.raises(TenantLinearError):sender.authority(db.cursor(),person,41)


@pytest.mark.parametrize('enabled',[False,True])
def test_lead_history_filters_mismatched_source_rows_when_scoped(scoped,monkeypatch,enabled):
    db,person=scoped;grants(db,33,'department','none')
    import tenant_chat_history_store as history
    monkeypatch.setattr(history,'_connect',lambda:db)
    # Legacy reader requires its historical CRM policy; mock only that old
    # deal gate, leaving the real current source checks and SQL intact.
    monkeypatch.setattr(TenantPolicy,'can_access_deal',lambda *_:True)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true' if enabled else 'false')
    db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body)
      VALUES(%s::uuid,%s::uuid,41,'wrong-source',%s,'incoming','hidden other source')''',(str(uuid.uuid4()),A,'connect:'+C2));db.commit()
    bodies=[m['body'] for m in history.page(person,41)['messages']]
    assert ('hidden other source' in bodies) is (not enabled)
    if enabled:
        from tenant_scoped_access_store import read_access_profile
        policy=TenantPolicy(read_access_profile(db.cursor(),A,33))
        query=access.lead_message_filter(db.cursor(),A,policy,41)
        assert CA in query and C2 not in query


def ai_setup(db,monkeypatch):
    import tenant_chat_ai_store as ai
    monkeypatch.setattr(ai,'_connect',lambda:db)
    monkeypatch.setattr(TenantPolicy,'can_access_deal',lambda *_:True)
    monkeypatch.setattr(TenantPolicy,'can_use_chat_ai',lambda *_:True)
    cipher=Fernet(Fernet.generate_key());monkeypatch.setattr(ai,'_fernet',lambda:cipher)
    config={'enabled':True,'consent':True,'daily_limit':50,'model':'synthetic',
      'audio_enabled':False,'transcription_model':'synthetic','media_hosts':[]}
    encrypted=cipher.encrypt(json.dumps({'tenant_id':A,'provider':'chat_ai','api_key':'synthetic-test'}).encode())
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,secrets,metadata) VALUES(%s::uuid,'chat_ai',%s,%s::jsonb)",(A,encrypted,json.dumps(config)))
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body) VALUES(%s::uuid,%s::uuid,41,'wrong-source',%s,'incoming','HIDDEN SOURCE SENTINEL')",(str(uuid.uuid4()),A,'connect:'+C2));db.commit()
    return ai,{'lead_id':41,'request_id':str(uuid.uuid4()),'mode':'reply','draft':'','message_id':''}


def test_ai_reserve_filters_mixed_source_rows_before_limit(scoped,monkeypatch):
    db,person=scoped;grants(db,33,'department','none')
    ai,command=ai_setup(db,monkeypatch)
    item=ai.reserve(person,command)
    assert [r['body'] for r in item['rows']]==['Internet is down']
    assert item['_dialog_message_filter']=="channel='connect:"+CA+"'"


def test_ai_generation_cannot_return_result_after_first_narrow_activation(scoped,monkeypatch):
    import asyncio
    import tenant_chat_ai_service as service
    db,person=scoped
    ai,command=ai_setup(db,monkeypatch)
    def generate(*args):
        assert 'HIDDEN SOURCE SENTINEL' in str(args)
        grants(db,33,'department','none')
        return 'Synthetic result based on mixed sources'
    monkeypatch.setattr(service,'generate',generate)
    with pytest.raises(TenantLinearError) as caught:asyncio.run(service.run(person,command,Mock()))
    assert caught.value.status==409
    row=db.execute('SELECT status,result FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(A,command['request_id']))[0]
    assert row['status']=='skipped' and row['result'] is None


def test_notice_first_scope_activation_invalidates_unscoped_snapshot(scoped,monkeypatch):
    db,person=scoped
    import tenant_crm_notice_store as notice
    from tenant_scoped_access_store import read_access_profile
    old=read_access_profile(db.cursor(),A,33)
    assert TenantPolicy(old).action_scope('dialogs','view') is None
    db.commit();grants(db,33,'none','none')
    monkeypatch.setattr(notice,'eligible',lambda *args,**kwargs:True)
    assert notice.source_eligible(db.cursor(),old,'incoming_message',{'channel':'connect:'+CA},'push') is False


def test_connect_lead_identity_is_unique_across_sources(scoped):
    db,_=scoped
    from psycopg.errors import UniqueViolation
    with pytest.raises(UniqueViolation):
        db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,'duplicate-lead',41)",(A,C2))
    db.rollback()


def test_mixed_channel_lead_pagination_filters_before_limit(scoped,monkeypatch):
    db,person=scoped;grants(db,33,'department','none')
    import tenant_chat_history_store as history
    monkeypatch.setattr(history,'_connect',lambda:db)
    for i in range(8):
        db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming',%s,now()+%s*interval '1 minute')",(str(uuid.uuid4()),A,'allow'+str(i),'connect:'+CA,'allowed-'+str(i),i))
    for i in range(140):
        db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','HIDDEN',now()+%s*interval '1 minute')",(str(uuid.uuid4()),A,'hidden'+str(i),'connect:'+C2,i+10))
    db.commit()
    found=[];cursor=''
    for _ in range(5):
        page=history.page(person,41,cursor=cursor,limit=3)
        assert all(m['body']!='HIDDEN' for m in page['messages'])
        found.extend(m['body'] for m in page['messages'])
        if not page['has_more']:break
        cursor=page['next_cursor']
    assert sorted(found)==sorted(['Internet is down']+['allowed-'+str(i) for i in range(8)])


def test_projection_search_counts_and_offsets_ignore_closed_sources(scoped):
    import tenant_operator_dialogs as dialogs
    db,person=scoped;grants(db,33,'department','none')
    for i in range(55):
        for source,lead,name in ((CA,1000+i,'Visible '+str(i)),(C2,2000+i,'Hidden '+str(i))):
            db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name) VALUES(%s::uuid,%s::uuid,%s,%s,%s)",(A,source,'route'+str(lead),lead,name))
    db.commit()
    page=dialogs.listing(person)
    assert page['total_count']==56
    assert len(page['dialogs'])==50
    assert all(row['channel_id']==CA for row in page['dialogs'])
    next_page=dialogs.listing(person,offset=page['next_offset'])
    assert len(next_page['dialogs'])==6
    assert len({r['lead_id'] for r in page['dialogs']+next_page['dialogs']})==56
    assert dialogs.listing(person,search='Hidden')['total_count']==0
    assert dialogs.listing(person,channel='connect:'+C2)['total_count']==0
    assert [c['id'] for c in page['channels']]==['connect:'+CA]




def test_update_none_hides_management_controls_without_disabling_ai_view(scoped,monkeypatch):
    db,person=scoped;grants(db,33,'department','none')
    from tenant_scoped_access_store import read_access_profile
    policy=TenantPolicy(read_access_profile(db.cursor(),A,33))
    monkeypatch.setattr(access,'independent_ai_allowed',lambda *a,**k:True)
    projected=access.apply_capabilities(db.cursor(),A,policy,{'provider':'connect','channel_id':CA,
      'can_send':True,'can_use_copilot':True,'controls_allowed':True,'resume_allowed':True,
      'available_actions':['active'],'agent_assignment':{'allowed':True}})
    assert projected['can_use_copilot'] is True
    assert projected['controls_allowed'] is False and projected['resume_allowed'] is False
    assert projected['available_actions']==[] and 'agent_assignment' not in projected


@pytest.mark.parametrize('gate', ['false','true'])
@pytest.mark.parametrize('provider', ['connect','kommo'])
def test_route_authorization_requires_policy_even_with_scoped_gate_off(monkeypatch,gate,provider):
    from tenant_operator_access import assert_route
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED',gate)
    cur=Mock()
    with pytest.raises(TenantLinearError) as denied:
        assert_route(cur,A,None,provider,'client',CA,41,allow_merged=True)
    assert denied.value.status==403
    cur.execute.assert_not_called()


def retained_source(db):
    from tenant_connect_dialog_equivalence import schema
    with db.cursor() as cur:schema(cur)
    db.execute('''INSERT INTO saas_connect_conversations
      (tenant_id,connect_channel_id,conversation_id,lead_id)
      VALUES(%s::uuid,%s::uuid,'retained-source',44)''',(A,CA))
    db.execute('''INSERT INTO saas_connect_dialog_links
      (tenant_id,channel_id,source_id,canonical_id,source_lead,canonical_lead,pair_id,snapshot)
      VALUES(%s::uuid,%s::uuid,'retained-source','client',44,41,'synthetic-pair','{}'::jsonb)''',(A,CA))
    db.commit()


@pytest.mark.parametrize('action', ['view','update'])
@pytest.mark.parametrize('selected,own_binding,allowed', [
    ('none',False,False),('self',False,False),('self',True,True),('department',False,True)])
def test_merged_route_keeps_independent_current_source_scopes(scoped,action,selected,own_binding,allowed):
    from tenant_operator_access import access as authorize,assert_route
    db,person=scoped
    retained_source(db)
    grants(db,33,selected if action=='view' else 'department',selected if action=='update' else 'department')
    if own_binding:
        db.execute('''INSERT INTO saas_dialog_source_members(tenant_id,source_id,employee_id,updated_by)
          VALUES(%s::uuid,%s::uuid,33,11)''',(A,CA));db.commit()
    with db.cursor() as cur:
        _,_,policy=authorize(cur,person,action='update' if action=='view' else 'view')
        if allowed:
            assert assert_route(cur,A,policy,'connect','retained-source',CA,44,
                                allow_merged=True,action=action)['lead_id']==41
            # A cached actor policy cannot preserve a revoked source grant.
            db.execute('DELETE FROM saas_dialog_source_members WHERE tenant_id=%s::uuid AND source_id=%s::uuid',(A,CA))
        with pytest.raises(TenantLinearError) as denied:
            assert_route(cur,A,policy,'connect','retained-source',CA,44,allow_merged=True,action=action)
        assert denied.value.status==403


def test_structural_redirect_proof_keeps_exact_tenant_source_and_route(scoped):
    from tenant_operator_access import _resolve_connect_route
    db,_=scoped
    retained_source(db)
    with db.cursor() as cur:
        assert _resolve_connect_route(cur,A,'retained-source',CA,44)['lead_id']==44
        assert _resolve_connect_route(cur,A,'retained-source',CA,44,allow_merged=True)['lead_id']==41
        for tenant,conversation,channel,lead in (
            (B,'retained-source',CA,44),(A,'retained-source',CB,44),
            (A,'retained-source',CA,9999),(A,'unknown-source',CA,44),
            (A,'retained-source',CA,41),(A,'client','',None)):
            with pytest.raises(TenantLinearError) as denied:
                _resolve_connect_route(cur,tenant,conversation,channel,lead,allow_merged=True)
            assert denied.value.status==404


@pytest.mark.parametrize('selected',['self','department'])
def test_card_save_with_unmapped_sources_is_fail_closed(scoped,monkeypatch,selected):
    import tenant_member_store as members
    from tenant_scoped_access_store import read_access_profile
    db,person=scoped
    monkeypatch.setattr(members,'_connect',lambda:db)
    db.execute('DELETE FROM saas_dialog_source_members WHERE tenant_id=%s::uuid',(A,))
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body) VALUES(%s::uuid,%s::uuid,99,'unmapped-kommo','whatsapp','incoming','hidden')",(str(uuid.uuid4()),A));db.commit()
    row=next(r for r in members.list_members(tenant_id=A,owner_id=11) if r['telegram_id']==33)
    receipt=members.upsert_member(tenant_id=A,owner_id=11,telegram_id=33,
      expected_version=row['card_version'],changes={'access_settings':{
        'expected_version':row['access_settings']['version'],
        'changes':{'scopes':{'dialogs':{'view':selected,'update':selected}}}}})
    assert receipt['card_version']!=row['card_version']
    result=dialogs.listing(person)
    assert result['dialogs']==[] and result['channels']==[]
    for source,lead in ((CA,41),(C2,43),(CB,42)):
        with pytest.raises(TenantLinearError):dialogs.history(person,'connect',lead,source,'client')
        with pytest.raises(TenantLinearError):sender.authority(db.cursor(),person,lead)
    policy=TenantPolicy(read_access_profile(db.cursor(),A,33))
    with pytest.raises(TenantLinearError):access.require_source(db.cursor(),A,policy,'kommo','whatsapp')
    assert not db.execute('SELECT * FROM saas_dialog_source_members WHERE tenant_id=%s::uuid',(A,))


def test_one_assignment_does_not_expose_unmapped_sources(scoped):
    db,person=scoped
    db.execute('DELETE FROM saas_dialog_source_members WHERE tenant_id=%s::uuid',(A,))
    db.execute('INSERT INTO saas_dialog_source_members(tenant_id,source_id,employee_id,updated_by) VALUES(%s::uuid,%s::uuid,33,11)',(A,CA))
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body) VALUES(%s::uuid,%s::uuid,99,'unmapped-kommo','whatsapp','incoming','hidden')",(str(uuid.uuid4()),A));db.commit()
    grants(db,33,'self','self')
    result=dialogs.listing(person)
    assert [r['channel_id'] for r in result['dialogs']]==[CA]
    assert [r['id'] for r in result['channels']]==['connect:'+CA]
    assert dialogs.history(person,'connect',41,CA,'client')['messages'][0]['body']=='Internet is down'
    assert sender.authority(db.cursor(),person,41)[1]==['connect',CA,'client']
    with pytest.raises(TenantLinearError):dialogs.history(person,'connect',43,C2,'client')
    with pytest.raises(TenantLinearError):sender.authority(db.cursor(),person,43)
