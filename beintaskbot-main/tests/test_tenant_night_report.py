"""Night shift accounting against PostgreSQL/WASM, with no external services."""
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch
import pytest
from test_tenant_support_inbox import database, db, A, B, CA, CB, AGENT, PROFILE, seed_job
from tenant_linear_policy import TenantLinearError
import tenant_night_report_store as store
from tenant_night_report_period import interval

NOW=datetime(2026,10,16,12,tzinfo=timezone.utc)
CC=str(uuid.UUID(int=901))

@pytest.fixture
def night_db(db,monkeypatch):
    monkeypatch.setattr(store,'_connect',lambda:db)
    # Establish the existing sender schema once, outside the read-only report transaction.
    store.ensure_send_schema(db);db.commit()
    return db


def report(**args):return store.report(PROFILE,'2026-10-15','UTC','19:00','09:00',now=NOW,**args)

def event(db,name,channel=CA,conversation='client',state='completed',at='2026-10-15T21:00:00Z',error=''):
    db.execute('''INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider,event_type,payload,state,received_at,completed_at,error_code)
      VALUES(%s::uuid,%s,'connect','incoming_message',%s::jsonb,%s,%s::timestamptz,%s::timestamptz,%s)''',
      (A,name,json.dumps({'channel_id':channel,'conversation_id':conversation}),state,at,at,error))

def message(db,external,lead=41,channel=CA,at='2026-10-15T21:00:00Z',tenant=A,body='No internet'):
    db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,happened_at)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s,'incoming',%s,%s::timestamptz)''',
      (str(uuid.uuid4()),tenant,lead,external,'connect:'+channel,body,at))

def ticket(db,id=None,conversation='client',channel=CA,created='2026-10-15T21:00:00Z',status='waiting',source='ai_handoff'):
    id=id or str(uuid.uuid4())
    db.execute('''INSERT INTO saas_support_tickets(tenant_id,id,channel_id,conversation_id,source,status,description,summary,summary_state,created_at,updated_at)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,'Internet down','AI suggested restarting router','completed',%s::timestamptz,%s::timestamptz)''',
      (A,id,channel,conversation,source,status,created,created))
    return id

def audit(db,id,payload,at='2026-10-15T22:00:00Z',action='support_update',entity='support_ticket'):
    db.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload,created_at)
      VALUES(%s::uuid,%s::uuid,11,%s,%s,%s,%s::jsonb,%s::timestamptz)''',
      (str(uuid.uuid4()),A,action,entity,id,json.dumps(payload),at))

def route(db,channel=CC,conversation='client',lead=43):
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Second') ON CONFLICT DO NOTHING",(A,channel))
    db.execute('''INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name)
      VALUES(%s::uuid,%s::uuid,%s,%s,'Customer')''',(A,channel,conversation,lead))


def test_all_metrics_deduplicate_routes_handoffs_claims_and_failures(night_db):
    db=night_db
    seed_job(db)
    db.execute("UPDATE saas_tenant_webhook_events SET payload=%s::jsonb,received_at='2026-10-15T21:00Z',completed_at='2026-10-15T21:00Z' WHERE tenant_id=%s::uuid AND fingerprint='evt'",(json.dumps({'channel_id':CA,'conversation_id':'client'}),A))
    route(db)
    message(db,'one',at='2026-10-15T19:00:00Z');message(db,'two');message(db,'same-jid-other-channel',43,CC)
    for revision,state in ((1,'completed'),(2,'failed'),(3,'unknown')):
        db.execute('''INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state,created_at,updated_at)
          VALUES(%s::uuid,%s::uuid,%s::uuid,'evt',%s,'test',%s,'2026-10-15T21:00Z','2026-10-15T21:05Z')''',(A,str(uuid.uuid4()),AGENT,revision,state))
    first=ticket(db)
    for name in ('handoff-one','handoff-two'):
        event(db,name)
        db.execute('INSERT INTO saas_support_events(tenant_id,event_key,ticket_id) VALUES(%s::uuid,%s,%s::uuid) ON CONFLICT DO NOTHING',(A,'handoff:'+name,first))
    audit(db,'client',{'provider':'connect','channel_id':CA},action='ai_conversation_human',entity='conversation')
    ticket(db,channel=CC,created='2026-10-14T20:00Z',status='new',source='manual')
    audit(db,first,{'status':'in_progress','assigned_to':11});audit(db,first,{'status':'in_progress','assigned_to':11},at='2026-10-15T23:00Z')
    db.execute("UPDATE saas_support_tickets SET status='in_progress',assigned_to=11 WHERE id=%s::uuid",(first,))
    event(db,'evt-fail',CC,state='failed',error='connect_failed')
    # A run failure echoed by its webhook must not also count as a Connect incident.
    db.execute("UPDATE saas_tenant_webhook_events SET state='failed',error_code='model_failed' WHERE tenant_id=%s::uuid AND fingerprint='evt'",(A,))
    db.execute("INSERT INTO saas_chat_send_receipts(tenant_id,actor_id,request_id,lead_id,input_hash,state,updated_at) VALUES(%s::uuid,11,%s::uuid,43,'hash','unknown','2026-10-15T22:00Z')",(A,str(uuid.uuid4())))
    message(db,'foreign',42,CB,tenant=B)
    db.commit()
    result=report()
    assert result['metrics']=={'dialogs':2,'ai_dialogs':1,'handed_off':1,'unresolved':2,'ai_errors':1,'connect_errors':2,'taken_in_work':1,'attention_count':2}
    assert len(result['tickets'])==2 and len(result['failures'])==3
    json.dumps(result)  # The actual API serializer must not encounter UUID/datetime objects.
    assert result['tickets'][0]['summary']=='AI suggested restarting router'
    assert result['tickets'][0]['assignee_name']=='Owner'
    assert sum(t['created_in_shift'] for t in result['tickets'])==1


