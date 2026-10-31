"""Isolated runtime evidence integration; every provider and media fetch is mocked."""
import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, MagicMock
import pytest
import tenant_episode_media as runtime
import tenant_knowledge_media as core
from tenant_linear_policy import TenantLinearError
from tenant_media_evidence import source_hash
from test_connect_images import PNG


def row(kind='voice',number=1,**kw):
    return {'external_id':'connect:'+str(number),'body':'','direction':'incoming',
            'message_type':kind,'raw':{'mediaId':'media-'+str(number)},
            'happened_at':'2026-10-31T16:15:00Z',**kw}


class Authority(runtime.Execution):
    tenant='tenant';channel='channel';lead=17;conversation='conversation'
    config={'consent':True,'audio_enabled':True,'transcription_model':'whisper-1','video_enabled':True}
    key='unit-test';model='gpt-4o-mini';video_model='gpt-4o-mini';language='az';credential_source='company'
    def __init__(self,rows):
        self.rows=rows;self.check=Mock();self.calls=[]
    def load(self,profile,lead,external):
        selected=next(r for r in self.rows if r['external_id']==external)
        return {'provider':'connect','route':{'channel_id':self.channel,'conversation_id':self.conversation},'row':copy.deepcopy(selected)}
    def dispatch(self,model,operation,invoke):
        self.media_attempts=getattr(self,'media_attempts',0)+1
        self.calls.append((model,operation));return invoke()


@pytest.fixture
def sandbox(monkeypatch):
    # In-memory durable-store boundary for orchestration tests, shared by two
    # independent authorities (manual/automatic). SQL claims have separate tests.
    receipts={};flights={};lock=threading.Lock()
    monkeypatch.setattr(runtime,'reusable',lambda profile,ref:receipts.get(source_hash(ref)))
    monkeypatch.setattr('tenant_media_evidence.reusable',lambda profile,ref:receipts.get(source_hash(ref)))
    def claim(authority,ref):
        key=source_hash(ref)
        with lock:
            if key not in flights:
                flights[key]={'done':threading.Event(),'result':None};return key,'token',None
            current=flights[key]
        assert current['done'].wait(3)
        return key,None,current['result']
    def finish(authority,ref,key,token,result,*,retryable=False):
        saved={**result}
        if retryable:
            current=flights.pop(key)
            current['result']={'status':'unavailable','code':'pre_dispatch_retryable'}
            current['done'].set()
            return
        flights[key]['result']=saved
        receipts[key]={'status':saved['status'],'code':saved['code'],'result':saved,
                       'coverage':{'complete':saved['status']=='processed' and ref['media_kind']!='video'}}
        flights[key]['done'].set()
    monkeypatch.setattr(runtime,'_flight',claim);monkeypatch.setattr(runtime,'_finish',finish)
    monkeypatch.setattr(core,'download_media',AsyncMock(return_value=(PNG,'image/png')))
    monkeypatch.setattr('tenant_connect_voice.validate_audio',lambda data,*a,**kw:(data,'wav','audio/wav'))
    monkeypatch.setattr('tenant_chat_ai_provider.transcribe',Mock(return_value=('Səs mənbəyi',{'total_tokens':2})))
    monkeypatch.setattr('tenant_chat_ai_provider.transcribe_video',Mock(return_value=('Video mənbəyi',{}, {'speech_state':'confident','language':'az'})))
    monkeypatch.setattr('tenant_chat_ai_provider.generate',Mock(return_value=('Visible error 17',{'total_tokens':5})))
    monkeypatch.setattr(core,'extract',Mock(return_value=('attachment','text','Document mənbəyi')))
    monkeypatch.setattr('tenant_ai_spend_budget.limit_microusd',lambda tenant:1000000)
    monkeypatch.setattr('tenant_ai_video_model.require_video_tariff',Mock())
    monkeypatch.setattr(core,'video_frames',lambda *a:[(PNG,'image/png',{'second':0})])
    monkeypatch.setattr(core,'video_audio',lambda *a:(b'audio','wav','audio/wav'))
    return receipts,flights


def actual_authority(adapter,rows):
    if adapter=='manual':
        auth=runtime.ManualExecution('tenant',7,{'channel_id':'channel','conversation_id':'conversation',
            'lead_id':17,'request_id':'request'},{**Authority.config,'video_model':'gpt-4o-mini'},'v1','unit-test','gpt-4o-mini','az',rows)
    else:
        auth=runtime.AutoExecution({'tenant_id':'tenant','channel_id':'channel','conversation_id':'conversation','lead_id':17},
            {'config':Authority.config,'model':'gpt-4o-mini','api_key':'unit-test','video_model':'gpt-4o-mini',
             'agent_language':'az','credential_source':'company','video_api_key':'unit-test'})
    auth.rows=rows;auth.calls=[];auth.check=Mock()
    auth.load=Authority.load.__get__(auth)
    auth.dispatch=Authority.dispatch.__get__(auth)
    return auth


