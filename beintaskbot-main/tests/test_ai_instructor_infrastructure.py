import json
import importlib.util
import os
import shutil
from pathlib import Path

import pytest
from unittest.mock import patch

from tenant_ai_instructor_contract import ContractError
from tenant_ai_instructor_media import (build_script, generate_script, review_script, srt_text)
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


def test_edited_subtitle_file_is_bounded_plain_text_and_roundtrips():
    from tenant_ai_instructor_media import srt_text
    from tenant_ai_instructor_store import _parse_srt
    cues=[{'start_ms':0,'end_ms':2500,'text':'Məhsulu yoxlayın.'},
          {'start_ms':2500,'end_ms':5000,'text':'Anbarı seçin.'}]
    assert _parse_srt(srt_text(cues))==cues
    with pytest.raises(ValueError):_parse_srt('1\n00:00:00,000 --> 00:00:02,000\n<script>alert(1)</script>')


def test_ai_scenario_injection_respects_token_budget_and_validates_output():
    def generator(_prompt,*,max_output_tokens,timeout):
        assert max_output_tokens==700 and timeout==30
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


def test_browser_origin_and_private_network_checks_fail_closed():
    assert _same_origin('https://demo.example.test/inventory','https://demo.example.test')
    assert not _same_origin('https://evil.example/inventory','https://demo.example.test')
    assert not _same_origin('http://demo.example.test','https://demo.example.test')
    with pytest.raises(ValueError):_public_addresses('localhost')


def test_connection_status_reports_saved_credentials_without_exposing_them():
    from tenant_ai_instructor_store import _connection_dict
    row={'id':'00000000-0000-0000-0000-000000000001','label':'ERP demo',
         'origin':'https://dev.akul.az','enabled':False,'updated_at':None,
         'account_ref':None,'demo_company_id':None,'attestation_ref':None,
         'restore_receipt_ref':None,'credentials':b'encrypted-secret'}
    saved=_connection_dict(row)
    assert saved['label']=='ERP demo' and saved['origin']=='https://dev.akul.az'
    assert saved['research_ready'] is False and saved['operation_ready'] is False
    assert 'credentials' not in saved and 'password' not in saved
    row.update(enabled=True,account_ref='a',demo_company_id='c',attestation_ref='i')
    verified=_connection_dict(row)
    assert verified['research_ready'] is True and verified['operation_ready'] is False
    assert verified['operation_blocker']=='fixture_restore_unverified'


def test_research_permission_is_read_only_and_does_not_require_restore_receipt():
    import uuid
    from tenant_ai_instructor_contract import (DemoConnection,TaskScope,RESEARCH_PERMISSIONS,
        BrowserAction,ContractError)
    tenant=str(uuid.uuid4());task=str(uuid.uuid4());primary=str(uuid.uuid4());expert=str(uuid.uuid4())
    connection=DemoConnection(tenant,str(uuid.uuid4()),str(uuid.uuid4()),'https://dev.akul.az',
        str(uuid.uuid4()),str(uuid.uuid4()),None,RESEARCH_PERMISSIONS,True)
    scope=TaskScope(tenant,task,primary,expert,'inventory.warehouse_transfer',False,False,
        None,None,30,20,12000,'research')
    connection.validate();scope.validate(connection)
    BrowserAction('navigate','transfers').validate(scope,connection)
    BrowserAction('fill_demo','quantity','fixture.quantity_one').validate(scope,connection)
    with pytest.raises(ContractError,match='research_write_forbidden'):
        BrowserAction('click','save_demo_transfer').validate(scope,connection)


def test_demonstration_operation_requires_restore_receipt():
    import uuid
    from tenant_ai_instructor_contract import DemoConnection,TaskScope,RESEARCH_PERMISSIONS,ContractError
    tenant=str(uuid.uuid4());connection=DemoConnection(tenant,str(uuid.uuid4()),str(uuid.uuid4()),
        'https://dev.akul.az',str(uuid.uuid4()),str(uuid.uuid4()),None,RESEARCH_PERMISSIONS,True)
    scope=TaskScope(tenant,str(uuid.uuid4()),str(uuid.uuid4()),str(uuid.uuid4()),
        'inventory.warehouse_transfer',False,False,None,None,30,20,12000,'demonstration_operation')
    with pytest.raises(ContractError,match='fixture_restore_required'):scope.validate(connection)


