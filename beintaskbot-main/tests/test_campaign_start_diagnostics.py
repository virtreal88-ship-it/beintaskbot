"""Synthetic-only campaign start diagnostics: no provider calls or live data."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
import tenant_knowledge_candidates_api as api
import tenant_knowledge_campaigns as campaigns
from test_knowledge_campaigns import campaign, row, PROFILE, CA
from test_knowledge_candidates import ready
from test_tenant_support_inbox import database, db


def approval(db, data, key):
    return {'expected_version':1,'channel_ids':[CA],'max_calls':data['max_calls'],
            'max_tokens':data['max_tokens'],'confirm_external_processing':True,
            'media_policy':row(db,key)['media_policy']}


def test_committed_start_is_reported_even_if_readback_fails(campaign, monkeypatch):
    db,provider,data,key=campaign
    payload=approval(db,data,key)
    monkeypatch.setattr(campaigns,'get',Mock(side_effect=RuntimeError('SECRET token and private SQL values')))
    with pytest.raises(campaigns.CampaignStartReadbackFailed) as outcome:
        campaigns.approve(PROFILE,key,payload)
    assert outcome.value.error_class=='RuntimeError'
    assert row(db,key)['status']=='running'
    provider.assert_not_called()


def test_exception_before_commit_rolls_back(campaign, monkeypatch):
    import tenant_knowledge_quality as quality
    db,provider,data,key=campaign
    payload=approval(db,data,key)
    monkeypatch.setattr(quality,'schema',Mock(side_effect=RuntimeError('synthetic failure')))
    with pytest.raises(RuntimeError):campaigns.approve(PROFILE,key,payload)
    assert row(db,key)['status']=='awaiting_budget'
    assert row(db,key)['approved_at'] is None
    provider.assert_not_called()


def request():
    return SimpleNamespace(method='POST',content_type='application/json',json=AsyncMock(return_value={}),match_info={'campaign_id':'synthetic'})


def test_safe_exception_reference_and_committed_result(monkeypatch):
    monkeypatch.setattr(api,'_origin',lambda *_:None)
    monkeypatch.setattr(api,'_expected',lambda *_:None)
    logger=Mock();handler=api.handler(lambda _:PROFILE,'https://fixture.test',logger,'campaign_start')
    secret='secret-token customer-text SELECT private_value'
    monkeypatch.setattr(campaigns,'approve',Mock(side_effect=RuntimeError(secret)))
    response=asyncio.run(handler(request()));body=json.loads(response.text)
    assert response.status==503 and body['success'] is False
    assert len(body['error'].split('Код: ')[1])==32
    logged=str(logger.error.call_args)
    assert 'start_unconfirmed' in logged and 'RuntimeError' in logged and secret not in logged
    assert secret not in response.text
    logger.reset_mock()
    monkeypatch.setattr(campaigns,'approve',Mock(side_effect=campaigns.CampaignStartReadbackFailed('RuntimeError')))
    response=asyncio.run(handler(request()));body=json.loads(response.text)
    assert response.status==200 and body['result']['start_committed'] and body['result']['readback_pending']
    assert len(body['result']['reference'])==32
    assert 'committed_readback' in str(logger.error.call_args)
    assert secret not in str(logger.error.call_args) and secret not in response.text
