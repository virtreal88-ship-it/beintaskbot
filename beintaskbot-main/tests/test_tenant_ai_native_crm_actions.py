"""Native AI CRM actions are allowlisted, scoped, idempotent, and default-off."""
import asyncio
from unittest import mock
import pytest
from tenant_linear_policy import TenantLinearError
import tenant_ai_agents_policy as policy
import tenant_ai_agent_native_crm_actions as native


def test_native_actions_validate_static_confirmed_inputs():
    row=policy.trigger({'name':'create deal','trigger_type':'phrase','condition':{'phrase':'buy','native_deal_title':'Deal','native_pipeline_id':'p','native_stage_id':'s'},'actions':['native_find_create_deal'],'enabled':False},creating=True)
    assert row['enabled'] is False
    with pytest.raises(TenantLinearError):
        policy.trigger({'name':'bad','trigger_type':'phrase','condition':{'phrase':'x'},'actions':['native_find_create_company'],'enabled':False},creating=True)


def test_native_action_engine_proposes_only_while_global_or_tenant_gate_is_off(monkeypatch):
    job={'tenant_id':'tenant','provider':'connect','channel_id':'channel','conversation_id':'c@s.whatsapp.net'}
    trigger={'id':'t','actions':['native_find_create_contact','native_create_task'],'condition':{}}
    monkeypatch.delenv('AI_NATIVE_CRM_ACTIONS_ENABLED',raising=False)
    result=asyncio.run(native.execute(job,{'body':'hello'},[trigger],tenant_enabled=True,automatic=True))
    assert [x['state'] for x in result['actions']]==['proposed','proposed']
    monkeypatch.setenv('AI_NATIVE_CRM_ACTIONS_ENABLED','true')
    result=asyncio.run(native.execute(job,{'body':'hello'},[trigger],tenant_enabled=False,automatic=True))
    assert all(x['state']=='proposed' for x in result['actions'])


def test_native_action_server_gate_is_forced_off_in_staging(monkeypatch):
    monkeypatch.setenv('AI_NATIVE_CRM_ACTIONS_ENABLED','true')
    monkeypatch.setenv('STAGING_MODE','true')
    assert native.runtime_flag('AI_NATIVE_CRM_ACTIONS_ENABLED') is False


