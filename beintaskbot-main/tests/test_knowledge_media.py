"""Synthetic historical attachments; providers are mocked, never paid."""
import asyncio
import copy
import io
import json
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from unittest.mock import AsyncMock, Mock
import pytest
import tenant_knowledge_media as media
from test_knowledge_candidates import ready
from test_tenant_support_inbox import A,PROFILE
from tenant_linear_policy import TenantLinearError
from test_connect_images import PNG

@pytest.fixture
def context(monkeypatch):
    import tenant_knowledge_media_cache as cache
    def unit_asr(profile,item,model,media,invoke,check_before_ai=None,*,return_diagnostics=False):
        item['asr_attempted']=True
        if check_before_ai: check_before_ai()
        response=invoke(item['key'],model,*media,with_usage=True)
        if return_diagnostics:
            text,usage,diagnostics=response
            details={'speech_state':diagnostics.get('speech_state','uncertain'),
                'speech_language':diagnostics.get('language',''),'speech_reason':diagnostics.get('reason','')}
            return text,usage,True,details
        text,usage=response
        return text,usage,True
    monkeypatch.setattr(cache,'transcribe_once',unit_asr)
    item={'tenant':'tenant','model':'gpt-4.1-2025-04-14','route':{'conversation_id':'demo@g.us'},'refs':[], 'messages':[]}
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({
        'gpt-4.1-2025-04-14':{'input':2_000_000,'output':8_000_000}}))
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({
        'gpt-4.1-2025-04-14':{'vision_low':8_896}}))
    def add(kind='image',channel='one'):
        index=len(item['refs'])+1
        item['refs'].append({'id':str(index),'channel_id':channel,'lead_id':index,'external_id':'connect:'+str(index),'media_kind':kind,'role':'customer','happened_at':'2026-10-18T00:00:00Z'})
        item['messages'].append({'ref':'m'+str(index),'role':'customer','text':'Synthetic caption'})
    monkeypatch.setattr(media,'load',lambda p,l,m:{'provider':'connect','route':{'channel_id':item['refs'][l-1]['channel_id'],'conversation_id':'demo@g.us'},'row':{'message_type':item['refs'][l-1]['media_kind']}})
    download=AsyncMock(return_value=(PNG,'image/png'));monkeypatch.setattr(media,'download_media',download)
    monkeypatch.setattr(media,'cached_transcript',lambda *a:'')
    return item,add,download

def test_photo_permission_exact_model_and_origin(context):
    item,add,download=context;add()
    images,results=media.collect({},item)
    assert not images and results[0]['status']=='unavailable'
    assert results[0]['code']=='external_media_permission_required'
    item['route']['allow_external_media']=True
    images,results=media.collect({},item)
    assert images[0]['source_ref']=='m1' and results[0]['channel_id']=='one'
    assert results[0]['role']=='customer' and results[0]['happened_at']
    assert results[0]['code']=='awaiting_model'
    item['model']='gpt-4.1-unknown'
    assert media.collect({},item)[1][0]['status']=='unsupported'

def test_unified_two_channel_identical_photo_is_one_input(context):
    item,add,download=context;add(channel='one');add(channel='two');item['route']['allow_external_media']=True
    images,results=media.collect({},item)
    assert len(images)==1 and results[1]['duplicate_of']=='1'
    assert [r['channel_id'] for r in results]==['one','two']

@pytest.mark.parametrize('status,expected',[(404,'error'),(410,'error'),(403,'error'),(502,'error'),(415,'unsupported')])
def test_evolution_failure_is_never_success(context,status,expected):
    item,add,download=context;add();download.side_effect=TenantLinearError('synthetic',status)
    images,results=media.collect({},item)
    assert images==[] and results[0]['status']==expected

def test_channel_or_conversation_mismatch_never_downloads(context,monkeypatch):
    item,add,download=context;add()
    monkeypatch.setattr(media,'load',lambda *a:{'provider':'connect','route':{'channel_id':'foreign','conversation_id':'demo@g.us'}})
    result=media.collect({},item)[1][0]
    assert (result['status'],result['code'],result['failure_stage'])==('error','source_access_denied','source_validation')
    download.assert_not_called()


@pytest.mark.parametrize('stage,status,code',[
    ('source_validation',403,'source_access_denied'),('source_validation',404,'source_reference_unavailable'),
    ('media_download',404,'source_fetch_unavailable'),('media_download',429,'source_rate_limited'),
    ('media_download',502,'source_check_failed')])
def test_source_failure_diagnostics_do_not_claim_file_loss(context,monkeypatch,stage,status,code):
    item,add,download=context;add();item['route']['allow_external_media']=True
    error=TenantLinearError('secret url and sensitive data',status)
    if stage=='source_validation':monkeypatch.setattr(media,'load',lambda *_:(_ for _ in ()).throw(error))
    else:download.side_effect=error
    images,results=media.collect({},item)
    assert not images and results[0]['status']=='error'
    assert results[0]['code']==code and results[0]['failure_stage']==stage
    assert 'secret' not in json.dumps(results)
    if stage=='source_validation':download.assert_not_called()

def test_acknowledged_current_connect_retry_uses_loader_and_actual_bytes_not_metadata_receipt(context,monkeypatch):
    import tenant_media_evidence as evidence
    import tenant_knowledge_media_cache as cache
    item,add,download=context;add('image');item.update(key='synthetic',model='gpt-4.1')
    item['route'].update(allow_external_media=True,allow_vision=True,owner_retry_unknown=True,
        owner_retry_basis='current_connect_message',media_grant_id='grant',message_ids=['1'],source_version='a'*64)
    # A metadata-version receipt may describe different bytes; do not consult it.
    reused=Mock(return_value={'status':'processed','observation':'stale metadata result'})
    monkeypatch.setattr(evidence,'reusable',reused)
    observed=[]
    monkeypatch.setattr(cache,'observe_once',lambda _p,_i,_r,content,_mime,_origin,*a,**k:
        (observed.append(content) or 'fresh byte observation',{},True))
    images,results=media.collect({},item)
    assert images==[] and results[0]['status']=='processed'
    assert results[0]['observations'][0]['text']=='fresh byte observation'
    assert observed==[PNG] and download.await_count==1
    reused.assert_not_called()

