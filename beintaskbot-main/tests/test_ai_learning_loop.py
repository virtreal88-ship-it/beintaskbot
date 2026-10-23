"""Learning Loop over isolated PostgreSQL (PGlite), never a paid model."""
import asyncio
import json
import logging
import uuid
import pytest
from test_tenant_support_inbox import database, db, seed_simulation_run, A, B, CA, PROFILE
import tenant_ai_dashboard as dashboard
from tenant_ai_learning import findings, improvements
from tenant_linear_policy import TenantLinearError


def duplicate_run(db, source, *, events, outcome='unsupported', reason='simulation_media_context_unavailable', recheck=None):
    q,r=str(uuid.uuid4()),str(uuid.uuid4())
    db.execute('''INSERT INTO saas_ai_simulation_queue(id,tenant_id,channel_id,conversation_id,lead_id,source_message_id,
        source_event_id,event_fingerprint,state,recheck_of_run_id,recheck_request_key)
        VALUES(%s::uuid,%s::uuid,%s::uuid,'client',41,%s::uuid,%s,%s,'completed',%s::uuid,%s)''',
        (q,A,CA,source,q,q,recheck or source,q))
    # Fixtures may use their initial run as a valid recheck parent.
    db.execute('''INSERT INTO saas_ai_simulation_runs(id,tenant_id,queue_id,channel_id,conversation_id,source_message_id,
        outcome,virtual_stop_reason,events) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,'client',%s::uuid,%s,%s,%s::jsonb)''',
        (r,A,q,CA,source,outcome,reason,json.dumps(events)))
    db.commit()
    return r


@pytest.mark.parametrize('event,category,component',[
    ({'type':'knowledge_retrieved','source_ids':[]},'knowledge','retrieval'),
    ({'type':'expert_unavailable','reason':'expert_scope_denied'},'behavior','expert'),
    ({'type':'simulation_unsupported','reason':'media_context_unavailable','message_type':'audio'},'context','media'),
    ({'type':'virtual_no_answer_action','reason':'conflicting_evidence'},'quality','answer_validator'),
    ({'type':'provider_result_unknown'},'infrastructure','provider'),
    ({'type':'virtual_no_answer_action','reason':'clarification_limit_reached'},'behavior','simulation'),
])
def test_classification_requires_recorded_evidence(event,category,component):
    items=findings({'outcome':'virtual_reply','events':[event]})
    assert len(items)==1 and items[0]['category']==category and items[0]['component']==component


def test_no_inferred_failure_from_absent_expert_or_human_observation():
    assert findings({'outcome':'virtual_reply','events':[
        {'type':'active_mode_stop_observed','reason':'conversation_human_or_paused'},
        {'type':'trigger_checked','matched':False,'possible_topic':'price'}]})==[]
    assert findings({'outcome':'virtual_reply','knowledge_refs':[]})==[]


def test_grouping_pagination_reviews_and_read_only(db,monkeypatch):
    event={'type':'simulation_unsupported','reason':'media_context_unavailable','message_type':'audio'}
    parent,source,_=seed_simulation_run(db,outcome='unsupported',events=[event])
    for _ in range(14):
        duplicate_run(db,source,events=[event,event],recheck=parent)
    duplicate_run(db,source,events=[{'type':'expert_unavailable','reason':'expert_scope_denied'}],
                  outcome='virtual_reply',reason='',recheck=parent)
    monkeypatch.setattr('tenant_chat_ai_provider.generate',lambda *a,**k:pytest.fail('Diagnostics called AI'))
    before=db.execute('SELECT count(*) AS n FROM saas_ai_simulation_queue')[0]
    first=improvements(PROFILE,limit=1)
    second=improvements(PROFILE,limit=1,cursor=first['next_cursor'])
    assert first['total']==2 and len(first['items'])==1 and len(second['items'])==1
    items=first['items']+second['items']
    media=next(x for x in items if x['component']=='media')
    assert media['count']==15 and len(media['examples'])==5
    assert any(x['result']=='not_observed' for x in media['comparisons'])
    assert improvements(PROFILE)==improvements(PROFILE)  # restart/read produces no duplicates
    assert db.execute('SELECT count(*) AS n FROM saas_ai_simulation_queue')[0]==before
    assert improvements({**PROFILE,'tenant_id':B,'telegram_id':22})['total']==0
    for status in ('in_review','confirmed','fixing','recheck_pending','fixed','dismissed','deferred'):
        result=dashboard.review_issue(PROFILE,media['run_id'],media['issue_key'],status)
        assert result['status']==status
    reviewed=next(x for x in improvements(PROFILE)['items'] if x['id']==media['id'])
    assert len(reviewed['history'])==7 and reviewed['status']=='deferred'
    assert db.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE action='ai_improvement_reviewed'")[0]['n']==7
    with pytest.raises(TenantLinearError):
        dashboard.review_issue({**PROFILE,'tenant_id':B,'telegram_id':22},media['run_id'],media['issue_key'],'fixed')
    db.execute("UPDATE saas_tenant_members SET role='operator' WHERE tenant_id=%s::uuid",(A,));db.commit()
    with pytest.raises(TenantLinearError) as exc:improvements(PROFILE)
    assert exc.value.status==403