def test_shift_bounds_are_half_open_and_timezone_aware(night_db):
    db=night_db
    route(db,conversation='before',lead=44);route(db,conversation='last',lead=45)
    message(db,'before',44,CC,'2026-10-15T14:59:59Z')
    message(db,'start',at='2026-10-15T15:00:00Z')
    message(db,'last',45,CC,'2026-10-16T04:59:59Z')
    route(db,conversation='end',lead=46);message(db,'excluded-end',46,CC,'2026-10-16T05:00:00Z')
    ticket(db,created='2026-10-16T05:00:00Z')
    db.commit()
    result=store.report(PROFILE,'2026-10-15','Asia/Baku','19:00','09:00',now=NOW)
    assert result['period']['starts_at_utc']=='2026-10-15T15:00:00+00:00'
    assert result['period']['ends_at_utc']=='2026-10-16T05:00:00+00:00'
    assert result['metrics']['dialogs']==2 and result['metrics']['unresolved']==0


def test_historical_unresolved_is_separate_from_current_queue(night_db):
    db=night_db
    first=ticket(db,status='resolved')
    audit(db,first,{'status':'resolved'},at='2026-10-16T10:00Z')
    db.execute("UPDATE saas_support_tickets SET resolved_at='2026-10-16T10:00Z' WHERE id=%s::uuid",(first,));db.commit()
    result=report()
    assert result['metrics']['unresolved']==1 and result['metrics']['attention_count']==0
    # Reopened after the shift: resolved at cutoff, but actionable now.
    db.execute("UPDATE saas_support_tickets SET status='waiting',resolved_at=NULL WHERE id=%s::uuid",(first,))
    audit(db,first,{'status':'resolved'},at='2026-10-15T23:00Z')
    audit(db,first,{'status':'waiting'},at='2026-10-16T11:00Z');db.commit()
    result=report()
    assert result['metrics']['unresolved']==0 and result['metrics']['attention_count']==1


@pytest.mark.parametrize('permissions,role,allowed',[([], 'owner',True),([], 'admin',False),(['support'],'manager',False),(['night_report'],'worker',True),(['deals'],'manager',False),(['night_report'],'master',False)])
def test_fresh_membership_permissions(night_db,permissions,role,allowed):
    db=night_db
    db.execute("UPDATE saas_tenant_members SET permissions=%s::jsonb,role=%s WHERE tenant_id=%s::uuid AND telegram_id=11",(json.dumps(permissions),role,A));db.commit()
    if allowed: assert report()['tenant_id']==A
    else:
        with pytest.raises(TenantLinearError) as caught:report()
        assert caught.value.status==403


@pytest.mark.parametrize('case',['archived','suspended','inactive','foreign_user'])
def test_archived_and_foreign_memberships_are_denied(night_db,case):
    db=night_db
    profile=PROFILE
    if case in {'archived','suspended'}:db.execute('UPDATE saas_tenants SET status=%s WHERE id=%s::uuid',(case,A))
    elif case=='inactive':db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=11',(A,))
    else:profile={'tenant_id':B,'telegram_id':11,'role':'owner'}
    db.commit()
    with pytest.raises(TenantLinearError):store.report(profile,now=NOW)
    with pytest.raises(TenantLinearError):store.detail(profile,channel_id=CA,conversation_id='client')


