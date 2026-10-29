"""Owner-session continuation HTTP contract; no live service or provider calls."""
import asyncio
import json
from unittest.mock import Mock
import tenant_knowledge_candidates_api as api
import tenant_case_continuation_ui as continuation


class Request:
    def __init__(self,method,query=None,body=None,origin='https://crm.test'):
        self.method=method;self.query=query or {};self.body=body or {};self.content_type='application/json'
        self.headers={'Origin':origin};self.match_info={'campaign_id':'campaign'}
    async def json(self):return self.body


def test_owner_session_origin_and_tenant_guards(monkeypatch):
    profile={'tenant_id':'tenant-a','telegram_id':11};calls=[]
    monkeypatch.setattr(continuation,'preview_owner',lambda *args:(calls.append(('read',args)) or {'case_count':22}))
    monkeypatch.setattr(continuation,'apply_owner',lambda *args:(calls.append(('apply',args)) or {'count':1}))
    handler=api.handler(lambda request:profile,'https://crm.test',Mock(),'campaign_case_continuation')
    async def run():
        expected={'expected_tenant_id':'tenant-a','expected_user_id':'11'}
        response=await handler(Request('GET',{**expected,'expected_version':'9'}));assert response.status==200
        assert json.loads(response.text)['result']['case_count']==22
        assert (await handler(Request('GET',{**expected,'expected_tenant_id':'tenant-b','expected_version':'9'}))).status==409
        body={**expected,'expected_version':9,'source_refs':[],'snapshot_hash':'a'*64,'request_id':'r','confirm_exact_preview':True}
        assert (await handler(Request('POST',body=body,origin='https://evil.test'))).status==403
        assert (await handler(Request('POST',body={**body,'expected_user_id':'12'}))).status==409
        assert (await handler(Request('POST',body=body))).status==200
        assert [c[0] for c in calls]==['read','apply']
        assert not {'expected_tenant_id','expected_user_id'} & set(calls[-1][1][2])
        denied=api.handler(lambda request:None,'https://crm.test',Mock(),'campaign_case_continuation')
        assert (await denied(Request('GET',{**expected,'expected_version':'9'}))).status==403
    asyncio.run(run())


def test_current_media_check_requires_same_origin_and_scoped_session(monkeypatch):
    import tenant_case_current_media as current_media
    from tenant_linear_policy import TenantLinearError
    profile={'tenant_id':'tenant-a','telegram_id':11};calls=[]
    def check(*args):calls.append(args);return {'current_bytes_checked':True,'provider':'connect'}
    monkeypatch.setattr(current_media,'verify_owner_source',check)
    handler=api.handler(lambda request:profile,'https://crm.test',Mock(),'campaign_current_media_check')
    data={'expected_tenant_id':'tenant-a','expected_user_id':'11','expected_version':9,'case_id':'c','message_id':'m','case_version':1}
    async def run():
        assert (await handler(Request('POST',body=data,origin='https://evil.test'))).status==403
        assert (await handler(Request('POST',body={**data,'expected_tenant_id':'other'}))).status==409
        response=await handler(Request('POST',body=data));assert response.status==200 and len(calls)==1
        def rate(*args):
            error=TenantLinearError('Connect: rate limit',429);error.code='connect_rate_limit';error.retry_after=12;raise error
        monkeypatch.setattr(current_media,'verify_owner_source',rate)
        response=await handler(Request('POST',body=data))
        assert response.status==429 and response.headers['Retry-After']=='12' and json.loads(response.text)['code']=='connect_rate_limit'
    asyncio.run(run())


