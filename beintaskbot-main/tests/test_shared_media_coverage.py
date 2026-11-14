"""One episode barrier at actual prompt/send boundaries; no external I/O."""
import asyncio
import copy
import json
import uuid
from unittest.mock import AsyncMock,Mock
import pytest
from tenant_linear_policy import TenantLinearError
from tenant_media_coverage import MediaCoverage,CoverageError,evidence_ready
from tenant_media_evidence import apply_reusable
from tenant_episode_media import context_lines
import tenant_operator_copilot as copilot
import tenant_ai_agent_worker as worker
import tenant_connect_transport as transport
import tenant_knowledge_media as media
import tenant_chat_ai_provider as provider
ACTUAL_GENERATE=provider.generate
from test_integrated_connect_release import flow
from test_assigned_operator_copilot import ready
from test_tenant_support_inbox import database,db,A,CA,PROFILE


def prepared(kind='voice',text='EXACT EVIDENCE',number=1,**receipt_fields):
    row={'external_id':'connect:'+str(uuid.UUID(int=number)),'channel':'connect:'+CA,'direction':'incoming','message_type':kind,
      'body':'','raw':{},'happened_at':f'2026-11-01T12:00:{number:02d}Z','_manual_request_source':True,'_episode_channel':CA,'_copilot_context_body':''}
    saved={'status':'processed','code':'transcribed' if kind=='voice' else 'vision_observation','coverage':{'complete':True},
      'result':{'transcript':text} if kind=='voice' else {'observations':[{'text':text}]}}
    message={'text':''};receipt=apply_reusable(message,saved);receipt.update(receipt_fields)
    row.update(_episode_receipt=receipt,_copilot_attachment_text=message['text'],_copilot_media_notice='[Exact saved evidence]')
    return row


def manifest_for(rows,manual=False):return MediaCoverage(rows,A,CA,41,latest_request=manual)


def test_mixed_full_evidence_shared_manual_and_auto_exact_ranges_and_wire():
    rows=[prepared('voice','FIRST SPEECH',1),prepared('voice','SECOND SPEECH',2),prepared('voice','THIRD SPEECH',3),prepared('image','VISIBLE FACT',4),prepared('document','FULL DOCUMENT',5)]
    for manual in (False,True):
        manifest=manifest_for(rows,manual)
        if manual:lines=copilot._bounded_conversation(rows,audio_coverage=manifest.coverage,coverage_manifest=manifest)
        else:lines,_=context_lines(rows,rows,coverage=manifest)
        manifest.seal(lines)
        wire=provider.request_payload('fixture-model','reply',lines,'')
        manifest.check(lines,wire)
        assert all(text in str(lines) for text in ('FIRST SPEECH','SECOND SPEECH','THIRD SPEECH','VISIBLE FACT','FULL DOCUMENT'))
        assert not any('coverage' in line for line in lines)
        forged=copy.deepcopy(wire);payload=json.loads(forged['input']);payload['conversation'][0]['text']='Omitted evidence';forged['input']=json.dumps(payload)
        with pytest.raises(CoverageError):manifest.check(wire=forged)


@pytest.mark.parametrize('receipt',[{'status':'unknown'},{'status':'error'},{'status':'partial'},{'truncated':True},{'code':'document_text_truncated'},{'code':'document_pages_sampled'},{'coverage':{}},{'coverage':{'complete':False}}])
def test_status_only_or_contradictory_partial_is_not_complete(receipt):
    row=prepared('document',**receipt)
    assert not evidence_ready(row)
    with pytest.raises(CoverageError):manifest_for([row]).prepared()


