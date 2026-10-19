"""Synthetic integration regressions; no external media or AI calls."""
import uuid
from unittest.mock import Mock
import pytest
from test_tenant_support_inbox import database,db,A,B,CA,AGENT,PROFILE
from test_knowledge_candidates import ready,GROUP
from test_knowledge_campaigns import campaign,row,add,approve_fixture
import tenant_knowledge_campaigns as campaigns
import tenant_knowledge_candidates_store as store
from tenant_linear_policy import TenantLinearError


def test_selected_channel_filters_before_group_canonicalization(campaign):
    d,provider,_,key=campaign;second=str(uuid.uuid4())
    d.execute('UPDATE saas_connect_conversations SET conversation_id=%s WHERE tenant_id=%s::uuid',(GROUP,A))
    d.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Other')",(A,second))
    d.execute('INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,%s,43)',(A,second,GROUP))
    d.execute("INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body) VALUES(%s::uuid,%s::uuid,43,'other',%s,'outgoing','MUST NOT ENTER CONTEXT')",(A,str(uuid.uuid4()),'connect:'+second));d.commit()
    route={'channel_id':CA,'conversation_id':GROUP,'history_mode':'unified_group','approved_channel_ids':[CA]}
    with d.cursor() as cur:item=store.snapshot(cur,PROFILE,route)
    assert item['refs'] and all(r['channel_id']==CA for r in item['refs'])
    assert 'MUST NOT ENTER CONTEXT' not in str(item)
    provider.assert_not_called()


def test_source_scope_survives_claim(campaign):
    d,_,_,key=campaign
    add(d,key);approve_fixture(d,key)
    claimed=campaigns.claim(A,key)
    assert claimed['route']['approved_channel_ids']==[CA]
    assert claimed['route']['allow_paid_asr'] is False
    assert claimed['route']['allow_external_media'] is False
    assert claimed['reservation']['calls']==1


@pytest.mark.parametrize('field',['max_main_calls','max_asr_calls','max_vision_calls'])
def test_per_kind_limits_are_independent(field):
    from tenant_knowledge_campaign_policy import validate,reservation,fits
    p=validate({'allow_external_media':True,'allow_paid_asr':True,
                'max_main_calls':12,'max_asr_calls':4,'max_vision_calls':4},20)
    amount=reservation(p)
    assert amount==dict(main=1,asr=1,vision=2,calls=4,tokens=32000)
    assert fits(p,{},amount)
    assert not fits(p,{field[4:-6]:p[field]},amount)


def test_asr_off_uses_no_asr_reserve():
    from tenant_knowledge_campaign_policy import validate,reservation
    p=validate({'allow_external_media':True,'max_vision_calls':4},20)
    assert reservation(p)['asr']==0
    with pytest.raises(TenantLinearError):validate({'allow_paid_asr':True},20)


def test_zero_asr_limit_disables_dispatch_even_with_consent(campaign):
    import json
    d,provider,_,key=campaign
    policy={**row(d,key)['media_policy'],'allow_external_media':True,'allow_paid_asr':True,'max_asr_calls':0}
    d.execute('UPDATE saas_knowledge_campaigns SET media_policy=%s::jsonb',(json.dumps(policy),));d.commit()
    add(d,key);approve_fixture(d,key)
    claimed=campaigns.claim(A,key)
    assert claimed['route']['allow_paid_asr'] is False
    assert claimed['reservation']['asr']==0
    provider.assert_not_called()


@pytest.fixture
def vision(campaign,monkeypatch):
    import tenant_knowledge_media_cache as cache
    import tenant_knowledge_candidates_service as service
    d,_,_,_=campaign
    monkeypatch.setattr(cache,'_connect',lambda:d)
    monkeypatch.setattr(service,'check_active',lambda *a:None)
    ref={'id':'source','channel_id':CA}
    item={'tenant':A,'key':'mock','model':'fixture','refs':[ref],
          'route':{'allow_external_media':True,'allow_vision':True,'approved_channel_ids':[CA]}}
    return cache,item,ref


