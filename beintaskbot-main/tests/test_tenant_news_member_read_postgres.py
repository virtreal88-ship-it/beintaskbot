"""Published company news is readable without a Linear grant; all writes stay protected."""
import json
import uuid

import pytest
from aiohttp.test_utils import TestClient, TestServer
from aiohttp import web
from unittest.mock import Mock

import tenant_news as news
from tenant_news_api import news_handler
from tenant_linear_policy import TenantLinearError
from tenant_member_access import page_capabilities
from test_company_employees_navigation_postgres import A,B,database,db,staff


@pytest.fixture
def feed(staff,monkeypatch):
    monkeypatch.setattr(news,'_connect',staff['factory'])
    import tenant_linear_observer_schema,tenant_news_ai_schema,tenant_news_telegram_schema
    for module in (tenant_linear_observer_schema,tenant_news_ai_schema,tenant_news_telegram_schema):
        monkeypatch.setattr(module,'_ready',False)
    with staff['factory']() as conn:
        news.ensure_observer_schema(conn)
        for tenant,status,age,title in [(A,'published',1,'A shared'),(B,'published',1,'B private'),
                (A,'pending',1,'A pending private'),(A,'rejected',1,'A rejected private'),(A,'published',100,'A expired')]:
            key=str(uuid.uuid4())
            conn.execute('''INSERT INTO saas_linear_news
              (tenant_id,id,issue_id,identifier,project_name,title,summary,url,source,status,published_at)
              VALUES(%s,%s,%s,'SRC-1','Project',%s,'Public summary','https://example.test',%s::jsonb,%s,
                now()-(%s * interval '1 day'))''',(tenant,key,key,title,json.dumps({'_news_ai':{'private':'SECRET CLASSIFICATION'},'private':'SECRET SOURCE'}),status,age))
    return staff


def reader():return {'tenant_id':A,'telegram_id':32,'role':'operator','active':True,'permissions':[]}


def test_active_employee_reads_only_published_same_company_without_linear(feed,monkeypatch):
    monkeypatch.setattr(news,'_read_tenant_workflow',Mock(side_effect=AssertionError('Published reading must not require integration rights')))
    response=news.member_published(reader())
    assert response['tenant_id']==A and response['user_id']==32
    assert [row['title'] for row in response['news']]==['A shared']
    serialized=json.dumps(response)
    for private in ('SECRET','reviewed_by','telegram_status','classification','source','status','updated_at'):
        assert private not in serialized
    assert page_capabilities({**reader(),'modules':{}})['news'] is True
    assert page_capabilities({**reader(),'active':False})['news'] is False


@pytest.mark.parametrize('change',['inactive_member','inactive_company','not_member'])
def test_reader_rechecks_current_membership_and_company(feed,change):
    with feed['factory']() as conn:
        if change=='inactive_member':conn.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=32',(A,))
        if change=='inactive_company':conn.execute("UPDATE saas_tenants SET status='suspended' WHERE id=%s",(A,))
    session={**reader(),'telegram_id':999999} if change=='not_member' else reader()
    with pytest.raises(TenantLinearError) as error:news.member_published(session)
    assert error.value.status==403


def test_reader_cannot_read_review_queues_or_publish_reject_or_send(feed):
    for status in ('pending','published','rejected'):
        with pytest.raises(TenantLinearError) as error:news.listing(reader(),status=status)
        assert error.value.status==403
    for action in ('publish','reject','create_publish'):
        data={'action':action,'confirm_publication':True,'request_id':str(uuid.uuid4()),'id':str(uuid.uuid4()),
            'title':'Disallowed','summary':'Disallowed','project_name':'Test','url':''}
        with pytest.raises(TenantLinearError) as error:news.command(reader(),data)
        assert error.value.status==403
    from tenant_news_telegram_settings import read
    import tenant_news_telegram_settings
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(tenant_news_telegram_settings,'_connect',feed['factory'])
        with pytest.raises(TenantLinearError):read(reader())


def test_member_feed_keyset_is_tenant_scoped(feed):
    with feed['factory']() as conn:
        for n in range(33):
            key=str(uuid.uuid4())
            conn.execute('''INSERT INTO saas_linear_news
              (tenant_id,id,issue_id,identifier,project_name,title,summary,url,source,status,published_at)
              VALUES(%s,%s,%s,'','Test',%s,'Text','','{}','published',now())''',(A,key,key,'Page '+str(n)))
    first=news.member_published(reader());second=news.member_published(reader(),after=first['after'])
    assert len(first['news'])==30 and len(second['news'])==4 and second['after'] is None
    assert len({r['id'] for r in first['news']+second['news']})==34
    assert 'B private' not in json.dumps([first,second])


@pytest.mark.asyncio
async def test_actual_member_api_feed_cannot_select_other_tenant_or_review_status(feed):
    app=web.Application();handle=news_handler(lambda request:reader(),'http://fixture.test',Mock())
    app.router.add_get('/api/platform/news',handle);app.router.add_post('/api/platform/news',handle)
    async with TestClient(TestServer(app)) as client:
        response=await client.get('/api/platform/news?feed=published&status=pending&tenant_id='+B)
        assert response.status==200
        result=await response.json();assert [r['title'] for r in result['news']]==['A shared']
        assert response.headers['Vary']=='Cookie'
        assert (await client.get('/api/platform/news?status=pending')).status==403
        response=await client.post('/api/platform/news?feed=published',headers={'Origin':'http://fixture.test'},json={
            'expected_tenant_id':A,'expected_user_id':32,'action':'reject','id':str(uuid.uuid4())})
        assert response.status==403


def test_default_deny_feed_requires_explicit_read_grant(feed, monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    with feed['factory']() as conn:
        conn.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'employee_access_defaults','{\"deny_unset\":true}')", (A,))
    with pytest.raises(TenantLinearError) as denied: news.member_published(reader())
    assert denied.value.status == 403
    with feed['factory']() as conn:
        conn.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value", (A,json.dumps({'32':{'access_scopes':{'news':{'view':'all'}}}})))
    assert [row['title'] for row in news.member_published(reader())['news']] == ['A shared']
