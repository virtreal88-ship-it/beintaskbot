"""Operator workspace regressions against isolated PostgreSQL, never production."""
import asyncio
import json
import uuid
from unittest.mock import Mock,patch
from aiohttp.test_utils import make_mocked_request
import pytest
from test_tenant_support_inbox import database,db,A,B,CA,CB,AGENT,PROFILE,manual,seed_job
import tenant_operator_dialogs as dialogs
import tenant_ai_agents_store as agents
import tenant_support_inbox_store as tickets
import tenant_chat_send_store as sender
from tenant_linear_policy import TenantLinearError
from tenant_policy import TenantPolicy


@pytest.fixture
def operator_db(db,monkeypatch):
    monkeypatch.setattr(dialogs,'_connect',lambda:db)
    monkeypatch.setattr(sender,'_connect',lambda:db)
    monkeypatch.setattr(dialogs,'runtime_flag',lambda _key:True)
    monkeypatch.setattr(agents,'runtime_flag',lambda _key:True)
    return db


def employee(db,permissions,role='manager',actor=33):
    db.execute('''INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions)
      VALUES(%s::uuid,%s,%s,'Operator',%s::jsonb)''',(A,actor,role,json.dumps(permissions)));db.commit()
    return {'tenant_id':A,'telegram_id':actor}


def test_all_connect_conversations_are_visible_without_ai_or_kommo(operator_db):
    result=dialogs.listing(PROFILE)
    assert len(result['dialogs'])==1
    assert result['dialogs'][0]['display_state']=='unmanaged'
    assert result['dialogs'][0]['channel_id']==CA
    assert result['attention_count']==0
    assert result['channels']==[{'id':'connect:'+CA,'name':'WhatsApp'}]
    page=dialogs.history(PROFILE,'connect',41,CA,'client')
    assert page['messages'][0]['body']=='Internet is down'
    assert page['dialog']['can_send'] is False


def test_existing_unassigned_dialog_lists_candidate_without_mutating_state(operator_db):
    operator_db.execute("INSERT INTO saas_ai_agent_settings(tenant_id,enabled,operating_mode) VALUES(%s::uuid,TRUE,'active') ON CONFLICT(tenant_id) DO UPDATE SET enabled=TRUE,operating_mode='active'",(A,))
    operator_db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status) VALUES(%s::uuid,'connect','connected') ON CONFLICT(tenant_id,provider) DO UPDATE SET status='connected'",(A,))
    operator_db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,enabled,mode,scopes) VALUES(%s::uuid,%s::uuid,'Night support',11,TRUE,'manual',%s::jsonb)",
        (A,AGENT,json.dumps([{'provider':'connect','channel_id':CA}])));operator_db.commit()
    before=operator_db.execute('SELECT * FROM saas_ai_conversation_state ORDER BY tenant_id,provider,channel_id,conversation_id');operator_db.commit()
    row=dialogs.listing(PROFILE)['dialogs'][0]
    after=operator_db.execute('SELECT * FROM saas_ai_conversation_state ORDER BY tenant_id,provider,channel_id,conversation_id');operator_db.commit()
    assert row['agent_assignment']['allowed'] is True, row['agent_assignment']
    assert [candidate['id'] for candidate in row['agent_assignment']['agents']]==[AGENT]
    assert before==after
    assert not operator_db.execute('SELECT * FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid',(A,));operator_db.commit()


def test_connect_media_filename_exposes_only_safe_basename():
    assert dialogs._media_filename({'document':{'fileName':'/private/path/manual.pdf'}})=='manual.pdf'
    assert dialogs._media_filename({'fileName':'https://private.provider/token/secret.pdf'})==''
    assert dialogs._media_filename({'file_name':'//private.provider/secret.pdf'})==''
    assert dialogs._media_filename({'filename':'unsafe\nname.pdf'})==''


