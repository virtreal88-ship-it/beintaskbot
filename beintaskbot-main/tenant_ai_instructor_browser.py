"""Fail-closed Playwright adapter for approved semantic demo actions.

No arbitrary model selectors, JavaScript, URLs, persistent browser state, downloads,
cross-origin requests or client-messaging actions are exposed. A platform egress
firewall is still required in deployed environments; Chromium DNS is pinned here
after validating the resolved public addresses.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import socket
import time
import uuid
import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from tenant_ai_instructor_contract import BrowserAction, DemoConnection, ScreenshotEvidence, TaskScope

_PATHS = {'inventory':'/p=product','transfers':'/p=move','new_transfer':'/newmove'}
_BUTTONS = {'new_transfer':('button','Yerdəyişmə')}
_TEST_IDS = {'source_warehouse':'source-warehouse','destination_warehouse':'destination-warehouse',
             'product':'product','quantity':'quantity','save_demo_transfer':'save-transfer',
             'transfer_result':'transfer-result'}


async def _fill_demo_locator(locator,value):
    """Use the native select API for dropdowns and fill only editable fields."""
    tag=str(await locator.evaluate('(element) => element.tagName')).casefold()
    if tag=='select':
        await locator.select_option(label=value,timeout=5000)
    elif tag in {'input','textarea'}:
        await locator.fill(value,timeout=5000)
    else:
        raise RuntimeError('approved_demo_field_not_editable')


def _allowed_origin(origin):
    configured={x.strip().rstrip('/') for x in str(os.environ.get('AI_INSTRUCTOR_ALLOWED_ORIGINS') or 'https://dev.akul.az').split(',') if x.strip()}
    return origin.rstrip('/') in configured


def _public_addresses(host):
    try:
        rows=socket.getaddrinfo(host,None,type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError('demo_host_unresolvable') from None
    addresses=sorted({row[4][0] for row in rows})
    if not addresses:
        raise ValueError('demo_host_unresolvable')
    for value in addresses:
        ip=ipaddress.ip_address(value)
        if not ip.is_global:
            raise ValueError('demo_host_not_public')
    return addresses


def _same_origin(url, origin):
    wanted=urlsplit(origin);actual=urlsplit(url)
    return (actual.scheme=='https' and actual.hostname==wanted.hostname and
            actual.port in (None,443) and not actual.username and not actual.password)


def _request_method_allowed(permission_level,method,path,*,allow_auth=False,login_path='',write_state=None,transfer_path=''):
    if method in {'GET','HEAD','OPTIONS'}:return True
    if allow_auth and method=='POST' and login_path.startswith('/') and path==login_path:return True
    if (permission_level=='demonstration_operation' and write_state is not None and write_state.get('enabled') and
        not write_state.get('used') and method=='POST' and transfer_path.startswith('/') and path==transfer_path):
        write_state['used']=True
        return True
    return False


@dataclass
class _Session:
    playwright: object
    browser: object
    context: object
    page: object
    started: float
    deadline: float
    actions_left: int
    connection: DemoConnection
    scope: TaskScope
    recording_dir: Path
    timeline_events: list = field(default_factory=list)
    closed: bool=False


class PlaywrightDemoExecutor:
    """Ephemeral isolated Chromium session; available only when explicitly enabled."""
    def __init__(self, artifact_dir=None):
        configured=artifact_dir or os.environ.get('AI_INSTRUCTOR_ARTIFACT_DIR')
        self.artifact_dir=Path(configured).resolve() if configured else None
        self._sessions={}

    async def verify_identity(self, origin, credentials):
        """Authenticate in an unrecorded ephemeral browser and compare trusted ERP identity.

        Company identity is accepted only from a same-origin server endpoint and
        must exactly match an operator-configured UUID. The browser cannot submit
        either value. Isolation attestation is also server configuration.
        """
        identity_path=str(os.environ.get('AI_INSTRUCTOR_IDENTITY_PATH') or '').strip()
        expected_company=str(os.environ.get('AI_INSTRUCTOR_EXPECTED_COMPANY_ID') or '').strip()
        isolation_ref=str(os.environ.get('AI_INSTRUCTOR_ISOLATION_ATTESTATION_REF') or '').strip()
        account_key=str(os.environ.get('AI_INSTRUCTOR_IDENTITY_ACCOUNT_FIELD') or 'account_id').strip()
        company_key=str(os.environ.get('AI_INSTRUCTOR_IDENTITY_COMPANY_FIELD') or 'company_id').strip()
        login_path=str(os.environ.get('AI_INSTRUCTOR_LOGIN_SUBMIT_PATH') or '').strip()
        try:
            from tenant_ai_instructor_contract import identity
            expected_company=identity(expected_company);isolation_ref=identity(isolation_ref)
        except Exception:raise RuntimeError('trusted_demo_identity_configuration_missing') from None
        if (not identity_path.startswith('/') or identity_path.startswith('//') or '?' in identity_path or '#' in identity_path or
            not login_path.startswith('/') or login_path.startswith('//') or '?' in login_path or '#' in login_path):
            raise RuntimeError('trusted_demo_identity_configuration_missing')
        if not _allowed_origin(origin):raise RuntimeError('demo_origin_not_allowlisted')
        host=urlsplit(origin).hostname;addresses=_public_addresses(host)
        playwright=await __import__('playwright.async_api',fromlist=['async_playwright']).async_playwright().start()
        browser=None;context=None
        try:
            browser=await playwright.chromium.launch(headless=True,args=[f'--host-resolver-rules=MAP {host} {addresses[0]},EXCLUDE localhost',
                '--disable-background-networking','--disable-webrtc','--disable-dns-prefetch'])
            context=await browser.new_context(accept_downloads=False,service_workers='block',viewport={'width':1280,'height':720})
            async def guard(route):
                request=route.request
                if not _same_origin(request.url,origin):await route.abort('blockedbyclient');return
                if not _request_method_allowed('research',request.method,urlsplit(request.url).path,
                    allow_auth=True,login_path=login_path):await route.abort('blockedbyclient');return
                await route.continue_()
            await context.route('**/*',guard)
            page=await context.new_page();response=await page.goto(origin,wait_until='domcontentloaded',timeout=15000)
            if response is None or response.status>=400:raise RuntimeError('demo_auth_unavailable')
            username=str((credentials or {}).get('username') or '');password=str((credentials or {}).get('password') or '')
            try:
                user=page.get_by_test_id(os.environ.get('AI_INSTRUCTOR_LOGIN_USERNAME_TEST_ID','login-username'))
                secret=page.get_by_test_id(os.environ.get('AI_INSTRUCTOR_LOGIN_PASSWORD_TEST_ID','login-password'))
                submit=page.get_by_test_id(os.environ.get('AI_INSTRUCTOR_LOGIN_SUBMIT_TEST_ID','login-submit'))
                if await user.count()!=1 or await secret.count()!=1 or await submit.count()!=1:
                    raise RuntimeError('approved_login_map_unavailable')
                if not username or not password:raise RuntimeError('demo_credentials_unavailable')
                await user.fill(username,timeout=5000);await secret.fill(password,timeout=5000);await submit.click(timeout=5000)
                await page.wait_for_load_state('domcontentloaded',timeout=12000)
            finally:
                username='';password=''
            identity_response=await page.request.get(origin+identity_path,timeout=8000)
            if not identity_response.ok:raise RuntimeError('demo_identity_endpoint_unavailable')
            payload=await identity_response.json()
            if not isinstance(payload,dict):raise RuntimeError('demo_identity_response_invalid')
            data=payload.get('result') if isinstance(payload.get('result'),dict) else payload
            actual_company=str(data.get(company_key) or '').strip()
            actual_account=str(data.get(account_key) or '').strip()
            if actual_company!=expected_company or not actual_account:
                raise RuntimeError('demo_company_identity_mismatch')
            account_ref=str(uuid.uuid5(uuid.NAMESPACE_URL,f'beintaskbot:{origin}:{actual_account}'))
            return {'account_ref':account_ref,'demo_company_id':expected_company,'attestation_ref':isolation_ref}
        finally:
            if context:await context.close()
            if browser:await browser.close()
            await playwright.stop()

    async def open_demo(self, connection: DemoConnection, scope: TaskScope, credentials=None):
        connection.validate();scope.validate(connection)
        transfer_path=str(os.environ.get('AI_INSTRUCTOR_TRANSFER_SUBMIT_PATH') or '').strip()
        if not _allowed_origin(connection.origin): raise RuntimeError('demo_origin_not_allowlisted')
        if str(os.environ.get('AI_INSTRUCTOR_ENABLED') or '').casefold() not in {'1','true','yes','on'}:
            raise RuntimeError('instructor_disabled')
        from tenant_ai_instructor_store import _artifact_root
        try:self.artifact_dir=_artifact_root()
        except Exception:raise RuntimeError('private_artifact_storage_unavailable') from None
        if scope.permission_level=='demonstration_operation' and (
            not transfer_path.startswith('/') or transfer_path.startswith('//') or '?' in transfer_path or '#' in transfer_path):
            raise RuntimeError('demo_transfer_write_endpoint_not_configured')
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError('playwright_unavailable') from None
        host=urlsplit(connection.origin).hostname
        addresses=_public_addresses(host)
        rules=f'MAP {host} {addresses[0]},EXCLUDE localhost'
        run_dir=self.artifact_dir/str(connection.tenant_id)/str(scope.task_id)/'recording'
        run_dir.mkdir(parents=True,exist_ok=True)
        playwright=await async_playwright().start()
        browser=None;session_id=None
        try:
            browser=await playwright.chromium.launch(headless=True,args=[f'--host-resolver-rules={rules}',
                '--disable-background-networking','--disable-webrtc','--disable-dns-prefetch',
                '--disable-features=NetworkPrediction,SpeculationRulesPrefetch'])
            async def make_context(storage_state=None,record=False,allow_auth=False,write_state=None):
                options={'storage_state':storage_state,'accept_downloads':False,'service_workers':'block',
                         'viewport':{'width':1280,'height':720}}
                if record:
                    options.update(record_video_dir=str(run_dir),record_video_size={'width':1280,'height':720})
                ctx=await browser.new_context(**options)
                async def guard(route):
                    req=route.request
                    if req.url.startswith(('data:','blob:')) and req.resource_type in {'image','media','font'}:
                        await route.continue_();return
                    if not _same_origin(req.url,connection.origin):
                        await route.abort('blockedbyclient');return
                    login_path=str(os.environ.get('AI_INSTRUCTOR_LOGIN_SUBMIT_PATH') or '').strip()
                    if not _request_method_allowed(scope.permission_level,req.method,urlsplit(req.url).path,
                        allow_auth=allow_auth,login_path=login_path,write_state=write_state,transfer_path=transfer_path):
                        await route.abort('blockedbyclient');return
                    await route.continue_()
                await ctx.route('**/*',guard)
                async def block_websocket(websocket):
                    await websocket.close(code=1008,reason='WebSocket is not part of the approved demo contract')
                await ctx.route_web_socket('**/*',block_websocket)
                return ctx

            # Authenticate in a context that is never recorded. Transfer only
            # the ephemeral in-memory storage state to the recording context;
            # credentials and the login screen cannot enter the lesson video.
            auth_context=await make_context(allow_auth=True)
            page=await auth_context.new_page()
            response=await page.goto(connection.origin,wait_until='domcontentloaded',timeout=10000)
            if response is None or response.status>=400: raise RuntimeError('demo_unavailable')
            if credentials is not None:
                username=str(credentials.get('username') or '')
                password=str(credentials.get('password') or '')
                try:
                    if not username or not password: raise RuntimeError('demo_credentials_unavailable')
                    user_field=page.get_by_test_id(os.environ.get('AI_INSTRUCTOR_LOGIN_USERNAME_TEST_ID','login-username'))
                    pass_field=page.get_by_test_id(os.environ.get('AI_INSTRUCTOR_LOGIN_PASSWORD_TEST_ID','login-password'))
                    submit=page.get_by_test_id(os.environ.get('AI_INSTRUCTOR_LOGIN_SUBMIT_TEST_ID','login-submit'))
                    if await user_field.count()!=1 or await pass_field.count()!=1 or await submit.count()!=1:
                        raise RuntimeError('approved_login_map_unavailable')
                    await user_field.fill(username,timeout=3000);await pass_field.fill(password,timeout=3000)
                    await submit.click(timeout=3000)
                    await page.wait_for_load_state('domcontentloaded',timeout=8000)
                finally:
                    if isinstance(credentials,dict):credentials.clear()
                    del username,password
            proof=await self._verify_page_identity(page,connection)
            if not proof:raise RuntimeError('demo_company_identity_mismatch')
            storage_state=await auth_context.storage_state()
            await auth_context.close()
            write_state={'enabled':False,'used':False}
            context=await make_context(storage_state,record=True,write_state=write_state)
            recording_started=time.monotonic()
            context._ai_instructor_write_state=write_state
            for cookie in storage_state.get('cookies',[]):cookie.clear()
            for entry in storage_state.get('origins',[]):entry.get('localStorage',[]).clear()
            storage_state.clear()
            page=await context.new_page()
            response=await page.goto(connection.origin,wait_until='domcontentloaded',timeout=10000)
            if response is None or response.status>=400:raise RuntimeError('demo_unavailable')
            if not await self._verify_page_identity(page,connection):raise RuntimeError('demo_company_identity_mismatch')
            session_id=str(uuid.uuid4())
            session=_Session(playwright,browser,context,page,recording_started,recording_started+scope.seconds_remaining,
                             min(scope.actions_remaining,20),connection,scope,run_dir)
            self._sessions[session_id]=session
            return session_id
        except Exception:
            if session_id:self._sessions.pop(session_id,None)
            try:
                if 'auth_context' in locals():await auth_context.close()
            except Exception:pass
            try:
                if 'context' in locals():await context.close()
            except Exception:pass
            if browser: await browser.close()
            await playwright.stop()
            raise

    async def _verify_page_identity(self,page,connection):
        identity_path=str(os.environ.get('AI_INSTRUCTOR_IDENTITY_PATH') or '').strip()
        account_key=str(os.environ.get('AI_INSTRUCTOR_IDENTITY_ACCOUNT_FIELD') or 'account_id').strip()
        company_key=str(os.environ.get('AI_INSTRUCTOR_IDENTITY_COMPANY_FIELD') or 'company_id').strip()
        if not identity_path.startswith('/') or identity_path.startswith('//') or '?' in identity_path or '#' in identity_path:return False
        try:
            response=await page.request.get(connection.origin+identity_path,timeout=8000)
            if not response.ok:return False
            payload=await response.json();data=payload.get('result') if isinstance(payload,dict) and isinstance(payload.get('result'),dict) else payload
            if not isinstance(data,dict):return False
            account=str(data.get(account_key) or '').strip();company=str(data.get(company_key) or '').strip()
            if company!=str(connection.demo_company_id):return False
            account_ref=str(uuid.uuid5(uuid.NAMESPACE_URL,f'beintaskbot:{connection.origin}:{account}')) if account else ''
            return bool(account_ref and account_ref==str(connection.account_ref))
        except Exception:return False

    def _session(self, session_ref, scope=None):
        session=self._sessions.get(str(session_ref))
        if not session or session.closed: raise RuntimeError('browser_session_closed')
        session.scope.validate(session.connection)
        if scope and (str(scope.tenant_id)!=str(session.scope.tenant_id) or str(scope.task_id)!=str(session.scope.task_id)):
            raise RuntimeError('browser_scope_mismatch')
        if time.monotonic()>session.deadline or session.actions_left<=0: raise RuntimeError('browser_budget_exhausted')
        return session

    async def observe(self, session_ref: str, scope: TaskScope):
        session=self._session(session_ref,scope)
        session.actions_left-=1
        return await self._observe_page(session)

    async def _observe_page(self, session):
        page=session.page
        result={'title':(await page.title())[:160],'url_path':urlsplit(page.url).path}
        allowed=[]
        for target,test_id in _TEST_IDS.items():
            if session.scope.permission_level=='research' and target=='save_demo_transfer':continue
            locator=page.get_by_test_id(test_id)
            try:
                if await locator.count()==1 and await locator.is_visible():allowed.append(target)
            except Exception:pass
        for target,path in _PATHS.items():
            link=page.locator('a[href="'+path+'"]')
            try:
                if await link.count()==1 and await link.is_visible():allowed.append(target)
            except Exception:pass
        for target,(role,name) in _BUTTONS.items():
            locator=page.get_by_role(role,name=name,exact=True)
            try:
                if await locator.count()==1 and await locator.is_visible():allowed.append(target)
            except Exception:pass
        result['allowed_targets']=sorted(set(allowed))
        status=page.locator('[data-demo-status]')
        if await status.count(): result['status']=(await status.first.inner_text())[:300]
        transfer=page.locator('[data-testid="transfer-result"]')
        result['result_verified']=False
        if await transfer.count() and await transfer.is_visible():
            result['transfer_result']=(await transfer.inner_text())[:300]
            normalized=result['transfer_result'].casefold()
            result['result_verified']='demo transfer saved' in normalized or 'synthetic transfer saved' in normalized
        return result

    async def act(self, session_ref: str, scope: TaskScope, action: BrowserAction):
        session=self._session(session_ref,scope);action.validate(scope,session.connection);session.actions_left-=1
        page=session.page
        started=time.monotonic()
        focus=None
        if action.target in _TEST_IDS:
            try:
                box=await page.get_by_test_id(_TEST_IDS[action.target]).bounding_box()
                if box:focus={key:round(float(box[key]),2) for key in ('x','y','width','height')}
            except Exception:pass
        elif action.target in _BUTTONS:
            try:
                role,name=_BUTTONS[action.target]
                box=await page.get_by_role(role,name=name,exact=True).bounding_box()
                if box:focus={key:round(float(box[key]),2) for key in ('x','y','width','height')}
            except Exception:pass
        if action.action=='open_demo':
            await page.goto(session.connection.origin,wait_until='domcontentloaded',timeout=10000)
        elif action.action=='observe': return await self._observe_page(session)
        elif action.action=='navigate':
            path=_PATHS.get(action.target)
            if not path: raise ValueError('target_forbidden')
            await page.goto(session.connection.origin+path,wait_until='domcontentloaded',timeout=10000)
        elif action.action=='click':
            if action.target in _BUTTONS:
                role,name=_BUTTONS[action.target]
                locator=page.get_by_role(role,name=name,exact=True)
                if await locator.count()!=1 or not await locator.is_visible():
                    raise RuntimeError('approved_button_unavailable')
                await locator.click(timeout=5000)
            else:
                test_id=_TEST_IDS.get(action.target)
                if not test_id: raise ValueError('target_forbidden')
                write_state=getattr(session.context,'_ai_instructor_write_state',None)
                if action.target=='save_demo_transfer' and scope.permission_level=='demonstration_operation':
                    if not write_state or write_state.get('used'):raise RuntimeError('demo_transfer_write_budget_exhausted')
                    write_state['enabled']=True
                try:await page.get_by_test_id(test_id).click(timeout=5000)
                finally:
                    if write_state:write_state['enabled']=False
        elif action.action=='fill_demo':
            test_id=_TEST_IDS.get(action.target)
            values={'fixture.source_warehouse':'Main warehouse','fixture.destination_warehouse':'Store 1',
                    'fixture.product':'Demo shirt','fixture.quantity_one':'1'}
            if not test_id or action.demo_value not in values: raise ValueError('demo_value_forbidden')
            await _fill_demo_locator(page.get_by_test_id(test_id),values[action.demo_value])
        elif action.action=='screenshot': return await self._capture(session)
        else: raise ValueError('action_forbidden')
        session.timeline_events.append({'action':action.action,'target':action.target or '',
            'at_ms':max(0,round((started-session.started)*1000)),
            'viewport_width':1280,'viewport_height':720,'focus':focus})
        # The action itself was counted above. Observing the resulting page is
        # part of that action and must not consume a second action-budget unit.
        return await self._observe_page(session)

    async def capture(self, session_ref: str, scope: TaskScope):
        session=self._session(session_ref,scope);session.actions_left-=1
        return await self._capture(session)

    async def _capture(self, session):
        scope=session.scope
        # Mask credential and explicitly sensitive controls before any screenshot.
        target=session.page.locator('input, textarea, [data-sensitive="true"]')
        output=session.recording_dir/'screen-'+str(uuid.uuid4())+'.png'
        await session.page.screenshot(path=str(output),full_page=False,animations='disabled',mask=[target])
        body=output.read_bytes();digest=hashlib.sha256(body).hexdigest();receipt=str(uuid.uuid4())
        return ScreenshotEvidence(str(scope.tenant_id),str(scope.task_id),output.stem,digest,receipt,
                                  (await session.page.locator('html').get_attribute('data-ui-version')) or 'unreported').validate(scope)

    def screenshot_path(self,session_ref,evidence):
        session=self._session(session_ref)
        path=(session.recording_dir/(str(evidence.artifact_id)+'.png')).resolve()
        try:path.relative_to(session.recording_dir.resolve())
        except ValueError:raise RuntimeError('artifact_scope_mismatch') from None
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=evidence.sha256:
            raise RuntimeError('screenshot_evidence_invalid')
        return path

    async def close(self, session_ref: str):
        session=self._sessions.pop(str(session_ref),None)
        if not session or session.closed:return None
        session.closed=True
        # Close page/context before browser so the Playwright video artifact is finalized.
        video=session.page.video
        recording=None
        try:
            await session.context.close()
            if video:recording=await video.path()
        finally:
            try: await session.browser.close()
            finally: await session.playwright.stop()
        return {'recording_dir':str(session.recording_dir),'recording_path':str(recording) if recording else None,
                'timeline_events':list(session.timeline_events)}