def test_detail_and_pagination_are_tenant_and_channel_scoped_without_ai_mutation(night_db):
    db=night_db
    seed_job(db);first=ticket(db);route(db)
    db.execute('UPDATE saas_support_tickets SET assigned_to=11 WHERE id=%s::uuid',(first,))
    for n in range(56):message(db,'history-'+str(n),at='2026-10-15T21:%02d:00Z'%(n%60))
    message(db,'other-channel',43,CC,body='SECRET other channel')
    db.commit()
    before=db.execute('SELECT * FROM saas_ai_conversation_state');db.commit()
    page=store.detail(PROFILE,ticket_id=first)
    assert page['ticket']['id']==first and len(page['messages'])==50 and page['next_before']
    assert page['ticket']['assignee_name']=='Owner'
    older=store.detail(PROFILE,ticket_id=first,before=page['next_before'])
    assert len(older['messages'])==7
    assert not {m['id'] for m in page['messages']} & {m['id'] for m in older['messages']}
    assert all('SECRET' not in m['body'] for m in page['messages']+older['messages'])
    own_other=store.detail(PROFILE,channel_id=CC,conversation_id='client')
    assert own_other['messages'][0]['body']=='SECRET other channel'
    with pytest.raises(TenantLinearError):store.detail({'tenant_id':B,'telegram_id':22},ticket_id=first)
    with pytest.raises(TenantLinearError):store.detail(PROFILE,channel_id=CB,conversation_id='client')
    assert db.execute('SELECT * FROM saas_ai_conversation_state')==before;db.commit()
    assert db.execute("SELECT count(*) AS n FROM saas_tenant_integrations WHERE provider='kommo'")[0]['n']==0;db.commit()


def test_no_kommo_ai_generation_or_messages_on_report_read(night_db):
    with patch('tenant_connect_transport._request',side_effect=AssertionError('No transport')),\
         patch('tenant_support_inbox_summary.summarize',side_effect=AssertionError('No AI calls')):
        assert report()['scope']=='connect'


def test_default_company_zone_and_agent_schedule_do_not_change_settings(night_db):
    db=night_db
    seed_job(db)
    schedule={'timezone':'America/New_York','days':[0,1,2,3,4],'start':'19:00','end':'09:00'}
    db.execute('UPDATE saas_ai_agents SET schedule=%s::jsonb WHERE tenant_id=%s::uuid',(json.dumps(schedule),A))
    db.execute('UPDATE saas_tenants SET onboarding=%s::jsonb WHERE id=%s::uuid',(json.dumps({'timezone':'Pacific/Auckland'}),A));db.commit()
    result=store.report(PROFILE,'2026-10-15',now=NOW)
    assert result['defaults']['timezone_source']=='company' and result['period']['timezone']=='Pacific/Auckland'
    assert result['period']['start']=='19:00'
    assert db.execute('SELECT schedule FROM saas_ai_agents')[0]['schedule']==schedule;db.commit()


def test_ongoing_is_capped_at_now_and_future_is_empty(night_db):
    message(night_db,'future',at='2026-10-16T08:00:00Z');night_db.commit()
    result=store.report(PROFILE,'2026-10-15','UTC','19:00','09:00',now=datetime(2026,10,16,7,tzinfo=timezone.utc))
    assert result['period']['state']=='ongoing' and result['metrics']['dialogs']==0
    result=store.report(PROFILE,'2026-10-17','UTC',now=NOW)
    assert result['period']['state']=='not_started' and not any(result['metrics'].values())


@pytest.mark.parametrize('day,zone,duration,startutc',[
    ('2026-03-28','Europe/Berlin',13*60,'2026-03-28T18:00:00+00:00'),
    ('2026-10-24','Europe/Berlin',15*60,'2026-10-24T17:00:00+00:00'),
    ('2026-10-15','Asia/Baku',14*60,'2026-10-15T15:00:00+00:00'),
    ('2026-10-15','Pacific/Auckland',14*60,'2026-10-15T06:00:00+00:00'),
    ('2026-10-15','America/New_York',14*60,'2026-10-15T23:00:00+00:00')])