def test_vision_reuses_across_fragments_and_retains_usage(vision):
    cache,item,ref=vision
    invoke=Mock(return_value=('Synthetic error visible',{'total_tokens':20}))
    assert cache.observe_once(PROFILE,item,ref,b'image','image/png',{},invoke)[2]
    assert not cache.observe_once(PROFILE,{**item,'id':'another-fragment'},ref,b'image','image/png',{},invoke)[2]
    assert invoke.call_count==1
    assert item['vision_usage']==[{'total_tokens':20}]


def test_unknown_vision_never_retries_or_becomes_success(vision):
    cache,item,ref=vision;invoke=Mock(side_effect=TimeoutError())
    with pytest.raises(TimeoutError):cache.observe_once(PROFILE,item,ref,b'image','image/png',{},invoke)
    with pytest.raises(TenantLinearError):cache.observe_once(PROFILE,item,ref,b'image','image/png',{},invoke)
    assert invoke.call_count==1


def test_vision_permission_and_tenant_rechecked(vision):
    cache,item,ref=vision;invoke=Mock(return_value=('Visible',{'total_tokens':2}))
    cache.observe_once(PROFILE,item,ref,b'image','image/png',{},invoke)
    with pytest.raises(TenantLinearError):cache.observe_once({'tenant_id':B,'telegram_id':22},item,ref,b'image','image/png',{},invoke)
    with pytest.raises(TenantLinearError):cache.observe_once(PROFILE,{**item,'route':{**item['route'],'allow_vision':False}},ref,b'image','image/png',{},invoke)
    assert invoke.call_count==1


def test_changed_vision_parameters_do_not_reuse(vision):
    cache,item,ref=vision;invoke=Mock(return_value=('Visible',{'total_tokens':2}))
    cache.observe_once(PROFILE,item,ref,b'image','image/png',{},invoke)
    cache.observe_once(PROFILE,{**item,'model':'other'},ref,b'image','image/png',{},invoke)
    assert invoke.call_count==2


def test_permission_change_not_silently_approved(campaign):
    d,provider,data,key=campaign
    approval={'expected_version':1,'channel_ids':[CA],'max_calls':data['max_calls'],
              'max_tokens':data['max_tokens'],'confirm_external_processing':True,
              'media_policy':{**row(d,key)['media_policy'],'allow_external_media':True}}
    with pytest.raises(TenantLinearError):campaigns.approve(PROFILE,key,approval)
    assert row(d,key)['status']=='awaiting_budget'
    provider.assert_not_called()


def test_main_budget_renewal_keeps_other_permissions(campaign):
    d,_,data,key=campaign
    d.execute("UPDATE saas_knowledge_campaigns SET status='budget_exhausted',reserved_kinds='{\"main\":2}'");d.commit()
    policy={**row(d,key)['media_policy'],'max_main_calls':4}
    result=campaigns.approve(PROFILE,key,{'expected_version':1,'channel_ids':[CA],
      'max_calls':4,'max_tokens':72000,'confirm_external_processing':True,'media_policy':policy})['campaign']
    assert result['media_policy']==policy
    assert result['reserved_kinds']['main']==2
    assert not result['media_policy']['allow_paid_asr']


def test_unknown_main_and_asr_reserves_are_added_after_known_vision(campaign):
    import tenant_knowledge_candidates_service as service
    d,provider,_,_=campaign
    item=service.prepare(PROFILE,{'request_id':str(uuid.uuid4()),'channel_id':CA,
        'conversation_id':'client','analysis_agent_id':AGENT})
    item.update(generation_attempted=True,generation_usage_received=False,
                asr_attempted=True,vision_attempts=1,vision_usage=[{'total_tokens':20}])
    service.finish(PROFILE,item,None,{'total_tokens':20},'unknown',True)
    assert d.execute('SELECT reserved_tokens FROM saas_ai_agent_token_usage WHERE run_id=%s::uuid',(item['id'],))[0]['reserved_tokens']==24000
    provider.assert_not_called()
