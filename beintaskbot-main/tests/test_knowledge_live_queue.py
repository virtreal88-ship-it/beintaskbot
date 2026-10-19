import uuid
from unittest.mock import Mock
import pytest

import tenant_knowledge_live_queue as live


TENANT='10000000-0000-0000-0000-000000000001'
CHANNEL='10000000-0000-0000-0000-000000000002'
MESSAGE='10000000-0000-0000-0000-000000000003'


class Cursor:
    def __init__(self):self.calls=[]
    def __enter__(self):return self
    def __exit__(self,*_):return False
    def execute(self,sql,params=None):self.calls.append((sql,params))


class Connection:
    def __init__(self):self.value=Cursor();self.commits=0
    def __enter__(self):return self
    def __exit__(self,*_):return False
    def cursor(self):return self.value
    def commit(self):self.commits+=1


def test_webhook_enqueue_is_scoped_and_debounced(monkeypatch):
    monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_ENABLED','true')
    connection=Connection()
    live.enqueue(connection.cursor(),TENANT,CHANNEL,'120363@g.us',42,MESSAGE,actor_id=77)
    sql,params=connection.value.calls[0]
    assert 'PRIMARY KEY' not in sql
    assert 'generation=saas_knowledge_live_queue.generation+1' in sql
    assert 'WHERE saas_knowledge_live_queue.last_message_id IS DISTINCT FROM EXCLUDED.last_message_id' in sql
    assert params[:6]==(TENANT,CHANNEL,'120363@g.us',42,77,MESSAGE)
    assert params[6]==live.DEBOUNCE_SECONDS
    assert 'text' not in sql.lower() and 'body' not in sql.lower()


def test_worker_reuses_stable_receipt_and_only_requests_az(monkeypatch):
    monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_ENABLED','true')
    item={'tenant_id':TENANT,'channel_id':CHANNEL,'conversation_id':'client@s.whatsapp.net',
          'lead_id':42,'actor_id':None,'reviewer_id':9,'last_message_id':MESSAGE,'generation':2}
    monkeypatch.setattr(live,'claim',lambda:item)
    finish=Mock();monkeypatch.setattr(live,'finish',finish)
    analyze=Mock(return_value={'request':{'status':'completed'},'candidates':[{'status':'pending_review'}]})
    monkeypatch.setattr(live,'analyze',analyze)
    assert live.tick()
    args=analyze.call_args.args
    assert args[0]=={'tenant_id':TENANT,'telegram_id':9}
    assert args[1]['language']=='az'
    assert args[1]['channel_id']==CHANNEL and args[1]['conversation_id']==item['conversation_id']
    assert args[1]['request_id']==live._request_id(item)
    assert live._request_id(item)==live._request_id(dict(item))
    assert analyze.call_args.kwargs['before_provider'] is live.require_extraction_enabled
    finish.assert_called_once_with(item,args[1]['request_id'],'')


def test_uncertain_ai_result_is_terminal_until_a_new_message(monkeypatch):
    monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_ENABLED','true')
    item={'tenant_id':TENANT,'channel_id':CHANNEL,'conversation_id':'client',
          'lead_id':42,'reviewer_id':9,'last_message_id':MESSAGE,'generation':1}
    monkeypatch.setattr(live,'claim',lambda:item)
    finish=Mock();monkeypatch.setattr(live,'finish',finish)
    monkeypatch.setattr(live,'analyze',Mock(side_effect=RuntimeError('provider outcome unknown')))
    assert live.tick()
    finish.assert_called_once_with(item,error_code='worker_result_unknown')


def test_default_off_false_and_backlog_are_inert(monkeypatch):
    for value in (None, 'false', 'FALSE', '0', ''):
        if value is None:
            monkeypatch.delenv('SUPPORT_CASES_LIVE_EXTRACTION_ENABLED', raising=False)
        else:
            monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_ENABLED', value)
        db=Connection()
        connect=Mock(side_effect=AssertionError('DB accessed with extraction disabled'))
        analyze=Mock(side_effect=AssertionError('AI called with extraction disabled'))
        monkeypatch.setattr(live, '_connect', connect)
        monkeypatch.setattr(live, 'analyze', analyze)
        live.enqueue(db.cursor(), TENANT, CHANNEL, 'client', 42, MESSAGE)
        assert db.value.calls == []
        assert live.claim() is None
        assert live.tick() is False
        connect.assert_not_called()
        analyze.assert_not_called()


def test_flag_is_independent_of_ai_agent_settings(monkeypatch):
    db=Connection()
    monkeypatch.setenv('AI_AGENTS_ENABLED','true')
    monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_ENABLED','false')
    live.enqueue(db.cursor(), TENANT, CHANNEL, 'client', 42, MESSAGE)
    assert not db.value.calls
    monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_ENABLED','true')
    live.enqueue(db.cursor(), TENANT, CHANNEL, 'client', 42, MESSAGE)
    assert len(db.value.calls)==1
    monkeypatch.setenv('AI_AGENTS_ENABLED','false')
    assert live.extraction_enabled() is True


def test_restart_with_disabled_flag_never_claims_backlog(monkeypatch):
    monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_ENABLED','false')
    monkeypatch.setattr(live,'_connect',Mock(side_effect=AssertionError('backlog touched')))
    assert live.tick() is False
    assert live.claim() is None


def test_switch_off_between_claim_and_analyze(monkeypatch):
    monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_ENABLED', 'true')
    item={'tenant_id':TENANT,'channel_id':CHANNEL,'conversation_id':'client',
          'lead_id':42,'reviewer_id':9,'last_message_id':MESSAGE,'generation':1}
    def claim_then_disable():
        monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_ENABLED', 'false')
        return item
    monkeypatch.setattr(live,'claim',claim_then_disable)
    ai=Mock(side_effect=AssertionError('AI request must not run'))
    finish=Mock()
    monkeypatch.setattr(live,'analyze',ai)
    monkeypatch.setattr(live,'finish',finish)
    assert live.tick() is False
    ai.assert_not_called()
    finish.assert_called_once_with(item,error_code='live_extraction_disabled_after_claim')


def test_disabled_queue_receipt_is_terminally_blocked(monkeypatch):
    item={'tenant_id':TENANT,'channel_id':CHANNEL,'conversation_id':'client','generation':3}
    connection=Connection()
    monkeypatch.setattr(live,'_connect',lambda:connection)
    live.finish(item,error_code='live_extraction_disabled_after_claim')
    sql,params=connection.value.calls[0]
    assert "WHEN %s<>'' THEN 'blocked'" in sql
    assert params[1]=='live_extraction_disabled_after_claim'
    assert connection.commits==1


def test_switch_off_during_analyze_stops_before_next_provider_call(monkeypatch):
    from tenant_knowledge_candidates_service import LiveExtractionDisabled
    monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_ENABLED', 'true')
    item={'tenant_id':TENANT,'channel_id':CHANNEL,'conversation_id':'client',
          'lead_id':42,'reviewer_id':9,'last_message_id':MESSAGE,'generation':1}
    monkeypatch.setattr(live,'claim',lambda:item)
    enabled={'value':True}
    provider=Mock(side_effect=AssertionError('next AI call must not start'))
    def analyze(_profile,_data,*,before_provider):
        enabled['value']=False
        before_provider()
        provider()
    monkeypatch.setattr(live,'analyze',analyze)
    finish=Mock();monkeypatch.setattr(live,'finish',finish)
    monkeypatch.setattr(live,'extraction_enabled',lambda:enabled['value'])
    assert live.tick() is False
    provider.assert_not_called()
    finish.assert_called_once_with(item,error_code='live_extraction_disabled_before_provider')
