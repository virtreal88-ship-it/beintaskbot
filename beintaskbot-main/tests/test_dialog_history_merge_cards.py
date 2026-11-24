import pytest

from tenant_dialog_history_merge_cards import fold_authorized


def row(conv, lead, *, state='accepted', operator=22, channel='channel-a', **extra):
    return {'provider':'connect','channel_id':channel,'conversation_id':conv,'lead_id':lead,
            'lifecycle':{'state':state,'operator_id':operator,'can_close':state=='accepted'},'company_name':conv+' company',**extra}


PN = row('pn',101)
LID = row('lid',201)
UNRELATED = row('unrelated',666)
PAIR = {'id':'proof','channel_id':'channel-a','primary_id':'pn','primary_lead':101,
        'alternate_id':'lid','alternate_lead':201}


def fold(rows, pairs=(PAIR,), native_companions=None, actor=22):
    return fold_authorized(rows,pairs,native_companions or {},actor=actor)


def test_fold_both_same_actor_returns_exact_choice_and_does_not_borrow_metadata():
    cards=fold([LID,UNRELATED,PN])
    assert cards==[
        {**PN,'history_merge_id':'proof','history_merge_action_routes':[
          {'provider':'connect','channel_id':'channel-a','conversation_id':'pn','lead_id':101},
          {'provider':'connect','channel_id':'channel-a','conversation_id':'lid','lead_id':201},
        ]}, UNRELATED]
    assert 'history_merge_id' not in PN and cards[0]['company_name']=='pn company'


@pytest.mark.parametrize('other', [
    None, row('lid',201,operator=33), row('lid',201,channel='channel-b'),
    row('lid',201,archive_read_only=True), row('lid',201,owner_overview_read_only=True),
    row('lid',201,state='waiting',operator=None), row('lid',202),
])
def test_filtered_cross_source_passive_and_owned_elsewhere_are_never_folded(other):
    rows=[PN]+([other] if other else [])
    assert fold(rows)==rows


def test_duplicate_route_native_and_conflicting_proofs_all_block_projection():
    assert fold([PN,LID,LID])==[PN,LID,LID]
    assert fold([PN,LID],native_companions={('channel-a','pn',101):LID})==[PN,LID]
    assert fold([PN,LID],pairs=(PAIR,{**PAIR,'id':'competing','alternate_id':'another'}))==[PN,LID]


def test_closed_same_access_has_no_action_chooser_and_incompatible_modes_remain():
    p=row('pn',101,state='closed',archive_read_only=True)
    a=row('lid',201,state='closed',archive_read_only=True)
    assert fold([a,p])==[{**p,'history_merge_id':'proof'}]
    assert fold([a,{**p,'archive_read_only':False}])==[a,{**p,'archive_read_only':False}]


def test_waiting_with_different_accept_policy_stays_two():
    p=row('pn',101,state='waiting',operator=None)
    a=row('lid',201,state='waiting',operator=None)
    a['lifecycle']['can_accept']=True
    assert fold([a,p])==[a,p]


def test_both_passive_can_fold_without_action_target_even_with_different_assignees():
    p=row('pn',101,operator=33,owner_overview_read_only=True)
    a=row('lid',201,operator=44,owner_overview_read_only=True)
    assert fold([a,p])==[{**p,'history_merge_id':'proof'}]


@pytest.mark.parametrize('other,mode', [
    ('waiting', {}),
    ('closed', {'archive_read_only':True}),
    ('dismissed', {'archive_read_only':True}),
])
def test_explicit_mixed_all_facets_keep_each_exact_route(other, mode):
    accepted=row('lid',201)
    accepted['lifecycle']['can_close']=True
    companion=row('pn',101,state=other,operator=None,**mode)
    if other=='waiting':
        companion['lifecycle']['can_accept']=True
    rows=[companion,UNRELATED,accepted]
    assert fold_authorized(rows,[PAIR],{},actor=22)==rows
    cards=fold_authorized(rows,[PAIR],{},actor=22,allow_mixed=True)
    assert cards==[{**accepted,'history_merge_id':'proof','history_merge_certificate':'proof','history_merge_read_routes':[
        {'provider':'connect','channel_id':'channel-a','conversation_id':'pn','lead_id':101,
         'state':other,'read_only':bool(mode)},
        {'provider':'connect','channel_id':'channel-a','conversation_id':'lid','lead_id':201,
         'state':'accepted','read_only':False},
    ]},UNRELATED]


def test_mixed_fails_closed_without_both_routes_and_exact_roles():
    accepted=row('lid',201)
    accepted['lifecycle']['can_close']=True
    waiting=row('pn',101,state='waiting',operator=None)
    def mixed(rows):
        return fold_authorized(rows,[PAIR],{},actor=22,allow_mixed=True)
    assert mixed([accepted])==[accepted]
    assert mixed([accepted,waiting])==[accepted,waiting]
    waiting['lifecycle']['can_accept']=True
    passive={**waiting,'owner_overview_read_only':True}
    foreign={**accepted,'lifecycle':{**accepted['lifecycle'],'operator_id':44}}
    assert mixed([accepted,passive])==[accepted,passive]
    assert mixed([foreign,waiting])==[foreign,waiting]
    assert mixed([accepted,{**waiting,'channel_id':'channel-b'}])==[accepted,{**waiting,'channel_id':'channel-b'}]


def test_mixed_explicit_passive_and_owned_archive_are_navigation_only():
    currently_owned=row('lid',201,operator=22)
    for companion,expected_state in (
        (row('pn',101,operator=44,owner_overview_read_only=True),'accepted'),
        (row('pn',101,operator=22,state='closed'),'closed'),
    ):
        group=fold_authorized([companion,currently_owned],[PAIR],{},actor=22,allow_mixed=True)
        assert len(group)==1 and group[0]['lead_id']==201
        assert [(r['lead_id'],r['state'],r['read_only']) for r in group[0]['history_merge_read_routes']]==[
            (101,expected_state,True),(201,'accepted',False)]
        assert 'history_merge_action_routes' not in group[0]
