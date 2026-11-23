import copy
from test_dialog_lifecycle_group_projection import (projection,database,db,flow,channels,group,history_db,A,CA,CB,PROFILE,G,change)
import tenant_operator_dialogs as dialogs
from tenant_dialog_rollout import transition


def activate(d):
    with d.cursor() as cur: transition(cur,A,mode='active',admission_enabled=True,expected_generation=0)
    d.commit()


def cards(actor=11,**kw):
    return [r for r in dialogs.listing({'tenant_id':A,'telegram_id':actor},**kw)['dialogs'] if r['conversation_id']==G]


def test_active_group_card_retains_exact_authorized_sources_and_filters(projection):
    activate(projection)
    result=cards()
    assert len(result)==1
    assert {r['channel_id'] for r in result[0]['group_sources']}=={CB}
    assert {r['lead_id'] for r in result[0]['group_sources']}=={1002}
    assert {r['channel_id'] for r in cards(33)[0]['group_sources']}=={CB}
    assert cards(channel='connect:'+CA)==[]
    assert {r['channel_id'] for r in cards(lifecycle='all')[0]['group_sources']}=={CA,CB}
    assert {r['channel_id'] for r in cards(channel='connect:'+CB)[0]['group_sources']}=={CB}
    assert cards()==cards()  # repeated list read is stable and read only
    assert dialogs.listing({'tenant_id':A,'telegram_id':11},offset=50)['dialogs']==[]


def test_claim_close_do_not_leak_or_hide_other_source(projection):
    activate(projection)
    change(44)
    assert {r['channel_id'] for r in cards()[0]['group_sources']}=={CB}
    assert {r['channel_id'] for r in cards(44)[0]['group_sources']}=={CA,CB}
    change(44,'close',1)
    assert {r['channel_id'] for r in cards(44)[0]['group_sources']}=={CB}
    assert projection.execute('SELECT count(*) AS n FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND conversation_id=%s',(A,G))[0]['n']==2


def test_owner_overview_group_prefers_working_source_and_filters_before_folding(projection):
    activate(projection)
    change(44)
    fixture=cards(11,lifecycle='all',owner_overview=True)
    assert len(fixture)==1 and fixture[0]['channel_id']==CB
    members={r['channel_id']:r for r in fixture[0]['group_sources']}
    assert set(members)=={CA,CB}
    assert members[CA]['owner_overview_read_only'] and not members[CA]['unread_count']
    assert 'owner_overview_read_only' not in members[CB]
    filtered=cards(11,lifecycle='all',owner_overview=True,lifecycle_state='accepted')
    assert len(filtered)==1 and [r['channel_id'] for r in filtered[0]['group_sources']]==[CA]
    assert cards(11,lifecycle='all',owner_overview=True,lifecycle_state='accepted',channel='connect:'+CB)==[]
    assert {r['channel_id'] for r in cards(33,lifecycle='all',owner_overview=True)[0]['group_sources']}=={CB}


def test_fold_uses_only_group_jid_and_retains_each_source():
    def row(channel,jid=G,name='Same name'):
        return dict(provider='connect',channel_id=channel,channel_name=channel,conversation_id=jid,lead_id=1,customer_name=name,unread_count=1)
    source=[row('a'),row('b'),row('a','994501111111@s.whatsapp.net'),row('b','994501111111@s.whatsapp.net'),row('a','120363431037910652@g.us')]
    before=copy.deepcopy(source)
    result=dialogs._fold_group_routes(source)
    assert len(result)==4
    assert len(result[0]['group_sources'])==2
    assert source==before
    assert result[0]['unread_count']==1
    assert len(dialogs._fold_group_routes([row('a',f'{100000+i}@g.us') for i in range(55)]+[row('b',f'{100000+i}@g.us') for i in range(55)]))==55


def test_closed_source_reopens_once_without_duplicate_card(projection):
    import tenant_dialog_lifecycle as lifecycle
    activate(projection)
    change(44)
    change(44,'close',1)
    assert len(cards(44)[0]['group_sources'])==1
    from test_connect_group_history import add
    add(projection,99,CA,event='synthetic-fresh',direction='incoming')
    for _ in range(2):
        with projection.cursor() as cur:
            lifecycle.note_inbound(cur,A,CA,G,'synthetic-fresh','2099-01-01T00:00:00Z')
        projection.commit()
    result=cards(44)
    assert len(result)==1 and len(result[0]['group_sources'])==2
    reopened=next(r for r in result[0]['group_sources'] if r['channel_id']==CA)
    assert reopened['lifecycle']['state']=='waiting'
    assert reopened['lifecycle']['revision']==3


def test_completion_preview_retains_physical_source_tokens_and_visibility(projection):
    activate(projection)
    row=cards(completion_preview=True)[0]
    sources={r['channel_id']:r for r in row['group_sources']}
    assert set(sources)=={CA,CB}
    assert sources[CA]['lifecycle']['last_inbound_id'] is None
    assert sources[CA]['lifecycle']['can_dismiss'] is False
    assert sources[CB]['lifecycle']['last_inbound_id']
    assert sources[CB]['lifecycle']['can_dismiss'] is True
    assert {r['channel_id'] for r in cards(33,completion_preview=True)[0]['group_sources']}=={CB}
    change(44)
    assert {r['channel_id'] for r in cards(completion_preview=True)[0]['group_sources']}=={CB}
    owned={r['channel_id']:r for r in cards(44,completion_preview=True)[0]['group_sources']}
    assert owned[CA]['lifecycle']['can_close'] is True
    assert owned[CA]['lifecycle']['operator_id']==44


def test_completed_modes_preserve_source_privacy_and_search(projection):
    activate(projection)
    change(44)
    change(44,'close',1)
    assert {r['channel_id'] for r in cards(44,lifecycle='all')[0]['group_sources']}=={CA,CB}
    completed=cards(44,lifecycle='completed')
    assert len(completed)==1
    assert {r['channel_id'] for r in completed[0]['group_sources']}=={CA}
    state=completed[0]['group_sources'][0]['lifecycle']
    assert state['state']=='closed' and not state['can_close'] and not state['can_dismiss']
    assert {r['channel_id'] for r in cards(44,lifecycle='active')[0]['group_sources']}=={CB}
    # Ownership is still enforced in the mixed queue. Completed allows a
    # separate archive read only if the observer can view the exact source.
    assert {r['channel_id'] for r in cards(11,lifecycle='all')[0]['group_sources']}=={CB}
    shared=cards(11,lifecycle='completed',search=G)
    assert len(shared)==1
    assert [(source['channel_id'],source['lead_id'],source['archive_read_only'])
            for source in shared[0]['group_sources']]==[(CA,1001,True)]
    assert not shared[0]['can_send'] and shared[0]['unread_count']==0
    assert cards(11,lifecycle='completed',channel='connect:'+CB)==[]
    assert cards(33,lifecycle='completed')==[]
    assert cards(33,lifecycle='completed',channel='connect:'+CA)==[]
    assert cards(44,lifecycle='completed',search=G)
    assert cards(44,lifecycle='completed',search='no-such-synthetic-contact')==[]
    assert cards(44,lifecycle='completed',channel='connect:'+CB)==[]
    assert projection.execute('SELECT state,revision FROM saas_dialog_workflows WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s',(A,CA,G))[0]=={'state':'closed','revision':2}