@pytest.mark.parametrize('manual',[False,True])
def test_per_message_and_total_clipping_fail_closed_without_context_increase(manual):
    rows=[prepared('voice','X'*5000)]
    manifest=manifest_for(rows,manual)
    lines=copilot._bounded_conversation(rows,audio_coverage=manifest.coverage,coverage_manifest=manifest) if manual else context_lines(rows,rows,coverage=manifest)[0]
    with pytest.raises(CoverageError):manifest.seal(lines)
    assert len(lines[0]['text'])<=(1200 if manual else 4000)
    rows=[prepared('voice','REQUIRED',1)]+[{'external_id':str(i),'direction':'incoming','message_type':'text','body':'Y'*4000,'happened_at':f'2026-11-01T12:01:{i:02d}Z','channel':CA,'_manual_request_source':True} for i in range(2,28)]
    manifest=manifest_for(rows,manual)
    lines=copilot._bounded_conversation(rows,audio_coverage=manifest.coverage,coverage_manifest=manifest) if manual else context_lines(rows,rows,coverage=manifest)[0]
    with pytest.raises(CoverageError):manifest.seal(lines)
    assert sum(len(line['text']) for line in lines)<=(14000 if manual else 24000)


def test_group_alias_requires_matching_physical_channel():
    own=prepared('image','LOCAL VISUAL')
    manifest=manifest_for([own])
    foreign={**own,'channel':str(uuid.uuid4()),'external_id':own['external_id'].removeprefix('connect:')}
    lines,_=context_lines([foreign],[own],coverage=manifest)
    with pytest.raises(CoverageError):manifest.seal(lines)
    aggregate={**foreign,'channel':CA,'external_id':'different-group-representative','photo_message_id':own['external_id'].removeprefix('connect:')}
    lines,_=context_lines([aggregate],[own],coverage=manifest)
    manifest.seal(lines)
    assert 'LOCAL VISUAL' in str(lines)


def test_old_unrelated_failure_before_staff_boundary_is_not_required():
    bad=prepared('voice',status='unknown');staff={'direction':'outgoing','message_type':'text','body':'Employee reply','happened_at':'2026-11-01T12:00:03Z'}
    good=prepared('image','CURRENT',4)
    manifest=manifest_for([bad,staff,good]);assert manifest.required==(good,)


@pytest.mark.parametrize('kind',['image','video','document'])
def test_actual_worker_unavailable_media_blocks_generation_send_and_expert(flow,monkeypatch,kind):
    f=flow;expert=AsyncMock();monkeypatch.setattr(worker,'request_expert',expert)
    monkeypatch.setattr(media,'download_media',AsyncMock(side_effect=TenantLinearError('Synthetic unavailable',403)))
    f['inbound'](kind,'');f['inbound']('text','Current question about this attachment')
    assert f['process']()
    assert (f['generate'].call_count,f['send'].call_count)==(0,0)
    expert.assert_not_called()
    if kind=='video':media.download_media.assert_not_called()


def test_actual_worker_multiple_voices_photo_no_double_dispatch(flow,monkeypatch):
    f=flow;monkeypatch.setattr(media,'supports_images',lambda _:True)
    for _ in range(3):f['inbound']('voice','')
    f['inbound']('image','');f['inbound']('text','Explain the attached materials')
    assert f['process']();assert f['process']() is None
    f['generate'].assert_called_once();f['send'].assert_called_once()
    lines=str(f['generate'].call_args.args[3]);assert lines.count('Salam, qiymet nedir?')>=3 and 'Visible test observation' in lines
    assert f['speech'].call_count==3 and f['observation'].call_count==1


