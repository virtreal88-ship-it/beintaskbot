"""Contract validation only: no demo UI, screenshots or browser execution are mocked."""
from dataclasses import replace
import pytest
from tenant_ai_instructor_contract import (DemoConnection,TaskScope,BrowserAction,ScreenshotEvidence,
    ContractError,OPERATION,PERMISSIONS,availability)

IDS=[f'00000000-0000-0000-0000-{i:012d}' for i in range(1,12)]

@pytest.fixture
def connection():
    # Synthetic shapes, not a claimed existing application or account.
    return DemoConnection(IDS[0],IDS[1],IDS[2],'https://demo.example.test',IDS[3],IDS[4],IDS[5],PERMISSIONS,True)

@pytest.fixture
def scope():
    return TaskScope(IDS[0],IDS[6],IDS[7],IDS[8],OPERATION,False,False,4,4,20,16,12000)


def test_only_connection_contract_available():
    value=availability()
    assert value['status']=='integration_unavailable' and not value['verified']
    assert not value['screenshots'] and not value['customer_send_allowed']


def test_scope_and_approved_semantic_actions(connection,scope):
    assert connection.validate() is connection
    assert scope.validate(connection) is scope
    BrowserAction('open_demo').validate(scope,connection)
    BrowserAction('navigate','transfers').validate(scope,connection)
    BrowserAction('click','new_transfer').validate(scope,connection)
    BrowserAction('fill_demo','quantity','fixture.quantity_one').validate(scope,connection)
    BrowserAction('screenshot').validate(scope,connection)


@pytest.mark.parametrize('origin',['http://demo.example.test','https://user:password@demo.example.test',
    'https://127.0.0.1','https://localhost','https://demo.example.test/?token=secret',
    'https://demo.example.test/users','https://demo.example.test:8080','https://db.railway.internal','https://akul.az'])
def test_unsafe_connection_locations_rejected(connection,origin):
    with pytest.raises((ContractError,ValueError)):replace(connection,origin=origin).validate()


@pytest.mark.parametrize('patch',[{'tenant_id':IDS[9]},{'human_mode':True},{'paused':True},
    {'group_revision':3},{'group_revision':None},{'group_revision':True},
    {'seconds_remaining':0},{'seconds_remaining':901},{'actions_remaining':0},
    {'tokens_remaining':12001},{'operation':'users.create'},{'primary_agent_id':IDS[8]}])
def test_tenant_human_pause_revision_and_budgets(connection,scope,patch):
    with pytest.raises(ContractError):replace(scope,**patch).validate(connection)


def test_job_runtime_budget_supports_video_capture_window(connection,scope):
    assert replace(scope,seconds_remaining=900).validate(connection).seconds_remaining==900


@pytest.mark.parametrize('action',[BrowserAction('sql'),BrowserAction('shell'),BrowserAction('send_whatsapp'),
    BrowserAction('resume'),BrowserAction('click','billing'),BrowserAction('navigate','users'),
    BrowserAction('click','https://other.example.test'),BrowserAction('fill_demo','quantity','100000'),
    BrowserAction('fill_demo','product','real_customer_product'),BrowserAction('observe',demo_value='secret')])
def test_dangerous_actions_and_real_data_rejected(connection,scope,action):
    with pytest.raises(ContractError):action.validate(scope,connection)


def test_minimum_rights_and_isolated_session_required(connection):
    for patch in ({'permissions':PERMISSIONS|{'admin'}},{'isolated_session':False},{'restore_receipt_ref':''}):
        with pytest.raises(ContractError):replace(connection,**patch).validate()


def test_screenshot_contract_rejects_external_scope_and_missing_sanitization(scope):
    artifact=ScreenshotEvidence(IDS[0],IDS[6],IDS[9],'a'*64,IDS[10],'ui-version-ref')
    assert artifact.validate(scope) is artifact
    for patch in ({'tenant_id':IDS[1]},{'task_id':IDS[2]},{'sha256':'secret'},
                  {'sanitization_receipt_ref':''},{'ui_version':''}):
        with pytest.raises(ContractError):replace(artifact,**patch).validate(scope)
