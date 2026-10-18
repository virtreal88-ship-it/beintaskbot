"""Exact group read union; no transport, state mutations or AI calls."""
import json,uuid
import pytest
from test_group_coordination import database,db,flow,channels,group,A,B,CA,CB,PROFILE,G
import tenant_operator_dialogs as dialogs
import tenant_ai_agent_connect as connect
from tenant_connect_group_history import page
from tenant_linear_policy import TenantLinearError

@pytest.fixture
def history_db(group,monkeypatch):
    d=group['db'];monkeypatch.setattr(dialogs,'_connect',lambda:d);monkeypatch.setattr(dialogs,'runtime_flag',lambda _:True)
    return d


def add(d,n,channel=CA,*,event=None,participant='994500000001@s.whatsapp.net',direction='incoming',body='same',tenant=A,lead=None,raw_patch=None):
    raw={'channelId':channel,'conversationId':G,'externalMessageId':event or 'EVENT_'+str(n),'participantJid':participant,'participantName':'Real sender'}
    raw.update(raw_patch or {})
    d.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,author_name,body,message_type,happened_at,raw)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,'Real sender',%s,'text',%s::timestamptz,%s::jsonb)''',
      (str(uuid.UUID(int=10000+n)),tenant,lead or (1001 if channel==CA else 1002),'connect:stored_'+str(n),'connect:'+channel,direction,body,'2026-10-18T00:'+str(n//60).zfill(2)+':'+str(n%60).zfill(2)+'Z',json.dumps(raw)))
    d.commit()


def test_long_short_union_pagination_unique_and_sorted(history_db):
    d=history_db
    for n in range(125):add(d,n,CA if n<5 else CB)
    result=[];before=None
    while True:
        response=dialogs.history(PROFILE,'connect',1001,CA,G,before=before)
        result=response['messages']+result;before=response['next_before']
        if not before:break
    assert len(result)==125 and len({x['id'] for x in result})==125
    assert [x['happened_at'] for x in result]==sorted(x['happened_at'] for x in result)
    assert {x['source_channel_id'] for x in result}=={CA,CB}
    assert all(x['author_name']=='Real sender' and 'raw' not in x for x in result)
    assert len([x for x in dialogs.listing(PROFILE)['dialogs'] if x['conversation_id']==G])==1


def test_only_verified_duplicates_collapsed(history_db):
    d=history_db
    add(d,1,CA,event='SHARED_EVENT');add(d,2,CB,event='SHARED_EVENT')
    add(d,3,CB,event='OTHER_EVENT');add(d,4,CB,event='SHARED_EVENT',participant='994500000002@s.whatsapp.net')
    add(d,5,CA,event='AMBIGUOUS',participant='');add(d,6,CB,event='AMBIGUOUS',participant='')
    add(d,7,CB,event='SHARED_EVENT',raw_patch={'conversationId':'other@g.us'})
    values=dialogs.history(PROFILE,'connect',1001,CA,G)['messages']
    assert len(values)==6
    merged=next(x for x in values if len(x['sources'])==2)
    assert {x['channel_id'] for x in merged['sources']}=={CA,CB}
    assert len(d.execute('SELECT id FROM saas_crm_messages WHERE kommo_lead_id IN (1001,1002)'))==7


def test_names_directions_media_route_and_isolation(history_db):
    d=history_db
    add(d,1,CA);add(d,2,CB,direction='outgoing');add(d,3,CB)
    d.execute('''INSERT INTO saas_connect_participant_display_names(tenant_id,connect_channel_id,conversation_id,participant_jid,display_name,updated_by)
      VALUES(%s::uuid,%s::uuid,%s,'994500000001@s.whatsapp.net','Channel B name',11)''',(A,CB,G));d.commit()
    result=dialogs.history(PROFILE,'connect',1001,CA,G)['messages']
    assert result[0]['author_name']=='Real sender' and result[2]['author_name']=='Channel B name'
    assert result[1]['direction']=='outgoing' and result[1]['author_type']=='operator'
    assert result[2]['source_lead_id']==1002
    with d.cursor() as cur:assert page(cur,B,G)==[] and page(cur,A,'another group')==[]
    with pytest.raises(TenantLinearError):dialogs.history({**PROFILE,'tenant_id':B},'connect',1001,CA,G)
    anchor=d.execute('SELECT id FROM saas_crm_messages WHERE kommo_lead_id=41')[0]['id']
    assert dialogs.history(PROFILE,'connect',1001,CA,G,before=str(anchor))['messages']==[]


def test_context_union_bounded_without_changing_source_history(history_db,monkeypatch):
    d=history_db;monkeypatch.setattr(connect,'_connect',lambda:d)
    for n in range(90):add(d,n,CA if n==89 else CB)
    own=connect.history(A,1001,CA)
    assert len(own)==1
    job={'tenant_id':A,'channel_id':CA,'conversation_id':G,'lead_id':1001}
    ctx=connect.group_context(job,own,own[0]['happened_at'])
    assert len(ctx)==80 and ctx[-1]['external_id']==own[0]['external_id']
    assert len(connect.history(A,1001,CA))==1
    assert connect.group_context({**job,'lead_id':1002},own,own[0]['happened_at'])==[]
    assert connect.group_context({**job,'conversation_id':'client'},own,own[0]['happened_at']) is own
    assert all('source_channels' in x and x['author_name']=='Real sender' for x in ctx)


def test_dedup_precedes_pagination_and_foreign_rows_stay_out(history_db):
    d=history_db
    for n in range(60):add(d,n)
    for n in range(60,65):add(d,n,CB,event='EVENT_'+str(n-10))
    d.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,'120363000000001@g.us',2001)",(A,CA));d.commit()
    add(d,70,CA,lead=2001,body='other-group')
    d.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,%s,2002)",(B,CB,G));d.commit()
    add(d,71,CB,lead=2002,tenant=B,body='other-tenant')
    first=dialogs.history(PROFILE,'connect',1001,CA,G)
    second=dialogs.history(PROFILE,'connect',1001,CA,G,before=first['next_before'])
    both=second['messages']+first['messages']
    assert len(first['messages'])==50 and len(second['messages'])==10 and second['next_before'] is None
    assert len({x['id'] for x in both})==60 and all(x['body']=='same' for x in both)
    assert all(len(x['sources'])<=2 for x in both)


@pytest.mark.parametrize('mode',['manual','automatic'])
def test_worker_uses_other_channel_evidence_without_extra_send(group,monkeypatch,mode):
    from test_group_coordination import groups,agents,config,incoming
    f=group;d=f['db']
    d.execute('DELETE FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid',(A,))
    d.execute('UPDATE saas_ai_agents SET mode=%s WHERE tenant_id=%s::uuid',(mode,A));d.commit()
    add(d,1,CB,body='Only channel B knows the router model')
    d.execute("UPDATE saas_crm_messages SET happened_at=now()-interval '1 day' WHERE kommo_lead_id=1002");d.commit()
    groups.configure(PROFILE,config());agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CA)
    assert incoming(f,CA,'NEW_GROUP_QUESTION')==1
    f['process']()
    f['generate'].assert_called_once()
    context=f['generate'].call_args.args[3]
    assert any('Only channel B knows' in line['text'] for line in context)
    assert len(context)<=30 and any(line.get('sender')=='Real sender' for line in context)
    assert f['send'].call_count==int(mode=='automatic')
    if mode=='automatic':assert f['send'].call_args.args[0]['channel_id']==CA
    assert d.execute('SELECT count(*) n FROM saas_ai_agent_drafts')[0]['n']==int(mode=='manual')
    assert incoming(f,CA,'NEW_GROUP_QUESTION')==0


def test_read_does_not_mutate_jobs_state_or_drafts_and_requires_permission(history_db):
    from test_tenant_operator_workspace import employee
    d=history_db;add(d,1,CB)
    tables=['saas_ai_agent_jobs','saas_ai_agent_drafts','saas_ai_conversation_state','saas_ai_group_coordination']
    before={t:d.execute('SELECT * FROM '+t) for t in tables}
    person=employee(d,['dialogs'])
    assert dialogs.history(person,'connect',1001,CA,G)['messages']
    after={t:d.execute('SELECT * FROM '+t) for t in tables}
    assert before==after
    denied=employee(d,[],actor=34)
    with pytest.raises(TenantLinearError):dialogs.history(denied,'connect',1001,CA,G)


def test_photo_duplicate_uses_current_channel_replica(history_db,monkeypatch):
 d=history_db;monkeypatch.setattr(connect,'_connect',lambda:d)
 add(d,1,CA,event='PHOTO_EVENT',body='Synthetic caption');add(d,2,CB,event='PHOTO_EVENT',body='Synthetic caption')
 ids=[str(uuid.UUID(int=9001)),str(uuid.UUID(int=9002))]
 for lead,key in [(1001,ids[0]),(1002,ids[1])]:d.execute("UPDATE saas_crm_messages SET message_type='image',external_id=%s WHERE kommo_lead_id=%s",('connect:'+key,lead))
 d.commit();rows=connect.history(A,1002,CB)
 merged=connect.group_context({'tenant_id':A,'channel_id':CB,'lead_id':1002,'conversation_id':G},rows,'2026-10-18T01:00:00Z')
 photo=next(r for r in merged if r['message_type']=='image');assert photo['photo_message_id']==ids[1] and photo['channel']==CB
 assert len([r for r in merged if r['message_type']=='image'])==1
 assert len(photo['source_channels'])==2


@pytest.mark.parametrize('out_first',[False,True])
def test_company_echo_alias_and_ai_context(history_db,monkeypatch,out_first):
    d=history_db;monkeypatch.setattr(connect,'_connect',lambda:d)
    lid='123456789012345@lid';jid='994500000001@s.whatsapp.net'
    add(d,1 if out_first else 2,CA,event='COMPANY_ECHO',participant=lid,direction='outgoing')
    add(d,2 if out_first else 1,CB,event='COMPANY_ECHO',participant=jid,
        raw_patch={'participantIdentity':{'lid':lid,'jid':jid}})
    result=dialogs.history(PROFILE,'connect',1001,CA,G)['messages']
    assert len(result)==1 and result[0]['author_type']=='operator'
    assert result[0]['direction']=='outgoing' and result[0]['source_channel_id']==CA
    assert {x['direction'] for x in result[0]['sources']}=={'incoming','outgoing'}
    own=connect.history(A,1001,CA)
    ctx=connect.group_context({'tenant_id':A,'channel_id':CA,'conversation_id':G,'lead_id':1001},own,'2026-10-19T00:00:00Z')
    assert len(ctx)==1 and ctx[0]['direction']=='outgoing'
    assert d.execute('SELECT count(*) n FROM saas_crm_messages WHERE kommo_lead_id IN (1001,1002)')[0]['n']==2


def test_alias_is_event_scoped_not_text_or_author_guess(history_db):
    d=history_db;lid='123456789012345@lid';jid='994500000001@s.whatsapp.net'
    add(d,1,CA,event='PROVED',participant=lid,direction='outgoing')
    add(d,2,CB,event='PROVED',participant=jid,raw_patch={'participantIdentity':{'lid':lid,'jid':jid}})
    # Same text and same event ID from another author remains separate.
    add(d,3,CB,event='PROVED',participant='994500000002@s.whatsapp.net')
    # Alias evidence from another event cannot repair ambiguous legacy copies.
    add(d,4,CA,event='LEGACY',participant=lid,direction='outgoing')
    add(d,5,CB,event='LEGACY',participant=jid)
    assert len(dialogs.history(PROFILE,'connect',1001,CA,G)['messages'])==4


@pytest.mark.parametrize('patch',[
    {'participantIdentity':{'lid':'bad@lid','jid':'994500000001@s.whatsapp.net'}},
    {'participantIdentity':{'lid':'123456789012345@lid','jid':'994500000001@s.whatsapp.net'},'channelId':CA},
    {'participantIdentity':{'lid':'123456789012345@lid','jid':'994500000001@s.whatsapp.net'},'conversationId':'120363999999999@g.us'},
])
def test_bad_alias_or_scope_never_merges(history_db,patch):
    d=history_db
    add(d,1,CA,event='SHARED',participant='123456789012345@lid',direction='outgoing')
    add(d,2,CB,event='SHARED',raw_patch=patch)
    assert len(dialogs.history(PROFILE,'connect',1001,CA,G)['messages'])==2


def test_conflicting_aliases_fail_closed(history_db):
    d=history_db;lid='123456789012345@lid'
    add(d,1,CA,event='SHARED',participant=lid,direction='outgoing')
    for n,jid in [(2,'994500000001@s.whatsapp.net'),(3,'994500000002@s.whatsapp.net')]:
        add(d,n,CB,event='SHARED',participant=jid,raw_patch={'participantIdentity':{'lid':lid,'jid':jid}})
    assert len(dialogs.history(PROFILE,'connect',1001,CA,G)['messages'])==3



def test_anchor_scoped_group_history_and_foreign_fallback(history_db):
    d=history_db
    for n in range(60):add(d,n,CA if n%2 else CB)
    anchor=str(uuid.UUID(int=10020))
    result=dialogs.history(PROFILE,'connect',1001,CA,G,anchor=anchor)
    assert result['anchor_message_id']==anchor
    assert anchor in {m['id'] for m in result['messages']}
    assert {m['source_channel_id'] for m in result['messages']}=={CA,CB}
    alien=d.execute('SELECT id FROM saas_crm_messages WHERE kommo_lead_id=41')[0]['id'];d.commit()
    fallback=dialogs.history(PROFILE,'connect',1001,CA,G,anchor=str(alien))
    assert fallback['anchor_message_id'] is None
    assert len(fallback['messages'])==50 and str(alien) not in {m['id'] for m in fallback['messages']}
