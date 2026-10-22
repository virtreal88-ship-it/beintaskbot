import json
import importlib.util
import os
import shutil
from pathlib import Path

import pytest
from unittest.mock import patch

from tenant_ai_instructor_contract import ContractError
from tenant_ai_instructor_media import (ElevenLabsAdapter, ElevenLabsBudget, build_script,
                                         generate_script, review_script, srt_text)
from tenant_ai_instructor_browser import _same_origin, _public_addresses


def _events():
    return [
        {'action':'navigate','target':'transfers','verified_description':'Transfer form opened'},
        {'action':'fill_demo','target':'source_warehouse','verified_description':'Main warehouse selected'},
        {'action':'fill_demo','target':'destination_warehouse','verified_description':'Store 1 selected'},
        {'action':'fill_demo','target':'product','verified_description':'Demo shirt selected'},
        {'action':'fill_demo','target':'quantity','verified_description':'Quantity one set'},
        {'action':'click','target':'save_demo_transfer','verified_description':'Synthetic transfer saved','evidence_ref':'screen-1'},
    ]


def test_scenario_subtitles_are_bounded_and_localized():
    result=build_script(_events(),'az')
    assert result['verified'] is False and result['review_status']=='draft'
    assert 'Məhsul' in result['title']
    assert len(result['slides'])==6
    text=srt_text(result['subtitles'])
    assert '00:00:00,000 --> 00:00:04,500' in text
    with pytest.raises(ContractError):build_script([{'action':'send_whatsapp','target':'external'}])


def test_ai_scenario_injection_respects_token_budget_and_validates_output():
    def generator(_prompt,*,max_output_tokens,timeout):
        assert max_output_tokens==1800 and timeout==30
        return json.dumps({'title':'Demo transfer','slides':[{'title':'Open form','text':'Use the demo form.'}],
                           'subtitles':[{'start_ms':0,'end_ms':4000,'text':'Open the form'}]}),{'total_tokens':42}
    result=generate_script(generator,_events(),'en',token_budget_remaining=1800)
    assert result['tokens_used']==42 and result['verified'] is False
    with pytest.raises(ContractError):generate_script(generator,_events(),'en',token_budget_remaining=0)
    def overspend(*_args,**_kwargs):return '{}',{'total_tokens':99}
    with pytest.raises(ContractError):generate_script(overspend,_events(),'en',token_budget_remaining=20)
    scenario={'title':'Demo','slides':[{'title':'Transfer','text':'Only synthetic inventory.'}]}
    def reviewer(_prompt,*,max_output_tokens,timeout):
        assert max_output_tokens==20 and timeout==30
        return json.dumps({'supported':False,'issues':['No saved transfer receipt was observed.']}),{'total_tokens':11}
    review=review_script(reviewer,scenario,_events(),token_budget_remaining=20)
    assert review['review_status']=='employee_review_required' and review['verified'] is False
    assert not review['published'] and review['supported'] is False


def test_elevenlabs_fails_closed_without_confirmed_preflight_and_persistent_reservation():
    calls=[]
    adapter=ElevenLabsAdapter('fake',budget=ElevenLabsBudget(allow_one_call=True),transport=lambda *a,**k:calls.append(a))
    assert adapter.preflight('Salam')['allowed'] is False
    with pytest.raises(RuntimeError):adapter.synthesize('Salam',voice_id='voice')
    assert not calls


def test_elevenlabs_hard_one_dollar_ceiling_and_one_durable_reservation():
    calls=[];reservations=[]
    adapter=ElevenLabsAdapter('fake',budget=ElevenLabsBudget(max_usd=1,max_characters=100,
        confirmed_usd_per_1000_chars=100,confirmation_id='review-1',allow_one_call=True),
        transport=lambda *a,**k:calls.append(a) or b'audio',
        reserve_one_call=lambda ref,cost:reservations.append((ref,cost)) is None)
    assert adapter.preflight('a'*20)['estimated_usd']==2
    with pytest.raises(RuntimeError):adapter.synthesize('a'*20,voice_id='x')
    assert not calls and not reservations

    ok=ElevenLabsAdapter('fake',budget=ElevenLabsBudget(max_usd=1,max_characters=100,
        confirmed_usd_per_1000_chars=10,confirmation_id='review-1',allow_one_call=True),
        transport=lambda *a,**k:calls.append(a) or b'audio',
        reserve_one_call=lambda ref,cost:reservations.append((ref,cost)) or True)
    assert ok.synthesize('a'*20,voice_id='x')==b'audio'
    with pytest.raises(RuntimeError):ok.synthesize('a'*20,voice_id='x')
    assert len(calls)==1 and len(reservations)==1 and reservations[0][1]<=1