def test_different_causes_and_media_are_not_merged():
    audio=findings({'outcome':'unsupported','events':[{'type':'simulation_unsupported','reason':'media_context_unavailable','message_type':'audio'}]})[0]
    image=findings({'outcome':'unsupported','events':[{'type':'simulation_unsupported','reason':'media_context_unavailable','message_type':'image'}]})[0]
    assert audio['issue_key']!=image['issue_key']
    private=findings({'outcome':'blocked','virtual_stop_reason':'private customer content'})[0]
    assert private['reason']=='unclassified_stop' and 'private customer' not in json.dumps(private)


def test_schema_rerun_preserves_legacy_reviews(db):
    import tenant_ai_simulation_schema as schema
    run,_,_=seed_simulation_run(db,events=[{'type':'expert_unavailable'}])
    dashboard.review_issue(PROFILE,run,'expert_unavailable','confirmed')
    schema._ready=False
    schema.ensure_schema(db)
    db.commit()
    assert db.execute('SELECT status,history FROM saas_ai_dashboard_issue_reviews')[0]=={'status':'confirmed','history':[]}


def test_new_evidence_reopens_fixed_group_and_retains_source_links(db):
    event={'type':'expert_unavailable','reason':'expert_scope_denied'}
    parent,source,_=seed_simulation_run(db,events=[event],refs=[{'source_id':'synthetic-source'}])
    db.execute("UPDATE saas_ai_simulation_runs SET expert_results=%s::jsonb WHERE id=%s::uuid",
               (json.dumps([{'agent_id':'synthetic-expert','status':'unavailable'}]),parent));db.commit()
    issue=improvements(PROFILE)['items'][0]
    assert issue['examples'][0]['knowledge_ids']==['synthetic-source']
    assert issue['examples'][0]['expert_ids']==['synthetic-expert']
    original=db.execute('SELECT events,knowledge_refs,expert_results FROM saas_ai_simulation_runs WHERE id=%s::uuid',(parent,))[0]
    dashboard.review_issue(PROFILE,issue['run_id'],issue['issue_key'],'fixed')
    db.execute("UPDATE saas_ai_dashboard_issue_reviews SET updated_at=now()-interval '1 minute'");db.commit()
    duplicate_run(db,source,events=[event],outcome='virtual_reply',reason='',recheck=parent)
    reopened=improvements(PROFILE)['items'][0]
    assert reopened['status']=='new' and reopened['recurred']
    assert reopened['history'][0]['status']=='fixed'
    assert db.execute('SELECT events,knowledge_refs,expert_results FROM saas_ai_simulation_runs WHERE id=%s::uuid',(parent,))[0]==original


def test_grouped_read_endpoint_uses_real_authorization_and_pagination(db):
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    from tenant_ai_simulation_api import handler
    seed_simulation_run(db,events=[{'type':'provider_result_unknown'}])
    profile=dict(PROFILE)
    async def check():
        app=web.Application()
        app.router.add_get('/issues',handler(lambda _:profile,'https://crm.test',logging.getLogger(),'issues'))
        async with TestClient(TestServer(app)) as client:
            response=await client.get('/issues?grouped=1&limit=1')
            assert response.status==200
            data=(await response.json())['result']
            assert data['total']==1 and data['paid_calls']==0
            profile.update(tenant_id=B,telegram_id=22)
            assert (await (await client.get('/issues?grouped=1')).json())['result']['total']==0
            profile.update(tenant_id=A,telegram_id=22)
            assert (await client.get('/issues?grouped=1')).status==403
    asyncio.run(check())


def test_recheck_api_requires_explicit_cost_consent(db,monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    from tenant_ai_simulation_api import handler
    run,_,_=seed_simulation_run(db)
    monkeypatch.setattr('tenant_ai_agent_runtime_flags.enabled',lambda _:True)
    async def check():
        app=web.Application()
        app.router.add_post('/runs/{run_id}/recheck',handler(lambda _:PROFILE,'https://crm.test',logging.getLogger(), 'recheck'))
        payload={'expected_tenant_id':A,'expected_user_id':11,'request_key':'explicit-review'}
        async with TestClient(TestServer(app)) as client:
            response=await client.post('/runs/'+run+'/recheck',json=payload,headers={'Origin':'https://crm.test'})
            assert response.status==409
            assert db.execute('SELECT count(*) AS n FROM saas_ai_simulation_queue')[0]['n']==1
            response=await client.post('/runs/'+run+'/recheck',json={**payload,'confirm_paid_recheck':True},headers={'Origin':'https://crm.test'})
            assert response.status==200
            assert db.execute('SELECT count(*) AS n FROM saas_ai_simulation_queue')[0]['n']==2
    asyncio.run(check())
