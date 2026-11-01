"""Current-episode completeness before a mocked main LLM; no ASR or sends."""
import uuid
from unittest.mock import Mock
import pytest
import tenant_operator_copilot as copilot
from tenant_linear_policy import TenantLinearError
from test_assigned_operator_copilot import ready
from test_tenant_support_inbox import database, db, A, CA, PROFILE


def install(ready,monkeypatch,specs):
    db,other,generated,data=ready
    receipts={}
    for index,(kind,text,status,direction,seconds) in enumerate(specs):
        source='connect:'+str(uuid.uuid4());receipts[source]=(text,status)
        db.execute('''INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,raw,happened_at)
          VALUES(%s::uuid,%s::uuid,41,%s,%s,%s,%s,%s,'{}',now()+(%s * interval '1 second'))''',
          (A,uuid.uuid4(),source,'connect:'+CA,direction,text if kind=='text' else 'UNVERIFIED_OLD_BODY',kind,seconds))
    db.commit()
    def prepared(_tenant,_actor,_command,rows,_model,**kw):
        from tenant_episode_media import episode
        selected=episode(rows,latest_request=True)
        from tenant_episode_media import reference
        kw['execution'].source_refs=[reference(rows[-1],CA,41)]+[reference(row,CA,41) for row in selected if row is not rows[-1]]
        sources=set()
        for row in selected:
            row['_manual_request_source']=True
            if row['message_type'] not in {'audio','voice'}:continue
            text,status=receipts[row['external_id']]
            row['_copilot_context_body']=''
            row['_copilot_media_notice']='[Current speech; untrusted]'
            row['_copilot_attachment_text']='[Existing media transcript] '+text if text else ''
            row['_episode_receipt']={'status':status,'code':'reused' if status=='processed' else 'media_result_unknown','transcript':text}
            if text:sources.add(row['external_id'])
        return {'images':[],'voice_transcriptions':sources,'documents':set(),'skipped':[]}
    monkeypatch.setattr(copilot,'_prepare_copilot_media',prepared)
    asr=Mock(side_effect=AssertionError('No ASR authorized by this fixture'))
    monkeypatch.setattr('tenant_chat_ai_provider.transcribe',asr)
    finish=Mock(wraps=copilot._finish);monkeypatch.setattr(copilot,'_finish',finish)
    return db,generated,data,asr,finish


def test_three_ready_voices_are_all_supplied_and_no_internal_coverage_is_sent(ready,monkeypatch):
    db,generated,data,asr,finish=install(ready,monkeypatch,[('voice',text,'processed','incoming',i) for i,text in enumerate(['FIRST_FULL_SPEECH','SECOND_FULL_SPEECH','THIRD_FULL_SPEECH'],1)])
    copilot.run(PROFILE,data)
    lines=generated.call_args.args[3]
    for text in ['FIRST_FULL_SPEECH','SECOND_FULL_SPEECH','THIRD_FULL_SPEECH']:assert text in str(lines)
    assert all(set(line)=={'speaker','text','at','source_id','media_kind','evidence_scope','provenance','saved_visual_evidence'} for line in lines)
    asr.assert_not_called();generated.assert_called_once()