def test_browser_origin_and_private_network_checks_fail_closed():
    assert _same_origin('https://demo.example.test/inventory','https://demo.example.test')
    assert not _same_origin('https://evil.example/inventory','https://demo.example.test')
    assert not _same_origin('http://demo.example.test','https://demo.example.test')
    with pytest.raises(ValueError):_public_addresses('localhost')


def test_browser_executor_requires_configured_private_artifact_storage(monkeypatch):
    import asyncio
    from tenant_ai_instructor_browser import PlaywrightDemoExecutor
    from tenant_ai_instructor_contract import DemoConnection,TaskScope,PERMISSIONS
    monkeypatch.delenv('AI_INSTRUCTOR_ARTIFACT_DIR',raising=False)
    monkeypatch.setenv('AI_INSTRUCTOR_ENABLED','1')
    tenant='00000000-0000-0000-0000-000000000051'
    connection=DemoConnection(tenant,'00000000-0000-0000-0000-000000000052','00000000-0000-0000-0000-000000000053',
        'https://dev.akul.az','00000000-0000-0000-0000-000000000054','00000000-0000-0000-0000-000000000055',
        '00000000-0000-0000-0000-000000000056',PERMISSIONS,True)
    scope=TaskScope(tenant,'00000000-0000-0000-0000-000000000057','00000000-0000-0000-0000-000000000058',
        '00000000-0000-0000-0000-000000000059','inventory.warehouse_transfer',False,False,None,None,30,20,12000)
    with pytest.raises(RuntimeError,match='private_artifact_storage_unavailable'):
        asyncio.run(PlaywrightDemoExecutor().open_demo(connection,scope))


def test_generated_video_is_saved_only_as_reviewable_instruction_draft(tmp_path):
    from tenant_ai_instructor_store import save_generated_lesson_draft
    shot=tmp_path/'screen.png';shot.write_bytes(b'fake screenshot bytes')
    calls=[]
    def library(profile,action,data,lesson_id=None):
        calls.append((action,data,lesson_id))
        if action=='upload':return {'asset_id':'00000000-0000-0000-0000-000000000001.jpg','width':640,'height':480,'sha256':'a'*64}
        return {'id':lesson_id,'state':'draft'}
    with patch('tenant_instruction_library.operation',side_effect=library),patch(
            'tenant_ai_instructor_store.attach_recording_to_draft',return_value={'lesson_id':'draft','state':'draft','published':False}):
        result=save_generated_lesson_draft({'tenant_id':'00000000-0000-0000-0000-000000000001','telegram_id':1},
            '00000000-0000-0000-0000-000000000002',
            {'title':'Synthetic transfer','language':'az','slides':[{'title':'Open','text':'Open form'}],
                 'subtitles':[{'text':'Open the form'}]},[shot],'00000000-0000-0000-0000-000000000003')
    assert result['state']=='draft' and result['published'] is False
    assert calls[0][0]=='upload' and calls[1][0]=='save'
    doc=calls[1][1]['doc']
    assert doc['verification']['status']=='not_verified'
    assert doc['slides'][0]['image']['asset_id'].endswith('.jpg')


