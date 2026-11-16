"""Explicit episode AI case analysis; real isolated SQL, mocked model, no fees."""
import json
import uuid
import pytest
from test_tenant_support_inbox import database,db,A,CA,PROFILE
from test_knowledge_candidates import ready,model_card
from test_dialog_lifecycle_pglite import episodes,payload,command,insert_message
from tenant_linear_policy import TenantLinearError
import tenant_dialog_lifecycle as lifecycle
import tenant_dialog_episode_cases as cases
import tenant_knowledge_candidates_store as store
import tenant_knowledge_candidates_service as service


def close_with_messages(episodes,actor=11):
    accepted=command(actor=actor)['lifecycle']
    ids=[insert_message(episodes,'episode-text-'+str(i),created_at=f'2026-01-01T00:00:0{i}Z') for i in range(3)]
    episodes.execute("UPDATE saas_crm_messages SET body='Help with connection' WHERE id=%s::uuid",(ids[0],))
    episodes.execute("UPDATE saas_crm_messages SET body='Check connection',direction='outgoing' WHERE id=%s::uuid",(ids[1],))
    episodes.execute("UPDATE saas_crm_messages SET body='It works, thank you' WHERE id=%s::uuid",(ids[2],));episodes.commit()
    command('close',1,actor=actor)
    return accepted['id'],ids


def test_explicit_analysis_exact_closed_episode_not_recent_window(episodes,ready):
    db,generation,_=ready
    episode,ids=close_with_messages(db)
    generation.return_value=(json.dumps({'suitable':True,'reason':'Reusable troubleshooting steps','cards':[model_card(confirmed=False)]}),{'input_tokens':40,'output_tokens':20,'total_tokens':60})
    # Much newer unrelated history must never become part of this closed episode.
    for i in range(35):insert_message(db,'newer-'+str(i),created_at='2099-01-01T00:00:00Z')
    with db.cursor() as cur:lifecycle.note_inbound(cur,A,CA,'client','new-arrival','2099-01-02T00:00:00Z')
    db.commit();current=command(revision=3)['lifecycle']
    new_message=insert_message(db,'current-episode-message')
    assert current['id']!=episode and generation.call_count==0
    result=lifecycle.mutate(PROFILE,payload('case',4,episode_id=episode))
    assert result['case_state']=='completed' and result['case_id']
    assert len(result['candidates'])==1
    assert set(result['candidates'][0]['source_message_ids'])==set(ids)
    assert new_message not in result['candidates'][0]['source_message_ids']
    assert generation.call_count==1
    assert 'exactly one draft' in generation.call_args.kwargs['instructions']
    extraction=db.execute('SELECT * FROM saas_knowledge_extractions WHERE id=%s::uuid',(result['request_id'],))[0]
    assert extraction['route']['training_target']['episode_id']==episode
    assert {r['id'] for r in extraction['source_refs']}==set(ids)
    for flag in ('allow_external_media','allow_paid_asr','allow_vision'):assert extraction['route'][flag] is False
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_candidate_sources')[0]['n']==0
    assert db.execute('SELECT count(*) AS n FROM saas_support_tickets')[0]['n']==0
    db.commit()
    again=lifecycle.mutate(PROFILE,payload('case',4,episode_id=episode))
    assert again['case_id']==result['case_id'] and again['request_id']==result['request_id']
    assert generation.call_count==1,'a new button request ID only checks the same paid attempt'
    opened=store.detail(PROFILE,result['case_id'])
    assert set(opened['candidate']['source_message_ids'])==set(ids)


def test_oversized_episode_rejected_before_ai_reservation(episodes,ready):
    db,generation,_=ready
    command()
    for i in range(81):insert_message(db,'long-'+str(i))
    command('close',1)
    with pytest.raises(TenantLinearError) as too_large:command('case',2)
    assert too_large.value.status==413
    assert generation.call_count==0
    assert not db.execute('SELECT * FROM saas_knowledge_extractions')
    assert db.execute('SELECT count(*) AS n FROM saas_dialog_episode_messages')[0]['n']==81


def test_invalid_multiple_drafts_persist_no_candidate_and_never_auto_retry(episodes,ready):
    db,generation,_=ready
    episode,ids=close_with_messages(db)
    generation.return_value=(json.dumps({'suitable':True,'reason':'Reusable','cards':[model_card(confirmed=False),model_card(confirmed=False,title='Second')]}),{'input_tokens':40,'output_tokens':20,'total_tokens':60})
    first=command('case',2,episode_id=episode)
    assert first['case_id'] is None and first['case_state']!='completed'
    assert first['error_code']=='invalid_model_result'
    assert not db.execute('SELECT * FROM saas_knowledge_candidates')
    assert not db.execute('SELECT case_id FROM saas_dialog_episodes WHERE case_id IS NOT NULL');db.commit()
    second=command('case',2,episode_id=episode)
    assert second['request_id']==first['request_id']
    assert generation.call_count==1


