from test_connect_channel_multiprocess import database,race
from test_tenant_support_inbox import db,A,B,CA,CB,AGENT,PROFILE
from test_integrated_connect_release import flow
from test_connect_channel_isolation import channels
from test_ai_draft_review import draft,approval
import tenant_ai_draft_review as review


def test_two_sends_reserve_only_once(draft):
    f=draft;v=review.read(PROFILE,f['draft_id']);payload={'id':v['id'],'data':approval(v)}
    values=race(f['db'],[('review_reserve',payload)]*2)
    assert sum(isinstance(x,dict) for x in values)==1
    assert not f['db'].execute('SELECT * FROM test_external_sends');f['db'].commit()


def test_two_editors_use_one_version(draft):
    f=draft;v=review.read(PROFILE,f['draft_id']);payload={'id':v['id'],'data':{'draft':'Reviewed','expected_version':v['version']}}
    values=race(f['db'],[('review_edit',payload)]*2)
    assert sum(isinstance(x,dict) for x in values)==1
    assert review.read(PROFILE,v['id'])['version']==2


def test_edit_races_send_without_mixing_text_and_approval(draft):
    f=draft;v=review.read(PROFILE,f['draft_id'])
    values=race(f['db'],[('review_edit',{'id':v['id'],'data':{'draft':'New','expected_version':v['version']}}),('review_reserve',{'id':v['id'],'data':approval(v)})])
    sent=[x for x in values if isinstance(x,dict) and x.get('expected_revision') is not None]
    if sent:assert sent[0]['draft']==v['draft']
    assert sum(isinstance(x,dict) for x in values)==1
