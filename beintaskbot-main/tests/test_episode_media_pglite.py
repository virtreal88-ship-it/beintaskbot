"""Exact-version runtime claims and spend admission against isolated PostgreSQL."""
from unittest.mock import Mock
import pytest
pytest_plugins=('test_tenant_support_inbox',)
from test_tenant_support_inbox import A,CA,db
import tenant_episode_media as runtime
from tenant_linear_policy import TenantLinearError


@pytest.fixture
def flights(db,monkeypatch):
    runtime._schema(db)
    db.execute('DELETE FROM saas_episode_media_flights');db.commit()
    monkeypatch.setattr(runtime,'_connect',lambda:db)
    return db


def authority():return type('Authority',(),{'tenant':A,'check':Mock()})()


def ref(version='v1',channel=CA):
    return {'external_id':'connect:unit','media_kind':'voice','body_hash':'body',
            'media_version':version,'channel_id':channel}


def test_committed_claim_joins_then_reuses_result_without_new_token(flights,monkeypatch):
    auth=authority();key,token,result=runtime._flight(auth,ref())
    assert token and result is None
    def finish_during_join(_):
        flights.execute("UPDATE saas_episode_media_flights SET state='completed',result=%s::jsonb WHERE tenant_id=%s::uuid",
                        ('{"status":"processed","transcript":"one result"}',A));flights.commit()
    monkeypatch.setattr(runtime.time,'sleep',finish_during_join)
    same,second,reused=runtime._flight(auth,ref())
    assert same==key and second is None and reused['transcript']=='one result'
    assert flights.execute('SELECT count(*) AS n FROM saas_episode_media_flights')[0]['n']==1
    flights.commit()


def test_changed_version_gets_new_flight_unknown_version_never_reclaimed(flights):
    auth=authority();first=runtime._flight(auth,ref())
    flights.execute("UPDATE saas_episode_media_flights SET state='unknown' WHERE tenant_id=%s::uuid",(A,));flights.commit()
    old=runtime._flight(auth,ref())
    assert old[1] is None and old[2]['status']=='unknown'
    fresh=runtime._flight(auth,ref('v2'))
    assert fresh[1] and fresh[0]!=first[0]


def test_same_attachment_in_other_tenant_or_channel_is_not_reused(flights):
    from test_tenant_support_inbox import B,CB
    a=authority();runtime._flight(a,ref())
    different_channel=runtime._flight(a,ref(channel=CB))
    b=authority();b.tenant=B
    different_tenant=runtime._flight(b,ref())
    assert different_channel[1] and different_tenant[1]


def test_receipt_publication_revalidates_source_and_is_reusable(flights):
    import uuid
    import tenant_media_evidence as evidence
    row={'external_id':'connect:unit','body':'caption','message_type':'image','raw':{'mediaId':'media1'}}
    flights.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,message_type,raw)
      VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming',%s,%s,%s::jsonb)''',
      (str(uuid.uuid4()),A,row['external_id'],'connect:'+CA,row['body'],row['message_type'],'{"mediaId":"media1"}'))
    flights.commit()
    auth=authority();auth.channel=CA;auth.lead=41
    selected=runtime.reference(row,CA,41)
    key,token,_=runtime._flight(auth,selected)
    result={'id':selected['id'],'status':'processed','code':'vision_observation','observation':'Visible E17'}
    runtime._finish(auth,selected,key,token,result)
    saved=flights.execute('SELECT status,result FROM saas_media_evidence_receipts WHERE tenant_id=%s::uuid',(A,))[0]
    assert saved['status']=='processed' and saved['result']['observation']=='Visible E17'
    flights.commit()
    # A later changed source cannot be published under the original metadata key.
    changed=runtime.reference({**row,'raw':{'mediaId':'media2'}},CA,41)
    key,token,_=runtime._flight(auth,changed)
    with pytest.raises(TenantLinearError):runtime._finish(auth,changed,key,token,{**result,'id':changed['id']})


