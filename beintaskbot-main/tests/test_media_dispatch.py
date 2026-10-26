"""Dispatch fences and diagnostics with synthetic bytes and mocked providers."""
from unittest.mock import Mock
import pytest
from test_tenant_support_inbox import database,db,A,CA,PROFILE
from test_knowledge_candidates import ready
from test_knowledge_media import context
import tenant_knowledge_media_cache as cache
import tenant_knowledge_candidates_service as service
from tenant_linear_policy import TenantLinearError
from tenant_chat_ai_provider import ChatAIProviderError


@pytest.fixture
def cached(ready,monkeypatch):
    conn,_,_=ready
    monkeypatch.setattr(cache,'_connect',lambda:conn)
    monkeypatch.setattr(service,'check_active',lambda *a:None)
    ref={'id':'synthetic','channel_id':CA}
    item={'tenant':A,'key':'synthetic','model':'fixture','refs':[ref],
          'route':{'allow_external_media':True,'allow_vision':True}}
    return conn,item,ref


@pytest.mark.parametrize('gate',['active','callback','asr_callback'])
def test_gate_failure_before_dispatch_is_not_unknown(cached,monkeypatch,gate):
    conn,item,ref=cached
    cache.schema(conn.cursor())
    conn.execute("INSERT INTO saas_knowledge_media_cache(tenant_id,fingerprint,state,token) VALUES(%s::uuid,'old-held','unknown','00000000-0000-0000-0000-000000000001')",(A,));conn.commit()
    invoke=Mock(return_value=('Synthetic observation',{'total_tokens':2}))
    error=TenantLinearError('private URL and credentials must not escape',409)
    if gate=='active':
        monkeypatch.setattr(service,'check_active',Mock(side_effect=[None,error]))
    callback=Mock(side_effect=error) if gate!='active' else None
    with pytest.raises(TenantLinearError) as caught:
        if gate=='asr_callback':
            cache.transcribe_once(PROFILE,item,'fixture',(b'synthetic','wav','audio/wav'),invoke,check_before_ai=callback)
        else:
            cache.observe_once(PROFILE,item,ref,b'synthetic','image/png',{},invoke,check_before_ai=callback)
    invoke.assert_not_called()
    assert not item.get('vision_attempts') and not item.get('asr_attempted')
    assert not getattr(caught.value,'media_outcome_unknown',False)
    assert conn.execute('SELECT fingerprint,state FROM saas_knowledge_media_cache')==[{'fingerprint':'old-held','state':'unknown'}]


@pytest.mark.parametrize('kind',['vision','asr'])
@pytest.mark.parametrize('error,code',[
    (ChatAIProviderError(403,'project_access'),'provider_http_403_project_access'),
    (TimeoutError('secret endpoint and payload'),'provider_timeout'),
    (ValueError('private response'),'provider_response_invalid')])
def test_dispatched_failure_keeps_unknown_and_safe_cause(cached,kind,error,code):
    conn,item,ref=cached;invoke=Mock(side_effect=error)
    def run():
        if kind=='asr':return cache.transcribe_once(PROFILE,item,'fixture',(b'image','wav','audio/wav'),invoke)
        return cache.observe_once(PROFILE,item,ref,b'image','image/png',{},invoke)
    with pytest.raises(type(error)) as first:run()
    assert first.value.media_outcome_unknown and first.value.media_failure_code==code
    assert item.get('asr_attempted') if kind=='asr' else item['vision_attempts']==1
    with pytest.raises(TenantLinearError) as second:run()
    assert second.value.media_outcome_unknown and second.value.media_failure_code==code
    invoke.assert_called_once()
    row=conn.execute('SELECT state,metadata FROM saas_knowledge_media_cache')[0]
    assert row['state']=='unknown' and row['metadata']['failure_code']==code
    assert 'secret' not in str(row) and 'private' not in str(row)


@pytest.mark.parametrize('failure,expected',[
    (ChatAIProviderError(401,'invalid_credentials'),'provider_http_401_invalid_credentials'),
    (TimeoutError('SECRET'),'provider_timeout'),
    (RuntimeError('SECRET'),'provider_call_failed')])
def test_extraction_persists_safe_provider_cause(ready,failure,expected):
    conn,provider,data=ready;provider.side_effect=failure
    result=service.analyze(PROFILE,data)
    provider.assert_called_once()
    assert result['request']['status']=='unknown'
    assert result['request']['error_code']==expected
    assert 'SECRET' not in str(result)


def test_collector_cause_reaches_read_projection_without_payload(context,monkeypatch):
    from test_knowledge_media import media
    from tenant_knowledge_media_coverage import project
    item,add,_=context;add('image');item['route'].update(allow_vision=True,allow_external_media=True)
    error=ChatAIProviderError(429,'billing_or_quota')
    error.media_outcome_unknown=True
    error.media_failure_code='provider_http_429_billing_or_quota'
    monkeypatch.setattr(cache,'observe_once',Mock(side_effect=error))
    _,receipts=media.collect(PROFILE,item)
    value=project(item['refs'],receipts,'unknown',include_content=True)
    assert value['media_results'][0]['status']=='unknown'
    assert value['media_results'][0]['failure_code']==error.media_failure_code
    assert 'result' not in value['media_results'][0]
    receipts[0]['failure_code']='SECRET https://private.invalid'
    assert 'failure_code' not in project(item['refs'],receipts,'unknown')['media_results'][0]


def test_saved_failure_diagnostic_survives_reuse(cached,monkeypatch):
    import tenant_media_evidence as evidence
    conn,_,ref=cached;monkeypatch.setattr(evidence,'_connect',lambda:conn)
    ref={**ref,'external_id':'synthetic','media_kind':'image','media_version':'v1','body_hash':'hash'}
    evidence.create_schema(conn)
    evidence.persist(conn.cursor(),A,[ref],[{'id':ref['id'],'status':'error','code':'media_unavailable',
      'failure_stage':'visual_analysis','failure_code':'dispatch_blocked'}]);conn.commit()
    saved=evidence.reusable(PROFILE,ref)
    reused=evidence.apply_reusable({'text':''},saved)
    assert reused['failure_code']=='dispatch_blocked' and reused['failure_stage']=='visual_analysis'
