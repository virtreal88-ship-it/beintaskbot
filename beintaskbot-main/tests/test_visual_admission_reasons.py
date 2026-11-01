"""Vision failures use synthetic bytes and intercept every provider call."""
from unittest.mock import Mock,MagicMock
import pytest
import tenant_episode_media as runtime
import tenant_knowledge_media as core
from tenant_linear_policy import TenantLinearError
from test_episode_media import sandbox,actual_authority,row

@pytest.mark.parametrize('reason,status,step',[
 ('ai_spend_media_price_unknown',409,'budget'),
 ('ai_spend_budget_exhausted',429,'budget'),
 ('Company daily limit exhausted.',429,'calls'),
 ('Private assignment detail',409,'authority'),
])
def test_visual_pre_dispatch_reason_is_preserved(sandbox,monkeypatch,reason,status,step):
    rows=[row('image')];auth=actual_authority('manual',rows)
    auth.dispatch=runtime.Execution.dispatch.__get__(auth);auth.admit_call=Mock()
    conn=MagicMock();conn.__enter__.return_value=conn
    monkeypatch.setattr(runtime,'_connect',lambda:conn)
    for name in ('ensure_schema','reserve_media','settle_media'):monkeypatch.setattr('tenant_ai_spend_budget.'+name,Mock())
    if step=='calls':auth.admit_call.side_effect=TenantLinearError(reason,status)
    elif step=='budget':
        import tenant_ai_spend_budget as budget
        budget.reserve_media.side_effect=TenantLinearError(reason,status)
    else:
        original=auth.validate;count=[]
        def validate(ref):
            count.append(ref)
            if len(count)>1:raise TenantLinearError(reason,status)
            return original(ref)
        auth.validate=validate
    runtime.prepare(auth,rows)
    receipt=rows[0]['_episode_receipt']
    assert receipt['failure_stage']=='visual_analysis'
    expected={'ai_spend_media_price_unknown':'ai_spend_vision_price_unknown','ai_spend_budget_exhausted':'ai_spend_budget_exhausted','calls':'ai_call_limit_exhausted','authority':'media_authority_changed'}
    assert receipt['code']==expected.get(reason,expected.get(step))
    assert receipt['admission_status']==status
    assert not getattr(auth,'media_attempts',0)
    assert not sandbox[0] and not sandbox[1]
    from tenant_chat_ai_provider import generate
    generate.assert_not_called()

@pytest.mark.parametrize('failure', ['model_access','timeout'])
def test_visual_provider_safe_category_is_preserved(sandbox,monkeypatch,failure):
    from tenant_chat_ai_provider import generate,ChatAIProviderError
    rows=[row('image')];auth=actual_authority('manual',rows)
    auth.dispatch=runtime.Execution.dispatch.__get__(auth);auth.admit_call=Mock()
    conn=MagicMock();conn.__enter__.return_value=conn
    monkeypatch.setattr(runtime,'_connect',lambda:conn)
    for name in ('ensure_schema','reserve_media','settle_media'):monkeypatch.setattr('tenant_ai_spend_budget.'+name,Mock())
    generate.side_effect=ChatAIProviderError(403,'model_access') if failure=='model_access' else TimeoutError('Private endpoint detail')
    runtime.prepare(auth,rows)
    receipt=rows[0]['_episode_receipt']
    assert (receipt['status'],receipt['code'],receipt['failure_stage'])==('unknown','media_result_unknown','visual_analysis')
    assert receipt['failure_code']==('provider_http_403_model_access' if failure=='model_access' else 'provider_timeout')
    assert receipt.get('admission_status') is None and auth.media_attempts==1
    assert 'Private' not in str(receipt)
    from tenant_ai_spend_budget import settle_media
    assert settle_media.call_args.kwargs['unknown'] is True
    assert settle_media.call_args.kwargs['attempted_units']==1
    again=[row('image')]
    runtime.prepare(actual_authority('manual',again),again)
    generate.assert_called_once()
    assert again[0]['_episode_receipt']['status']=='unknown'