def test_manual_media_budget_denied_rolls_back_extra_call_and_never_invokes(flights,monkeypatch):
    import uuid,json
    import tenant_ai_spend_budget as budget
    monkeypatch.setattr(budget,'_connect',lambda:flights)
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:1}))
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({'gpt-4o-mini':{'vision_low':2}}))
    request=str(uuid.uuid4())
    flights.execute('''INSERT INTO saas_chat_ai_runs(tenant_id,request_id,requested_by,lead_id,input_hash,config_version,status,reserved_calls,provider,channel_id,conversation_id)
      VALUES(%s::uuid,%s::uuid,11,41,'hash','v1','sending',1,'connect',%s,'client')''',(A,request,CA))
    flights.commit()
    auth=runtime.ManualExecution(A,11,{'channel_id':CA,'conversation_id':'client','lead_id':41,'request_id':request},
        {'daily_limit':50,'copilot_daily_limit':50},'v1','unit-test','gpt-4o-mini','az',[])
    auth.check=Mock();auth.validate=Mock();auth.current_ref={}
    invoke=Mock()
    with pytest.raises(TenantLinearError):auth.dispatch('gpt-4o-mini','vision_low',invoke)
    invoke.assert_not_called()
    assert flights.execute('SELECT reserved_calls FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(A,request))[0]['reserved_calls']==1
    assert flights.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations')[0]['n']==0
    flights.commit()


@pytest.mark.parametrize('versioned,state,error,expected',[
    (True,'completed','','processed'),(False,'completed','','unavailable'),
    (False,'processing','','unavailable'),(False,'failed','voice_media_unavailable',None)])
def test_legacy_voice_requires_exact_version_or_safe_pre_dispatch_failure(flights,versioned,state,error,expected):
    import uuid,json
    from tenant_media_evidence import media_version
    raw={'mediaId':'original'}
    if versioned:raw['voice_transcription']={'media_version':media_version(raw),'status':'completed'}
    row={'external_id':'connect:unit','body':'saved transcript','message_type':'voice','raw':raw}
    flights.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,message_type,raw)
      VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming',%s,'voice',%s::jsonb)''',
      (str(uuid.uuid4()),A,row['external_id'],'connect:'+CA,row['body'],json.dumps(raw)))
    flights.execute('''INSERT INTO saas_connect_voice_receipts(tenant_id,channel_id,external_id,run_id,model,state,error_code)
      VALUES(%s::uuid,%s::uuid,%s,%s::uuid,'mock',%s,%s)''',(A,CA,row['external_id'],str(uuid.uuid4()),state,error))
    flights.commit()
    auth=authority();selected=runtime.reference(row,CA,41)
    _,token,result=runtime._flight(auth,selected)
    if expected:
        assert token is None and result['status']==expected
        if expected=='processed':assert result['transcript']=='saved transcript'
    else:assert token and result is None


def test_media_tokens_are_summed_once_with_generation_without_repricing_media_as_text(flights,monkeypatch):
    import uuid
    import tenant_ai_agent_run_store as runs
    from test_tenant_support_inbox import seed_job,AGENT
    seed_job(flights)
    run_id=str(uuid.uuid4())
    flights.execute('''INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state,reserved_calls)
      VALUES(%s::uuid,%s::uuid,%s::uuid,'evt',1,'gpt-4o-mini','generating',3)''',(A,run_id,AGENT));flights.commit()
    job={'tenant_id':A,'channel_id':CA,'conversation_id':'client','lead_id':41,'agent_id':AGENT}
    reservation={'run_id':run_id,'model':'gpt-4o-mini','api_key':'mock'}
    execution=runtime.AutoExecution(job,reservation)
    execution.record_usage({'input_tokens':10,'output_tokens':2,'total_tokens':12})
    execution.record_usage({'input_tokens':20,'output_tokens':3,'total_tokens':23})
    runs.record_token_usage(job,reservation,{'input_tokens':5,'output_tokens':2,'total_tokens':7},cumulative=True,generated=True)
    result=flights.execute('SELECT input_tokens,output_tokens,total_tokens FROM saas_ai_agent_token_usage WHERE tenant_id=%s::uuid AND run_id=%s::uuid',(A,run_id))[0]
    assert result=={'input_tokens':35,'output_tokens':7,'total_tokens':42}
    flights.commit()