@pytest.mark.parametrize('adapter',['manual','automatic'])
@pytest.mark.parametrize('kind',['voice','audio','image','document','video'])
def test_each_kind_analysed_once_then_reused_by_other_mode(sandbox,monkeypatch,adapter,kind):
    rows=[row(kind)];auth=actual_authority(adapter,rows)
    mime={'voice':'audio/wav','audio':'audio/wav','image':'image/png','document':'text/plain','video':'video/mp4'}[kind]
    core.download_media.return_value=(PNG,mime)
    runtime.prepare(auth,rows)
    assert rows[0]['_episode_receipt']['status']==('partial' if kind=='video' else 'processed')
    first_calls=list(auth.calls)
    again=[row(kind)];second=actual_authority('automatic' if adapter=='manual' else 'manual',again)
    runtime.prepare(second,again)
    assert not second.calls
    assert core.download_media.await_count==1
    assert 'unavailable' not in again[0]['_copilot_media_notice']
    assert again[0]['_copilot_attachment_text']
    if kind!='document':assert first_calls
    # Same shared core is used by legitimate concrete adapters, not a
    # candidate-authority shim. The labels cover both initiating directions.
    assert issubclass(runtime.ManualExecution,runtime.Execution)
    assert issubclass(runtime.AutoExecution,runtime.Execution)


def test_all_episode_attachments_are_processed_including_more_than_old_four_file_limit(sandbox):
    rows=[row('image',n) for n in range(9)]
    auth=Authority(rows);runtime.prepare(auth,rows)
    assert len(auth.calls)==9
    assert all(r['_episode_receipt']['status']=='processed' for r in rows)


def test_changed_version_does_not_reuse_saved_receipt(sandbox):
    rows=[row('image')];auth=Authority(rows);runtime.prepare(auth,rows)
    changed=[row('image',raw={'mediaId':'replacement'})]
    new=Authority(changed);runtime.prepare(new,changed)
    assert len(new.calls)==1
    assert core.download_media.await_count==2


def test_old_image_new_voice_is_not_answered_from_image(sandbox):
    rows=[row('image',1,happened_at='2026-10-31T15:00:00Z'),
          row('text',2,direction='outgoing',body='Yoxlayıram'),row('voice',3)]
    auth=Authority(rows);core.download_media.return_value=(b'audio','audio/wav')
    runtime.prepare(auth,rows)
    assert auth.calls==[('whisper-1','asr')]
    lines,missing=runtime.context_lines(rows,rows)
    assert lines[0]['evidence_scope']=='historical'
    assert 'Visible error' not in str(lines)
    assert 'Səs mənbəyi' in lines[-1]['text']
    assert lines[-1]['source_id']=='connect:3' and lines[-1]['media_kind']=='voice'


def test_exact_receipt_wins_over_contradictory_legacy_body(sandbox):
    receipts,_=sandbox
    rows=[row('voice',body='[Voice unavailable]',raw={'mediaId':'v','voice_transcription':{'status':'failed'}})]
    ref=runtime.reference(rows[0],'channel',17)
    receipts[source_hash(ref)]={'status':'processed','code':'transcribed','coverage':{'complete':True},'result':{'transcript':'Dəqiq mənbə'}}
    auth=Authority(rows);runtime.prepare(auth,rows)
    assert not auth.calls
    assert rows[0]['_copilot_context_body']==''
    assert 'Dəqiq mənbə' in rows[0]['_copilot_attachment_text']
    assert 'unavailable' not in rows[0]['_copilot_media_notice']


def test_legacy_unversioned_transcript_is_not_exact_version_receipt(sandbox):
    rows=[row('voice',body='old legacy transcript',raw={'mediaId':'changed','voice_transcription':{'status':'completed'}})]
    core.download_media.return_value=(b'audio','audio/wav')
    auth=Authority(rows);runtime.prepare(auth,rows)
    assert auth.calls==[('whisper-1','asr')]
    assert 'old legacy' not in rows[0]['_copilot_attachment_text']


def test_concurrent_click_event_join_same_flight(sandbox,monkeypatch):
    started=threading.Event();release=threading.Event()
    def provider(*a,**kw):
        started.set();assert release.wait(3);return 'one observation',{}
    monkeypatch.setattr('tenant_chat_ai_provider.generate',Mock(side_effect=provider))
    a=Authority([row('image')]);b=Authority([row('image')])
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(runtime.prepare,a,a.rows)
        assert started.wait(2)
        second=pool.submit(runtime.prepare,b,b.rows)
        release.set();first.result();second.result()
    assert len(a.calls)+len(b.calls)==1
    assert core.download_media.await_count==1
    assert 'one observation' in b.rows[0]['_copilot_attachment_text']