def test_acknowledged_current_connect_retry_fails_closed_if_loader_unavailable(context,monkeypatch):
    import tenant_knowledge_media_cache as cache
    item,add,download=context;add('image')
    item['route'].update(allow_external_media=True,allow_vision=True,owner_retry_unknown=True,
        owner_retry_basis='current_connect_message',media_grant_id='grant',message_ids=['1'],source_version='a'*64)
    monkeypatch.setattr(media,'load',lambda *_:(_ for _ in ()).throw(TenantLinearError('expired',410)))
    observed=Mock();monkeypatch.setattr(cache,'observe_once',observed)
    images,results=media.collect({},item)
    assert images==[] and results[0]['status']=='error' and results[0]['code']=='source_reference_unavailable'
    download.assert_not_awaited();observed.assert_not_called()


def test_video_input_bound_is_model_specific_and_rejects_before_cache_or_provider(monkeypatch):
    import tenant_knowledge_media_cache as cache
    import tenant_ai_video_model as video_model
    from unittest.mock import Mock

    assert video_model.input_tokens_upper('gpt-4.1-2025-04-14') <= video_model.INPUT_UPPER
    assert video_model.input_tokens_upper('gpt-4o-mini') > video_model.INPUT_UPPER
    assert video_model.OUTPUT_UPPER == 600 and video_model.MAX_FRAMES == 2
    monkeypatch.setattr('tenant_connect_images.supports_images', lambda _model: True)
    monkeypatch.setattr('tenant_ai_spend_budget.media_price', lambda *_: 8896)
    monkeypatch.setattr('tenant_ai_spend_budget.price', lambda *_: (2_000_000,8_000_000))
    monkeypatch.setattr('tenant_ai_spend_budget.limit_microusd', lambda *_: 10_000_000)
    quote=video_model.quote('gpt-4.1-2025-04-14','synthetic-tenant')
    assert quote['max_frames']==2 and quote['max_vision_calls']==2
    assert quote['input_upper_tokens']==video_model.INPUT_UPPER
    assert quote['request_input_upper_tokens']<=quote['input_upper_tokens']
    assert quote['output_upper_tokens']==600

    monkeypatch.setattr('tenant_ai_spend_budget.media_price', lambda *_: 2500)
    with pytest.raises(TenantLinearError,match='video_model_tariff_below_input_bound'):
        video_model.require_video_tariff('gpt-4.1-2025-04-14')

    monkeypatch.setattr(cache, '_connect', lambda: (_ for _ in ()).throw(AssertionError('cache claim reached')))
    monkeypatch.setattr('tenant_knowledge_candidates_service.check_active', lambda *_: None)
    ref={'id':'synthetic-message','channel_id':'synthetic-channel','media_kind':'video'}
    item={'tenant':'synthetic-tenant','model':'gpt-4o-mini','key':'mock-only',
        'route':{'allow_external_media':True,'allow_vision':True,
                 '_video_input_model':'gpt-4o-mini'},'refs':[ref]}
    provider=Mock()
    with pytest.raises(TenantLinearError, match='video_model_input_bound_exceeded'):
        cache.observe_once(PROFILE,item,ref,b'synthetic frame','image/jpeg',{'second':0},provider)
    provider.assert_not_called()


def test_video_input_bound_rejects_prompt_growth_without_provider(monkeypatch):
    import tenant_knowledge_media_cache as cache
    import tenant_ai_video_model as video_model

    monkeypatch.setattr(cache, 'OBSERVATION_PROMPT', 'x' * video_model.INPUT_UPPER)
    with pytest.raises(TenantLinearError, match='video_model_input_bound_exceeded'):
        video_model.require_input_bound('gpt-4.1-2025-04-14')


def test_video_input_bound_blocks_single_case_before_decode_or_asr(context,monkeypatch):
    import tenant_knowledge_media_cache as cache
    item,add,download=context;add('video')
    item['model']='gpt-4o-mini'
    item['route'].update(allow_external_media=True,allow_vision=True,allow_paid_asr=True)
    download.return_value=(b'synthetic video bytes','video/mp4')
    frames=Mock();audio=Mock();transcribe=Mock()
    monkeypatch.setattr(media,'video_frames',frames)
    monkeypatch.setattr(media,'video_audio',audio)
    monkeypatch.setattr(cache,'transcribe_once',transcribe)

    _,results=media.collect({},item)
    assert results[0]['status']=='error'
    assert results[0]['failure_stage']=='media_validation'
    frames.assert_not_called();audio.assert_not_called();transcribe.assert_not_called()

def test_count_limit_unsupported_and_pending(context):
    item,add,download=context
    for _ in range(5):add('sticker')
    _,results=media.collect({},item)
    assert [r['status'] for r in results]==['unsupported']*4+['partial']
    assert results[-1]['code']=='file_count_limit'
    download.assert_not_called()