def test_research_prompt_explicitly_forbids_persistent_actions():
    from tenant_ai_instructor_execution import _next_action_prompt
    prompt=_next_action_prompt({'permission_level':'research','title':'Explore transfer form'},'az',
        {'allowed_targets':['transfers']},[],1)
    assert 'strictly read-only' in prompt
    assert 'do not save, submit, confirm, delete' in prompt


def test_worker_scope_keeps_research_permission_level():
    import uuid
    from tenant_ai_instructor_worker import _scope
    tenant=str(uuid.uuid4())
    scope=_scope({'tenant_id':tenant,'id':str(uuid.uuid4()),'primary_agent_id':str(uuid.uuid4()),
        'expert_agent_id':str(uuid.uuid4()),'permission_level':'research'}, {})
    assert scope.permission_level=='research'


def test_browser_network_fence_blocks_research_writes_and_caps_operation_to_one_request():
    from tenant_ai_instructor_browser import _request_method_allowed
    state={'enabled':False,'used':False}
    assert _request_method_allowed('research','GET','/inventory')
    assert _request_method_allowed('research','POST','/login',allow_auth=True,login_path='/login')
    assert not _request_method_allowed('research','POST','/inventory/transfers',write_state=state,
        transfer_path='/inventory/transfers')
    assert not _request_method_allowed('demonstration_operation','POST','/inventory/transfers',
        write_state=state,transfer_path='/inventory/transfers')
    state['enabled']=True
    assert _request_method_allowed('demonstration_operation','POST','/inventory/transfers',
        write_state=state,transfer_path='/inventory/transfers')
    assert state['used'] is True
    assert not _request_method_allowed('demonstration_operation','POST','/inventory/transfers',
        write_state=state,transfer_path='/inventory/transfers')
    assert not _request_method_allowed('demonstration_operation','DELETE','/inventory/transfers',
        write_state=state,transfer_path='/inventory/transfers')


