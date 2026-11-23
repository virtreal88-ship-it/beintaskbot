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


def test_renewal_rejects_different_operator_account(monkeypatch):
    from datetime import datetime, timezone
    op = actions.operator
    stamp = datetime.now(timezone.utc)
    monkeypatch.setattr(op, "_tenant", lambda profile:"workspace")
    monkeypatch.setattr(op, "_pending_auth_candidate", lambda profile:dict(
        received_at=stamp, token="synthetic", base_url="example"))
    monkeypatch.setattr(op, "_request_operator", lambda *args,**kw:{})
    monkeypatch.setattr(op, "operator_me_payload",lambda reply:{"identifier":"other@example"})
    monkeypatch.setattr(op, "_record_operator_read",lambda *args:None)
    monkeypatch.setattr(op, "_lock_accounts_capability_scope",lambda *args:None)
    monkeypatch.setattr(op, "_current_profile",lambda *args:{"role":"owner"})
    monkeypatch.setattr(op, "_operator_row",lambda *args,**kw:{})
    monkeypatch.setattr(op, "_connected_login",lambda *args:"original@example")
    monkeypatch.setattr(op, "_pending_timestamp_is_current",lambda *args:True)
    conn=MagicMock()
    monkeypatch.setattr(op, "_connect", lambda:conn)
    with pytest.raises(op.OperatorError) as error:
        op.renew_connected_token({"telegram_id":11},stamp.isoformat())
    assert error.value.status==409
    conn.__enter__.return_value.commit.assert_not_called()


def test_api_switch_is_independent_of_crm_permissions(monkeypatch):
    import tenant_beinsystems_operator_access as access
    monkeypatch.setattr(access,"is_operator_workspace",lambda tenant:tenant=="company")
    staff=dict(tenant_id="company",active=True,role="operator",permissions=["operator_api"])
    assert actions.capabilities(staff)==dict(task=True,credit=True)
    for grants in ([],["tasks","finance","customers"],["operator_data:view"],["operator_data"]):
        assert not any(actions.capabilities(dict(staff,permissions=grants)).values())
    for change in ({"active":False},{"tenant_id":"other"}):
        assert not any(actions.capabilities(dict(staff,**change)).values())
