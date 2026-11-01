"""Known ASR output must not be discarded by a later publication check."""
import pytest
import re
import copy
import requests
from test_dialog_audio_transcription import ready,db,database,command,A,PROFILE
import tenant_dialog_audio_transcription as flow
from tenant_chat_ai_provider import ChatAIProviderError


@pytest.mark.parametrize('failure_kind',['runtime','sql'])
def test_known_success_survives_unexpected_publication_authority_failure(ready,monkeypatch,failure_kind):
    db,data,provider,download,sent=ready
    original=flow._authority;finished=False;failed=False
    def authority(*args,**kwargs):
        nonlocal failed
        if finished and kwargs.get('require_ai') and not failed:
            failed=True
            args[0].execute('SELECT state,transcript FROM saas_dialog_audio_attempts WHERE tenant_id=%s::uuid',(A,))
            assert args[0].fetchone()=={'state':'completed','transcript':'known validated ASR text'}
            if failure_kind=='sql':args[0].execute('SELECT 1/0')
            raise RuntimeError('synthetic post-provider publication failure')
        return original(*args,**kwargs)
    def generate(*args,**kwargs):
        nonlocal finished
        kwargs['before_dispatch']();sent.append(args[1]);finished=True
        return 'known validated ASR text',{'seconds':.1}
    monkeypatch.setattr(flow,'_authority',authority);provider.side_effect=generate
    result=flow.execute(PROFILE,command(data,flow.preview(PROFILE,data)))
    assert result['requested_attempt']['state']=='completed'
    assert result['requested_attempt']['text']=='known validated ASR text'
    assert not result['requested_attempt']['published']
    assert not result['latest_success'] and not result['message'].get('audio_transcript')
    assert result['history'][0]['text']=='known validated ASR text'
    assert result['requested_attempt']['code']=='publication_check_unavailable'
    assert db.execute('SELECT state,actual_microusd FROM saas_ai_spend_reservations')==[{'state':'settled','actual_microusd':30000}]
    # A fresh authorized transcribe reuses this exact known result, not paid ASR.
    reused=flow.execute(PROFILE,command(data,flow.preview(PROFILE,data),action='transcribe'))
    assert reused['requested_attempt']['origin']=='reuse' and len(sent)==1
    assert reused['message']['audio_transcript']=='known validated ASR text'


@pytest.mark.parametrize('failure,code',[
    (requests.exceptions.Timeout('never expose provider details'),'provider_timeout'),
    (requests.exceptions.ConnectionError('never expose connection details'),'provider_connection_error'),
    (ChatAIProviderError(401,'invalid_credentials'),'provider_http_401_invalid_credentials'),
    (ChatAIProviderError(429,'rate_limit'),'provider_http_429_rate_limit'),
    (ValueError('never expose response payload'),'provider_response_invalid'),
])
def test_new_unknown_keeps_safe_failure_reason_and_hold_without_replay(ready,failure,code):
    db,data,provider,download,sent=ready
    def fail(*args,**kwargs):
        kwargs['before_dispatch']();sent.append(args[1]);raise failure
    provider.side_effect=fail
    request=command(data,flow.preview(PROFILE,data));result=flow.execute(PROFILE,request)
    assert result['requested_attempt']['state']=='unknown' and result['requested_attempt']['code']==code
    assert 'never expose' not in str(result)
    assert db.execute('SELECT state,actual_microusd,reserved_microusd FROM saas_ai_spend_reservations')==[{'state':'unknown','actual_microusd':None,'reserved_microusd':30000}]
    duplicate=flow.execute(PROFILE,request)
    assert duplicate['requested_attempt']['id']==result['requested_attempt']['id'] and len(sent)==1


def test_settlement_failure_retains_known_text_and_conservative_reserve(ready,monkeypatch):
    import tenant_ai_spend_budget as budget
    db,data,provider,download,sent=ready
    def unavailable(*args,**kwargs):raise RuntimeError('synthetic financial write failure')
    monkeypatch.setattr(budget,'settle_media',unavailable)
    request=command(data,flow.preview(PROFILE,data));result=flow.execute(PROFILE,request)
    assert result['requested_attempt']['state']=='completed'
    assert result['requested_attempt']['code']=='accounting_settlement_pending'
    assert result['message']['audio_transcript']=='synthetic speech'
    assert db.execute('SELECT state,actual_microusd,reserved_microusd FROM saas_ai_spend_reservations')==[{'state':'reserved','actual_microusd':None,'reserved_microusd':30000}]
    duplicate=flow.execute(PROFILE,request)
    assert duplicate['requested_attempt']['id']==result['requested_attempt']['id'] and len(sent)==1


def test_valid_result_persistence_error_is_not_reclassified_as_provider_failure(ready,monkeypatch):
    db,data,provider,download,sent=ready
    original=flow._finish;calls=[]
    def unavailable(*args,**kwargs):
        calls.append(kwargs)
        raise RuntimeError('synthetic durable result write failure')
    monkeypatch.setattr(flow,'_finish',unavailable)
    request=command(data,flow.preview(PROFILE,data))
    with pytest.raises(RuntimeError):flow.execute(PROFILE,request)
    assert len(calls)==1 and calls[0]['text']=='synthetic speech' and 'error' not in calls[0]
    assert db.execute('SELECT state,code,transcript FROM saas_dialog_audio_attempts')==[{'state':'sending','code':'','transcript':''}]
    assert db.execute('SELECT state,actual_microusd FROM saas_ai_spend_reservations')==[{'state':'reserved','actual_microusd':None}]
    monkeypatch.setattr(flow,'_finish',original)
    duplicate=flow.execute(PROFILE,request)
    assert duplicate['requested_attempt']['state']=='sending' and len(sent)==1