def test_owner_batch_retry_cache_is_single_attempt_and_shared_by_same_artifact(ready,monkeypatch):
    import uuid
    from unittest.mock import Mock
    import tenant_knowledge_candidates_service as service
    import tenant_knowledge_media_cache as cache
    db,_,_=ready
    monkeypatch.setattr(cache,'_connect',lambda:db)
    monkeypatch.setattr(service,'_connect',lambda:db)
    monkeypatch.setattr(service,'check_active',lambda *_:None)
    message_id=str(uuid.uuid4());provider_id=str(uuid.uuid4());grant_id=str(uuid.uuid4());content=b'synthetic printer image bytes'
    ref={'id':message_id,'external_id':'connect:'+provider_id,'channel_id':'00000000-0000-0000-0000-0000000000c9','media_version':'a'*64}
    route={'owner_retry_unknown':True,'media_grant_id':grant_id,'message_ids':[message_id],
      'channel_id':ref['channel_id'],
      'source_version':'a'*64,'owner_retry_basis':'current_connect_message','allow_external_media':True,'allow_vision':True}
    item={'tenant':A,'model':'gpt-4.1','route':route,'refs':[ref],'key':'mock-key','vision_attempts':0}
    provider=Mock(return_value=('A synthetic label observation.',{'input_tokens':20,'output_tokens':8,'total_tokens':28}))
    result=cache.observe_once(PROFILE,item,ref,content,'image/png',{},provider)
    assert result==('A synthetic label observation.',{'input_tokens':20,'output_tokens':8,'total_tokens':28},True)
    provider.assert_called_once()
    # A second current-source route under the same grant cannot call again;
    # its completed artifact is reusable even when this copy has no old hold.
    ordinary={**item,'route':{**route,'owner_retry_unknown':False}}
    reused=cache.observe_once(PROFILE,ordinary,ref,content,'image/png',{},provider)
    assert reused==('A synthetic label observation.',{},False)
    provider.assert_called_once()
    rows=db.execute("SELECT state,metadata FROM saas_knowledge_media_cache WHERE tenant_id=%s::uuid AND metadata->>'owner_retry_grant'=%s",(A,grant_id))
    assert len(rows)==2 and {row['state'] for row in rows}=={'completed'}
    assert sum(row['metadata'].get('owner_retry_artifact') is True for row in rows)==1
    # The same approval cannot dispatch again if the selected message bytes
    # or provider message binding changes after its first attempt.
    changed_content=b'different bytes from same current message'
    with pytest.raises(TenantLinearError,match='Источник изменился'):
        cache.observe_once(PROFILE,item,ref,changed_content,'image/png',{},provider)
    changed_ref={**ref,'external_id':'connect:'+str(uuid.uuid4())}
    with pytest.raises(TenantLinearError,match='Источник изменился'):
        cache.observe_once(PROFILE,{**item,'refs':[changed_ref]},changed_ref,content,'image/png',{},provider)
    provider.assert_called_once()

    # An unknown billed attempt also consumes the source-scoped permit. A
    # different payload cannot create another namespace under the same grant.
    unknown_id=str(uuid.uuid4());unknown_ref={'id':unknown_id,'external_id':'connect:'+str(uuid.uuid4()),'channel_id':ref['channel_id'],'media_version':'a'*64}
    unknown_route={**route,'media_grant_id':str(uuid.uuid4()),'message_ids':[unknown_id]}
    unknown_item={**item,'route':unknown_route,'refs':[unknown_ref]}
    uncertain_provider=Mock(side_effect=RuntimeError('synthetic timeout'))
    uncertain_content=b'a different synthetic image with unknown outcome'
    with pytest.raises(RuntimeError):cache.observe_once(PROFILE,unknown_item,unknown_ref,uncertain_content,'image/png',{},uncertain_provider)
    with pytest.raises(TenantLinearError) as second:
        cache.observe_once(PROFILE,unknown_item,unknown_ref,b'changed after timeout','image/png',{},uncertain_provider)
    assert second.value.media_outcome_unknown
    uncertain_provider.assert_called_once()


@pytest.mark.parametrize('state',['unknown','running','completed'])
def test_legacy_owner_retry_cache_preserves_hold_or_exact_result(ready,monkeypatch,state):
    import hashlib,uuid
    from unittest.mock import Mock
    import tenant_knowledge_candidates_service as service
    import tenant_knowledge_media_cache as cache
    import tenant_knowledge_candidates_store as store
    from tenant_linear_policy import TenantLinearError
    db,_,_=ready
    monkeypatch.setattr(cache,'_connect',lambda:db);monkeypatch.setattr(service,'_connect',lambda:db)
    monkeypatch.setattr(service,'check_active',lambda *_:None)
    message_id=str(uuid.uuid4());grant_id=str(uuid.uuid4());external='connect:'+str(uuid.uuid4())
    channel='00000000-0000-0000-0000-0000000000c9';content=b'legacy retry exact bytes'
    ref={'id':message_id,'external_id':external,'channel_id':channel,'media_version':'a'*64}
    route={'owner_retry_unknown':True,'owner_retry_basis':'current_connect_message','media_grant_id':grant_id,
      'message_ids':[message_id],'channel_id':channel,'source_version':'a'*64,
      'allow_external_media':True,'allow_vision':True}
    item={'tenant':A,'key':'mock','model':'gpt-4.1','route':route,'refs':[ref]}
    meta={'kind':'vision','version':'observations-v1','model':'gpt-4.1','detail':'low',
      'channel_id':channel,'mime':'image/png','origin':{},'sha256':hashlib.sha256(content).hexdigest()}
    artifact={key:meta[key] for key in ('kind','version','model','detail','mime','sha256')}
    base=store.digest(artifact);legacy=store.digest({'artifact':base,'owner_retry_grant':grant_id,'component':'vision'})
    with db.cursor() as cur:
        cache.schema(cur)
        cur.execute("INSERT INTO saas_knowledge_media_cache(tenant_id,fingerprint,state,token,transcript,metadata) VALUES(%s::uuid,%s,%s,%s::uuid,%s,%s::jsonb)",
          (A,legacy,state,str(uuid.uuid4()),'exact legacy cached result',json.dumps({'owner_retry_grant':grant_id,'owner_retry_component':'vision','artifact_fingerprint':base})))
    db.commit()
    provider=Mock(return_value=('must not dispatch',{'total_tokens':1}))
    if state=='completed':
        assert cache.observe_once(PROFILE,item,ref,content,'image/png',{},provider)==('exact legacy cached result',{},False)
    else:
        with pytest.raises(TenantLinearError) as blocked:
            cache.observe_once(PROFILE,item,ref,content,'image/png',{},provider)
        assert blocked.value.media_outcome_unknown
    assert provider.call_count==0

def test_plain_document_extracts_redacts_and_preserves_caption(context):
    item,add,download=context;add('document')
    download.return_value=(b'Restart demo. email: demo@example.com\npassword: fake-secret','text/plain')
    images,results=media.collect({},item)
    assert not images and results[0]['status']=='processed'
    assert 'Synthetic caption' in item['messages'][0]['text']
    assert 'demo@example.com' not in item['messages'][0]['text'] and 'fake-secret' not in item['messages'][0]['text']


