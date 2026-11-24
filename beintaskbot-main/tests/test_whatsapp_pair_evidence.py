"""Synthetic ledger only; no provider/network or customer messages."""
import json
import pytest
from test_tenant_support_inbox import database, db, A, B, CA, CB
from tenant_whatsapp_pair_evidence import check_pair_evidence, evaluate_pair_evidence

PN='994100000102@s.whatsapp.net'
LEGACY_PN=PN.replace('@s.whatsapp.net','@c.us')
LID='111111111111111@lid'
VERIFIED='2026-11-20T00:00:00Z'


def confirmed(**changes):
    return {'pair_matches_conversation':True,'directIdentity':{'jid':PN,'lid':LID},
            'originalConversationId':PN,'canonicalConversationId':LID,**changes}


def self_only():
    return {'pair_matches_conversation':False,'directIdentity':{'jid':'','lid':''},
            'originalConversationId':LID,'canonicalConversationId':LID}


def insert(db,event,day,evidence,route=LID,tenant=A,channel=CA):
    db.execute('''INSERT INTO saas_connect_identity_conflict_events
      (tenant_id,connect_channel_id,conversation_id,event_id,evidence,created_at)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s::jsonb,
        '2026-11-01'::timestamptz+%s*interval '1 day')''',
      (tenant,channel,route,event,json.dumps(evidence),day))
    db.commit()


def check(db,**kwargs):
    with db.cursor() as cur:
        return check_pair_evidence(cur,A,CA,PN,LID,VERIFIED,**kwargs)


@pytest.mark.parametrize('problem',[None,'before_missing','after_missing','same_time','wrong_route',
    'wrong_tenant','wrong_channel','wrong_phone','wrong_lid','wrong_original','wrong_canonical',
    'after_verification','missing_empty_fields','contradiction','self_after_verification'])
def test_exact_historical_bracketing(db,problem):
    if problem!='before_missing':insert(db,'before',1,confirmed())
    middle=self_only()
    if problem=='missing_empty_fields':middle['directIdentity']={}
    insert(db,'self',21 if problem=='self_after_verification' else 2,middle)
    if problem!='after_missing':
        proof=confirmed()
        if problem=='wrong_phone':proof['directIdentity']['jid']='994100000103@s.whatsapp.net'
        if problem=='wrong_lid':proof['directIdentity']['lid']='222222222222222@lid'
        if problem=='wrong_original':proof['originalConversationId']='222222222222222@lid'
        if problem=='wrong_canonical':proof['canonicalConversationId']='222222222222222@lid'
        insert(db,'after',2 if problem=='same_time' else 20 if problem=='after_verification' else 3,proof,
               route=PN if problem=='wrong_route' else LID,
               tenant=B if problem=='wrong_tenant' else A,channel=CB if problem=='wrong_channel' else CA)
    if problem=='contradiction':insert(db,'bad',4,confirmed(directIdentity={'jid':PN,'lid':'222222222222222@lid'}))
    result=check(db)
    assert result['safe'] is (problem is None)


def test_alias_empty_ledger_and_exact_scope(db):
    assert check(db)['safe']
    insert(db,'unrelated',1,{},route='222222222222222@lid')
    insert(db,'before',1,confirmed(directIdentity={'jid':PN.replace('@s.whatsapp.net','@c.us'),'lid':LID}))
    insert(db,'self',2,self_only())
    insert(db,'after',3,confirmed())
    assert check(db)=={'safe':True,'reason':'historical_evidence_clear','scanned':3}
    insert(db,'bad',5,{})
    assert not check(db)['safe']


@pytest.mark.parametrize('primary,mirror',[(PN,LEGACY_PN),(LEGACY_PN,PN)])
@pytest.mark.parametrize('problem',[None,'event','timestamp','evidence','source','tenant','unbracketed','contradiction'])
def test_phone_alias_mirror_requires_the_same_accepted_receipt(db,primary,mirror,problem):
    original={**self_only(),'originalConversationId':primary,'canonicalConversationId':primary}
    if problem=='contradiction':
        original=confirmed(directIdentity={'jid':PN,'lid':'222222222222222@lid'})
    proof=confirmed(originalConversationId=primary)
    insert(db,'before',1,proof,route=primary)
    if problem!='unbracketed':
        insert(db,'after',3,proof,route=primary)
    insert(db,'self',2,original,route=primary,tenant=B if problem=='tenant' else A,
           channel=CB if problem=='source' else CA)
    copy={**original,'extra':'changed'} if problem=='evidence' else original
    insert(db,'other' if problem=='event' else 'self',4 if problem=='timestamp' else 2,copy,route=mirror)
    with db.cursor() as cur:
        result=check_pair_evidence(cur,A,CA,primary,LID,VERIFIED)
    assert result['safe'] is (problem is None)
    if problem is None:
        assert result['scanned']==3


def test_scan_overflow_fails_closed_even_if_prefix_safe(db):
    for n in range(4):insert(db,str(n),n,confirmed())
    assert check(db,scan_limit=3)=={'safe':False,'reason':'evidence_scan_limit','scanned':4}
    assert check(db,scan_limit=4)['safe']


@pytest.mark.parametrize('limit',[0,-1,513,True,'3'])
def test_invalid_bounds_fail_closed_without_sql(limit):
    assert check_pair_evidence(None,A,CA,PN,LID,VERIFIED,scan_limit=limit)['reason']=='invalid_scan_limit'


def test_invalid_pair_and_unknown_facts_fail_closed():
    assert check_pair_evidence(None,A,CA,PN,PN,VERIFIED)['reason']=='invalid_pair'
    assert check_pair_evidence(None,A,CA,PN,LID,None)['reason']=='invalid_pair'
    assert not evaluate_pair_evidence([{}])['safe']
    assert not evaluate_pair_evidence([{'matching_pair':'true'}])['safe']
