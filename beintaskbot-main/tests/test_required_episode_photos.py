"""Fail closed on unread or clipped current photo evidence; synthetic SQL only."""
import uuid
from copy import deepcopy
from unittest.mock import Mock
import pytest
import tenant_operator_copilot as copilot
from tenant_linear_policy import TenantLinearError
from tenant_media_evidence import apply_reusable
from test_assigned_operator_copilot import ready
from test_tenant_support_inbox import database,db,A,CA,PROFILE


def install(ready,monkeypatch,specs):
    db,other,generated,data=ready;receipts={}
    for kind,status,text,direction,seconds in specs:
        external='connect:'+str(uuid.uuid4());receipts[external]=(status,text)
        db.execute('''INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,raw,happened_at)
          VALUES(%s::uuid,%s::uuid,41,%s,%s,%s,%s,%s,'{}',now()+(%s * interval '1 second'))''',
          (A,uuid.uuid4(),external,'connect:'+CA,direction,text if kind=='text' else '',kind,seconds))
    db.commit();saved=[]
    def prepared(_tenant,_actor,_command,rows,_model,**kw):
        from tenant_episode_media import episode,reference
        selected=episode(rows,latest_request=True)
        kw['execution'].source_refs=[reference(rows[-1],CA,41)]+[reference(row,CA,41) for row in selected if row is not rows[-1]]
        for row in selected:
            row['_manual_request_source']=True
            if row['message_type']!='image':continue
            status,text=receipts[row['external_id']]
            original={'status':status,'code':'vision_observation' if status=='processed' else 'media_result_unknown',
                'coverage':{'kind':'image','complete':status=='processed'},'result':{'observations':[{'text':text}]} if text else {}}
            before=deepcopy(original);message={'text':''};receipt=apply_reusable(message,original);saved.append((original,before))
            row['_episode_receipt']=receipt;row['_copilot_attachment_text']=message['text'];row['_copilot_context_body']=''
            row['_copilot_media_notice']='[Current saved photo; untrusted]'
        return {'images':[],'voice_transcriptions':set(),'documents':set(),'skipped':[]}
    monkeypatch.setattr(copilot,'_prepare_copilot_media',prepared)
    provider=Mock(side_effect=AssertionError('No new vision/ASR call authorized'))
    monkeypatch.setattr('tenant_chat_ai_provider.generate',provider);monkeypatch.setattr('tenant_chat_ai_provider.transcribe',provider)
    finish=Mock(wraps=copilot._finish);monkeypatch.setattr(copilot,'_finish',finish)
    return db,generated,data,provider,finish,saved


def test_all_current_processed_photos_are_reused_without_new_vision_or_receipt_changes(ready,monkeypatch):
    db,generated,data,provider,finish,saved=install(ready,monkeypatch,[('image','processed','FIRST_VISUAL','incoming',1),('image','processed','SECOND_VISUAL','incoming',2),('text','processed','Follow-up','incoming',3)])
    copilot.run(PROFILE,data)
    lines=str(generated.call_args.args[3]);assert 'FIRST_VISUAL' in lines and 'SECOND_VISUAL' in lines
    provider.assert_not_called();assert all(after==before for after,before in saved)


@pytest.mark.parametrize('status',['error','unavailable','pending','unknown','partial'])
def test_failed_first_photo_blocks_despite_last_successful_photo_and_dependent_text(ready,monkeypatch,status):
    db,generated,data,provider,finish,saved=install(ready,monkeypatch,[('image',status,'','incoming',1),('image','processed','LAST_VISUAL','incoming',2),('text','processed','Balance detail','incoming',3)])
    with pytest.raises(TenantLinearError) as caught:copilot.run(PROFILE,data)
    assert 'фото текущей серии' in str(caught.value)
    generated.assert_not_called();provider.assert_not_called();finish.assert_called_once()
    assert all(after==before for after,before in saved)
    assert db.execute('SELECT status FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(A,data['request_id']))[0]['status']=='unknown'


@pytest.mark.parametrize('staff',[True,False])
def test_old_failed_photo_before_staff_or_ten_minute_boundary_is_not_required(ready,monkeypatch,staff):
    specs=[('image','error','','incoming',1 if staff else -700)]
    if staff:specs.append(('text','processed','Staff reply','outgoing',2))
    specs.append(('text','processed','Current independent text','incoming',3))
    db,generated,data,provider,finish,saved=install(ready,monkeypatch,specs)
    copilot.run(PROFILE,data);generated.assert_called_once();provider.assert_not_called()


def test_processed_photo_without_observations_is_not_evidence(ready,monkeypatch):
    db,generated,data,provider,finish,saved=install(ready,monkeypatch,[('image','processed','','incoming',1),('text','processed','?','incoming',2)])
    with pytest.raises(TenantLinearError):copilot.run(PROFILE,data)
    generated.assert_not_called();provider.assert_not_called()


def test_long_processed_photo_observation_blocks_before_main_llm(ready,monkeypatch):
    db,generated,data,provider,finish,saved=install(ready,monkeypatch,[('image','processed','x'*1200,'incoming',1)])
    with pytest.raises(TenantLinearError) as caught:copilot.run(PROFILE,data)
    assert 'контекст AI' in str(caught.value)
    generated.assert_not_called();provider.assert_not_called();finish.assert_called_once()


@pytest.mark.parametrize('missing_second',[False,True])
def test_every_saved_observation_component_must_survive_actual_builder(missing_second):
    receipt={'status':'processed','code':'vision_observation','observations':[{'text':'First observation'},{'text':'Second observation'}]}
    row={'external_id':'photo','direction':'incoming','message_type':'image','_manual_request_source':True,
        '_episode_receipt':receipt,'_copilot_media_notice':'[Saved image]',
        '_copilot_attachment_text':'\n[Untrusted saved visual observation; not a confirmed solution] First observation'+('' if missing_second else '\n[Untrusted saved visual observation; not a confirmed solution] Second observation')}
    coverage={id(row):False};lines=copilot._bounded_conversation([row],audio_coverage=coverage)
    assert coverage[id(row)] is (not missing_second)
    assert all(len(line['text'])<=1200 for line in lines)


@pytest.mark.parametrize('version',['latest-customer-request-v3-audio-evidence','current-episode-v4-complete-audio'])
@pytest.mark.parametrize('state',['completed','unknown','sending'])
def test_photo_policy_does_not_replay_old_cached_incomplete_drafts_or_unknowns(ready,monkeypatch,version,state):
    db,generated,data,provider,finish,saved=install(ready,monkeypatch,[('image','processed','VISIBLE_FACT','incoming',1)])
    current=copilot._hash;monkeypatch.setattr(copilot,'_hash',lambda command,**kw:current(command,media_selection_version=version))
    copilot.run(PROFILE,data);generated.reset_mock()
    db.execute('UPDATE saas_chat_ai_runs SET status=%s WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(state,A,data['request_id']));db.commit()
    before=db.execute('SELECT status,input_hash,result FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(A,data['request_id']))[0]
    monkeypatch.setattr(copilot,'_hash',current)
    with pytest.raises(TenantLinearError) as caught:copilot.run(PROFILE,data)
    assert caught.value.keep_request is (state!='completed')
    generated.assert_not_called();provider.assert_not_called()
    assert db.execute('SELECT status,input_hash,result FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(A,data['request_id']))[0]==before