@pytest.mark.parametrize('change',[None,'agent','source','unknown'])
def test_get_keeps_unpublished_history_without_writes_or_false_current_projection(ready,monkeypatch,change):
    db,data,provider,download,sent=ready
    monkeypatch.setattr(flow,'_publish_known',lambda *args,**kw:(_ for _ in ()).throw(RuntimeError('synthetic publication failure')))
    result=flow.execute(PROFILE,command(data,flow.preview(PROFILE,data)))
    assert result['requested_attempt']['state']=='completed' and not result['requested_attempt']['published']
    if change=='agent':db.execute('UPDATE saas_ai_agents SET version=version+1 WHERE tenant_id=%s::uuid',(A,))
    if change=='source':db.execute("UPDATE saas_crm_messages SET raw=raw||'{\"mediaId\":\"changed-source\"}'::jsonb WHERE id=%s::uuid",(data['message_row_id'],))
    if change=='unknown':
        db.execute("UPDATE saas_dialog_audio_attempts SET state='unknown',code='provider_outcome_unknown',transcript=''")
        db.execute("UPDATE saas_dialog_audio_sources SET active_attempt_id=%s::uuid",(result['requested_attempt']['id'],))
        db.execute("UPDATE saas_ai_spend_reservations SET state='unknown',actual_microusd=NULL")
    db.commit()
    before=copy.deepcopy(db.execute('SELECT * FROM saas_dialog_audio_attempts'))
    ledger=copy.deepcopy(db.execute('SELECT state,actual_microusd,reserved_microusd FROM saas_ai_spend_reservations'))
    execute=db.execute;queries=[]
    def read_only(sql,*params):
        queries.append(sql)
        assert not re.search(r'\b(INSERT|UPDATE|DELETE|CREATE|ALTER|TRUNCATE|DROP)\b',sql,re.I),sql
        return execute(sql,*params)
    monkeypatch.setattr(db,'execute',read_only)
    preview=flow.preview(PROFILE,data)
    monkeypatch.setattr(db,'execute',execute)
    assert queries and db.execute('SELECT * FROM saas_dialog_audio_attempts')==before
    assert db.execute('SELECT state,actual_microusd,reserved_microusd FROM saas_ai_spend_reservations')==ledger
    assert len(sent)==1
    assert not preview['latest_success'] and not preview['message'].get('audio_transcript')
    if change!='unknown':
        assert preview['history'][0]['text']=='synthetic speech'
        assert preview['history'][0]['state']=='completed' and not preview['history'][0]['published']
    if change=='unknown':assert preview['state']=='unknown' and preview['current_attempt']['code']=='provider_outcome_unknown'


def test_dispatch_fence_failure_is_not_a_provider_failure_or_refund(ready,monkeypatch):
    db,data,provider,download,sent=ready
    before=flow._before_dispatch
    def interrupted(*args):
        before(*args)
        raise RuntimeError('synthetic dispatch commit/readback interruption')
    monkeypatch.setattr(flow,'_before_dispatch',interrupted)
    result=flow.execute(PROFILE,command(data,flow.preview(PROFILE,data)))
    assert result['requested_attempt']['state']=='unknown'
    assert result['requested_attempt']['code']=='dispatch_blocked' and not sent
    assert db.execute('SELECT state,actual_microusd,reserved_microusd FROM saas_ai_spend_reservations')==[{'state':'unknown','actual_microusd':None,'reserved_microusd':30000}]


def test_late_publication_cannot_overwrite_a_newer_zero_dispatch_repair(ready,monkeypatch):
    import tenant_dialog_audio_store as store
    db,data,provider,download,sent=ready
    publish=flow._publish_known;repaired=[]
    def interleaved(profile,auth,attempt,**kwargs):
        preview=flow.preview(PROFILE,data)
        assert not preview['latest_success'] and preview['history'][0]['text']=='synthetic speech'
        repaired.append(flow.execute(PROFILE,command(data,preview,action='transcribe')))
        publish(profile,auth,attempt,**kwargs)
    monkeypatch.setattr(flow,'_publish_known',interleaved)
    result=flow.execute(PROFILE,command(data,flow.preview(PROFILE,data)))
    assert len(sent)==1 and repaired[0]['requested_attempt']['origin']=='reuse'
    assert result['current_attempt']['id']==repaired[0]['requested_attempt']['id']
    assert result['latest_success']['attempt_id']==repaired[0]['requested_attempt']['id']
    assert result['requested_attempt']['published'] is False
    with db.cursor() as cur:
        auth=flow._authority(cur,PROFILE,data)
        shared=store.current_ref(cur,A,auth['ref'])
    assert str(shared['id'])==repaired[0]['requested_attempt']['id']
    assert shared['transcript']=='synthetic speech'