def test_execution_pipeline_uses_mock_ai_and_saves_only_unpublished_draft(tmp_path,monkeypatch):
    """Exercise the orchestration without external AI, ERP, or publishing effects."""
    import asyncio
    import hashlib
    import tenant_ai_instructor_execution as pipeline
    from tenant_ai_instructor_contract import DemoConnection,TaskScope,PERMISSIONS,ScreenshotEvidence
    tenant='00000000-0000-0000-0000-000000000031'
    task='00000000-0000-0000-0000-000000000032'
    primary='00000000-0000-0000-0000-000000000033'
    expert='00000000-0000-0000-0000-000000000034'
    connection=DemoConnection(tenant,'00000000-0000-0000-0000-000000000035',
        '00000000-0000-0000-0000-000000000036','https://dev.akul.az',
        '00000000-0000-0000-0000-000000000037','00000000-0000-0000-0000-000000000038',
        '00000000-0000-0000-0000-000000000039',PERMISSIONS,True)
    scope=TaskScope(tenant,task,primary,expert,'inventory.warehouse_transfer',False,False,
        None,None,30,20,12000)
    actions=[{'action':'navigate','target':'transfers'},
        {'action':'fill_demo','target':'source_warehouse','demo_value':'fixture.source_warehouse'},
        {'action':'fill_demo','target':'destination_warehouse','demo_value':'fixture.destination_warehouse'},
        {'action':'fill_demo','target':'product','demo_value':'fixture.product'},
        {'action':'fill_demo','target':'quantity','demo_value':'fixture.quantity_one'},
        {'action':'click','target':'save_demo_transfer'}]
    plan_json=json.dumps({'actions':actions})
    slides=[{'title':f'Step {i}','text':'Use only the synthetic demo.'} for i in range(6)]
    cues=[{'start_ms':i*4000,'end_ms':(i+1)*4000,'text':f'Step {i+1}'} for i in range(6)]
    scenario_json=json.dumps({'title':'Synthetic transfer','slides':slides,'subtitles':cues})

    class MockExecutor:
        def __init__(self):self.files={};self.actions=[];self.done=False
        async def open_demo(self,*_args):return 'isolated-session'
        async def observe(self,*_args):return {'title':'SYNTHETIC DEMO','transfer_result':'Synthetic transfer saved' if self.done else ''}
        async def act(self,_session,_scope,action):
            self.actions.append((action.action,action.target))
            if action.target=='save_demo_transfer':self.done=True
            return await self.observe()
        async def capture(self,*_args):
            aid='screen-'+str(len(self.files))
            path=Path(os.environ['AI_INSTRUCTOR_ARTIFACT_DIR'])/f'{aid}.png'
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'synthetic sanitized screenshot')
            self.files[aid]=path
            return ScreenshotEvidence(tenant,task,aid,hashlib.sha256(path.read_bytes()).hexdigest(),
                '00000000-0000-0000-0000-000000000040','synthetic-ui-v1')
        def screenshot_path(self,_session,evidence):return self.files[evidence.artifact_id]
        async def close(self,*_args):
            recording=Path(os.environ['AI_INSTRUCTOR_ARTIFACT_DIR'])/'recording.webm'
            recording.write_bytes(b'synthetic browser video')
            return {'recording_path':str(recording)}

    executor=MockExecutor();monkeypatch.setenv('AI_INSTRUCTOR_ARTIFACT_DIR',str(tmp_path/'private'))
    reservations={};settled=[];events=[];actions_recorded=[];artifacts=[];finished=[]
    def reserve(prompt,cap):
        estimate=len(prompt.encode())+cap+512
        if sum(x['reserved_tokens'] for x in reservations.values())+estimate>12000:return None
        key=str(len(reservations)+1);reservations[key]={'id':key,'reserved_tokens':estimate};return reservations[key]
    def settle(reservation_id,actual,*,uncertain):
        settled.append((reservation_id,actual,uncertain));return True
    def primary_provider(prompt,*,max_output_tokens,timeout):
        if 'Return JSON {actions:' in prompt:return plan_json,{'total_tokens':12}
        return scenario_json,{'total_tokens':18}
    def expert_provider(prompt,*,max_output_tokens,timeout):
        if 'plan_sha256=' in prompt:
            digest=prompt.split('plan_sha256=',1)[1].split(';',1)[0]
            return json.dumps({'approved':True,'plan_sha256':digest}),{'total_tokens':8}
        return json.dumps({'supported':True,'issues':[]}),{'total_tokens':7}
    def add_artifact(_tenant,_task,kind,path,mime,receipt):
        item={'id':f'00000000-0000-0000-0000-{len(artifacts)+41:012d}','kind':kind,'path':str(path),'mime':mime}
        artifacts.append(item);return item
    def save_lesson(_tenant,_task,scenario,screenshots,video_id):
        assert len(screenshots)==6 and scenario['verified'] is False and video_id
        return {'lesson_id':'synthetic-draft','published':False}
    def render(_recording,_subs,out,ffmpeg_path=None):Path(out).write_bytes(b'fake-mp4');return {'mime':'video/mp4'}
    monkeypatch.setattr(pipeline,'render_mp4',render)
    result=asyncio.run(pipeline.execute_job(job={'tenant_id':tenant,'id':task,'primary_agent_id':primary,
        'expert_agent_id':expert,'title':'Synthetic stock transfer','language':'en'},connection=connection,scope=scope,
        credentials={'username':'synthetic','password':'not-real'},executor=executor,
        primary_planner=primary_provider,expert_plan_review=expert_provider,
        primary_script_writer=primary_provider,expert_script_reviewer=expert_provider,
        reserve_tokens=reserve,settle_tokens=settle,record_action=lambda *_args:actions_recorded.append(_args),
        event_sink=lambda *_args:events.append(_args),add_artifact=add_artifact,save_lesson=save_lesson,
        verify_transfer=lambda observation,_job:observation.get('transfer_result')=='Synthetic transfer saved',
        finish_job=lambda *args:finished.append(args)))
    assert result['status']=='completed' and result['employee_review_required'] is True
    assert result['real_erp_pilot']=='unverified' and result['lesson']['published'] is False
    assert len(settled)==4 and len(artifacts)==9 and len(executor.actions)==6
    assert len(actions_recorded)==14 and finished==[(tenant,task,'completed',None)]
    assert all(row[3].get('published') is False for row in events if row[2]=='lesson_saved')