def test_document_text_truncation_is_reported_as_partial(context,monkeypatch):
    item,add,download=context;add('document')
    monkeypatch.setattr(media,'extract',lambda *a:('txt',None,'x'*4001))
    download.return_value=(b'x'*4001,'text/plain')
    _,results=media.collect({},item)
    assert results[0]['status']=='partial'
    assert results[0]['code']=='document_text_truncated'
    assert results[0]['truncated'] is True
    assert len(results[0]['observation'])==4000

@pytest.mark.parametrize('kind',['audio','voice'])
@pytest.mark.parametrize('language,text',[('az','Miqdarı yoxlayın'),('ru','Проверьте количество'),('en','Check quantity')])
def test_cached_asr_reused_without_download(context,monkeypatch,kind,language,text):
    item,add,download=context;add(kind);monkeypatch.setattr(media,'cached_transcript',lambda *a:text)
    assert media.collect({},item)[1][0]['code']=='existing_transcript'
    assert text in item['messages'][0]['text'];download.assert_not_called()

def test_asr_requires_both_explicit_permissions(context):
    item,add,download=context;add('audio')
    assert media.collect({},item)[1][0]['code']=='asr_authorization_required'
    download.assert_not_called()

@pytest.mark.parametrize('text',['Məhsulları yoxlayın','Проверьте товары','Check the products'])
def test_missing_asr_explicit_paid_call_uses_existing_provider(context,monkeypatch,text):
    import tenant_connect_voice as voice
    import tenant_chat_ai_provider as provider
    item,add,download=context;add('audio');add('audio')
    item.update(key='synthetic',credential_source='platform')
    item['route'].update(allow_external_media=True,allow_paid_asr=True)
    download.return_value=(b'OggSsynthetic','audio/ogg')
    monkeypatch.setattr(service,'check_active',lambda *a:None)
    monkeypatch.setattr(voice,'validate_audio',lambda *a,**kw:(b'synthetic','ogg','audio/ogg'))
    calls=[]
    monkeypatch.setattr(provider,'transcribe',lambda *a,**kw:(calls.append(a) or (text,{'total_tokens':20})))
    images,results=media.collect({},item)
    assert len(calls)==1 and calls[0][1]=='gpt-transcribe'
    assert results[0]['transcript']==text and results[0]['status']=='processed'
    assert results[1]['status']=='unavailable' and item['asr_usage']['total_tokens']==20

