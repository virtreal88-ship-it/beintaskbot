"""Pure saved-status projection. No network, receipt lookup or status writes."""
import socket
import psycopg
import pytest
import tenant_operator_dialogs as dialogs


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Delivery projection must not access a database/provider')
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(psycopg, 'connect', forbidden)


def row(**changes):
    return {'id':'synthetic-row','external_id':'connect:synthetic','channel':'connect:synthetic-channel',
            'direction':'outgoing','message_type':'text','body':'Saved text',**changes}


@pytest.mark.parametrize('state',['sent','delivered','read','pending','retrying','failed','unknown'])
def test_only_persisted_outgoing_connect_status_is_projected(state):
    value=dialogs._message(row(raw={'status':state,'private_provider_url':'private'}))
    assert value['delivery_state']==state
    assert 'raw' not in value and 'private' not in str(value)


@pytest.mark.parametrize('state',['accepted','queued','sending','reserved'])
def test_queue_acceptance_never_becomes_a_sent_tick(state):
    assert dialogs._message(row(raw={'status':state}))['delivery_state']=='pending'


@pytest.mark.parametrize('raw',[{}, {'status':None},{'status':[]},{'status':{}},{'status':True},
    {'status':'READ'},{'status':'unexpected'},{'read':True,'delivered':True,'readAt':'2026-01-01'}])
def test_unknown_or_indirect_facts_never_prove_delivery(raw):
    assert 'delivery_state' not in dialogs._message(row(raw=raw,delivery_state='read'))


@pytest.mark.parametrize('changes',[{'direction':'incoming'},{'direction':'system'},{'channel':'kommo'}])
def test_incoming_or_other_provider_does_not_gain_outgoing_receipt(changes):
    assert 'delivery_state' not in dialogs._message(row(raw={'status':'read'},**changes))