@pytest.mark.parametrize('mutation',['late_incoming','backdated_media','employee','lease','revision','source'])
def test_actual_worker_changes_after_generation_block_locked_send(flow,mutation):
    f=flow;db=f['db'];f['inbound']('voice','');f['inbound']('text','Current question')
    def generated(*args,**kwargs):
        if mutation=='late_incoming':f['inbound']('text','A new question')
        elif mutation in {'backdated_media','employee'}:
            db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,message_type,body,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,%s,%s,'Late committed source',clock_timestamp()-(%s*interval '1 second'))",(str(uuid.uuid4()),A,'connect:'+str(uuid.uuid4()),'connect:'+CA,'outgoing' if mutation=='employee' else 'incoming','text' if mutation=='employee' else 'image',0 if mutation=='employee' else 30));db.commit()
        elif mutation=='lease':db.execute("UPDATE saas_ai_agent_jobs SET lease_until=now()-interval '1 second'");db.commit()
        elif mutation=='revision':db.execute("UPDATE saas_ai_conversation_state SET revision=revision+1");db.commit()
        else:db.execute("UPDATE saas_crm_messages SET raw='{\"mediaId\":\"changed\"}'::jsonb WHERE tenant_id=%s::uuid AND message_type='voice'",(A,));db.commit()
        return 'Generated but stale',{'total_tokens':2}
    f['generate'].side_effect=generated
    f['process']();f['generate'].assert_called_once();f['send'].assert_not_called()


def test_manifest_swap_run_cannot_send(flow,monkeypatch):
    f=flow;f['inbound']('text','Current text')
    original=transport.send_ai_reply_if_active
    def swap(*args,**kwargs):
        assert kwargs.get('coverage_manifest')
        changed=list(args);changed[5]=str(uuid.uuid4())
        return original(*changed,**kwargs)
    monkeypatch.setattr(transport,'send_ai_reply_if_active',swap)
    f['process']();f['generate'].assert_called_once();f['send'].assert_not_called()


def test_manual_completed_cache_rechecks_backdated_selection(ready):
    db,_,generated,data=ready
    first=copilot.run(PROFILE,data)
    assert first['media_coverage']['complete'] is True
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,message_type,body,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','image','',now()-interval '30 seconds')",(str(uuid.uuid4()),A,'connect:'+str(uuid.uuid4()),'connect:'+CA));db.commit()
    with pytest.raises(TenantLinearError):copilot.run(PROFILE,data)
    generated.assert_called_once()


def test_manual_publication_rechecks_backdated_selection(ready):
    db,_,generated,data=ready
    def response(*args,**kwargs):
        db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,message_type,body,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','image','',now()-interval '30 seconds')",(str(uuid.uuid4()),A,'connect:'+str(uuid.uuid4()),'connect:'+CA));db.commit()
        return 'Stale private reply',{'total_tokens':1}
    generated.side_effect=response
    with pytest.raises(TenantLinearError):copilot.run(PROFILE,data)
    generated.assert_called_once()


def test_pre_resume_unavailable_media_is_unrelated_to_new_post_resume_turn(flow):
    f=flow;db=f['db'];f['inbound']('image','')
    db.execute("UPDATE saas_ai_agent_jobs SET state='skipped'")
    db.execute("UPDATE saas_ai_conversation_state SET resumed_at=clock_timestamp(),revision=revision+1");db.commit()
    f['inbound']('text','A new post-resume text question')
    f['process']();f['generate'].assert_called_once();f['send'].assert_called_once()
    f['observation'].assert_not_called()


def test_more_than_bounded_selection_proof_stops_before_provider(flow):
    f=flow;db=f['db']
    # Existing page extension loads the complete current burst; proof never silently clips it.
    for i in range(260):
        db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,message_type,body,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','text','current burst',clock_timestamp())",(str(uuid.uuid4()),A,'connect:'+str(uuid.uuid4()),'connect:'+CA))
    db.commit();f['inbound']('text','Latest question');f['process']()
    f['generate'].assert_not_called();f['send'].assert_not_called()


@pytest.mark.parametrize('mutation',['source','backdated','text'])
def test_actual_expert_boundary_source_changes_prevent_generation(flow,monkeypatch,mutation):
    f=flow;db=f['db'];first,_=f['inbound']('text','First current fact');f['inbound']('voice','');f['inbound']('text','Current question')
    async def expert(*args,**kwargs):
        if mutation=='source':db.execute("UPDATE saas_crm_messages SET raw='{\"mediaId\":\"changed\"}'::jsonb WHERE tenant_id=%s::uuid AND message_type='voice'",(A,))
        elif mutation=='text':db.execute("UPDATE saas_crm_messages SET body='Changed earlier fact' WHERE tenant_id=%s::uuid AND external_id=%s",(A,'connect:'+first))
        else:db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,message_type,body,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','image','',clock_timestamp()-interval '30 seconds')",(str(uuid.uuid4()),A,'connect:'+str(uuid.uuid4()),'connect:'+CA))
        db.commit();return None
    monkeypatch.setattr(worker,'request_expert',expert)
    f['process']();f['generate'].assert_not_called();f['send'].assert_not_called()


