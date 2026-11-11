"""Disposable PGlite support C/R/U matrix. No provider or external DB calls."""
import json
import uuid
import pytest
from test_tenant_support_inbox import db, database, A, B, CA, PROFILE
import tenant_support_inbox_store as store
import tenant_support_inbox_summary as summary
import tenant_internal_consultations as consultations
from tenant_linear_policy import TenantLinearError


@pytest.fixture
def seeded(db,monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    monkeypatch.setattr(store,'summarize_created',lambda *_:None)
    monkeypatch.setattr(consultations,'_connect',lambda:db)
    monkeypatch.setattr(summary,'generate',lambda *a,**k:pytest.fail('provider forbidden'))
    dept=str(uuid.uuid4());other=str(uuid.uuid4())
    for key in (dept,other):
        db.execute('INSERT INTO saas_tenant_departments(tenant_id,id,name) VALUES(%s,%s,%s)',(A,key,key))
    for actor,d in ((31,dept),(32,dept),(33,other)):
        db.execute('''INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,department_id,permissions)
          VALUES(%s,%s,'admin',%s,'["support","dialogs"]')''',(A,actor,d))
    keys=[]
    for n,responsible in enumerate((31,32,33,None)):
        conversation='case-'+str(n);lead=100+n
        db.execute('''INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name)
          VALUES(%s,%s,%s,%s,%s)''',(A,CA,conversation,lead,conversation))
        with db.cursor() as cur:
            receipt=store.create_in_transaction(cur,A,CA,conversation,'private reason','fixture-'+str(n))
        db.execute('UPDATE saas_support_tickets SET access_responsible_id=%s WHERE tenant_id=%s AND id=%s',(responsible,A,receipt['id']))
        keys.append(receipt['id'])
    db.commit()
    return keys,dept,other


def configure(db,**scopes):
    db.execute('''INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'members',%s::jsonb)
      ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value''',
      (A,json.dumps({'31':{'ai_agents_manage':True,'access_scopes':{'support':scopes}}})))
    db.commit()
    return {'tenant_id':A,'telegram_id':31,'role':'owner'} # stale/forged role ignored


@pytest.mark.parametrize('scope,expected',[('self',1),('department',2),('all',4),('none',0)])
def test_list_count_search_detail(db,seeded,scope,expected):
    keys,_,_=seeded;profile=configure(db,view=scope)
    if scope=='none':
        with pytest.raises(TenantLinearError):store.listing(profile)
        with pytest.raises(TenantLinearError):store.detail(profile,keys[0])
        return
    listing=store.listing(profile)
    assert len(listing['tickets'])==expected
    assert listing['unresolved_count']==expected
    assert len(listing['channels'])==1
    for n,key in enumerate(keys):
        allowed=n<expected
        assert len(store.listing(profile,search='case-'+str(n))['tickets'])==int(allowed)
        if allowed: assert store.detail(profile,key)['ticket']['id']==key
        else:
            with pytest.raises(TenantLinearError):store.detail(profile,key)
    assert not store.listing(profile,channel_id=str(uuid.uuid4()))['tickets']


def test_independent_update_and_summary_guard(db,seeded):
    keys,_,_=seeded;profile=configure(db,view='none',update='self')
    result=store.update(profile,keys[0],{'description':'changed without read'})
    assert result=={'ticket':{'id':keys[0]},'readable':False}
    with pytest.raises(TenantLinearError):store.update(profile,keys[1],{'description':'forbidden'})
    with pytest.raises(TenantLinearError):summary.reserve(A,keys[1],profile)
    with pytest.raises(TenantLinearError):consultations.listing(profile,keys[0])
    configure(db,view='all',update='none')
    assert store.detail(profile,keys[1])['ticket']['id']==keys[1]
    with pytest.raises(TenantLinearError):store.update(profile,keys[0],{'status':'resolved'})
    with pytest.raises(TenantLinearError):summary.reserve(A,keys[0],profile)


def test_create_only_and_upsert_cannot_update_foreign_case(db,seeded):
    keys,_,_=seeded;profile=configure(db,view='none',create='self',update='none')
    request={'request_id':str(uuid.uuid4()),'lead_id':41}
    result=store.create(profile,request)
    assert store.create(profile,request)==result
    assert result['readable'] is False
    stored=db.execute('SELECT access_responsible_id FROM saas_support_tickets WHERE id=%s',(result['ticket']['id'],))[0]
    assert stored['access_responsible_id']==31
    db.commit()
    with pytest.raises(TenantLinearError):
        store.create(profile,{'request_id':str(uuid.uuid4()),'lead_id':101})
    with pytest.raises(TenantLinearError):
        with db.cursor() as cur:
            store.create_in_transaction(cur,A,CA,'case-1','forbidden handoff','new-handoff',actor=31)
    db.rollback()


def test_department_live_changes_and_tenant_fence(db,seeded):
    keys,dept,other=seeded;profile=configure(db,view='department',update='department')
    assert len(store.listing(profile)['tickets'])==2
    db.execute('UPDATE saas_tenant_members SET department_id=%s WHERE tenant_id=%s AND telegram_id=32',(other,A));db.commit()
    with pytest.raises(TenantLinearError):store.detail(profile,keys[1])
    with pytest.raises(TenantLinearError):store.update(profile,keys[1],{'description':'denied'})
    with pytest.raises(TenantLinearError):store.detail({'tenant_id':B,'telegram_id':31},keys[0])


def test_gate_off_sparse_and_ai_inheritance(db,seeded,monkeypatch):
    keys,_,_=seeded;profile=configure(db,view='none',update='none')
    with pytest.raises(TenantLinearError):store.listing(profile)
    monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED')
    assert len(store.listing(profile)['tickets'])==4
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    configure(db,update='none')
    assert len(store.listing(profile)['tickets'])==4
    with pytest.raises(TenantLinearError):store.update(profile,keys[0],{'description':'denied'})


def test_notifications_filter_scoped_tickets_and_counts(db,seeded,monkeypatch):
    import tenant_notification_center as center
    from test_tenant_notification_center import notice
    monkeypatch.setattr(center,'_connect',lambda:db)
    keys,_,_=seeded;profile=configure(db,view='self')
    own=notice(db,actor=31,event='support:own',target='/app?view=support&support_ticket_id='+keys[0])
    other=notice(db,actor=31,event='support:other',target='/app?view=support&support_ticket_id='+keys[1])
    result=center.listing(profile)
    assert result['unread_count']==1
    assert [r['id'] for r in result['notifications']]==[own]
    with pytest.raises(TenantLinearError):center.mark_read(profile,other)
    configure(db,view='none')
    assert center.listing(profile)['unread_count']==0


def test_dialog_projection_does_not_leak_ticket_fields(db,seeded):
    from tenant_operator_access import access
    from tenant_operator_dialogs import projection
    keys,_,_=seeded;profile=configure(db,view='self')
    db.execute('UPDATE saas_support_tickets SET assigned_to=32 WHERE tenant_id=%s',(A,));db.commit()
    with db.cursor() as cur:
        _,_,policy=access(cur,profile,'dialogs')
        sql,args=projection(policy)
        cur.execute(sql+"SELECT conversation_id,assigned_to FROM visible WHERE provider='connect' AND conversation_id LIKE 'case-%%' ORDER BY conversation_id",(A,A,*args,False))
        rows=cur.fetchall()
    assert [r['assigned_to'] for r in rows]==[32,None,None,None]


def test_aggregate_diagnostics_fail_closed_with_narrow_support(db,seeded):
    from tenant_ai_management_access import require_unrestricted_dialog_read
    from tenant_night_report_access import authorize
    _,_,_=seeded;profile=configure(db,view='self')
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError,match='unrestricted_support'):
            require_unrestricted_dialog_read(cur,profile)
        with pytest.raises(TenantLinearError,match='unrestricted_support'):
            authorize(cur,profile)