def test_docx_extractor_uses_no_external_relationships(context):
    import zipfile
    item,add,download=context;add('document')
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as archive:
        archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:t>Synthetic solution</w:t></w:p></w:document>')
    download.return_value=(out.getvalue(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    assert media.collect({},item)[1][0]['code']=='document_text'
    assert 'Synthetic solution' in item['messages'][0]['text']

def test_xlsx_extractor_keeps_sheet_and_cell_provenance(context):
    import zipfile
    item,add,download=context;add('document')
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as archive:
        archive.writestr('xl/worksheets/sheet1.xml',
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Printer model</t></is></c><c r="B1"><v>42</v></c></row></sheetData></worksheet>')
    download.return_value=(out.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    _,results=media.collect({},item)
    assert results[0]['status']=='processed'
    assert '[Sheet 1, cell A1] Printer model' in results[0]['observation']
    assert '[Sheet 1, cell B1] 42' in results[0]['observation']
    assert '[Sheet 1, cell A1]' in item['messages'][0]['text']

def test_xlsx_rejects_corrupt_container_with_explicit_error(context):
    item,add,download=context;add('document')
    download.return_value=(b'PK not a zip archive','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    _,results=media.collect({},item)
    assert results[0]['status']=='error'
    assert results[0]['code']=='media_unavailable'

def test_encrypted_xlsx_is_reported_unsupported(context,monkeypatch):
    from types import SimpleNamespace
    import tenant_ai_knowledge_files as files
    class EncryptedArchive:
        def __enter__(self):return self
        def __exit__(self,*_args):return False
        def infolist(self):return [SimpleNamespace(flag_bits=1,file_size=128)]
    item,add,download=context;add('document')
    download.return_value=(b'PK encrypted','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    monkeypatch.setattr(files.zipfile,'ZipFile',lambda _stream:EncryptedArchive())
    _,results=media.collect({},item)
    assert results[0]['status']=='unsupported'
    assert results[0]['code']=='unsupported_document_feature'

def test_password_protected_ole_workbook_with_xlsx_mime_is_reported_unsupported(context):
    item,add,download=context;add('document')
    download.return_value=(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'+b'protected-office-data',
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    _,results=media.collect({},item)
    assert results[0]['status']=='unsupported'
    assert results[0]['code']=='unsupported_document_feature'

@pytest.mark.parametrize('feature,xml',[
    ('chart',None),
    ('formula','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><f>1+1</f><v>2</v></c></row></sheetData></worksheet>'),
    ('defined_name',None),
])
def test_xlsx_with_unanalyzed_content_is_explicitly_unsupported(context,feature,xml):
    import zipfile
    item,add,download=context;add('document')
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as archive:
        archive.writestr('xl/worksheets/sheet1.xml',xml or
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Visible cells</t></is></c></row></sheetData></worksheet>')
        if feature=='chart':
            archive.writestr('xl/charts/chart1.xml','<chartSpace/>')
        elif feature=='defined_name':
            archive.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><definedNames><definedName name="Total">SUM(Sheet1!A1:A4)</definedName></definedNames></workbook>')
    download.return_value=(out.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    _,results=media.collect({},item)
    assert results[0]['status']=='unsupported'
    assert results[0]['code']=='unsupported_document_feature'
    assert 'Visible cells' not in item['messages'][0]['text']

def test_scanned_pdf_is_not_claimed_as_read_without_consent(context):
    from pypdf import PdfWriter
    item,add,download=context;add('document')
    out=io.BytesIO();writer=PdfWriter();writer.add_blank_page(width=200,height=200);writer.write(out)
    download.return_value=(out.getvalue(),'application/pdf')
    assert media.collect({},item)[1][0]['code']=='external_media_permission_required'

def test_scanned_pdf_bounded_pages(context,monkeypatch):
    from pypdf import PdfWriter
    item,add,download=context;add('document');item['route']['allow_external_media']=True
    out=io.BytesIO();writer=PdfWriter();writer.add_blank_page(width=200,height=200);writer.write(out)
    download.return_value=(out.getvalue(),'application/pdf')
    monkeypatch.setattr(media,'pdf_pages',lambda b:[(PNG,'image/png',{'page':1})])
    images,results=media.collect({},item)
    assert len(images)==1 and results[0]['visuals']==[{'page':1}]

@pytest.mark.skipif(not shutil.which('pdftoppm'),reason='local PDF renderer unavailable')
def test_real_synthetic_pdf_page_rendering():
    from pypdf import PdfWriter
    out=io.BytesIO();writer=PdfWriter()
    for _ in range(3): writer.add_blank_page(width=200,height=200)
    writer.write(out)
    pages=media.pdf_pages(out.getvalue())
    assert len(pages)==2 and [origin['page'] for _,_,origin in pages]==[1,2]
    assert all(media.signature(data,mime) for data,mime,_ in pages)

def test_video_frames_are_bounded(context,monkeypatch):
    item,add,download=context;add('video');item['route']['allow_external_media']=True
    download.return_value=(b'synthetic video','video/mp4')
    monkeypatch.setattr(media,'video_frames',lambda *a:[(PNG,'image/png',{'second':0}),(PNG,'image/png',{'second':2})])
    assert len(media.collect({},item)[0])==2


def test_video_analysis_limit_is_100_mib_inclusive_and_separate_from_photo_audio():
    assert media.media_byte_limit('video')==100*1024*1024
    media.validate_media_size('video',100*1024*1024)
    with pytest.raises(ValueError,match='video_size_limit'):
        media.validate_media_size('video',100*1024*1024+1)
    assert media.media_byte_limit('image')==20*1024*1024
    assert media.media_byte_limit('audio')==10*1024*1024


def test_video_analysis_duration_accepts_120_and_rejects_longer_or_missing():
    media.validate_video_duration(120)
    with pytest.raises(ValueError,match='video_duration_limit'):
        media.validate_video_duration(120.001)
    with pytest.raises(ValueError,match='video_duration_limit'):
        media.validate_video_duration(None)


def test_terminal_receipt_reuses_exact_scoped_evidence_without_download(context,monkeypatch):
    item,add,download=context;add('video')
    item['tenant_id']='tenant'
    saved={'status':'partial','code':'vision_observation','coverage':{'complete':False,'sampled_frames':[{'second':0}]},
           'result':{'transcript':'Synthetic saved narration','observation':'Synthetic sampled frame'}}
    import tenant_media_evidence as evidence
    monkeypatch.setattr(evidence,'reusable',lambda profile,ref:saved)
    images,results=media.collect(item,item)
    assert images==[] and results[0]['status']=='partial' and results[0]['reused'] is True
    assert 'Synthetic saved narration' in item['messages'][0]['text']
    assert 'partial sampled video observation' in item['messages'][0]['text']
    download.assert_not_called()

def test_video_audio_uses_single_authorized_asr_and_preserves_frames(context,monkeypatch):
    import tenant_connect_voice as voice
    import tenant_chat_ai_provider as provider
    item,add,download=context;add('video');item.update(key='synthetic',credential_source='platform')
    item['route'].update(allow_external_media=True,allow_paid_asr=True)
    download.return_value=(b'synthetic video','video/mp4')
    monkeypatch.setattr(media,'video_frames',lambda *a:[(PNG,'image/png',{'second':0})])
    monkeypatch.setattr(media,'video_audio',lambda *a:(b'RIFFsynthetic','wav','audio/wav'))
    monkeypatch.setattr(service,'check_active',lambda *a:None)
    monkeypatch.setattr(provider,'transcribe_video',lambda *a,**kw:('Synthetic explanation',
        {'total_tokens':25},{'speech_state':'confident','language':'az'}))
    images,results=media.collect({},item)
    assert len(images)==1 and results[0]['audio_status']=='processed'
    assert 'Synthetic explanation' in item['messages'][0]['text']
    assert item['asr_attempted'] and item['asr_usage']['total_tokens']==25

def test_video_mini_transcript_candidate_is_attempted_but_not_promoted_to_case_facts(context,monkeypatch):
    import tenant_knowledge_media_cache as cache
    import tenant_knowledge_candidates_service as service
    item,add,download=context;add('video');item.update(key='synthetic',credential_source='platform')
    item['route'].update(allow_external_media=True,allow_paid_asr=True,video_model='gpt-4.1-2025-04-14')
    item['route']['allow_vision']=True
    download.return_value=(b'synthetic video','video/mp4')
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({
        'gpt-4.1-2025-04-14':{'vision_low':8_896}}))
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({
        'gpt-4.1-2025-04-14':{'input':2_000_000,'output':8_000_000}}))
    monkeypatch.setattr(media,'video_frames',lambda *a:[(PNG,'image/png',{'second':0})])
    monkeypatch.setattr(media,'video_audio',lambda *a:(b'RIFFsynthetic','wav','audio/wav'))
    monkeypatch.setattr(service,'check_active',lambda *a:None)
    monkeypatch.setattr(cache,'observe_once',lambda *a,**kw:('blank label',{},True))
    def uncertain_asr(_profile,selected,model,track,invoke,check_before_ai=None,*,return_diagnostics=False):
        selected['asr_attempted']=True
        text,usage,diagnostics=invoke(selected['key'],model,*track,with_usage=True)
        selected['asr_usage']=usage
        # Return the redacted cached candidate with diagnostics; collect() must
        # preserve it as uncertain evidence without promoting it to case facts.
        details={'speech_state':diagnostics['speech_state'],'speech_reason':diagnostics['reason']}
        return text,usage,True,details
    monkeypatch.setattr(cache,'transcribe_once',uncertain_asr)
    monkeypatch.setattr('tenant_chat_ai_provider.transcribe_video',lambda *a,**kw:(
        'This printer is broken',{'total_tokens':12},
        {'speech_state':'uncertain','reason':'speech_confidence_unavailable','language':''}))
    images,results=media.collect({},item)
    assert results[0]['audio_status']=='uncertain'
    assert results[0]['audio_reason']=='speech_confidence_unavailable'
    assert results[0]['transcript_candidate']=='This printer is broken'
    assert results[0]['transcript_candidate_status']=='uncertain_unverified'
    assert item['asr_attempted'] and item['asr_usage']=={'total_tokens':12}
    assert 'This printer is broken' not in item['messages'][0]['text']
    assert not images and results[0]['status']=='partial'
    assert 'blank label' in item['messages'][0]['text']

def test_uncertain_asr_cache_preserves_redacted_candidate_without_promoting_it(ready,monkeypatch):
    import tenant_knowledge_media_cache as cache
    db,provider,_=ready;monkeypatch.setattr(cache,'_connect',lambda:db)
    item={'tenant':A,'key':'synthetic-key','route':{'language':'az'},'refs':[]}
    calls=[]
    def uncertain(*args,**kwargs):
        calls.append(args)
        return 'Synthetic hallucinated speech',{'total_tokens':17},{
            'speech_state':'uncertain','language':'ja','reason':'expected_language_mismatch'}
    result=cache.transcribe_once(PROFILE,item,'gpt-4o-mini-transcribe',
        (b'RIFF synthetic wav','wav','audio/wav'),uncertain,return_diagnostics=True)
    assert result==('Synthetic hallucinated speech',{'total_tokens':17},True,{
        'speech_state':'uncertain','speech_language':'ja','speech_reason':'expected_language_mismatch'})
    fingerprint=cache.transcription_fingerprint(item,'gpt-4o-mini-transcribe',
        (b'RIFF synthetic wav','wav','audio/wav'),return_diagnostics=True)
    saved=db.execute('SELECT transcript,metadata FROM saas_knowledge_media_cache WHERE tenant_id=%s::uuid AND fingerprint=%s',(A,fingerprint))[0]
    assert saved['transcript']=='Synthetic hallucinated speech'
    assert saved['metadata']['speech_state']=='uncertain'
    def must_not_call(*args,**kwargs):raise AssertionError('completed uncertain receipt must be reused')
    reused=cache.transcribe_once(PROFILE,item,'gpt-4o-mini-transcribe',
        (b'RIFF synthetic wav','wav','audio/wav'),must_not_call,return_diagnostics=True)
    assert reused==('Synthetic hallucinated speech',{},False,{
        'speech_state':'uncertain','speech_language':'ja','speech_reason':'expected_language_mismatch'})
    assert len(calls)==1 and provider.call_count==0

def test_decoder_unavailable_is_explicit(context,monkeypatch):
    item,add,download=context;add('video');item['route']['allow_external_media']=True
    monkeypatch.setattr(media.shutil,'which',lambda *a:None)
    download.return_value=(b'synthetic','video/mp4')
    assert media.collect({},item)[1][0]['status']=='unsupported'

def test_real_synthetic_video_decoder(tmp_path,monkeypatch):
    imageio_ffmpeg=pytest.importorskip('imageio_ffmpeg')
    executable=imageio_ffmpeg.get_ffmpeg_exe()
    target=tmp_path/('ffmpeg.exe' if os.name=='nt' else 'ffmpeg');shutil.copy(executable,target)
    monkeypatch.setenv('PATH',str(tmp_path)+os.pathsep+os.environ['PATH'])
    path=tmp_path/'demo.mp4'
    subprocess.run([str(target),'-v','error','-f','lavfi','-i','color=c=blue:s=160x120:r=10','-f','lavfi','-i','sine=frequency=440','-t','1','-pix_fmt','yuv420p',str(path)],check=True,timeout=10)
    frames=media.video_frames(path.read_bytes(),'video/mp4')
    assert len(frames)==2 and all(media.signature(b,mime) for b,mime,_ in frames)
    track,extension,mime=media.video_audio(path.read_bytes(),'video/mp4')
    assert extension=='wav' and mime=='audio/wav' and track.startswith(b'RIFF')

from test_knowledge_candidates import ready, db, database, A, CA, AGENT, PROFILE
import tenant_knowledge_candidates_service as service


def _authorized_inline_campaign(database, data):
    """Synthetic approved campaign fixture; no real source or provider."""
    import tenant_knowledge_campaign_schema as campaign_schema
    import uuid
    campaign_id=str(uuid.uuid4());request_id=str(uuid.uuid4())
    campaign_schema.create_schema(database)
    route={'provider':'connect','channel_id':CA,'conversation_id':'client','history_mode':'channel',
           'allow_external_media':True,'allow_paid_asr':True,'approved_channel_ids':[CA]}
    database.execute("""INSERT INTO saas_knowledge_campaigns
      (tenant_id,id,created_by,sources,analysis_agent_id,status,approved_at,max_calls,max_tokens,media_policy)
      VALUES(%s::uuid,%s::uuid,11,%s::jsonb,%s::uuid,'running',now(),8,64000,%s::jsonb)""",
      (A,campaign_id,json.dumps([CA]),AGENT,json.dumps({'allow_external_media':True,'allow_paid_asr':True,'max_vision_calls':2,'max_asr_calls':1})))
    database.execute("""INSERT INTO saas_knowledge_campaign_fragments
      (tenant_id,campaign_id,id,source_key,route,status,lease_until)
      VALUES(%s::uuid,%s::uuid,%s::uuid,'synthetic-inline',%s::jsonb,'running',now()+interval '5 minutes')""",
      (A,campaign_id,request_id,json.dumps(route)))
    source=str(uuid.uuid4())
    database.execute("""INSERT INTO saas_crm_messages
      (tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,raw,happened_at)
      VALUES(%s::uuid,%s::uuid,41,'synthetic-inline-image',%s,'incoming','synthetic attachment','image',%s::jsonb,now()+interval '4 seconds')""",
      (A,source,'connect:'+CA,json.dumps({'mediaId':'synthetic-inline','fileLength':100})))
    database.commit()
    return {**data,'request_id':request_id,'allow_external_media':True,'allow_paid_asr':True,
            'approved_channel_ids':[CA]}


def _priced_campaign_env(monkeypatch,database):
    import tenant_ai_spend_budget as budget
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:1_000_000}))
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({'fixture':{'input':1,'output':1}}))
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({
        'fixture':{'vision_low':100},'gpt-4o-mini-transcribe':{'asr':50},
    }))
    monkeypatch.setattr(budget,'_connect',lambda:database)
    monkeypatch.setattr(service,'platform_model',lambda:'fixture')
    database.execute("UPDATE saas_ai_agents SET model='fixture' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT));database.commit()


def test_campaign_inline_visual_success_keeps_unknown_visual_reserve_and_aggregates_tokens(ready,monkeypatch):
    """Authorized legacy inline route crosses the final mocked provider once."""
    database,generation,data=ready
    data=_authorized_inline_campaign(database,data);_priced_campaign_env(monkeypatch,database)
    def collect(_,item,**__):
        item['asr_attempted']=True;item['asr_usage']={'input_tokens':8,'output_tokens':2,'total_tokens':10}
        ref=item['refs'][-1];return ([{'content':b'synthetic','content_type':'image/png','detail':'low','source_ref':item['messages'][-1]['ref']}],
          [{**{key:ref[key] for key in ('id','channel_id','lead_id','role','happened_at')},'kind':'image','status':'pending_processing','code':'awaiting_model'}])
    monkeypatch.setattr(media,'collect',collect)
    response=service.analyze(PROFILE,data)
    assert generation.call_args.kwargs['images'] and response['request']['execution_status']=='completed'
    # Inline supply succeeded, but no reusable visual observation was saved.
    assert response['request']['status']=='partial' and not response['request']['media_coverage']['complete']
    stored=database.execute('SELECT status,media_results FROM saas_knowledge_extractions WHERE id=%s::uuid',(response['request']['id'],))[0]
    assert stored['status']=='completed' and stored['media_results'][0]['code']=='vision_supplied'
    rows={row['kind']:(row['state'],row['actual_microusd']) for row in database.execute('SELECT kind,state,actual_microusd FROM saas_ai_spend_reservations')}
    assert rows['knowledge_asr'][0]=='settled' and rows['knowledge_vision']==('unknown',None)
    assert database.execute('SELECT total_tokens FROM saas_ai_agent_token_usage WHERE run_id=%s::uuid',(response['request']['id'],))[0]['total_tokens']==70


def test_campaign_inline_visual_provider_exception_keeps_visual_reserve_unknown(ready,monkeypatch):
    """An exception after the final image boundary never refunds its reserve."""
    database,generation,data=ready
    data=_authorized_inline_campaign(database,data);_priced_campaign_env(monkeypatch,database)
    def collect(_,item,**__):
        ref=item['refs'][-1];return ([{'content':b'synthetic','content_type':'image/png','detail':'low','source_ref':item['messages'][-1]['ref']}],
          [{**{key:ref[key] for key in ('id','channel_id','lead_id','role','happened_at')},'kind':'image','status':'pending_processing','code':'awaiting_model'}])
    monkeypatch.setattr(media,'collect',collect);generation.side_effect=RuntimeError('synthetic provider failure')
    response=service.analyze(PROFILE,data)
    assert generation.call_count==1 and response['request']['status']=='unknown'
    row=database.execute("SELECT state,actual_microusd FROM saas_ai_spend_reservations WHERE kind='knowledge_vision'")[0]
    assert row=={'state':'unknown','actual_microusd':None}

def test_media_only_source_is_retained_status_persisted_and_replay_idempotent(ready,monkeypatch):
    import uuid
    database,generation,data=ready
    source=str(uuid.uuid4());external='connect:'+str(uuid.uuid4())
    database.execute("INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','','image',now()+interval '4 seconds')",(A,source,external,'connect:'+CA));database.commit()
    # Unsupported source remains visible and must never be invented as analyzed.
    monkeypatch.setattr(media,'load',lambda *a:(_ for _ in ()).throw(TenantLinearError('expired',410)))
    response=service.analyze(PROFILE,data)
    assert response['request']['message_count']==4
    assert response['request']['media_results'][0]['status']=='error'
    assert response['request']['media_results'][0]['code']=='source_reference_unavailable'
    assert response['request']['media_results'][0]['id']==source
    assert any('Attachment status: error; source_reference_unavailable' in m['text'] for m in generation.call_args.args[3])
    assert service.analyze(PROFILE,data)['request']['id']==response['request']['id']
    generation.assert_called_once()


def test_finish_revalidation_rejects_replaced_media_identity(ready):
    """The source fence covers the durable media identity, not only text."""
    import uuid
    database,_,data=ready
    source=str(uuid.uuid4()); external='connect:'+str(uuid.uuid4())
    database.execute("""INSERT INTO saas_crm_messages
      (tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,raw,happened_at)
      VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','same caption','image',%s::jsonb,now()+interval '4 seconds')""",
      (A,source,external,'connect:'+CA,json.dumps({'mediaId':'before','fileLength':10})))
    database.commit()
    item=service.prepare(PROFILE,data)
    assert any(ref.get('id')==source and ref.get('media_version') for ref in item['refs'])
    database.execute("UPDATE saas_crm_messages SET raw=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",
      (json.dumps({'mediaId':'after','fileLength':10}),A,source))
    database.commit()
    import tenant_knowledge_candidates_store as candidate_store
    with database.cursor() as cur:
        with pytest.raises(TenantLinearError,match='Вложение исходного сообщения изменилось'):
            candidate_store.evidence(cur,PROFILE,{**item,'source_refs':item['refs']})

def test_media_consent_changes_fingerprint_without_replaying_original(ready,monkeypatch):
    import uuid
    database,generation,data=ready
    original=service.analyze(PROFILE,data)
    changed=service.analyze(PROFILE,{**data,'request_id':str(uuid.uuid4()),'allow_external_media':True})
    assert changed['request']['id']!=original['request']['id']

def test_asr_permission_reserves_extra_call_and_tokens(ready):
    database,generation,data=ready
    item=service.prepare(PROFILE,{**data,'allow_external_media':True,'allow_paid_asr':True})
    rows=database.execute('SELECT reserved_calls FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,item['id']))
    assert rows[0]['reserved_calls']==2
    assert database.execute('SELECT reserved_tokens FROM saas_ai_agent_token_usage WHERE tenant_id=%s::uuid AND run_id=%s::uuid',(A,item['id']))[0]['reserved_tokens']==service.MAX_TOKENS+8000


def test_inline_visual_without_explicit_vision_fails_closed_under_money_cap(ready,monkeypatch):
    """Inline image inputs are not an unpriced escape hatch when capped."""
    import tenant_ai_spend_budget as budget
    import uuid
    database,generation,data=ready
    source=str(uuid.uuid4())
    database.execute("""INSERT INTO saas_crm_messages
      (tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,happened_at)
      VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','photo attached','image',now()+interval '4 seconds')""",
      (A,source,'media-inline','connect:'+CA))
    database.commit()
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:1_000_000}))
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({'fixture':{'input':1,'output':1}}))
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({'fixture':{'vision_low':100}}))
    monkeypatch.setattr(budget,'_connect',lambda:database)
    monkeypatch.setattr(service,'platform_model',lambda:'fixture')
    # No campaign grants Vision for this source: the extraction must stop
    # before any provider call instead of accepting an unpriced inline image.
    with pytest.raises(TenantLinearError,match='Vision'):
        service.prepare(PROFILE,{**data,'allow_external_media':True})
    generation.assert_not_called()