def test_dialogs_endpoint_lists_real_postgres_rows_and_detects_manual_handoff(operator_db,monkeypatch):
    seed_job(operator_db)
    executed=[]
    original_execute=operator_db.execute
    def capture_execute(sql,params=()):
        executed.append(sql)
        return original_execute(sql,params)
    monkeypatch.setattr(operator_db,'execute',capture_execute)
    from tenant_operator_api import handler
    handle=handler(lambda _request:PROFILE,'https://crm.test',Mock(),'dialogs')
    request=make_mocked_request('GET','/api/platform/ai/dialogs')
    response=asyncio.run(handle(request))
    assert response.status==200
    body=json.loads(response.text)
    assert body['success'] is True and len(body['result']['dialogs'])==1
    assert body['result']['dialogs'][0]['conversation_id']=='client'
    projection_sql='\n'.join(executed)
    for pattern in ("LIKE 'handoff:%%'","LIKE 'manual-handoff:%%'","LIKE 'group-handoff:%%'","NOT LIKE 'connect:%%'"):
        assert pattern in projection_sql

    agents.set_conversation_state(PROFILE,'client','human','Manual handoff','connect',CA)
    response=asyncio.run(handle(make_mocked_request('GET','/api/platform/ai/dialogs')))
    assert response.status==200
    dialog=json.loads(response.text)['result']['dialogs'][0]
    assert dialog['handoff_active'] is True
    assert dialog['handoff_source']=='manual'
    assert dialog['handoff_event_id'].startswith('manual-handoff:')

def test_operator_rights_do_not_grant_configuration(operator_db):
    person=employee(operator_db,['dialogs','support'])
    assert dialogs.listing(person)['dialogs']
    with pytest.raises(TenantLinearError) as caught:agents.list_agents(person)
    assert caught.value.status==403
    agents.set_conversation_state(person,'client','human','operator_handoff','connect',CA)
    ticket=tickets.listing(person)['tickets'][0]
    assert ticket['ai_state']=='human'
    tickets.update(person,ticket['id'],{'assigned_to':33,'status':'in_progress'})
    assert tickets.listing(person,assignee='me')['tickets'][0]['assigned_to']==33


def test_assignment_is_separate_from_handoff_and_sends_nothing(operator_db):
    seed_job(operator_db)
    person=employee(operator_db,['support'])
    ticket=manual()['ticket']['id']
    before=operator_db.execute('SELECT * FROM saas_ai_conversation_state')[0];operator_db.commit()
    tickets.update(person,ticket,{'status':'in_progress','assigned_to':33})
    after=operator_db.execute('SELECT * FROM saas_ai_conversation_state')[0];operator_db.commit()
    assert before==after and after['state']=='active'
    with pytest.raises(TenantLinearError):agents.set_conversation_state(person,'client','human','operator_handoff','connect',CA)
    assert operator_db.execute('SELECT count(*) AS n FROM saas_crm_messages')[0]['n']==2;operator_db.commit()


def test_dialog_filters_counters_and_ticket_search(operator_db):
    seed_job(operator_db)
    result=dialogs.listing(PROFILE,agent=AGENT,state='manual')
    assert result['dialogs'][0]['agent_id']==AGENT and result['total_count']==1
    assert dialogs.listing(PROFILE,channel='connect:'+CB)['dialogs']==[]
    assert dialogs.listing(PROFILE,search='CLIENT')['dialogs']
    agents.set_conversation_state(PROFILE,'client','paused',provider='connect',channel_id=CA)
    assert dialogs.listing(PROFILE,state='manual')['total_count']==1
    assert dialogs.listing(PROFILE,state='attention')['total_count']==0
    assert dialogs.listing(PROFILE,state='attention')['attention_count']==1
    agents.set_conversation_state(PROFILE,'client','human',provider='connect',channel_id=CA)
    result=tickets.listing(PROFILE,search='Internet',channel_id=CA)
    assert result['unresolved_count']==1 and len(result['tickets'])==1
    assert tickets.listing(PROFILE,search='not-here')['tickets']==[]
    assert tickets.listing(PROFILE,channel_id=CB)['tickets']==[]