def test_local_preview_serves_instructor_route_and_assets():
    import threading,urllib.request,json
    from urllib.error import HTTPError
    from http.server import ThreadingHTTPServer
    from ui.preview import Preview,POSTS
    server=ThreadingHTTPServer(('127.0.0.1',0),Preview)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        base=f'http://127.0.0.1:{server.server_port}'
        page=urllib.request.urlopen(base+'/app/ai-instructor').read().decode()
        assert 'aiInstructor' in page and 'Preview file missing' not in page
        script=urllib.request.urlopen(base+'/assets/tenant-ai-instructor.js').read().decode()
        assert 'permission_level' in script and 'Verify connection' in script
        assert 'Данные доступа сохранены' in script
        status=json.loads(urllib.request.urlopen(base+'/api/platform/ai/instructor').read())
        assert status['result']['real_pilot']=='unverified'
        assert status['result']['blocked_actions']
        before=len(POSTS)
        request=urllib.request.Request(base+'/api/platform/ai/instructor/connections',
            data=json.dumps({'username':'test','password':'must-not-be-retained'}).encode(),
            headers={'Content-Type':'application/json'},method='POST')
        with pytest.raises(HTTPError) as blocked:urllib.request.urlopen(request)
        assert blocked.value.code==403 and len(POSTS)==before
        assert all('must-not-be-retained' not in str(row) for row in POSTS)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


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
    slides=[{'title':f'Step {i}','text':'Use only the synthetic demo.'} for i in range(6)]
    cues=[{'start_ms':i*4000,'end_ms':(i+1)*4000,'text':f'Step {i+1}'} for i in range(6)]
    scenario_json=json.dumps({'title':'Synthetic transfer','slides':slides,'subtitles':cues})

    class MockExecutor:
        def __init__(self):self.files={};self.actions=[];self.stage='start'
        async def open_demo(self,*_args):return 'isolated-session'
        async def observe(self,*_args):
            targets={'start':['transfers'],'source':['source_warehouse'],'destination':['destination_warehouse'],
                'product':['product'],'quantity':['quantity'],'save':['save_demo_transfer'],'saved':[]}[self.stage]
            return {'title':'SYNTHETIC DEMO','allowed_targets':targets,
                'transfer_result':'Synthetic transfer saved' if self.stage=='saved' else '',
                'result_verified':self.stage=='saved'}
        async def act(self,_session,_scope,action):
            self.actions.append((action.action,action.target))
            transitions={'transfers':'source','source_warehouse':'destination','destination_warehouse':'product',
                'product':'quantity','quantity':'save','save_demo_transfer':'saved'}
            self.stage=transitions[action.target]
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
    reservations={};settled=[];events=[];actions_recorded=[];artifacts=[];finished=[];planner_steps=[]
    def reserve(prompt,cap,model=None):
        assert model is None or isinstance(model,str)
        estimate=(len(prompt.encode())+2)//3+cap+256
        if sum(x['reserved_tokens'] for x in reservations.values())+estimate>12000:return None
        key=str(len(reservations)+1);reservations[key]={'id':key,'reserved_tokens':estimate};return reservations[key]
    def settle(reservation_id,actual,*,uncertain):
        settled.append((reservation_id,actual,uncertain));return True
    def primary_provider(prompt,*,max_output_tokens,timeout):
        if 'choose ONE next semantic browser action' in prompt:
            planner_steps.append(prompt)
            observation=json.loads(prompt.split('; observation=',1)[1].split('; completed_actions=',1)[0])
            available=observation['allowed_targets']
            decisions={'transfers':{'action':'navigate','target':'transfers'},
                'source_warehouse':{'action':'fill_demo','target':'source_warehouse','demo_value':'fixture.source_warehouse'},
                'destination_warehouse':{'action':'fill_demo','target':'destination_warehouse','demo_value':'fixture.destination_warehouse'},
                'product':{'action':'fill_demo','target':'product','demo_value':'fixture.product'},
                'quantity':{'action':'fill_demo','target':'quantity','demo_value':'fixture.quantity_one'},
                'save_demo_transfer':{'action':'click','target':'save_demo_transfer'}}
            assert len(available)==1
            return json.dumps(decisions[available[0]]),{'total_tokens':12}
        return scenario_json,{'total_tokens':18}
    def expert_provider(prompt,*,max_output_tokens,timeout):
        if 'task_sha256=' in prompt:
            digest=prompt.split('task_sha256=',1)[1].split(';',1)[0]
            return json.dumps({'approved':True,'task_sha256':digest}),{'total_tokens':8}
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
        verify_transfer=lambda observation,_job:observation.get('result_verified') is True,
        finish_job=lambda *args:finished.append(args)))
    assert result['status']=='completed' and result['employee_review_required'] is True
    assert result['real_erp_pilot']=='unverified' and result['lesson']['published'] is False
    assert len(settled)==9 and len(artifacts)==10 and len(executor.actions)==6
    observed=[json.loads(prompt.split('; observation=',1)[1].split('; completed_actions=',1)[0])
              for prompt in planner_steps]
    assert len(planner_steps)==6 and [x['allowed_targets'][0] for x in observed]==[
        'transfers','source_warehouse','destination_warehouse','product','quantity','save_demo_transfer']
    assert len(actions_recorded)==13 and finished==[(tenant,task,'completed',None)]
    assert all(row[3].get('published') is False for row in events if row[2]=='lesson_saved')


def test_provider_usage_over_reservation_fails_closed_without_retry():
    import asyncio
    from tenant_ai_instructor_execution import _provider_call
    from tenant_ai_instructor_contract import ContractError
    settled=[];calls=[]
    def provider(*_args,**_kwargs):calls.append(1);return '{}',{'total_tokens':11}
    def reserve(_prompt,_cap,_model=None):return {'id':'reservation-1','reserved_tokens':10}
    def settle(reservation,actual,*,uncertain):settled.append((reservation['id'],actual,uncertain))
    with pytest.raises(ContractError,match='instructor_usage_unknown'):
        asyncio.run(_provider_call(provider,'bounded prompt',100,reserve,settle))
    assert len(calls)==1 and settled==[('reservation-1',None,True)]