@pytest.mark.parametrize('mutation',['source','backdated','text'])
def test_actual_transport_locked_boundary_rejects_changes_after_worker_check(flow,monkeypatch,mutation):
    f=flow;db=f['db'];first,_=f['inbound']('text','Earlier current text');f['inbound']('voice','');f['inbound']('text','Current question')
    original=transport.send_ai_reply_if_active
    def changed(*args,**kwargs):
        if mutation=='source':db.execute("UPDATE saas_crm_messages SET raw='{\"mediaId\":\"changed-at-send\"}'::jsonb WHERE tenant_id=%s::uuid AND message_type='voice'",(A,))
        elif mutation=='text':db.execute("UPDATE saas_crm_messages SET body='changed-at-send' WHERE tenant_id=%s::uuid AND external_id=%s",(A,'connect:'+first))
        else:db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,message_type,body,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','image','',clock_timestamp()-interval '30 seconds')",(str(uuid.uuid4()),A,'connect:'+str(uuid.uuid4()),'connect:'+CA))
        db.commit();return original(*args,**kwargs)
    monkeypatch.setattr(transport,'send_ai_reply_if_active',changed)
    f['process']();f['generate'].assert_called_once();f['send'].assert_not_called()


@pytest.mark.parametrize('tamper',[False,True])
def test_actual_provider_wire_is_checked_before_any_http(flow,monkeypatch,tamper):
    from types import SimpleNamespace
    f=flow;f['inbound']('voice','');f['inbound']('text','Current question')
    monkeypatch.setattr(worker,'generate',ACTUAL_GENERATE)
    response=SimpleNamespace(status_code=200,json=lambda:{'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'Mock final response'}]}],'usage':{'input_tokens':3,'output_tokens':2,'total_tokens':5}})
    http=Mock(return_value=response);monkeypatch.setattr(provider.requests,'post',http)
    original=provider.request_payload
    def payload(*args,**kwargs):
        wire=original(*args,**kwargs)
        if tamper:
            content=json.loads(wire['input']);voice=next(line for line in content['conversation'] if line.get('media_kind') in {'voice','audio'});assert 'Salam, qiymet nedir?' in voice['text'];voice['text']='';wire['input']=json.dumps(content)
        return wire
    monkeypatch.setattr(provider,'request_payload',payload)
    f['process']()
    if tamper:http.assert_not_called();f['send'].assert_not_called()
    else:
        http.assert_called_once();f['send'].assert_called_once()
        assert 'Salam, qiymet nedir?' in json.dumps(http.call_args.kwargs['json'])


@pytest.mark.parametrize('code',['document_evidence_incomplete','document_context_incomplete','video_full_coverage_unavailable','consultation_media_coverage_unavailable'])
def test_new_fixed_failure_codes_remain_metadata_only_allowlisted(code):
    from tenant_manual_ai_run_diagnostics import CODES
    assert code in CODES


def test_large_unrelated_pre_staff_history_does_not_consume_proof_cap(flow):
    f=flow;db=f['db']
    for _ in range(260):
        db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,message_type,body,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','text','Old prior request',clock_timestamp()-interval '2 minutes')",(str(uuid.uuid4()),A,'connect:'+str(uuid.uuid4()),'connect:'+CA))
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,message_type,body,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,'outgoing','text','Employee boundary',clock_timestamp()-interval '1 minute')",(str(uuid.uuid4()),A,'connect:'+str(uuid.uuid4()),'connect:'+CA));db.commit()
    f['inbound']('text','New current question');f['process']()
    f['generate'].assert_called_once();f['send'].assert_called_once()


def test_renderer_true_flag_cannot_hide_missing_actual_component():
    row=prepared('voice','FACT');manifest=manifest_for([row])
    lines,_=context_lines([row],[row],coverage=manifest)
    assert manifest.coverage[id(row)] is True
    lines[0]['text']=''
    with pytest.raises(CoverageError):manifest.seal(lines)
    # A boolean without a source/channel-qualified actual rendered line is insufficient too.
    forged=manifest_for([row]);forged.coverage[id(row)]=True
    with pytest.raises(CoverageError):forged.seal([{'source_id':row['external_id'],'text':'FACT'}])


def test_unknown_media_hold_survives_known_main_coverage_stop_without_pause(flow,monkeypatch):
    f=flow;db=f['db']
    monkeypatch.setattr(media,'supports_images',lambda _:True)
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:1000000}))
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({'test-model':{'input':1000000,'output':1000000}}))
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({'test-model':{'vision_low':1000}}))
    f['observation'].side_effect=TimeoutError('Synthetic provider timeout')
    f['inbound']('image','');f['inbound']('text','Current photo question');f['process']()
    f['generate'].assert_not_called();f['send'].assert_not_called();f['observation'].assert_called_once()
    entries=db.execute('SELECT kind,state,reserved_microusd,actual_microusd FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid ORDER BY kind',(A,))
    assert any(row['kind']=='active_primary' and row['state']=='released' and row['actual_microusd']==0 for row in entries)
    assert any(row['kind']=='episode_media' and row['state']=='unknown' and row['reserved_microusd']==1000 for row in entries)
    assert db.execute("SELECT state FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND channel_id=%s",(A,CA))[0]['state']=='active'
    f['process']();f['observation'].assert_called_once()
    db.commit()


