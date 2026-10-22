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
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from tenant_ai_instructor_contract import BrowserAction, DemoConnection, ScreenshotEvidence, TaskScope

_PATHS = {'inventory':'/inventory','transfers':'/inventory/transfers','new_transfer':'/inventory/transfers/new'}
_TEST_IDS = {'source_warehouse':'source-warehouse','destination_warehouse':'destination-warehouse',
             'product':'product','quantity':'quantity','save_demo_transfer':'save-transfer',
             'transfer_result':'transfer-result'}


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
    closed: bool=False


class PlaywrightDemoExecutor:
    """Ephemeral isolated Chromium session; available only when explicitly enabled."""
    def __init__(self, artifact_dir=None):
        configured=artifact_dir or os.environ.get('AI_INSTRUCTOR_ARTIFACT_DIR')
        self.artifact_dir=Path(configured).resolve() if configured else None
        self._sessions={}

    async def open_demo(self, connection: DemoConnection, scope: TaskScope, credentials=None):
        connection.validate();scope.validate(connection)
        if not _allowed_origin(connection.origin): raise RuntimeError('demo_origin_not_allowlisted')
        if str(os.environ.get('AI_INSTRUCTOR_ENABLED') or '').casefold() not in {'1','true','yes','on'}:
            raise RuntimeError('instructor_disabled')
        from tenant_ai_instructor_store import _artifact_root
        try:self.artifact_dir=_artifact_root()
        except Exception:raise RuntimeError('private_artifact_storage_unavailable') from None
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
            async def make_context(storage_state=None,record=False):
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
                    await route.continue_()
                await ctx.route('**/*',guard)
                async def block_websocket(websocket):
                    await websocket.close(code=1008,reason='WebSocket is not part of the approved demo contract')
                await ctx.route_web_socket('**/*',block_websocket)
                return ctx

            # Authenticate in a context that is never recorded. Transfer only
            # the ephemeral in-memory storage state to the recording context;
            # credentials and the login screen cannot enter the lesson video.
            auth_context=await make_context()
            page=await auth_context.new_page()
            response=await page.goto(connection.origin,wait_until='domcontentloaded',timeout=10000)
            if response is None or response.status>=400: raise RuntimeError('demo_unavailable')
            if credentials is not None:
                username=str(credentials.get('username') or '')
                password=str(credentials.get('password') or '')
                try:
                    if not username or not password: raise RuntimeError('demo_credentials_unavailable')
                    user_field=page.get_by_test_id('login-username');pass_field=page.get_by_test_id('login-password')
                    submit=page.get_by_test_id('login-submit')
                    if await user_field.count()!=1 or await pass_field.count()!=1 or await submit.count()!=1:
                        raise RuntimeError('approved_login_map_unavailable')
                    await user_field.fill(username,timeout=3000);await pass_field.fill(password,timeout=3000)
                    await submit.click(timeout=3000)
                    await page.locator('[data-demo-banner]').wait_for(state='visible',timeout=5000)
                finally:
                    if isinstance(credentials,dict):credentials.clear()
                    del username,password
            banner=page.locator('[data-demo-banner]')
            if await banner.count()!=1 or (await banner.inner_text()).strip()!='SYNTHETIC DEMO':
                raise RuntimeError('demo_identity_not_verified')
            storage_state=await auth_context.storage_state()
            await auth_context.close()
            context=await make_context(storage_state,record=True)
            for cookie in storage_state.get('cookies',[]):cookie.clear()
            for entry in storage_state.get('origins',[]):entry.get('localStorage',[]).clear()
            storage_state.clear()
            page=await context.new_page()
            response=await page.goto(connection.origin,wait_until='domcontentloaded',timeout=10000)
            if response is None or response.status>=400:raise RuntimeError('demo_unavailable')
            banner=page.locator('[data-demo-banner]')
            if await banner.count()!=1 or (await banner.inner_text()).strip()!='SYNTHETIC DEMO':
                raise RuntimeError('demo_identity_not_verified')
            session_id=str(uuid.uuid4())
            session=_Session(playwright,browser,context,page,time.monotonic(),time.monotonic()+scope.seconds_remaining,
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
        status=page.locator('[data-demo-status]')
        if await status.count(): result['status']=(await status.first.inner_text())[:300]
        transfer=page.locator('[data-testid="transfer-result"]')
        if await transfer.count() and await transfer.is_visible(): result['transfer_result']=(await transfer.inner_text())[:300]
        return result

    async def act(self, session_ref: str, scope: TaskScope, action: BrowserAction):
        session=self._session(session_ref,scope);action.validate(scope,session.connection);session.actions_left-=1
        page=session.page
        if action.action=='open_demo':
            await page.goto(session.connection.origin,wait_until='domcontentloaded',timeout=10000)
        elif action.action=='observe': return await self._observe_page(session)
        elif action.action=='navigate':
            path=_PATHS.get(action.target)
            if not path: raise ValueError('target_forbidden')
            await page.goto(session.connection.origin+path,wait_until='domcontentloaded',timeout=10000)
        elif action.action=='click':
            test_id=_TEST_IDS.get(action.target)
            if not test_id: raise ValueError('target_forbidden')
            await page.get_by_test_id(test_id).click(timeout=5000)
        elif action.action=='fill_demo':
            test_id=_TEST_IDS.get(action.target)
            values={'fixture.source_warehouse':'Main warehouse','fixture.destination_warehouse':'Store 1',
                    'fixture.product':'Demo shirt','fixture.quantity_one':'1'}
            if not test_id or action.demo_value not in values: raise ValueError('demo_value_forbidden')
            await page.get_by_test_id(test_id).fill(values[action.demo_value],timeout=5000)
        elif action.action=='screenshot': return await self._capture(session)
        else: raise ValueError('action_forbidden')
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
        return {'recording_dir':str(session.recording_dir),'recording_path':str(recording) if recording else None}