def test_internal_episode_capability_cannot_be_forged(episodes,ready):
    episode,ids=close_with_messages(episodes)
    assert cases.episode_id({**PROFILE,'_episode_case_id':episode,'_episode_case_capability':True}) is None
    assert cases.episode_id(cases.scoped(PROFILE,episode))==episode
    with episodes.cursor() as cur:
        with pytest.raises(TenantLinearError):cases.snapshot_rows(cur,cases.scoped({'tenant_id':A,'telegram_id':33},episode),{'channel_id':CA,'conversation_id':'client','history_mode':'channel'},{})



def test_unsuitable_episode_explains_reason_without_candidate_or_repeat_cost(episodes,ready):
    db,generation,_=ready
    episode,ids=close_with_messages(db)
    generation.return_value=(json.dumps({'suitable':False,'reason':'Only a greeting and acknowledgement; no reusable solution.','cards':[]}),{'input_tokens':40,'output_tokens':20,'total_tokens':60})
    first=command('case',2,episode_id=episode)
    assert first['case_state']=='not_suitable' and first['case_id'] is None
    assert first['suitability']['suitable'] is False and 'no reusable solution' in first['suitability']['reason']
    assert not db.execute('SELECT * FROM saas_knowledge_candidates')
    assert not db.execute('SELECT * FROM saas_knowledge_candidate_sources')
    assert db.execute('SELECT count(*) AS n FROM saas_dialog_episode_messages')[0]['n']==3
    db.commit()
    second=command('case',2,episode_id=episode)
    assert second['suitability']==first['suitability'] and generation.call_count==1


def test_proven_owner_episode_publishes_only_assigned_agent_once(episodes,ready,monkeypatch):
    db,generation,_=ready
    episode,ids=close_with_messages(db)
    generation.return_value=(json.dumps({'suitable':True,'reason':'Confirmed reusable resolution','cards':[{**model_card(),'resolution_steps':['Check connection']}]}),{'input_tokens':40,'output_tokens':20,'total_tokens':60})
    import tenant_ai_agents_store as agents
    def unexpected_schema(conn):raise AssertionError('nested publication must not bootstrap/commit')
    monkeypatch.setattr(agents,'ensure_schema',unexpected_schema)
    first=command('case',2,episode_id=episode)
    assert first['publication']['state']=='published',first
    links=db.execute('SELECT * FROM saas_knowledge_candidate_sources')
    assert len(links)==1 and links[0]['agent_id']==first['publication']['agent_id']
    db.commit()
    again=command('case',2,episode_id=episode)
    assert again['publication']==first['publication'] and generation.call_count==1
    assert len(db.execute('SELECT * FROM saas_knowledge_candidate_sources'))==1


def test_assignment_change_keeps_confirmed_episode_as_draft(episodes,ready):
    db,generation,_=ready
    episode,ids=close_with_messages(db)
    generation.return_value=(json.dumps({'suitable':True,'reason':'Confirmed reusable resolution','cards':[{**model_card(),'resolution_steps':['Check connection']}]}),{'input_tokens':40,'output_tokens':20,'total_tokens':60})
    # Change after extraction has completed, exactly before publication admission.
    original=cases.publish_if_proven
    from unittest.mock import patch
    def changed(*args):
        db.execute("UPDATE saas_ai_conversation_state SET revision=revision+1 WHERE tenant_id=%s::uuid",(A,));db.commit()
        return original(*args)
    with patch.object(cases,'publish_if_proven',side_effect=changed):first=command('case',2,episode_id=episode)
    assert first['publication']['state']=='draft' and 'изменилось' in first['publication']['reason']
    assert not db.execute('SELECT * FROM saas_knowledge_candidate_sources')


def test_employee_can_submit_own_episode_but_not_publish_or_manage(episodes,ready):
    db,generation,_=ready
    episode,ids=close_with_messages(db,actor=33)
    employee={'tenant_id':A,'telegram_id':33}
    generation.return_value=(json.dumps({'suitable':True,'reason':'Confirmed reusable resolution','cards':[{**model_card(),'resolution_steps':['Check connection']}]}),{'input_tokens':40,'output_tokens':20,'total_tokens':60})
    first=command('case',2,actor=33,episode_id=episode)
    assert first['case_id'] and first['publication']['state']=='draft'
    assert generation.call_count==1
    assert not db.execute('SELECT * FROM saas_knowledge_candidate_sources');db.commit()
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError):store.authorize(cur,employee)
        with pytest.raises(TenantLinearError):store.authorize(cur,{**employee,'_episode_case_id':episode,'_episode_case_capability':True})
    db.commit()
    repeat=command('case',2,actor=33,episode_id=episode)
    assert repeat['publication']==first['publication'] and generation.call_count==1


