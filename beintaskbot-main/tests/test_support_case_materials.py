"""Stable case material/assigned-summary regressions; isolated SQL and mocked AI."""
import json
import uuid
from unittest.mock import patch
import pytest
from test_tenant_support_inbox import database, db, A, B, CA, CB, AGENT, PROFILE, manual, seed_job, config_company
import tenant_support_inbox_store as tickets
import tenant_support_inbox_summary as summary
import tenant_support_case_context as context
import tenant_public_case as public
import tenant_ai_agents_store as agents
import tenant_media_evidence as evidence
from tenant_linear_policy import TenantLinearError

GROUP = '120363411822115409@g.us'
OUTPUT = {key: 'Verified summary' for key in ('description','summary','suggestions','next_steps')}


def add(db, kind='text', body='', tenant=A, channel=CA, lead=41):
    key = str(uuid.uuid4())
    db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,
      channel,direction,body,message_type) VALUES(%s::uuid,%s::uuid,%s,%s,%s,'incoming',%s,%s)''',
      (key, tenant, lead, 'connect:' + key, 'connect:' + channel, body, kind))
    db.commit()
    return key


def legacy(db, ticket):
    db.execute("UPDATE saas_support_tickets SET history_scope='legacy' WHERE id=%s::uuid", (ticket,))
    db.execute('DELETE FROM saas_support_ticket_messages WHERE ticket_id=%s::uuid', (ticket,))
    db.commit()


def test_snapshot_attach_preserves_text_audio_video_and_excludes_future_incident(db):
    audio, video = add(db, 'audio'), add(db, 'video')
    first = manual();ticket = first['ticket']['id']
    before = {row['id'] for row in first['messages']}
    assert {audio, video} <= before and len(before) == 3
    other = add(db, body='A later unrelated incident')
    assert {row['id'] for row in tickets.detail(PROFILE,ticket)['messages']} == before
    tickets.attach_case_media(PROFILE,ticket,[audio])
    assert {row['id'] for row in tickets.detail(PROFILE,ticket)['messages']} == before
    tickets.attach_case_media(PROFILE,ticket,[add(db,'image')])
    after = tickets.detail(PROFILE,ticket)
    assert before <= {row['id'] for row in after['messages']}
    assert other not in {row['id'] for row in after['messages']}
    assert after['scope']['message_count'] == 4 and after['scope']['media_count'] == 3


def test_manual_handoff_without_episode_ids_still_keeps_a_bounded_transcript(db):
    seed_job(db)
    audio,video=add(db,'audio'),add(db,'video')
    with patch.object(tickets,'summarize_created'):
        agents.set_conversation_state(PROFILE,'client','human',provider='connect',channel_id=CA)
    ticket=tickets.listing(PROFILE)['tickets'][0]['id']
    page=tickets.detail(PROFILE,ticket)
    assert {audio,video} <= {row['id'] for row in page['messages']}
    assert page['scope']['kind']=='snapshot' and page['scope']['message_count']==3


@pytest.mark.parametrize('old_event', ['support-media-picker:old','public-case-selection:old','consultation-attachment-selection:old'])
def test_legacy_additions_do_not_become_episode_boundaries(db,old_event):
    audio,video = add(db,'audio'),add(db,'video')
    ticket = manual()['ticket']['id'];legacy(db,ticket)
    with db.cursor() as cur:
        tickets.record_case_selected_message_ids(cur,A,ticket,[audio],old_event)
    db.commit()
    page=tickets.detail(PROFILE,ticket)
    assert page['scope']['kind']=='conversation'
    assert {audio,video} <= {row['id'] for row in page['messages']}
    assert len(page['messages'])==3


def test_legacy_intentional_episode_is_preserved(db):
    audio,video=add(db,'audio'),add(db,'video')
    ticket=manual()['ticket']['id'];legacy(db,ticket)
    # Existing bounded legacy data predates the new material-addition guard.
    db.execute('''INSERT INTO saas_support_ticket_messages(tenant_id,ticket_id,message_id,event_key)
      VALUES(%s::uuid,%s::uuid,%s::uuid,'handoff:episode')''',(A,ticket,audio))
    db.commit()
    page=tickets.detail(PROFILE,ticket)
    assert page['scope']['kind']=='selected'
    assert [row['id'] for row in page['messages']]==[audio]
    assert video not in {row['id'] for row in page['messages']}


def test_later_handoff_cannot_redefine_existing_legacy_full_history(db):
    audio=add(db,'audio');ticket=manual()['ticket']['id'];legacy(db,ticket)
    before=tickets.detail(PROFILE,ticket)
    job=seed_job(db)
    with db.cursor() as cur:
        receipt=tickets.handoff_ticket(cur,{**job,'source_message_id':'connect:'+audio},'New handoff')
    db.commit()
    after=tickets.detail(PROFILE,ticket)
    assert receipt['id']==ticket
    assert before['scope']['kind']==after['scope']['kind']=='conversation'
    assert {row['id'] for row in before['messages']}=={row['id'] for row in after['messages']}


def test_public_text_selection_never_changes_internal_scope_or_memberships(db,monkeypatch):
    monkeypatch.setattr(public,'_connect',lambda:db)
    audio,video=add(db,'audio'),add(db,'video')
    first=manual();ticket=first['ticket']['id']
    text=next(row['id'] for row in first['messages'] if row['message_type']=='text')
    db.execute('''INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id)
      VALUES(%s::uuid,%s::uuid,%s,99)''',(A,CA,GROUP));db.commit()
    before=db.execute('SELECT * FROM saas_support_ticket_messages WHERE ticket_id=%s::uuid',(ticket,));db.commit()
    share=public.create(PROFILE,ticket,{'message_ids':[text],'media_message_ids':[],
        'group_channel_id':CA,'group_conversation_id':GROUP},'https://crm.test')
    after=db.execute('SELECT * FROM saas_support_ticket_messages WHERE ticket_id=%s::uuid',(ticket,));db.commit()
    assert before==after
    assert {audio,video} <= {row['id'] for row in tickets.detail(PROFILE,ticket)['messages']}
    page=public.read(share['url'].rsplit('/',1)[1])
    assert [row['id'] for row in page['public_messages']]==[text]
    assert page['selected_media_ids']==[]


def test_explicit_manual_bounds_and_old_case_selection_ignore_unrelated_latest_history(db):
    prior=add(db,body='Previous incident')
    first,last=add(db,body='Current problem'),add(db,'audio')
    ticket=manual(message_ids=[first,last])['ticket']['id']
    for i in range(85):add(db,body='Later unrelated '+str(i))
    page=tickets.detail(PROFILE,ticket)
    assert [row['id'] for row in page['messages']]==[first,last]
    with db.cursor() as cur:
        assert [row['id'] for row in context.selection(cur,A,ticket,CA,'client',[first,last])]==[first,last]
        with pytest.raises(TenantLinearError):context.selection(cur,A,ticket,CA,'client',[prior])
        with pytest.raises(TenantLinearError):context.selection(cur,B,ticket,CB,'client',[first,last])


def test_explicit_manual_selection_rejects_gaps_and_wrong_route(db):
    first=add(db,body='First');add(db,body='Gap');last=add(db,body='Last')
    with pytest.raises(TenantLinearError):manual(message_ids=[first,last])
    foreign=add(db,tenant=B,channel=CB,lead=42)
    with pytest.raises(TenantLinearError):manual(message_ids=[foreign])
    assert tickets.listing(PROFILE)['tickets']==[]


def test_no_assigned_agent_blocks_company_summary_without_spend_or_fallback(db):
    cipher=config_company(db,assign_agent=True)
    db.execute('UPDATE saas_ai_conversation_state SET agent_id=NULL WHERE tenant_id=%s::uuid',(A,));db.commit()
    with patch.object(summary,'_fernet',return_value=cipher),patch.object(summary,'generate') as provider:
        page=manual()
    provider.assert_not_called()
    assert not page['ticket']['agent_id'] and not page['ticket']['summary_available']
    assert tickets.listing(PROFILE)['tickets'][0]['agent_id'] is None
    assert db.execute('SELECT count(*) AS n FROM saas_ai_agent_runs')[0]['n']==0


def test_blank_assigned_model_never_uses_platform_default_or_reserves_ai(db,monkeypatch):
    cipher=config_company(db,assign_agent=True)
    db.execute("UPDATE saas_ai_agents SET model='' WHERE tenant_id=%s::uuid",(A,));db.commit()
    monkeypatch.setenv('OPENAI_HISTORY_REPLY_MODEL','unrelated-platform-default')
    with patch.object(summary,'_fernet',return_value=cipher),patch.object(summary,'generate') as provider,patch.object(summary,'choose_api_credential') as credential:
        page=manual()
    provider.assert_not_called();credential.assert_not_called()
    assert page['ticket']['agent_id']==AGENT and not page['ticket']['summary_available']
    assert db.execute('SELECT count(*) AS n FROM saas_ai_agent_runs')[0]['n']==0


def test_summary_uses_current_assignment_instructions_language_and_exact_case(db):
    cipher=config_company(db,assign_agent=True)
    first,last=add(db,body='Only current problem'),add(db,body='Current supporting fact')
    second=str(uuid.uuid4())
    db.execute('''INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,enabled,model,instructions,language,style)
      VALUES(%s::uuid,%s::uuid,'Current agent',11,TRUE,'test-model','Current agent instructions','ru','precise')''',(A,second))
    db.execute("UPDATE saas_ai_agents SET instructions='Old job instructions' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT))
    db.execute('UPDATE saas_ai_conversation_state SET agent_id=%s::uuid,revision=revision+1 WHERE tenant_id=%s::uuid',(second,A))
    db.execute("UPDATE saas_ai_agent_settings SET company_instructions='Current company instructions' WHERE tenant_id=%s::uuid",(A,));db.commit()
    with patch.object(summary,'_fernet',return_value=cipher),patch.object(summary,'generate',return_value=(json.dumps(OUTPUT),{'total_tokens':9})) as provider:
        page=manual(message_ids=[first,last])
    assert page['ticket']['summary_state']=='completed'
    assert page['ticket']['agent_id']==second and page['ticket']['summary_current']
    instructions=provider.call_args.kwargs['instructions']
    assert 'Current agent instructions' in instructions and 'Current company instructions' in instructions
    assert 'Old job instructions' not in instructions
    assert provider.call_args.kwargs['extra_context']['assigned_agent_language']=='ru'
    evidence=json.dumps(provider.call_args.args[3])
    assert 'Only current problem' in evidence and 'Internet is down' not in evidence
    assert db.execute('SELECT agent_id::text AS id FROM saas_ai_agent_runs')[0]['id']==second


@pytest.mark.parametrize('change',['assignment','materials','disabled'])
def test_inflight_summary_does_not_apply_after_context_change(db,change):
    cipher=config_company(db,assign_agent=True)
    with patch.object(tickets,'summarize_created'):ticket=manual()['ticket']['id']
    def provider(*_args,**_kwargs):
        if change=='assignment':db.execute('UPDATE saas_ai_conversation_state SET revision=revision+1 WHERE tenant_id=%s::uuid',(A,))
        elif change=='disabled':db.execute('UPDATE saas_ai_agents SET enabled=FALSE WHERE tenant_id=%s::uuid',(A,))
        else:
            image=add(db,'image')
            tickets.attach_case_media(PROFILE,ticket,[image])
        db.commit()
        return json.dumps(OUTPUT),{'total_tokens':7}
    with patch.object(summary,'_fernet',return_value=cipher),patch.object(summary,'generate',side_effect=provider):
        summary.regenerate(PROFILE,ticket)
    page=tickets.detail(PROFILE,ticket)
    assert page['ticket']['summary_state']=='unavailable' and page['ticket']['summary']==''
    assert db.execute('SELECT total_tokens FROM saas_ai_agent_token_usage')[0]['total_tokens']==7
    assert db.execute('SELECT state FROM saas_ai_agent_runs')[0]['state']=='completed'


def test_saved_summary_is_historical_and_explicitly_regenerates_after_agent_edit(db):
    cipher=config_company(db,assign_agent=True)
    with patch.object(summary,'_fernet',return_value=cipher),patch.object(summary,'generate',return_value=(json.dumps(OUTPUT),{})) as provider:
        ticket=manual()['ticket']['id']
        assert tickets.detail(PROFILE,ticket)['ticket']['summary_current']
        summary.regenerate(PROFILE,ticket)
        assert provider.call_count==1
        db.execute("UPDATE saas_ai_agents SET instructions='Changed instructions',version=version+1 WHERE tenant_id=%s::uuid",(A,));db.commit()
        page=tickets.detail(PROFILE,ticket)
        assert not page['ticket']['summary_current'] and page['ticket']['summary']==OUTPUT['summary']
        summary.regenerate(PROFILE,ticket)
        assert provider.call_count==2
        assert tickets.detail(PROFILE,ticket)['ticket']['summary_current']


def save_audio_receipt(db,mid,transcript):
    evidence.create_schema(db)
    ref={'id':mid,'channel_id':CA,'external_id':'connect:'+mid,'body_hash':evidence._body_hash(''),
         'media_kind':'audio','media_version':evidence.media_version({}),'role':'incoming'}
    with db.cursor() as cur:
        evidence.persist(cur,A,[ref],[{'id':mid,'kind':'audio','status':'processed','transcript':transcript}])
    db.commit()


def test_new_exact_media_receipt_invalidates_summary_without_changing_crm_source(db):
    cipher=config_company(db,assign_agent=True);audio=add(db,'audio')
    with patch.object(summary,'_fernet',return_value=cipher),patch.object(summary,'generate',return_value=(json.dumps(OUTPUT),{})) as provider:
        ticket=manual()['ticket']['id']
        assert tickets.detail(PROFILE,ticket)['ticket']['summary_current']
        before=db.execute('SELECT body,raw FROM saas_crm_messages WHERE id=%s::uuid',(audio,))[0];db.commit()
        save_audio_receipt(db,audio,'New saved transcript proving a different issue')
        assert db.execute('SELECT body,raw FROM saas_crm_messages WHERE id=%s::uuid',(audio,))[0]==before
        db.commit()
        page=tickets.detail(PROFILE,ticket)
        assert page['ticket']['summary']==OUTPUT['summary'] and not page['ticket']['summary_current']
        assert provider.call_count==1
        summary.regenerate(PROFILE,ticket)
        assert provider.call_count==2
        assert 'New saved transcript proving a different issue' in json.dumps(provider.call_args.args[3])
        assert tickets.detail(PROFILE,ticket)['ticket']['summary_current']


def test_receipt_arriving_during_generation_withholds_stale_summary(db):
    cipher=config_company(db,assign_agent=True);audio=add(db,'audio')
    with patch.object(tickets,'summarize_created'):ticket=manual()['ticket']['id']
    def provider(*_args,**_kwargs):
        save_audio_receipt(db,audio,'Speech became available during the request')
        return json.dumps(OUTPUT),{'total_tokens':11}
    with patch.object(summary,'_fernet',return_value=cipher),patch.object(summary,'generate',side_effect=provider):
        summary.regenerate(PROFILE,ticket)
    page=tickets.detail(PROFILE,ticket)
    assert page['ticket']['summary']=='' and page['ticket']['summary_state']=='unavailable'
    assert db.execute('SELECT total_tokens FROM saas_ai_agent_token_usage')[0]['total_tokens']==11


def test_summary_keeps_initial_case_problem_before_more_than_thirty_followups(db):
    cipher=config_company(db,assign_agent=True)
    first=add(db,body='The initial and only problem is duplicate receipt printing')
    ids=[first]+[add(db,body='Followup acknowledgement '+str(i)) for i in range(44)]
    with patch.object(summary,'_fernet',return_value=cipher),patch.object(summary,'generate',return_value=(json.dumps(OUTPUT),{})) as provider:
        page=manual(message_ids=ids)
    assert page['ticket']['summary_state']=='completed'
    lines=provider.call_args.args[3]
    assert len(lines)==45
    assert 'duplicate receipt printing' in lines[0]['text']


def test_long_case_summary_preserves_problem_and_recent_evidence_with_fixed_budget(db):
    cipher=config_company(db,assign_agent=True)
    first=add(db,body='Original problem: duplicate printing. '+('a'*2400))
    with patch.object(tickets,'summarize_created'):ticket=manual()['ticket']['id']
    newer=[add(db,body='Recent evidence '+str(i)+' '+('b'*2400)) for i in range(90)]
    tickets.attach_case_messages(PROFILE,ticket,newer[:80])
    tickets.attach_case_messages(PROFILE,ticket,newer[80:])
    with patch.object(summary,'_fernet',return_value=cipher),patch.object(summary,'generate',return_value=(json.dumps(OUTPUT),{})) as provider:
        summary.regenerate(PROFILE,ticket)
    lines=provider.call_args.args[3]
    assert len(lines)==80
    assert any('Original problem: duplicate printing.' in row['text'] for row in lines)
    assert 'Recent evidence 89' in lines[-1]['text']
    assert sum(len(row['text']) for row in lines)<=summary.MAX_SUMMARY_HISTORY_CHARS
    assert provider.call_args.kwargs['extra_context']['history_truncated']
    assert provider.call_args.kwargs['extra_context']['total_case_messages']==92


def test_explicit_later_text_and_media_additions_preserve_existing_case(db):
    image=add(db,'image');first=manual();ticket=first['ticket']['id']
    later=add(db,body='Later relevant customer clarification '+('x'*1500))
    audio=add(db,'audio')
    assert later not in {row['id'] for row in tickets.detail(PROFILE,ticket)['messages']}
    listed=tickets.available_case_messages(PROFILE,ticket)['messages']
    preview=next(row for row in listed if row['id']==later)
    assert len(preview['body'])==1000 and 'raw' not in preview and 'media_url' not in preview
    result=tickets.attach_case_messages(PROFILE,ticket,[later,audio])
    assert result['attached_count']==2
    tickets.attach_case_messages(PROFILE,ticket,[later,audio])
    after=tickets.detail(PROFILE,ticket)
    assert {row['id'] for row in first['messages']}|{later,audio}=={row['id'] for row in after['messages']}
    assert image in {row['id'] for row in after['attached_media']}
    assert db.execute('SELECT count(*) AS n FROM saas_tenant_notifications')[0]['n']==0


def test_explicit_material_additions_reject_foreign_source_and_closed_cases(db):
    ticket=manual()['ticket']['id']
    foreign=add(db,tenant=B,channel=CB,lead=42)
    with pytest.raises(TenantLinearError):tickets.attach_case_messages(PROFILE,ticket,[foreign])
    later=add(db,body='Later valid clarification')
    tickets.update(PROFILE,ticket,{'status':'resolved'})
    with pytest.raises(TenantLinearError):tickets.attach_case_messages(PROFILE,ticket,[later])


def test_case_message_picker_paginates_without_duplicate_or_foreign_rows(db):
    ticket=manual()['ticket']['id']
    ids={add(db,body='Current source '+str(i)) for i in range(55)}
    foreign=add(db,tenant=B,channel=CB,lead=42)
    page=tickets.available_case_messages(PROFILE,ticket)
    first=[row['id'] for row in page['messages']]
    second=tickets.available_case_messages(PROFILE,ticket,page['next_before'])
    combined=first+[row['id'] for row in second['messages']]
    assert len(first)==50 and len(combined)==len(set(combined))==56
    assert ids<=set(combined) and foreign not in combined