def test_normalized_status_filter_runs_before_pagination_and_returns_total(operator_db):
    import uuid
    db=operator_db
    db.execute("INSERT INTO saas_ai_agent_settings(tenant_id,enabled,operating_mode) VALUES(%s::uuid,TRUE,'active') ON CONFLICT(tenant_id) DO UPDATE SET enabled=TRUE,operating_mode='active'",(A,))
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status) VALUES(%s::uuid,'connect','connected') ON CONFLICT(tenant_id,provider) DO UPDATE SET status='connected'",(A,))
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,enabled,mode,scopes) VALUES(%s::uuid,%s::uuid,'Manual agent',11,TRUE,'manual',%s::jsonb)",(A,AGENT,json.dumps([{'provider':'connect','channel_id':CA}])))
    for index in range(55):
        db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,%s,%s)",(A,CA,'filter-'+str(index),1000+index))
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,agent_id,state) VALUES(%s::uuid,'connect',%s,'filter-54',%s::uuid,'paused')",(A,CA,AGENT));db.commit()
    first=dialogs.listing(PROFILE,state='manual',offset=0)
    assert first['total_count']==1 and [row['conversation_id'] for row in first['dialogs']]==['filter-54']
    assert first['next_offset'] is None
    needing=dialogs.listing(PROFILE,channel='connect:'+CA,state='needs_assignment',offset=0)
    assert needing['total_count']==55 and len(needing['dialogs'])==50 and needing['next_offset']==50
    second=dialogs.listing(PROFILE,channel='connect:'+CA,state='needs_assignment',offset=50)
    assert second['total_count']==55 and len(second['dialogs'])==5 and second['next_offset'] is None


def test_automatic_agent_in_test_mode_is_not_classified_as_live_automatic(operator_db):
    seed_job(operator_db)
    operator_db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status) VALUES(%s::uuid,'connect','connected') ON CONFLICT(tenant_id,provider) DO UPDATE SET status='connected'",(A,))
    operator_db.execute("UPDATE saas_ai_agents SET mode='automatic' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT));operator_db.commit()
    row=dialogs.listing(PROFILE,state='attention')['dialogs'][0]
    assert row['agent_status']=='attention' and row['agent_status_reason']=='test_binding_required'
    assert dialogs.listing(PROFILE,state='automatic')['total_count']==0


@pytest.mark.parametrize('case',['archived','inactive','missing_permission','foreign_tenant'])
def test_live_authority_and_tenant_isolation(operator_db,case):
    person=employee(operator_db,['dialogs','support'])
    if case=='archived':operator_db.execute("UPDATE saas_tenants SET status='archived' WHERE id=%s::uuid",(A,))
    elif case=='inactive':operator_db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=33',(A,))
    elif case=='missing_permission':operator_db.execute("UPDATE saas_tenant_members SET permissions='[]' WHERE tenant_id=%s::uuid AND telegram_id=33",(A,))
    else:person={'tenant_id':B,'telegram_id':33}
    operator_db.commit()
    for operation in (lambda:dialogs.listing(person),lambda:dialogs.history(person,'connect',41,CA,'client')):
        with pytest.raises(TenantLinearError):operation()


def test_foreign_or_mismatched_channel_cannot_open_or_change_dialog(operator_db):
    person=employee(operator_db,['dialogs'])
    with pytest.raises(TenantLinearError):dialogs.history(person,'connect',41,CB,'client')
    with pytest.raises(TenantLinearError):dialogs.history(person,'connect',42,CB,'client')
    with pytest.raises(TenantLinearError):agents.set_conversation_state(person,'client','human','operator_handoff','connect',CB)
    assert operator_db.execute('SELECT count(*) AS n FROM saas_ai_conversation_state')[0]['n']==0;operator_db.commit()


def test_ambiguous_jid_stays_readable_but_controls_cannot_cross_channels(operator_db):
    other=str(uuid.uuid4())
    operator_db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Second')",(A,other))
    operator_db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,'client',43)",(A,other));operator_db.commit()
    operator_db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,state) VALUES(%s::uuid,'connect','client','human')",(A,));operator_db.commit()
    person=employee(operator_db,['dialogs'])
    rows=dialogs.listing(person)['dialogs']
    assert len(rows)==2 and all(not row['controls_allowed'] for row in rows)
    assert dialogs.history(person,'connect',43,other,'client')['messages']==[]
    assert len(dialogs.history(person,'connect',41,CA,'client')['messages'])==1
    with pytest.raises(TenantLinearError):agents.set_conversation_state(person,'client','paused',provider='connect',channel_id=CA)


