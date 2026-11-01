"""Forward-only approved scoped adapter/cost replacement; offline synthetic SQL."""
import uuid
from contextlib import contextmanager
import pytest
from test_dialog_audio_transcription import ready,db,database,command,A,PROFILE
import tenant_ai_asr_model as asr
import tenant_dialog_audio_transcription as flow


@contextmanager
def old_adapter(monkeypatch):
    with monkeypatch.context() as old:
        old.setattr(asr,'STRONG_MODEL','gpt-transcribe')
        old.setattr(asr,'scoped_strong_quote',lambda tenant:{'model':'gpt-transcribe','operation':'asr','reserve_microusd':30000,'minimum_microusd':22500,'max_duration_seconds':300})
        yield


def test_old_three_cent_confirmation_rejected_before_download_or_provider(ready,monkeypatch):
    db,data,provider,download,sent=ready
    with old_adapter(monkeypatch):
        request=command(data,flow.preview(PROFILE,data))
    fresh=flow.preview(PROFILE,data)
    assert fresh['effective_model']=='gpt-4o-transcribe' and fresh['price']['reserve_microusd']==60000
    with pytest.raises(flow.AudioError) as error:flow.execute(PROFILE,request)
    assert error.value.code=='preview_changed'
    download.assert_not_called();provider.assert_not_called()
    assert db.execute('SELECT count(*) AS n FROM saas_chat_ai_runs')[0]['n']==0
    assert db.execute('SELECT count(*) AS n FROM saas_dialog_audio_attempts')[0]['n']==0


def test_old_unknown_hold_and_idempotent_operation_never_repriced_or_replayed(ready,monkeypatch):
    db,data,provider,download,sent=ready
    def fail(*args,**kwargs):
        kwargs['before_dispatch']();sent.append(args[1]);raise TimeoutError('synthetic uncertainty')
    provider.side_effect=fail
    with old_adapter(monkeypatch):
        request=command(data,flow.preview(PROFILE,data));previous=flow.execute(PROFILE,request)
    assert previous['requested_attempt']['model']=='gpt-transcribe' and previous['requested_attempt']['state']=='unknown'
    current=flow.execute(PROFILE,request)
    assert current['requested_attempt']['id']==previous['requested_attempt']['id']
    assert current['effective_model']=='gpt-4o-transcribe' and current['allowed_actions']==[]
    assert len(sent)==1
    assert db.execute('SELECT model,state,reserved_microusd,actual_microusd FROM saas_ai_spend_reservations')==[{'model':'gpt-transcribe','state':'unknown','reserved_microusd':30000,'actual_microusd':None}]
    with pytest.raises(flow.AudioError):flow.execute(PROFILE,{**request,'idempotency_key':str(uuid.uuid4())})
    assert len(sent)==1


def test_old_success_is_reused_without_new_charge_and_keeps_actual_model(ready,monkeypatch):
    db,data,provider,download,sent=ready
    with old_adapter(monkeypatch):
        previous=flow.execute(PROFILE,command(data,flow.preview(PROFILE,data)))
    current=flow.execute(PROFILE,command(data,flow.preview(PROFILE,data),action='transcribe'))
    assert current['effective_model']=='gpt-4o-transcribe'
    assert current['requested_attempt']['origin']=='reuse' and current['requested_attempt']['reserve_microusd']==0
    assert current['latest_success']['model']=='gpt-transcribe'
    assert current['latest_success']['text']==previous['latest_success']['text']
    assert len(sent)==1 and db.execute('SELECT count(*) AS n FROM saas_chat_ai_runs')[0]['n']==1
    assert db.execute('SELECT model,state,reserved_microusd,actual_microusd FROM saas_ai_spend_reservations')==[{'model':'gpt-transcribe','state':'settled','reserved_microusd':30000,'actual_microusd':30000}]