def test_finish_separates_text_media_unknown_and_media_only_cancel(ready,monkeypatch):
    """Text and fixed media settlements have independent lifecycle receipts."""
    import tenant_ai_spend_budget as budget
    import uuid
    database,_,data=ready
    settled=[];media=[];released=[]
    monkeypatch.setattr(budget,'settle',lambda *args,**kwargs:settled.append((args,kwargs)))
    monkeypatch.setattr(budget,'settle_media',lambda *args,**kwargs:media.append((args,kwargs)))
    monkeypatch.setattr(budget,'release',lambda *args,**kwargs:released.append((args,kwargs)))

    item=service.prepare(PROFILE,data)
    item.update(generation_attempted=True,asr_attempted=True,vision_attempts=0,inline_vision_attempted=True,vision_usage=[],
      media_spend={
        'asr':{'key':'asr-key','operation':'asr','units':1},
        'vision':{'key':'vision-key','operation':'vision_low','units':2},
      })
    service.finish(PROFILE,item,[],{'input_tokens':7,'output_tokens':3,'total_tokens':10},'',True,
                   accounting_usage={'input_tokens':12,'output_tokens':8,'total_tokens':20})
    assert settled and settled[0][0][2]=={'input_tokens':7,'output_tokens':3,'total_tokens':10}
    assert database.execute('SELECT total_tokens FROM saas_ai_agent_token_usage WHERE run_id=%s::uuid',(item['id'],))[0]['total_tokens']==20
    assert released==[]
    assert [(call[0][1],call[1]['unknown']) for call in media]==[('asr-key',True),('vision-key',True)]

    # If only a cached/cancelled media branch ran, no text generation occurred:
    # release its reservation instead of settling it as a zero-token model call.
    item=service.prepare(PROFILE,{**data,'request_id':str(uuid.uuid4()),'allow_external_media':True})
    item.update(generation_attempted=False,asr_attempted=False,vision_attempts=0,
                media_spend={'asr':{'key':'cached-asr','operation':'asr','units':1}})
    service.finish(PROFILE,item,None,None,'',False)
    assert released and released[-1][0][1]=='extraction:'+item['id']
    assert media[-1][0][1]=='cached-asr' and media[-1][1]['attempted_units']==0