def test_policy_coverage_stop_does_not_change_mode_or_pause(flow,monkeypatch):
    import tenant_ai_agent_run_store as runs
    f=flow;db=f['db'];f['inbound']('text','Current text')
    job=runs.claim_next();reservation=runs.reserve_run(job,pipeline_id='',stage_id='',model='test-model')
    manifest=MediaCoverage([],A,CA,41);manifest.receipts=();manifest.lines_hash='sealed';reservation['_media_coverage']=manifest
    monkeypatch.setattr(worker,'_check_connect_media',Mock(side_effect=CoverageError(code='media_episode_members_changed')))
    result=asyncio.run(worker._send_policy_reply(job,reservation,{},'Clarification',None,None,Mock()))
    assert result is False
    assert db.execute("SELECT state FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND channel_id=%s",(A,CA))[0]['state']=='active'
    f['send'].assert_not_called()
    db.commit()


def test_schedule_closing_while_entering_send_lock_stops_send(flow,monkeypatch):
    import tenant_ai_agent_schedule as schedule
    from datetime import datetime,timezone
    f=flow;f['inbound']('text','Current question')
    original=transport.send_ai_reply_if_active
    def close(*args,**kwargs):
        monkeypatch.setattr(schedule,'next_open',lambda *_args,**_kwargs:datetime.now(timezone.utc))
        return original(*args,**kwargs)
    monkeypatch.setattr(transport,'send_ai_reply_if_active',close)
    f['process']();f['generate'].assert_called_once();f['send'].assert_not_called()


def approved_alias(flow):
    import tenant_connect_dialog_equivalence as equivalent
    f=flow;db=f['db']
    with db.cursor() as cur:equivalent.schema(cur)
    # Keep canonical job route 41; the retained approved physical source stays on 43.
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,'retained-source',43)",(A,CA))
    db.execute("INSERT INTO saas_connect_dialog_links(tenant_id,channel_id,source_id,canonical_id,source_lead,canonical_lead,pair_id,snapshot) VALUES(%s::uuid,%s::uuid,'retained-source','client',43,41,'synthetic-approved','{}')",(A,CA))
    source='connect:'+str(uuid.uuid4())
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,message_type,body,happened_at) VALUES(%s::uuid,%s::uuid,43,%s,%s,'incoming','text','Retained current fact',clock_timestamp()-interval '30 seconds')",(str(uuid.uuid4()),A,source,'connect:'+CA));db.commit()
    return source