def test_timezones_and_dst(day,zone,duration,startutc):
    result=interval(day,zone,now=NOW)
    assert result['duration_minutes']==duration and result['starts_at_utc']==startutc


@pytest.mark.parametrize('args',[{'zone':'bad/zone'},{'day':'2026-02-31'},{'start':'9:00'},{'start':'19:00','end':'19:00'}, {'day':'2026-03-29','zone':'Europe/Berlin','start':'02:30','end':'09:00'}])
def test_invalid_intervals_fail_explicitly(args):
    with pytest.raises(TenantLinearError):interval(now=NOW,**args)


def test_default_date_is_start_day_of_most_recent_shift():
    assert interval(zone='Asia/Baku',now=datetime(2026,10,16,2,tzinfo=timezone.utc))['date']=='2026-10-15'
    assert interval(zone='Asia/Baku',now=datetime(2026,10,16,17,tzinfo=timezone.utc))['date']=='2026-10-16'


def test_ticket_pagination_counts_all_rows(night_db):
    db=night_db
    for n in range(53):
        route(db,conversation='page-'+str(n),lead=100+n)
        ticket(db,channel=CC,conversation='page-'+str(n),created='2026-10-15T21:00Z')
    db.commit()
    page=report();older=report(offset=page['next_offset'])
    assert page['metrics']['attention_count']==53 and len(page['tickets'])==50 and len(older['tickets'])==3
    assert not {t['id'] for t in page['tickets']} & {t['id'] for t in older['tickets']}
    assert older['next_offset'] is None


def test_admin_existing_ai_manage_flag_can_read_report(night_db):
    db=night_db
    db.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,))
    db.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s::uuid,'members',%s::jsonb)",(A,json.dumps({'11':{'ai_agents_manage':True}})));db.commit()
    assert report()['tenant_id']==A


def test_daytime_interval_and_ambiguous_dst_boundaries():
    day=interval('2026-10-15','UTC','09:00','19:00',now=NOW)
    assert day['ends_at_utc'].startswith('2026-10-15T19:00') and day['duration_minutes']==600
    ambiguous=interval('2026-10-25','Europe/Berlin','01:30','02:30',now=NOW)
    assert ambiguous['duration_minutes']==120


def test_report_permission_can_be_granted_through_existing_member_normalizer():
    from tenant_platform_common import _permissions
    from tenant_policy import TenantPolicy
    assert _permissions(['night_report'],'worker')==['night_report']
    assert _permissions(['night_report'],'master')==[]
    policy=TenantPolicy({'tenant_id':A,'role':'worker','active':True,'permissions':['night_report']})
    assert policy.allows('night_report') and not policy.allows('ai_agents')


def test_failed_voice_and_pre_generation_job_are_reported_once(night_db):
    db=night_db
    seed_job(db)
    db.execute("UPDATE saas_tenant_webhook_events SET payload=%s::jsonb,received_at='2026-10-15T21:00Z' WHERE tenant_id=%s::uuid AND fingerprint='evt'",(json.dumps({'channel_id':CA,'conversation_id':'client'}),A))
    message(db,'connect:voice-message')
    run=str(uuid.uuid4())
    db.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state,created_at,updated_at) VALUES(%s::uuid,%s::uuid,%s::uuid,'evt',1,'test','unknown','2026-10-15T21:00Z','2026-10-15T21:01Z')",(A,run,AGENT))
    db.execute("INSERT INTO saas_connect_voice_receipts(tenant_id,channel_id,external_id,run_id,model,state,error_code,created_at,updated_at) VALUES(%s::uuid,%s::uuid,'connect:voice-message',%s::uuid,'voice','failed','transcription_failed','2026-10-15T21:00Z','2026-10-15T21:01Z')",(A,CA,run))
    db.execute("UPDATE saas_ai_agent_jobs SET state='failed',error_code='connect_worker_failed',updated_at='2026-10-15T21:02Z' WHERE tenant_id=%s::uuid",(A,))
    db.execute("UPDATE saas_tenant_webhook_events SET state='failed' WHERE tenant_id=%s::uuid AND fingerprint='evt'",(A,));db.commit()
    result=report()
    assert result['metrics']['ai_dialogs']==1 and result['metrics']['ai_errors']==1 and result['metrics']['connect_errors']==0
    db.execute('DELETE FROM saas_connect_voice_receipts');db.execute('DELETE FROM saas_ai_agent_runs');db.commit()
    # Failure before generation has no run row, but its job still proves one incident.
    assert report()['metrics']['ai_errors']==1