def test_provider_usage_over_reservation_fails_closed_without_retry():
    import asyncio
    from tenant_ai_instructor_execution import _provider_call
    from tenant_ai_instructor_contract import ContractError
    settled=[];calls=[]
    def provider(*_args,**_kwargs):calls.append(1);return '{}',{'total_tokens':11}
    def reserve(_prompt,_cap):return {'id':'reservation-1','reserved_tokens':10}
    def settle(reservation,actual,*,uncertain):settled.append((reservation['id'],actual,uncertain))
    with pytest.raises(ContractError,match='instructor_usage_unknown'):
        asyncio.run(_provider_call(provider,'bounded prompt',100,reserve,settle))
    assert len(calls)==1 and settled==[('reservation-1',None,True)]


def test_demo_credentials_are_encrypted_and_never_returned(monkeypatch):
    from cryptography.fernet import Fernet
    import tenant_ai_instructor_store as store
    tenant='00000000-0000-0000-0000-000000000021';captured={}
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*_args):return False
        def execute(self,sql,params=None):captured['params']=params
        def fetchone(self):return {'id':'00000000-0000-0000-0000-000000000022','label':'demo','origin':'https://dev.akul.az',
            'account_ref':None,'demo_company_id':None,'attestation_ref':None,'restore_receipt_ref':None,
            'enabled':False,'updated_at':'now'}
    class Conn:
        def __enter__(self):return self
        def __exit__(self,*_args):return False
        def cursor(self):return Cursor()
        def commit(self):pass
    cipher=Fernet(Fernet.generate_key())
    monkeypatch.setattr(store,'schema',lambda _conn:None)
    monkeypatch.setattr(store,'_connect',lambda:Conn())
    monkeypatch.setattr(store,'_fernet',lambda:cipher)
    monkeypatch.setattr(store,'_authorize',lambda _cur,_profile:(tenant,7))
    monkeypatch.setattr(store,'_audit',lambda *_args,**_kwargs:None)
    result=store.save_connection({'tenant_id':tenant,'telegram_id':7},{'label':'demo','origin':'https://dev.akul.az',
        'username':'synthetic-user','password':'very-secret-test-only'})
    encrypted=captured['params'][-1]
    assert b'very-secret-test-only' not in encrypted
    decrypted=json.loads(cipher.decrypt(encrypted))
    assert decrypted['password']=='very-secret-test-only'
    assert 'password' not in result and result['enabled'] is False and result['verification_required'] is True
    with pytest.raises(ValueError):store.save_connection({'tenant_id':tenant,'telegram_id':7},
        {'label':'bad','origin':'https://example.com','username':'x','password':'y'})
    with pytest.raises(ValueError,match='verification_proofs_server_only'):
        store.save_connection({'tenant_id':tenant,'telegram_id':7},{'label':'forged','origin':'https://dev.akul.az',
            'username':'x','password':'y','attestation_ref':'00000000-0000-0000-0000-000000000023',
            'account_ref':'00000000-0000-0000-0000-000000000024','demo_company_id':'00000000-0000-0000-0000-000000000025',
            'restore_receipt_ref':'00000000-0000-0000-0000-000000000026'})


def test_postgres_schema_is_additive_and_idempotent(monkeypatch):
    """Only use a dedicated disposable DB explicitly supplied for this test."""
    dsn=os.environ.get('AI_INSTRUCTOR_TEST_DATABASE_URL')
    if not dsn: pytest.skip('AI_INSTRUCTOR_TEST_DATABASE_URL is not configured')
    monkeypatch.setenv('DATABASE_URL',dsn)
    from tenant_platform import _connect
    from tenant_ai_instructor_store import schema
    with _connect() as conn:
        schema(conn);conn.commit()
        schema(conn);conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('saas_ai_instructor_jobs') IS NOT NULL AS jobs, to_regclass('saas_ai_instructor_artifacts') IS NOT NULL AS artifacts")
            row=cur.fetchone()
            assert row['jobs'] and row['artifacts']


@pytest.mark.skipif(importlib.util.find_spec('playwright') is None,
                    reason='Install optional requirements-ai-instructor-test.txt for Chromium smoke')