def test_connect_send_authority_needs_no_crm_deal_or_kommo(operator_db):
    person=employee(operator_db,['dialogs'])
    from cryptography.fernet import Fernet
    cipher=Fernet(Fernet.generate_key())
    secret=cipher.encrypt(json.dumps({'api_key':'test-connect-key'}).encode())
    operator_db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets) VALUES(%s::uuid,'connect','connected',%s)",(A,secret));operator_db.commit()
    with patch('tenant_connect_transport._fernet',return_value=cipher):
        with operator_db.cursor() as cur:profile,connection,credentials=sender.authority(cur,person,41)
    assert connection==['connect',CA,'client'] and credentials['provider']=='connect'
    assert profile['telegram_id']==33


def test_operator_modules_are_explicit_permissions_not_ai_editor_access():
    for role in ('manager','worker','admin'):
        policy=TenantPolicy({'role':role,'active':True,'permissions':['dialogs','support']})
        assert policy.allows('dialogs') and policy.allows('support')
        assert not policy.allows('ai_agents')
    assert not TenantPolicy({'role':'worker','permissions':['tasks']}).allows('dialogs')
    assert not TenantPolicy({'role':'master','permissions':['dialogs','support']}).allows('support')


def test_kommo_dialogs_keep_pipeline_scope_and_deny_unassigned_operator(operator_db):
    db=operator_db
    person=employee(db,['dialogs','deals'])
    db.execute("UPDATE saas_tenants SET modules=%s::jsonb WHERE id=%s::uuid",(json.dumps({'deals':True}),A))
    db.execute("INSERT INTO saas_tenant_pipelines(tenant_id,pipeline_id,name,owner_telegram_id) VALUES(%s::uuid,'501','Allowed',33),(%s::uuid,'502','Other',44)",(A,A))
    for lead,pipeline in ((51,501),(52,502)):
        db.execute("INSERT INTO saas_crm_deals(tenant_id,kommo_lead_id,pipeline_id,status_id,name) VALUES(%s::uuid,%s,%s,1,'Kommo customer')",(A,lead,pipeline))
        db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body) VALUES(%s::uuid,%s::uuid,%s,%s,'whatsapp','incoming','Kommo message')",(str(uuid.uuid4()),A,lead,str(lead)))
    db.commit()
    assert [row['lead_id'] for row in dialogs.listing(person)['dialogs'] if row['provider']=='kommo']==[51]
    page=dialogs.history(person,'kommo',51,'whatsapp','')
    assert page['messages'][0]['body']=='Kommo message'
    assert not page['dialog']['controls_allowed']
    with pytest.raises(TenantLinearError):dialogs.history(person,'kommo',52,'whatsapp','')
    db.execute("UPDATE saas_tenant_members SET permissions=%s::jsonb WHERE tenant_id=%s::uuid AND telegram_id=33",(json.dumps(['dialogs']),A));db.commit()
    assert all(row['provider']=='connect' for row in dialogs.listing(person)['dialogs'])
    with pytest.raises(TenantLinearError):dialogs.history(person,'kommo',51,'whatsapp','')


def test_ticket_shows_effective_ai_state_without_changing_it(operator_db):
    seed_job(operator_db)
    ticket=manual()['ticket']['id']
    operator_db.execute("UPDATE saas_ai_agent_settings SET emergency_stop=TRUE WHERE tenant_id=%s::uuid",(A,));operator_db.commit()
    assert tickets.detail(PROFILE,ticket)['ticket']['ai_state']=='blocked'
    assert operator_db.execute('SELECT state FROM saas_ai_conversation_state')[0]['state']=='active'
    operator_db.commit()
    operator_db.execute("DELETE FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid",(A,))
    operator_db.execute("UPDATE saas_ai_agent_settings SET emergency_stop=FALSE WHERE tenant_id=%s::uuid",(A,));operator_db.commit()
    assert tickets.detail(PROFILE,ticket)['ticket']['ai_state']=='active'
    assert operator_db.execute('SELECT count(*) AS n FROM saas_ai_conversation_state')[0]['n']==0
    operator_db.commit()