@pytest.mark.parametrize('permissions',[['dialogs'],['support']])
def test_episode_submission_revoked_permission_never_spends(episodes,ready,permissions):
    db,generation,_=ready
    episode,ids=close_with_messages(db,actor=33)
    db.execute('UPDATE saas_tenant_members SET permissions=%s::jsonb WHERE tenant_id=%s::uuid AND telegram_id=33',(json.dumps(permissions),A));db.commit()
    with pytest.raises(TenantLinearError):command('case',2,actor=33,episode_id=episode)
    assert generation.call_count==0 and not db.execute('SELECT * FROM saas_knowledge_extractions')


def test_full_text_never_silently_trimmed_or_paid(episodes,ready):
    db,generation,_=ready
    episode,ids=close_with_messages(db)
    db.execute('UPDATE saas_crm_messages SET body=%s WHERE id=%s::uuid',('long complete text '*1500,ids[0]));db.commit()
    with pytest.raises(TenantLinearError) as blocked:command('case',2,episode_id=episode)
    assert blocked.value.status==413 and generation.call_count==0
    assert '12000' in str(blocked.value) and 'не обрезан' in str(blocked.value)
    assert not db.execute('SELECT * FROM saas_knowledge_extractions')


def test_incomplete_episode_model_coverage_creates_nothing(episodes,ready):
    db,generation,_=ready
    episode,ids=close_with_messages(db)
    generation.return_value=(json.dumps({'suitable':True,'reason':'Useful','cards':[model_card(refs=('m2','m3'))]}),{'input_tokens':40,'output_tokens':20,'total_tokens':60})
    result=command('case',2,episode_id=episode)
    assert result['error_code']=='invalid_model_result' and result['case_id'] is None
    assert not db.execute('SELECT * FROM saas_knowledge_candidates')


def test_unproven_confirmation_cannot_publish(episodes,ready):
    db,generation,_=ready
    episode,ids=close_with_messages(db)
    db.execute("UPDATE saas_crm_messages SET body='Still not working' WHERE id=%s::uuid",(ids[-1],));db.commit()
    generation.return_value=(json.dumps({'suitable':True,'reason':'Useful','cards':[{**model_card(),'resolution_steps':['Check connection']}]}),{'input_tokens':40,'output_tokens':20,'total_tokens':60})
    result=command('case',2,episode_id=episode)
    assert result['publication']['state']=='draft' and result['publication']['reason']
    assert not db.execute('SELECT * FROM saas_knowledge_candidate_sources')


def test_independent_ai_use_denial_blocks_episode_before_cost(episodes,ready,monkeypatch):
    db,generation,_=ready
    episode,ids=close_with_messages(db,actor=33)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    settings={'33':{'ai_enabled':False,'access_scopes':{'ai_agents':{'view':'none','update':'none'}}}}
    db.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s::uuid,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value",(A,json.dumps(settings)));db.commit()
    with pytest.raises(TenantLinearError) as denied:command('case',2,actor=33,episode_id=episode)
    assert denied.value.status==403 and generation.call_count==0
    assert not db.execute('SELECT * FROM saas_knowledge_extractions')


def test_31_short_messages_preserve_whole_episode(episodes,ready):
    db,generation,_=ready
    accepted=command()['lifecycle']
    ids=[insert_message(db,'whole-'+str(i),created_at=f'2026-01-01T00:00:{i:02d}Z') for i in range(31)]
    command('close',1)
    generation.return_value=(json.dumps({'suitable':True,'reason':'Reusable draft','cards':[model_card(refs=tuple('m'+str(i+1) for i in range(31)),confirmed=False)]}),{'input_tokens':40,'output_tokens':20,'total_tokens':60})
    result=command('case',2,episode_id=accepted['id'])
    assert result['case_id'] and generation.call_count==1
    assert set(result['candidates'][0]['source_message_ids'])==set(ids)


def test_confirmed_result_with_invented_instruction_stays_draft(episodes,ready):
    db,generation,_=ready
    episode,ids=close_with_messages(db)
    generation.return_value=(json.dumps({'suitable':True,'reason':'Reusable','cards':[{**model_card(),'resolution_steps':['Delete the database and disable security']}]}),{'input_tokens':40,'output_tokens':20,'total_tokens':60})
    result=command('case',2,episode_id=episode)
    assert result['publication']['state']=='draft' and 'Шаги решения' in result['publication']['reason']
    assert not db.execute('SELECT * FROM saas_knowledge_candidate_sources')


def test_legacy_unknown_send_blocks_case_before_ai_cost(episodes,ready):
    db,generation,_=ready
    episode,_=close_with_messages(db)
    db.execute("INSERT INTO saas_chat_send_receipts(tenant_id,actor_id,request_id,lead_id,input_hash,state,route) VALUES(%s::uuid,11,%s::uuid,41,'legacy','unknown','{}'::jsonb)",(A,str(uuid.uuid4())))
    db.commit()
    with pytest.raises(TenantLinearError) as blocked:
        lifecycle.mutate(PROFILE,payload('case',2,episode_id=episode))
    assert blocked.value.status==409
    assert 'старая отправка' in str(blocked.value)
    assert generation.call_count==0