@pytest.mark.parametrize('kind',['image','audio','video','document'])
def test_case_byte_snapshot_mismatch_stops_before_provider(context,monkeypatch,kind):
    import tenant_media_evidence as evidence
    import tenant_connect_voice as voice
    item,add,download=context;add(kind)
    item['route'].update(allow_external_media=True,allow_paid_asr=True,case_source_sha256='0'*64)
    monkeypatch.setattr(evidence,'reusable',Mock(side_effect=AssertionError('metadata cache is not byte proof')))
    monkeypatch.setattr(media,'cached_transcript',Mock(side_effect=AssertionError('transcript is not byte proof')))
    monkeypatch.setattr(voice,'transcription_model',lambda item:'test')
    with pytest.raises(media.CaseSourceBytesChanged):media.collect({},item)
    download.assert_awaited_once()


def test_video_audio_unknown_keeps_safe_failure_separate_from_sampled_frames(context,monkeypatch):
    import tenant_knowledge_media_cache as cache
    import tenant_knowledge_candidates_service as service
    item,add,download=context;add('video');item.update(key='synthetic',credential_source='platform')
    item['route'].update(allow_external_media=True,allow_paid_asr=True,allow_vision=True,video_model='gpt-4.1-2025-04-14')
    download.return_value=(b'synthetic video','video/mp4')
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({'gpt-4.1-2025-04-14':{'vision_low':8896}}))
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({'gpt-4.1-2025-04-14':{'input':2000000,'output':8000000}}))
    monkeypatch.setattr(media,'video_frames',lambda *a:[(PNG,'image/png',{'second':0})])
    monkeypatch.setattr(media,'video_audio',lambda *a:(b'RIFFsynthetic','wav','audio/wav'))
    monkeypatch.setattr(service,'check_active',lambda *a:None)
    monkeypatch.setattr(cache,'observe_once',lambda *a,**kw:('sampled frame',{},True))
    def fail(*args,**kwargs):
        error=RuntimeError('SECRET must never be returned');error.media_outcome_unknown=True;error.media_failure_code='provider_http_403_model_access';raise error
    monkeypatch.setattr(cache,'transcribe_once',fail)
    _,results=media.collect({},item)
    assert results[0]['status']=='partial' and results[0]['audio_status']=='unknown'
    assert results[0]['audio_failure_code']=='provider_http_403_model_access'
    assert results[0]['observations'][0]['text']=='sampled frame'
    assert 'SECRET' not in json.dumps(results)
