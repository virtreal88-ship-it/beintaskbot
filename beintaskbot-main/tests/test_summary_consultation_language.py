"""Real consultation/outbox consumers; only external generation/transport stubs."""
import json
import uuid

import pytest

from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db,A,CA,AGENT,PROFILE
from test_summary_language_integration import language_db,_policy,_case,_messages,EN,AZ,SUMMARY
from test_integrated_connect_release import flow
from test_pr465_followup_worker import case_flow,_queue_initial_case
import tenant_summary_language as languages
import tenant_support_inbox_store as cases
import tenant_support_inbox_summary as summaries
import tenant_internal_consultations as internal
import tenant_dialog_consultations as consultations
import tenant_dialog_consultations_worker as dispatch
from tenant_consultation_summary import for_case,COPY,group_instructions
from tenant_consultation_group import _case_group_body


def _handoff(f,case_id,message_ids):
    job={'provider':'connect','tenant_id':A,'channel_id':CA,'conversation_id':'client',
      'lead_id':41,'agent_id':AGENT,'event_fingerprint':'consultation-summary-'+uuid.uuid4().hex,
      'source_message_id':message_ids[-1],'case_message_ids':message_ids}
    with f['factory']() as conn:
        with conn.cursor() as cur:
            receipt=cases.handoff_ticket(cur,job,'ai_developer_consultation:missing_knowledge')
    assert receipt['id']==case_id
    return job