def test_reservation_callback_typeerror_is_not_retried():
    import asyncio
    from tenant_ai_instructor_execution import _provider_call
    reservations=[];provider_calls=[]
    def reserve(*_args):
        reservations.append(1)
        raise TypeError('reservation storage failed after write')
    def provider(*_args,**_kwargs):provider_calls.append(1);return '{}',{'total_tokens':1}
    with pytest.raises(TypeError,match='reservation storage failed'):
        asyncio.run(_provider_call(provider,'bounded prompt',100,reserve,lambda *_args,**_kwargs:None))
    assert len(reservations)==1 and provider_calls==[]


def test_token_budget_uses_selected_model_and_fails_closed(monkeypatch):
    import sys
    from types import SimpleNamespace
    import tenant_ai_instructor_store as store
    from tenant_linear_policy import TenantLinearError
    calls=[]
    class Encoding:
        def encode(self,text):return list(text.split())
    def encoding_for_model(model):
        calls.append(model)
        if model!='demo-model':raise KeyError(model)
        return Encoding()
    monkeypatch.setitem(sys.modules,'tiktoken',SimpleNamespace(encoding_for_model=encoding_for_model))
    assert store.estimate_provider_tokens('one two','demo-model')==258
    assert calls==['demo-model']
    with pytest.raises(TenantLinearError,match='requires a tokenizer'):
        store.estimate_provider_tokens('prompt','unknown-model')


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
    assert result['verification_blockers']==['account_identity_unverified','demo_company_unverified',
        'isolation_scope_unverified','connection_not_activated']
    assert 'operation_blocker' not in result and result['research_ready'] is False
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
            cur.execute("""SELECT to_regclass('saas_ai_instructor_jobs') IS NOT NULL AS jobs,
                to_regclass('saas_ai_instructor_artifacts') IS NOT NULL AS artifacts,
                to_regclass('saas_ai_instructor_restore_claims') IS NOT NULL AS restore_claims,
                EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='saas_ai_instructor_jobs'
                  AND column_name='permission_level') AS permission_level""")
            row=cur.fetchone()
            assert row['jobs'] and row['artifacts'] and row['restore_claims'] and row['permission_level']


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
                self._json({'success':True,'result':{'enabled':False,'connections':[{'id':'synthetic-connection',
                    'label':'Synthetic demo','origin':'https://dev.akul.az','enabled':False,'verification_required':True,
                    'verification_blockers':['demo_company_unverified','isolation_scope_unverified',
                        'fixture_restore_unverified','connection_not_activated']}]}});return
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
            assert 'Просмотр разрешается только после серверной проверки' in page.locator('body').inner_text()
            assert 'идентификатор компании не подтверждён доверенным ERP endpoint' in page.locator('body').inner_text()
            assert 'для операции записи не подтверждено восстановление тестовых остатков' in page.locator('body').inner_text()
            assert 'Исследование: просмотр, навигация, несохраняемые формы' in page.locator('body').inner_text()
            for lang,title in [('az','AI ilə dərs yaradın'),('en','Create a lesson with AI')]:
                page.goto(f'http://127.0.0.1:{server.server_port}/app/ai-instructor?site_lang={lang}',wait_until='networkidle')
                assert page.locator('h1').inner_text()==title
            for width,height in [(1440,900),(1920,1080),(390,844)]:
                page.set_viewport_size({'width':width,'height':height})
                for theme in ('light','dark'):
                    page.emulate_media(color_scheme=theme)
                    page.goto(f'http://127.0.0.1:{server.server_port}/app/ai-instructor?site_lang=az',wait_until='networkidle')
                    assert page.locator('h1').is_visible()
                    assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth'),(width,theme)
            assert 'customer_send_allowed' not in page.locator('body').inner_text()
            browser.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