def test_approved_linked_physical_text_is_frozen_without_rewriting_lead(flow):
    f=flow;source=approved_alias(f);f['inbound']('text','Question about the retained fact')
    f['process']();f['generate'].assert_called_once();f['send'].assert_called_once()
    assert 'Retained current fact' in str(f['generate'].call_args.args[3])
    # Capture frozen proof via the actual guarded send below in the revocation cases.
    assert f['db'].execute('SELECT kommo_lead_id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s',(A,source))[0]['kommo_lead_id']==43


@pytest.mark.parametrize('stage',['expert','send'])
@pytest.mark.parametrize('mutation',['inactive','revoked','wrong_channel','wrong_tenant'])
def test_alias_membership_is_reproved_at_generation_and_locked_send(flow,monkeypatch,stage,mutation):
    from test_tenant_support_inbox import B,CB
    f=flow;db=f['db'];source=approved_alias(f);f['inbound']('text','Current question')
    def mutate():
        if mutation=='revoked':db.execute('DELETE FROM saas_connect_dialog_links WHERE tenant_id=%s::uuid',(A,))
        else:
            db.execute('UPDATE saas_connect_dialog_links SET active=FALSE WHERE tenant_id=%s::uuid',(A,))
            if mutation in {'wrong_channel','wrong_tenant'}:
                # An unrelated retained link cannot authorize this channel/tenant source.
                target_tenant,target_channel=(B,CB) if mutation=='wrong_tenant' else (A,CB)
                db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Other') ON CONFLICT DO NOTHING",(target_tenant,target_channel))
                db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,'retained-source',143),(%s::uuid,%s::uuid,'unrelated-canonical',141)",(target_tenant,target_channel,target_tenant,target_channel))
                db.execute("INSERT INTO saas_connect_dialog_links(tenant_id,channel_id,source_id,canonical_id,source_lead,canonical_lead,pair_id,snapshot) VALUES(%s::uuid,%s::uuid,'retained-source','unrelated-canonical',143,141,'wrong-scope','{}')",(target_tenant,target_channel))
        db.commit()
    if stage=='expert':
        async def expert(*args,**kwargs):mutate();return None
        monkeypatch.setattr(worker,'request_expert',expert)
    else:
        original=transport.send_ai_reply_if_active
        def send(*args,**kwargs):
            assert any(ref['external_id']==source and ref['lead_id']==43 for ref in kwargs['coverage_manifest'].refs)
            mutate();return original(*args,**kwargs)
        monkeypatch.setattr(transport,'send_ai_reply_if_active',send)
    f['process']();f['send'].assert_not_called()
    if stage=='expert':f['generate'].assert_not_called()
    else:f['generate'].assert_called_once()