def test_group_history_keeps_each_participant_and_outbound_author(operator_db):
    group='120363411822115409@g.us'
    operator_db.execute("UPDATE saas_connect_conversations SET conversation_id=%s,display_name='Night support',conversation_kind='group',phone='' WHERE tenant_id=%s::uuid AND lead_id=41",(group,A))
    for direction,author,body,raw in [
        ('incoming','Leyla','First request',{'participantJid':'994501234567@s.whatsapp.net'}),
        ('incoming','Samir','Second request',{'participantJid':'994551112233@s.whatsapp.net'}),
        ('outgoing','AI Agent','AI response',{'run_id':'run-1'}),
        ('outgoing','Operator','Operator response',{'source':'connect'}),
    ]:
        operator_db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,author_name,body,raw)
          VALUES(%s,%s::uuid,41,%s,%s,%s,%s,%s,%s::jsonb)''',
          (str(uuid.uuid4()),A,str(uuid.uuid4()),'connect:'+CA,direction,author,body,json.dumps(raw)))
    operator_db.commit()
    page=dialogs.history(PROFILE,'connect',41,CA,group)
    assert page['dialog']['customer_name']=='Night support'
    assert page['dialog']['customer_kind']=='group'
    by_body={message['body']:message for message in page['messages']}
    assert by_body['First request']['author_type']=='participant'
    assert by_body['First request']['participant_jid']=='994501234567@s.whatsapp.net'
    assert by_body['Second request']['author_name']=='Samir'
    assert by_body['AI response']['direction']=='outgoing' and by_body['AI response']['author_type']=='ai'
    assert by_body['Operator response']['direction']=='outgoing' and by_body['Operator response']['author_type']=='operator'


def test_group_and_participant_manual_names_persist_per_tenant_and_channel(operator_db):
    group='120363411822115409@g.us'
    second=str(uuid.uuid4())
    operator_db.execute("UPDATE saas_connect_conversations SET conversation_id=%s,display_name='Evolution subject',conversation_kind='group',phone='' WHERE tenant_id=%s::uuid AND lead_id=41",(group,A))
    operator_db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Second')",(A,second))
    operator_db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,display_name,conversation_kind) VALUES(%s::uuid,%s::uuid,%s,43,'Other subject','group')",(A,second,group))
    for lead,channel,body in [(41,CA,'First channel'),(43,second,'Second channel')]:
        operator_db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,author_name,body,raw)
          VALUES(%s,%s::uuid,%s,%s,%s,'incoming','WhatsApp name',%s,%s::jsonb)''',
          (str(uuid.uuid4()),A,lead,str(uuid.uuid4()),'connect:'+channel,body,json.dumps({'participantJid':'994501234567@s.whatsapp.net','participantAlt':'lid-123@lid','pushName':'WhatsApp name'})))
    operator_db.commit()
    dialogs.save_display_name(PROFILE,{'provider':'connect','kind':'conversation','lead_id':41,'channel_id':CA,'conversation_id':group,'display_name':'Night support'})
    dialogs.save_display_name(PROFILE,{'provider':'connect','kind':'participant','lead_id':41,'channel_id':CA,'conversation_id':group,'participant_jid':'994501234567@s.whatsapp.net','display_name':'Leyla'})
    # A fresh listing/history read models a browser reload.
    first=dialogs.history(PROFILE,'connect',41,CA,group)
    again=dialogs.history(PROFILE,'connect',41,CA,group)
    second_page=dialogs.history(PROFILE,'connect',43,second,group)
    assert first['dialog']['customer_name']==again['dialog']['customer_name']=='Night support'
    assert next(item for item in first['messages'] if item['body']=='First channel')['author_name']=='Leyla'
    assert next(item for item in first['messages'] if item['body']=='First channel')['participant_alt']=='lid-123@lid'
    assert second_page['dialog']['customer_name']=='Other subject'
    assert next(item for item in second_page['messages'] if item['body']=='Second channel')['author_name']=='WhatsApp name'