def test_chromium_synthetic_program_recording(tmp_path):
    """Real browser recording against a local synthetic app; never dev.akul.az."""
    import http.server
    import threading
    from playwright.sync_api import sync_playwright

    fixture=Path(__file__).parent/'fixtures'/'ai_instructor_demo'/'index.html'
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(fixture.parent),**kwargs)
        def log_message(self,*_args):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    video_dir=tmp_path/'video';video_dir.mkdir()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            context=browser.new_context(storage_state=None,accept_downloads=False,
                record_video_dir=str(video_dir),record_video_size={'width':1280,'height':720})
            page=context.new_page();page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(f'http://127.0.0.1:{server.server_port}/') else route.abort())
            page.goto(f'http://127.0.0.1:{server.server_port}/',wait_until='domcontentloaded')
            assert page.locator('[data-demo-banner]').inner_text()=='SYNTHETIC DEMO'
            page.get_by_test_id('source-warehouse').select_option(label='Main warehouse')
            page.get_by_test_id('destination-warehouse').select_option(label='Store 1')
            page.get_by_test_id('product').select_option(label='Demo shirt')
            page.get_by_test_id('quantity').fill('1')
            page.screenshot(path=str(tmp_path/'synthetic-transfer.png'))
            page.get_by_test_id('save-transfer').click()
            assert 'Demo transfer saved' in page.get_by_test_id('transfer-result').inner_text()
            video=page.video
            context.close()
            recording=Path(video.path())
            browser.close()
        assert recording.is_file() and recording.stat().st_size>1000
        assert (tmp_path/'synthetic-transfer.png').is_file()
        if importlib.util.find_spec('imageio_ffmpeg') or shutil.which('ffmpeg'):
            from tenant_ai_instructor_media import render_mp4,srt_text
            captions=tmp_path/'captions.srt'
            captions.write_text(srt_text([{'start_ms':0,'end_ms':2000,'text':'Sintetik demo köçürməsi'}]),encoding='utf-8')
            output=tmp_path/'lesson.mp4'
            rendered=render_mp4(recording,captions,output)
            assert output.is_file() and rendered['mime']=='video/mp4' and output.stat().st_size>1024
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


@pytest.mark.skipif(importlib.util.find_spec('playwright') is None,
                    reason='Install optional requirements-ai-instructor-test.txt for Chromium smoke')
def test_ai_instructor_app_preview_languages_themes_and_mobile():
    """Open the actual /app page against a stubbed authenticated API, no DB/secrets."""
    import http.server
    import threading
    from playwright.sync_api import sync_playwright
    docs=Path(__file__).parents[1]/'docs'
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path=self.path.split('?',1)[0]
            if path=='/app/ai-instructor':file=docs/'tenant-ai-instructor.html';mime='text/html'
            elif path=='/assets/tenant-ai-instructor.js':file=docs/'tenant-ai-instructor.js';mime='text/javascript'
            elif path=='/assets/tenant-ai-instructor.css':file=docs/'tenant-ai-instructor.css';mime='text/css'
            elif path=='/api/platform/me':
                self._json({'member':{'tenant_id':'test-tenant','telegram_id':7}});return
            elif path.startswith('/api/platform/ai/instructor/connections'):
                self._json({'success':True,'result':{'enabled':False,'connections':[]}});return
            elif path.startswith('/api/platform/ai/instructor/jobs'):
                self._json({'success':True,'result':[]});return
            elif path.startswith('/api/platform/ai/instructor'):
                self._json({'success':True,'result':{'enabled':False,'worker':'not_configured',
                    'real_pilot':'unverified','customer_send_allowed':False,'auto_publish':False}});return
            elif path.startswith('/api/platform/ai/agents'):
                self._json({'success':True,'result':{'agents':[]}});return
            else:self.send_error(404);return
            body=file.read_bytes();self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def _json(self,value):
            body=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*_args):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1440,'height':900},color_scheme='light')
            page.goto(f'http://127.0.0.1:{server.server_port}/app/ai-instructor?site_lang=ru',wait_until='networkidle')
            assert page.locator('h1').inner_text()=='Создать урок с AI'
            assert 'Реальный ERP-пилот: не проверен' in page.locator('body').inner_text()
            for lang,title in [('az','AI ilə dərs yaradın'),('en','Create a lesson with AI')]:
                page.goto(f'http://127.0.0.1:{server.server_port}/app/ai-instructor?site_lang={lang}',wait_until='networkidle')
                assert page.locator('h1').inner_text()==title
            page.set_viewport_size({'width':390,'height':844});page.emulate_media(color_scheme='dark')
            page.reload(wait_until='networkidle')
            assert page.locator('h1').is_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
            assert 'customer_send_allowed' not in page.locator('body').inner_text()
            browser.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
