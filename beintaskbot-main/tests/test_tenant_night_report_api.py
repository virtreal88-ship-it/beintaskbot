"""Narrow authenticated GET dispatch; reads use only the verified selected tenant."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from aiohttp import web
from tenant_linear_policy import TenantLinearError

ROOT=Path(__file__).resolve().parents[1]
PROFILE={'tenant_id':'company-a','telegram_id':11}

class InlineAsyncio:
    @staticmethod
    async def to_thread(fn,*args,**kwargs):return fn(*args,**kwargs)

def run(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    raise AssertionError('Unexpected asynchronous I/O')

def make(resource='report',profile=PROFILE,reader=None):
    fn=next(n for n in ast.parse((ROOT/'tenant_night_report_api.py').read_text(encoding='utf-8-sig')).body if isinstance(n,ast.FunctionDef) and n.name=='handler')
    report=reader or Mock(return_value={'metrics':{'dialogs':2}});detail=reader or Mock(return_value={'messages':[]})
    namespace={'asyncio':InlineAsyncio,'web':web,'TenantLinearError':TenantLinearError,'report':report,'detail':detail}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'night-report-api','exec'),namespace)
    return namespace['handler'](Mock(return_value=profile),Mock(),resource),report,detail

def request(query=None,ticket='ticket-id'):return SimpleNamespace(query=query or {},match_info={'ticket_id':ticket})


def test_report_reads_only_session_tenant_and_forwards_valid_filters():
    handle,read,_=make()
    response=run(handle(request({'date':'2026-10-15','timezone':'Asia/Baku','start':'19:00','end':'09:00','tenant_id':'hostile-tenant','offset':'50'})))
    assert response.status==200 and response.headers['Cache-Control']=='no-store'
    read.assert_called_once_with(PROFILE,'2026-10-15','Asia/Baku','19:00','09:00','50')

@pytest.mark.parametrize('query',[{'expected_tenant_id':'company-b'},{'expected_user_id':'22'}])
def test_stale_identity_is_rejected_before_any_read(query):
    handle,read,detail=make()
    assert run(handle(request(query))).status==409
    read.assert_not_called();detail.assert_not_called()


def test_missing_session_is_denied():
    handle,read,_=make(profile=None)
    assert run(handle(request())).status==403
    read.assert_not_called()


@pytest.mark.parametrize('resource',['dialog','ticket'])
def test_history_and_ticket_dispatch_remain_read_only(resource):
    handle,_,read=make(resource)
    assert run(handle(request({'channel_id':'channel','conversation_id':'same-jid','before':'cursor'}))).status==200
    read.assert_called_once_with(PROFILE,ticket_id='ticket-id',channel_id='channel',conversation_id='same-jid',before='cursor')


@pytest.mark.parametrize('error,status',[(TenantLinearError('Denied',403),403),(TenantLinearError('Missing',404),404),(ValueError('not-safe'),400),(RuntimeError('secret-stack'),503)])
def test_safe_errors(error,status):
    handle,_,_=make(reader=Mock(side_effect=error))
    response=run(handle(request()))
    assert response.status==status and 'secret-stack' not in response.text and 'not-safe' not in response.text
    assert json.loads(response.text)['success'] is False


def test_report_ui_is_app_only_and_preserves_pilot():
    import subprocess
    changed=subprocess.check_output(['git','diff','--name-only','77d5312a','--','docs/index.html','docs/tenant-webapp.html'],cwd=ROOT,text=True)
    assert changed==''
    app=(ROOT/'docs/tenant-app.html').read_text(encoding='utf-8-sig')
    assert app.count('id="nightReportNav"') == 1
    assert 'href="/app/night-report"' in app
    assets=(ROOT/'tenant_web_assets.py').read_text(encoding='utf-8')
    assert "('/app/night-report', 'tenant-night-report.html'" in assets
    script=(ROOT/'docs/tenant-night-report.js').read_text(encoding='utf-8-sig')
    assert '/webapp' not in script
    assert "method:'POST'" not in script