def test_manual_display_names_require_dialog_access_and_exact_route(operator_db):
    saved=dialogs.save_display_name(PROFILE,{'provider':'connect','kind':'conversation','lead_id':41,'channel_id':CA,'conversation_id':'client','display_name':'Saved contact'})
    assert saved['display_name']=='Saved contact'
    assert dialogs.history(PROFILE,'connect',41,CA,'client')['dialog']['customer_name']=='Saved contact'
    person=employee(operator_db,['support'])
    with pytest.raises(TenantLinearError):
        dialogs.save_display_name(person,{'provider':'connect','kind':'conversation','lead_id':41,'channel_id':CA,'conversation_id':'client','display_name':'No access'})
    with pytest.raises(TenantLinearError):
        dialogs.save_display_name(PROFILE,{'provider':'connect','kind':'conversation','lead_id':41,'channel_id':CB,'conversation_id':'client','display_name':'Wrong channel'})


def test_direct_inbound_message_uses_the_route_contact_name_without_changing_other_authors():
    incoming={'direction':'incoming','author_name':'994501234567','raw':{}}
    outgoing={'direction':'outgoing','author_name':'Operator','raw':{}}
    assert dialogs._message(incoming,contact_name='Saved contact')['author_name']=='Saved contact'
    assert dialogs._message(incoming,contact_name='WhatsApp pushName')['author_name']=='WhatsApp pushName'
    assert dialogs._message(outgoing,contact_name='Saved contact')['author_name']=='Operator'


def test_group_participant_name_priority_is_manual_then_saved_then_whatsapp_then_number():
    raw={'participantJid':'994501234567@s.whatsapp.net','participantAlt':'lid-1@lid','pushName':'WhatsApp name'}
    stored={'direction':'incoming','author_name':'Saved name','raw':raw}
    assert dialogs._message(stored,group=True,participant_names={'994501234567@s.whatsapp.net':'Manual name'})['author_name']=='Manual name'
    assert dialogs._message(stored,group=True)['author_name']=='Saved name'
    assert dialogs._message({'direction':'incoming','author_name':'','raw':raw},group=True)['author_name']=='WhatsApp name'
    assert dialogs._message({'direction':'incoming','author_name':'','raw':{'participantJid':'994501234567@s.whatsapp.net'}},group=True)['author_name']=='994501234567'


def test_dialogue_actions_are_derived_from_availability_not_only_storage_state(operator_db):
    base={'provider':'connect','conversation_id':'client','channel_active':True,'unambiguous':True,'connected':True,
          'tenant_status':'active','company_enabled':True,'company_emergency_stop':False,'agent_id':AGENT,
          'agent_enabled':True,'agent_mode':'automatic','agent_scopes':[{'provider':'connect','channel_id':'channel'}],'channel_id':'channel','state':'active','reason':'','latest_job_state':'queued','latest_job_error':'',
          'customer_name':'Client','phone':'994501234567','conversation_kind':'','updated_at':None,'display_state':'active'}
    active=dialogs.decorate(base)
    assert active['ai_availability']=='available' and active['available_actions']==['pause','handoff']
    replied=dialogs.decorate({**base,'state':'human','reason':'manager_replied'})
    assert replied['handoff_active'] is False and replied['available_actions']==['resume']
    paused=dialogs.decorate({**base,'state':'paused','reason':'manager_pause'})
    assert paused['available_actions']==['resume']
    blocked=dialogs.decorate({**base,'company_emergency_stop':True})
    assert blocked['ai_availability']=='blocked' and blocked['available_actions']==[]
    manual=dialogs.decorate({**base,'agent_mode':'manual'})
    assert manual['available_actions']==['handoff']
    manual_paused=dialogs.decorate({**base,'agent_mode':'manual','state':'paused','reason':'manager_pause'})
    assert manual_paused['available_actions']==['resume']
    failed=dialogs.decorate({**base,'latest_job_state':'failed','latest_job_error':'connect_policy_blocked'})
    assert failed['display_state']=='error' and failed['available_actions']==[]