def test_byte_snapshot_is_refetched_and_unknown_outcome_rejected(monkeypatch):
    import pytest
    import tenant_case_current_media as current
    from tenant_linear_policy import TenantLinearError
    case='0250f744-2714-4e68-8174-39721448fa76'
    message='60c57a14-fc9d-4ddc-a99b-27293228867e'
    digest='a'*64
    data={'expected_version':9,'source_refs':[{'case_id':case,'message_id':message,'expected_version':1}],
      'snapshot_hash':'b'*64,'request_id':'request','confirm_exact_preview':True,
      'current_byte_sources':[{'case_id':case,'message_id':message,'case_version':1,'sha256':digest}]}
    calls=[]
    monkeypatch.setattr(current,'verify_owner_source',lambda *args:{'current_sha256':digest,'continuation_supported':True,'attempt_reconciliation':{'state':'known_not_attempted'}})
    monkeypatch.setattr(continuation,'apply',lambda *args,**kwargs:(calls.append(kwargs) or {'count':1}))
    assert continuation.apply_owner({},'campaign',data)=={'count':1}
    assert calls[0]['verified_bytes']=={(case,message):digest}
    monkeypatch.setattr(current,'verify_owner_source',lambda *args:{'current_sha256':digest,'continuation_supported':True,'attempt_reconciliation':{'state':'unknown'}})
    with pytest.raises(TenantLinearError,match='unknown'):continuation.apply_owner({},'campaign',data)
    monkeypatch.setattr(current,'verify_owner_source',lambda *args:{'current_sha256':'c'*64,'continuation_supported':True,'attempt_reconciliation':{'state':'known_not_attempted'}})
    with pytest.raises(TenantLinearError,match='changed'):continuation.apply_owner({},'campaign',data)
    monkeypatch.setattr(current,'verify_owner_source',lambda *args:{'current_sha256':digest,'continuation_supported':False,'attempt_reconciliation':{'state':'known_not_attempted'}})
    with pytest.raises(TenantLinearError,match='audio'):continuation.apply_owner({},'campaign',data)
    monkeypatch.setattr(current,'verify_owner_source',lambda *args:{'current_sha256':digest,'continuation_supported':True,'attempt_reconciliation':{'state':'incomplete_history_eligible'},'saved_byte_results':[{'text':'cached'}]})
    with pytest.raises(TenantLinearError,match='already available'):continuation.apply_owner({},'campaign',{**data,'acknowledge_incomplete_history':True})
    assert len(calls)==1
    monkeypatch.setattr(current,'verify_owner_source',lambda *args:{'current_sha256':digest,'continuation_supported':True,'uncertain_retry_available':True,'attempt_reconciliation':{'state':'unknown'}})
    assert continuation.apply_owner({},'campaign',{**data,'acknowledge_unknown_selected':True})=={'count':1}
    assert calls[-1]['unknown_selected']=={(case,message)}
    with pytest.raises(TenantLinearError,match='snapshot'):continuation.apply_owner({},'campaign',{**data,'current_byte_sources':[],'acknowledge_unknown_selected':True})
    with pytest.raises(TenantLinearError,match='не более 10'):continuation.apply_owner({},'campaign',{**data,'current_byte_sources':data['current_byte_sources']*21,'acknowledge_unknown_selected':True})
    monkeypatch.setattr(current,'verify_owner_source',lambda *args:{'current_sha256':digest,'continuation_supported':True,'uncertain_retry_available':False,'attempt_reconciliation':{'state':'known_not_attempted'}})
    with pytest.raises(TenantLinearError,match='changed or is active'):continuation.apply_owner({},'campaign',{**data,'acknowledge_unknown_selected':True})


def test_byte_fence_uses_actual_bytes():
    import hashlib
    import pytest
    from tenant_knowledge_media import _check_case_bytes,CaseSourceBytesChanged
    expected=hashlib.sha256(b'picture').hexdigest()
    _check_case_bytes({'route':{'case_source_sha256':expected}},b'picture')
    with pytest.raises(CaseSourceBytesChanged):
        _check_case_bytes({'route':{'case_source_sha256':expected}},b'changed')


def test_verified_media_result_does_not_repeat_text_extraction(monkeypatch):
    from unittest.mock import MagicMock,Mock
    import tenant_knowledge_candidates_service as service
    import tenant_knowledge_media as media
    item={'tenant':'t','id':'id','route':{'case_source_sha256':'a'*64},
      'refs':[{'id':'m','media_kind':'image'}],'messages':[]}
    monkeypatch.setattr(service,'prepare',lambda *_:item)
    monkeypatch.setattr(service,'check_active',lambda *_:None)
    conn=MagicMock();cur=conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value={'enabled':True,'emergency_stop':False}
    monkeypatch.setattr(service,'_connect',lambda:conn)
    monkeypatch.setattr(service.store,'extraction_row',lambda *_:{})
    monkeypatch.setattr(service.store,'evidence',lambda *_:None)
    monkeypatch.setattr(service.store,'request_detail',lambda *_:{'ok':True})
    monkeypatch.setattr(media,'collect',lambda *args,**kw:([],[{'id':'m','status':'processed','observations':[{'text':'visible'}]}]))
    provider=Mock();finish=Mock();monkeypatch.setattr(service,'generate',provider);monkeypatch.setattr(service,'finish',finish)
    assert service.analyze({},{} )=={'ok':True}
    provider.assert_not_called()
    assert finish.call_args.args[2]==[] and finish.call_args.args[4]==''


def test_daily_ceiling_action_requires_expected_identity_and_origin(monkeypatch):
    import tenant_case_daily_ceiling as daily
    import tenant_knowledge_campaigns as campaigns
    profile={'tenant_id':'tenant-a','telegram_id':11};calls=[]
    monkeypatch.setattr(daily,'approve',lambda *args:(calls.append(args) or {'daily_call_ceiling':150}))
    monkeypatch.setattr(campaigns,'get',lambda *args:{'campaign':{'version':10}})
    handler=api.handler(lambda request:profile,'https://crm.test',Mock(),'campaign_control')
    data={'expected_tenant_id':'tenant-a','expected_user_id':'11','expected_version':9,'action':'approve_daily_ceiling_150'}
    async def run():
        assert (await handler(Request('POST',body=data,origin='https://evil.test'))).status==403
        assert (await handler(Request('POST',body={**data,'expected_tenant_id':'other'}))).status==409
        assert (await handler(Request('POST',body={**data,'ceiling':999}))).status==400
        assert (await handler(Request('POST',body=data))).status==200
        assert len(calls)==1 and calls[0][2]==9
    asyncio.run(run())