def test_partial_failure_remains_explicit_and_does_not_repeat_spend(sandbox,monkeypatch):
    monkeypatch.setattr('tenant_chat_ai_provider.generate',Mock(side_effect=TimeoutError()))
    rows=[row('image')];auth=Authority(rows)
    runtime.prepare(auth,rows)
    assert rows[0]['_episode_receipt']['status']=='error'
    retry=Authority([row('image')]);runtime.prepare(retry,retry.rows)
    assert not retry.calls
    assert 'processed' not in retry.rows[0]['_copilot_media_notice']


def test_episode_boundaries_employee_resume_and_time():
    assert runtime.episode([row('image',1,happened_at='2026-10-31T15:00:00Z'),row('voice',2)])==[row('voice',2)]
    employee=row('text',2,raw={'verified_employee_id':7})
    assert runtime.episode([row('image'),employee,row('voice',3)])==[row('voice',3)]
    assert runtime.episode([row('voice',voice_eligible=False),row('image',3)])==[row('image',3)]


def test_authority_loss_prevents_even_cached_evidence(sandbox):
    auth=Authority([row('image')]);auth.check.side_effect=TenantLinearError('revoked',403)
    with pytest.raises(TenantLinearError):runtime.prepare(auth,auth.rows)
    assert not auth.calls
    core.download_media.assert_not_awaited()


def test_manual_prompt_preserves_sources_language_and_continuation_rule():
    from tenant_operator_copilot import INSTRUCTIONS,_bounded_conversation
    assert 'Never repeat a greeting when any prior EMPLOYEE message exists' in INSTRUCTIONS
    assert 'even when the employee request is in another language' in INSTRUCTIONS
    assert 'Preserve source quotations in their original language' in INSTRUCTIONS
    rows=[row('voice',_copilot_media_notice='[processed]',_copilot_attachment_text='AZ source')]
    lines=_bounded_conversation(rows)
    assert lines[0]['source_id']=='connect:1' and lines[0]['media_kind']=='voice'


def test_budget_denial_prevents_provider_dispatch(monkeypatch):
    authority=Authority([row('image')])
    authority.current_ref=runtime.reference(authority.rows[0],'channel',17)
    conn=MagicMock();conn.__enter__.return_value=conn
    monkeypatch.setattr(runtime,'_connect',lambda:conn)
    monkeypatch.setattr('tenant_ai_spend_budget.ensure_schema',Mock())
    reserve=Mock(side_effect=TenantLinearError('budget exhausted',429))
    monkeypatch.setattr('tenant_ai_spend_budget.reserve_media',reserve)
    authority.admit_call=Mock()
    invoke=Mock()
    with pytest.raises(TenantLinearError):runtime.Execution.dispatch(authority,'gpt-4o-mini','vision_low',invoke)
    invoke.assert_not_called()
    authority.admit_call.assert_called_once()


def test_manual_assignment_or_new_inbound_fence_is_rechecked(monkeypatch):
    import tenant_operator_copilot as copilot
    conn=MagicMock();conn.__enter__.return_value=conn
    cur=conn.cursor.return_value.__enter__.return_value
    command={'channel_id':'channel','conversation_id':'conversation','lead_id':17,'_agent_binding':'binding'}
    auth=runtime.ManualExecution('tenant',7,command,Authority.config,'v1','key','gpt-4o-mini','az',[row()])
    monkeypatch.setattr(runtime,'_connect',lambda:conn)
    monkeypatch.setattr('tenant_operator_access.access',Mock(return_value=('tenant',7,SimpleNamespace(can_use_chat_ai=lambda:True))))
    monkeypatch.setattr('tenant_operator_access.assert_route',Mock())
    monkeypatch.setattr(copilot,'_assigned_context',Mock(return_value={}))
    monkeypatch.setattr(copilot,'_agent_binding',Mock(return_value='other-assignment'))
    with pytest.raises(TenantLinearError):auth.check()
    cur.execute.assert_not_called()
    monkeypatch.setattr(copilot,'_agent_binding',Mock(return_value='binding'))
    cur.fetchone.side_effect=[{'updated_at':'v1','metadata':{'enabled':True,'consent':True}}, {'external_id':'new-inbound'}]
    with pytest.raises(TenantLinearError):auth.check()


def test_auto_requires_real_live_job_and_budget(monkeypatch):
    auth=runtime.AutoExecution({'tenant_id':'tenant','channel_id':'channel','conversation_id':'conversation','lead_id':17},
        {'model':'gpt-4o-mini','api_key':'mock'})
    monkeypatch.setattr('tenant_connect_images.live',Mock(return_value=False))
    budget=Mock(return_value=True)
    monkeypatch.setattr('tenant_connect_voice.generation_budget_available',budget)
    with pytest.raises(TenantLinearError):auth.check()
    budget.assert_not_called()