@pytest.mark.parametrize('code',['az','ru','en','tr'])
def test_new_ai_consultation_reuses_exact_completed_case_summary_with_provenance(language_db,monkeypatch,code):
    f=language_db;_policy(mode='fixed',fixed=code)
    page,ids=_case(f,[EN],expected=code)
    job=_handoff(f,page['ticket']['id'],ids)
    monkeypatch.setattr(internal,'_connect',f['factory'])
    receipt=internal.create_from_ai(job,recipient_id=33,reason='English technical reason code',
      evidence={'query':EN,'case_message_ids':ids,'source_external_id':'connect:'+ids[-1]})
    assert receipt['created'] and receipt['customer_message_sent'] is False
    row=f['db'].execute('SELECT reason,evidence FROM saas_internal_consultations WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,receipt['id']))[0]
    bundle=row['evidence']['ai_summary']
    assert bundle['state']=='ready' and bundle['language']['language']==code
    assert bundle['language']['policy_version']==1 and bundle['language']['method']=='company_fixed'
    assert SUMMARY[code] in row['reason'] and row['evidence']['reason_code']=='English technical reason code'
    body=_case_group_body(receipt['id'],page['ticket']['id'],'',row['evidence'])
    assert COPY[code]['summary'] in body and SUMMARY[code] in body
    f['provider'].assert_called_once()  # Existing case summary, no consultation AI call.


@pytest.mark.parametrize('change',['pending','failed','manual','source','policy','legacy','selection'])
def test_unverified_case_summary_never_masquerades_as_new_consultation_translation(language_db,change):
    f=language_db;_policy(mode='fixed',fixed='ru')
    if change in {'pending','legacy'}:
        ids=_messages(f,[EN,AZ])
        with f['factory']() as conn:
            with conn.cursor() as cur:
                receipt=cases.create_in_transaction(cur,A,CA,'client','Employee selected case',
                    'pending-'+uuid.uuid4().hex,actor=11,source='manual',
                    message_ids=ids,bounded_episode=True)
                assert cases.record_case_selected_message_ids(cur,A,receipt['id'],ids,'selected')==len(ids)
        page=cases.detail(PROFILE,receipt['id'])
    else:
        if change=='failed':f['provider'].side_effect=TimeoutError('Synthetic unknown summary response')
        page,ids=_case(f,[EN,AZ],expected='ru')
    case_id=page['ticket']['id'];before=f['db'].execute('SELECT summary FROM saas_support_tickets WHERE id=%s::uuid',(case_id,))[0]['summary']
    if change=='failed':assert page['ticket']['summary_state']=='unavailable'
    elif change=='legacy':f['db'].execute("UPDATE saas_support_tickets SET summary='Legacy employee text is preserved' WHERE id=%s::uuid",(case_id,));before='Legacy employee text is preserved'
    elif change=='manual':cases.update(PROFILE,case_id,{'description':'Exact manual edit; keep it'})
    elif change=='source':f['db'].execute("UPDATE saas_crm_messages SET body='A changed source question needs a different analysis.' WHERE id=%s::uuid",(ids[0],))
    elif change=='policy':_policy(mode='fixed',fixed='tr')
    f['db'].commit()
    with f['factory']() as conn:
        with conn.cursor() as cur:
            bundle=for_case(cur,A,case_id,selected_ids=ids[-1:] if change=='selection' else ids)
    assert bundle['state']=='unavailable' and bundle['language'] is None and bundle['summary_hash'] is None
    assert COPY[bundle['selection']['language']]['unavailable'] in bundle['text']
    assert f['db'].execute('SELECT summary FROM saas_support_tickets WHERE id=%s::uuid',(case_id,))[0]['summary']==before
    assert f['provider'].call_count==(0 if change in {'pending','legacy'} else 1)
    if change=='selection':assert EN not in bundle['text'] and bundle['source_message_ids']==ids[-1:]


@pytest.mark.parametrize('window',['ready','pending','completes_before_prepare'])
def test_actual_initial_outbox_uses_frozen_summary_or_honest_pending_fallback_without_extra_ai(case_flow,monkeypatch,window):
    f=case_flow;local=f['db'];monkeypatch.setattr(languages,'_connect',lambda:local)
    _policy(mode='fixed',fixed='ru')
    item=_queue_initial_case(f)
    case_id=local.execute('SELECT case_id::text AS id FROM saas_ai_case_developer_dispatches WHERE id=%s::uuid',(item,))[0]['id'];local.commit()
    f['summary'].return_value=(json.dumps({key:SUMMARY['ru'] for key in ('description','summary','suggestions','next_steps')}),{'total_tokens':3})
    if window=='ready':summaries.summarize(A,case_id,PROFILE)
    original=consultations.prepare
    if window=='completes_before_prepare':
        def finish_then_prepare(*args,**kwargs):
            summaries.summarize(A,case_id,PROFILE)
            return original(*args,**kwargs)
        monkeypatch.setattr(consultations,'prepare',finish_then_prepare)
    dispatch.dispatch_auto_case(item)
    f['developer_sends'].assert_called_once()
    row=local.execute('''SELECT c.reason,c.evidence,d.state FROM saas_ai_case_developer_dispatches d
      JOIN saas_internal_consultations c ON c.tenant_id=d.tenant_id AND c.id=d.consultation_id WHERE d.id=%s::uuid''',(item,))[0]
    bundle=row['evidence']['ai_summary']
    assert row['state']=='sent' and bundle['selection']['language']=='ru'
    if window=='ready':assert bundle['state']=='ready' and SUMMARY['ru'] in row['reason']
    else:assert bundle['state']=='unavailable' and bundle['language'] is None and COPY['ru']['unavailable'] in row['reason']
    assert f['summary'].call_count==(0 if window=='pending' else 1)
    # A later completed case summary cannot rewrite/re-send the frozen request.
    snapshot=row['reason'];dispatch.dispatch_auto_case(item)
    f['developer_sends'].assert_called_once()
    assert local.execute('SELECT reason FROM saas_internal_consultations WHERE tenant_id=%s::uuid AND id=(SELECT consultation_id FROM saas_ai_case_developer_dispatches WHERE id=%s::uuid)',(A,item))[0]['reason']==snapshot


def test_new_group_update_language_is_scoped_to_internal_answer_not_customer_reply(language_db):
    f=language_db;_policy(mode='fixed',fixed='en')
    page,ids=_case(f,[AZ],expected='en')
    with f['factory']() as conn:
        with conn.cursor() as cur:bundle=for_case(cur,A,page['ticket']['id'],selected_ids=ids)
    prompt=group_instructions(bundle)
    assert 'action=group_answer ONLY' in prompt and 'English' in prompt
    assert 'customer_question or customer_update preserve the assigned agent reply language' in prompt
    body=_case_group_body(str(uuid.uuid4()),page['ticket']['id'],'The developer will review this tomorrow.',
      {'ai_summary':bundle},turn_context=True,summary_language=bundle['selection'])
    assert COPY['en']['update'] in body and 'The developer will review this tomorrow.' in body
    assert SUMMARY['en'] in body and 'Новые сведения' not in body
    assert f['provider'].call_count==1