@pytest.mark.parametrize('flag',['pilot_developer_followup','pilot_customer_case_reply'])
def test_text_only_case_seals_after_appended_archived_context(flow,monkeypatch,flag):
    import tenant_ai_agent_run_store as runs
    import tenant_connect_images as images
    import tenant_consultation_group as groups
    import tenant_support_inbox_store as cases
    f=flow;message_id,_=f['inbound']('text','Text-only current CASE question')
    # This is a real CASE consumer now, so bind the fixture to a real selected
    # case. Coverage and the source/model send barriers remain fully exercised.
    with f['db'].cursor() as cur:
        receipt=cases.create_in_transaction(cur,A,CA,'client','Synthetic selected case',
            'coverage-case-'+message_id,actor=11,source='manual',message_ids=[],bounded_episode=True)
        assert cases.record_case_message_membership(cur,A,receipt['id'],CA,'client',[message_id],'coverage-selected')==1
    f['db'].commit()
    job=runs.claim_next();job['payload'].update({flag:True,'case_id':receipt['id'],'consultation_id':str(uuid.uuid4())})
    artifact={'message_id':'archived-other-turn','cached_observation':{'relevance':'relevant','text':'Saved archived fact','source':'case_receipt'},'analysis_expected':False}
    monkeypatch.setattr(images,'prepare_case',AsyncMock(return_value=([],set(),[artifact])))
    monkeypatch.setattr(images,'persist_case_observations',Mock(return_value=[]));monkeypatch.setattr(images,'validate_cached_case_observations',Mock(return_value=['archived-other-turn']))
    monkeypatch.setattr(groups,'record_pilot_followup_action',Mock(return_value=True));monkeypatch.setattr(groups,'record_group_turn_action',Mock(return_value=True))
    f['generate'].return_value=(json.dumps({'action':'customer_update','message':'Fixture answer','evidence_quote':'','media_observations':[]}),{'total_tokens':3})
    asyncio.run(worker.process_connect_one(job,Mock()))
    f['generate'].assert_called_once();f['send'].assert_called_once()
    assert 'Saved archived fact' in str(f['generate'].call_args.args[3])


def test_approved_physical_voice_load_and_cached_proof_keep_alias_fence(flow,monkeypatch):
    f=flow;db=f['db'];source=approved_alias(f)
    db.execute("UPDATE saas_crm_messages SET message_type='voice',body='' WHERE tenant_id=%s::uuid AND external_id=%s",(A,source));db.commit()
    f['inbound']('text','Explain this approved retained voice')
    proofs=[];original=transport.send_ai_reply_if_active
    def send(*args,**kwargs):
        proof=kwargs['coverage_manifest'].proof();proofs.append(proof)
        assert any(ref['external_id']==source and ref['lead_id']==43 for ref in proof['refs'])
        with db.cursor() as cur:MediaCoverage.validate_proof(cur,A,CA,41,proof)
        return original(*args,**kwargs)
    monkeypatch.setattr(transport,'send_ai_reply_if_active',send)
    f['process']();f['generate'].assert_called_once();f['send'].assert_called_once()
    assert 'Salam, qiymet nedir?' in str(f['generate'].call_args.args[3])
    db.execute('UPDATE saas_connect_dialog_links SET active=FALSE WHERE tenant_id=%s::uuid',(A,));db.commit()
    with db.cursor() as cur:
        with pytest.raises(CoverageError,match='incomplete'):MediaCoverage.validate_proof(cur,A,CA,41,proofs[0])
    f['generate'].assert_called_once();f['send'].assert_called_once()


@pytest.mark.parametrize('language,expected,misleading',[
    ('az','AI videonu tam təhlil edə bilmir','kontekstinə tam sığmır'),
    ('en','AI cannot analyze the entire video','fit fully in the AI context'),
    ('ru','AI не может проанализировать его целиком','не помещаются целиком'),
    ('auto','AI не может проанализировать его целиком','не помещаются целиком'),
])
@pytest.mark.parametrize('context_limit',[False,True])
def test_video_failure_copy_names_unsupported_full_analysis(language,expected,misleading,context_limit):
    row=prepared('video')
    command={'channel_id':CA,'lead_id':41}
    error=copilot._episode_evidence_error(command,row,{'language':language},context_limit=context_limit)
    assert expected in str(error)
    assert misleading not in str(error)
    assert '[context_preparation: video_full_coverage_unavailable]' in str(error)
    assert error.keep_request is True
    assert command['_media_failure']['code']=='video_full_coverage_unavailable'
    assert command['_media_failure']['failure_stage']=='context_preparation'
    for manual in (False,True):
        with pytest.raises(CoverageError,match='Required current-episode media') as caught:
            manifest_for([row],manual).supported()
        assert caught.value.code=='video_full_coverage_unavailable'
    assert not evidence_ready(row)