def test_runtime_has_no_send_or_candidate_mutation_authority():
    import ast,inspect
    tree=ast.parse(inspect.getsource(runtime))
    names={n.func.id for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}
    assert not names & {'send_ai_reply_if_active','send_agent','send_message','prepare_candidate','create_candidate'}


def test_admitted_platform_job_retains_existing_audio_permission_without_company_key():
    auth=runtime.AutoExecution({'tenant_id':'tenant','channel_id':'channel','conversation_id':'conversation','lead_id':17},
        {'model':'gpt-4o-mini','api_key':'platform-test-key','config':{},'credential_source':'platform'})
    auth.check=Mock()
    item=auth.item(runtime.reference(row('voice'),'channel',17),{'ref':'voice','text':''})
    assert item['route']['allow_paid_asr'] is True
    assert item['route']['allow_external_media'] is True
    assert item['credential_source']=='platform'


def test_truncated_voice_is_partial_including_after_reuse(sandbox,monkeypatch):
    rows=[row('voice')];core.download_media.return_value=(b'audio','audio/wav')
    monkeypatch.setattr('tenant_chat_ai_provider.transcribe',Mock(return_value=('mənbə '*1000,{})))
    authority=Authority(rows);runtime.prepare(authority,rows)
    assert rows[0]['_episode_receipt']['status']=='partial'
    assert rows[0]['_episode_receipt']['code']=='transcript_truncated'
    again=Authority([row('voice')]);runtime.prepare(again,again.rows)
    assert again.rows[0]['_episode_receipt']['status']=='partial' and not again.calls


def test_scanned_pdf_with_only_two_of_three_pages_read_remains_partial(sandbox,monkeypatch):
    import io
    from pypdf import PdfWriter
    document=PdfWriter()
    for _ in range(3):document.add_blank_page(width=100,height=100)
    output=io.BytesIO();document.write(output)
    core.download_media.return_value=(output.getvalue(),'application/pdf')
    monkeypatch.setattr(core,'extract',Mock(side_effect=TenantLinearError('textless',415)))
    monkeypatch.setattr(core,'pdf_pages',lambda data:[(PNG,'image/png',{'page':1}),(PNG,'image/png',{'page':2})])
    auth=Authority([row('document')]);runtime.prepare(auth,auth.rows)
    receipt=auth.rows[0]['_episode_receipt']
    assert receipt['status']=='partial' and receipt['code']=='document_pages_sampled'
    assert receipt['coverage']['complete'] is False
    assert receipt['document_pages']==3 and receipt['rendered_pages']==2
    again=Authority([row('document')]);runtime.prepare(again,again.rows)
    assert again.rows[0]['_episode_receipt']['status']=='partial' and not again.calls


@pytest.mark.parametrize('kind',['voice','video'])
def test_policy_denial_before_provider_can_be_retried_after_authorized_setting_change(sandbox,kind):
    rows=[row(kind)];auth=Authority(rows)
    auth.config={**Authority.config,'audio_enabled':False,'video_enabled':False}
    core.download_media.return_value=(b'audio','audio/wav') if kind=='voice' else (PNG,'video/mp4')
    runtime.prepare(auth,rows)
    assert not auth.calls and rows[0]['_episode_receipt']['status']=='unavailable'
    assert not sandbox[0] and not sandbox[1]
    allowed=Authority([row(kind)])
    runtime.prepare(allowed,allowed.rows)
    assert allowed.calls
    assert allowed.rows[0]['_episode_receipt']['status'] in {'processed','partial'}


def test_budget_denial_before_provider_releases_flight_without_poisoning_receipt(sandbox):
    auth=Authority([row('image')])
    auth.dispatch=Mock(side_effect=TenantLinearError('budget exhausted',429))
    runtime.prepare(auth,auth.rows)
    assert not sandbox[0] and not sandbox[1]
    allowed=Authority([row('image')]);runtime.prepare(allowed,allowed.rows)
    assert len(allowed.calls)==1


def test_successful_evidence_completes_before_reply_authority_is_rechecked(sandbox,monkeypatch):
    auth=Authority([row('voice')]);core.download_media.return_value=(b'audio','audio/wav')
    def arrived(*args,**kw):
        auth.check.side_effect=TenantLinearError('new inbound',409)
        return 'Səs mənbəyi',{}
    monkeypatch.setattr('tenant_chat_ai_provider.transcribe',Mock(side_effect=arrived))
    runtime.prepare(auth,auth.rows)
    assert sandbox[1][next(iter(sandbox[1]))]['done'].is_set()
    with pytest.raises(TenantLinearError):auth.check()
    later=Authority([row('voice')]);runtime.prepare(later,later.rows)
    assert not later.calls and 'Səs mənbəyi' in later.rows[0]['_copilot_attachment_text']
