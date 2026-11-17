import json
import pytest
from test_connect_missed_calls import call, rejected
from test_connect_call_corrections import invalidated, apply
from tenant_connect_calls import validate
import tenant_connect_transport as transport


def observed(**changes):
    event=call(callStatus='observed',callRevision=0,outcome='unknown',source='evolution',
        offerOccurredAt='2026-11-15T08:59:45Z',terminatedAt='2026-11-15T09:00:00Z',occurredAt='2026-11-15T09:00:00Z')
    event['type']='call.observed';event['data'].update(changes)
    return event


def test_valid_observation():
    assert validate(observed())=='2026-11-15T09:00:00+00:00'
    assert validate(observed(isVideo=True))
    event=observed();del event['data']['callRevision']
    with pytest.raises(ValueError):validate(event)


@pytest.mark.parametrize('changes',[
    {'callRevision':1},{'callRevision':False},{'outcome':'missed'},{'outcome':None},
    {'source':'other'},{'source':None},{'direction':'outgoing'},{'isGroup':True},{'isVideo':None},
    {'offerOccurredAt':None},{'offerOccurredAt':'2026-11-15T09:00:01Z'},
    {'offerOccurredAt':'2026-11-15T08:00:00Z'},{'offerOccurredAt':'2026-11-15T08:59:45'},
    {'terminatedAt':'2026-11-15T09:00:01Z'},{'occurredAt':None},{'occurredAt':'2026-11-15T09:01:00Z'},{'duration':0},{'durationSeconds':0},
    {'duration_seconds':0},{'durationMs':0},{'correctionOf':'call.rejected'}])
def test_invalid_observation(changes):
    event=observed(**changes)
    with pytest.raises(ValueError):validate(event)
    with pytest.raises(transport.ConnectWebhookError) as caught:
        transport.ingest(json.dumps(event).encode(),'bad',event)
    assert caught.value.code=='invalid_call'


@pytest.mark.parametrize('factory',[call,rejected,invalidated])
def test_monotonic_observation_upgrade_and_stale_replay(monkeypatch,factory):
    prior=observed()['data'];known=factory(source='evolution')['data']
    cur,result=apply(monkeypatch,prior,known)
    statement,params=cur.execute.call_args.args
    raw=json.loads(params[1]);assert result is False
    assert raw['callStatus']==known['callStatus'] and raw['_crm_call_observation']==prior
    assert 'outcome' not in raw and 'happened_at=' not in statement
    cur,result=apply(monkeypatch,known,prior)
    assert result is False and cur.execute.call_count==2


@pytest.mark.parametrize('changes',[{'offerOccurredAt':'2026-11-15T08:59:44Z'},
    {'isVideo':True},{'conversationId':'994700000099@s.whatsapp.net'},
    {'source':'other'},{'direction':'outgoing'}])
def test_observation_conflict(monkeypatch,changes):
    with pytest.raises(ValueError,match='call scope conflict'):
        apply(monkeypatch,observed()['data'],observed(**changes)['data'])


def test_observation_duplicate(monkeypatch):
    cur,result=apply(monkeypatch,observed()['data'],observed()['data'])
    assert result is False and cur.execute.call_count==2


@pytest.mark.parametrize('factory',[call,rejected,invalidated])
@pytest.mark.parametrize('changes',[{'source':'other'},{'isVideo':True},
    {'conversationId':'994700000099@s.whatsapp.net'},{'occurredAt':'2026-11-15T09:00:01Z'}])
def test_same_revision_conflicting_evidence(monkeypatch,factory,changes):
    prior=factory(source='evolution',occurredAt='2026-11-15T09:00:00Z')['data']
    incoming={**prior,**changes}
    with pytest.raises(ValueError,match='call scope conflict'):apply(monkeypatch,prior,incoming)


def test_correction_retry_compares_correction_evidence(monkeypatch):
    previous=rejected(occurredAt='2026-11-15T08:59:00Z')['data']
    incoming=invalidated(occurredAt='2026-11-15T09:00:00Z')['data']
    cur,_=apply(monkeypatch,previous,incoming)
    corrected=json.loads(cur.execute.call_args.args[1][0])
    cur,result=apply(monkeypatch,corrected,incoming)
    assert result is False and cur.execute.call_count==2