def test_complete_and_current_member_revocation(db,seeded):
    keys,_,_=seeded;profile=configure(db,view='none',update='self',create='none')
    payload={'request_id':str(uuid.uuid4()),'lead_id':100,'channel_id':CA,
             'conversation_id':'case-0','outcome':'resolved_confirmed'}
    assert store.complete(profile,payload)['readable'] is False
    assert store.complete(profile,payload)['ticket']['id']==keys[0]
    with pytest.raises(TenantLinearError):
        store.complete(profile,{**payload,'request_id':str(uuid.uuid4()),'lead_id':101,'conversation_id':'case-1'})
    db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=31',(A,));db.commit()
    with pytest.raises(TenantLinearError):store.update(profile,keys[0],{'description':'stale actor'})


def test_no_create_without_department_and_no_delete_catalog(db,seeded):
    from tenant_scoped_access import SCOPE_ACTION_CATALOG
    profile=configure(db,view='all',create='department')
    db.execute('UPDATE saas_tenant_members SET department_id=NULL WHERE tenant_id=%s AND telegram_id=31',(A,));db.commit()
    with pytest.raises(TenantLinearError):store.create(profile,{'request_id':str(uuid.uuid4()),'lead_id':41})
    assert SCOPE_ACTION_CATALOG['support']==('view','create','update')


def test_read_only_listing_does_not_enumerate_assignee_options(db,seeded):
    profile=configure(db,view='self',update='none')
    assert store.listing(profile)['members']==[]
    configure(db,view='self',update='self')
    assert store.listing(profile)['members']
    # No visible update target, even though the action itself exists.
    assert store.listing(profile,search='absent-case')['members']==[]


def test_consultation_replay_requires_exact_actor_case_and_input(db,seeded):
    keys,_,_=seeded;profile=configure(db,view='none',update='self')
    request={'case_id':keys[1],'request_key':'private-key','recipient_id':32,'reason':'private','evidence':{'local':'evidence'}}
    first=consultations.create(PROFILE,request)
    assert consultations.create(PROFILE,request)['id']==first['id']
    for data in ({**request,'case_id':keys[0]},):
        with pytest.raises(TenantLinearError,match='consultation_case_changed'):
            consultations.create(profile,data)
    for field,value in (('reason','changed'),('recipient_id',33),('evidence',{})):
        with pytest.raises(TenantLinearError,match='consultation_case_changed'):
            consultations.create(PROFILE,{**request,field:value})
    # Same readable/updatable case still does not make another actor's key ours.
    own_request={**request,'case_id':keys[0],'request_key':'another-actor-key'}
    consultations.create(PROFILE,own_request)
    with pytest.raises(TenantLinearError,match='consultation_case_changed'):
        consultations.create(profile,own_request)
