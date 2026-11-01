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
    with db.cursor() as cur:tickets.record_case_selected_message_ids(cur,A,ticket,[audio],'handoff:episode')
    db.commit()
    page=tickets.detail(PROFILE,ticket)
    assert page['scope']['kind']=='selected'
    assert [row['id'] for row in page['messages']]==[audio]
    assert video not in {row['id'] for row in page['messages']}


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
    cipher=config_company(db)
    db.execute('UPDATE saas_ai_conversation_state SET agent_id=NULL WHERE tenant_id=%s::uuid',(A,));db.commit()
    with patch.object(summary,'_fernet',return_value=cipher),patch.object(summary,'generate') as provider:
        page=manual()
    provider.assert_not_called()
    assert not page['ticket']['agent_id'] and not page['ticket']['summary_available']
    assert tickets.listing(PROFILE)['tickets'][0]['agent_id'] is None
    assert db.execute('SELECT count(*) AS n FROM saas_ai_agent_runs')[0]['n']==0


def test_summary_uses_current_assignment_instructions_language_and_exact_case(db):
    cipher=config_company(db)
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
    cipher=config_company(db)
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


def test_saved_summary_is_historical_and_explicitly_regenerates_after_agent_edit(db):
    cipher=config_company(db)
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