def test_native_stage_action_uses_tenant_service_and_is_idempotent(monkeypatch):
    job={'tenant_id':'t1','provider':'connect','channel_id':'11111111-1111-4111-8111-111111111111',
         'conversation_id':'994501234567@s.whatsapp.net','lead_id':7,'revision':1,'lease_token':'l',
         'event_fingerprint':'evt','payload':{}}
    trigger={'id':'tr1','actions':['native_move_stage'],'condition':{'native_pipeline_id':'p1','native_stage_id':'s2'}}
    deal={'id':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','pipeline_id':'p1'}
    monkeypatch.setenv('AI_NATIVE_CRM_ACTIONS_ENABLED','true')
    monkeypatch.setattr(native,'_still_allowed',lambda *_:True)
    monkeypatch.setattr(native,'_contact',lambda *_:'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')
    monkeypatch.setattr('tenant_crm_native_deals.find_chat_deal',lambda *_:deal)
    monkeypatch.setattr('tenant_crm_native_deals.get_deal',lambda *_:deal)
    monkeypatch.setattr('tenant_crm_native_deals.set_deal_stage',lambda *a:{'id':deal['id'],'stage_id':'s2'})
    monkeypatch.setattr(native,'_finish',lambda *a:None)
    with mock.patch.object(native,'_reserve',return_value=('run1','running',None)) as reserve, \
         mock.patch('tenant_crm_native_deals.set_deal_stage') as change:
        change.return_value={'id':deal['id'],'stage_id':'s2'}
        result=asyncio.run(native.execute(job,{'body':'go'},[trigger],tenant_enabled=True,automatic=True))
        assert result['actions'][0]['state']=='accepted'
        change.assert_called_once_with('t1',deal['id'],'s2')
        reserve.assert_called_once()
    with mock.patch.object(native,'_reserve',return_value=(None,'accepted',{'id':deal['id'],'state':'s2'})), \
         mock.patch('tenant_crm_native_deals.set_deal_stage') as change:
        result=asyncio.run(native.execute(job,{'body':'go'},[trigger],tenant_enabled=True,automatic=True))
        assert result['actions'][0]['state']=='accepted'
        change.assert_not_called()


def test_pipeline_mismatch_and_group_routes_fail_closed(monkeypatch):
    job={'tenant_id':'t1','provider':'connect','channel_id':'c','conversation_id':'994501234567@s.whatsapp.net',
         'lead_id':7,'revision':1,'lease_token':'l','event_fingerprint':'evt','payload':{}}
    trigger={'id':'tr','actions':['native_move_stage'],'condition':{'native_pipeline_id':'p2','native_stage_id':'s2'}}
    monkeypatch.setenv('AI_NATIVE_CRM_ACTIONS_ENABLED','true')
    monkeypatch.setattr(native,'_still_allowed',lambda *_:True)
    monkeypatch.setattr(native,'_contact',lambda *_:'contact')
    monkeypatch.setattr('tenant_crm_native_deals.find_chat_deal',lambda *_:{'id':'deal','pipeline_id':'p1'})
    monkeypatch.setattr('tenant_crm_native_deals.get_deal',lambda *_:{'id':'deal','pipeline_id':'p1'})
    with mock.patch('tenant_crm_native_deals.set_deal_stage') as change:
        result=asyncio.run(native.execute(job,{'body':'go'},[trigger],tenant_enabled=True,automatic=True))
        assert result['blocked'] is True
        change.assert_not_called()
    job['conversation_id']='12345-678@g.us'
    result=asyncio.run(native.execute(job,{'body':'go'},[trigger],tenant_enabled=True,automatic=True))
    assert result['actions']==[]


def test_handoff_after_reservation_blocks_write(monkeypatch):
    job={'tenant_id':'t1','provider':'connect','channel_id':'11111111-1111-4111-8111-111111111111',
         'conversation_id':'994501234567@s.whatsapp.net','lead_id':7,'revision':1,'lease_token':'l',
         'event_fingerprint':'evt','payload':{}}
    trigger={'id':'tr1','actions':['native_move_stage'],'condition':{'native_pipeline_id':'p1','native_stage_id':'s2'}}
    deal={'id':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','pipeline_id':'p1'}
    monkeypatch.setenv('AI_NATIVE_CRM_ACTIONS_ENABLED','true')
    monkeypatch.setattr(native,'_still_allowed',mock.Mock(side_effect=[True,False]))
    monkeypatch.setattr(native,'_contact',lambda *_:'contact')
    monkeypatch.setattr('tenant_crm_native_deals.find_chat_deal',lambda *_:deal)
    monkeypatch.setattr('tenant_crm_native_deals.get_deal',lambda *_:deal)
    with mock.patch.object(native,'_reserve',return_value=('run1','running',None)), \
         mock.patch.object(native,'_finish') as finish, \
         mock.patch('tenant_crm_native_deals.set_deal_stage') as change:
        result=asyncio.run(native.execute(job,{'body':'go'},[trigger],tenant_enabled=True,automatic=True))
    assert result['blocked'] is True
    change.assert_not_called()
    finish.assert_called_once()
    assert finish.call_args.args[2]=='blocked'


def test_contact_creation_requires_inbound_personal_jid(monkeypatch):
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*_):pass
    class Conn:
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def cursor(self):return Cursor()
        def commit(self):pass
    monkeypatch.setattr(native,'_connect',lambda:Conn())
    monkeypatch.setattr(native,'ensure_schema',lambda *_:None)
    with mock.patch('tenant_crm_native_contacts.upsert_whatsapp_contact',return_value='contact-uuid') as upsert:
        job={'tenant_id':'tenant','channel_id':'11111111-1111-4111-8111-111111111111','conversation_id':'994501234567@s.whatsapp.net'}
        assert native._create_verified_contact(job,{'direction':'incoming','external_id':'message-1'})=='contact-uuid'
        upsert.assert_called_once()
        assert native._create_verified_contact({**job,'conversation_id':'12345-678@g.us'}, {'direction':'incoming'})==''
        assert native._create_verified_contact(job,{'direction':'outgoing'})==''