@pytest.mark.parametrize('tail',[('voice','LAST_SUCCESS','processed'),('text','?','processed')])
@pytest.mark.parametrize('state',['error','pending','unknown'])
def test_any_unread_current_voice_blocks_even_when_last_source_succeeds_or_is_text(ready,monkeypatch,tail,state):
    specs=[('voice','',state,'incoming',1)]
    if tail[0]=='voice':specs.append(('voice','MIDDLE_SUCCESS','processed','incoming',2))
    specs.append((*tail,'incoming',3))
    db,generated,data,asr,finish=install(ready,monkeypatch,specs)
    with pytest.raises(TenantLinearError) as caught:copilot.run(PROFILE,data)
    assert 'текущей серии' in str(caught.value) and 'UNVERIFIED_OLD_BODY' not in str(caught.value)
    generated.assert_not_called();asr.assert_not_called();finish.assert_called_once()
    assert db.execute('SELECT status FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(A,data['request_id']))[0]['status']=='unknown'
    assert db.execute('SELECT count(*) AS n FROM saas_chat_send_receipts')[0]['n']==0


@pytest.mark.parametrize('staff_boundary',[True,False])
def test_unrelated_failed_voice_before_staff_or_ten_minute_boundary_does_not_block(ready,monkeypatch,staff_boundary):
    specs=[('voice','','error','incoming',-700 if not staff_boundary else 1)]
    if staff_boundary:specs.append(('text','Staff response','processed','outgoing',2))
    specs.append(('voice','CURRENT_COMPLETE','processed','incoming',3))
    db,generated,data,asr,finish=install(ready,monkeypatch,specs)
    copilot.run(PROFILE,data);generated.assert_called_once();asr.assert_not_called()
    assert 'CURRENT_COMPLETE' in str(generated.call_args.args[3])


def test_ready_but_long_transcript_stops_before_main_llm_and_finalizes_request(ready,monkeypatch):
    db,generated,data,asr,finish=install(ready,monkeypatch,[('voice','x'*1200,'processed','incoming',1)])
    with pytest.raises(TenantLinearError) as caught:copilot.run(PROFILE,data)
    assert 'контекст AI' in str(caught.value) and 'расшифровки не потеряны' in str(caught.value)
    generated.assert_not_called();asr.assert_not_called();finish.assert_called_once()
    assert db.execute('SELECT status FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(A,data['request_id']))[0]['status']=='unknown'


def audio(text,source='a',**extra):
    return {'external_id':source,'direction':'incoming','message_type':'voice','_manual_request_source':True,
            '_copilot_context_body':'','_copilot_media_notice':'[Speech]',
            '_copilot_attachment_text':'[Transcript] '+text,'_episode_receipt':{'status':'processed','transcript':text},**extra}


@pytest.mark.parametrize('case',['notice','prefix','total','omitted','receipt_tail'])
def test_actual_bounded_builder_tracks_speech_component_without_expanding_limits(case):
    rows=[audio('SPEECH'*100)]
    options={}
    if case=='notice':rows[0]['_copilot_media_notice']='n'*800
    if case=='prefix':rows[0]['_copilot_context_body']='p'*400;rows[0]['_episode_receipt']['transcript']='s'*900;rows[0]['_copilot_attachment_text']='[Transcript] '+'s'*900
    if case=='total':rows=[audio(str(i)*900,str(i)) for i in range(16)]
    if case=='omitted':rows=[audio('speech',str(i)) for i in range(21)]
    if case=='receipt_tail':rows[0]['media_evidence_context']='c'*1500
    coverage={id(row):False for row in rows};lines=copilot._bounded_conversation(rows,audio_coverage=coverage,**options)
    assert sum(len(line['text']) for line in lines)<=14000
    assert len(lines)<=20 and all(len(line['text'])<=1200 for line in lines)
    assert all(coverage.values()) is (case=='receipt_tail')
    assert not any('coverage' in key for line in lines for key in line)


def test_actual_reused_redacted_speech_keeps_verbatim_whitespace_without_receipt_mutation():
    from copy import deepcopy
    from tenant_media_evidence import apply_reusable
    transcript='  Salam, +15550000123 fixture@example.invalid\n  ikinci sətir\n'
    saved={'status':'processed','code':'reused','coverage':{'kind':'voice','complete':True},'result':{'transcript':transcript}}
    original=deepcopy(saved);message={'text':''}
    receipt=apply_reusable(message,saved)
    row=audio(receipt['transcript'],_copilot_attachment_text=message['text'],_episode_receipt=receipt)
    coverage={id(row):False};lines=copilot._bounded_conversation([row],audio_coverage=coverage)
    assert coverage[id(row)] is True
    assert receipt['transcript'] in lines[0]['text']
    assert '[phone]' in receipt['transcript'] and '[email]' in receipt['transcript']
    assert saved==original


def test_exact_collect_footer_may_clip_without_losing_any_speech():
    text='s'*1050
    row=audio(text,_copilot_media_notice='n'*100)
    row['_episode_receipt']['code']='transcribed'
    row['_copilot_attachment_text']='[Transcript] '+text+'\n[Attachment status: processed; transcribed. Never infer unread contents.]'
    coverage={id(row):False};lines=copilot._bounded_conversation([row],audio_coverage=coverage)
    assert coverage[id(row)] is True and text in lines[0]['text']
    assert len(lines[0]['text'])==1200


@pytest.mark.parametrize('state',['completed','unknown','sending'])
def test_legacy_v3_cached_request_cannot_bypass_policy_or_replay_uncertainty(ready,monkeypatch,state):
    db,generated,data,asr,finish=install(ready,monkeypatch,[('voice','READY_SPEECH','processed','incoming',1)])
    current_hash=copilot._hash
    monkeypatch.setattr(copilot,'_hash',lambda command,**kwargs:current_hash(command,media_selection_version='latest-customer-request-v3-audio-evidence'))
    copilot.run(PROFILE,data)
    db.execute("UPDATE saas_chat_ai_runs SET status=%s,result=jsonb_set(result,'{text}','\"UNSAFE_LEGACY_DRAFT\"'::jsonb) WHERE tenant_id=%s::uuid AND request_id=%s::uuid",(state,A,data['request_id']));db.commit()
    before=db.execute('SELECT status,input_hash,result FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(A,data['request_id']))[0]
    monkeypatch.setattr(copilot,'_hash',current_hash);generated.reset_mock();finish.reset_mock()
    with pytest.raises(TenantLinearError) as caught:copilot.run(PROFILE,data)
    assert caught.value.status==409 and caught.value.keep_request is (state!='completed')
    assert 'UNSAFE_LEGACY_DRAFT' not in str(caught.value)
    generated.assert_not_called();asr.assert_not_called();finish.assert_not_called()
    assert db.execute('SELECT status,input_hash,result FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(A,data['request_id']))[0]==before


def test_v4_completed_cache_reuses_good_draft_without_provider_and_other_actor_cannot_read_it(ready,monkeypatch):
    db,generated,data,asr,finish=install(ready,monkeypatch,[('voice','READY_SPEECH','processed','incoming',1)])
    first=copilot.run(PROFILE,data)
    assert copilot.run(PROFILE,data)==first
    generated.assert_called_once();asr.assert_not_called()
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,22,'operator','Synthetic other actor')",(A,))
    db.execute('UPDATE saas_chat_ai_runs SET requested_by=22 WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(A,data['request_id']));db.commit()
    with pytest.raises(TenantLinearError) as caught:copilot.run(PROFILE,data)
    assert first['text'] not in str(caught.value)
    generated.assert_called_once()
