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


def test_current_exact_route_only_for_live_receipt_enrichment_of_joined_history():
    from tenant_message_delivery import candidates
    channel='00000000-0000-0000-0000-000000000201'
    primary=('one@s.whatsapp.net',41,'00000000-0000-0000-0000-000000000701')
    alternate=('two@lid',50,'00000000-0000-0000-0000-000000000702')
    def message(route):
        conversation,lead,message_id=route
        return {'source':'connect','source_channel_id':channel,'source_lead_id':lead,
                'source_conversation_id':conversation,'external_id':'connect:'+message_id,'direction':'outgoing'}
    dialog={'provider':'connect','channel_id':channel,'conversation_id':primary[0],'lead_id':primary[1]}
    result={'dialog':dialog,'messages':[message(primary),message(alternate)]}
    assert len(candidates(result))==2 # Existing ordinary two-source behavior.
    dialog['history_merge_id']='synthetic:01'
    assert set(candidates(result))=={(channel,primary[0],primary[1])}
    for mode in ('archive_read_only','owner_overview_read_only','search_overview_read_only'):
        assert candidates({**result,'dialog':{**dialog,mode:True}})=={}