def test_observed_dto_and_ai_isolation():
    from tenant_operator_dialogs import _message
    from tenant_chat_ai_policy import history
    item=_message({'id':'test','channel':'connect:channel','message_type':'call_observed',
        'direction':'system','body':'','raw':{'isVideo':True,'private':'secret'}})
    assert item['call_is_video'] is True and item['author_type']=='system' and 'raw' not in item
    lines,missing=history([{'message_type':'call_observed','direction':'system','body':'','has_attachment':True}]*40+
        [{'message_type':'text','direction':'incoming','body':'Hello'}],{})
    assert missing==0 and len(lines)==1 and lines[0]['text']=='Hello'
    assert 'call.observed' not in transport.CONNECT_AUTO_CALL_EVENTS


def test_explicit_observed_activation_is_owner_and_destination_scoped(monkeypatch):
    hook={'id':'hook','url':'https://crm.example/api/platform/connect/webhook','active':True,'events':['custom.event','call.missed']}
    profile={'tenant_id':'authorized-tenant','telegram_id':123}
    calls=[]
    monkeypatch.setattr(transport,'_owner',lambda p: calls.append(('owner',p)))
    monkeypatch.setattr(transport,'_stored_connect_credentials',lambda p: {'api_key':'synthetic','webhook_id':'hook'})
    def request(key,method,path,data=None):
        calls.append((method,path,data))
        return {'data':[{**hook,'events':hook['events']+(['call.observed','call.invalidated'] if any(c[0]=='POST' for c in calls) else [])}]}
    monkeypatch.setattr(transport,'_request',request)
    for wid,url in [('other',hook['url']),('hook','https://other.example/api/platform/connect/webhook')]:
        with pytest.raises(ValueError):transport.enable_observed_call_subscription(profile,expected_webhook_id=wid,expected_url=url)
    assert not any(c[0]=='POST' for c in calls)
    assert transport.enable_observed_call_subscription(profile,expected_webhook_id='hook',expected_url=hook['url'])
    posts=[c for c in calls if c[0]=='POST']
    assert posts == [('POST','/v1/webhooks/hook/events',{'add':['call.observed','call.invalidated'],'expectedUrl':hook['url']})]
    assert calls[0]==('owner',profile)


def observed_invalidated(**changes):
    event=observed()
    event['type']='call.invalidated'
    event['data'].update(callStatus='invalidated',callRevision=2,correctionOf='call.observed',correctionReason='provider_contradiction')
    event['data'].pop('outcome')
    event['data'].update(changes)
    return event


def test_observation_correction_contract():
    assert validate(observed_invalidated())
    for changes in [{'outcome':'unknown'},{'source':'other'},{'correctionReason':'other'},
                    {'offerOccurredAt':None},{'terminatedAt':'2026-11-15T09:01:00Z'}]:
        with pytest.raises(ValueError):validate(observed_invalidated(**changes))


def test_observation_correction_retries_and_stale_observation(monkeypatch):
    prior=observed()['data'];incoming=observed_invalidated()['data']
    cur,_=apply(monkeypatch,prior,incoming)
    raw=json.loads(cur.execute.call_args.args[1][1])
    assert raw['correctionOf']=='call.observed' and 'outcome' not in raw
    for event in [incoming,prior]:
        cur,result=apply(monkeypatch,raw,event)
        assert result is False and cur.execute.call_count==2
    with pytest.raises(ValueError):
        apply(monkeypatch,raw,observed_invalidated(offerOccurredAt='2026-11-15T08:59:44Z')['data'])


@pytest.mark.parametrize('factory',[call,rejected,invalidated])
def test_known_evidence_wins_observation_correction_in_both_orders(monkeypatch,factory):
    neutral=observed_invalidated()['data'];known=factory(source='evolution')['data']
    cur,_=apply(monkeypatch,neutral,known)
    raw=json.loads(cur.execute.call_args.args[1][1])
    assert raw['callStatus']==known['callStatus']
    if known['callStatus']!='invalidated':assert 'correctionOf' not in raw
    else:assert raw['correctionOf']=='call.rejected'
    cur,result=apply(monkeypatch,known,neutral)
    assert result is False and cur.execute.call_count==2
