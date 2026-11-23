from unittest.mock import MagicMock
import pytest
import tenant_dialog_company_actions as actions


@pytest.mark.parametrize("amount", ["-1","0.001","NaN","0","1000000000",True])
def test_credit_rejects_invalid_money(amount):
    with pytest.raises(actions.operator.OperatorError):
        actions.payload("credit", {"info":"Test", "amount":amount})


def test_account_cannot_be_selected_by_caller():
    with pytest.raises(actions.operator.OperatorError):
        actions.payload("credit", {"amount":"1.23","info":"Test","account":"other"})
    assert actions.payload("credit",{"amount":"1.23","info":"Test"}) == {"amount":1.23,"info":"Test"}


def test_task_requires_known_operator_project_and_priority():
    fields = dict(title="Title", description="Text", project="Umumi", priority=3)
    assert actions.payload("task",fields) == fields
    for invalid in [dict(project="missing"),dict(priority=True),dict(title=" ")]:
        with pytest.raises(actions.operator.OperatorError):
            actions.payload("task",dict(fields,**invalid))


@pytest.mark.parametrize(("code","body","state"), [
    (200,b'{"success":true}',"done"),(403,b'{}',"denied"),
    (500,b'{}',"unconfirmed"),(200,b'{"success":false}',"unconfirmed"),
    (200,b'<html>',"unconfirmed")])
def test_provider_contract(monkeypatch, code, body, state):
    session=MagicMock()
    client=session.return_value.__enter__.return_value
    response=client.post.return_value.__enter__.return_value
    response.status_code=code
    response.iter_content.return_value=[body]
    monkeypatch.setattr(actions.operator,"_NoRedirectRequestsSession",session)
    base=actions.operator.OPERATOR_API_BASE_URL
    monkeypatch.setattr(actions.operator,"api_base_url",lambda:base)
    data={"account":"test","info":"Test","amount":1.23}
    assert actions._send("credit",data,{"base_url":base,"token":"test"}) == state
    assert client.post.call_args.args == (base+"/finance/addCredit",)
    assert client.post.call_args.kwargs["json"] == data
    assert client.post.call_args.kwargs["allow_redirects"] is False
