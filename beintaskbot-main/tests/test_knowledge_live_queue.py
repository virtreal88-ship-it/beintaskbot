import uuid
from unittest.mock import Mock

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


def test_webhook_enqueue_is_scoped_and_debounced():
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
    finish.assert_called_once_with(item,args[1]['request_id'],'')


def test_uncertain_ai_result_is_terminal_until_a_new_message(monkeypatch):
    item={'tenant_id':TENANT,'channel_id':CHANNEL,'conversation_id':'client',
          'lead_id':42,'reviewer_id':9,'last_message_id':MESSAGE,'generation':1}
    monkeypatch.setattr(live,'claim',lambda:item)
    finish=Mock();monkeypatch.setattr(live,'finish',finish)
    monkeypatch.setattr(live,'analyze',Mock(side_effect=RuntimeError('provider outcome unknown')))
    assert live.tick()
    finish.assert_called_once_with(item,error_code='worker_result_unknown')
