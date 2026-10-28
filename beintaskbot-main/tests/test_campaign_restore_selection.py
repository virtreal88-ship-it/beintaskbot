"""Synthetic tenant fixtures only; no paid or live operations."""
import uuid
from test_knowledge_campaigns import campaign, row, PROFILE, A, B
from test_knowledge_candidates import ready
from test_tenant_support_inbox import database, db
import tenant_knowledge_campaigns as campaigns


def test_current_campaign_precedes_newer_stopped_history_and_exact_id_stays_exact(campaign):
    db,provider,data,active=campaign;stopped=str(uuid.uuid4())
    db.execute('''INSERT INTO saas_knowledge_campaigns
      (tenant_id,id,created_by,sources,analysis_agent_id,status,progress,version,max_calls,max_tokens,media_policy,reserved_kinds,created_at)
      SELECT tenant_id,%s::uuid,created_by,sources,analysis_agent_id,'stopped',progress,version,max_calls,max_tokens,media_policy,reserved_kinds,created_at+interval '1 hour'
      FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid''',(stopped,A,active));db.commit()
    before=[dict(row(db,key)) for key in (active,stopped)]
    assert campaigns.get(PROFILE)['campaign']['id']==active
    assert campaigns.get(PROFILE,stopped)['campaign']['id']==stopped
    assert campaigns.get({'tenant_id':B,'telegram_id':22},active)['campaign'] is None
    assert [dict(row(db,key)) for key in (active,stopped)]==before
    db.execute("UPDATE saas_knowledge_campaigns SET status='stopped' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,active));db.commit()
    assert campaigns.get(PROFILE)['campaign']['id']==stopped
    provider.assert_not_called()


def test_collection_api_requires_session_identity_and_returns_only_own_campaign(campaign, monkeypatch):
    import asyncio, json
    from unittest.mock import Mock
    from aiohttp.test_utils import make_mocked_request
    import tenant_knowledge_candidates_api as api
    db,provider,data,active=campaign
    handler=api.handler(lambda _: PROFILE,'https://fixture.test',Mock(),'campaigns')
    def call(query):return asyncio.run(handler(make_mocked_request('GET','/api/platform/ai/knowledge-candidates/campaigns'+query)))
    denied=call('');assert denied.status==409
    denied=call(f'?expected_tenant_id={B}&expected_user_id=11');assert denied.status==409
    response=call(f'?expected_tenant_id={A}&expected_user_id=11');assert response.status==200
    assert json.loads(response.text)['result']['campaign']['id']==active
    provider.assert_not_called()
